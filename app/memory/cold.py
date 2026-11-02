"""冷记忆压缩（M3.5，衡忆 2.0 版）。

两个月前的一条条零碎记忆，留着占检索位、也不该被完整记住。
按"时间 + 主题"合并成一条概要，保留线索，保留溯源。

三条硬规则（没变）：
1. 钉住的不参与（那是承诺，不是记忆）。
2. 雷点、约定、原话锚点不参与。
3. 合并前后都可追溯：原文存在 `memory_merges` 里，出问题能还原。

跟旧实现的区别（docs/49 §5）：
旧版是"插摘要 → 删原条目 → 还原时重插"，**硬删除**，一还原就把热度、钉住全丢了。
新版用引擎自己的 `merged_into` 字段做**软合并**：源条目只是"被并进摘要"，行还在，
`restore()` 把 `merged_into` 清掉就原样回来——热度、召回次数、线索一个不丢。
﻿‌﻿‍‍‍‌​﻿​​‍‍‍‌‌‌‌﻿﻿‍​‌‍﻿​‌‍‍﻿​​﻿﻿"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core import db, memengine
from app.memory import h2

COLD_AGE_DAYS = 30
MIN_GROUP = 3
PROTECTED_KINDS = h2.PROTECTED_KINDS


def _now() -> datetime:
    return datetime.now().astimezone()


def _age_days(ts: str) -> float:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return max(0.0, (_now() - dt).total_seconds() / 86400)
    except (ValueError, TypeError):
        return 0.0


def candidates() -> list[dict[str, Any]]:
    """挑出可以合并的冷记忆。"""
    out: list[dict[str, Any]] = []
    for item in memengine.store().list_items():
        if item.pinned:
            continue
        if str(item.kind) in PROTECTED_KINDS:
            continue
        row = h2.to_old(item)
        if float(row["heat"]) >= h2.COLD_THRESHOLD:
            continue
        if _age_days(str(row["first_seen"])) < COLD_AGE_DAYS:
            continue
        out.append(row)
    out.sort(key=lambda m: m["id"])
    return out


def group(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """时间（月）+ 主题（kind）+ 归属（人/场合）四键。跨人的记忆绝不合并。"""
    buckets: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
    for m in items:
        month = str(m.get("first_seen") or "")[:7]
        key = (month, str(m.get("kind", "fact")), int(m.get("user_id") or 0),
               str(m.get("chat_type") or ""))
        buckets.setdefault(key, []).append(m)
    return [g for g in buckets.values() if len(g) >= MIN_GROUP]


def _rule_summary(items: list[dict[str, Any]]) -> str:
    ranked = sorted(items, key=lambda m: -float(m.get("importance") or 0))
    parts: list[str] = []
    for m in ranked[:3]:
        text = " ".join(str(m.get("content", "")).split())
        if text and text not in parts:
            parts.append(text[:34])
    return "；".join(parts)[:110]


async def _llm_summary(rt, items: list[dict[str, Any]]) -> str | None:
    if getattr(rt.cfg.provider, "is_mock", True):
        return None
    lines = "\n".join(f"- {m.get('content')}" for m in items[:8])
    system = (
        "把下面几条旧记忆合并成一句，60 字以内。\n"
        "只保留具体事实（他喜欢什么、发生过什么），不要形容词总结，不要提到记忆/用户。"
    )
    try:
        from app.mind import models

        spec = models.pick("summary")
        r = await rt.llm.chat(system, [{"role": "user", "content": lines}], max_tokens=120,
                              model=spec.provider_model, thinking=False)
        text = (r.text or "").strip().replace("\n", " ")
        return text[:120] if text else None
    except Exception:  # noqa: BLE001
        return None


def _merge_cues(items: list[dict[str, Any]]) -> list[str]:
    cues: list[str] = []
    for m in items:
        raw = m.get("cues")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        for c in raw or []:
            if c and c not in cues:
                cues.append(c)
    return cues[:12]


async def compact_cold(rt, persona, dry_run: bool = False) -> dict[str, Any]:
    """执行一次冷记忆合并。返回合并明细。"""
    items = candidates()
    groups = group(items)
    if not groups:
        return {"ok": True, "groups": 0, "merged": 0, "freed": 0, "detail": []}

    merged: list[dict[str, Any]] = []
    for g in groups:
        summary = await _llm_summary(rt, g) or _rule_summary(g)
        if not summary:
            continue
        if dry_run:
            merged.append({"summary": summary, "count": len(g), "dry_run": True})
            continue

        owner = int(g[0].get("user_id") or 0)
        chat_type = str(g[0].get("chat_type") or "")
        keep_heat = max(float(m.get("heat") or 0) for m in g)
        new_id = h2.add(
            "event",
            summary,
            importance=max(float(m.get("importance") or 0.5) for m in g),
            cues=_merge_cues(g),
            source_ids=[int(m["id"]) for m in g],
            user_id=owner,
            chat_type=chat_type,
        )
        store = memengine.store()
        summary_item = store.get_item(int(new_id))
        if summary_item is not None:
            summary_item.heat = min(1.0, max(0.05, keep_heat))
            summary_item.clarity = "fuzzy"
            store.update_item(summary_item)

        # 软合并：源条目只打 merged_into，行不删
        for m in g:
            item = store.get_item(int(m["id"]))
            if item is None or item.pinned:
                continue
            item.merged_into = int(new_id)
            store.update_item(item)

        db.execute(
            "INSERT INTO memory_merges (ts, session_seq, summary_id, source_ids, sources)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                _now().isoformat(timespec="seconds"),
                _current_seq(),
                int(new_id),
                json.dumps([int(m["id"]) for m in g]),
                json.dumps(
                    [{"id": int(m["id"]), "kind": m["kind"], "content": m["content"]} for m in g],
                    ensure_ascii=False,
                ),
            ),
        )
        merged.append({"summary": summary, "count": len(g), "new_id": int(new_id)})

    return {
        "ok": True,
        "groups": len(groups),
        "merged": len(merged),
        "freed": sum(m["count"] for m in merged) - len(merged),
        "detail": merged[:12],
    }


def _current_seq() -> int:
    r = db.one("SELECT seq FROM session WHERE id = 1")
    return int(r["seq"]) if r else 1


def restore(merge_id: int) -> dict[str, Any]:
    """把一次合并还原成原始条目。留个后悔药。"""
    row = db.one("SELECT * FROM memory_merges WHERE id = ?", (merge_id,))
    if not row:
        return {"ok": False, "error": "没有这次合并记录"}
    try:
        sources = json.loads(row["sources"] or "[]")
    except json.JSONDecodeError:
        sources = []
    store = memengine.store()
    restored = 0
    for s in sources:
        item = store.get_item(int(s.get("id") or 0))
        if item is None:
            continue
        item.merged_into = None
        store.update_item(item)
        restored += 1
    try:
        memengine.lifecycle().forget(int(row["summary_id"]), reason="merge_restore")
    except Exception:  # noqa: BLE001 - 摘要已经被手动遗忘了也不算失败
        pass
    db.execute("DELETE FROM memory_merges WHERE id = ?", (merge_id,))
    return {"ok": True, "restored": restored}


def stats() -> dict[str, Any]:
    cold = candidates()
    groups = group(cold)
    total = db.one("SELECT COUNT(*) c FROM memory_merges")
    return {
        "cold_candidates": len(cold),
        "groups": len(groups),
        "merges": int(total["c"]) if total else 0,
    }
