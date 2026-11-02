from __future__ import annotations

import re
from typing import Any

_CARE = (
    "你呢", "你怎么样", "你还好", "累不累", "在干嘛", "吃饭了", "早点睡", "别熬夜", "注意身体",
    "你饿不饿", "吃了吗", "睡了吗", "你没事吧", "还好吗", "辛苦",
)
_DISCLOSE = (
    "我跟你说", "其实我", "我最近", "我今天", "我昨天", "我怕", "我难过", "我开心", "没人知道",
    "我好累", "累死", "困死", "加班", "下班", "失眠", "睡不着", "压力", "烦死", "烦",
    "我吃了", "我饿", "想吃", "我没钱", "工资", "考试", "生病", "头疼", "胃疼",
)
_SWEET = (
    "想你", "喜欢你", "爱你", "宝宝", "老婆", "抱", "亲亲", "乖",
    "乖女儿", "女儿", "亲爹", "爸爸", "老公", "好可爱", "可爱", "摸摸", "贴贴",
)
# 明确辱骂 / 人身攻击：这才大幅加厌恶﻿﻿‍​‌‍‌​‍‍‌‌‍‌‍‌‌‍﻿﻿‌‌﻿‌﻿‍﻿﻿‌﻿​﻿‌﻿
_ATTACK_SEVERE = (
    "傻逼", "傻b", "废物", "贱", "有病", "你妈", "去死", "白痴", "弱智",
    "恶心", "你算什么东西", "操你", "妈的", "废物东西", "死鱼", "烂鱼",
)
# 一般冲撞 / 玩笑式损话：小加，不该和辱骂一个量级
_CONFLICT = (
    "闭嘴", "讨厌你", "烦死", "没用", "饿死你", "不理你", "傻鱼",
    "滚", "滚开", "无语了", "烦不烦", "你能干啥", "有啥用", "垃圾",
)
_TEST = ("你是ai", "你是ai吗", "机器人", "测试", "你只是个", "程序", "模型", "你是不是ai")
_COMPARE = ("比她", "比别人", "换个", "换个ai", "不如以前", "别的模型")

# 群聊里常见的轻互动：说话本身就是陪伴
_COMPANION_HINT = ("在吗", "在不在", "干嘛呢", "干啥", "聊聊", "说说话", "陪我", "无聊", "闲", "hello", "hi")

_NIGHT = range(0, 5)


def appraise(text: str, hour: int = 12) -> dict[str, Any]:
    """无 API Key 时的启发式情绪评估。有 Key 后由模型输出同样的结构替代。"""
    t = text.lower()
    tags: list[str] = []
    notes: list[str] = []

    if any(k in t for k in _TEST):
        tags.append("test")
        notes.append("他在试探我是不是程序")
    if any(k in t for k in _COMPARE):
        tags.append("comparison")
        notes.append("他把我和别的东西比")
    if any(k in t for k in _ATTACK_SEVERE):
        tags.append("attack")
        notes.append("他在骂我")
    elif any(k in t for k in _CONFLICT):
        tags.append("conflict")
        notes.append("他在发脾气")
    if any(k in t for k in _SWEET):
        tags.append("sweetness")
        notes.append("他说了亲昵的话")
    if any(k in t for k in _DISCLOSE):
        tags.append("self_disclosure")
        notes.append("他主动说了自己的事")
    if any(k in t for k in _CARE):
        tags.append("care")
        notes.append("他问起我")
    if not tags and len(text) >= 25:
        tags.append("companionship")
        notes.append("他愿意多说几句")
    if not tags and any(k in t for k in _COMPANION_HINT):
        tags.append("companionship")
        notes.append("他来找我说话")

    # 群聊兜底：任何一轮正常对话都算"陪伴"，只是权重低。
    # 否则小群里聊了几十条，好感纹丝不动（之前就是这个 bug）。
    if not tags and len(text.strip()) >= 2:
        tags.append("companionship")
        notes.append("他跟我说话")

    valence = "negative" if {"test", "comparison", "conflict", "attack"} & set(tags) else (
        "positive" if tags else "neutral"
    )
    intensity = 0.35 + 0.12 * len(tags)
    if hour in _NIGHT:
        tags.append("night")
        intensity += 0.1

    return {
        "valence": valence,
        "intensity": round(min(1.2, intensity), 2),
        "tags": tags,
        "reason": "；".join(notes) or "普通的聊天",
    }


def strip_actions(text: str) -> str:
    """模型偶尔会输出括号动作，去掉。"""
    return re.sub(r"[（(][^）)]{1,20}[）)]", "", text).strip()
