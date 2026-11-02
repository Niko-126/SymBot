"""主动招呼还没认识的人。

两个入口：
1. **自动**：某个 QQ 第一次在群里说话（还没被她认识过），她回完话再 @ 他一句，
   问叫什么、是谁。
2. **手动**：控制台「群招呼」→ 拉群成员列表，把没登记的人建号，再主动 @ 招呼。

边界：
- 同一个人只招呼一次（kv `greeted:{openid}`），不刷屏。
- 一次手动任务最多招呼 3 人。
- 家人（生父/干爹）、自己、已拉黑的不招呼。
- 测试期应答开着时不招呼（避免联调噪音）。
﻿﻿‍​‌‌​‍​​‌﻿﻿‍‌​​﻿‍‍‍‌‌‌​‍﻿﻿﻿‍‌﻿﻿﻿"""

from __future__ import annotations

import random

from app.core import db

LINE_POOL = (
    "诶，还没见过你呢。你叫什么名字？",
    "这位是新来的吧？我该怎么称呼你呀。",
    "你好呀，第一次见。你是谁来着？",
    "还没跟你聊过呢，怎么叫你？",
)


def line() -> str:
    return random.choice(LINE_POOL)


def _key(openid: str) -> str:
    return f"greeted:{str(openid or '').strip()}"


def already_greeted(openid: str) -> bool:
    oid = str(openid or "").strip()
    return bool(oid and db.kv_get(_key(oid), "") == "1")


def mark_greeted(openid: str) -> None:
    oid = str(openid or "").strip()
    if oid:
        db.kv_set(_key(oid), "1")


def family_skip(openid: str) -> bool:
    from app.relation import love

    return bool(love.family_role(0, openid=str(openid)))


def greeting_enabled() -> bool:
    return db.kv_get("testing_reply", "0") != "1"
