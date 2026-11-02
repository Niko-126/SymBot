"""私密偏好分析：把亲密记录提炼成"他喜欢/讨厌/边界"档案。

用户要求："允许记录分析该模式下所有上下文"，但记录不等于理解。
这里隔一段时间把 intimate_context 折成一小张档案，注入下次亲密模式：
- likes    他明确表达过喜欢的（被怎么对待）
- dislikes 他明确不喜欢的
- boundaries 他的边界（哪些话/做法会让他不舒服）
- summary  一句话概括

只写"他表达过的"，不写露骨原文；模型解析失败就保留空档案，不编。
﻿​​﻿‍​​﻿‍‌‍﻿‍﻿‌‌​‍‌‍﻿‍﻿﻿​​​​‍‌‍﻿‍﻿"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.core import db
from app.mind import models
from app.relation import appraise


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def due(user_id: int) -> bool:
    """有没有新的亲密会话需要分析（按上次分析后新增的会话数）。"""
    row = db.one("SELECT sessions_seen FROM intimate_profile WHERE user_id = ?", (int(user_id),))
    seen = int(row["sessions_seen"] or 0) if row else 0
    total = db.one(
        "SELECT COUNT(*) c FROM intimate_context WHERE user_id = ? AND ended_at IS NOT NULL",
        (int(user_id),),
    )
    return int(total["c"] or 0) > seen


def profile_of(user_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM intimate_profile WHERE user_id = ?", (int(user_id),))
    if row is None:
        return None
    data = dict(row)
    for key in ("likes", "dislikes", "boundaries", "aftercare"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except (ValueError, TypeError):
            data[key] = []
    data.setdefault("last_stage", "")
    return data


def profile_hint(user_id: int) -> str:
    """给 prompt 用的一段偏好提示（没有档案就空串）。"""
    p = profile_of(user_id)
    if not p:
        return ""
    likes = "、".join(str(x) for x in (p.get("likes") or [])[:4])
    dislikes = "、".join(str(x) for x in (p.get("dislikes") or [])[:4])
    bounds = "、".join(str(x) for x in (p.get("boundaries") or [])[:4])
    lines = ["【你已经知道的他】"]
    if likes:
        lines.append(f"他吃这一套：{likes}")
    if dislikes:
        lines.append(f"他不太喜欢：{dislikes}")
    if bounds:
        lines.append(f"他的边界：{bounds}")
    care = "、".join(str(x) for x in (p.get("aftercare") or [])[:4])
    if care:
        lines.append(f"结束后他需要：{care}")
    if p.get("last_stage"):
        lines.append(f"上次停在哪：{p['last_stage']}（接着这个往下，别从头来）")
    if p.get("summary"):
        lines.append(f"一句话：{p['summary']}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _collect(user_id: int, sessions: int = 6, lines: int = 60) -> str:
    # 只读私聊：群聊里的任何内容都不进档案（群聊/私聊隔离）
    rows = db.rows(
        "SELECT events, is_private FROM intimate_context WHERE user_id = ? AND is_private = 1"
        " ORDER BY id DESC LIMIT ?",
        (int(user_id), max(1, int(sessions))),
    )
    out: list[str] = []
    for row in rows:
        try:
            events = json.loads(row["events"] or "[]")
        except (ValueError, TypeError):
            events = []
        for ev in events[-lines:]:
            role = "他" if str(ev.get("role")) == "user" else "你"
            text = str(ev.get("text") or "").strip()[:160]
            if text:
                out.append(f"{role}：{text}")
    return "\n".join(out[-lines:])


def _parse_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(user_id: int, data: dict[str, Any], sessions_seen: int) -> dict[str, Any]:
    def _list(key: str, limit: int = 6) -> str:
        items = data.get(key)
        if not isinstance(items, list):
            return "[]"
        clean = [str(x).strip()[:80] for x in items if str(x).strip()][:limit]
        return json.dumps(clean, ensure_ascii=False)

    db.execute(
        "INSERT INTO intimate_profile (user_id, likes, dislikes, boundaries, summary,"
        " sessions_seen, updated_at, aftercare, last_stage)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET likes = excluded.likes, dislikes = excluded.dislikes,"
        " boundaries = excluded.boundaries, summary = excluded.summary,"
        " sessions_seen = excluded.sessions_seen, updated_at = excluded.updated_at,"
        # 这两项：这次没提到就保留旧值，别把上次记下的照护/阶梯冲掉
        " aftercare = CASE WHEN excluded.aftercare = '[]' THEN intimate_profile.aftercare"
        "                  ELSE excluded.aftercare END,"
        " last_stage = CASE WHEN excluded.last_stage = '' THEN intimate_profile.last_stage"
        "                  ELSE excluded.last_stage END",
        (
            int(user_id), _list("likes"), _list("dislikes"), _list("boundaries"),
            str(data.get("summary") or "")[:300], int(sessions_seen), _now(),
            _list("aftercare"), str(data.get("last_stage") or "")[:20],
        ),
    )
    return profile_of(user_id) or {}


def set_last_stage(user_id: int, stage_label: str) -> None:
    """记下"上次停在哪一级"（只动 last_stage，不碰 likes/dislikes）。

    单独一个 upsert：阶梯一动就能落库，不用等下一次模型分析。
    R 级插件的 stage.py 会调它；插件没装时没人调，无害。
    """
    uid = int(user_id or 0)
    if uid <= 0 or not str(stage_label or "").strip():
        return
    db.execute(
        "INSERT INTO intimate_profile (user_id, likes, dislikes, boundaries, summary,"
        " sessions_seen, updated_at, aftercare, last_stage)"
        " VALUES (?, '[]', '[]', '[]', '', 0, ?, '[]', ?)"
        " ON CONFLICT(user_id) DO UPDATE SET last_stage = excluded.last_stage,"
        " updated_at = excluded.updated_at",
        (uid, _now(), str(stage_label)[:20]),
    )


async def analyze_user(rt, user_id: int) -> dict[str, Any] | None:
    """把最近的私密记录折成偏好档案。模型失败/解析失败就返回空档案，不编。"""
    uid = int(user_id)
    body = _collect(uid)
    total = db.one("SELECT COUNT(*) c FROM intimate_context WHERE user_id = ?", (uid,))
    seen = int(total["c"] or 0) if total else 0
    if not body:
        return None
    system = (
        "你在回顾你和他的私密对话。只提炼他明确表达过的东西，不要写露骨原文，不要猜。\n"
        "输出一个 JSON 对象，不要别的文字：\n"
        '{"likes":["他喜欢被怎么对待"],"dislikes":["他明确不喜欢的"],'
        '"boundaries":["会让他不舒服的话或做法"],"summary":"一句话概括"}'
    )
    spec = models.pick("reflect")
    try:
        reply = await rt.llm.chat(
            system, [{"role": "user", "content": body}],
            model=spec.provider_model, max_tokens=400, temperature=0.4, thinking=False,
        )
        text = appraise.strip_actions(reply.text or "").strip()
    except Exception:  # noqa: BLE001 - 分析失败不影响聊天
        text = ""
    return _save(uid, _parse_json(text), seen)


def pending_users(limit: int = 2) -> list[int]:
    rows = db.rows(
        "SELECT DISTINCT user_id FROM intimate_context WHERE ended_at IS NOT NULL ORDER BY user_id"
    )
    out = [int(r["user_id"]) for r in rows if due(int(r["user_id"]))]
    return out[: max(1, int(limit))]
