"""私密记忆：亲密模式下的上下文记录与分析。

独立于普通记忆（memories 表），存在 intimate_context 表里。
只有用户明确进入亲密模式后才记录，退出即停止。
﻿​​‍﻿‌‌​​﻿‌‌﻿‍‍﻿‍‍‍​‍﻿﻿﻿‌‌﻿‍‌​‍​‍﻿"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core import db


def start_session(user_id: int, mode: str, is_private: bool) -> int:
    """进入亲密模式时记录。"""
    return db.execute(
        "INSERT INTO intimate_context (user_id, mode, is_private, started_at, events)"
        " VALUES (?, ?, ?, ?, '[]')",
        (
            int(user_id),
            mode,
            1 if is_private else 0,
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )


def end_session(user_id: int) -> int | None:
    """退出亲密模式，标记结束。"""
    row = db.one(
        "SELECT id FROM intimate_context WHERE user_id = ? AND ended_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    if not row:
        return None
    sid = int(row["id"])
    db.execute(
        "UPDATE intimate_context SET ended_at = ? WHERE id = ?",
        (datetime.now().astimezone().isoformat(timespec="seconds"), sid),
    )
    return sid


def record(user_id: int, role: str, text: str, mode: str, is_private: bool) -> None:
    """追加一条对话到当前的亲密会话。"""
    row = db.one(
        "SELECT id, events FROM intimate_context WHERE user_id = ? AND ended_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )
    if not row:
        # 自动开会话
        start_session(user_id, mode, is_private)
        row = db.one(
            "SELECT id, events FROM intimate_context WHERE user_id = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (int(user_id),),
        )
        if not row:
            return
    try:
        events = json.loads(str(row["events"] or "[]"))
    except json.JSONDecodeError:
        events = []
    events.append(
        {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "role": role,
            "text": text[:500],
            "mode": mode,
            "private": is_private,
        }
    )
    db.execute(
        "UPDATE intimate_context SET events = ? WHERE id = ?",
        (json.dumps(events[-200:], ensure_ascii=False), int(row["id"])),
    )


def recent(user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    """最近的亲密上下文（给分析用）。"""
    rows = db.rows(
        "SELECT * FROM intimate_context WHERE user_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (int(user_id), max(1, limit)),
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["events"] = json.loads(d.get("events") or "[]")
        except json.JSONDecodeError:
            d["events"] = []
        out.append(d)
    return out


def stats(user_id: int) -> dict[str, Any]:
    total = db.one(
        "SELECT COUNT(*) c FROM intimate_context WHERE user_id = ?", (int(user_id),)
    )["c"]
    return {"sessions": int(total), "user_id": user_id}
