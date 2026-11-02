"""各格式的对白解析器。

每种格式只做一件事：把文本切成 {ts, role, text} 的线性序列，外加原话池。
不做记忆抽取，不做时间推断——那些在 ingest.py 里。
﻿‍​‍﻿﻿‍​﻿​﻿﻿‌​﻿​﻿‌﻿‍﻿﻿﻿‍​﻿​﻿﻿﻿‍﻿​﻿"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.memory.declare import Declared

_RE_WHO = re.compile(r"^(?P<who>[^：:\s]{1,8})\s*[：:]\s*(?P<text>.+)$")
_RE_SCENE = re.compile(r"^【[^】]{0,24}】\s*(?P<when>.*)$")
_RE_QUOTE = re.compile(r"^\s*(?P<freq>\d{1,6})\s{2,}(?P<text>\S.*)$")
_RE_SECTION = re.compile(r"^\s*[—─━\-]{2,}\s*(?P<title>[^—─━\-]{0,24}?)\s*[—─━\-]{2,}\s*$")
_RE_TS_WHO = re.compile(
    r"^\[?(?P<ts>\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(?P<who>[^：:\s]{1,10})\s*[：:]\s*(?P<text>.+)$"
)
_RE_DATE = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")

_HOURS = {
    "凌晨": 2, "清晨": 6, "早上": 7, "早晨": 7, "上午": 9,
    "中午": 12, "下午": 15, "傍晚": 18, "晚上": 20, "夜里": 22, "深夜": 1,
}


@dataclass
class Parsed:
    events: list[dict[str, Any]] = field(default_factory=list)
    quotes: list[tuple[str, int]] = field(default_factory=list)
    remember: list[str] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)
    warning: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def ts_from_when(when: str, seq: int = 0) -> str | None:
    m = _RE_DATE.search(when or "")
    if not m:
        return None
    hour = 20
    for word, h in _HOURS.items():
        if word in when:
            hour = h
            break
    minute = (seq * 3) % 60
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T{hour:02d}:{minute:02d}:00"


def _blank_to_none(ts: str | None) -> str | None:
    return ts


def parse_scene(lines: list[str], d: Declared) -> Parsed:
    p = Parsed()
    cur_ts: str | None = None
    seq = 0
    unknown: dict[str, int] = {}

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        m = _RE_SCENE.match(s)
        if m:
            cur_ts = ts_from_when(m.group("when") or s)
            seq = 0
            continue
        m = _RE_WHO.match(s)
        if m:
            who = m.group("who").strip()
            role = d.role_of(who)
            if role == "unknown":
                unknown[who] = unknown.get(who, 0) + 1
                role = "me"
            p.events.append(
                {"ts": cur_ts, "role": role, "who": who, "text": m.group("text").strip()}
            )
            seq += 1
        elif p.events:
            p.events[-1]["text"] += " " + s

    if unknown:
        top = sorted(unknown.items(), key=lambda kv: -kv[1])[:3]
        p.warning.append(
            "以下说话人标签没在声明里出现，已按「我」处理："
            + "、".join(f"{k}({v})" for k, v in top)
        )
    p.stats = {"lines": len(lines), "speakers": dict(unknown) and unknown or {}}
    return p


def parse_style_library(lines: list[str], d: Declared) -> Parsed:
    p = Parsed()
    skipped = 0
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _RE_QUOTE.match(raw)
        if not m:
            skipped += 1
            continue
        text = m.group("text").strip()
        if 2 <= len(text) <= 60:
            p.quotes.append((text, int(m.group("freq"))))
    p.quotes.sort(key=lambda q: -q[1])
    p.stats = {"quotes": len(p.quotes), "skipped": skipped}
    if skipped:
        p.warning.append(f"台词库里有 {skipped} 行不符合「频次  台词」格式，已跳过")
    return p


def parse_training_pairs(lines: list[str], d: Declared) -> Parsed:
    p = Parsed()
    bad = 0
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            bad += 1
            continue
        ctx = obj.get("context") or obj.get("prompt") or []
        reply = obj.get("reply") or obj.get("response") or obj.get("completion") or ""
        if isinstance(ctx, str):
            ctx = [ctx]
        for item in ctx:
            p.events.append({"ts": None, "role": "me", "who": "对方", "text": str(item).strip()})
        for piece in str(reply).split("\n"):
            if piece.strip():
                p.events.append({"ts": None, "role": "her", "who": d.target or "她", "text": piece.strip()})
    p.stats = {"pairs": len(p.events), "bad_lines": bad}
    if bad:
        p.warning.append(f"{bad} 行 JSON 解析失败，已跳过")
    return p


def parse_letter(text: str, d: Declared) -> Parsed:
    """迁移信：头部已由 declare 解析，这里切正文 + 人工钦定段。"""
    p = Parsed()
    sections = _split_sections(text)
    body = sections.get("正文", "")
    if not body:
        body = "\n".join(body_lines for title, body_lines in sections.items() if title not in ("说给你听的", "不要记得的", "信到此为止"))
    p.events = parse_scene(body.splitlines(), d).events
    p.remember = _bullets(sections.get("说给你听的", ""))
    p.forget = _bullets(sections.get("不要记得的", ""))
    p.stats = {"sections": list(sections.keys()), "events": len(p.events)}
    return p


def _split_sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur = "头"
    for raw in text.splitlines():
        m = _RE_SECTION.match(raw)
        if m:
            cur = m.group("title").strip() or cur
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(raw)
    return {k: "\n".join(v) for k, v in out.items()}


def _bullets(block: str) -> list[str]:
    items: list[str] = []
    for raw in block.splitlines():
        s = raw.strip().lstrip("-·•*　 ").strip()
        if not s or s.startswith("#"):
            continue
        if len(s) >= 4:
            items.append(s)
    return items


def parse_generic(lines: list[str], d: Declared) -> Parsed:
    p = Parsed()
    cur_ts: str | None = None
    unknown: dict[str, int] = {}
    seq = 0
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _RE_SCENE.match(s)
        if m:
            cur_ts = ts_from_when(m.group("when") or s)
            seq = 0
            continue
        mt = _RE_TS_WHO.match(s)
        if mt:
            who = mt.group("who").strip()
            role = d.role_of(who)
            if role == "unknown":
                unknown[who] = unknown.get(who, 0) + 1
                role = "me"
            ts = mt.group("ts").replace("/", "-").replace(".", "-").replace(" ", "T")
            if len(ts) == 16:
                ts += ":00"
            p.events.append({"ts": ts, "role": role, "who": who, "text": mt.group("text").strip()})
            seq += 1
            continue
        m = _RE_WHO.match(s)
        if m and len(m.group("who")) <= 6:
            who = m.group("who").strip()
            role = d.role_of(who)
            if role == "unknown":
                unknown[who] = unknown.get(who, 0) + 1
                role = "me"
            ts = cur_ts
            p.events.append({"ts": ts, "role": role, "who": who, "text": m.group("text").strip()})
            seq += 1
        elif p.events:
            p.events[-1]["text"] += " " + s
        else:
            p.events.append({"ts": None, "role": "me", "who": "我", "text": s})
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: -kv[1])[:3]
        p.warning.append(
            "以下说话人标签没在声明里出现，已按「我」处理："
            + "、".join(f"{k}({v})" for k, v in top)
        )
    return p


def parse_plain(text: str, d: Declared) -> Parsed:
    p = Parsed()
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        p.events.append({"ts": None, "role": "me", "who": "我", "text": s})
    return p
