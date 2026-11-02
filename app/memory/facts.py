"""轻量事实抽取：让"记住你"真的发生。

每 6 轮用户消息抽一次，最多 2 条事实，交给衡忆存着。
抽不出来就不存——宁缺毋滥，噪音比空白更伤。
﻿﻿‍​‌﻿﻿﻿‌‌﻿‌​‌‍‍​﻿‌​﻿﻿‌‍​​‌​﻿‍​‍﻿﻿"""

from __future__ import annotations

import json
import re

from app.core import db

_SYSTEM = (
    "从对话里挑出关于对方（用户）的、以后还用得上的具体事实。\n"
    "只要具体的：职业、作息、喜好、身体、正在经历的事、约定。\n"
    "写事实时必须用对方的名字（例如「星辰在值班」），不要用「他/用户/对方/我爸」这种代词。\n"
    "分不清是谁说的、或者只是玩笑/假设/转述，就不要写。\n"
    "不要情绪、不要评价、不要猜。没有就给空数组。\n"
    '输出 JSON 数组，最多 2 条，每条 30 字以内，例如 ["星辰凌晨三点睡", "魔法粉兔子会变鱼饭"]'
)

_NOISE = ("用户", "对方", "他", "她", "感觉", "可能", "喜欢", "不喜欢")


def _clean(items: list) -> list[str]:
    out: list[str] = []
    for it in items:
        if not isinstance(it, str):
            continue
        t = " ".join(it.split()).strip("，。； ")
        if not (4 <= len(t) <= 40):
            continue
        if t in ("用户", "对方", "他", "她"):
            continue
        if any(t.startswith(n) for n in ("用户说", "对方说", "用户是", "他是一个")):
            continue
        out.append(t)
    return out[:2]


def _scrub_openids(text: str, names_by_openid: dict[str, str]) -> str:
    """把事实里残留的 openid 换成名字（真机实测：官方通道昵称为空时，
    记忆里出现「49BEBF…明天有面试」这种字符串）。"""
    out = str(text or "")
    for oid, name in names_by_openid.items():
        if oid and oid in out:
            out = out.replace(oid, name or "对方")
    return out.strip()


async def maybe_extract(rt, seq: int = 1, user_id: int = 0, chat_type: str = "") -> int:
    """到点就抽一次。返回新增条数。user_id>0 时只抽该用户的对话，记忆归他。

    `chat_type` 只影响"从哪个场合的对话里抽"，抽出来的事实也归这个场合。
    """
    user_id = int(user_id or 0)
    chat_type = str(chat_type or "")
    if user_id > 0:
        user_count = db.one(
            "SELECT COUNT(*) c FROM events WHERE role = 'user' AND user_id = ?", (user_id,)
        )["c"]
    else:
        user_count = db.one("SELECT COUNT(*) c FROM events WHERE role = 'user'")["c"]
    if not user_count or user_count % 6 != 0:
        return 0

    chat_sql, chat_args = "", ()
    if chat_type:
        chat_sql = " AND (chat_type = ? OR chat_type = '')"
        chat_args = (chat_type,)
    if user_id > 0:
        rows = db.rows(
            "SELECT role, text, user_id FROM events WHERE (user_id = ? OR user_id = 0)" + chat_sql
            + " ORDER BY id DESC LIMIT 14",
            (user_id, *chat_args),
        )
    else:
        rows = db.rows(
            "SELECT role, text, user_id FROM events WHERE 1 = 1" + chat_sql + " ORDER BY id DESC LIMIT 14",
            chat_args,
        )
    names: dict[int, str] = {}
    nicks_by_openid: dict[str, str] = {}
    for r in db.rows("SELECT id, openid, nickname FROM qq_user"):
        # 名字优先级：QQ 昵称 > 用户自己设的称呼（kv nick:<id>）> 空
        nick = str(r["nickname"] or "").strip()
        if not nick:
            nick = str(db.kv_get(f"nick:{int(r['id'])}", "") or "").strip()
        # 绝不用 openid 当名字：官方通道拿不到昵称时会退化成 "49BEBF…明天有面试" 进长期记忆
        names[int(r["id"])] = nick
        nicks_by_openid[str(r["openid"] or "")] = nick
    self_id = str(getattr(rt.cfg.onebot, "self_id", "") or "")
    self_ids = {int(r["id"]) for r in db.rows("SELECT id, openid FROM qq_user")
                if self_id and str(r["openid"]) == self_id}

    def _label(r) -> str:
        uid = int(r["user_id"] or 0)
        if r["role"] == "her":
            return "你"
        if uid in self_ids:
            return ""
        return names.get(uid, "") or "对方"

    convo = "\n".join(
        f"{_label(r)}：{str(r['text'] or '')}" if _label(r) else str(r["text"] or "")
        for r in reversed(rows)
    )
    if not convo.strip():
        return 0

    try:
        from app.mind import models

        spec = models.pick("summary")
        r = await rt.llm.chat(_SYSTEM, [{"role": "user", "content": convo}],
                              max_tokens=160, model=spec.provider_model, thinking=False)
        models.record(spec.key, "summary", r.tokens_in, r.tokens_out, seq)
        raw = (r.text or "").strip()
        match = re.search(r"\[.*\]", raw, re.S)
        if not match:
            return 0
        items = _clean(json.loads(match.group(0)))
    except Exception:  # noqa: BLE001
        return 0

    from app.memory import h2

    added = 0
    for text in items:
        # 兜底脱敏：万一模型还是把 openid 写进事实，用它对应的名字替换掉
        for oid, name in nicks_by_openid.items():
            if oid and oid in text:
                text = text.replace(oid, name or "对方")
        text = str(text or "").strip()
        if not text:
            continue
        if h2.find_by_content(text, user_id=user_id, chat_type=chat_type):
            continue
        h2.add("fact", text, importance=0.62, user_id=user_id, chat_type=chat_type)
        added += 1
    return added
