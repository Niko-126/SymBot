"""工作记忆压缩：把"聊过的"折成线索，不折成事实。

原则：
- 摘要只记线索：在聊什么、有什么没聊完、她现在的态度。
- 事实不写摘要——那是衡忆的活，重复存两处早晚对不上。
- 只在后台（或回合结束后）压缩，绝不阻塞回复。
- 最近 keep_recent 轮永远保留原文。
﻿​‍​﻿​‌‌‌​﻿‌‍‌‌​‌﻿​‌‍​‍‍‍‍​﻿‌​﻿​​﻿"""

from __future__ import annotations

import json
from typing import Any

from app.core import db
from app.memory import store

SUMMARY_LEVEL = 1

_UNRESOLVED = ("下次", "明天", "回头", "等你", "答应", "以后", "再聊", "等我", "改天", "说好了", "还没")


def latest_summary(session_seq: int = 1) -> dict[str, Any] | None:
    r = db.one(
        "SELECT * FROM summaries WHERE level = ? AND session_seq = ? ORDER BY id DESC LIMIT 1",
        (SUMMARY_LEVEL, session_seq),
    )
    return dict(r) if r else None


def covered_to(session_seq: int = 1) -> int:
    r = latest_summary(session_seq)
    return int(r["covered_to"] or 0) if r else 0


def should_compact(used_tokens: int, cfg, verbatim_count: int = 0) -> bool:
    """两个条件满足任一就压：
    1) 工作集超预算；
    2) 未压缩的原文太长（这个才是日常真正会触发的）。"""
    ctx = cfg.context
    if verbatim_count > ctx.verbatim_max:
        return True
    if ctx.mode == "ratio":
        return used_tokens > ctx.window * ctx.force_ratio
    return used_tokens > ctx.trigger


def _truncate(text: str, n: int = 22) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def _parse_prev(prev: str) -> tuple[list[str], list[str]]:
    topics: list[str] = []
    pending: list[str] = []
    for line in (prev or "").splitlines():
        line = line.strip()
        if line.startswith("在聊："):
            topics += [x.strip() for x in line[3:].split("；") if x.strip()]
        elif line.startswith("没聊完的："):
            pending += [x.strip() for x in line[4:].split("；") if x.strip()]
    return topics, pending


def rule_summary(prev: str, folded: list[dict[str, Any]], persona, relation: dict[str, Any]) -> str:
    """没有模型时的结构化摘要：合并旧线索 + 新线索，去重、限长。"""
    old_topics, old_pending = _parse_prev(prev)

    new_topics: list[str] = []
    for e in folded:
        if e.get("role") != "user":
            continue
        t = _truncate(e.get("text", ""), 18)
        if t and t not in new_topics:
            new_topics.append(t)

    new_pending: list[str] = []
    for e in folded:
        text = str(e.get("text", ""))
        if any(k in text for k in _UNRESOLVED):
            t = _truncate(text, 24)
            if t not in new_pending:
                new_pending.append(t)

    def merge(old: list[str], new: list[str], cap: int) -> list[str]:
        out: list[str] = []
        for x in old + new:
            if x and x not in out:
                out.append(x)
        return out[-cap:]

    topics = merge(old_topics, new_topics, 5)
    pending = merge(old_pending, new_pending, 3)

    from app.relation import session as session_mod

    phase = session_mod.aversion_phase(session_mod.get_aversion())
    stage = persona.stage_for(float(relation.get("affinity", 0)))
    mood = {
        "calm": "情绪平稳",
        "uneasy": "心里有点不舒服，但没说",
        "irritated": "在烦",
        "cold": "很冷",
        "ended": "已经结束",
    }.get(phase, "情绪平稳")

    lines: list[str] = []
    if topics:
        lines.append("在聊：" + "；".join(topics))
    if pending:
        lines.append("没聊完的：" + "；".join(pending))
    lines.append(f"现在的状态：{mood}，关系在「{stage.get('label', '')}」")
    return "\n".join(lines)[:420]


async def llm_summary(rt, prev: str, folded: list[dict[str, Any]], persona) -> str | None:
    """有模型时让她自己总结——比规则更懂"这场对话的线索"。"""
    if getattr(rt.cfg.provider, "is_mock", True):
        return None
    transcript = "\n".join(
        ("他：" if e.get("role") == "user" else "你：") + str(e.get("text", ""))[:60]
        for e in folded[-40:]
    )
    system = (
        "你要把刚才这段对话压缩成一条「线索」，不是复述事实。\n"
        "只写三件事：你们在聊什么、有什么没聊完的、你现在的态度。\n"
        "不要写他的偏好、他的承诺、具体经历——那些另存，不在这里重复。\n"
        "200 字以内，第一人称，短句，不要写'用户''对话记录'这类词。"
    )
    user = (f"之前的线索：{prev}\n\n刚才的对话：\n{transcript}") if prev else f"刚才的对话：\n{transcript}"
    try:
        from app.mind import models

        spec = models.pick("summary")
        seq = 1
        try:
            from app.relation import session as session_mod

            seq = int(session_mod.get(rt.default_name).get("seq") or 1)
        except Exception:  # noqa: BLE001
            pass
        r = await rt.llm.chat(
            system, [{"role": "user", "content": user}], max_tokens=320, model=spec.provider_model,
            thinking=False,
        )
        models.record(spec.key, "summary", r.tokens_in, r.tokens_out, seq)
        text = (r.text or "").strip()
        return text[:800] if text else None
    except Exception:  # noqa: BLE001
        return None


async def compact(rt, persona, relation: dict[str, Any], session_seq: int = 1) -> dict[str, Any]:
    """把"最近 keep_recent 轮之前、还没被摘要覆盖"的对话折成一条线索。"""
    keep = int(rt.cfg.context.keep_recent)
    recent = store.recent_events(limit=keep)
    if not recent:
        return {"ok": False, "reason": "没有可压缩的对话"}

    boundary = int(recent[0]["id"]) - 1
    start = covered_to(session_seq)
    if boundary <= start:
        return {"ok": False, "reason": "没有新的可压缩区间"}

    folded = store.events_between(start, boundary)
    if not folded:
        return {"ok": False, "reason": "区间为空"}

    prev_row = latest_summary(session_seq)
    prev = str(prev_row["text"]) if prev_row else ""

    text = await llm_summary(rt, prev, folded, persona)
    mode = "llm"
    if not text:
        text = rule_summary(prev, folded, persona, relation)
        mode = "rule"

    db.execute(
        "INSERT INTO summaries (session_seq, level, period_start, period_end, text, covered_to, tokens_est, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_seq,
            SUMMARY_LEVEL,
            str(folded[0].get("ts", "")),
            str(folded[-1].get("ts", "")),
            text,
            boundary,
            max(1, len(text) // 2),
            store.now_iso(),
        ),
    )
    # 只保留最近 5 条摘要，旧的可删（covered_to 一直往前推）
    db.execute(
        "DELETE FROM summaries WHERE level = ? AND session_seq = ? AND id NOT IN"
        " (SELECT id FROM summaries WHERE level = ? AND session_seq = ? ORDER BY id DESC LIMIT 5)",
        (SUMMARY_LEVEL, session_seq, SUMMARY_LEVEL, session_seq),
    )
    return {
        "ok": True,
        "mode": mode,
        "covered_to": boundary,
        "folded": len(folded),
        "summary": text,
        "tokens_est": max(1, len(text) // 2),
    }


def reset(session_seq: int = 1) -> None:
    db.execute("DELETE FROM summaries WHERE session_seq = ?", (session_seq,))


def status(session_seq: int = 1) -> dict[str, Any]:
    row = latest_summary(session_seq)
    return {
        "covered_to": covered_to(session_seq),
        "summary": row["text"] if row else "",
        "updated_at": row["created_at"] if row else "",
        "degraded": bool(db.kv_get("memory_degraded", "")),
    }
