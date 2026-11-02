"""JSONL 读取、逐行校验与目录汇总。

约定：
- 一个文件对应一种语料类型，类型从文件名推断：``rhythm.sample.jsonl`` / ``life-activity.jsonl``
  都能识别，只要文件名里出现 ``rhythm`` / ``activity`` / ``social_scene`` / ``snippet`` 之一。
- 行号从 1 开始，指向物理行（空行不跳过行号，但空行本身不校验），便于直接在编辑器里定位。
- 每行先做 JSON 解析，再做 schema 校验，最后做 PII 扫描；PII 命中同样记为 issue，严格模式一票否决。
﻿‍﻿‌​﻿﻿﻿‌​﻿​﻿‌‌‍﻿‌‌﻿‍‍﻿‍‌‍﻿‍​﻿‌‍​﻿"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, Final

from .pii import PII_LABELS, PiiFinding, scan_object
from .schema import KINDS, validate_record

_KIND_TOKENS: Final[frozenset[str]] = frozenset(KINDS)
_SPLIT_PATTERN_STRICT: Final = re.compile(r"[.\-]+")
_SPLIT_PATTERN: Final = re.compile(r"[._\-]+")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Issue:
    """一条校验问题。``line_no`` 为 0 表示文件级问题。"""

    path: str
    line_no: int
    kind: str
    code: str
    field: str
    message: str
    record_id: str = ""

    def format(self) -> str:
        location = f"{self.path}:{self.line_no}" if self.line_no else self.path
        prefix = f"[{self.kind}] " if self.kind else ""
        field_part = f"{self.field}: " if self.field else ""
        return f"{location} {prefix}{field_part}{self.message}（{self.code}）"


@dataclass(slots=True)
class FileReport:
    """单个 JSONL 文件的校验结果。"""

    path: str
    kind: str | None
    record_count: int = 0
    issues: list[Issue] = dc_field(default_factory=list)
    pii: list[PiiFinding] = dc_field(default_factory=list)
    ids: dict[str, int] = dc_field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return len(self.issues)

    @property
    def pii_count(self) -> int:
        return len(self.pii)

    @property
    def ok(self) -> bool:
        return not self.issues and not self.pii


@dataclass(slots=True)
class DirReport:
    """目录级汇总。"""

    root: str
    files: list[FileReport] = dc_field(default_factory=list)
    skipped: list[str] = dc_field(default_factory=list)

    @property
    def issues(self) -> list[Issue]:
        return [issue for file_report in self.files for issue in file_report.issues]

    @property
    def pii(self) -> list[PiiFinding]:
        return [finding for file_report in self.files for finding in file_report.pii]

    @property
    def error_count(self) -> int:
        return sum(file_report.error_count for file_report in self.files)

    @property
    def pii_count(self) -> int:
        return sum(file_report.pii_count for file_report in self.files)

    @property
    def record_count(self) -> int:
        return sum(file_report.record_count for file_report in self.files)

    @property
    def ok(self) -> bool:
        return self.error_count == 0 and self.pii_count == 0

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for file_report in self.files:
            if file_report.kind is None:
                continue
            counts[file_report.kind] = counts.get(file_report.kind, 0) + file_report.record_count
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files": len(self.files),
            "records": self.record_count,
            "errors": self.error_count,
            "pii": self.pii_count,
            "ok": self.ok,
            "kinds": self.kind_counts(),
        }


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------


def infer_kind(filename: str) -> str | None:
    """从文件名推断语料类型；识别不了返回 ``None``。

    先按 ``.`` 和 ``-`` 切（这样 ``social_scene.sample.jsonl`` 里的 ``social_scene`` 不会被拆散），
    切不出来再按 ``_`` 切（兼容 ``life_activity.jsonl`` 这种写法）。
    """
    name = Path(filename).name
    if name.endswith(".jsonl"):
        name = name[: -len(".jsonl")]
    for pattern in (_SPLIT_PATTERN_STRICT, _SPLIT_PATTERN):
        for token in pattern.split(name.lower()):
            if token in _KIND_TOKENS:
                return token
    return None


def iter_jsonl_files(root: Path) -> list[Path]:
    """递归列出目录下所有 ``*.jsonl``（按相对路径排序，保证输出稳定）。"""
    files = [path for path in root.rglob("*.jsonl") if path.is_file()]
    return sorted(files, key=lambda path: str(path.relative_to(root)).lower())


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    """逐行读取 JSONL，产出 ``(行号, 对象或 None, 错误说明或 None)``；空行跳过。"""
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                yield line_no, None, f"JSON 解析失败：{exc.msg}（第 {exc.colno} 列）"
                continue
            if not isinstance(value, Mapping):
                yield line_no, None, f"每行必须是 JSON 对象，实际是 {type(value).__name__}"
                continue
            yield line_no, dict(value), None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_file(
    path: str | Path,
    *,
    kind: str | None = None,
    scan_pii: bool = True,
) -> FileReport:
    """校验单个 JSONL 文件；``kind`` 不传时按文件名推断。"""
    file_path = Path(path)
    display = file_path.name
    resolved_kind = kind or infer_kind(file_path.name)
    report = FileReport(path=display, kind=resolved_kind)

    if not file_path.exists():
        report.issues.append(Issue(display, 0, resolved_kind or "", "file_missing", "", "文件不存在"))
        return report
    if not file_path.is_file():
        report.issues.append(Issue(display, 0, resolved_kind or "", "not_a_file", "", "不是普通文件"))
        return report
    if resolved_kind is None:
        report.issues.append(
            Issue(
                display,
                0,
                "",
                "unknown_kind",
                "",
                f"无法从文件名推断 kind，文件名需包含 {' / '.join(KINDS)} 之一",
            )
        )
        return report

    for line_no, record, error in read_jsonl(file_path):
        if error is not None or record is None:
            report.issues.append(Issue(display, line_no, resolved_kind, "bad_json", "", error or "JSON 解析失败"))
            continue

        report.record_count += 1
        raw_id = record.get("id")
        record_id = raw_id if isinstance(raw_id, str) else ""

        for field_error in validate_record(resolved_kind, record):
            report.issues.append(
                Issue(
                    display,
                    line_no,
                    resolved_kind,
                    field_error.code,
                    field_error.field,
                    field_error.message,
                    record_id,
                )
            )

        if record_id:
            first_line = report.ids.get(record_id)
            if first_line is not None:
                report.issues.append(
                    Issue(
                        display,
                        line_no,
                        resolved_kind,
                        "duplicate_id",
                        "id",
                        f"id {record_id!r} 与第 {first_line} 行重复",
                        record_id,
                    )
                )
            else:
                report.ids[record_id] = line_no

        if scan_pii:
            hits = scan_object(record)
            report.pii.extend(hits)
            for hit in hits:
                label = PII_LABELS.get(hit.kind, hit.kind)
                report.issues.append(
                    Issue(
                        display,
                        line_no,
                        resolved_kind,
                        "pii_found",
                        hit.path,
                        f"疑似{label} {hit.masked}，请脱敏后再入库",
                        record_id,
                    )
                )

    if report.record_count == 0 and not any(issue.code == "bad_json" for issue in report.issues):
        report.issues.append(Issue(display, 0, resolved_kind, "empty_file", "", "文件里没有任何记录"))

    return report


def validate_dir(dir_path: str | Path, *, scan_pii: bool = True) -> DirReport:
    """校验整个目录：递归扫描 JSONL，汇总文件/记录/错误/PII，并检查跨文件 id 重复。"""
    root = Path(dir_path)
    report = DirReport(root=str(root))
    if not root.exists():
        report.files.append(
            FileReport(path=root.name, kind=None, issues=[Issue(root.name, 0, "", "dir_missing", "", "目录不存在")])
        )
        return report
    if not root.is_dir():
        report.files.append(
            FileReport(path=root.name, kind=None, issues=[Issue(root.name, 0, "", "not_a_dir", "", "不是目录")])
        )
        return report

    jsonl_files = iter_jsonl_files(root)
    for path in jsonl_files:
        file_report = validate_file(path, scan_pii=scan_pii)
        file_report.path = str(path.relative_to(root))
        report.files.append(file_report)

    known = {str(path.relative_to(root)) for path in jsonl_files}
    report.skipped = sorted(
        path.name for path in root.iterdir() if path.is_file() and str(path.relative_to(root)) not in known
    )
    _check_global_duplicates(report)
    return report


def _check_global_duplicates(report: DirReport) -> None:
    """跨文件 id 必须唯一，否则训练/检索时会串样本。"""
    seen: dict[str, tuple[str, int]] = {}
    for file_report in report.files:
        for record_id, line_no in file_report.ids.items():
            previous = seen.get(record_id)
            if previous is None:
                seen[record_id] = (file_report.path, line_no)
                continue
            previous_path, previous_line = previous
            file_report.issues.append(
                Issue(
                    file_report.path,
                    line_no,
                    file_report.kind or "",
                    "duplicate_id_global",
                    "id",
                    f"id {record_id!r} 与 {previous_path}:{previous_line} 重复",
                    record_id,
                )
            )


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------


def render_dir_report(report: DirReport) -> str:
    """把目录校验结果渲染成给人看的多行文本。"""
    lines: list[str] = [f"语料目录：{report.root}"]
    if report.skipped:
        shown = ", ".join(report.skipped[:5])
        more = f" 等 {len(report.skipped)} 个" if len(report.skipped) > 5 else ""
        lines.append(f"跳过非 JSONL 文件：{shown}{more}")
    lines.append(f"扫描文件：{len(report.files)} 个")

    for file_report in report.files:
        status = "OK  " if file_report.ok else "FAIL"
        kind = file_report.kind or "unknown"
        lines.append(
            f"  [{status}] {file_report.path:<34} kind={kind:<12}"
            f" 记录 {file_report.record_count:<3} 错误 {file_report.error_count} PII {file_report.pii_count}"
        )

    if report.issues:
        lines.append("")
        lines.append(f"错误明细（{len(report.issues)} 条）：")
        for issue in report.issues:
            lines.append(f"  - {issue.format()}")

    if report.pii:
        lines.append("")
        lines.append(f"PII 明细（{len(report.pii)} 条，仅显示遮掩值）：")
        for finding in report.pii:
            where = f" @ {finding.path}" if finding.path else ""
            label = PII_LABELS.get(finding.kind, finding.kind)
            lines.append(f"  - {label} {finding.masked}{where}")

    verdict = "通过" if report.ok else "未通过"
    lines.append("")
    lines.append(
        f"汇总：文件 {len(report.files)} / 记录 {report.record_count} / "
        f"错误 {report.error_count} / PII {report.pii_count} → {verdict}"
    )
    return "\n".join(lines)
