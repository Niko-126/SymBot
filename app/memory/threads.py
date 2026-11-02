"""没说完的话题：她说了“明天再讲/回头再说”，到点要记得接上。

只存她自己的承诺句，不存用户隐私原文；到点注入一次提示，之后标记 surfaced。
﻿﻿​‌‍‌‌​‌‌﻿‌‌﻿‌‍‌​‍‍‍﻿‌​​​‍‍﻿﻿‍﻿﻿﻿"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core import db

_HINT = re.compile(
    r"([^。！？!?\n]*(?:明天|回头|等会|待会|晚点|下次|睡醒|醒了再|再说|改天)[^。！？!?\n]*)"
)
_LONG_GAP = ("明天", "睡醒", "醒了再", "改天", "下次")


def _now() -> datetime:
    return datetime.now().astimezone()


def capture(user_id: int, chat_type: str, text: str) -> int:
    """从她的话里抓一句“以后再说”，建一条到点提醒。返回新 id（0=没抓到/重复）。"""
    uid = int(user_id or 0)
    raw = str(text or "").strip()
    if uid <= 0 or not raw:
        return 0
    m = _HINT.search(raw)
    if not m:
        return 0
    snippet = m.group(1).strip()[:80]
    if len(snippet) < 4:
        return 0
    # 同一句 48h 内不重复建
    recent = db.one(
        "SELECT id FROM open_threads WHERE user_id = ? AND text = ? AND created_at >= ?",
        (uid, snippet, (_now() - timedelta(hours=48)).isoformat(timespec="seconds")),
    )
    if recent:
        return 0
    gap_h = 8 if any(k in snippet for k in _LONG_GAP) else 2
    due = (_now() + timedelta(hours=gap_h)).isoformat(timespec="seconds")
    return db.execute(
        "INSERT INTO open_threads (user_id, chat_type, text, due_at, status, created_at)"
        " VALUES (?, ?, ?, ?, 'open', ?)",
        (uid, str(chat_type or ""), snippet, due, _now().isoformat(timespec="seconds")),
    )


def due_threads(user_id: int, chat_type: str, limit: int = 2) -> list[dict[str, Any]]:
    """到点且没提过的话题；取到就标记 surfaced。"""
    uid = int(user_id or 0)
    if uid <= 0:
        return []
    rows = db.rows(
        "SELECT * FROM open_threads WHERE user_id = ? AND status = 'open'"
        " AND chat_type = ? AND due_at <= ? ORDER BY id LIMIT ?",
        (uid, str(chat_type or ""), _now().isoformat(timespec="seconds"), int(limit)),
    )
    items = [dict(r) for r in rows]
    for item in items:
        db.execute(
            "UPDATE open_threads SET status = 'surfaced', surfaced_at = ? WHERE id = ?",
            (_now().isoformat(timespec="seconds"), int(item["id"])),
        )
    return items


def prompt_hint(user_id: int, chat_type: str, limit: int = 2) -> str:
    items = due_threads(user_id, chat_type, limit)
    if not items:
        return ""
    lines = ["【之前没说完的】"]
    lines.extend(f"- {str(i['text'])[:80]}" for i in items)
    lines.append("可以自然地接一句，别像在背待办；他不想聊就放下。")
    return "\n".join(lines)


def stats(user_id: int) -> dict[str, int]:
    rows = db.rows(
        "SELECT status, COUNT(*) c FROM open_threads WHERE user_id = ? GROUP BY status",
        (int(user_id or 0),),
    )
    return {str(r["status"]): int(r["c"]) for r in rows}
