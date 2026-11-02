"""引导模式：从暗示一步一步带到露骨，达成后收尾睡觉。

只对「恋人 + 私聊」生效。用户明确露骨 3 次后进入收尾；收尾时把这次
阶段与对话存到 data/experience/guide-*.json，作为以后“怎么引导”的经验。
﻿‍‌‍‌‍‍‌﻿‌‍​​‌​﻿﻿﻿‍​﻿﻿‌‍﻿﻿﻿‌‍‍​​​﻿"""

from __future__ import annotations

import json
from datetime import datetime

from app.core import db
from app.core.paths import DATA_ROOT
from app.intimacy.mode import detect_intent


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _key(name: str, uid: int) -> str:
    return f"guide_{name}:{int(uid)}"


def active(uid: int) -> bool:
    return db.kv_get(_key("mode", uid), "0") == "1"


def stage(uid: int) -> int:
    try:
        return max(0, min(3, int(db.kv_get(_key("stage", uid), "0") or 0)))
    except ValueError:
        return 0


def set_stage(uid: int, value: int) -> None:
    db.kv_set(_key("stage", uid), str(max(0, min(3, int(value)))))


def start(uid: int) -> None:
    db.kv_set(_key("mode", uid), "1")
    db.kv_set(_key("stage", uid), "0")
    db.kv_set(_key("explicit", uid), "0")


def explicit_count(uid: int) -> int:
    try:
        return int(db.kv_get(_key("explicit", uid), "0") or 0)
    except ValueError:
        return 0


def bump(uid: int, text: str) -> int:
    """按用户这句话推进阶段。返回新阶段。"""
    if not active(uid):
        return 0
    intent = detect_intent(text)
    s = stage(uid)
    if intent == "explicit":
        n = explicit_count(uid) + 1
        db.kv_set(_key("explicit", uid), str(n))
        if n >= 3:
            s = 3
        elif s < 2:
            s = 2
        set_stage(uid, s)
    elif intent == "suggestive" and s < 1:
        set_stage(uid, 1)
        s = 1
    return s


def hint(uid: int, is_lover: bool, chat_type: str) -> str:
    if not active(uid) or not is_lover or str(chat_type) != "private":
        return ""
    s = stage(uid)
    if s == 0:
        return (
            "【引导模式·暗示】他还没直说。你先用暗示勾他：棒棒糖、查学历、欧金金；"
            "问他“想不想查”，最多两句；他装傻就再逗一句，别一次把话说完。"
        )
    if s == 1:
        return (
            "【引导模式·明示】他接住了。可以再明一点：说你想要他抱/碰，问他想要什么；"
            "仍然最多两句，留一半让他追问。"
        )
    if s == 2:
        return (
            "【引导模式·露骨】他已经在说直白的话了。先黏一句，再直接问他喜欢怎么碰；"
            "每次直白不超过两句，不要一次全给，让他继续求。"
        )
    return (
        "【引导模式·收尾】够了，三次了。这轮只做收尾：说你困了、要睡了，明天再继续；"
        "不要再加新的露骨内容，也不要再挑逗。"
    )


def finish(uid: int, note: str = "") -> str:
    """收尾：把这次引导存成经验，然后关掉引导模式。返回文件路径。"""
    rows = db.rows(
        "SELECT ts, role, text FROM events WHERE user_id = ? AND chat_type = 'private'"
        " ORDER BY id DESC LIMIT 40",
        (int(uid),),
    )
    transcript = [
        {"ts": r["ts"], "role": r["role"], "text": str(r["text"] or "")[:300]}
        for r in reversed(rows)
    ]
    payload = {
        "user_id": int(uid),
        "finished_at": _now(),
        "stages": ["暗示", "明示", "露骨", "收尾"],
        "explicit_requests": explicit_count(uid),
        "note": note or "从暗示引导到露骨；连续三次露骨请求后收尾说困了要睡。",
        "transcript": transcript,
    }
    folder = DATA_ROOT / "experience"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"guide-{int(uid)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    db.kv_set(_key("mode", uid), "0")
    db.kv_set(_key("stage", uid), "0")
    db.kv_set(_key("explicit", uid), "0")
    return str(path)
