"""关系里的小仪式：晚安、早安、认识第 N 天。

只给 prompt 一句轻提示，不硬塞；她真说了才记“今天做过了”，避免每晚重复。
﻿​​​​‌​﻿‍‌​‌‌‍‌‍​‍​﻿‌​​‌‍​﻿‍﻿‌‍﻿﻿﻿"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core import db


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _key(kind: str, user_id: int) -> str:
    return f"ritual:{kind}:{int(user_id)}:{_today()}"


def hint(user_id: int, hour: int, chat_type: str) -> str:
    uid = int(user_id or 0)
    if uid <= 0:
        return ""
    lines: list[str] = []
    if (hour >= 22 or hour <= 2) and db.kv_get(_key("goodnight", uid), "") != "1":
        lines.append("【今天的收尾】如果聊到合适，可以自然跟他说一句晚安；别硬塞。")
    if 6 <= hour <= 9 and db.kv_get(_key("morning", uid), "") != "1":
        lines.append("【早上的话】可以自然问一句早，或者昨晚睡得怎么样。")

    # 认识第 7/30/100 天：轻轻提一句，不搞纪念日大礼包
    row = db.one("SELECT first_seen FROM qq_user WHERE id = ?", (uid,))
    if row and row["first_seen"]:
        try:
            start = datetime.fromisoformat(str(row["first_seen"])).date()
            days = (datetime.now().astimezone().date() - start).days
        except (ValueError, TypeError):
            days = 0
        if days in (7, 30, 100) and db.kv_get(_key(f"day{days}", uid), "") != "1":
            lines.append(f"【认识第 {days} 天】可以顺口提一句“认识你第 {days} 天了”，别煽情。")
    if not lines:
        return ""
    return "\n".join(lines)


def confirm(user_id: int, reply_text: str) -> None:
    """她真说了晚安/早安才标记，避免第二天重复提示。"""
    uid = int(user_id or 0)
    text = str(reply_text or "")
    if uid <= 0 or not text:
        return
    if "晚安" in text:
        db.kv_set(_key("goodnight", uid), "1")
    if "早安" in text or "早上好" in text:
        db.kv_set(_key("morning", uid), "1")
