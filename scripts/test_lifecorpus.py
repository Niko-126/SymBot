"""life corpus 管线自测：不依赖 pytest，只用标准库，退出码 0 表示全过。

运行：
    python scripts/test_lifecorpus.py

覆盖五层：
- schema：合法记录通过、各类非法记录被拒、错误码与字段名正确
- pii：手机号/身份证/邮箱命中、遮掩格式、误报控制、递归路径
- validate：行号定位、坏 JSON、文件名推断、目录汇总、PII 入 issue、重复 id
- coverage：24 小时覆盖、缺口清单、轮数统计
- CLI：validate / coverage / report / fetch 的退出码与输出
- fetch：HTML→文本、GBK 兜底、robots 拒绝、异常不外抛、max_bytes、片段切分、
  dry-run 零联网、manifest 字段（全部离线，monkeypatch urllib.request.urlopen）
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app.lifecorpus import coverage as coverage_mod  # noqa: E402
from app.lifecorpus import fetch as fetch_mod  # noqa: E402
from app.lifecorpus import pii as pii_mod  # noqa: E402
from app.lifecorpus import schema as schema_mod  # noqa: E402
from app.lifecorpus import validate as validate_mod  # noqa: E402
import life_corpus  # noqa: E402

SAMPLES = ROOT / "samples" / "life_corpus"
FIXTURES = SAMPLES / "fixtures"
URLS_DEMO = FIXTURES / "urls-demo.txt"

_PASSED = 0
_FAILED = 0


# ---------------------------------------------------------------------------
# 断言与夹具
# ---------------------------------------------------------------------------


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"PASS  {name}")
    else:
        _FAILED += 1
        print(f"FAIL  {name}  {detail}")


def has_error(errors: list[schema_mod.FieldError], code: str, field: str | None = None) -> bool:
    for error in errors:
        if error.code == code and (field is None or error.field == field):
            return True
    return False


def describe(errors: list[schema_mod.FieldError]) -> str:
    return "; ".join(f"{error.code}@{error.field}" for error in errors)


def load_sample(kind: str) -> list[dict[str, Any]]:
    """读取 samples/life_corpus/<kind>.sample.jsonl，返回解析后的记录列表。"""
    path = SAMPLES / f"{kind}.sample.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def clone(record: dict[str, Any]) -> dict[str, Any]:
    """深拷贝一条记录，避免测试之间互相污染。"""
    return json.loads(json.dumps(record))


def write_jsonl(path: Path, rows: list[Any]) -> Path:
    """写 JSONL；字符串行原样写入（用于构造坏 JSON），空列表写成空文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@contextlib.contextmanager
def captured_stdout() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def call_cli(argv: list[str]) -> tuple[int, str]:
    with captured_stdout() as buffer:
        code = life_corpus.main(argv)
    return code, buffer.getvalue()


# ---------------------------------------------------------------------------
# 1. schema
# ---------------------------------------------------------------------------


def check_schema() -> None:
    all_valid = True
    for kind in schema_mod.KINDS:
        for record in load_sample(kind):
            errors = schema_mod.validate_record(kind, record)
            if errors:
                all_valid = False
                print(f"      样本不合法：{kind}/{record.get('id')} → {describe(errors)}")
    check("schema: 四类示例记录全部通过校验", all_valid)

    sample_counts = {kind: len(load_sample(kind)) for kind in schema_mod.KINDS}
    all_synthetic = all(record.get("synthetic") is True for kind in schema_mod.KINDS for record in load_sample(kind))
    check(
        "schema: 每类 2 条且 synthetic=true",
        all(count == 2 for count in sample_counts.values()) and all_synthetic,
        f"实际 {sample_counts}",
    )

    broken = clone(load_sample("rhythm")[0])
    broken["day_type"] = "weekday"
    check("schema: 非法 day_type 被拒", has_error(schema_mod.validate_record("rhythm", broken), "bad_enum", "day_type"))

    broken = clone(load_sample("rhythm")[0])
    broken["segments"][1]["start"] = "06:50"
    check(
        "schema: 24h 时间不连续被拒",
        has_error(schema_mod.validate_record("rhythm", broken), "rhythm_gap"),
        describe(schema_mod.validate_record("rhythm", broken)),
    )

    broken = clone(load_sample("rhythm")[0])
    broken["segments"][-1]["end"] = "23:30"
    check(
        "schema: 最后一段未到 24:00 被拒",
        has_error(schema_mod.validate_record("rhythm", broken), "rhythm_gap"),
    )

    broken = clone(load_sample("activity")[0])
    broken["duration_min"] = 0
    check(
        "schema: duration_min 越界被拒",
        has_error(schema_mod.validate_record("activity", broken), "out_of_range", "duration_min"),
    )

    broken = clone(load_sample("activity")[0])
    del broken["activity_type"]
    check("schema: 缺少 activity_type 被拒", has_error(schema_mod.validate_record("activity", broken), "missing"))

    broken = clone(load_sample("activity")[0])
    broken["duration_min"] = True
    check(
        "schema: bool 冒充整数被拒",
        has_error(schema_mod.validate_record("activity", broken), "bad_type", "duration_min"),
    )

    broken = clone(load_sample("activity")[0])
    broken["mood"] = "happy"
    check("schema: 未知字段被拒", has_error(schema_mod.validate_record("activity", broken), "unknown_field", "mood"))

    broken = clone(load_sample("activity")[0])
    broken["id"] = "Act-01"
    check("schema: 非法 id 被拒", has_error(schema_mod.validate_record("activity", broken), "bad_id", "id"))

    broken = clone(load_sample("rhythm")[0])
    broken["kind"] = "activity"
    check(
        "schema: kind 与文件类型不一致被拒",
        has_error(schema_mod.validate_record("rhythm", broken), "kind_mismatch", "kind"),
    )

    broken = clone(load_sample("social_scene")[0])
    broken["interrupt_policy"] = "maybe"
    check(
        "schema: 非法 interrupt_policy 被拒",
        has_error(schema_mod.validate_record("social_scene", broken), "bad_enum", "interrupt_policy"),
    )

    broken = clone(load_sample("social_scene")[0])
    broken["participants"] = 1
    check(
        "schema: participants 过小被拒",
        has_error(schema_mod.validate_record("social_scene", broken), "out_of_range", "participants"),
    )

    broken = clone(load_sample("snippet")[0])
    broken["messages"] = broken["messages"][:1]
    check(
        "schema: snippet 只有一条消息被拒",
        has_error(schema_mod.validate_record("snippet", broken), "out_of_range", "messages"),
    )

    broken = clone(load_sample("snippet")[0])
    for message in broken["messages"]:
        message["role"] = "other"
    check(
        "schema: snippet 缺 self 发言被拒",
        has_error(schema_mod.validate_record("snippet", broken), "no_self_turn", "messages"),
    )

    check("schema: 非对象记录被拒", has_error(schema_mod.validate_record("activity", ["not", "a", "dict"]), "bad_type"))
    check("schema: 未知 kind 被拒", has_error(schema_mod.validate_record("diary", {}), "bad_kind"))


# ---------------------------------------------------------------------------
# 2. PII
# ---------------------------------------------------------------------------


def check_pii() -> None:
    findings = pii_mod.scan_text("有问题打我电话 13800138000，随时。", "note")
    check(
        "pii: 手机号命中与遮掩",
        len(findings) == 1
        and findings[0].kind == "phone"
        and findings[0].masked == "138****8000"
        and findings[0].path == "note",
        f"实际 {[f.format() for f in findings]}",
    )

    findings = pii_mod.scan_text("身份证 110101199003071234 请核对")
    check(
        "pii: 身份证命中与遮掩",
        len(findings) == 1
        and findings[0].kind == "id_card"
        and findings[0].masked == "110101********1234",
        f"实际 {[f.format() for f in findings]}",
    )

    findings = pii_mod.scan_text("邮箱 zhangsan@example.com")
    check(
        "pii: 邮箱命中与遮掩",
        len(findings) == 1 and findings[0].kind == "email" and findings[0].masked == "z***@example.com",
        f"实际 {[f.format() for f in findings]}",
    )

    masked, findings = pii_mod.mask_text("电话 13800138000，邮箱 li.si@example.com")
    check(
        "pii: mask_text 同时遮掩且保留结构",
        masked == "电话 138****8000，邮箱 l***@example.com" and len(findings) == 2,
        f"实际 {masked!r}",
    )

    noisy = pii_mod.scan_text("订单号 1234567890123456789，日期 2024-10-30，10 位数字 1380013800")
    check("pii: 非 PII 数字不误报", noisy == [], f"实际 {[f.format() for f in noisy]}")

    payload = {"messages": [{"text": "我邮箱 a@b.com"}], "nested": {"phone": "13912345678"}}
    findings = pii_mod.scan_object(payload)
    paths = {finding.path for finding in findings}
    check(
        "pii: 递归路径正确",
        paths == {"messages[0].text", "nested.phone"},
        f"实际 {paths}",
    )

    findings, masked = pii_mod.scan_and_mask({"note": "手机 13800138000"})
    check(
        "pii: scan_and_mask 返回 findings + 副本且不改原对象",
        len(findings) == 1 and masked["note"] == "手机 138****8000" and payload is not masked,
        f"实际 {masked}",
    )


# ---------------------------------------------------------------------------
# 3. validate
# ---------------------------------------------------------------------------


def check_validate(tmp: Path) -> None:
    check("validate: 从 social_scene.sample.jsonl 推断类型", validate_mod.infer_kind("social_scene.sample.jsonl") == "social_scene")
    check("validate: 从 life-activity.jsonl 推断类型", validate_mod.infer_kind("life-activity.jsonl") == "activity")
    check("validate: 无法识别的文件名返回 None", validate_mod.infer_kind("notes.jsonl") is None)

    line_dir = tmp / "line_corpus"
    valid_first = load_sample("rhythm")[0]
    invalid = clone(valid_first)
    invalid["id"] = "rhythm_workday_bad"
    invalid["day_type"] = "weekday"
    valid_last = load_sample("rhythm")[1]
    line_path = write_jsonl(line_dir / "rhythm.jsonl", [valid_first, invalid, "", valid_last])

    file_report = validate_mod.validate_file(line_path)
    first_issue = file_report.issues[0] if file_report.issues else None
    check(
        "validate: 行号准确定位到第 2 行",
        first_issue is not None and first_issue.line_no == 2 and first_issue.kind == "rhythm",
        f"实际 {[(i.line_no, i.code) for i in file_report.issues]}",
    )
    check(
        "validate: 坏行也计入记录数（3 条）",
        file_report.record_count == 3,
        f"实际 {file_report.record_count}",
    )

    bad_json_path = write_jsonl(line_dir / "activity.jsonl", ["{not-json", "[1, 2]"])
    bad_json_report = validate_mod.validate_file(bad_json_path)
    codes = [issue.code for issue in bad_json_report.issues]
    check(
        "validate: 坏 JSON 与非对象行都报 bad_json",
        codes.count("bad_json") == 2,
        f"实际 {codes}",
    )

    unknown_path = write_jsonl(line_dir / "notes.jsonl", [{"id": "x"}])
    unknown_report = validate_mod.validate_file(unknown_path)
    check(
        "validate: 未知文件名报 unknown_kind",
        any(issue.code == "unknown_kind" for issue in unknown_report.issues),
    )

    pii_dir = tmp / "pii_corpus"
    pii_record = clone(load_sample("snippet")[0])
    pii_record["messages"][1]["text"] = "我手机 13800138000，你记一下。"
    pii_path = write_jsonl(pii_dir / "snippet.jsonl", [pii_record])
    pii_report = validate_mod.validate_file(pii_path)
    check(
        "validate: JSONL 内 PII 命中并记为 issue",
        pii_report.pii_count == 1
        and any(issue.code == "pii_found" and issue.line_no == 1 for issue in pii_report.issues),
        f"PII {pii_report.pii_count} / issues {[i.code for i in pii_report.issues]}",
    )

    duplicate = clone(load_sample("activity")[0])
    dup_path = write_jsonl(line_dir / "dup_activity.jsonl", [duplicate, clone(duplicate)])
    dup_report = validate_mod.validate_file(dup_path)
    check(
        "validate: 文件内重复 id 被检出",
        any(issue.code == "duplicate_id" and issue.line_no == 2 for issue in dup_report.issues),
    )

    global_dir = tmp / "global_dup_corpus"
    global_record = clone(load_sample("activity")[0])
    write_jsonl(global_dir / "activity-a.jsonl", [global_record])
    write_jsonl(global_dir / "activity-b.jsonl", [clone(global_record)])
    global_report = validate_mod.validate_dir(global_dir)
    check(
        "validate: 跨文件重复 id 被检出",
        any(issue.code == "duplicate_id_global" for issue in global_report.issues),
        f"实际 {[i.code for i in global_report.issues]}",
    )

    empty_path = write_jsonl(tmp / "empty_corpus" / "activity.jsonl", [])
    empty_report = validate_mod.validate_file(empty_path)
    check("validate: 空文件报 empty_file", any(issue.code == "empty_file" for issue in empty_report.issues))

    missing_report = validate_mod.validate_dir(tmp / "no_such_dir")
    check("validate: 目录不存在报 dir_missing", any(issue.code == "dir_missing" for issue in missing_report.issues))

    sample_report = validate_mod.validate_dir(SAMPLES)
    check(
        "validate: 示例目录 4 文件 8 记录 0 错误",
        len(sample_report.files) == 4
        and sample_report.record_count == 8
        and sample_report.error_count == 0
        and sample_report.pii_count == 0
        and sample_report.ok,
        f"实际 {sample_report.summary()}",
    )
    check(
        "validate: 示例目录按 kind 统计正确",
        sample_report.kind_counts() == {kind: 2 for kind in schema_mod.KINDS},
        f"实际 {sample_report.kind_counts()}",
    )
    rendered = validate_mod.render_dir_report(sample_report)
    check("validate: 渲染文本包含汇总与通过字样", "汇总" in rendered and "通过" in rendered)


# ---------------------------------------------------------------------------
# 4. coverage
# ---------------------------------------------------------------------------


def check_coverage() -> None:
    records = coverage_mod.load_records(SAMPLES)
    check(
        "coverage: 按类型读入 2 条",
        all(len(records[kind]) == 2 for kind in schema_mod.KINDS),
        f"实际 { {k: len(v) for k, v in records.items()} }",
    )

    report = coverage_mod.build_coverage(records)
    check("coverage: 示例 24h 全部铺满", all(minutes == 1440 for minutes in report.rhythm_coverage.values()), f"实际 {report.rhythm_coverage}")

    expected_turns = 8 + 10 + 2 + 3
    check(
        "coverage: 对话轮数统计正确（23）",
        report.turns_total == expected_turns
        and report.scene_turns_total == 18
        and report.snippet_turns_total == 5,
        f"实际 total={report.turns_total} scene={report.scene_turns_total} snippet={report.snippet_turns_total}",
    )
    check(
        "coverage: 场景与片段数量正确",
        report.scene_count == 2 and report.snippet_count == 2,
        f"实际 scene={report.scene_count} snippet={report.snippet_count}",
    )

    gap_items = {(gap.dimension, gap.item) for gap in report.gaps}
    check(
        "coverage: 报出缺失日型 holiday",
        ("day_type", "holiday") in gap_items,
        f"实际 {sorted(gap_items)}",
    )
    check(
        "coverage: 报出缺失关系 friend",
        ("relation", "friend") in gap_items,
    )
    check(
        "coverage: 示例存在关系种类严重缺口",
        any(gap.critical and gap.dimension == "relation" for gap in report.gaps),
        f"实际严重项 {[g.format() for g in report.gaps if g.critical]}",
    )

    empty_report = coverage_mod.build_coverage({kind: [] for kind in schema_mod.KINDS})
    check(
        "coverage: 空语料报严重缺口",
        empty_report.critical_gap_count >= 3 and empty_report.total_records == 0,
        f"实际严重 {empty_report.critical_gap_count}",
    )

    merged = coverage_mod.union_minutes(
        [
            {"start": "00:00", "end": "10:00"},
            {"start": "09:00", "end": "12:00"},
            {"start": "23:00", "end": "24:00"},
        ]
    )
    check("coverage: 重叠时间段只算一次", merged == 13 * 60, f"实际 {merged}")

    truncated = coverage_mod.union_minutes([{"start": "00:00", "end": "25:00"}])
    check("coverage: 非法时间段被忽略", truncated == 0, f"实际 {truncated}")

    text = coverage_mod.format_coverage(report)
    check(
        "coverage: 渲染文本包含七个小节与缺口清单",
        all(marker in text for marker in ("24 小时作息", "日型 × 记录类型", "缺口清单", "对话轮数")),
    )


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def check_cli(tmp: Path) -> None:
    code, output = call_cli(["validate", str(SAMPLES), "--strict"])
    check("cli: validate 示例目录 --strict 返回 0", code == 0, f"实际 {code}，输出尾部 {output[-200:]}")
    check("cli: validate 输出包含汇总与通过", "汇总" in output and "通过" in output)

    bad_dir = tmp / "bad_corpus"
    good = load_sample("rhythm")[0]
    bad = clone(good)
    bad["id"] = "rhythm_bad_01"
    bad["day_type"] = "weekday"
    write_jsonl(bad_dir / "rhythm.jsonl", [good, bad])

    code_strict, _ = call_cli(["validate", str(bad_dir), "--strict"])
    code_lax, _ = call_cli(["validate", str(bad_dir)])
    check("cli: validate 坏数据 --strict 返回 1", code_strict == 1, f"实际 {code_strict}")
    check("cli: validate 坏数据不加 --strict 返回 0", code_lax == 0, f"实际 {code_lax}")

    code, _ = call_cli(["validate", str(tmp / "definitely_missing"), "--strict"])
    check("cli: 目录不存在返回 2", code == 2, f"实际 {code}")

    pii_dir = tmp / "pii_corpus_cli"
    pii_record = clone(load_sample("activity")[0])
    pii_record["note"] = "紧急联系人 13800138000"
    write_jsonl(pii_dir / "activity.jsonl", [pii_record])
    code_with_pii, _ = call_cli(["validate", str(pii_dir), "--strict"])
    code_without_pii, _ = call_cli(["validate", str(pii_dir), "--strict", "--no-pii"])
    check("cli: PII 命中导致 --strict 返回 1", code_with_pii == 1, f"实际 {code_with_pii}")
    check("cli: --no-pii 跳过 PII 后返回 0", code_without_pii == 0, f"实际 {code_without_pii}")

    code, output = call_cli(["coverage", str(SAMPLES)])
    check("cli: coverage 返回 0", code == 0, f"实际 {code}")
    check("cli: coverage 输出缺口清单", "缺口清单" in output and "严重" in output)

    report_dir = tmp / "report_corpus"
    shutil.copytree(SAMPLES, report_dir)
    report_path = report_dir / "report.md"
    code, output = call_cli(["report", str(report_dir)])
    content = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    check(
        "cli: report 写出 report.md",
        code == 0 and report_path.exists() and "覆盖报告" in content,
        f"实际 code={code} exists={report_path.exists()}",
    )
    check(
        "cli: report 包含校验/覆盖/缺口三部分",
        all(marker in content for marker in ("校验结果", "覆盖矩阵", "缺口清单")),
    )

    code, _ = call_cli(["report", str(bad_dir), "--strict"])
    check("cli: report 坏数据 --strict 返回 1", code == 1, f"实际 {code}")


# ---------------------------------------------------------------------------
# 6. fetch（离线：monkeypatch urllib.request.urlopen，绝不联网）
# ---------------------------------------------------------------------------


class FakeResponse:
    """假响应：只实现 fetch.py 用到的 status / headers / read / close。"""

    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self._body = body
        self.status = status
        self.code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.close()
        return False


def page(body: bytes | str, *, status: int = 200, content_type: str = "text/html; charset=utf-8") -> Any:
    """返回一个"每次调用都造新响应"的路由，避免假响应被上一个用例 close 掉。"""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return lambda: FakeResponse(payload, status=status, content_type=content_type)


def raises(exc: Exception) -> Any:
    """路由：一调用就抛指定异常，用来模拟 403 / 超时 / 网络错。"""

    def _route() -> Any:
        raise exc

    return _route


def server(routes: dict[str, Any], calls: list[str]) -> Any:
    """按 URL 路由的假 urlopen；未登记 URL 直接抛错（防止测试悄悄联网）。"""

    def _open(request: Any, timeout: float | None = None, **_: Any) -> Any:
        url = getattr(request, "full_url", str(request))
        calls.append(url)
        route = routes.get(url)
        if route is None:
            raise urllib.error.URLError(f"离线测试：未登记的 URL {url}")
        if isinstance(route, Exception):
            raise route
        return route()

    return _open


@contextlib.contextmanager
def fake_network(routes: dict[str, Any], calls: list[str]) -> Iterator[list[str]]:
    """替换 urlopen；进出都清 robots 缓存，保证用例互不影响。"""
    original = urllib.request.urlopen
    urllib.request.urlopen = server(routes, calls)  # type: ignore[assignment]
    fetch_mod.clear_robots_cache()
    try:
        yield calls
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]
        fetch_mod.clear_robots_cache()


def check_fetch(tmp: Path) -> None:
    tiny_bytes = (FIXTURES / "tiny.html").read_bytes()
    allow_text = (FIXTURES / "robots-allow.txt").read_text(encoding="utf-8")
    disallow_text = (FIXTURES / "robots-disallow.txt").read_text(encoding="utf-8")
    page_url = "http://corpus.test/daily-life/one"
    policy = fetch_mod.FetchPolicy(delay_seconds=0.0)

    # 1) HTML → 纯文本
    routes = {
        "http://corpus.test/robots.txt": page(allow_text, content_type="text/plain"),
        page_url: page(tiny_bytes),
    }
    with fake_network(routes, []) as calls:
        result = fetch_mod.fetch_page(page_url, policy)
        robots_checked = calls == ["http://corpus.test/robots.txt", page_url]
    text = result["text"]
    check(
        "fetch: HTML→纯文本去掉 script/style/注释并保留换行",
        bool(result["ok"])
        and "早上七点半起床" in text
        and "晚上九点洗澡" in text
        and "\n" in text
        and "tracker" not in text
        and "alertMe" not in text
        and "display: none" not in text
        and "这条注释" not in text
        and "<p>" not in text
        and result["encoding"].lower() == "utf-8"
        and result["robots"]["allowed"] is True
        and robots_checked,
        f"ok={result['ok']} enc={result['encoding']} err={result['error']} text={text[:100]!r}",
    )

    # 2) GBK 页面：HTTP 头不给 charset，只能靠 meta + 兜底
    gbk_url = "http://gbk.test/market"
    routes = {
        "http://gbk.test/robots.txt": page(allow_text, content_type="text/plain"),
        gbk_url: page((FIXTURES / "gbk-page.html").read_bytes(), content_type="text/html"),
    }
    with fake_network(routes, []):
        gbk_result = fetch_mod.fetch_page(gbk_url, policy)
    check(
        "fetch: GBK 页面按 meta charset 兜底解码",
        bool(gbk_result["ok"])
        and "菜市场" in gbk_result["text"]
        and gbk_result["encoding"].lower() in {"gbk", "gb2312", "gb18030"},
        f"ok={gbk_result['ok']} enc={gbk_result['encoding']} err={gbk_result['error']}",
    )

    # 3) robots Disallow：必须拒绝，且不能再去请求页面
    blocked = "http://blocked.test/daily-life/one"
    routes = {"http://blocked.test/robots.txt": page(disallow_text, content_type="text/plain")}
    with fake_network(routes, []) as calls:
        blocked_result = fetch_mod.fetch_page(blocked, policy)
        blocked_calls = list(calls)
    check(
        "fetch: robots Disallow 直接拒绝，且只请求了 robots.txt",
        blocked_result["ok"] is False
        and "robots" in (blocked_result["error"] or "")
        and blocked_result["status"] is None
        and blocked_calls == ["http://blocked.test/robots.txt"],
        f"error={blocked_result['error']} calls={blocked_calls}",
    )

    # 4) 403 / 超时 / URLError：一律不外抛
    err_routes = {
        "http://err.test/robots.txt": page(allow_text, content_type="text/plain"),
        "http://err.test/403": raises(urllib.error.HTTPError("http://err.test/403", 403, "Forbidden", None, None)),
        "http://err.test/timeout": raises(TimeoutError("timed out")),
        "http://err.test/net": raises(urllib.error.URLError("no route")),
    }
    with fake_network(err_routes, []):
        err_results = [
            fetch_mod.fetch_page(url, policy)
            for url in ("http://err.test/403", "http://err.test/timeout", "http://err.test/net")
        ]
    check(
        "fetch: 403/超时/URLError 不外抛、ok=False 且 error 有原因",
        all(item["ok"] is False and (item["error"] or "") for item in err_results)
        and err_results[0]["status"] == 403,
        f"结果={[(item['status'], item['error']) for item in err_results]}",
    )

    # 5) max_bytes 截断
    big_url = "http://corpus.test/big"
    routes = {
        "http://corpus.test/robots.txt": page(allow_text, content_type="text/plain"),
        big_url: page(tiny_bytes * 12),
    }
    small_policy = fetch_mod.FetchPolicy(delay_seconds=0.0, max_bytes=64)
    with fake_network(routes, []):
        big_result = fetch_mod.fetch_page(big_url, small_policy)
    check(
        "fetch: max_bytes 截断生效（bytes≤限值 + truncated）",
        bool(big_result["ok"])
        and big_result["truncated"] is True
        and big_result["bytes"] <= 64
        and "晚上九点洗澡" not in big_result["text"],
        f"bytes={big_result['bytes']} truncated={big_result['truncated']}",
    )

    # 6) 片段切分：去重 / 截断 / 丢广告
    mess = "\n".join(
        [
            "  早上七点半起床，先去楼下买了两个包子。  ",
            "早上七点半起床，先去楼下买了两个包子。",
            "加微信 abc12345 领红包，扫码下载APP",
            "更多内容见 http://spam.example/promo",
            "联系电话 13800138000",
            "现在加入VIP群，优惠限时",
            "！@@#￥%……&*",
            "下午和邻居在小区里散步，聊到他家孩子上小学。",
            "晚上九点洗澡，十点半躺下，翻了两页书就睡了。",
            "长" * 300,
        ]
    )
    snippets = fetch_mod.split_snippets(mess, max_len=60)
    joined = "".join(snippets)
    check(
        "fetch: 片段切分去重/超长截断/广告行被丢",
        len(snippets) == 4
        and snippets.count("早上七点半起床，先去楼下买了两个包子。") == 1
        and all(len(item) <= 60 for item in snippets)
        and len(snippets[-1]) == 60
        and not any(word in joined for word in ("微信", "红包", "spam", "13800138000", "优惠", "下载APP")),
        f"n={len(snippets)} snippets={snippets[:3]!r}",
    )

    # 7) dry-run 全流程：urlopen 一调就炸也必须过
    out_dir = tmp / "dryrun_out"

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("dry-run 不许联网")

    original = urllib.request.urlopen
    urllib.request.urlopen = explode  # type: ignore[assignment]
    fetch_mod.clear_robots_cache()
    try:
        code, output = call_cli(
            ["fetch", "--urls", str(URLS_DEMO), "--dry-run", "--out", str(out_dir)]
        )
    except AssertionError as exc:  # 说明 dry-run 真的走到了网络
        code, output = -1, str(exc)
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]
        fetch_mod.clear_robots_cache()
    check(
        "fetch: dry-run 零联网、不落盘、退出 0",
        code == 0 and "dry-run" in output and "未检查" in output and not out_dir.exists(),
        f"code={code} out_exists={out_dir.exists()} tail={output[-160:]!r}",
    )

    # 8) --confirm：落盘 txt + manifest 字段齐全 / sha256 对得上
    url_file = tmp / "urls-manifest.txt"
    url_file.write_text(
        "\n".join(
            [
                "http://m1.test/today",
                "http://m2.test/missing",
                "# 注释行应被忽略",
                "",
                "http://m3.test/weekend",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    weekend_html = "<html><body><p>周末去公园跑了三公里，回来路上买了杯豆浆。</p></body></html>"
    routes = {
        "http://m1.test/robots.txt": page(allow_text, content_type="text/plain"),
        "http://m2.test/robots.txt": page(allow_text, content_type="text/plain"),
        "http://m3.test/robots.txt": page(allow_text, content_type="text/plain"),
        "http://m1.test/today": page(tiny_bytes),
        "http://m2.test/missing": raises(
            urllib.error.HTTPError("http://m2.test/missing", 404, "Not Found", None, None)
        ),
        "http://m3.test/weekend": page(weekend_html),
    }
    raw_dir = tmp / "raw"
    with fake_network(routes, []):
        code, output = call_cli(
            ["fetch", "--urls", str(url_file), "--confirm", "--out", str(raw_dir), "--delay", "0"]
        )
    manifest_path = raw_dir / "manifest.jsonl"
    rows = (
        [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if manifest_path.exists()
        else []
    )
    txt_files = sorted(raw_dir.glob("*.txt")) if raw_dir.exists() else []
    required_fields = {
        "url",
        "status",
        "bytes",
        "encoding",
        "sha256",
        "fetched_at",
        "snippet_count",
        "robots",
        "policy",
    }
    row_hashes = {row.get("sha256") for row in rows}
    file_hashes = {
        hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        for path in txt_files
    }
    check(
        "fetch: --confirm 落盘 txt + manifest 字段齐全、sha256 为 hex 且对得上",
        code == 0
        and len(rows) == 2
        and all(required_fields <= set(row) for row in rows)
        and all(re.fullmatch(r"[0-9a-f]{64}", str(row["sha256"])) for row in rows)
        and all(isinstance(row["snippet_count"], int) and row["snippet_count"] >= 1 for row in rows)
        and all(row["robots"]["allowed"] is True for row in rows)
        and len(txt_files) == 2
        and row_hashes == file_hashes
        and "404" in output,
        f"code={code} rows={len(rows)} txt={len(txt_files)} hashes_ok={row_hashes == file_hashes}",
    )

    # 9) respect_robots=False：不请求 robots.txt，直接抓页面
    no_robots_url = "http://norobots.test/page"
    routes = {no_robots_url: page(tiny_bytes)}
    with fake_network(routes, []) as calls:
        relaxed = fetch_mod.fetch_page(
            no_robots_url, fetch_mod.FetchPolicy(delay_seconds=0.0, respect_robots=False)
        )
        relaxed_calls = list(calls)
    check(
        "fetch: respect_robots=False 时不请求 robots.txt",
        bool(relaxed["ok"]) and relaxed_calls == [no_robots_url],
        f"ok={relaxed['ok']} calls={relaxed_calls}",
    )

    # 10) fetch_many：顺序抓、失败继续、请求之间 sleep、同 host 更保守
    many_urls = ["http://many.test/a", "http://many.test/b", "http://many.test/c"]
    routes = {
        "http://many.test/robots.txt": page(allow_text, content_type="text/plain"),
        "http://many.test/a": page("<html><body><p>上午去银行办了张储蓄卡。</p></body></html>"),
        "http://many.test/b": raises(
            urllib.error.HTTPError("http://many.test/b", 404, "Not Found", None, None)
        ),
        "http://many.test/c": page("<html><body><p>下午在家收拾衣柜，扔了两袋旧衣服。</p></body></html>"),
    }
    delays: list[float] = []
    with fake_network(routes, []):
        many_results = fetch_mod.fetch_many(
            many_urls, fetch_mod.FetchPolicy(delay_seconds=0.5), sleeper=delays.append
        )
    check(
        "fetch: fetch_many 失败继续 + 请求间 sleep（同 host ×1.5）",
        [item["ok"] for item in many_results] == [True, False, True]
        and "404" in (many_results[1]["error"] or "")
        and len(delays) == 2
        and len(many_results) == 3
        and delays[0] >= 0.5
        and delays[1] >= delays[0],
        f"ok={[item['ok'] for item in many_results]} delays={delays}",
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    print(f"life corpus 自测开始（Python {sys.version.split()[0]}）")
    check_schema()
    check_pii()

    with tempfile.TemporaryDirectory(prefix="life-corpus-test-") as tmp_name:
        tmp = Path(tmp_name)
        check_validate(tmp)
        check_coverage()
        check_cli(tmp)
        check_fetch(tmp)

    print("")
    print(f"合计：通过 {_PASSED} / 失败 {_FAILED} / 断言 {_PASSED + _FAILED}")
    if _FAILED:
        print("结果：未通过")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
