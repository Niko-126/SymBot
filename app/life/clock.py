from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import db
from app.life import human_life
from app.persona.load import Persona

_TZ = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    return datetime.now(_TZ)


def human_time(dt: datetime) -> str:
    h, m = dt.hour, dt.minute
    if h < 5:
        prefix = "凌晨"
    elif h < 8:
        prefix = "早上"
    elif h < 11:
        prefix = "上午"
    elif h < 13:
        prefix = "中午"
    elif h < 18:
        prefix = "下午"
    else:
        prefix = "晚上"
    return f"{prefix} {h} 点 {m} 分"


def get_state(persona: Persona) -> dict:
    """读一次，按作息表把"此刻"算出来，再写回。M0 不做交互累积。"""
    dt = now()
    slot = persona.activity_at(dt.hour, dt.minute)
    activity = str(slot.get("activity", "发呆"))
    base_energy = float(slot.get("energy", 50))
    sleeping = bool(slot.get("sleeping", False))
    dev_awake = db.kv_get("dev_awake", "0") == "1"
    if dev_awake:
        sleeping = False
        activity = "清醒着（开发者）"

    row = db.one("SELECT * FROM life_state WHERE id = 1")
    mood = float(row["mood"]) if row else 0.0
    focus = float(row["focus"]) if row else 50.0
    social = float(row["social_battery"]) if row else 60.0

    # 精力按"离作息表最近的状态"缓慢靠拢，避免一整点跳变﻿﻿​‌​﻿﻿​‌﻿﻿‍​‍​﻿‍‌‍‌‌﻿‌﻿‌​﻿‍‌‍​﻿​﻿
    energy = base_energy if row is None else round(float(row["energy"]) * 0.6 + base_energy * 0.4, 1)

    # 「她的一天」只读接管：只有 kv 开关（默认 "0"）打开时，才用人类语料盖掉
    # 槽位文案 / 精力 / 睡没睡；关着时整段跳过，返回值跟以前一模一样。
    # dev_awake 仍然最优先：开发者让她醒着，就不许语料把 sleeping 改回 true。
    hl = None
    if not dev_awake and human_life.enabled():
        try:
            hl = human_life.resolve(dt)
        except Exception:
            hl = None
    if hl:
        activity = str(hl.get("activity") or activity)
        energy = float(hl.get("energy"))
        sleeping = bool(hl.get("sleeping"))

    db.execute(
        "UPDATE life_state SET energy = ?, mood = ?, focus = ?, social_battery = ?, activity = ?, last_tick = ?"
        " WHERE id = 1",
        (energy, mood, focus, social, activity, dt.isoformat(timespec="seconds")),
    )

    return {
        "virtual_time": dt.isoformat(timespec="seconds"),
        "virtual_time_human": human_time(dt),
        "activity": activity,
        "energy": energy,
        "mood": mood,
        "focus": focus,
        "social_battery": social,
        "sleeping": sleeping,
        "hour": dt.hour,
        "human_life": hl,
    }


def set_mood(mood: float | None = None, energy: float | None = None) -> None:
    row = db.one("SELECT * FROM life_state WHERE id = 1")
    if not row:
        return
    db.execute(
        "UPDATE life_state SET mood = ?, energy = ? WHERE id = 1",
        (float(row["mood"]) if mood is None else mood, float(row["energy"]) if energy is None else energy),
    )
