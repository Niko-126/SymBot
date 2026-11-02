"""她的一天：每天写一段只给自己看的日记，第二天聊天时自然提起。

为什么单独一层：
- 日记不是"用户记忆"，是她的生活痕迹。写进 `memories(kind='diary')` 是为了可被检索，
  真正读给模型的是按天存的 kv（`diary:<day>`），这样"第二天提起"才有确定的时间锚点。
- 生成时机是"新的一天第一次聊天"：把昨天真实发生的事（聊天 + 她的作息）交给模型，
  折成 2-4 句第一人称日记。一天只写一次。

三条边界：
1. 一天只写一次（`diary_last_date`），没有昨天的聊天就不写——不编造生活。
2. 日记只在"第二天"被引用一次（`diary_ref_date`），之后靠正常记忆检索。
3. 模型失败不阻塞聊天：拿她昨天最后一句当兜底，绝不因为日记把对话搞崩。
﻿​‍﻿﻿​​﻿‌‌﻿﻿​‌﻿​​‍​​​‍‌﻿‌​‌​‍﻿‌﻿‍﻿"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from app.core import db
from app.life import clock
from app.memory import store, h2
from app.mind import models
from app.relation import appraise

DIARY_KIND = "diary"
_KEY_LAST = "diary_last_date"
_KEY_REF = "diary_ref_date"


def _day_key(day: str) -> str:
    return f"diary:{day}"


def _week_events(day: str, limit: int = 40) -> list[dict[str, Any]]:
    """取某天她与人说的话。**群聊不进日记**：那是别人的场面，不是她的生活。"""
    rows = db.rows(
        "SELECT id, ts, role, text FROM events WHERE ts LIKE ? AND role IN ('user', 'her')"
        " AND kind = 'text' AND (chat_type IS NULL OR chat_type != 'group')"
        " ORDER BY id LIMIT ?",
        (f"{day}%", limit),
    )
    return [dict(r) for r in rows if str(r["text"] or "").strip()]


def get(day: str) -> dict[str, Any] | None:
    raw = db.kv_get(_day_key(day), "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def recent(limit: int = 7) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT value FROM kv WHERE key LIKE 'diary:%' ORDER BY key DESC LIMIT ?", (max(1, limit),)
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(str(row["value"]))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("day"):
            out.append(data)
    return out


def _save(day: str, text: str, source_ids: list[int]) -> dict[str, Any]:
    entry = {"day": day, "text": text, "written_at": store.now_iso()}
    db.kv_set(_day_key(day), json.dumps(entry, ensure_ascii=False))
    h2.add(DIARY_KIND, f"[{day}] {text}", importance=0.8, source_ids=source_ids)
    return entry


def note_event(content: str, *, topic: str = "相处", user_id: int | None = None) -> int:
    """往日记里追加一条"小事记"（比如某人给了新昵称）。

    写进 human_notes(kind=topic) 与 memories(kind=diary)，这样日记页和她的记忆都能看到。
    """
    content = (content or "").strip()
    if not content:
        return 0
    today = clock.now().date().isoformat()
    try:
        from app.memory import observe

        observe.add_note(topic, content, confidence=0.9, user_id=user_id)
    except Exception:  # noqa: BLE001
        pass
    return h2.add(DIARY_KIND, f"[{today}] {content}", importance=0.7,
                  user_id=int(user_id or 0))


async def _compose(rt, persona, events: list[dict[str, Any]]) -> str:
    lines = []
    for e in events[-20:]:
        who = "他" if e["role"] == "user" else "我"
        lines.append(f"{who}：{str(e['text'])[:60]}")
    activities = "、".join(
        str(s.get("activity", "")) for s in persona.schedule() if str(s.get("activity", "")).strip()
    )
    system = (
        f"你是{persona.name}。{persona.tagline}\n"
        "你在写只给自己看的日记，没有人会读到。\n"
        "第一人称，2 到 4 句，具体、口语、短句。\n"
        "不要总结聊天内容，不要复述原话，不要提到聊天记录、AI 或模型，不要用书面腔。\n"
        f"你平常的一天大概是：{activities}"
    )
    messages = [
        {
            "role": "user",
            "content": "昨天发生的事：\n" + "\n".join(lines) + "\n\n写一段昨天的日记。",
        }
    ]
    spec = models.pick("reflect")
    text = ""
    try:
        reply = await rt.llm.chat(
            system, messages, model=spec.provider_model, max_tokens=220, temperature=0.9
        )
        text = appraise.strip_actions(reply.text or "").strip()
    except Exception:  # noqa: BLE001 - 日记失败不能拖垮对话
        text = ""
    if not text:
        hers = [str(e["text"]) for e in events if e["role"] == "her"]
        text = hers[-1] if hers else "今天也就这样，没什么可写的。"
    return text[:300].replace("\n", " ").strip()


async def ensure(rt, persona) -> dict[str, Any] | None:
    """新的一天第一次聊天时调用：有昨天的聊天，就补写昨天的日记。"""
    now = clock.now()
    today = now.date().isoformat()
    if db.kv_get(_KEY_LAST, "") == today:
        return None
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    # 先记账：今天不再尝试，避免每次聊天都探一次
    db.kv_set(_KEY_LAST, today)
    events = _week_events(yesterday)
    if not events:
        return None
    text = await _compose(rt, persona, events)
    return _save(yesterday, text, [int(e["id"]) for e in events])


async def compose_for(rt, persona, day: str) -> dict[str, Any] | None:
    """手动给某一天补写日记（设置页/调试用）。同一天已写过则直接返回旧的。"""
    existing = get(day)
    if existing:
        return existing
    events = _week_events(day)
    text = await _compose(rt, persona, events)
    return _save(day, text, [int(e["id"]) for e in events])


def prompt_line() -> str:
    """第二天第一次聊天时给模型的一句提示；引用过就不再提示。"""
    now = clock.now()
    today = now.date().isoformat()
    if db.kv_get(_KEY_REF, "") == today:
        return ""
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    entry = get(yesterday)
    if not entry:
        return ""
    return (
        f"（{yesterday} 你写下的日记）\n{entry.get('text', '')}\n"
        "可以顺口提一句，不要逐字念，也不要说「我日记里写了」这种话。"
    )


def mark_referenced() -> None:
    db.kv_set(_KEY_REF, clock.now().date().isoformat())


def status() -> dict[str, Any]:
    today = clock.now().date().isoformat()
    yesterday = (clock.now().date() - timedelta(days=1)).isoformat()
    return {
        "today": today,
        "last_written": db.kv_get(_KEY_LAST, ""),
        "referenced_on": db.kv_get(_KEY_REF, ""),
        "has_yesterday": bool(get(yesterday)),
        "count": len(recent(30)),
        "items": recent(7),
    }
