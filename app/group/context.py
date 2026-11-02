"""群聊语境：判断"在跟谁说话"、"她是不是焦点"、昵称管理。

三个问题：
1. 这条消息没 @ 她，是在跟她说话吗？（看最近几条的指向）
2. 她被 @ 之后，对话转给别人了 —— 该待机。
3. 她怎么称呼每个人？先用她自己想的；对方不满意就让他改名，并写进日记。
﻿​‌‌‍‍‍﻿​‍​‍‌﻿‍​​‍﻿‍‍‌‌﻿​​‍﻿﻿﻿‌‍​﻿"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core import db

# 昵称：默认从"叫她什么"里学，或者她起一个
# 注意顺序：否定/询问要在"给昵称"之前判断，否则"别叫我话痨"会被当成设昵称
NICK_ASK = re.compile(r"(别叫我|不要叫|别喊我|换个叫法|你叫我什么|怎么称呼|你想怎么叫我)")
# 用户明确给新昵称（排除"别叫我"）
NICK_GIVE = re.compile(r"(?:^|[，。；\s])(?:以后叫我|就叫我|可以叫我|叫我)\s*[\"'「“]?([^\s\"'」”，。]{1,8})")

# 她可能主动给的昵称池（按关系阶段/性格挑）
NICK_POOL = (
    "话痨", "夜猫子", "早起困难户", "白饭续命人", "深夜党", "火锅常客",
    "念念碎", "小情绪", "冷面热心", "话少星人",
)


def now() -> datetime:
    return datetime.now().astimezone()


# ---------------------------------------------------------------- 昵称


def nick_of(user_id: int) -> str:
    row = db.one("SELECT value FROM kv WHERE key = ?", (f"nick:{int(user_id)}",))
    return str(row["value"]) if row else ""


def set_nick(user_id: int, nick: str, *, by: str = "her") -> str:
    nick = (nick or "").strip()[:8]
    if not nick:
        return ""
    db.kv_set(f"nick:{int(user_id)}", nick)
    db.kv_set(f"nick_by:{int(user_id)}", by)
    # 写进日记：她记下"这个人想被怎么叫"
    try:
        from app.life import diary

        who = "他自己说的" if by == "user" else "我给他起的"
        diary.note_event(f"我开始叫他「{nick}」（{who}），以后别叫错了。", topic="相处", user_id=int(user_id))
    except Exception:  # noqa: BLE001
        pass
    return nick


def reset_nick(user_id: int) -> None:
    db.execute("DELETE FROM kv WHERE key IN (?, ?)", (f"nick:{int(user_id)}", f"nick_by:{int(user_id)}"))


def suggest_nick(user_id: int, user_name: str = "") -> str:
    """她起一个昵称（优先按对方说过的话里的特征）。"""
    existing = nick_of(user_id)
    if existing:
        return existing
    # 从最近的对话里找特征词
    rows = db.rows(
        "SELECT text FROM events WHERE user_id = ? AND role = 'user' ORDER BY id DESC LIMIT 8",
        (int(user_id),),
    )
    joined = " ".join(str(r["text"] or "") for r in rows)
    hints = [
        ("累", "夜猫子"), ("加班", "加班人"), ("困", "起床困难户"),
        ("饭", "干饭人"), ("吃", "干饭人"), ("睡", "深夜党"),
        ("哈哈", "话痨"), ("？", "十万个为什么"),
    ]
    for kw, nick in hints:
        if kw in joined:
            return set_nick(user_id, nick, by="her")
    import random

    return set_nick(user_id, random.choice(NICK_POOL), by="her")


def handle_nick_change(text: str, user_id: int) -> dict[str, Any] | None:
    """用户说"别叫我 xx"/"以后叫我 xx" → 改昵称。

    顺序很重要：先判否定/询问，再判给昵称。
    """
    t = text or ""
    # 1) "别叫我 X" / "不要叫 X" / "你叫我什么" → 让她重新起或让对方自己取
    if NICK_ASK.search(t):
        reset_nick(user_id)
        return {"ok": True, "nick": "", "by": "ask", "need_name": True}
    # 2) "以后叫我 X" / "就叫我 X" → 用对方给的名字
    m = NICK_GIVE.search(t)
    if m:
        pic = m.group(1).strip()
        # 去掉尾部的语气词（"阿岸吧" → "阿岸"）
        pic = re.sub(r"[吧啊呀哦呢嘛哈的了]+$", "", pic).strip()
        if pic:
            set_nick(user_id, pic, by="user")
            return {"ok": True, "nick": pic, "by": "user"}
    return None


# ---------------------------------------------------------------- 语境判断


def _recent(limit: int = 8, minutes: int = 10, gid: Any = "") -> list[dict[str, Any]]:
    """最近 N 条对话。`gid` 非空时只看这个群——不然会拿别的群的聊天判断语境。"""
    cutoff = (now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    g = str(gid or "").strip()
    if g:
        rows = db.rows(
            "SELECT role, text, user_id, ts FROM events WHERE ts >= ? AND role IN ('user','her')"
            " AND group_id = ? ORDER BY id DESC LIMIT ?",
            (cutoff, g, int(limit)),
        )
    else:
        rows = db.rows(
            "SELECT role, text, user_id, ts FROM events WHERE ts >= ? AND role IN ('user','her')"
            " ORDER BY id DESC LIMIT ?",
            (cutoff, int(limit)),
        )
    return [dict(r) for r in reversed(rows)]


def who_is_talking_to_her(text: str, sender_id: int, mentioned: bool,
                          gid: Any = "") -> tuple[bool, str]:
    """判断这句话是不是在跟她说话。

    返回 (是否在跟她说话, 理由)。`gid` 非空时语境只看这个群。
    """
    if mentioned:
        return True, "被 @ 了"
    lines = _recent(10, 15, gid)
    if not lines:
        return False, "没有上下文"
    last = lines[-1]
    # 1) 上一条是她说的，且这个人是刚才 @她的那个人 → 接话
    focus_raw = db.kv_get(db.kv_key("group_focus_user", gid), "") or ""
    focus_user = int(focus_raw) if focus_raw.isdigit() else 0
    if last.get("role") == "her" and int(last.get("user_id") or 0) == int(sender_id):
        return True, "接着她的话说"
    # 2) 刚才 @过她的人继续说话，且中间没人插话 → 还在聊
    if focus_user == int(sender_id) and last.get("role") == "her":
        return True, "刚才 @ 过她，接着聊"
    # 3) 最近 15 分钟内她的最后一条之后，只有这个人在说话
    her_idx = max(
        (i for i, x in enumerate(lines) if x.get("role") == "her"), default=-1
    )
    if her_idx >= 0:
        after = lines[her_idx + 1:]
        senders = {int(x.get("user_id") or 0) for x in after if x.get("role") == "user"}
        if senders and senders == {int(sender_id)}:
            return True, "同一人连续对话"
    # 4) 有人插话之后又回来 → 不接
    return False, "话题已转移"


def is_focus_lost(user_id: int, text: str, gid: Any = "") -> bool:
    """她是不是不再是焦点了：@她的那个人已经去跟别人说话。"""
    lines = _recent(6, 15, gid)
    if not lines:
        return False
    last = lines[-1]
    # 最后一条是别人（不是她，也不是原本跟她聊的人）→ 焦点转移
    if last.get("role") == "user" and int(last.get("user_id") or 0) != int(user_id):
        if not any(
            (x.get("role") == "her") and int(x.get("user_id") or 0) == int(user_id)
            for x in lines[-3:]
        ):
            return True
    return False


def standby_state(gid: Any = "") -> dict[str, Any]:
    """当前是否该待机（给前端/调试看）。`gid` 非空时只看这个群。"""
    from app.group import proactive

    silent, reason = proactive.is_silenced(gid)
    last = db.kv_get(db.kv_key("group_last_proactive", gid), "") or ""
    return {
        "silenced": silent,
        "silence_reason": reason,
        "last_proactive": last,
        "quota_ok": proactive.quota_ok(gid),
    }
