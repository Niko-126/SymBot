"""PII 检测与遮掩：中国手机号、18 位身份证号、常见邮箱。

策略：
- 一律保守（宁可多报）：手机号要求 ``1[3-9]`` 开头且前后不邻接数字；身份证固定 18 位
  （末位可为 X），前后不邻接字母数字；邮箱按常规字符集匹配。
- 同一段文本里按 邮箱 -> 身份证 -> 手机号 的顺序匹配，避免把邮箱里的数字错认成手机号。
- 检测结果带字段路径与偏移，便于人工复核；对外展示一律用 ``masked``，不要打印 ``value``。
- 纯正则、纯标准库，不联网、不读外部词典。
﻿﻿‍‌​​​﻿​‌﻿‌‌​﻿​‌‍﻿‍﻿​​‌​﻿​﻿﻿﻿‍‌‍﻿"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# ---------------------------------------------------------------------------
# 类型常量
# ---------------------------------------------------------------------------

PHONE: Final[str] = "phone"
ID_CARD: Final[str] = "id_card"
EMAIL: Final[str] = "email"

PII_KINDS: Final[tuple[str, ...]] = (PHONE, ID_CARD, EMAIL)
PII_LABELS: Final[dict[str, str]] = {
    PHONE: "手机号",
    ID_CARD: "身份证号",
    EMAIL: "邮箱",
}

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------

_EMAIL_PATTERN: Final[str] = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
_ID_CARD_PATTERN: Final[str] = r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])"
_PHONE_PATTERN: Final[str] = r"(?<!\d)1[3-9]\d{9}(?!\d)"

_SCAN: Final = re.compile(
    f"(?P<{EMAIL}>{_EMAIL_PATTERN})|(?P<{ID_CARD}>{_ID_CARD_PATTERN})|(?P<{PHONE}>{_PHONE_PATTERN})"
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PiiFinding:
    """一条 PII 命中。``path`` 形如 ``messages[0].text``；偏移基于原字符串。"""

    kind: str
    value: str
    masked: str
    path: str
    start: int
    end: int

    def format(self) -> str:
        label = PII_LABELS.get(self.kind, self.kind)
        where = f"（{self.path}）" if self.path else ""
        return f"{label} {self.masked}{where}"


# ---------------------------------------------------------------------------
# 遮掩
# ---------------------------------------------------------------------------


def mask_phone(value: str) -> str:
    """手机号保留前 3 后 4，中间 4 位掩掉：138****8000。"""
    return f"{value[:3]}****{value[-4:]}" if len(value) >= 7 else "*" * len(value)


def mask_id_card(value: str) -> str:
    """身份证保留前 6 后 4，中间 8 位掩掉：110101********1234。"""
    return f"{value[:6]}********{value[-4:]}" if len(value) >= 10 else "*" * len(value)


def mask_email(value: str) -> str:
    """邮箱保留首字母与域名：a***@example.com。"""
    local, separator, domain = value.partition("@")
    if not separator:
        return "*" * len(value)
    head = local[:1] if local else "*"
    return f"{head}***@{domain}"


_MASKERS: Final[dict[str, Callable[[str], str]]] = {
    PHONE: mask_phone,
    ID_CARD: mask_id_card,
    EMAIL: mask_email,
}


def mask_value(kind: str, value: str) -> str:
    """按类型遮掩单个值；未知类型整体打码。"""
    masker = _MASKERS.get(kind)
    if masker is None:
        return "*" * len(value)
    return masker(value)


# ---------------------------------------------------------------------------
# 文本级检测
# ---------------------------------------------------------------------------


def scan_text(text: str, path: str = "") -> list[PiiFinding]:
    """扫描一段文本，返回全部命中（按出现顺序）。"""
    findings: list[PiiFinding] = []
    for match in _SCAN.finditer(text):
        kind = match.lastgroup
        if kind is None:  # pragma: no cover - 组合正则必然带命名分组
            continue
        raw = match.group(0)
        findings.append(
            PiiFinding(
                kind=kind,
                value=raw,
                masked=mask_value(kind, raw),
                path=path,
                start=match.start(),
                end=match.end(),
            )
        )
    return findings


def mask_text(text: str, path: str = "") -> tuple[str, list[PiiFinding]]:
    """返回 ``(遮掩后的文本, 命中列表)``；无命中时原样返回。"""
    findings = scan_text(text, path)
    if not findings:
        return text, []
    pieces: list[str] = []
    cursor = 0
    for finding in findings:
        pieces.append(text[cursor : finding.start])
        pieces.append(finding.masked)
        cursor = finding.end
    pieces.append(text[cursor:])
    return "".join(pieces), findings


# ---------------------------------------------------------------------------
# 对象级检测（递归 JSON 结构）
# ---------------------------------------------------------------------------


def _iter_strings(node: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, str):
        yield path, node
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _iter_strings(value, child)
        return
    if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
        for index, value in enumerate(node):
            yield from _iter_strings(value, f"{path}[{index}]")


def scan_object(obj: Any, path: str = "") -> list[PiiFinding]:
    """递归扫描 JSON 结构里的所有字符串，返回命中列表。"""
    findings: list[PiiFinding] = []
    for item_path, text in _iter_strings(obj, path):
        findings.extend(scan_text(text, item_path))
    return findings


def mask_object(obj: Any, path: str = "") -> tuple[Any, list[PiiFinding]]:
    """深拷贝并遮掩：返回 ``(遮掩后的副本, 命中列表)``，不修改入参。"""
    findings: list[PiiFinding] = []

    def walk(node: Any, current: str) -> Any:
        if isinstance(node, str):
            masked, hits = mask_text(node, current)
            findings.extend(hits)
            return masked
        if isinstance(node, Mapping):
            return {
                key: walk(value, f"{current}.{key}" if current else str(key))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [walk(value, f"{current}[{index}]") for index, value in enumerate(node)]
        if isinstance(node, tuple):
            return tuple(walk(value, f"{current}[{index}]") for index, value in enumerate(node))
        return node

    return walk(obj, path), findings


def scan_and_mask(obj: Any, path: str = "") -> tuple[list[PiiFinding], Any]:
    """对外主入口：返回 ``(findings, mask)``，满足"先报命中、再给脱敏副本"的用法。"""
    masked, findings = mask_object(obj, path)
    return findings, masked


def summarize(findings: Sequence[PiiFinding]) -> dict[str, int]:
    """按类型汇总命中数量，供报告使用。"""
    summary = {kind: 0 for kind in PII_KINDS}
    for finding in findings:
        summary[finding.kind] = summary.get(finding.kind, 0) + 1
    return summary
