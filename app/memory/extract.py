from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.memory.surnames import SURNAMES as _SURNAMES


@dataclass
class ExtractedItem:
    kind: str
    content: str
    importance: float


@dataclass
class ExtractResult:
    items: list[ExtractedItem] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# 用户侧：能被抽成"她记得的事实"的句式﻿‍​‌﻿​‍‌‌﻿﻿﻿﻿‌​‌‌﻿​﻿‌‌‍‌‌​​﻿﻿‌‍​﻿﻿
_FACT_PATTERNS: list[tuple[str, float]] = [
    (r"我叫\s*([\u4e00-\u9fffA-Za-z]{1,8})", 0.95),
    (r"我是\s*([^。，,；;！!？?\n]{2,14})", 0.7),
    (r"我今年\s*(\d{1,3})\s*(?:岁|了)", 0.85),
    (r"我住在\s*([^。，,；;！!？?\n]{2,14})", 0.8),
    (r"我在\s*([^。，,；;！!？?\n]{2,16}?)\s*(?:工作|上班|上学)", 0.75),
    (r"我的\s*([\u4e00-\u9fff]{1,6})\s*叫\s*([\u4e00-\u9fffA-Za-z]{1,10})", 0.85),
    (r"我养了\s*(?:一只|一个|条)\s*([^。，,；;！!？?\n]{2,12})", 0.75),
]

_PREF_RE = re.compile(
    r"我[^。，,；;！!？?\n]{0,8}?(不喜欢|最怕|怕|喜欢|讨厌|受不了|习惯)\s*([^。，,；;！!？?\n]{1,18})"
)
_PREF_WEIGHT = {
    "不喜欢": 0.85,
    "最怕": 0.92,
    "怕": 0.88,
    "喜欢": 0.70,
    "讨厌": 0.85,
    "受不了": 0.80,
    "习惯": 0.60,
}

_BOUNDARY_HINTS = ("别再", "不要提", "不许", "别叫我", "别提", "忌讳", "底线")
_DEEP_HINTS = ("我想过", "其实我", "说实话", "从来没人", "第一次跟人说", "我从来没")


def heuristic_extract(events: list[dict[str, Any]], persona: Persona) -> ExtractResult:
    """无 API Key 时的抽取。规则粗，但足以证明导入管线是通的。"""
    items: list[ExtractedItem] = []
    quotes: list[str] = []
    her_names = {persona.name, "她", "TA", "ta", "ai", "AI", "助手", "bot"}
    seen: set[str] = set()

    def add(kind: str, content: str, imp: float) -> None:
        key = f"{kind}:{content}"
        if content and key not in seen and len(content) <= 60:
            seen.add(key)
            items.append(ExtractedItem(kind, content, imp))

    for e in events:
        text = str(e.get("text", "")).strip()
        role = str(e.get("role", "user"))
        if not text:
            continue

        if role == "her" or str(e.get("who", "")) in her_names:
            if 4 <= len(text) <= 42 and "http" not in text:
                clean = text.strip("\"'“”「」 ")
                if clean and clean not in quotes:
                    quotes.append(clean)
            continue

        for pat, imp in _FACT_PATTERNS:
            for m in re.finditer(pat, text):
                g = [x for x in m.groups() if x]
                if len(g) == 2:
                    add("fact", f"他的{g[0]}叫{g[1]}", imp)
                elif g:
                    add("fact", m.group(0).strip(), imp)

        for m in _PREF_RE.finditer(text):
            verb, obj = m.group(1), m.group(2).strip()
            obj = obj.rstrip("，。！!？? ")
            if len(obj) >= 2:
                add("preference", f"{verb}{obj}", _PREF_WEIGHT.get(verb, 0.7))

        for hint in _BOUNDARY_HINTS:
            if hint in text:
                add("boundary", text[:50], 0.95)
                break

        for hint in _DEEP_HINTS:
            if hint in text:
                add("event", text[:56], 0.8)
                break

        if len(text) >= 16 and any(k in text for k in ("昨天", "今天", "上周", "那次", "记得")):
            add("event", text[:56], 0.6)

    # 同一事实按内容长度排序去重后截断，避免噪声淹没 prompt
    items.sort(key=lambda i: i.importance, reverse=True)
    return ExtractResult(items=items[:60], quotes=quotes[:80], stats={"mode": "heuristic"})


def cold_start_from(events: list[dict[str, Any]], persona: Persona) -> dict[str, Any]:
    """从历史记录反推初始好感。宁可低估，也不要把关系一上来就点满。"""
    if not events:
        return {}

    n = len(events)
    user_n = sum(1 for e in events if e.get("role") == "user")
    intimate = sum(
        1
        for e in events
        if any(k in str(e.get("text", "")) for k in ("宝宝", "老婆", "老公", "爱你", "想你", "亲亲", "抱抱"))
    )
    night = sum(
        1
        for e in events
        if _hour_of(str(e.get("ts", ""))) in (0, 1, 2, 3, 4)
    )
    avg_len = sum(len(str(e.get("text", ""))) for e in events) / max(1, n)

    days = _span_days(events)
    base = min(52.0, 6.0 + 7.0 * math.log2(1.0 + n / 60.0))
    night_bonus = 22.0 * (night / max(1, n))
    intimate_bonus = min(18.0, 14.0 * (intimate / max(1, user_n)))
    length_bonus = min(6.0, avg_len / 12.0)

    raw_affinity = base + night_bonus + intimate_bonus + length_bonus
    # 迁移上限：一次导入不把关系点满，剩下的留给你们继续。
    cap = 72.0
    affinity = round(min(cap, raw_affinity), 1)
    trust = round(min(90.0, affinity * 0.78 + 8.0), 1)
    attraction = round(min(85.0, affinity * 0.48 + intimate_bonus), 1)

    reasons = [f"共 {n} 条记录，跨 {days} 天"]
    if night / max(1, n) > 0.12:
        reasons.append(f"凌晨对话占 {night * 100 // max(1, n)}%")
    if intimate:
        reasons.append(f"亲密称呼出现 {intimate} 次")
    if raw_affinity > cap:
        reasons.append(f"按迁移上限封顶在 {cap:.0f}，没有一次点满")
    return {
        "affinity": affinity,
        "trust": trust,
        "attraction": attraction,
        "reason": "，".join(reasons),
        "samples": n,
        "clipped": raw_affinity > cap,
        "raw_affinity": round(raw_affinity, 1),
    }


# ---------------------------------------------------------------- 污染扫描

# 姓氏表统一在 app/memory/surnames.py（以前这里抄了 79 条，scrub 那边 157 条）
_RE_LATIN_AFTER = re.compile(r"(?:跟|和|对|找|问|叫|说|帮)\s*([A-Za-z][A-Za-z0-9_]{1,7})")
_RE_VOCATIVE = re.compile(r"(?:^|[，。！？\s…~～])(?P<n>[\u4e00-\u9fff]{2,4})(?=啊|呀|呢|哈)")
_COMMON_LATIN = {
    "ai", "ok", "hh", "app", "api", "id", "qq", "wx", "vx", "wifi", "cpu", "gpu", "ios", "pc",
    "tv", "bgm", "cp", "dd", "gg", "mm", "emmm", "emm", "bo", "nm", "sb", "yyds", "emo", "vlog",
}


def scan_contamination(events: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """找「严格版」也可能漏掉的第三方痕迹。

    只报可疑，不自动删：猜错一次就会把她认识的人抹掉，比留着更糟。
    """
    sample = events[:20000]
    latin: Counter[str] = Counter()
    names: Counter[str] = Counter()

    for e in sample:
        text = str(e.get("text", ""))
        for m in _RE_LATIN_AFTER.finditer(text):
            tok = m.group(1)
            if tok.lower() not in _COMMON_LATIN:
                latin[tok] += 1
        for m in _RE_VOCATIVE.finditer(" " + text):
            tok = m.group("n")
            if tok[0] in _SURNAMES and not any(ch in tok for ch in "的了是我你他她它"):
                names[tok] += 1

    out: list[dict[str, Any]] = []
    for tok, c in latin.most_common(limit):
        if c >= 2:
            out.append({"token": tok, "count": c, "kind": "latin", "reason": "疑似第三方缩写"})
    for tok, c in names.most_common(limit):
        if tok in _NAME_STOP or not (2 <= c <= 40):
            continue
        out.append({"token": tok, "count": c, "kind": "name", "reason": "疑似人名（称呼语气）"})
    out.sort(key=lambda x: -x["count"])
    return out[:limit]


_NAME_STOP = {"何必", "何况", "何止", "马上", "于是", "周围", "周末", "周期", "安排", "安全", "常规"}


def _hour_of(ts: str) -> int:
    m = re.search(r"[T ](\d{1,2}):", ts)
    return int(m.group(1)) if m else -1


def _span_days(events: list[dict[str, Any]]) -> int:
    stamps = [str(e.get("ts", ""))[:10] for e in events if e.get("ts")]
    if not stamps:
        return 0
    return max(1, len(set(stamps)))
