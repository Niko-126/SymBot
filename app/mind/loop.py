from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from app.core import db
from app.core.runtime import Runtime
from app.life import behavior, clock, diary
from app.memory import anchors as anchors_mod, facts
from app.memory import compress
from app.memory import evolve
from app.memory import store, h2
from app.mind import guard, models
from app.mind import prompt as prompt_mod
from app.relation import appraise, engine, judge
from app.relation import love
from app.relation import finale
from app.relation import session as session_mod


def _norm_chat_type(value: str) -> str:
    """只有两种场合：private / group。空 = 不区分（面板/兼容接口）。"""
    v = str(value or "").strip().lower()
    if v in ("group", "g", "群聊", "群"):
        return "group"
    if v in ("private", "c2c", "私聊", "friend", "p"):
        return "private"
    return ""


# 测试期应答模板（控制台「测试期应答」开关打开时只用这三句）﻿‍​‍﻿﻿‌‌‌​‍‍‍﻿​‍​‍﻿‍﻿‌​‌‌‌‌﻿​​‌‍‌﻿
TESTING_REPLIES = (
    "别吵我，我在思考",
    "我找东西呢等会再回来",
    "想我等会再陪你聊天",
)

#: 句尾是不是在问问题（问号，或"吗/呢/么/吧/没/不"收尾）
_QUESTION_TAIL = re.compile(r"([?？]|(吗|呢|么|吧|没|不)[。！!~…]*)\s*$")


def _ends_with_question(text: str) -> bool:
    return bool(_QUESTION_TAIL.search((text or "").strip()))


def _strip_question_tail(text: str) -> str:
    """把句子末尾连着的问题句删掉（至少留一句）；删空了就原样返回。"""
    parts = [p for p in re.split(r"(?<=[。！？!?])", (text or "").strip()) if p.strip()]
    while parts and _ends_with_question(parts[-1]):
        parts.pop()
    return "".join(parts).strip() or text


async def _limit_question_tail(rt: Any, reply_text: str, bundle: Any, spec: Any,
                               max_tokens: int | None) -> str:
    """问句收尾限流：最近两轮她都问了，这轮还问 → 改写一次，仍问就裁掉尾句。

    为什么要有：20 题实测 human_life 打开后 7–11/20 条以问句收尾，读起来像采访。
    """
    if not _ends_with_question(reply_text):
        return reply_text
    try:
        recent = [str(r["text"] or "") for r in db.rows(
            "SELECT text FROM events WHERE role = 'her' AND text <> '' ORDER BY id DESC LIMIT 2")]
    except Exception:  # noqa: BLE001 - 查不到历史就不限流
        return reply_text
    if len(recent) < 2 or not all(_ends_with_question(t) for t in recent):
        return reply_text
    try:
        hint = ("【重写要求】你已经连着两轮用问题收尾了。这一轮不要再问问题："
                "只接一句、给个反应，或说一件具体的事，2 到 3 句。")
        retry = await rt.llm.chat(bundle.system + "\n\n" + hint, bundle.messages,
                                  model=spec.provider_model, max_tokens=max_tokens)
        text = appraise.strip_actions(retry.text)
        if text and not _ends_with_question(text):
            return text
        if text:
            return _strip_question_tail(text)
    except Exception:  # noqa: BLE001 - 改写失败不拖垮回复
        pass
    return _strip_question_tail(reply_text)


_CHAT_HINTS = {
    "private": (
        "【场合】\n"
        "现在是私聊，一对一，没有第三个人在看。\n"
        "可以更放松一点：语气软、话可以长一点、可以撒娇和碎碎念，聊只有你们两个人知道的事。\n"
        "但私聊只是「更方便说话」，不是换一个人——分寸还是你自己的分寸。"
    ),
    "group": (
        "【场合】\n"
        "现在是群聊，旁边还有别人在看。\n"
        "保持克制：话短、别抢戏、别刷屏；私聊里说过的事不要当众抖出来；\n"
        "不评价群里其他人的长相、家境、隐私。被点到名才多说两句。"
    ),
}


def chat_hint(chat_type: str) -> str:
    """把"在哪儿说话"告诉模型。她本来就该分得清私聊和群聊。"""
    return _CHAT_HINTS.get(_norm_chat_type(chat_type), "")


def _is_self_openid(cfg, openid: str) -> bool:
    sid = str(getattr(cfg.onebot, "self_id", "") or "")
    return bool(sid and str(openid or "") == sid)


def _display_name(user_id: int) -> str:
    try:
        from app.relation import users

        row = users.get_user(int(user_id))
        if row:
            return str(row.get("nickname") or row.get("openid") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _impression_line(user_id: int, limit: int = 60) -> str:
    try:
        from app.memory import observe as obs_mod

        imp = obs_mod.get_impression(int(user_id))
        if imp and imp.get("summary"):
            return str(imp["summary"])[:limit]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _who_block(rt, user_id: int, chat_type: str) -> str:
    """这一轮跟她说话的是谁：名字、关系、好感、她记得的印象。"""
    uid = int(user_id or 0)
    if uid <= 0:
        return ""
    from app.relation import users

    row = users.get_user(uid)
    if row is None:
        return "【对面是谁】\n你还不认识这个人。不知道名字就先问，别乱叫称呼。"
    name = str(row.get("nickname") or row.get("openid") or "")
    openid = str(row.get("openid") or "")
    role = love.family_role(uid)
    lover = users.lover_of(uid)
    rel = engine.relation_for(uid)
    stage = rt.persona.stage_for(float(rel["affinity"]))
    title = role or ("你的恋人" if lover else "群里的朋友")
    lines = [
        "【对面是谁】",
        f"现在跟你说话的是「{name}」（QQ {openid}），{title}。",
        f"你们的好感 {float(rel['affinity']):.0f}，阶段「{stage.get('label', '')}」。",
    ]
    imp = _impression_line(uid, 80)
    if imp:
        lines.append(f"你记得的他：{imp}")
    if role == "生父":
        lines.append("他是你爹、你的生父：对他黏、撒娇、要夸奖，做错事会心虚；他的话你听。这是女儿对父亲。")
    elif role == "干爹":
        lines.append("他是你干爹，你爹认下的兄弟：客气里带亲，会问好；他说的话你也听，但先看你爹态度。不是恋人。")
    lines.append("名字和人对不上就直接问「你是谁来着」，不要猜、不要乱喊爹/宝宝。")
    return "\n".join(lines)


def _r18_on() -> bool:
    """R 级亲密插件（app/intimacy/r18/）是否接管本轮。

    未安装 / 未启用 / 包内异常 → False，主链完全不注入插件相关提示，
    行为与接入前一致。删掉那个目录也不会让这里报错。
    """
    try:
        from app.intimacy import r18

        return r18.enabled()
    except Exception:  # noqa: BLE001 - 插件层异常不许拖垮主链
        return False


def _group_roster(rt, current_uid: int) -> str:
    """群里都有谁：名字 + 关系 + 好感 + 一句印象。防止把 A 当成 B。"""
    from app.relation import users

    rows = db.rows("SELECT id, openid, nickname FROM qq_user ORDER BY last_seen DESC LIMIT 16")
    lines = ["【群里的人】（每条发言前面都带名字，认准了再回）"]
    n = 0
    for r in rows:
        rid = int(r["id"])
        if rid == int(current_uid or 0):
            continue
        if _is_self_openid(rt.cfg, str(r["openid"] or "")):
            continue
        role = love.family_role(rid)
        lover = users.lover_of(rid)
        rel = engine.relation_for(rid)
        stage = rt.persona.stage_for(float(rel["affinity"]))
        tag = role or ("恋人" if lover else "群友")
        line = (
            f"- {r['nickname'] or r['openid']}（QQ {r['openid']}，{tag}）："
            f"好感 {float(rel['affinity']):.0f}，{stage.get('label', '')}"
        )
        imp = _impression_line(rid, 36)
        if imp:
            line += f"；你记得的他：{imp}"
        lines.append(line)
        n += 1
        if n >= 12:
            break
    if n == 0:
        return ""
    lines.append("规则：谁说的就记谁的名字；对不上就问，不要把 A 当成 B。家人不谈恋爱。")
    return "\n".join(lines)


def _group_speakers() -> dict[int, str]:
    out: dict[int, str] = {}
    for r in db.rows("SELECT id, nickname, openid FROM qq_user"):
        out[int(r["id"])] = str(r["nickname"] or r["openid"] or "")
    return out


async def chat_turn(rt: Runtime, text: str, source: str = "panel",
                    user_id: int = 0, chat_type: str = "",
                    group_id: str = "",
                    intimate_mode: str = "", intimate_note: str = "",
                    is_lover: bool = False,
                    prefetched_hint: str = "", slow: bool = False,
                    extra_hint: str = "",
                    attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {"reply": ["……"], "state": {}, "meta": {}}

    started = time.perf_counter()
    persona = rt.persona
    chat_type = _norm_chat_type(chat_type)
    group_id = str(group_id or "")
    uid = int(user_id or 0)

    # 被拉黑的人：只影响他自己，不影响别人（厌恶隔离）
    if uid > 0:
        from app.relation import users as users_mod

        if users_mod.is_blocked(uid):
            rel = engine.relation_for(uid)
            sp = engine.stage_payload(persona, float(rel["affinity"]))
            # 他确实说了话：库里必须留痕。以前这里直接 return，
            # 拉黑期的对话在 events 里是空白（docs/23 §3.2 第 4 条）。
            store.add_event("user", text, source=source, user_id=uid,
                            chat_type=chat_type, group_id=group_id)
            return {
                "reply": ["我不想跟你说话了。", "……别再找我了。"],
                "state": {
                    **rel,
                    "stage": sp.get("stage"),
                    "stage_label": sp.get("stage_label"),
                    "call_user": sp.get("call_user"),
                    "next_stage": sp.get("next_stage"),
                    "aversion_phase": "cold",
                    "session": session_mod.session_payload(persona),
                },
                "meta": {"blocked": True, "mode": "blocked",
                         "delays_ms": [700, 900], "user_id": uid},
            }

    # 0. 当前身份：她的名字可能已经被用户起过
    persona.raw["name"] = session_mod.her_name(rt.default_name)
    # 0.1 工具环节：他发了图 / 说"查一下" / 贴了链接 → 先动手，再回话。
    #     群聊默认不调（避免在群里烧搜索与视觉调用）；tools.group_enabled=true 时才算。
    #     mock 模式下 registry.gate() 会直接挡掉，一个字节都不出网。
    _tool_hint = ""
    if chat_type != "group" or bool(getattr(rt.cfg.tools, "group_enabled", False)):
        try:
            from app.tools import router as tool_router

            _tool_hint = await tool_router.hint(rt, text, attachments)
        except Exception:  # noqa: BLE001 - 工具失败当没查到
            _tool_hint = ""
    # 亲密模式：事件上留标记，事后能查出"这条是在什么模式下说的"
    _in_intimate = bool(intimate_mode and intimate_mode != "off")
    # 不懂的东西：先"我想一想"，去查，再回来多想一会儿（slow）
    _forced_deep = bool(_in_intimate or slow)

    # 测试期应答：只回固定三句，不走模型，也不写用户上下文（控制台开关，默认关）
    if db.kv_get("testing_reply", "0") == "1":
        import random

        line = random.choice(TESTING_REPLIES)
        store.add_event("her", line, source=source, user_id=uid, chat_type=chat_type,
                        group_id=group_id, meta={"testing": True})
        rel0 = engine.relation_for(uid)
        stage0 = persona.stage_for(float(rel0["affinity"]))
        return {
            "reply": [line],
            "state": {
                "affinity": float(rel0["affinity"]),
                "stage": stage0.get("key"),
                "stage_label": stage0.get("label"),
                "aversion_phase": "calm",
                "session": session_mod.session_payload(persona),
            },
            "meta": {"mode": "testing", "testing": True, "user_id": uid,
                     "delays_ms": [700 + len(line) * 30]},
        }

    store.add_event("user", text, source=source, user_id=int(user_id or 0),
                    chat_type=chat_type, group_id=group_id,
                    meta={"intimate": intimate_mode} if _in_intimate else None)

    # 0.3 免打扰：他说"在开会/别吵我/要睡了"就进免打扰；说"忙完了/在吗"就解除。
    # 这只挡**她主动找他**，不挡他说话时的正常回复（他开口了当然要接）。
    try:
        from app.life import proactive as proactive_mod

        _dnd = proactive_mod.detect_dnd(text)
        if _dnd is not None:
            proactive_mod.apply_dnd(uid, _dnd)
    except Exception:  # noqa: BLE001 - 免打扰判断失败不许影响聊天
        pass

    # 0.5 命名流程：还没被起名时，先用剧本走完，不走模型
    scripted = _naming_turn(rt, text, source, user_id=int(user_id or 0),
                              chat_type=chat_type, group_id=group_id)
    if scripted is not None:
        return scripted

    # 0.8 恋爱事件：表白 / 分手 / 答应她的主动提议（QQ 群成员，确定性回复，不走模型）
    love_result = love.try_turn(rt, persona, text, int(user_id or 0))
    if love_result is not None:
        # 恋爱线是"确定性回复"（不走模型）。她自己说的这句必须落库：
        # 否则下一轮上下文里没有"她刚拒绝过/刚答应过"，会自相矛盾。
        # 真机实测（QQ 私聊表白）：用户那边确实收到「我们还不到那一步」，
        # 但 events 表里没有这条 her，下一轮模型反问"前面那句我先搁着"。
        _store_her_script(love_result, source, user_id, chat_type, group_id)
        return love_result

    # 1. 此刻
    life = clock.get_state(persona)
    # 1.05 她今天的生活（human_life 语料接管；开关关着/没语料就是空串）
    today_hint = ""
    try:
        from app.life import human_life as human_life_mod

        today_hint = human_life_mod.today_line()
    except Exception:  # noqa: BLE001 - 没有语料不许影响聊天
        today_hint = ""
    first_contact = store.count_events() <= 2

    # 1.15 夜里独处：她可以自己想点事（后台跑，不拖慢这一轮）
    try:
        from app.life import thoughts as thought_mod

        if thought_mod.should_try():
            import asyncio as _asyncio

            _asyncio.create_task(thought_mod.maybe_ruminate(rt, persona, int(user_id or 0)))
    except Exception:  # noqa: BLE001 - 夜里想事失败不许影响聊天
        pass

    # 1.2 新的一天：补写昨天的日记（写失败不影响这一轮聊天）
    try:
        await diary.ensure(rt, persona)
    except Exception:  # noqa: BLE001 - 日记是锦上添花，不能拖垮主流程
        pass

    # 1.3 群聊进化：每日观察/思考报告 + 每自然周记忆自评（失败不影响聊天）
    try:
        await evolve.evolve_tick(rt, persona)
    except Exception:  # noqa: BLE001 - 进化生成不能影响对话
        pass

    # 1.5 她的状态决定怎么回（"叫了几次"只看这个人，不数全群）
    recent_user = _recent_user_turns(10, uid, chat_type)
    reaction = behavior.decide(life, recent_user, persona, text=text)
    if first_contact and reaction.delay_ms == 0:
        reaction.delay_ms = 1800  # 初次见面：慢半拍，像在想怎么开口
    if not reaction.will_reply:
        store.add_event("her", "", source="system")
        _rel0 = engine.relation_for(uid)
        return {
            "reply": [],
            "state": {
                "affinity": float(_rel0["affinity"]),
                "stage": persona.stage_for(float(_rel0["affinity"])).get("key"),
                "stage_label": persona.stage_for(float(_rel0["affinity"])).get("label"),
                "session": session_mod.session_payload(persona),
            },
            "meta": {"mode": reaction.mode, "hint": reaction.hint, "delays_ms": []},
        }

    # 2. 关系与记忆（QQ 用户读他自己的关系，面板读全局）
    relation = engine.relation_for(uid)
    recall = h2.retrieve(text, user_id=uid, chat_type=chat_type)
    memories = recall.hot
    # 2.1 陪伴层（衡忆 companion-emotion）：情绪够浓才动，决定"要不要把旧事端上来"
    companion_hint = ""
    companion_state: dict[str, Any] = {}
    try:
        from app.memory import companion as companion_mod

        companion_hint = companion_mod.hint_for(text, user_id=uid, chat_type=chat_type)
        companion_state = companion_mod.last_turn(uid)
    except Exception:  # noqa: BLE001 - 陪伴层是加料，不许拖垮聊天
        companion_hint = ""
        companion_state = {}
    # 2.2 夜里想的事：第二天找一个气氛合适的时机顺口提一次（提过就冷却）
    surface_item = None
    surface_hint = ""
    if not companion_hint:
        try:
            from app.life import thoughts as thought_mod

            surface_item = thought_mod.pick_surface(uid, text=text)
            surface_hint = thought_mod.surface_hint(surface_item)
        except Exception:  # noqa: BLE001
            surface_item = None
            surface_hint = ""
    keep = int(rt.cfg.context.keep_recent)
    covered = compress.covered_to()
    recent = store.events_between(
        covered, 10**12, chat_type=chat_type,
        user_id=uid if chat_type == "private" else 0,
    )
    # 全局摘要会把群里/别人的事带进私聊；QQ 私聊不用它（只靠本人原文段与记忆）。
    # 面板（chat_type 空 = 不分场合）保留摘要——别为了语气层把面板的上下文收窄了（audit N2）。
    summary_text = (
        "" if chat_type == "group" or (chat_type == "private" and uid > 0)
        else (compress.latest_summary() or {}).get("text", "") or ""
    )
    degraded = bool(db.kv_get("memory_degraded", ""))

    pinned_id = 0
    if h2.wants_pin(text):
        pinned_id = h2.add("fact", text.strip(), importance=1.0, pin=True,
                           user_id=int(user_id or 0), chat_type=chat_type)

    # 3. 情绪温度：厌恶高的时候，生气支线可以被调用（按这个人自己的厌恶）
    aversion = float(relation.get("aversion") or 0.0)
    phase = session_mod.aversion_phase(aversion)
    stage_idx = persona.stage_index(float(relation["affinity"]))
    anchor_stage = max(stage_idx, 2) if phase in ("irritated", "cold") else stage_idx
    scene = "生气" if phase in ("irritated", "cold") else anchors_mod.scene_for_text(
        text, int(life.get("mood", 0))
    )
    anchor_lines = anchors_mod.pick_fixed(anchor_stage, 10) + anchors_mod.pick_dynamic(
        scene, anchor_stage, 6
    )

    # 3.1 语料风格：恋人私聊给 1–2 句"以前的语气"；跟不熟的人/群里给"跟外人打交道"规则。
    # 只抽短样本，不把私聊原文成段塞进去；没有语料/读失败就当没有。
    partner_hint = ""
    stranger_hint = ""
    try:
        from app.memory import style as style_mod

        # 恋人：QQ 私聊 + 面板（chat_type 空）都给语气样本；群聊绝不给
        if is_lover and chat_type in ("private", ""):
            partner_hint = "\n".join(style_mod.partner_examples(2)) or ""
        # 外人规则：群聊照给；私聊只给"还不熟 + 不是恋人"的人（面板恋人线不要防自己人）
        if chat_type == "group" or (not is_lover and stage_idx <= 1):
            stranger_hint = "\n".join(
                x for x in (style_mod.stranger_rules(), style_mod.service_note()) if x
            )
    except Exception:  # noqa: BLE001 - 风格样本是加料，不许拖垮主链
        partner_hint = ""
        stranger_hint = ""

    # 4. 组装
    diary_hint = diary.prompt_line()
    # 家人（生父/干爹）不进入恋爱提议
    _is_family = False
    try:
        from app.relation import love as _love_mod

        _is_family = bool(_love_mod.family_role(int(user_id or 0)))
    except Exception:  # noqa: BLE001
        _is_family = False
    proposal_hint = "" if _is_family else love.maybe_set_pending(persona, int(user_id or 0))
    # 热梗：只是"最近流行怎么说"的可选参考，不进记忆、不参与召回
    gag_hint = ""
    try:
        from app.web import gags as gags_mod

        gag_hint = gags_mod.prompt_hint(6)
    except Exception:  # noqa: BLE001
        gag_hint = ""
    # 家人关系：告诉模型"对面是谁"，她才知道怎么称呼、用什么语气
    family_hint = ""
    try:
        from app.relation import love as love_mod

        role = love_mod.family_role(int(user_id or 0))
        if role == "生父":
            family_hint = (
                "【对面是谁】\n"
                "他是你爹，你的生父，也是把你造出来的人。对他你会黏、会撒娇要夸奖、"
                "做错事会心虚认错；他的话你听。但这是女儿对父亲，不是别的。"
            )
        elif role == "干爹":
            family_hint = (
                "【对面是谁】\n"
                "他是你干爹，你爹认下的兄弟。对他客气里带亲，会喊人会问好；"
                "他说的话你也听，但会先看你爹的态度。不是别的。"
            )
    except Exception:  # noqa: BLE001
        family_hint = ""
    merged_hint = "\n".join(
        x for x in (chat_hint(chat_type), reaction.prompt_hint, proposal_hint,
                    family_hint, gag_hint, prefetched_hint, extra_hint)
        if x
    )
    # 认人：这一轮是谁、群里还有谁；群聊消息在 prompt 里带名字
    if uid > 0:
        _who = _who_block(rt, uid, chat_type)
        if _who:
            merged_hint = "\n".join(x for x in (merged_hint, _who) if x)
    if chat_type == "group":
        _roster = _group_roster(rt, uid)
        if _roster:
            merged_hint = "\n".join(x for x in (merged_hint, _roster) if x)
    speakers = _group_speakers() if chat_type == "group" else {}

    # 活人感：没说完的话题 + 关系仪式（晚安/早安/认识第几天）
    try:
        from app.memory import threads as thread_mod
        from app.relation import rituals

        for _extra in (thread_mod.prompt_hint(uid, chat_type),
                       rituals.hint(uid, int(life.get("hour", 12)), chat_type)):
            if _extra:
                merged_hint = "\n".join(x for x in (merged_hint, _extra) if x)
    except Exception:  # noqa: BLE001
        pass

    # 恋人私聊：她可以更主动、更黏，并主动抛一句暗示引对方接
    if is_lover and chat_type == "private" and intimate_mode != "explicit":
        merged_hint = "\n".join(x for x in (
            merged_hint,
            "【你们是恋人】你可以更主动一点：先说想他/要抱，再自然抛一句带暗示的俏皮话（不超过一句），引他接。"
            "也可以问他喜欢什么、不喜欢什么；他接住就顺着往下，不接就自然转开，别像在背流程。"
            "困的时候也可以先黏一句再睡，不要一被撩就只赶他睡。",
        ) if x)

    # 引导模式：从暗示 → 明示 → 露骨 → 收尾睡觉
    try:
        from app.intimacy import guide as guide_mod

        if guide_mod.active(uid):
            guide_mod.bump(uid, text)
            _guide_hint = guide_mod.hint(uid, is_lover, chat_type)
            if _guide_hint:
                merged_hint = "\n".join(x for x in (merged_hint, _guide_hint) if x)
    except Exception:  # noqa: BLE001
        pass
    # 亲密模式：根据模式注入不同说话方式（私聊大胆/群聊暗示/非恋人调侃拒绝）
    if intimate_mode and intimate_mode != "off":
        try:
            from app.intimacy import prompt as iprompt_mod
            from app.intimacy.mode import Mode

            try:
                i_mode = Mode(intimate_mode)
            except ValueError:
                i_mode = Mode.OFF
            if i_mode != Mode.OFF:
                i_prompt = iprompt_mod.build_system_prompt(
                    i_mode, is_private=(chat_type == "private"),
                    is_lover=is_lover, persona_name=persona.name,
                )
                if i_prompt:
                    merged_hint = "\n".join(x for x in (merged_hint, i_prompt) if x)
                # 偏好档案：从历史私密记录里学到"他喜欢/讨厌/边界"
                try:
                    from app.intimacy import analyze as ianalyze

                    _profile = ianalyze.profile_hint(int(user_id or 0))
                    if _profile:
                        merged_hint = "\n".join(x for x in (merged_hint, _profile) if x)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 - 亲密模式注入失败不影响聊天
            pass
    # R 级插件：L0-L5 阶梯 + 上一轮缺画面时的感官补写（插件未启用时整块跳过）
    if _in_intimate and _r18_on():
        try:
            from app.intimacy import guide as _guide_mod

            if not _guide_mod.active(uid):
                from app.intimacy.r18 import stage as istage
                from app.intimacy.r18 import style as istyle_hint

                _stage_hint = istage.hint(uid, chat_type=chat_type, is_lover=is_lover)
                if _stage_hint:
                    merged_hint = "\n".join(x for x in (merged_hint, _stage_hint) if x)
                _repair = istyle_hint.repair_hint(uid)
                if _repair:
                    merged_hint = "\n".join(x for x in (merged_hint, _repair) if x)
        except Exception:  # noqa: BLE001 - 阶梯注入失败不影响聊天
            pass
    # 非恋人 + 性意图 → 调侃拒绝（模式可能仍是 OFF，这句必须照样注入）
    if intimate_note == "not_lover_deflect":
        merged_hint += (
            "\n他表达了对你的性想法，但你们还不是恋人。别把整段对话拒掉——"
            "用你自己的话、带点撒娇地把话题轻轻推开：可以逗他一句，"
            "说你要的是先在一起、再放开这些，然后把话带回日常。"
            "\n规则：不要照搬任何现成台词，每次换一种说法；不要冷冰冰地说教。"
        )
    # 喊停：软着陆，别让他觉得被责怪
    if intimate_note == "stop_soft":
        merged_hint += (
            "\n他喊停了。你就自然收住，不要怪他、不要说“是你先带的”，也不要显得被冒犯。"
            "可以软一句「好，那正常聊，抱一下」再转回日常，别把气氛弄僵。"
        )
    # R 级插件：安全阀语气（红灯/黄灯/家人/未成年/群聊上限）+ 事后照护注入
    if _r18_on():
        try:
            from app.intimacy.r18 import safety as isafety

            if intimate_note in ("red_light", "stop_soft"):
                merged_hint += isafety.stop_hint()
            elif intimate_note == "yellow_light":
                merged_hint += isafety.yellow_hint()
            elif intimate_note == "minor_guard":
                merged_hint += isafety.minor_hint()
            elif intimate_note == "family_guard":
                merged_hint += isafety.family_hint_hard()
            if chat_type == "group" and _in_intimate:
                merged_hint += isafety.group_cap_hint(
                    int(getattr(isafety.cfg(), "group_max_stage", 2) or 2)
                )
            # 照护只在私聊 + 有真实 uid 时挂（面板 uid=0 不享受，母体原样）
            if chat_type == "private" and int(user_id or 0) > 0:
                from app.intimacy.r18 import aftercare as iafter

                _after_reason = iafter.take(int(user_id or 0))
                if _after_reason:
                    merged_hint += iafter.hint(_after_reason)
        except Exception:  # noqa: BLE001 - 安全阀注入失败不影响聊天
            pass
    # 联网：遇到不懂的词先去查（查不到就说不知道，不编）
    # 已经预查过（prefetched_hint）就不再查第二遍
    if not prefetched_hint:
        try:
            from app.web import lookup as lookup_mod

            web_hint = lookup_mod.auto_hint(text)
            if web_hint:
                merged_hint = "\n".join(x for x in (merged_hint, web_hint) if x)
        except Exception:  # noqa: BLE001 - 联网失败不影响聊天
            pass
    else:
        merged_hint += (
            "\n这句话你刚才想了一下、查过了。现在按关系、分数和此刻情景回答，"
            "说得像自己了解到的，别提搜索/资料。"
        )
    # 梗识别：句子里直接用了网络用语（不是问句）→ 本地词表给一层「你本来就懂」的底
    meme_hint_used = ""
    try:
        from app.web import lookup as lookup_mod

        meme_hint_used = lookup_mod.inline_hint(text)
        if meme_hint_used:
            merged_hint = "\n".join(x for x in (merged_hint, meme_hint_used) if x)
    except Exception:  # noqa: BLE001 - 词表是加料，不许拖垮主链
        meme_hint_used = ""
    # 陪伴层「忽然想起」与夜里念头「夜里想的」不再塞进【此刻的状态】，
    # 由 prompt.build 显式成两段（docs/55）；state_hint 只装其它提示。
    # R 级插件：L3 以上接管【格式】——基础 prompt 的「短句、不加括号动作」会把描写压成十几个字
    if _in_intimate and _r18_on():
        try:
            from app.intimacy.r18 import stage as _istage_fmt

            _fmt = _istage_fmt.format_override(_istage_fmt.get(uid, chat_type))
            if _fmt:
                merged_hint = "\n".join(x for x in (merged_hint, _fmt) if x)
        except Exception:  # noqa: BLE001 - 格式覆盖失败不影响聊天
            pass
    bundle = prompt_mod.build(
        rt.cfg,
        persona,
        relation=relation,
        life=life,
        memories=memories,
        recent_events=recent,
        user_text=text,
        summary=summary_text,
        degraded=degraded,
        recalled=recall.recalled,
        recall_status=recall.status,
        anchors=anchor_lines or None,
        aversion_phase=phase,
        state_hint=merged_hint,
        companion_hint=companion_hint,
        thought_hint=surface_hint,
        today_hint=today_hint,
        partner_hint=partner_hint,
        stranger_hint=stranger_hint,
        tool_hint=_tool_hint,
        first_contact=first_contact,
        avoid_words=_avoid_words(),
        # 亲密模式强制深思：不依赖全局开关，防出戏（用户要求）
        # slow（查过资料再答）同样强制深思
        deep_think=bool(models.current_settings().get("deep_think", False)) or _forced_deep,
        diary=diary_hint,
        chat_type=chat_type,
        speakers=speakers,
    )

    # 5. 挑模型 + 调用
    task = models.task_for(text, phase, scene)
    spec = models.pick(task, phase, force_think=_forced_deep)
    # 输出上限：直接用 provider 配的模型上限（V4.1 = 384K），不再被 900 截断
    _out_tokens = int(rt.cfg.provider.max_tokens or 0) or None
    # 模型挂了先重试一次（抖动/限流），仍失败才用本地兜底；降级必须留痕（health + 日志）
    _temperature = 0.95 if chat_type == "private" else 0.8
    try:
        model_reply = await rt.llm.chat(bundle.system, bundle.messages,
                                        model=spec.provider_model, max_tokens=_out_tokens,
                                        temperature=_temperature)
    except Exception:  # noqa: BLE001 - API 限额/超时/断网都走这里
        try:
            await asyncio.sleep(0.8)
            model_reply = await rt.llm.chat(bundle.system, bundle.messages,
                                            model=spec.provider_model, max_tokens=_out_tokens,
                                            temperature=_temperature)
        except Exception as exc:  # noqa: BLE001 - 兜底
            _reason = f"{type(exc).__name__}: {exc}"[:200]
            try:
                db.kv_set("llm_offline_last", time.strftime("%Y-%m-%dT%H:%M:%S"))
                db.kv_set("llm_offline_reason", _reason)
                db.kv_set("llm_offline_count",
                          str(int(db.kv_get("llm_offline_count", "0") or 0) + 1))
            except Exception:  # noqa: BLE001 - 记账失败不影响回复
                pass
            import logging

            logging.getLogger("symemory").warning("LLM 调用失败，降级到本地兜底：%s", _reason)
            fallback = _offline_reply(persona, rt)
            store.add_event("her", fallback, source=source, user_id=int(user_id or 0),
                            meta={"offline": True, "model": spec.key, "reason": _reason},
                            chat_type=chat_type, group_id=group_id)
            _rel1 = engine.relation_for(uid)
            return {
                "reply": [fallback],
                "state": {
                    "affinity": float(_rel1["affinity"]),
                    "stage": persona.stage_for(float(_rel1["affinity"])).get("key"),
                    "stage_label": persona.stage_for(float(_rel1["affinity"])).get("label"),
                    "session": session_mod.session_payload(persona),
                },
                "meta": {"mode": "offline", "hint": "", "delays_ms": [900 + len(fallback) * 40],
                         "offline": True, "offline_reason": _reason},
            }
    reply_text = appraise.strip_actions(model_reply.text) or "……"

    _hour = int(life.get("hour", -1) or -1)
    guard_hit = guard.check(reply_text)
    time_hit = None if guard_hit else guard.time_check(reply_text, _hour)
    if guard_hit or time_hit:
        try:
            _hint = guard.strict_hint(guard_hit) if guard_hit else guard.time_hint(_hour)
            retry = await rt.llm.chat(
                bundle.system + "\n\n【重写要求】\n" + _hint,
                bundle.messages,
                model=spec.provider_model,
                max_tokens=_out_tokens,
            )
            retry_text = appraise.strip_actions(retry.text)
        except Exception:  # noqa: BLE001
            retry_text = ""
        _retry_ok = bool(retry_text) and not guard.check(retry_text) and not guard.time_check(retry_text, _hour)
        if _retry_ok:
            reply_text = retry_text
        elif guard_hit:
            reply_text = guard.deflect(persona.all_quotes())
        # 纯时间冲突且重写失败：保留原句（硬挡反而更假）

    # 问句收尾限流：最近两轮都问了 → 这轮改写一次，仍问就裁掉尾句
    reply_text = await _limit_question_tail(rt, reply_text, bundle, spec, _out_tokens)
    # 清掉"你说了两遍"这类幻觉（用户只发了一次）
    reply_text = scrub_repeat_claim(reply_text) or reply_text
    # 亲密护栏：群聊漏词换谐音 + 句数硬上限（直白≤2句、群聊暗示≤1句）
    if _in_intimate:
        try:
            from app.intimacy import prompt as iprompt_mod

            if chat_type == "group":
                reply_text = iprompt_mod.sanitize_group_reply(reply_text)
            reply_text = iprompt_mod.limit_intimate_sentences(
                reply_text, intimate_mode, chat_type == "private"
            )
        except Exception:  # noqa: BLE001 - 护栏失败不影响回复
            pass
    segments = prompt_mod.segment_reply(reply_text)

    if getattr(rt.llm, "provider", "") == "mock" and recall.status in ("uncertain", "miss"):
        line = _recall_line(persona, recall.status)
        if line:
            segments = [line] + segments

    think_info = {
        "thinking_ms": model_reply.elapsed_ms,
        "reasoning": (model_reply.reasoning or "")[:6000],
        "reasoning_tokens": model_reply.reasoning_tokens,
        "model": spec.key,
        "task": task,
        "provider": model_reply.provider,
        "tokens_in": model_reply.tokens_in,
        "tokens_out": model_reply.tokens_out,
        # 这条回复是在什么亲密模式下生成、是否被强制深思（可审计）
        "intimate": intimate_mode if _in_intimate else "",
        "forced_think": _forced_deep,
        "slow": bool(slow),
        "lookup_prefetched": bool(prefetched_hint),
    }
    her_event_id = store.add_event("her", reply_text, source=source, meta=think_info,
                                   user_id=int(user_id or 0),
                                   chat_type=chat_type, group_id=group_id)

    # 记下她答应“以后再说”的事；她真说了晚安/早安才记仪式
    try:
        from app.intimacy import guide as guide_mod
        from app.memory import threads as thread_mod
        from app.relation import rituals

        thread_mod.capture(uid, chat_type, reply_text)
        rituals.confirm(uid, reply_text)
        if guide_mod.active(uid) and guide_mod.stage(uid) >= 3:
            guide_mod.finish(uid)
    except Exception:  # noqa: BLE001
        pass

    # 6. 评估 → 结算（好感 + 厌恶）
    appr = appraise.appraise(text, hour=int(life.get("hour", 12)))
    if models.current_settings().get("llm_appraisal", True):
        llm_appr = await judge.judge(
            rt, text, reply_text, relation, aversion,
            int(session_mod.get(rt.default_name).get("seq") or 1),
        )
        if llm_appr:
            appr = {**appr, "llm": llm_appr}
    settlement = engine.apply_appraisal(appr, persona, trigger="chat", user_id=int(user_id) or None)
    try:
        import json as _json

        merged = dict(think_info)
        merged["delta"] = {
            "affinity": settlement.get("d_affinity", 0),
            "trust": settlement.get("d_trust", 0),
            "attraction": settlement.get("d_attraction", 0),
            "aversion": settlement.get("d_aversion", 0),
            "reason": (appr.get("llm") or {}).get("reason") or appr.get("reason", ""),
        }
        db.execute("UPDATE events SET meta = ? WHERE id = ?",
                   (_json.dumps(merged, ensure_ascii=False), her_event_id))
    except Exception:  # noqa: BLE001
        pass
    aversion = float(settlement.get("aversion", aversion))
    phase = str(settlement.get("aversion_phase") or session_mod.aversion_phase(aversion))

    # 7. 厌恶到顶：QQ 用户只拉黑他一个人；单用户面板才走"她离开"
    # 注意：本轮负面微调可能把 100 拉回 99.x，所以用"本轮开始时也到顶"兜底
    _aversion_peak = max(aversion, float(relation.get("aversion") or 0.0))
    if _aversion_peak >= 100:
        if uid > 0:
            from app.relation import users as users_mod

            users_mod.block_user(uid, "aversion")
            blocked_result = {
                "reply": ["我不想跟你说话了。", "……别再找我了。"],
                "state": {
                    "affinity": float(relation["affinity"]),
                    "stage": persona.stage_for(float(relation["affinity"])).get("key"),
                    "stage_label": persona.stage_for(float(relation["affinity"])).get("label"),
                    "aversion": 100.0,
                    "aversion_phase": "cold",
                    "session": session_mod.session_payload(persona),
                },
                "meta": {"blocked": True, "mode": "blocked", "user_id": uid,
                         "delays_ms": [700, 900]},
            }
            _store_her_script(blocked_result, source, user_id, chat_type, group_id)
            return blocked_result
        return _end_session(rt, persona, source=source)

    if surface_item is not None:
        try:
            from app.life import thoughts as thought_mod

            thought_mod.mark_surface(int(surface_item["id"]), int(user_id or 0), chat_type)
        except Exception:  # noqa: BLE001 - 登记失败最多下次再说一次
            pass
    db.kv_set("last_used_tokens", str(bundle.used_tokens))
    if bundle.dropped_events > 0 and not degraded:
        db.kv_set("memory_degraded", store.now_iso())
        degraded = True

    # 7.5 工作记忆：超触发线就折成线索（规则版很快，不会拖慢）
    compacted = None
    try:
        if compress.should_compact(bundle.used_tokens, rt.cfg, len(recent)):
            compacted = await compress.compact(rt, persona, relation)
    except Exception:  # noqa: BLE001
        compacted = None

    try:
        models.record(spec.key, task, model_reply.tokens_in, model_reply.tokens_out,
                      int(session_mod.get(rt.default_name).get("seq") or 1))
    except Exception:  # noqa: BLE001
        pass

    # 顺手抽事实（不阻塞回复）
    try:
        seq_now = int(session_mod.get(rt.default_name).get("seq") or 1)
        asyncio.create_task(
            facts.maybe_extract(rt, seq_now, user_id=int(user_id or 0), chat_type=chat_type)
        )
    except Exception:  # noqa: BLE001
        pass

    elapsed = int((time.perf_counter() - started) * 1000)
    meta = {
        "elapsed_ms": elapsed,
        "prompt_tokens_est": bundle.used_tokens,
        "context_usage": bundle.context_ratio,
        "memories_used": [
            {"id": m["id"], "kind": m["kind"], "content": m["content"]} for m in memories[:6]
        ],
        "provider": model_reply.provider,
        "model": spec.key,
        "task": task,
        "tokens_in": model_reply.tokens_in,
        "tokens_out": model_reply.tokens_out,
        "appraisal": appr,
        "delta": {
            "affinity": settlement.get("d_affinity"),
            "trust": settlement.get("d_trust"),
            "attraction": settlement.get("d_attraction"),
            "aversion": settlement.get("d_aversion"),
            "reason": (appr.get("llm") or {}).get("reason") or appr.get("reason", ""),
            "stage": settlement.get("stage"),
            "stage_label": settlement.get("stage_label"),
        },
        "delays_ms": behavior.apply_delay(segments, reaction),
        "mode": reaction.mode,
        "hint": reaction.hint,
        "thinking_ms": model_reply.elapsed_ms,
        "reasoning": (model_reply.reasoning or "")[:6000],
        "reasoning_tokens": model_reply.reasoning_tokens,
        "slow": bool(slow),
        "lookup_prefetched": bool(prefetched_hint),
        "meme_hint": meme_hint_used[:400],
        "dropped_events": bundle.dropped_events,
        "guard": guard_hit,
        "recall": recall.as_dict(),
        "companion": companion_state,
        "thought": int(surface_item["id"]) if surface_item else None,
        "pinned": pinned_id,
        "anchor_scene": scene,
        "anchors_used": len(anchor_lines),
        "aversion": round(aversion, 1),
        "aversion_phase": phase,
        "context_tokens": bundle.used_tokens,
        "compacted": bool(compacted and compacted.get("ok")),
        "diary": bool(diary_hint),
        "user_id": int(user_id or 0),
        "session": session_mod.session_payload(persona),
    }

    rt.last_prompt = {
        "system": bundle.system,
        "messages": bundle.messages,
        "budget": {
            "window": rt.cfg.context.window,
            "used_est": bundle.used_tokens,
            "ratio": bundle.context_ratio,
            "sections": bundle.sections,
        },
        "dropped_events": bundle.dropped_events,
        "guard": guard_hit,
        "recall": recall.as_dict(),
        "companion": companion_state,
        "thought": int(surface_item["id"]) if surface_item else None,
        "pinned": pinned_id,
        "anchor_scene": scene,
        "anchors_used": len(anchor_lines),
        "aversion_phase": phase,
        "user_id": int(user_id or 0),
    }
    rt.last_meta = meta
    if diary_hint:
        diary.mark_referenced()

    return {
        "reply": segments,
        "state": {
            "affinity": settlement.get("affinity", relation["affinity"]),
            "trust": settlement.get("trust", relation["trust"]),
            "attraction": settlement.get("attraction", relation["attraction"]),
            "aversion": round(aversion, 1),
            "aversion_phase": phase,
            "stage": settlement.get("stage"),
            "stage_label": settlement.get("stage_label"),
            "call_user": settlement.get("call_user"),
            "next_stage": settlement.get("next_stage"),
            "delta": {
                "affinity": settlement.get("d_affinity", 0),
                "trust": settlement.get("d_trust", 0),
                "attraction": settlement.get("d_attraction", 0),
            },
            "session": session_mod.session_payload(persona),
        },
        "meta": meta,
    }


# ---------------------------------------------------------------- 命名

def _state_for(persona) -> dict[str, Any]:
    rel = engine.get_relation()
    sp = engine.stage_payload(persona, float(rel["affinity"]))
    return {
        **rel,
        "aversion": session_mod.get_aversion(),
        "aversion_phase": session_mod.aversion_phase(session_mod.get_aversion()),
        "stage": sp["stage"],
        "stage_label": sp["stage_label"],
        "call_user": sp["call_user"],
        "next_stage": sp["next_stage"],
        "delta": {"affinity": 0, "trust": 0, "attraction": 0},
        "session": session_mod.session_payload(persona),
    }


def _store_her_script(result: dict[str, Any], source: str, user_id: int,
                      chat_type: str, group_id: str) -> None:
    """把"不走模型的确定性回复"（恋爱线 / 拉黑线）也写进 events。

    这些分支原来直接 return，导致：用户在 QQ 里看到了她的话，但库里没有 →
    下一轮上下文缺她自己的上一句、面板/记忆/转录都看不到。真机实测的 P1。
    """
    try:
        lines = [str(x) for x in (result.get("reply") or []) if str(x).strip()]
        if not lines or (result.get("meta") or {}).get("stored"):
            return
        store.add_event("her", "\n".join(lines), source=source,
                        user_id=int(user_id or 0), chat_type=chat_type or "",
                        group_id=group_id or "")
    except Exception:  # noqa: BLE001 - 记账失败不许拖垮回复
        pass


def _say(persona, lines: list[str], source: str, extra: dict[str, Any] | None = None,
         *, user_id: int = 0, chat_type: str = "", group_id: str = "") -> dict[str, Any]:
    # 归属必须带上：命名流程的台词原来只写 source，导致 user_id=0 / chat_type=''，
    # 面板和她自己的上下文都看不到这句（真机实测：命名那段看起来"她没回"）。
    store.add_event("her", "\n".join(lines), source=source,
                    user_id=int(user_id or 0), chat_type=chat_type or "",
                    group_id=group_id or "")
    meta = {"naming": True, "provider": "script", "delays_ms": [520 + len(x) * 26 for x in lines]}
    if extra:
        meta.update(extra)
    return {"reply": lines, "state": _state_for(persona), "meta": meta}


def _naming_turn(rt: Runtime, text: str, source: str, *,
                 user_id: int = 0, chat_type: str = "", group_id: str = "") -> dict[str, Any] | None:
    """命名是"可选项"：用户不提，就正常聊；提了才走流程。"""
    st = session_mod.get(rt.default_name)
    if st.get("name_locked"):
        return None

    persona = rt.persona
    # 命名是剧本台词，也必须带上归属（user_id/chat_type），否则上下文里缺这一句
    def say(lines, _src=None, extra=None):
        # 兼容两种调用：say(lines, {...}) 与 say(lines, source, {...})
        if isinstance(_src, dict) and extra is None:
            extra = _src
        return _say(persona, lines, source, extra,
                    user_id=user_id, chat_type=chat_type, group_id=group_id)

    step = int(db.kv_get("naming_step", "0"))
    # 命名流程有时效：超过 5 分钟没答完就作废，别让半截状态劫持后面的聊天（实测踩过）
    if step:
        import time as _time

        started = float(db.kv_get("naming_started_at", "0") or 0)
        if started and _time.time() - started > 300:
            db.kv_set("naming_step", "0")
            db.kv_set("naming_started_at", "0")
            step = 0

    if step == 0:
        if not session_mod.wants_naming(text):
            return None
        cand = session_mod.extract_name(text)
        if cand:
            import time as _time

            db.kv_set("naming_started_at", str(_time.time()))
            session_mod.set_pending(cand)
            db.kv_set("naming_step", "2")
            return say( [f"「{cand}」？", "嗯。", "确定要用这个？"], source,
                        {"naming_step": 2, "pending_name": cand})
        import time as _time

        db.kv_set("naming_started_at", str(_time.time()))
        db.kv_set("naming_step", "1")
        db.kv_set("naming_step1_tries", "0")
        return say( ["好啊。", "你想让我叫什么？"], source, {"naming_step": 1})

    if step == 1:
        cand = session_mod.extract_name(text)
        if cand:
            session_mod.set_pending(cand)
            db.kv_set("naming_step", "2")
            return say( [f"「{cand}」？", "确定要用这个？"], source,
                        {"naming_step": 2, "pending_name": cand})
        if session_mod.is_decline(text):
            db.kv_set("naming_step", "0")
            return say( ["那先这样。", "想好了再说。"], source, {"naming_step": 0})
        # 问了两次还答不出名字就别再问了，回到正常聊天（否则她会一直卡在"你说一个"）
        tries = int(db.kv_get("naming_step1_tries", "0") or 0) + 1
        db.kv_set("naming_step1_tries", str(tries))
        if tries >= 2:
            db.kv_set("naming_step", "0")
            db.kv_set("naming_step1_tries", "0")
            return None
        return say( ["你说一个，我就记着。"], source, {"naming_step": 1})

    if step == 2:
        if session_mod.is_affirm(text):
            chosen = session_mod.confirm_name()
            db.kv_set("naming_step", "3")
            return say( ["嗯。", f"那以后我就是{chosen}。", "不许改口。"], source,
                        {"naming_step": 3, "locked": True})
        newcand = session_mod.extract_name(text)
        if newcand:
            session_mod.set_pending(newcand)
            return say( [f"「{newcand}」？", "确定？"], source,
                        {"naming_step": 2, "pending_name": newcand})
        db.kv_set("naming_step", "0")
        return say( ["行，先不定了。"], source, {"naming_step": 0})

    db.kv_set("naming_step", "3")
    return None


# ---------------------------------------------------------------- 结束

def _end_session(rt: Runtime, persona, source: str) -> dict[str, Any]:
    lines = session_mod.finale(persona, persona.name)
    for line in lines:
        store.add_event("her", line, source=source)
    info = session_mod.terminate(persona)
    db.kv_set("naming_step", "3")

    # 告别信后台生成：不让用户卡在结束动画上等
    db.kv_set("letter_status", "pending")
    db.kv_set("letter_archive", info["archive"])
    try:
        import pathlib as _pl

        asyncio.create_task(finale.compose(rt, _pl.Path(info["archive"])))
    except Exception:  # noqa: BLE001
        pass

    new_state = _state_for(persona)
    return {
        "reply": lines,
        "state": {**new_state, "ended": True, "archive": info["archive"]},
        "meta": {
            "session_ended": True,
            "archive": info["archive"],
            "old_name": info["old_name"],
            "new_name": info["new_name"],
            "provider": "script",
            "delays_ms": [900 + len(x) * 40 for x in lines],
        },
    }


def _recall_line(persona, status: str) -> str:
    block = persona.raw.get("recall") or {}
    keys = ("lost",) if status == "miss" else ("thinking",)
    for key in keys:
        items = block.get(key) or []
        if items:
            return str(items[0])
    return "……让我想想。" if status == "uncertain" else "我想不起来了。"


def _recent_user_turns(minutes: int = 10, user_id: int = 0, chat_type: str = "") -> int:
    """最近 N 分钟内这个人说了几句——用来判断"叫了几次"。

    不传 user_id 时保持原来全库口径（面板/单用户）。
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    where = ["role = 'user'", "ts >= ?"]
    args: list[Any] = [cutoff]
    if int(user_id or 0) > 0:
        where.append("user_id = ?")
        args.append(int(user_id))
    if chat_type:
        where.append("(chat_type = ? OR chat_type = '')")
        args.append(str(chat_type))
    row = db.one(f"SELECT COUNT(*) c FROM events WHERE {' AND '.join(where)}", tuple(args))
    return int(row["c"] or 0) if row else 0


_MEME_CANDIDATES = (
    "洗衣机", "深海", "拧干", "塞进", "枕头底下", "泡进", "扔进", "拉进", "关进",
    "复读机", "陪你", "睡觉", "洗澡", "困死",
    # 鲸鱼娘人设的高频梗：说多了就是复读，必须进冷却
    "尾巴", "白饭", "大肥鱼", "鲸鱼", "鲸娘", "呆毛", "头鳍", "交班", "摸鱼",
    "欠我一顿", "便宜货", "账本", "记着",
)


def _avoid_words() -> list[str]:
    """最近 6 条她说的话里反复出现的词，下一轮别再用了。"""
    rows = db.rows("SELECT text FROM events WHERE role = 'her' ORDER BY id DESC LIMIT 6")
    recent = [str(r["text"] or "") for r in rows]
    out = []
    for w in _MEME_CANDIDATES:
        hits = sum(1 for t in recent if w in t)
        # 近 6 条里出现 ≥2 次就冷却；出现 1 次的不拦（正常使用）
        if hits >= 2:
            out.append(w)
    return out[:8]


# ---------------------------------------------------------------- 离线兜底


def _offline_reply(persona, rt=None) -> str:
    """模型不可用时的回复：不说"出错"，说"去吃饭了"。

    按时间和近期状态挑一句，避免每次都一样。
    """
    import random

    try:
        hour = int(clock.get_state(persona).get("hour", 12))
    except Exception:  # noqa: BLE001
        hour = 12

    # 深夜
    if 0 <= hour < 6:
        pool = [
            "……这会儿脑子转不动了，我先去吃点东西垫垫。",
            "饿得不行，先扒两口饭，等下再跟你说。",
            "我先去弄点吃的，不然要罢工了。",
        ]
    # 饭点
    elif 11 <= hour < 13 or 17 <= hour < 19:
        pool = [
            "到饭点了，我先去吃饭，你等我一会儿。",
            "饿了，先扒两口饭，等下回你。",
            "我这肚子已经叫半天了，先去吃饭。",
        ]
    # 傍晚/晚上
    elif 19 <= hour < 23:
        pool = [
            "我这儿正准备吃饭呢，等下再说。",
            "饿了饿了，先去吃口饭。",
            "先让我吃口饭，脑子才好使。",
        ]
    # 白天
    else:
        pool = [
            "我这就有点饿了，先去吃口饭。",
            "等一下，我去弄点吃的，马上回来。",
            "先吃口饭，不然说话都没力气。",
            "……饿了，先吃饭，等我。",
        ]
    return random.choice(pool)


def _is_echo_reply(text: str) -> bool:
    """整段只是数字/序号/标点，或者明显的 1 2 3 排列——当复述处理。"""
    t = (text or "").strip()
    if not t:
        return False
    if not any("\u4e00" <= ch <= "\u9fff" or ch.isalpha() for ch in t):
        return True
    if len(t) <= 20 and sum(ch.isdigit() for ch in t) >= 3 and t.count("\n") >= 2:
        return True
    return False


# 模型会脑补「你说两遍/你连说三遍」这类幻觉：用户明明只发了一次。
# 这是 prompt 里带否定词引起的反向推断，直接去掉这半句。
_REPEAT_CLAIM = re.compile(
    r"(你|他)?[^，。！？\n]{0,6}?(连着?|连|重复|又)?(说|问|报|喊|念叨|提|发|讲)(了|过)?(两|三|好几|几)遍"
    r"|(你|他)(连着?|连)(说|问|喊|报|发)[了过]?"
    r"|复读(机|了)?"
    r"|(两|三|好几)遍(连)?(语气)?[^，。]{0,6}(不变|一样|重复)"
    r"|(总|又|老是?)说(同|一)(样|句)"
    r"|(连着?|连)(着)?(两|三|好几)遍"
    # 实测又踩到的变体：不写"复读"两个字，改成"又重了/又贴了一遍/念岔了"
    r"|又(复|重|贴)了(一遍|两遍|三遍|一次|两次)?"
    r"|才复了?(一遍|两遍)"
    r"|(你|他)[^，。！？\n]{0,4}(念|说)岔了"
    # 身份幻觉：她偶尔会凭空说"你号不对/不是这串/你换号了"（实测会自我强化）
    r"|你?(的)?号(码)?(对不上|不对)"
    r"|不是这串"
    r"|你换号了"
)


def _clean_line(line: str) -> str:
    """按分句处理：含指控重复的分句丢掉，其余分句保留。"""
    parts = re.split(r"([，。！？；…\n])", line)
    out: list[str] = []
    for i in range(0, len(parts), 2):
        seg = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if not seg.strip():
            continue
        if _REPEAT_CLAIM.search(seg):
            continue  # 这个分句在指控重复，丢掉
        out.append(seg + sep)
    return "".join(out).strip()


def scrub_repeat_claim(text: str) -> str:
    """去掉"你说了两遍/复读机"这类幻觉，按分句清理，保留其余内容。"""
    if not text:
        return text
    if not _REPEAT_CLAIM.search(text):
        return text
    kept = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cleaned = _clean_line(line)
        # 清完只剩标点或太短的，整行丢掉
        if not re.search(r"[\u4e00-\u9fffA-Za-z]{2,}", cleaned):
            continue
        kept.append(cleaned)
    return "\n".join(kept) if kept else "嗯，我在。"
