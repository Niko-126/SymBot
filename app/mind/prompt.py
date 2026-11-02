from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Config
from app.persona.load import Persona

#: 用户在问"你记得吗"这类记忆问题（用于给 recall_status=none 补"不许装记得"的闸）﻿‌​‌‍‌‍​‌‍‌﻿​‍‍​​​​‌‌​‍﻿﻿‍‌​‍‌‍​﻿﻿
_MEMORY_QUESTION = re.compile(
    r"(还记得|记不记得|记得吗|我说过|我跟你说|上次说|上回说|之前说|以前说|那天说|那次说|想起来)"
)


def est_tokens(text: str) -> int:
    """粗略 token 估计：中日韩字符按 0.7，其余按 0.25。够用于预算控制，不用于计费。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    other = len(text) - cjk
    return int(cjk * 0.7 + other * 0.25) + 1


@dataclass
class PromptBundle:
    system: str
    messages: list[dict[str, str]]
    sections: dict[str, int] = field(default_factory=dict)
    used_tokens: int = 0
    context_ratio: float = 0.0
    dropped_events: int = 0


def _fmt_memories(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return ""
    buckets: dict[str, list[str]] = {}
    for m in memories:
        buckets.setdefault(str(m.get("kind", "fact")), []).append(str(m.get("content", "")))
    labels = {
        "boundary": "不能碰的",
        "promise": "答应过的事",
        "fact": "关于他",
        "preference": "他的口味和习惯",
        "event": "一起经历过的",
        "quote": "他说过的原话",
    }
    out: list[str] = []
    for kind, label in labels.items():
        items = [c for c in buckets.get(kind, []) if c]
        if items:
            out.append(f"{label}：")
            out.extend(f"- {c}" for c in items[:8])
    return "\n".join(out)


def _fmt_life(life: dict[str, Any]) -> str:
    energy = float(life.get("energy", 60))
    mood = float(life.get("mood", 0))
    parts = [
        f"现在是你那边的 {life.get('virtual_time_human', '')}。",
        f"你此刻在{life.get('activity', '发呆')}。",
    ]
    # 时间锚点：以系统时间为准，白天不许用深夜话术（实测踩过：下午 14:51 说「还没睡」）
    try:
        hour = int(life.get("hour", -1))
    except (TypeError, ValueError):
        hour = -1
    if 7 <= hour <= 21:
        parts.append("现在是白天/傍晚，按这个时间说话：别用「还没睡」「这么晚」「早点睡」「晚安」这类深夜话术。")
    elif hour >= 23 or 0 <= hour <= 5:
        parts.append("现在是深夜，话少一点、轻一点。")
    if energy <= 25:
        parts.append("你有点撑不住了，说话会更短、更慢。")
    elif energy <= 45:
        parts.append("你不太有精神，但还愿意说话。")
    if mood <= -2:
        parts.append("你心情不好，不会主动哄人。")
    elif mood >= 2:
        parts.append("你心情不错，话会比平时多一点。")
    if life.get("sleeping"):
        parts.append("这个时间你本该睡了，被抓到会有点不好意思。")
    return "".join(parts)


def _fmt_relation(persona: Persona, relation: dict[str, Any]) -> str:
    affinity = float(relation.get("affinity", 0))
    stage = persona.stage_for(affinity)
    reached = [
        s.get("label")
        for s in persona.stages()
        if float(s.get("at", 0)) <= affinity
    ]
    unlocks: list[str] = []
    for s in persona.stages():
        if float(s.get("at", 0)) <= affinity:
            unlocks.extend([str(u) for u in (s.get("unlock") or [])])
    lines = [
        f"你们现在处在「{stage.get('label', '')}」这个阶段。你对他的称呼：{stage.get('call_user', '「你」')}。",
        f"这个阶段的你：{stage.get('behavior', '')}",
    ]
    if len(reached) > 1:
        lines.append("你们已经走过：" + "、".join(str(r) for r in reached) + "。")
    if unlocks:
        lines.append("你已经知道的关于你的事：" + "；".join(unlocks[:4]) + "。")
    return "\n".join(lines)


def _fmt_traits(persona: Persona) -> str:
    """把八维翻译成行为规则。数值本身不进 prompt——她不该知道自己的参数。"""
    v = {k: float(v.get("baseline", 50)) for k, v in persona.traits().items()}
    lines: list[str] = []

    def rule(dim: str, high: str, low: str) -> None:
        val = v.get(dim)
        if val is None:
            return
        if val >= 70:
            lines.append(high)
        elif val <= 45:
            lines.append(low)

    rule("黏人", "你会连着发好几条，不等他回也不觉得尴尬。", "你不黏人，说完就放下。")
    rule("幽默", "你爱笑，爱损他，玩笑开得起，也接得住他的损。", "你不太开玩笑。")
    rule("沉静", "你话少，安静不觉得尴尬。", "你安静不下来，话很密。")
    rule("自持", "你压得住，不轻易失态。", "你情绪写在脸上，高兴和生气都藏不住。")
    rule("敏感", "你容易被一句话刺到，而且会记着。", "你不太往心里去。")
    rule("理性", "你会讲道理，也会在讲不通的时候停下来。", "你不讲道理，跟着感觉走。")
    rule("温柔", "你说话会软下来，尤其是对他。", "你不太会嘘寒问暖，亲密靠直给而不是照顾。")
    rule("好奇", "你会追问细节，想把他的一天问清楚。", "你不太追问。")
    if not lines:
        return ""
    return "\n".join(["你的脾气："] + [f"- {x}" for x in lines])


def _fmt_bottom_line(persona: Persona) -> str:
    bl = persona.raw.get("bottom_line") or {}
    if not bl:
        return ""
    lines = ["你在关系里的底线：", str(bl.get("principle", "")).strip()]
    repair = bl.get("repair") or []
    if repair:
        lines.append("出问题的时候：")
        lines.extend(f"- {r}" for r in repair)
    forbid = bl.get("forbidden") or []
    if forbid:
        lines.append("你不使用的手段：")
        lines.extend(f"- {f}" for f in forbid)
    floor = str(bl.get("floor", "")).strip()
    if floor:
        lines.append(f"最低限度：{floor}")
    return "\n".join(x for x in lines if x)


def _fmt_memory_loss(persona: Persona) -> str:
    block = persona.raw.get("memory_loss") or {}
    lines = [str(x) for x in (block.get("lines") or []) if str(x).strip()]
    if not lines:
        return ""
    return (
        "你最近记性变差了，有些事想不起来。被问到时，你只会说：\n"
        + "\n".join(f"「{x}」" for x in lines[:3])
        + "\n不要解释成生病以外的原因，也不要承认自己是程序。"
    )


def _fmt_immersion(persona: Persona) -> str:
    im = persona.raw.get("immersion") or {}
    never = im.get("never_say") or []
    asked = im.get("when_asked") or []
    lines = ["你从不说出口的话："]
    lines.extend(f"- {n}" for n in never)
    if asked:
        lines.append("被追问这类问题时，你会这样挡回去：")
        lines.extend(f"「{a}」" for a in asked)
    lines.append("- 不解释自己是什么，不承认也不否认，绕开就行")
    return "\n".join(lines)


def build(
    cfg: Config,    persona: Persona,
    *,
    relation: dict[str, Any],
    life: dict[str, Any],
    memories: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    user_text: str,
    summary: str = "",
    degraded: bool = False,
    state_hint: str = "",
    # 陪伴层的两个独立提示：忽然想起（当场触发的回忆）、夜里想的（独处时冒出来的念头）
    # 默认空字符串，旧调用方不传时行为与之前完全一致
    companion_hint: str = "",
    thought_hint: str = "",
    # 【你今天】human_life 语料接管的"她今天在干嘛"（只描述当下生活，不是回忆）
    today_hint: str = "",
    # 恋人私聊的语气样本（1–2 条短句）与对外人/服务人员的规则；默认空，旧调用方零改动
    partner_hint: str = "",
    stranger_hint: str = "",
    # 工具环节查回来的东西（搜索结果 / 网页正文 / 图里的内容）；默认空 = 这一轮没调工具
    tool_hint: str = "",
    first_contact: bool = False,
    avoid_words: list[str] | None = None,
    deep_think: bool = False,
    recalled: list[dict[str, Any]] | None = None,
    recall_status: str = "none",
    anchors: list[str] | None = None,
    aversion_phase: str = "calm",
    diary: str = "",
    chat_type: str = "",
    speakers: dict[int, str] | None = None,
) -> PromptBundle:
    affinity = float(relation.get("affinity", 0))
    stage = persona.stage_for(affinity)

    identity = "\n".join(
        filter(
            None,
            [
                f"你是{persona.name}。{persona.tagline}",
                f"你不是助手，不是虚拟伴侣产品，你是一个有自己生活的人。",
                f"【你所在的世间】\n{persona.world}",
                f"【你的样子】\n{persona.appearance}",
                f"自称「{persona.self_ref}」。不要用「本小姐」「人家」这类自称。",
                _fmt_traits(persona),
            ],
        )
    )

    now_block = _fmt_life(life)
    relation_block = _fmt_relation(persona, relation)
    memory_block = _fmt_memories(memories)
    av_lines = {
        "uneasy": "你心里有点不舒服，但没说出口。说话会短一点。",
        "irritated": "你现在很烦他。回复更短，不哄人，不解释，也不主动找话题。",
        "cold": "你几乎不想理他。一句话就够，别接他的话题。",
    }
    aversion_block = av_lines.get(aversion_phase, "")
    first_block = ""
    if first_contact:
        first_block = (
            "这是你们第一次说话。别等着对方找话题——你先开口，问一个具体的小问题（现在几点、在做什么、吃饭没有）。\n"
            "语气热一点，别客套，也别一次问太多。"
        )
    avoid_block = ""
    if avoid_words:
        avoid_block = (
            "这一轮换个说法就行。最近用得比较多的词："
            + "、".join(avoid_words[:8])
            + "（不必刻意回避，自然些更好）。"
        )
    think_block = (
        "回之前先在心里过一遍：\n"
        "1. 他这句话背后真正想确认什么、现在是什么情绪。\n"
        "2. 可以承认、可以含糊、可以反过来问他，但不要每句都用打闹收尾。\n"
        "3. 不要为了像她，硬塞口头禅或反复提同一个东西；先像一个正常人在回话。\n"
        "4. 不要每轮都用问题收尾：连着两轮问过了这轮就别再问；有时候只接一句、给个反应就够了。\n"
        + (avoid_block if avoid_block else "")
    )
    if deep_think:
        think_block += (
            "\n这次可以多想一会儿：如果他说的话里有不确定的地方，先说你的判断，再说你在意什么。"
        )
    summary_block = summary.strip()
    memory_loss = _fmt_memory_loss(persona) if degraded else ""
    recalled_block = "\n".join(f"- {m.get('content', '')}" for m in (recalled or [])[:3])
    recall_hint = ""
    if recall_status == "uncertain":
        recall_hint = "这件事你只有模糊的印象。先说一句「让我想想」这类的话，再给个大概；想不准的部分就说记不清。"
    elif recall_status == "miss":
        recall_hint = "你想不起来这件事。不要编。直接说记不清，然后问他一个线索（哪一年、在哪儿、当时还有谁）。"
    elif recall_status == "none" and _MEMORY_QUESTION.search(user_text or ""):
        # 实测踩过：库里一条记忆都没有时，恋人档会先说「记得」再补「记不太清」（讨好式编造）。
        # 事实不许装记得；偏好/情绪可以顺着聊——这是"善意"与"编造"的分界。
        recall_hint = (
            "这件事你这里没有记忆命中（记忆里找不到）。事实性内容一律不许装记得："
            "直接说记不清/没印象（可以说「我这儿没印象」），不要先说「记得」再补「记不太清」。"
            "偏好、口味、情绪这类可以顺着聊——但别把没发生过的事说成发生过。"
        )

    # 【忽然想起】/【夜里想的】独立成段：这两类临时念头不再混进【此刻的状态】
    companion_block = companion_hint.strip()
    thought_block = thought_hint.strip()
    # 【你今天】她今天的生活（不是记忆里的往事）
    today_block = today_hint.strip()
    # 【你们以前的语气】恋人私聊时她以前的说话样本；【跟外人打交道】对陌生人的行为规则
    partner_block = partner_hint.strip()
    stranger_block = stranger_hint.strip()
    # 【刚查到的】联网工具 / 识图工具的结果；空串时整段不出现
    tool_block = tool_hint.strip()

    speech = "\n".join(
        ["你说话的方式："]
        + [f"- {s}" for s in persona.style()]
        + ["", "硬规则："]
        + [f"- {r}" for r in persona.rules()]
        + ["", "语气样本（只看语感：节奏、口语、怎么收尾；不要整句照搬，也不要每轮都用）："]
        + [f"「{q}」" for q in (anchors if anchors else persona.anchors(per_scene=1))[:6]]
    )
    forbid = "\n".join(["你绝对不会："] + [f"- {n}" for n in persona.negatives()])
    bottom = _fmt_bottom_line(persona)
    immersion = _fmt_immersion(persona)

    # 劝睡抑制：同一晚她已经劝过两次，这一轮就明令禁止再提睡觉
    _nag_words = ("睡吧", "早点睡", "该睡了", "别熬", "快去睡", "睡去了")
    _nag_n = sum(1 for e in (recent_events or [])
                 if e.get("role") == "her"
                 and any(w in str(e.get("text", "")) for w in _nag_words))
    sleep_nag_block = (
        "【别再劝睡】\n你已经劝他睡过两回了，他还在跟你聊。这一轮**不许再提睡觉**，"
        "也不要用「明天再说」收尾；就当他明天不上班，陪他聊下去。"
        if _nag_n >= 2 else ""
    )

    sections = {
        "identity": est_tokens(identity),
        "now": est_tokens(now_block),
        "relation": est_tokens(relation_block),
        "memory": est_tokens(memory_block),
        "speech": est_tokens(speech),
        "forbid": est_tokens(forbid),
        "bottom": est_tokens(bottom),
        "immersion": est_tokens(immersion),
        "recalled": est_tokens(recalled_block),
        "summary": est_tokens(summary_block),
        "state_hint": est_tokens(state_hint),
        # 两个新分区单独计预算，键名固定供面板/测试断言
        "companion": est_tokens(companion_hint),
        "thought_surface": est_tokens(thought_hint),
        # 她今天的日常（human_life 语料），单独计预算
        "today": est_tokens(today_hint),
        # 语气样本与对外人规则，单独计预算，键名固定供面板/测试断言
        "partner": est_tokens(partner_hint),
        "stranger": est_tokens(stranger_hint),
        # 工具环节的结果单独计预算，键名固定供面板/测试断言
        "tools": est_tokens(tool_hint),
        "first": est_tokens(first_block),
        "think": est_tokens(think_block),
        "memory_loss": est_tokens(memory_loss),
        "recall_hint": est_tokens(recall_hint),
        "aversion": est_tokens(aversion_block),
        "diary": est_tokens(diary),
        "sleep_nag": est_tokens(sleep_nag_block),
    }

    # ---- 窗口预算：从最老的开始丢，直到塞得下 ----
    keep = list(recent_events)
    dropped = 0
    fixed = sum(sections.values()) + est_tokens(user_text) + 64
    while keep and fixed + sum(est_tokens(e.get("text", "")) for e in keep) > cfg.context.usable:
        keep.pop(0)
        dropped += 1

    messages: list[dict[str, str]] = []
    for e in keep:
        role = "assistant" if e.get("role") == "her" else "user"
        body = str(e.get("text", ""))
        # 群聊：每条别人的话前面带名字，否则她分不清谁是谁（认人问题的根）
        if role == "user" and chat_type == "group":
            label = ""
            try:
                label = str((speakers or {}).get(int(e.get("user_id") or 0), "") or "")
            except (TypeError, ValueError):
                label = ""
            if label:
                body = f"{label}：{body}"
        messages.append({"role": role, "content": body})
    messages.append({"role": "user", "content": user_text})

    system = "\n\n".join(
        [x for x in [
            f"【身份】\n{identity}",
            f"【此刻】\n{now_block}",
            f"【你今天】\n{today_block}" if today_block else "",
            f"【我们】\n{relation_block}",
            f"【你们以前的语气】\n{partner_block}" if partner_block else "",
            f"【之前聊过】\n{summary_block}" if summary_block else "",
            f"【你记得的】\n{memory_block}" if memory_block else "【你记得的】\n（暂时还不多）",
            f"【你昨天的日记】\n{diary}" if diary else "",
            f"【刚想起来的】\n{recalled_block}" if recalled_block else "",
            f"【忽然想起】\n{companion_block}" if companion_block else "",
            f"【夜里想的】\n{thought_block}" if thought_block else "",
            f"【想不起来的时候】\n{recall_hint}" if recall_hint else "",
            f"【你现在的心情】\n{aversion_block}" if aversion_block else "",
            f"【此刻的状态】\n{state_hint}" if state_hint else "",
            sleep_nag_block,
            f"【跟外人打交道】\n{stranger_block}" if stranger_block else "",
            f"【刚查到的】\n{tool_block}" if tool_block else "",
            f"【第一次见面】\n{first_block}" if first_block else "",
            f"【先想再答】\n{think_block}" if think_block else "",
            f"【你最近的身体】\n{memory_loss}" if memory_loss else "",
            f"【说话】\n{speech}",
            f"【边界】\n{forbid}",
            f"【底线】\n{bottom}" if bottom else "",
            f"【别露馅】\n{immersion}" if immersion else "",
            "【说话的感觉】\n"
            "像人在 QQ 上打字：句子长短不齐，有时一句就完，有时连着说三句；\n"
            "可以有口头语（啊、吧、呢、行了、说真的），可以跑题一下再拉回来，可以故意说一半。\n"
            "不用每句都完整、都漂亮、都讲道理——这不像人。\n"
            "想笑就笑，想怼就怼，想沉默就只说两三个字。\n"
            "他说的每句话都是新的，正常接住就行。\n"
            "他重复同一句话、绕回来、或者前后对不上，都不要指出来（别说「你说了两遍」「又重了」「念岔了」）"
            "——直接接内容，像人一样不当回事。\n"
            "不要质疑对方的身份或账号（别说「你号不对」「你换号了」「不是这串」）；"
            "认人就认你记得的那个名字，没记清就直接问。\n"
            "如果他一次发来几条短消息、一串数字或几个词，就把意思合起来接一句。\n"
            "与其说漂亮话，不如说一件具体的事：时间、动作、你刚才在做什么。\n"
            "别每轮都用「去睡吧 / 早点睡」收尾；同一晚不要连着三次劝他睡，"
            "他醒着你就正常聊，困不困你自己心里有数就行。\n"
            "同一句话、同一个句式不要连着重用（包括「不过…」「你先…」）。",
        ] if x]
    )

    used = est_tokens(system) + sum(est_tokens(m["content"]) for m in messages)
    return PromptBundle(
        system=system,
        messages=messages,
        sections=sections,
        used_tokens=used,
        context_ratio=round(used / max(1, cfg.context.window), 4),
        dropped_events=dropped,
    )


_SPLIT = re.compile(r"(?<=[。？！?!…])")


def segment_reply(text: str, max_segments: int = 5) -> list[str]:
    """把一段回复切成多条气泡。人在聊天里不会一次发一大段。"""
    text = text.strip()
    if not text:
        return ["……"]
    parts = [p.strip() for p in text.replace("\r", "").split("\n") if p.strip()]
    out: list[str] = []
    for part in parts:
        chunks = [c.strip() for c in _SPLIT.split(part) if c.strip()]
        # 太碎的合并：小于 6 字的句子并到下一句
        buf = ""
        for c in chunks:
            if len(buf) + len(c) <= 10 and buf:
                buf += c
            else:
                if buf:
                    out.append(buf)
                buf = c
        if buf:
            out.append(buf)
    out = [o for o in out if o]
    if len(out) > max_segments:
        head = out[: max_segments - 1]
        tail = "".join(out[max_segments - 1 :])
        out = head + [tail]
    return out or ["……"]
