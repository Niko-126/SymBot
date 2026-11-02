from __future__ import annotations

import hashlib
import json
import pathlib
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from app.core import db
from app.memory import declare as declare_mod
from app.memory import formats as fmt_mod
from app.memory import store
from app.memory.extract import cold_start_from, heuristic_extract, scan_contamination
from app.persona.load import Persona

ACCEPT_SUFFIX = {".txt", ".md", ".jsonl", ".json", ".log"}
MAX_QUOTE_MEMORIES = 120
EXTRACT_CHUNK_CAP = 300

# 语料型文件不做规则抽取：它们的正文是日常口语，规则只会产出"不许""我叫啥"这类噪声。﻿‌﻿‌‌‍﻿‍‌﻿‍﻿‍‌﻿‌‌﻿‌‍‍‍﻿​﻿﻿‌‌‍‌​​﻿﻿
# 事实抽取留给 M1 的模型抽取（需要上下文才能判断哪句是"他的事"）。
CORPUS_FORMATS = {"scene_corpus", "style_library", "training_pairs"}


def read_text(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _fill_timestamps(events: list[dict[str, Any]]) -> None:
    """没有时间戳的记录：以今天为终点，按 45 秒一条倒推。"""
    if not any(e.get("ts") is None for e in events):
        for e in events:
            if len(e["ts"]) == 16:
                e["ts"] = e["ts"] + ":00"
        return
    end = datetime.now() - timedelta(hours=6)
    step = timedelta(seconds=45)
    cursor = end - step * len(events)
    for e in events:
        if e.get("ts") is None:
            cursor += step
            e["ts"] = cursor.isoformat(timespec="seconds")
        elif len(e["ts"]) == 16:
            e["ts"] = e["ts"] + ":00"


def chunk(events: list[dict[str, Any]], max_chars: int = 1400) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for e in events:
        n = len(str(e.get("text", "")))
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _check_time_conflict(decl: declare_mod.Declared, events: list[dict[str, Any]]) -> list[str]:
    if not decl.time_start:
        return []
    stamps = [e["ts"][:10] for e in events if e.get("ts")]
    if not stamps:
        return []
    lo, hi = min(stamps), max(stamps)
    if lo < decl.time_start or hi > decl.time_end:
        return [
            f"声明的时间范围是 {decl.time_start} ~ {decl.time_end}，"
            f"但正文里的日期是 {lo} ~ {hi}。已按正文日期导入，"
            "请确认哪一个是准的（它会影响到记忆的新鲜度权重）。"
        ]
    return []


def ingest(
    path: pathlib.Path,
    persona: Persona,
    apply_cold_start: bool = True,
    max_events: int = 0,
    force: bool = False,
    extract_facts: bool | None = None,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    if path.suffix.lower() not in ACCEPT_SUFFIX:
        return {"ok": False, "error": f"不支持的文件类型：{path.suffix}"}

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    if not force and db.kv_get(f"ingest:{digest}") == "done":
        return {"ok": True, "already_imported": True, "path": str(path)}

    text = read_text(path)
    decl = declare_mod.parse(path, text, persona)
    warnings: list[str] = list(decl.warning)

    lines = text.splitlines()
    if decl.fmt == "scene_corpus":
        parsed = fmt_mod.parse_scene(lines, decl)
    elif decl.fmt == "style_library":
        parsed = fmt_mod.parse_style_library(lines, decl)
    elif decl.fmt == "training_pairs":
        parsed = fmt_mod.parse_training_pairs(lines, decl)
    elif decl.fmt == "memory_letter":
        parsed = fmt_mod.parse_letter(text, decl)
    elif decl.fmt == "generic_dialogue":
        parsed = fmt_mod.parse_generic(lines, decl)
    else:
        parsed = fmt_mod.parse_plain(text, decl)

    warnings.extend(parsed.warning)
    events = parsed.events
    if max_events and len(events) > max_events:
        events = events[:max_events]
        warnings.append(f"只导入了前 {max_events} 条（测试模式）")

    if not events and not parsed.quotes:
        return {
            "ok": False,
            "error": "没有解析出任何可用的对话或台词",
            "format_detected": decl.fmt,
            "declared": decl.as_dict(),
        }

    _fill_timestamps(events)
    warnings.extend(_check_time_conflict(decl, events))

    # ---- 污染扫描：严格版也会漏，先报出来，不自动删 ----
    contamination = scan_contamination(events) if events else []
    if contamination:
        top = "、".join(f"{c['token']}({c['count']})" for c in contamination[:5])
        warnings.append(f"疑似第三方残留：{top}。导入前建议确认，它们可能会被记成「他认识的人」")

    # ---- 事件：一次事务批量写 ----
    if events:
        db.executemany(
            "INSERT INTO events (ts, role, kind, text, source) VALUES (?, ?, ?, ?, ?)",
            [(e["ts"], e["role"], "text", e["text"], "ingest") for e in events],
        )

    # ---- 原话池：台词库按频次，对白按重复次数 ----
    quote_counter: Counter[str] = Counter()
    for q, freq in parsed.quotes:
        quote_counter[q] += max(1, freq)
    for e in events:
        if e.get("role") != "her":
            continue
        t = str(e.get("text", "")).strip().strip("\"'“”「」 ")
        if 4 <= len(t) <= 42 and "http" not in t and "了" not in t[-2:]:
            quote_counter[t] += 1

    top_quotes = quote_counter.most_common(MAX_QUOTE_MEMORIES)
    for rank, (q, freq) in enumerate(top_quotes):
        importance = round(min(0.95, 0.5 + 0.45 * (1 - rank / max(1, len(top_quotes)))), 2)
        store.add_memory("quote", q, importance, 0.7)

    # ---- 人工钦定的记忆：优先级压过一切算法 ----
    for item in parsed.remember:
        kind = "boundary" if any(k in item for k in ("别", "不要", "不能", "忌讳", "绝对")) else "fact"
        store.add_memory(kind, item, 1.0, 1.0)

    # ---- 规则抽取：只对用户自己写的文本做，语料不碰 ----
    chunks = chunk(events) if events else []
    do_extract = (decl.fmt not in CORPUS_FORMATS) if extract_facts is None else extract_facts
    facts_written = 0
    if do_extract:
        for group in chunks[:EXTRACT_CHUNK_CAP]:
            for item in heuristic_extract(group, persona).items:
                if store.add_memory(item.kind, item.content, item.importance):
                    facts_written += 1
    elif events and not parsed.remember:
        warnings.append("这是语料型文件，已跳过规则抽取：规则会把「不许」「我叫啥」记成回忆。事实抽取留给模型做。")

    # ---- 冷启动 ----
    stats = cold_start_from(events, persona) if events else {}
    if apply_cold_start and stats:
        from app.relation import engine as relation_engine

        current = float(relation_engine.get_relation()["affinity"])
        if current < stats["affinity"] * 0.6:
            relation_engine.set_relation(
                affinity=stats["affinity"],
                trust=stats["trust"],
                attraction=stats["attraction"],
                reason=f"从聊天记录冷启动：{stats['reason']}",
            )

    if parsed.forget:
        warnings.append(f"按「不要记得的」跳过了 {len(parsed.forget)} 条，未写入任何记忆")

    db.kv_set(f"ingest:{digest}", "done")
    db.kv_set(
        "last_ingest",
        json.dumps(
            {"path": str(path), "at": store.now_iso(), "events": len(events), "format": decl.fmt},
            ensure_ascii=False,
        ),
    )

    return {
        "ok": True,
        "path": str(path),
        "format_detected": decl.fmt,
        "declared": decl.as_dict(),
        "warning": "；".join(dict.fromkeys(warnings)),
        "events_parsed": len(events),
        "memories_written": facts_written + len(top_quotes) + len(parsed.remember),
        "facts_written": facts_written,
        "quotes_collected": len(top_quotes),
        "chunks": len(chunks),
        "extract_facts": bool(do_extract),
        "cold_start": stats,
        "contamination": contamination,
        "samples": [q for q, _ in top_quotes[:5]],
    }
