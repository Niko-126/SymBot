from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core import db

PRIORITY_KINDS = ("boundary", "promise")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_dict(r: Any) -> dict[str, Any]:
    d = dict(r)
    if "source_ids" in d and isinstance(d["source_ids"], str):
        try:
            d["source_ids"] = json.loads(d["source_ids"])
        except json.JSONDecodeError:
            d["source_ids"] = []
    return d


# ---------------------------------------------------------------- events﻿​‌​‌‌​​​‌﻿‌‍​‌​‍‌﻿‌‍‍‍‌​​‌​​‍‌‌‌﻿

def add_event(role: str, text: str, source: str = "panel", kind: str = "text",
              meta: dict | None = None, user_id: int = 0, chat_type: str = "",
              group_id: str = "", ts: str | None = None) -> int:
    """写一条事件。``ts`` 只在测试/补发时用（默认真实时间）。

    主动外发允许传入"这次决策用的时刻"，这样配额/窗口/冷却和事件时间戳口径一致。
    """
    import json

    return db.execute(
        "INSERT INTO events (ts, role, kind, text, source, meta, user_id, chat_type, group_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts or now_iso(), role, kind, text, source,
         json.dumps(meta, ensure_ascii=False) if meta else "", int(user_id or 0),
         str(chat_type or ""), str(group_id or "")),
    )


def recent_events(limit: int = 40, chat_type: str = "", user_id: int = 0) -> list[dict[str, Any]]:
    """按来源取最近事件。chat_type 空 = 不筛。"""
    where: list[str] = []
    args: list[Any] = []
    if chat_type:
        # 私聊上下文不该混进群聊原文，反之亦然（老数据按 group_id 判断）
        if str(chat_type) == "group":
            where.append(
                "(chat_type = 'group' OR (chat_type = '' AND group_id IS NOT NULL AND group_id <> ''))"
            )
        else:
            where.append(
                "(chat_type = ? OR (chat_type = '' AND (group_id IS NULL OR group_id = '')))"
            )
            args.append(str(chat_type))
    if user_id:
        where.append("user_id = ?")
        args.append(int(user_id))
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return [_to_dict(r) for r in reversed(db.rows(sql, tuple(args)))]


def events_between(after_id: int, upto_id: int, chat_type: str = "",
                   user_id: int = 0) -> list[dict[str, Any]]:
    """工作记忆的原文段。给了 chat_type 就只取这个场合的；私聊再按 user_id 隔离。"""
    where = ["id > ?", "id <= ?"]
    args: list[Any] = [after_id, upto_id]
    if chat_type:
        # 私聊只收私聊；群聊只收群聊。老数据 chat_type 为空时按 group_id 判断
        if str(chat_type) == "group":
            where.append(
                "(chat_type = 'group' OR (chat_type = '' AND group_id IS NOT NULL AND group_id <> ''))"
            )
        else:
            where.append(
                "(chat_type = ? OR (chat_type = '' AND (group_id IS NULL OR group_id = '')))"
            )
            args.append(str(chat_type))
    if user_id:
        where.append("user_id = ?")
        args.append(int(user_id))
    rs = db.rows(
        "SELECT * FROM events WHERE " + " AND ".join(where) + " ORDER BY id",
        tuple(args),
    )
    return [_to_dict(r) for r in rs]


def count_events() -> int:
    r = db.one("SELECT COUNT(*) AS c FROM events")
    return int(r["c"]) if r else 0


# ---------------------------------------------------------------- memories
#
# 衡忆 2.0（docs/49）：记忆本体在引擎表 mem_item / mem_cue，写入只走 app/memory/h2.py。
# 这里的函数是老调用点的薄壳，签名保持不变；`memories` 是只读视图，别再往里写。

def add_memory(
    kind: str,
    content: str,
    importance: float = 0.6,
    stability: float = 0.5,
    source_ids: list[int] | None = None,
    pin: bool = False,
    user_id: int = 0,
    chat_type: str = "",
) -> int:
    from app.memory import h2

    return h2.add(
        kind, content, importance=importance, stability=stability,
        pin=pin, source_ids=source_ids, user_id=user_id, chat_type=chat_type,
    )


def get_memory(mid: int) -> dict[str, Any] | None:
    from app.memory import h2

    return h2.get_memory(mid)


def delete_memory(mid: int) -> bool:
    from app.memory import h2

    return h2.delete_memory(mid)


def search_memories(q: str = "", kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    from app.memory import h2

    return h2.search_memories(q=q, kind=kind, limit=limit)


def memory_stats() -> dict[str, Any]:
    from app.memory import h2

    return h2.memory_stats()


def recent_memories(limit: int = 5) -> list[dict[str, Any]]:
    """原话（quote）只用于风格锚点，别让它挤满"最近记忆"的展示位。"""
    from app.memory import h2

    return h2.recent_memories(limit)


def mark_recalled(ids: list[int]) -> None:
    from app.memory import h2

    h2.mark_recalled(ids)
