"""加好友：OneBot 没有"主动加好友"的接口。

真实流程只有一条：
    对方发好友申请 → 我们收到 post_type=request 的上报 → 回 set_friend_add_request(flag, approve)

所以这里做的是"把申请记下来、能通过就通过"，不是假装能凭空把人加成好友。
控制台里的"添加好友"只是**本地登记**（已知用户 + 关系记录），不会改变 QQ 一侧的好友列表。
﻿​‍​​‍‌‌‍​‌‍‍​​﻿‍﻿‌﻿​‌‍﻿‍‌‌‍‍‌​‌‌﻿"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core import db

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
FAILED = "failed"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(
    flag: str,
    openid: str,
    nickname: str = "",
    comment: str = "",
    source: str = "onebot",
    seq: int = 0,
) -> int:
    """收到一条好友申请。同一个 flag 不重复建行，返回行 id。"""
    flag = str(flag or "")
    openid = str(openid or "")
    if flag:
        row = db.one("SELECT id FROM friend_request WHERE flag = ?", (flag,))
        if row is not None:
            db.execute(
                "UPDATE friend_request SET nickname = ?, comment = ?, updated_at = ? WHERE id = ?",
                (nickname or "", comment or "", now_iso(), int(row["id"])),
            )
            return int(row["id"])
    now = now_iso()
    return db.execute(
        "INSERT INTO friend_request (seq, flag, openid, nickname, comment, source, status,"
        " note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
        (int(seq or 0), flag, openid, nickname or "", comment or "", source, PENDING, now, now),
    )


def mark(request_id: int, status: str, note: str = "") -> bool:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE friend_request SET status = ?, note = ?, updated_at = ? WHERE id = ?",
            (status, note, now_iso(), int(request_id)),
        )
        return cur.rowcount > 0


def get(request_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM friend_request WHERE id = ?", (int(request_id),))
    return dict(row) if row is not None else None


def by_flag(flag: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM friend_request WHERE flag = ?", (str(flag or ""),))
    return dict(row) if row is not None else None


def listing(status: str = "", limit: int = 50) -> list[dict[str, Any]]:
    if status:
        rows = db.rows(
            "SELECT * FROM friend_request WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, int(limit)),
        )
    else:
        rows = db.rows("SELECT * FROM friend_request ORDER BY id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in rows]


def pending_count() -> int:
    row = db.one("SELECT COUNT(*) c FROM friend_request WHERE status = ?", (PENDING,))
    return int(row["c"]) if row is not None else 0
