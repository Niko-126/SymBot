"""亲密模式引擎：私聊大胆、群聊暗示、非恋人调侃拒绝。

三级模式（仅私聊可到 EXPLICIT）：
  OFF        → 正常聊天
  SUGGESTIVE → 隐喻/打擦边球（"查学历""棒棒糖"），群聊恋人限此级
  EXPLICIT   → 直白模式，仅在私聊 + 恋人 + 用户明确表态后进入

关键设计：
- 用户明确表达性意图 → 进入 EXPLICIT（仅私聊）
- 非恋人 + 性意图 → 调侃拒绝（"我是好女孩"）
- 群聊 + 恋人 → 只到 SUGGESTIVE，说"查"不说"插"
- 说 "查" 这是谐音隐喻，说变体（查户口 / 查学历 / 查避税 / 大调查）
- 进入 EXPLICIT 时强制开启深度思考（防出戏）
﻿‍‍‌‌‌‌‌‌‍‌﻿‍‍﻿‌﻿‌‌​﻿‌‌​‍​​﻿‌‍​﻿‌﻿"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from app.core import db


class Mode(str, Enum):
    OFF = "off"
    SUGGESTIVE = "suggestive"
    EXPLICIT = "explicit"


# ---------------- 用户意图检测 ----------------

# 用户明确表达性意图（→进 EXPLICIT 或调侃拒绝）
_EXPLICIT_INTENT = re.compile(
    r"(我想操|想操|好想操|想肏|想日|想上|想上你|想做爱|想睡|想睡你|"
    r"想插|想被插|被.*?插|欲仙欲死|想肏你|想干你|想日你|"
    r"想让你|想.*?舔|想.*?吸|想摸你|想舔你|想吸你|硬了|湿了|想要你|想宠你|"
    r"做爱|爱爱|papapa|打炮|来一炮|那个嘛|色色|"
    r"骚话|荤话|露骨|dirty|大鸡巴|大屌|肉棒|小穴|逼|乃子|奶子)"
)

# 用户表达暧昧但未到直白（→进 SUGGESTIVE）
_SUGGESTIVE_INTENT = re.compile(
    r"(亲亲|抱抱|抱我|摸摸|摸摸我|贴贴|蹭蹭|想抱|想亲|想摸|想黏|黏你|"
    r"喜欢你|爱你|想你|想见你|亲爹|乖女儿|宝贝|宝宝|陪我睡|"
    r"欧金金|棒棒糖|福福|逼逼|查.*?学历|查.*?避税|"
    r"想查|调查|大调查)"
)

# 用户叫停（→退回 OFF）
_STOP_INTIMATE = re.compile(
    r"(不色了|别色了|不玩了|正常聊|别这样|算了算了|"
    r"不想了|不要了|停|够了|打住)"
)


def detect_intent(text: str) -> str:
    """检测用户意图：explicit / suggestive / stop / none"""
    t = (text or "").strip()
    if not t:
        return "none"
    if _STOP_INTIMATE.search(t):
        return "stop"
    if _EXPLICIT_INTENT.search(t):
        return "explicit"
    if _SUGGESTIVE_INTENT.search(t):
        return "suggestive"
    return "none"


# ---------------- 模式管理 ----------------


def _key(user_id: int) -> str:
    return f"intimate_mode:{int(user_id)}"


def _ts_key(user_id: int) -> str:
    return f"intimate_mode_ts:{int(user_id)}"


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


# 模式有效期：直白是"一时兴起"，暗示也不该挂一整天
EXPLICIT_TTL_MIN = 45
SUGGESTIVE_TTL_MIN = 8 * 60


def touch(user_id: int) -> None:
    db.kv_set(_ts_key(user_id), _now_iso())


def expire(user_id: int, now: datetime | None = None) -> Mode:
    """过期的模式自动降级：直白 45 分钟、暗示 8 小时、跨天必降。

    `now` 只是给测试用的；不传就是此刻。
    """
    m = get_mode(user_id)
    if m == Mode.OFF:
        return Mode.OFF
    raw = db.kv_get(_ts_key(user_id), "")
    try:
        ts = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        ts = None
    if ts is None:
        touch(user_id)
        return m
    now = now or datetime.now().astimezone()
    if ts.tzinfo is None:
        ts = ts.astimezone()
    age_min = (now - ts).total_seconds() / 60
    if ts.date() < now.date() or age_min > SUGGESTIVE_TTL_MIN:
        set_mode(user_id, Mode.OFF)
        db.kv_set(_ts_key(user_id), "")
        return Mode.OFF
    if m == Mode.EXPLICIT and age_min > EXPLICIT_TTL_MIN:
        set_mode(user_id, Mode.SUGGESTIVE)
        touch(user_id)
        return Mode.SUGGESTIVE
    return m


def get_mode(user_id: int) -> Mode:
    v = db.kv_get(_key(user_id), Mode.OFF.value)
    try:
        return Mode(v)
    except ValueError:
        return Mode.OFF


def set_mode(user_id: int, mode: Mode) -> None:
    db.kv_set(_key(user_id), mode.value)


def force_off(user_id: int) -> None:
    """硬退出：模式和时效一起清掉（红灯/喊停/家人/未成年都走这里）。

    和 transition 里的 stop 分支等价，但给 gate / R 级插件的 safety 层用，
    省得它们自己去碰 kv key。
    """
    set_mode(user_id, Mode.OFF)
    db.kv_set(_ts_key(user_id), "")


def transition(
    user_id: int, intent: str, *, is_private: bool, is_lover: bool
) -> tuple[Mode, str]:
    """状态转移。返回 (新模式, 说明文本给 prompt 用)。"""
    current = expire(user_id)

    if intent == "stop":
        set_mode(user_id, Mode.OFF)
        db.kv_set(_ts_key(user_id), "")
        return Mode.OFF, "stop_soft"

    if intent == "explicit":
        if not is_private:
            if is_lover:
                # 群里说直白的 → 拉回暗示级，不可直白
                set_mode(user_id, Mode.SUGGESTIVE)
                touch(user_id)
                return Mode.SUGGESTIVE, "group_limit"
            # 群聊 + 非恋人：不启动（群里不跟非恋人玩这个）
            set_mode(user_id, Mode.OFF)
            return Mode.OFF, ""
        if is_lover:
            set_mode(user_id, Mode.EXPLICIT)
            touch(user_id)
            return Mode.EXPLICIT, ""
        # 非恋人 + 性意图 → 调侃拒绝，不动模式
        return current, "not_lover_deflect"

    if intent == "suggestive" and current == Mode.OFF:
        set_mode(user_id, Mode.SUGGESTIVE)
        touch(user_id)
        return Mode.SUGGESTIVE, ""

    # 带着 EXPLICIT 状态进群聊：立刻降级，群里永远不直白
    if not is_private and current == Mode.EXPLICIT:
        set_mode(user_id, Mode.SUGGESTIVE)
        touch(user_id)
        return Mode.SUGGESTIVE, "group_limit"

    if current == Mode.OFF and intent == "none":
        return Mode.OFF, ""

    # TODO（docs/29 §十）：断恋后 EXPLICIT 残留待修——
    # current == EXPLICIT and not is_lover 时应回落 OFF。
    # 用户要求"先做记录"，本轮不修。

    if current != Mode.OFF:
        touch(user_id)
    return current, ""


# ---------------- 记忆标记 ----------------


def is_intimate_context(user_id: int) -> bool:
    return get_mode(user_id) != Mode.OFF


def mark_event(user_id: int, is_private: bool) -> str:
    """返回事件标记字符串，用于 meta 里辨识。"""
    m = get_mode(user_id)
    if m == Mode.OFF:
        return ""
    return f"{m.value}|{'private' if is_private else 'group'}"
