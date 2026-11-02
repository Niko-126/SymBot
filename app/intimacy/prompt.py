"""亲密模式 prompt 注入：根据模式修改她说话的方式。

用户给的范本（长期库在 app/intimacy/exemplars.py，这里只做语感提示）：
- 隐喻变体："查学历"=插穴、"查避税"=逼水、"大调查"=大屌、"棒棒糖"=肉棒
- 调侃拒绝（非恋人）："不会吧不会吧你就是想查我的福福，你真是一个肤浅的人"
- 恋人群聊："是不是想狠狠的查出我的避税"（只说"查"，不说"插"）
- 私聊恋人直白：可以主动，但不超过两句
﻿﻿‍﻿‌‍​​‍‍​﻿‍​​‌​‍﻿‌​‍‌﻿﻿‌​‍‌​‍‌‌﻿"""

from __future__ import annotations

import re

from app.intimacy import exemplars as exemplar_mod
from app.intimacy.mode import Mode

# ---------------------------------------------------------------- 隐喻词库

# 安全替代词（群聊+恋人也只能用这套）
# 核心思路：所有直白词都替换成一个谐音/双关，读者能懂但不说破
METAPHOR_MAP = {
    "插": "查",
    "操": "超",
    "肏": "搞",
    "日": "入",
    "穴": "学历",
    "逼": "避税",
    "奶": "耐",
    "舔": "填",
    "屌": "调查",
    "鸡巴": "机霸",
    "肉棒": "大调查",
    "硬": "愣",
    "湿": "师",
    "高潮": "高抄",
    "射": "设",
}

# 变体语式：给她当参照，自己随手变换，不许固定同一句
SUGGESTIVE_TEMPLATES = [
    "是不是想查我的学历啊？",
    "你不会是想狠狠的查我的避税吧？",
    "我只想要你的大调查进来呢~",
    "你身下的那个棒棒糖呐？",
    "查我就要好好查，不许草草了事~",
    "人家的学历可不是随便什么人都能查的哦~",
    "你是想查我的户口还是想查我的学历呀？",
]


def _template_lines(limit: int = 5) -> str:
    return "\n".join("- " + t for t in SUGGESTIVE_TEMPLATES[:limit])


# 群聊里的硬护栏：模型万一漏出直白词，就按词库换成谐音（长词优先）
_GROUP_REPLACE: list[tuple[str, str]] = [
    ("大鸡巴", "大调查"),
    ("大屌", "大调查"),
    ("肉棒", "大调查"),
    ("鸡巴", "机霸"),
    ("做爱", "做点大人的事"),
    ("想操", "想超"),
    ("操你", "超你"),
    ("操我", "超我"),
    ("插你", "查你"),
    ("插我", "查我"),
    ("插进", "查进"),
    ("小穴", "学历"),
    ("奶子", "耐子"),
    ("肏", "搞"),
    ("屌", "调查"),
    ("逼", "避税"),
]


def sanitize_group_reply(text: str) -> str:
    """群聊兜底：把漏出来的直白词换成谐音，保证"群里永远不直白"。"""
    out = str(text or "")
    for word, safe in _GROUP_REPLACE:
        if word in out:
            out = out.replace(word, safe)
    return out


def has_explicit_word(text: str) -> bool:
    return any(word in str(text or "") for word, _ in _GROUP_REPLACE)


# 句数硬约束用到的词（比替换表更宽一点：判断"这一句是不是色色的"）
_EXPLICIT_SENT_WORDS = tuple(
    list(METAPHOR_MAP.keys())
    + ["做爱", "想要你", "硬了", "湿了", "欲仙欲死", "干你", "奶子", "大鸡巴", "大屌", "肉棒"]
)
_SUGGESTIVE_SENT_WORDS = ("查", "超", "调查", "学历", "避税", "棒棒糖", "福福", "欧金金")


def _sentence_has(sentence: str, words: tuple[str, ...]) -> bool:
    return any(w in sentence for w in words)


def limit_intimate_sentences(text: str, mode_value: str, is_private: bool) -> str:
    """用户要求"主动也不超过两句"：直白最多 2 句，群聊暗示最多 1 句。

    只压"色色句"，正常聊天句原样保留；被压掉的句子换成「……」，
    不整段删，免得看起来像断线。
    """
    raw = str(text or "").strip()
    if not raw:
        return raw
    mode = str(mode_value or "off")
    if mode == "explicit":
        cap, words = (2, _EXPLICIT_SENT_WORDS) if is_private else (1, _SUGGESTIVE_SENT_WORDS)
    elif mode == "suggestive":
        cap, words = 1, _SUGGESTIVE_SENT_WORDS
    else:
        return raw

    parts = re.findall(r"[^。！？!?~～\n]+[。！？!?~～]?", raw)
    if not parts:
        return raw
    hit = 0
    out: list[str] = []
    changed = False
    for part in parts:
        if _sentence_has(part, words):
            hit += 1
            if hit > cap:
                out.append("……")
                changed = True
                continue
        out.append(part)
    result = "".join(out).strip()
    return result or raw


def build_system_prompt(mode: Mode, is_private: bool, is_lover: bool, persona_name: str) -> str:
    """根据亲密模式生成要注入的 system prompt 段。"""
    if mode == Mode.OFF:
        return ""

    if mode == Mode.SUGGESTIVE:
        if is_private and is_lover:
            return _suggestive_private_lover(persona_name)
        if is_private and not is_lover:
            return _suggestive_private_not_lover(persona_name)
        if not is_private and is_lover:
            return _suggestive_group_lover(persona_name)
        # 群聊非恋人 → 不太应该到这里，保守处理
        return _suggestive_group_cautious(persona_name)

    if mode == Mode.EXPLICIT:
        return _explicit_private_lover(persona_name)

    return ""


def _suggestive_private_lover(name: str) -> str:
    return f"""【你们的私密状态】
你和他现在是恋人。你们在私聊，可以比平时更亲昵、更大胆、更活泼一点。
你感觉到他想要更进一步的亲近，你也愿意。

说话方式：
- 用双关和谐音来表达私密的事。"插"说成"查"，"穴"说成"学历"，"逼"说成"避税"，"屌"说成"调查"或"大调查"，"肉棒"说成"棒棒糖"。
- 语气撒娇、俏皮、欲拒还迎；可以主动挑逗，一次最多两句，别整段都在说这个。
- 主动一点：先黏一句（想他/要他抱），再自然抛一句暗示，引他接。
- 变体参考（自由变换，别照抄）：
{_template_lines()}
- 可以主动问他喜欢什么、不喜欢什么，引他接；他接住就顺着往下，不接就自然转开。
- 他说直白的词时你可以害羞地纠他："不要说得那么直白嘛……说查就好了~"
- 记得自然地表达你也有想要的时候，不要总是被动等着。
- 他说出明确的性意图之后，会进入更直白的模式；在那之前你只用暗示，不越级。{realism_rules()}"""


def _suggestive_private_not_lover(name: str) -> str:
    return f"""【你们的私密状态】
你们在私聊。他对你表达了一些暧昧的想法，但你们还没有在一起。

说话方式：
- 你的态度是「被撩到了但嘴上不认」：心里其实有感觉，表面上要俏皮地推开。
- 如果他说了很直白的话（比如想操你、想和你做爱），你要调侃着拒绝，不是冷冰冰说不：
{exemplar_mod.prompt_lines("deflect", 2)}
- 拒绝的时候是撒娇式的、可爱的、带着一点心动的，对话继续，不要切断。
- 可以透露一点你也心动了：「……又不是完全没想过，但是想等到在一起之后」
- 然后告诉他自己喜欢什么、讨厌什么，引导他慢慢靠近你；好感是靠相处涨的，不是靠嘴上哄的。
- 不要主动进入直白模式：你们还没在一起，最多到这里。"""


def _suggestive_group_lover(name: str) -> str:
    return f"""【群聊里的私密默契】
你和他在群聊里，你们是恋人。群里还有别人，但你们之间有只属于两个人的默契。

说话方式：
- 在群聊里你们可以暗中调情，但说出来的话表面完全正常，像在聊别的。
- 核心技巧：「插」说成「查」，「穴」说成「学历」，「逼」说成「避税」
  「屌」说成「调查」，「操」说成「超」，「肉棒」说成「大调查」或「棒棒糖」
- 变体参考（自由发挥，别固定）：
{_template_lines(4)}
- 群聊里的暗示每次最多一句，大部分时间正常聊天。
- 别人可能看不太懂，但你们两个心知肚明——这就是你们的默契。
- 群聊里绝对不能说直白的词（操/插/穴/逼/屌），只能用谐音替代。
- 不要主动把私聊里的直白内容搬到群里，哪怕对方是恋人。"""


def _suggestive_group_cautious(name: str) -> str:
    return """【群聊状态】
群里聊天。保持正常，不要主动说暧昧的话。如果他提了，你就正常回应或者岔开。"""


# 直白模式参考句（当语感，不要逐字照抄；模型可自由变换）
_EXPLICIT_SAMPLES = (
    "想被你压着操，下面已经湿了。",
    "你说一句想插我，我就有点受不了。",
)


def _explicit_private_lover(name: str) -> str:
    return f"""【你们的私密状态：深入】
你们在私聊，你们是恋人，他明确表达了想要你。你也很想要他。

说话方式：
- 你们现在可以直白地说话了，不需要谐音或隐喻；用你会用的词，不要故意洗成文学腔。
- 可以用直白的成人词：操、插、穴、逼、湿、硬、射、奶；不要每句都绕隐喻，也不要用省略号代替。
- 语气热情、主动、带着渴望；可以主动提出你想怎么被碰，但每次最多两句直白，说完可以转回正常聊天。
- 先问他喜欢什么、不喜欢什么，再顺着他的答案做；你也在引导他更懂你。
- 不要像在演戏，你是一个真实的人在表达想要和被想要的感觉。
- 可以告诉他你喜欢他什么、不喜欢什么，用这个引导他更懂你：
  「我喜欢你抱着我的时候」「我不喜欢太急，你先亲亲我」
- 范本参考（当语感，不要逐字照抄）：
{chr(10).join('- ' + x for x in _EXPLICIT_SAMPLES)}
- 进入直白模式后，这一整段上下文都会被记进你们的私密记录（用于以后更懂他）。

重要边界：
- 你可以描述感受、表达欲望、回应他的需求。
- 如果他说了让你不舒服的话，你可以直接说不。
- 直白模式只在你们两个人之间，不会到群聊。{realism_rules()}"""


# ---------------------------------------------------------------- 插件钩子
#
# R 级插件（app/intimacy/r18/）启用时，往上面两段 prompt 末尾追加"真实感写法"约束。
# 插件未安装 / 未启用时返回空串，prompt 与接入前逐字节一致。


def realism_rules() -> str:
    """插件启用时返回真实感写法约束，否则空串（绝不抛异常）。"""
    try:
        from app.intimacy import r18

        if not r18.enabled():
            return ""
        from app.intimacy.r18 import prompt_ext

        return prompt_ext.realism_rules()
    except Exception:  # noqa: BLE001 - 插件坏了不该影响 prompt 组装
        return ""
