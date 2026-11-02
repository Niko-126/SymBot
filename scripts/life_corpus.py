"""life corpus 命令行：validate / coverage / report / fetch。

用法（在仓库根目录）：
    python scripts/life_corpus.py validate  [目录] [--strict] [--no-pii]
    python scripts/life_corpus.py coverage  [目录] [--strict]
    python scripts/life_corpus.py report    [目录] [--out 报告路径] [--strict] [--no-pii]
    python scripts/life_corpus.py fetch     --urls FILE [--out DIR] [--dry-run|--confirm]
                                            [--delay 2.0] [--max-bytes N] [--timeout 10]

退出码：
    0  正常（未开 --strict 时，即使有错误也退出 0，便于先看全量报告）；
       fetch：dry-run 正常结束，或 --confirm 下至少成功落盘 1 条
    1  --strict 下存在错误 / PII（validate、report）或严重缺口（coverage）；
       fetch：--confirm 下一条都没抓成功
    2  目录不存在、URL 文件读不了等环境问题

约束：
- validate / coverage / report 纯标准库、不联网、不写数据库，report 只写 Markdown。
- fetch 默认 **dry-run**：只解析 URL、不联网、不写文件；`--confirm` 才发 GET，
  且不登录、不带 cookie、不绕验证码、遵守 robots.txt、请求之间必 sleep。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/life_corpus.py`
    sys.path.insert(0, str(ROOT))

from app.lifecorpus import fetch as corpus_fetch  # noqa: E402
from app.lifecorpus.coverage import (  # noqa: E402
    CoverageReport,
    build_coverage,
    format_coverage,
    load_records,
)
from app.lifecorpus.pii import PII_LABELS  # noqa: E402
from app.lifecorpus.validate import DirReport, render_dir_report, validate_dir  # noqa: E402

DEFAULT_CORPUS_DIR: Path = ROOT / "data" / "human_life"
DEFAULT_RAW_DIR: Path = ROOT / "data" / "human_life" / "_raw"

#: fetch 子命令每次都会打印的纪律文案，防止以后被顺手改宽
FETCH_RULES: str = "不登录 / 不带 cookie / 不绕验证码 / 遵守 robots.txt / 默认 dry-run"


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def run_validate(path: str | Path, *, strict: bool = False, scan_pii: bool = True) -> int:
    """校验目录；返回进程退出码。"""
    target = Path(path)
    if not target.exists():
        print(f"目录不存在：{target}")
        print(f"提示：默认目录为 {DEFAULT_CORPUS_DIR}，可用 --help 查看用法。")
        return 2
    report = validate_dir(target, scan_pii=scan_pii)
    print(render_dir_report(report))
    if not strict and not report.ok:
        print("提示：未开启 --strict，本次仍以退出码 0 结束；发布前请加 --strict。")
    return 1 if strict and not report.ok else 0


def run_coverage(path: str | Path, *, strict: bool = False) -> int:
    """输出覆盖矩阵与缺口清单；返回进程退出码。"""
    target = Path(path)
    if not target.exists():
        print(f"目录不存在：{target}")
        return 2
    report = build_coverage(load_records(target))
    print(format_coverage(report))
    if strict and report.critical_gap_count > 0:
        print(f"--strict：存在 {report.critical_gap_count} 项严重缺口，退出码 1。")
        return 1
    return 0


def run_report(
    path: str | Path,
    *,
    out: str | Path | None = None,
    strict: bool = False,
    scan_pii: bool = True,
) -> int:
    """生成 Markdown 报告；默认写到 ``<目录>/report.md``。"""
    target = Path(path)
    if not target.exists():
        print(f"目录不存在：{target}")
        return 2
    dir_report = validate_dir(target, scan_pii=scan_pii)
    coverage_report = build_coverage(load_records(target))
    out_path = Path(out) if out is not None else target / "report.md"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            render_markdown(dir_report, coverage_report, target),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"写报告失败：{out_path}（{exc}）")
        return 2
    print(f"报告已写入：{out_path}")
    print(f"校验：错误 {dir_report.error_count} / PII {dir_report.pii_count}")
    print(f"覆盖：缺口 {coverage_report.gap_count} 项，其中严重 {coverage_report.critical_gap_count} 项")
    return 1 if strict and (not dir_report.ok or coverage_report.critical_gap_count > 0) else 0


# ---------------------------------------------------------------------------
# 采集子命令（默认 dry-run）
# ---------------------------------------------------------------------------


def read_url_file(path: str | Path) -> list[str] | None:
    """读 URL 列表：一行一条，忽略空行与 ``#`` 注释，按出现顺序去重。

    返回 ``None`` 表示文件读不了（原因已打印）；空列表表示文件里没有有效行。
    """
    target = Path(path)
    try:
        content = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"读不到 URL 文件：{target}（{exc}）")
        return None
    except UnicodeDecodeError as exc:
        print(f"URL 文件不是 UTF-8：{target}（{exc}）")
        return None

    urls: list[str] = []
    seen: set[str] = set()
    duplicates = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)
        urls.append(line)
    if duplicates:
        print(f"提示：忽略重复 URL {duplicates} 条。")
    return urls


def run_fetch(
    urls_file: str | Path,
    *,
    out: str | Path | None = None,
    dry_run: bool = True,
    delay: float = 2.0,
    max_bytes: int = 2_000_000,
    timeout: float = 10.0,
) -> int:
    """采集脚手架入口；``dry_run`` 时绝不联网、绝不写文件。"""
    if max_bytes <= 0:
        print(f"--max-bytes 必须为正整数，实际 {max_bytes}。")
        return 2
    if delay < 0:
        print(f"--delay 不能为负，实际 {delay}。")
        return 2

    policy = corpus_fetch.FetchPolicy(
        delay_seconds=delay,
        timeout_s=timeout,
        max_bytes=max_bytes,
    )
    mode = "dry-run（离线检查，不发请求）" if dry_run else "--confirm（真实抓取）"
    print(f"人类日常语料采集：{mode}")
    print(f"规则：{FETCH_RULES}")

    urls = read_url_file(urls_file)
    if urls is None:
        return 2
    if not urls:
        print("URL 列表为空（只有注释或空行），什么也没做。")
        return 2

    print(f"URL 文件：{Path(urls_file)}（{len(urls)} 条）")
    print(
        f"policy：delay={policy.delay_seconds}s timeout={policy.timeout_s}s "
        f"max_bytes={policy.max_bytes} respect_robots={policy.respect_robots}"
    )

    plan = corpus_fetch.plan_fetch(urls, policy)
    valid_urls = [item["url"] for item in plan if item["valid"]]

    if dry_run:
        print(corpus_fetch.format_plan(plan))
        print(
            f"汇总：{len(plan)} 条 URL，其中有效 {len(valid_urls)} 条；"
            "robots 未检查（dry-run 不联网），未写任何文件。"
        )
        print(
            f"提示：确认选源后加 --confirm 才真实抓取；--out 默认 {DEFAULT_RAW_DIR}"
            "（data/ 已在 .gitignore）。"
        )
        return 0

    if not valid_urls:
        print("没有可抓取的 http/https URL，已退出。")
        return 1
    if len(valid_urls) != len(plan):
        print(f"提示：跳过 {len(plan) - len(valid_urls)} 条非 http/https URL。")

    target_dir = Path(out) if out is not None else DEFAULT_RAW_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"建输出目录失败：{target_dir}（{exc}）")
        return 2
    print(f"输出目录：{target_dir}")

    results = corpus_fetch.fetch_many(valid_urls, policy)
    manifest_path = target_dir / "manifest.jsonl"
    written = 0
    failures = 0
    try:
        manifest_handle = manifest_path.open("a", encoding="utf-8")
    except OSError as exc:
        print(f"打不开 manifest：{manifest_path}（{exc}）")
        return 2

    with manifest_handle:
        for result in results:
            if not result["ok"]:
                failures += 1
                print(f"[fail] {result['url']}  error={result['error']}")
                continue

            snippets = corpus_fetch.split_snippets(result["text"])
            base_name = Path(corpus_fetch.raw_filename(result["url"]))
            text_path = target_dir / base_name.name
            suffix = 1
            while text_path.exists():  # 同 host 同秒撞名时加序号，绝不覆盖已采文件
                text_path = target_dir / f"{base_name.stem}-{suffix}{base_name.suffix}"
                suffix += 1
            try:
                text_path.write_text(result["text"], encoding="utf-8")
            except OSError as exc:
                failures += 1
                print(f"[fail] {result['url']}  error=写文件失败 {exc}")
                continue

            entry = corpus_fetch.manifest_entry(result, policy, len(snippets))
            manifest_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1
            print(
                f"[ok]   {result['url']}  status={result['status']} bytes={result['bytes']} "
                f"encoding={result['encoding']} 片段={len(snippets)} → {text_path.name}"
            )

    print(
        f"汇总：成功 {written} / 失败 {failures}；"
        f"manifest 追加 {written} 行：{manifest_path}"
    )
    print("提示：原始文本仍需人工核对并做 PII 脱敏后才能进训练集；本命令不自动入库。")
    return 0 if written else 1


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


def render_markdown(dir_report: DirReport, coverage_report: CoverageReport, target: Path) -> str:
    """把校验 + 覆盖结果拼成一份 Markdown 报告。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "# 人类作息与社交语料覆盖报告",
        "",
        f"- 语料目录：`{target}`",
        f"- 生成时间：{stamp}",
        f"- 生成命令：`python scripts/life_corpus.py report {target}`",
        "- 说明：本报告只做静态检查，不包含真实 PII 原文，命中的 PII 均已遮掩。",
        "",
        "## 1. 校验结果",
        "",
        "| 文件 | 类型 | 记录 | 错误 | PII | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for file_report in dir_report.files:
        status = "通过" if file_report.ok else "未通过"
        lines.append(
            f"| `{file_report.path}` | {file_report.kind or '未知'} | {file_report.record_count} "
            f"| {file_report.error_count} | {file_report.pii_count} | {status} |"
        )
    if not dir_report.files:
        lines.append("| （空目录） | - | 0 | 0 | 0 | 未通过 |")

    lines.extend(["", f"汇总：文件 {len(dir_report.files)} / 记录 {dir_report.record_count} / "
                      f"错误 {dir_report.error_count} / PII {dir_report.pii_count} / "
                      f"结论 {'通过' if dir_report.ok else '未通过'}", ""])

    lines.append("### 错误明细")
    lines.append("")
    if dir_report.issues:
        for issue in dir_report.issues:
            lines.append(f"- `{issue.format()}`")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("### PII 明细（仅遮掩值）")
    lines.append("")
    if dir_report.pii:
        for finding in dir_report.pii:
            label = PII_LABELS.get(finding.kind, finding.kind)
            where = f" @ `{finding.path}`" if finding.path else ""
            lines.append(f"- {label} `{finding.masked}`{where}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.extend(
        [
            "## 2. 覆盖矩阵",
            "",
            "```text",
            format_coverage(coverage_report),
            "```",
            "",
            "## 3. 缺口清单",
            "",
        ]
    )
    if coverage_report.gaps:
        for gap in coverage_report.gaps:
            lines.append(f"- `{gap.format()}`")
    else:
        lines.append("- 无缺口")
    lines.append("")

    lines.extend(["## 4. 下一步建议", ""])
    if coverage_report.critical_gap_count:
        lines.append(f"1. 先补 {coverage_report.critical_gap_count} 项严重缺口：24 小时必须铺满、"
                     "工作日/休息日必须有样本、社交关系至少 3 种。")
    else:
        lines.append("1. 严重缺口已清零，可以按 `warn` 清单逐步扩充覆盖面。")
    if dir_report.error_count or dir_report.pii_count:
        lines.append("2. 先处理校验错误与 PII 命中，未脱敏的语料不要进入训练集。")
    else:
        lines.append("2. 校验与 PII 扫描均通过，可以交给采集脚本继续扩样。")
    lines.append("3. 新增记录后重跑 `validate --strict` 与 `coverage`，保持本报告随语料更新。")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def _ensure_utf8_stdout() -> None:
    """重定向到管道/文件时强制 UTF-8，避免 Windows 控制台默认 cp936 把中文写成乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # 极少数环境不允许重配，忽略即可
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="life_corpus",
        description="人类作息与社交语料管线：校验、覆盖分析、报告生成、采集脚手架"
        "（标准库；校验类子命令离线，fetch 默认 dry-run）",
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="{validate,coverage,report,fetch}"
    )

    validate_parser = subparsers.add_parser("validate", help="校验 JSONL 语料")
    validate_parser.add_argument("path", nargs="?", default=str(DEFAULT_CORPUS_DIR), help="语料目录")
    validate_parser.add_argument("--strict", action="store_true", help="有错误或 PII 时以退出码 1 结束")
    validate_parser.add_argument("--no-pii", action="store_true", help="跳过 PII 扫描")

    coverage_parser = subparsers.add_parser("coverage", help="输出覆盖矩阵与缺口清单")
    coverage_parser.add_argument("path", nargs="?", default=str(DEFAULT_CORPUS_DIR), help="语料目录")
    coverage_parser.add_argument("--strict", action="store_true", help="存在严重缺口时以退出码 1 结束")

    report_parser = subparsers.add_parser("report", help="生成 Markdown 报告")
    report_parser.add_argument("path", nargs="?", default=str(DEFAULT_CORPUS_DIR), help="语料目录")
    report_parser.add_argument("--out", default=None, help="报告输出路径，默认 <目录>/report.md")
    report_parser.add_argument("--strict", action="store_true", help="有错误、PII 或严重缺口时以退出码 1 结束")
    report_parser.add_argument("--no-pii", action="store_true", help="跳过 PII 扫描")

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="采集原始页面（默认 dry-run；--confirm 才联网）",
        description=(
            "人类日常语料采集脚手架：只读 GET，遵守 robots.txt；"
            "不登录、不带 cookie、不绕验证码。默认 dry-run：只解析 URL、打印 robots 状态，"
            "不联网也不写文件；加 --confirm 才会逐条抓取并落盘原文 + manifest.jsonl。"
        ),
    )
    fetch_parser.add_argument("--urls", required=True, help="URL 列表文件，一行一条，# 开头为注释")
    fetch_parser.add_argument(
        "--out",
        default=None,
        help=f"原文与 manifest 输出目录，默认 {DEFAULT_RAW_DIR}（data/ 已 gitignore）",
    )
    fetch_mode = fetch_parser.add_mutually_exclusive_group()
    fetch_mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="只解析 URL 并打印计划，不联网、不写文件（默认）",
    )
    fetch_mode.add_argument(
        "--confirm",
        dest="dry_run",
        action="store_false",
        help="确认选源后真实抓取（仍在 robots 允许范围内，逐条 sleep）",
    )
    fetch_parser.add_argument("--delay", type=float, default=2.0, help="两次请求之间等待秒数，默认 2.0")
    fetch_parser.add_argument("--max-bytes", type=int, default=2_000_000, help="单页最多读取字节数，默认 2000000")
    fetch_parser.add_argument("--timeout", type=float, default=10.0, help="单次请求超时秒数，默认 10.0")
    fetch_parser.set_defaults(dry_run=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口；返回进程退出码，方便测试直接调用。"""
    _ensure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return run_validate(args.path, strict=args.strict, scan_pii=not args.no_pii)
    if args.command == "coverage":
        return run_coverage(args.path, strict=args.strict)
    if args.command == "report":
        return run_report(args.path, out=args.out, strict=args.strict, scan_pii=not args.no_pii)
    if args.command == "fetch":
        return run_fetch(
            args.urls,
            out=args.out,
            dry_run=args.dry_run,
            delay=args.delay,
            max_bytes=args.max_bytes,
            timeout=args.timeout,
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
