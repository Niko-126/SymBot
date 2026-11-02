"""执行模块：让她**真的**去联系别人（不只是嘴上说"我去问问他"）。

为什么要它（2026-11-02 owner 提的）：
之前的"主动"只会对聊过天的人开口，且没有任何"按指令去联系某个第三方"的入口。
结果就是她能说出"我去跟阿叔说声晚安"，然后什么都没发生——这在真机上很假。

现在的分工：
- `parse_contact()` 从用户话里抠出「联系谁 + 说什么 + 用什么口气」；
- `resolve_target()` 把名字（或 QQ 号）落到 `qq_user` 里的真实账号；
- `deliver()` 真发一条私聊，成功才记账；
- `remember()` 把这次社交写进记忆（**这是她的社交履历，不是别人的**）。

三条铁律：
1. **没真发出去就不记账**——不然她会以为自己问候过；
2. 口气（直白/温柔/撒娇/客气…）要一起记，不然以后回忆起来只剩一句话；
3. 记忆写的是"我今天主动去问候了 X"，落 X 的分区，因为她对 X 的记忆该有这段。
﻿​‍​‌‍​﻿‌﻿‌﻿‌‌﻿‍﻿﻿‌​‍‍‍‍﻿‍‌﻿​‍​​​﻿"""

from __future__ import annotations

import re
from typing import Any

from app.core import db
from app.memory import store

#: "去跟X说…" / "联系一下X" / "给小X发…" 这类
_CONTACT_VERBS = (
    "去跟", "去和", "去对", "去问候", "去问问", "去问一下", "联系一下", "联系下",
    "帮我给", "帮我跟", "帮我联系", "给", "跟他说", "跟她说", "跟他讲", "跟她讲",
    "转告", "告诉他", "告诉她", "传达", "打个招呼", "说句话", "发条消息",
)
#: "给 / 向 / 跟 / 和 / 对 + 名字 + 发/说/问/讲"：中文里最常见的传话句式
_TO_ADDRESSEE = re.compile(
    r"(?:帮(?:我)?|替我)?(?:给|向|跟|和|对)\s*(?P<name>[^\s，。,、]{1,20}?)\s*"
    r"(?:发|说|问|讲|转告|传个话|带个话|带句话)"
)
#: 口气关键词 → 归一化标签
_TONES = (
    ("直白", ("直白", "直接说", "直说", "坦白", "别绕")),
    ("温柔", ("温柔", "软一点", "轻声", "体贴")),
    ("撒娇", ("撒娇", "黏", "可爱一点", "卖萌")),
    ("客气", ("客气", "正式", "礼貌", "正经")),
    ("冷淡", ("冷淡", "冷一点", "别太热情")),
)
_DEFAULT_TONE = "自然"


def _norm(text: str) -> str:
    return (text or "").strip()


def wants_contact(text: str) -> bool:
    body = _norm(text)
    if not body:
        return False
    if _TO_ADDRESSEE.search(body):
        return True
    return any(v in body for v in _CONTACT_VERBS)


def tone_of(text: str) -> str:
    """口气偏好只是一个"参考提示"，真正的句子交给模型；但**否定要先判**。

    "别太直白" 里有"直白"两个字，早先会被误判成"就要直白"（2026-11-02 真机踩到）。
    """
    body = _norm(text)
    if re.search(r"(别|不要|不用|少|不必)(太|这么|那么|过于)?直白", body):
        return "委婉"
    for label, words in _TONES:
        if any(w in body for w in words):
            return label
    return _DEFAULT_TONE


def parse_contact(text: str) -> dict[str, Any] | None:
    """把"去跟阿叔说声晚安，语气直白点"拆成 {target, content, tone, raw}。

    抠不出来就返回 None（让调用方按普通聊天处理）。
    """
    body = _norm(text)
    if not wants_contact(body):
        return None
    # 1) "给小煙发…" 这种「给/向/跟 + 名字 + 发/说」先抓，比找动词位置准
    m = _TO_ADDRESSEE.search(body)
    if m:
        target = m.group("name")
        rest = body[m.end():]
    else:
        # 2) 退回到旧做法：找动词位置，取后面那截
        pos = -1
        for verb in _CONTACT_VERBS:
            i = body.find(verb)
            if i >= 0 and (pos < 0 or i < pos):
                pos = i + len(verb)
        if pos < 0:
            return None
        rest = body[pos:].strip()
        target = ""

    # 3) 名字：优先 @某人 或「引号里的名字」或 QQ 号，否则取第一段（遇到 说/问/发/让/叫 截止）
    if not target:
        m = re.search(r"@\s*(\S+)", body)
        if m:
            target = m.group(1)
    if not target:
        m = re.search(r"[「『\"“《](.{1,20}?)[」』\"”》]", rest)
        if m:
            # 引号里如果是"要我传的话"而不是名字，就别当名字（下一步会当内容）
            cand = m.group(1)
            if not re.search(r"[，。！？,.!?]", cand):
                target = cand
    if not target:
        m = re.match(r"^([^\s，。,\.]{1,20}?)(?=说|问|发|让|叫|，|,|\s|$)", rest)
        if m:
            target = m.group(1)
    target = re.sub(r"^(一下|下|个|位)+", "", (target or "").strip()).strip(" 　，。！？、")
    if not target:
        return None

    # 3) 内容：动词后面的"说/问/发…"之后那截，或者名字之后剩下的
    content = ""
    tail = rest[len(target):] if rest.startswith(target) else rest
    m = re.search(r"(?:说|问|发|讲|道)(?:一句|一声|句|声)?[：:，,]?\s*(.+)$", tail)
    if m:
        content = m.group(1).strip()
    if not content:
        content = tail.strip(" 　：:，,说问发讲道").strip()
    # 剪掉"一句/一声/条消息"这类量词和"别太直白"这类语气尾巴，只留要传的话
    content = re.sub(r"^(一句|一声|条消息|条|句话|句|声|个|下)", "", content).strip()
    content = re.sub(r"(?:语气|口气|别太|不要太|不用太)[^，。,]*$", "", content).strip(" 　，。！？、")

    return {
        "target": target,
        "content": content[:120],
        "tone": tone_of(body),
        "raw": body[:200],
    }


def resolve_target(name: str) -> dict[str, Any] | None:
    """名字/QQ 号 → `qq_user` 里的真实账号。返回 {uid, openid, nickname}。

    匹配顺序：纯数字当 QQ 号 → 昵称精确 → 昵称包含 → 反查"他/她"叫不出来的情况返回 None。
    """
    key = _norm(name).lstrip("@")
    if not key:
        return None
    if key.isdigit():
        row = db.one("SELECT id, openid, nickname FROM qq_user WHERE openid = ? AND blocked = 0", (key,))
        if row:
            return {"uid": int(row["id"]), "openid": str(row["openid"]), "nickname": str(row["nickname"] or key)}
        # 表里还没有这个人：uid 用 0，openid 就是号码（发过去之后对方回话会自动建档）
        return {"uid": 0, "openid": key, "nickname": key}
    rows = db.rows("SELECT id, openid, nickname FROM qq_user WHERE blocked = 0")
    for row in rows:
        if str(row["nickname"] or "").strip() == key:
            return {"uid": int(row["id"]), "openid": str(row["openid"]), "nickname": str(row["nickname"])}
    for row in rows:  # 包含匹配（"阿叔" vs "阿叔（群里那个）"）
        nick = str(row["nickname"] or "")
        if key and (key in nick or nick in key):
            return {"uid": int(row["id"]), "openid": str(row["openid"]), "nickname": nick}
    return None


# ---------------------------------------------------------------- 让她自己想怎么说
#
# 2026-11-02 owner 定：**别用关键词硬判"直白/委婉"**，那套死板。
# 把"要不要直白、怎么开口"整个交给模型：给她对方的资料 + 关系 + 要他转达的要点，
# 让她自己决定口气和句子。这里只负责给约束（短、别客服腔、必须是私聊能直接发的话）。

_COMPOSE_SYSTEM = """你是「{name}」，一个有自己的脾气和说话习惯的人，不是客服。

现在你要**主动**给一个认识的人发一条私聊。不是在群里说话，是点开对话框那种。

你要判断的：
- 要不要直白。直接说事 vs 先绕一句再切入，你自己按关系和他平时的脾气定；
- 开口方式。是打招呼、是关心、是吐槽、是逗他；
- 长度。私聊一条就是一两条短句，别写作文、别分点、别用「首先/其次」。

硬约束：
- 只输出 JSON，形如 {{"tone": "直白|委婉|撒娇|客气|随便", "text": "要发出去的那句话"}}
- text 里不要出现动作描写、不要括号、不要引号包裹整句、不要 emoji 以外的格式
- 只有一句话到三句话，总长不超过 60 字
"""


def _relations_hint(rt: Any, target: dict[str, Any]) -> str:
    """把"我跟他是什么关系、多久没说话"塞进提示词，让她判断口气时有依据。"""
    bits: list[str] = []
    uid = int(target.get("uid") or 0)
    if uid > 0:
        try:
            from app.relation import engine

            rel = engine.relation_for(uid)
            bits.append(f"好感 {float(rel.get('affinity', 0)):.0f}")
            try:
                stage = rt.persona.stage_index(float(rel.get("affinity", 0)))
                bits.append(f"阶段 {stage}")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            last = db.one(
                "SELECT ts FROM events WHERE role = 'user' AND user_id = ? ORDER BY id DESC LIMIT 1",
                (uid,),
            )
            if last:
                bits.append(f"他上次说话在 {last['ts']}")
        except Exception:  # noqa: BLE001
            pass
    return "；".join(bits) if bits else "还不熟，没聊过几句"


async def compose(rt: Any, target: dict[str, Any], *, hint: str = "",
                  tone_hint: str = "") -> dict[str, Any]:
    """让模型决定"怎么问"：返回 {text, tone, reason, via}。模型挂了就退回兜底句。"""
    import json

    nick = str(target.get("nickname") or target.get("openid") or "他")
    persona_name = str(getattr(getattr(rt, "persona", None), "name", "") or "她")
    system = _COMPOSE_SYSTEM.format(name=persona_name)
    user_msg = (
        f"要发给：{nick}\n"
        f"你跟他：{_relations_hint(rt, target)}\n"
        f"这次要说的事：{hint or '没什么具体事，就是想跟他说句话'}\n"
        f"你的语气偏好（可参考，不必照做）：{tone_hint or '你自己看着办'}\n"
        "现在写那条要发出去的话。"
    )
    try:
        reply = await rt.llm.chat(
            system, [{"role": "user", "content": user_msg}],
            max_tokens=160, temperature=0.8,
        )
        raw = (getattr(reply, "text", "") or "").strip()
        data: dict[str, Any] = {}
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = {}
        text = str(data.get("text") or raw).strip() if data else raw
        text = text.replace("\n", " ").strip(' "“”「」')
        if text:
            return {
                "text": text[:120],
                "tone": str(data.get("tone") or tone_hint or "自然"),
                "reason": str(data.get("reason") or ""),
                "via": "llm",
            }
    except Exception as exc:  # noqa: BLE001 - 模型挂了也要能把话带到
        return {
            "text": (hint or f"{nick}，在吗。")[:120],
            "tone": tone_hint or "自然",
            "reason": f"模型不可用，退回要点原话：{exc}"[:160],
            "via": "fallback",
        }
    return {
        "text": (hint or f"{nick}，在吗。")[:120],
        "tone": tone_hint or "自然",
        "reason": "模型没给出可用句子，退回要点原话",
        "via": "fallback",
    }


def remember(target: dict[str, Any], lines: list[str], *, tone: str, ok: bool,
             reason: str = "") -> int:
    """把这次社交写进记忆（只写成功的那次）。

    记的东西要够以后回忆：对谁、什么口气、说了什么、为什么这么问。
    """
    if not ok or not lines:
        return 0
    from app.memory import h2

    said = " / ".join(str(x) for x in lines if str(x).strip())[:200]
    nick = str(target.get("nickname") or target.get("openid") or "他")
    content = f"我今天主动去问候了{nick}，用的是{tone}的说法：「{said}」"
    if reason:
        content += f"（这么说是想：{reason[:60]}）"
    try:
        return int(h2.add(
            "fact", content, importance=0.55, stability=0.6,
            cues=[nick, "主动", "问候", "社交", tone],
            user_id=int(target.get("uid") or 0), chat_type="private",
            extra_meta={"social": "contact", "tone": tone, "openid": target.get("openid"),
                        "reason": reason[:120]},
        ) or 0)
    except Exception:  # noqa: BLE001 - 记不下来不影响消息已经发出去
        return 0


def log_event(uid: int, lines: list[str], *, target: dict[str, Any], tone: str, ok: bool,
              reason: str = "") -> None:
    """留痕：她说了什么、对谁、什么口气、为什么这么问、成没成。"""
    for line in lines:
        store.add_event(
            "her", str(line), source="contact", kind="text",
            meta={
                "social": "contact",
                "to_uid": target.get("uid"),
                "to_openid": target.get("openid"),
                "to_nickname": target.get("nickname"),
                "tone": tone,
                "reason": reason[:120],
                "delivered": bool(ok),
            },
            user_id=int(target.get("uid") or 0), chat_type="private",
        )
