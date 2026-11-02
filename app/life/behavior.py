"""状态驱动的回复行为。

同一个问题，她在睡觉、在上班、在洗澡，反应应该不一样：
- **清醒度模型（不是硬闸门）**：睡着时你一句话不一定能叫醒她，但**越聊越清醒**；
  停一会儿不聊，会自己慢慢睡回去（半衰期约 6 分钟）。
  - asleep  0.00-0.25  被吵醒，迷糊，只回两三个字
  - drowsy  0.25-0.55  半梦半醒，短句，愿意接话但没力气展开
  - sleepy  0.55-0.80  基本醒了，嘴上还带着困意
  - awake   0.80-1.00  彻底醒了，正常说话
- 忙（上班/洗澡/吃饭）：延迟变长，句子变短
- 精力低：延迟增加，回复更短

这里只产出"策略"，不改业务：延迟、是否回、给用户看的提示、给模型的状态提示。
﻿‌﻿‌​﻿​​‍﻿‍‍​​‌‍‍‍‌‌‍‍​​‌‌​‌​‍‌​﻿﻿"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Reaction:
    mode: str = "awake"          # awake | sleepy | drowsy | asleep | busy
    will_reply: bool = True
    delay_ms: int = 0            # 首条气泡之前的额外等待
    hint: str = ""               # 面板上的系统提示
    prompt_hint: str = ""        # 注入模型的状态描述


_SLEEPY_ACTIVITY = ("睡",)
_BUSY_ACTIVITY = ("洗澡", "吃饭", "上班", "归档", "转录", "开会", "下班路上")

#: 清醒度：每聊一句涨一点，停聊按半衰期回落
AWAKE_KEY = "awake_level"
AWAKE_TS_KEY = "awake_ts"
AWAKE_HALF_LIFE_MIN = 6.0     # 停 6 分钟掉一半；约 20 分钟回到"睡着"
AWAKE_STEP_BASE = 0.14        # 一句话的基础增量（第一句只够"迷糊"，连聊才会醒）
AWAKE_STEP_MAX_EXTRA = 0.10   # 长句的额外增量上限
AWAKE_KEYS = (0.25, 0.55, 0.80)


def _kv_get(key: str, default: str = "") -> str:
    try:
        from app.core import db

        return str(db.kv_get(key, default) or default)
    except Exception:  # noqa: BLE001 - 没有库就当作没状态
        return default


def _kv_set(key: str, value: str) -> None:
    try:
        from app.core import db

        db.kv_set(key, value)
    except Exception:  # noqa: BLE001
        pass


def awake_level(now: float | None = None) -> float:
    """当前清醒度（含按时间衰减）。0=睡死，1=完全清醒。"""
    now = time.time() if now is None else float(now)
    try:
        level = float(_kv_get(AWAKE_KEY, "0") or 0)
    except ValueError:
        level = 0.0
    try:
        ts = float(_kv_get(AWAKE_TS_KEY, "0") or 0)
    except ValueError:
        ts = 0.0
    level = max(0.0, min(1.0, level))
    if ts > 0 and now > ts:
        idle_min = (now - ts) / 60.0
        level *= 0.5 ** (idle_min / AWAKE_HALF_LIFE_MIN)
    return max(0.0, min(1.0, level))


def bump_awake(chars: int = 0, recent_user_turns: int = 0, now: float | None = None) -> float:
    """聊了一句 → 清醒度上升。连续聊/长句涨得更快（越聊越想聊）。"""
    now = time.time() if now is None else float(now)
    level = awake_level(now)
    extra = min(AWAKE_STEP_MAX_EXTRA, max(0, int(chars)) / 240.0)
    streak = 0.02 * min(3, max(0, int(recent_user_turns)))
    level = min(1.0, level + AWAKE_STEP_BASE + extra + streak)
    _kv_set(AWAKE_KEY, f"{level:.4f}")
    _kv_set(AWAKE_TS_KEY, f"{now:.3f}")
    return level


def _level_mode(level: float) -> str:
    if level < AWAKE_KEYS[0]:
        return "asleep"
    if level < AWAKE_KEYS[1]:
        return "drowsy"
    if level < AWAKE_KEYS[2]:
        return "sleepy"
    return "awake"


_SLEEP_HINTS = {
    "asleep": ("刚被你叫醒，脑子是糊的，眼皮都睁不开。只回一句，两三个字，别解释、别提问。", 1500),
    "drowsy": ("半梦半醒，被他的话勾得清醒了一点，但还没力气。回一到两句，短，带困意；不要说自己「在忙」（你现在只是困）。", 2200),
    "sleepy": ("困，但已经能正常接话了。两句以内，语气软，可以有一点点黏；不要编「在忙」这种借口。", 1600),
    "awake": ("你已经完全醒了（他一句话一句话把你聊醒的）。正常说话，但别突然亢奋。", 900),
}


def decide(
    life: dict[str, Any],
    recent_user_turns: int = 0,
    persona=None,
    *,
    text: str = "",
    now: float | None = None,
) -> Reaction:
    sleeping = bool(life.get("sleeping"))
    energy = float(life.get("energy") or 60)
    hour = int(life.get("hour") or 12)
    activity = str(life.get("activity") or "")
    sleepish = sleeping or any(k in activity for k in _SLEEPY_ACTIVITY)

    # 清醒度只在"该睡的时候"才需要爬坡；白天本来就是醒的
    prev_level = awake_level(now)
    level = prev_level
    if sleepish:
        level = bump_awake(len(text or ""), recent_user_turns, now)
        if prev_level >= AWAKE_KEYS[2]:
            # 已经聊醒了：别再每轮塞"被叫醒"的状态提示（她会一直劝睡/复述）
            return Reaction(mode="awake", will_reply=True, delay_ms=600, hint="",
                            prompt_hint="")
        mode = _level_mode(level)
        hint, base_delay = _SLEEP_HINTS[mode]
        if mode == "asleep":
            return Reaction(
                mode="asleep", will_reply=True, delay_ms=base_delay,
                hint="她好像刚睡着，被你叫醒了。再聊两句她就清醒了。",
                prompt_hint=(
                    f"【现在的状态】{hint}"
                    + (f"（她今晚的作息：{activity}）" if activity else "")
                ),
            )
        if mode == "awake":
            return Reaction(
                mode="awake", will_reply=True, delay_ms=base_delay,
                hint="聊着聊着，她清醒过来了。",
                prompt_hint="【现在的状态】你本来快睡了，被他一句一句聊醒了，现在脑子清楚了不少。语气正常，可以带一句「被你说醒了」。",
            )
        return Reaction(
            mode=mode, will_reply=True, delay_ms=base_delay,
            hint="她被你聊得清醒了一点。" if mode == "drowsy" else "",
            prompt_hint=f"【现在的状态】{hint}",
        )

    # 白天：也记一句，便于"聊着聊着更精神"（这里不做衰减，只抬升）
    bump_awake(len(text or ""), recent_user_turns, now)

    # 忙：延迟，短句
    if any(k in activity for k in _BUSY_ACTIVITY):
        return Reaction(
            mode="busy",
            delay_ms=4200,
            hint="",
            prompt_hint=f"你正在{activity}，只能抽空回一两句。句子短，不展开。",
        )

    # 精力低：慢一点、短一点，但别冷冰冰
    if energy <= 25:
        return Reaction(
            mode="sleepy",
            delay_ms=2600,
            hint="",
            prompt_hint="你精力很低，快撑不住了，但还想跟他说话。回复短、语气软，可以黏一句、撒个娇，别赶人。",
        )

    # 深夜（0-5 点）但仍醒着：比白天慢
    if 0 <= hour <= 5:
        return Reaction(
            mode="awake",
            delay_ms=1200,
            hint="",
            prompt_hint="现在是深夜，你有点困但还醒着，话比白天少；可以软一点、黏一点，别只回一个「嗯」。",
        )

    return Reaction(mode="awake")


def apply_delay(segments: list[str], reaction: Reaction) -> list[int]:
    """把策略里的延迟加进分段节奏；加一点随机抖动，别像机器定时器。"""
    import random

    base = max(0, int(reaction.delay_ms))
    return [max(200, base + 420 + len(s) * 28 + random.randint(-80, 220)) for s in segments]
