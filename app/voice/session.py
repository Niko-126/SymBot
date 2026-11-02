"""语音会话节奏：连着发几条语音 → 主动"说话累了"退回打字 → 用户要就继续。

为什么要它（2026-11-02 owner 定的玩法）：
- 一直发语音不像人。真人连着说几条会累，会自己改成打字；
- 但用户说"我还想听"的时候要立刻恢复，不能死板地一直拒绝；
- 每发三条问一句"还要听吗"——**问法不写死**，由模型自己换着说
  （这里只给提示，句子交给 `loop.chat_turn` 生成，避免模板腔）；
- 用户说"不用了/休息吧"，或者干脆不回（超过 `idle_reset_min` 分钟），
  状态自动清掉，下次从"可以发语音"重新开始。

状态存 kv（`voice_session:<uid>`），跨进程/重启都还在，但**不写 events**——
这是节奏控制，不是她的记忆。

对外只有四个函数：`decide()` / `note_voice()` / `note_text()` / `reset()`。
﻿‌‌​‍﻿﻿​‌‌‌​​‍​‍﻿​​﻿‍﻿‍​‍‌‍﻿‌​﻿‌‍﻿"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core import db

KV_PREFIX = "voice_session:"

#: 用户明确要语音（恢复/继续）
_WANT_VOICE = (
    "想听语音", "听你说话", "听你说", "用语音", "语音回", "发语音", "继续说",
    "继续语音", "再来一句", "还想听", "接着发", "别打字", "我要听", "说话给我听",
)
#: 用户明确叫停
_STOP = (
    "不用了", "别发了", "别语音", "不用语音", "停一下", "停下", "休息吧", "歇会儿",
    "算了", "打字就行", "打字吧", "太吵", "安静点",
)
#: 问"还要听吗"时轮换的语气提示 —— 保证每次问法不一样
_ASK_STYLES = (
    "用小声撒娇的语气",
    "用有点不好意思的语气",
    "用故意装拽的语气",
    "用逗他的语气",
    "用刚唱完还没缓过来的语气",
    "用半开玩笑的语气",
)

_STATE_FIELDS = ("mode", "streak", "last_at", "last_ask_streak")


def _key(uid: int) -> str:
    return f"{KV_PREFIX}{int(uid)}"


def _load(uid: int) -> dict[str, Any]:
    try:
        raw = db.kv_get(_key(uid), "") or ""
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - 状态坏了就当没有，别影响聊天
        data = {}
    return data if isinstance(data, dict) else {}


def _save(uid: int, state: dict[str, Any]) -> None:
    try:
        db.kv_set(_key(uid), json.dumps(state, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def _cfg(rt: Any) -> Any:
    return getattr(rt, "cfg", None).voice if rt is not None and getattr(rt, "cfg", None) else None


def _get(cfg: Any, name: str, default: Any) -> Any:
    box = getattr(cfg, "voice", cfg)
    value = getattr(box, name, default) if box is not None else default
    return default if value is None else value


def reset(uid: int) -> None:
    """一键清空（开发者台/测试用）。"""
    _save(int(uid), {})


def decide(rt: Any, uid: int, text: str, *, now: float | None = None) -> dict[str, Any]:
    """这一轮该不该发语音、要不要喊累、要不要问"还要听吗"。

    返回 `{"allow", "tired", "ask_now", "ask_hint", "tired_hint", "reason"}`。
    只读 + 不写盘（写盘在 `note_voice` / `note_text` 里做，避免"没发出去也算一条"）。
    """
    cfg = _cfg(rt)
    ask_every = max(1, int(_get(cfg, "ask_every", 3)))
    streak_limit = max(1, int(_get(cfg, "streak_limit", 4)))
    idle_min = max(1, int(_get(cfg, "idle_reset_min", 30)))

    state = _load(uid)
    ts = time.time() if now is None else float(now)
    last_at = float(state.get("last_at") or 0)
    if last_at and ts - last_at > idle_min * 60:
        state = {}  # 冷太久：忘了"累了"，从可以发语音重新开始

    mode = str(state.get("mode") or "voice")
    streak = int(state.get("streak") or 0)
    last_ask = int(state.get("last_ask_streak") or 0)
    mode_at_entry = mode

    body = (text or "").strip()
    wants = any(k in body for k in _WANT_VOICE)
    stops = any(k in body for k in _STOP)

    if stops and not wants:
        mode, streak = "text", 0
    if wants:
        mode, streak = "voice", 0  # 用户点名要语音：立刻恢复，重新计数

    allowed = mode != "text"
    tired = False
    if allowed and streak >= streak_limit and not wants:
        # 连着发够了：这一轮不改语音，并且让她自己说"累了"
        tired = True
        allowed = False
        mode = "text"
        streak = 0
    # 已经改成打字了就别每轮都念一遍"我累了"，只在切换的那一轮说
    if mode_at_entry == "text" and not wants:
        tired = False

    ask_now = bool(allowed and streak > 0 and streak % ask_every == 0 and last_ask != streak)

    ask_hint = ""
    if ask_now:
        style = _ASK_STYLES[streak % len(_ASK_STYLES)]
        ask_hint = (
            "【这一轮的要求】你已经连着用语音说了好几条。"
            f"在回复的最末尾，{style}问他还要不要继续听你说话。"
            "只能问这一句，别解释、别写成固定模板，也别重复以前的问法。"
        )
    tired_hint = ""
    if tired:
        tired_hint = (
            "【这一轮的要求】你连着发了好几条语音，嗓子真的有点累了。"
            "用一句话说出来（要像人，不要像客服），然后自然地把话题带回打字聊天；"
            "如果他之后说还想听，你随时可以再开口。"
        )

    return {
        "allow": allowed,
        "tired": tired,
        "ask_now": ask_now,
        "ask_hint": ask_hint,
        "tired_hint": tired_hint,
        "streak": streak,
        "mode": mode,
        "reason": "wants" if wants else ("stop" if stops else ("tired" if tired else mode)),
    }


def note_voice(rt: Any, uid: int, *, asked: bool = False, now: float | None = None) -> dict[str, Any]:
    """真发出去了一条语音：计数 +1，记下这轮问没问过。"""
    ts = time.time() if now is None else float(now)
    state = _load(uid)
    streak = int(state.get("streak") or 0) + 1
    state.update({
        "mode": "voice",
        "streak": streak,
        "last_at": ts,
    })
    if asked:
        state["last_ask_streak"] = streak
    _save(uid, state)
    return state


def note_text(rt: Any, uid: int, *, now: float | None = None) -> dict[str, Any]:
    """只发了文字：只更新时间戳，不动计数（文字回复不该打断语音连发）。"""
    ts = time.time() if now is None else float(now)
    state = _load(uid)
    state["last_at"] = ts
    state.setdefault("mode", "voice")
    state.setdefault("streak", 0)
    _save(uid, state)
    return state


def mark_resume(uid: int, *, now: float | None = None) -> dict[str, Any]:
    """用户明确说"还想听"：立刻从打字模式切回语音，计数清零重来。"""
    ts = time.time() if now is None else float(now)
    state = _load(uid)
    state.update({"mode": "voice", "streak": 0, "last_at": ts})
    _save(uid, state)
    return state


def mark_tired(uid: int, *, now: float | None = None) -> dict[str, Any]:
    """"喊累了"的那一轮落盘：记住改成打字，不然下一轮又会重新数到上限再喊一遍。"""
    ts = time.time() if now is None else float(now)
    state = _load(uid)
    state.update({"mode": "text", "streak": 0, "last_at": ts})
    _save(uid, state)
    return state


def status(uid: int) -> dict[str, Any]:
    return _load(uid)
