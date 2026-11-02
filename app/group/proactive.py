"""群聊主动行为：找话题、随机活跃、语境判断、昵称、待机。

设计原则（docs/26）：
1. **用户话语绝对优先**：任何"别发了/闭嘴/停"都立刻进入静默，且静默期内不再主动。
2. **不粘人**：主动有冷却与频次上限，默认每小时最多 1-2 次，夜间不打扰。
3. **只在有语境时开口**：从群历史里找"还没聊完的事/她记得的话题/关键词视频"，
   没有抓手就闭嘴，不硬找话题。
4. **被 @ 之后会"接着聊"**：@她的人聊完转去跟别人说话 → 她判断自己不是焦点 → 待机。
5. **昵称**：她先用自己想的叫法；对方不满意就让他自己取，并写进日记。
﻿‌﻿‌﻿‌‍​​​﻿​‌‌​﻿‌​﻿‍﻿​﻿‍‍﻿‌﻿‌﻿﻿‌‌﻿"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Any

from app.core import db
from app.memory import store

# ---------------- 静默指令（最高优先级，命中即停） ----------------
STOP_PATTERNS = re.compile(
    r"(别发了|别说了|闭嘴|消停|安静|别吵|别刷|不要发|停止|停一下|先停|"
    r"别找话题|别主动|别打扰|不想聊|别烦我|别烦了|别再@|别艾特|"
    r"别回|别说话|禁言|安静点)"
)
# 静默时长：30 分钟。期间完全不输出；只有被 @ 才能打破。
STOP_MINUTES = 30
# 打破方式：被 @ 或明确叫她的名字
BREAK_ON_MENTION = True


def now() -> datetime:
    return datetime.now().astimezone()


def _kv(key: str, default: str = "") -> str:
    return db.kv_get(key, default) or default


def _gk(name: str, gid: Any = "") -> str:
    """群作用域键：传了 group_id 就按群隔离，没传就用全局键（单群/旧数据兼容）。"""
    return db.kv_key(name, gid)


# ---------------------------------------------------------------- 静默


def is_silenced(gid: Any = "") -> tuple[bool, str]:
    """当前是否在静默期。返回 (是否静默, 原因)。`gid` 非空时只看这个群。"""
    k_until = _gk("group_silent_until", gid)
    until = _kv(k_until)
    if not until:
        return False, ""
    try:
        dt = datetime.fromisoformat(until)
    except ValueError:
        return False, ""
    if dt > now():
        left = int((dt - now()).total_seconds() // 60) + 1
        return True, f"静默中，还剩 {left} 分钟"
    db.kv_set(k_until, "")
    return False, ""


def silence(minutes: int = STOP_MINUTES, reason: str = "用户要求安静", gid: Any = "") -> str:
    until = (now() + timedelta(minutes=max(1, minutes))).isoformat(timespec="seconds")
    db.kv_set(_gk("group_silent_until", gid), until)
    db.kv_set(_gk("group_silent_reason", gid), reason)
    return until


def break_silence(by: str = "@", gid: Any = "") -> bool:
    """被 @ 打破静默。返回是否真的打破了（只看这个群）。"""
    was = is_silenced(gid)[0]
    db.kv_set(_gk("group_silent_until", gid), "")
    db.kv_set(_gk("group_silent_reason", gid), "")
    return was


def unsilence(gid: Any = "") -> None:
    db.kv_set(_gk("group_silent_until", gid), "")
    db.kv_set(_gk("group_silent_reason", gid), "")


def detect_stop(text: str) -> bool:
    return bool(STOP_PATTERNS.search(text or ""))


# ---------------------------------------------------------------- 无回应就停


NO_REPLY_LIMIT = 3  # 连续几条没人接就闭嘴


def unanswered_streak(gid: Any = "") -> int:
    """她连续发了几条、中间没有任何人说话。

    从最新事件往回数：「她说的」算 +1，遇到「用户说的」就停。
    `gid` 非空时只数这个群——不然 A 群的冷场会被算到 B 群头上。
    """
    g = str(gid or "").strip()
    if g:
        rows = db.rows(
            "SELECT role FROM events WHERE role IN ('user','her') AND group_id = ?"
            " ORDER BY id DESC LIMIT 20",
            (g,),
        )
    else:
        rows = db.rows(
            "SELECT role FROM events WHERE role IN ('user','her') ORDER BY id DESC LIMIT 20"
        )
    n = 0
    for r in rows:
        role = str(r["role"])
        if role == "her":
            n += 1
        else:
            break
    return n


def should_be_quiet(gid: Any = "") -> tuple[bool, str]:
    """她是不是该闭嘴了：连续 2~3 条没人接。返回 (是否该停, 原因)。"""
    streak = unanswered_streak(gid)
    if streak >= 2:
        return True, f"已经连发 {streak} 条没人接"
    return False, ""


def focus_user_silent(minutes: int = 20) -> bool:
    """跟她聊的人是不是已经晾了她一段时间。"""
    last_user = db.one(
        "SELECT ts FROM events WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    )
    if not last_user:
        return True
    try:
        gap = (now() - datetime.fromisoformat(str(last_user["ts"]))).total_seconds() / 60
    except ValueError:
        return False
    return gap >= minutes


# ---------------------------------------------------------------- 主动配额


def _today_key(name: str, gid: Any = "") -> str:
    base = db.kv_key(f"group_{name}", gid)
    return f"{base}:{now().date().isoformat()}"


def _bump(name: str, gid: Any = "") -> int:
    k = _today_key(name, gid)
    n = int(_kv(k, "0") or 0) + 1
    db.kv_set(k, str(n))
    return n


def _count(name: str, gid: Any = "") -> int:
    return int(_kv(_today_key(name, gid), "0") or 0)


def quota_ok(gid: Any = "") -> tuple[bool, str]:
    """主动消息配额：白天每小时最多 2 次，全天最多 8 次，22:00-08:00 不主动。

    `gid` 非空时配额按群各算各的（一个群聊满了不会把另一个群也锁死）。
    """
    h = now().hour
    if h >= 22 or h < 8:
        return False, "夜间不主动"
    last = _kv(_gk("group_last_proactive", gid))
    if last:
        try:
            gap = (now() - datetime.fromisoformat(last)).total_seconds() / 60
            if gap < 25:  # 至少隔 25 分钟
                return False, f"冷却中（{int(gap)} 分钟前刚发过）"
        except ValueError:
            pass
    if _count("proactive", gid) >= 8:
        return False, "今天主动次数已满"
    return True, ""


def mark_proactive(gid: Any = "") -> None:
    db.kv_set(_gk("group_last_proactive", gid), now().isoformat(timespec="seconds"))
    _bump("proactive", gid)


# ---------------------------------------------------------------- 话题素材


def _recent_group_lines(limit: int = 40, hours: int = 48, gid: Any = "") -> list[dict[str, Any]]:
    cutoff = (now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    g = str(gid or "").strip()
    if g:
        rows = db.rows(
            "SELECT id, role, text, user_id, ts FROM events WHERE ts >= ?"
            " AND role IN ('user','her') AND group_id = ? ORDER BY id DESC LIMIT ?",
            (cutoff, g, int(limit)),
        )
    else:
        rows = db.rows(
            "SELECT id, role, text, user_id, ts FROM events WHERE ts >= ?"
            " AND role IN ('user','her') ORDER BY id DESC LIMIT ?",
            (cutoff, int(limit)),
        )
    return [dict(r) for r in reversed(rows)]


def _unfinished_topics(limit: int = 8, gid: Any = "") -> list[str]:
    """从群历史里找"还没聊完"的话题：只看**用户说的**，不看她自己说的。

    只取问句、待约定、以及用户提过的事——这样她开口是"接人的话"，不是自言自语。
    """
    lines = _recent_group_lines(80, 72, gid)
    picks: list[str] = []
    for item in reversed(lines):
        if item.get("role") != "user":
            continue
        t = (item.get("text") or "").strip().replace("\n", " ")
        if len(t) < 6 or len(t) > 40:
            continue
        if t in picks:
            continue
        if "？" in t or "?" in t:
            picks.append(t)
        elif any(k in t for k in ("明天", "改天", "下次", "等我", "回头", "再说", "打算", "准备")):
            picks.append(t)
        if len(picks) >= limit:
            break
    return picks


def _hot_gag() -> str | None:
    try:
        from app.web import gags as gags_mod

        g = gags_mod.pick_gag()
        return str(g.get("text") if g else "") or None
    except Exception:  # noqa: BLE001
        return None


def _keyword_video() -> dict[str, Any] | None:
    """从热梗库/群历史里挑一个关键词，搜一个视频当话题。"""
    try:
        from app.web import bili

        rows = db.rows("SELECT title FROM web_videos ORDER BY RANDOM() LIMIT 1")
        kw = str(rows[0]["title"])[:12] if rows else ""
        if not kw:
            return None
        items = bili.search_videos(kw, 2)
        return items[0] if items else None
    except Exception:  # noqa: BLE001
        return None


def build_topic(gid: Any = "") -> dict[str, Any] | None:
    """给"她主动开口"准备话题。返回 {kind, text} 或 None（没抓手就闭嘴）。

    会排除最近 3 天已经说过的同类话题，避免在群里复读同一个梗。
    `gid` 非空时只从**这个群**的历史里找话题，不去引用别的群聊。
    """
    from datetime import timedelta

    g = str(gid or "").strip()
    recent_cut = (now() - timedelta(days=3)).isoformat(timespec="seconds")
    rows = (
        db.rows(
            "SELECT text FROM events WHERE source = 'group_proactive' AND ts >= ?"
            " AND group_id = ?", (recent_cut, g),
        )
        if g else
        db.rows(
            "SELECT text FROM events WHERE source = 'group_proactive' AND ts >= ?", (recent_cut,),
        )
    )
    said = {str(r["text"] or "") for r in rows}

    def _fresh(candidate: str) -> bool:
        # 只要"核心片段"没出现在最近说过的内容里，就算新
        core = candidate[:24]
        return not any(core in s or s[:24] in candidate for s in said)

    roll = random.random()
    if roll < 0.45:
        topics = [t for t in _unfinished_topics(gid) if _fresh(t)]
        if topics:
            t = random.choice(topics)
            return {"kind": "unfinished", "text": f"对了，刚想起你之前说的「{t}」，后来怎么样了？"}
    if roll < 0.75:
        gag = _hot_gag()
        if gag and _fresh(gag):
            return {"kind": "gag", "text": f"刷到个说法：{gag}。是不是挺像我们群的。"}
    video = _keyword_video()
    if video:
        text = f"翻到个视频，{video['title'][:30]}\n{video['url']}"
        if _fresh(text):
            return {"kind": "video", "text": text}
    return None
