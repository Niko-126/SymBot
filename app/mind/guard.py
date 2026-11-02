"""出戏守卫：拦住"作为一个人工智能…"这类句子。

她可以不知道自己是什么，但不能像一个产品那样解释自己。
守卫生成后的文本，命中就重写一次；重写还命中，就用人物自己的回避句式兜住。
﻿﻿‌﻿‌‍‍‍​﻿​‌‍​‌﻿﻿﻿​﻿‍﻿‌﻿‌‌​﻿‍‌​﻿﻿﻿"""

from __future__ import annotations

import re
from typing import Iterable

_BREAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ai_identity", re.compile(r"(作为|我是)\s*(一个|一名|个)?\s*(AI|人工智能|语言模型|大模型|智能助手|虚拟助手|chatgpt)", re.I)),
    ("model_talk", re.compile(r"(我的|本) ?(设定|人设|提示词|系统提示|system prompt)")),
    ("no_body", re.compile(r"(我)?没有(真实|真正)的(身体|感情|情绪|意识)")),
    ("training", re.compile(r"(我)(是被|由|经过).{0,8}(创造|开发|训练)")),
    ("meta_memory", re.compile(r"(根据|参考|基于)(我的|你的|我们的)?(记忆库|记录|数据|设定)")),
    ("assistant_tone", re.compile(r"(很高兴|乐意为您|有什么可以帮|希望这个回答|请问还有什么)")),
    ("disclaimer", re.compile(r"我(只|就)?是(一个|一段|一段段|个)?\s*(程序|机器|软件|代码|模型)")),
]

_SAFE_OPENERS = {"ai_identity", "model_talk", "disclaimer", "training", "no_body"}

#: 白天天说深夜话术（实测踩过：下午 14:51 回「这个点怎么还没睡」）
#:
#: 判据只收「对当前时间的断言」，不收「未来的劝睡」：
#: - 命中：还没睡 / 这么晚 / 太晚了 / 夜深了 / 大半夜 / 这个点了 —— 19 点说这些是错的
#: - 不命中：早点睡 / 该睡了 / 晚安 —— 傍晚说「早点睡吧」是人话（对抗训练实测误伤，见
#:   data/_adv 的 R10 用例），硬拦会把她改成不自然的样子
_NIGHT_TALK = re.compile(r"(还没睡|这么晚|太晚了|夜深了|大半夜|这个点了?|几点了还不睡)")


def time_check(text: str, hour: int) -> str | None:
    """时间感守卫：白天（7–21 点）出现深夜话术就判冲突；hour 非法时放行。"""
    if 7 <= int(hour) <= 21 and _NIGHT_TALK.search(text or ""):
        return "daytime_night_talk"
    return None


def time_hint(hour: int) -> str:
    return (
        f"上一句的时间感不对：现在是白天（约 {int(hour)} 点），不要用「还没睡」「这么晚」「早点睡」「晚安」这类话。"
        "重写：2 到 4 句，按白天的时间说话。"
    )


def check(text: str) -> str | None:
    """命中则返回违规类型，否则 None。"""
    for kind, pattern in _BREAK_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def strict_hint(violation: str) -> str:
    return (
        "上一句里出现了不该出现的东西（" + violation + "）。重写：\n"
        "- 不要提到 AI、模型、程序、设定、系统、记忆库、数据\n"
        "- 不要解释自己是什么\n"
        "- 用你平时的方式回应，2 到 4 句，句子更短\n"
        "- 如果对方在追问这个，用你自己的方式绕开"
    )


def deflect(persona_anchors: Iterable[str] | None = None, name: str = "她") -> str:
    """最后的兜底：用人物自己的口气把问题挡回去，而不是承认或否认。"""
    pool = [a for a in (persona_anchors or []) if a]
    for a in pool:
        if any(k in a for k in ("干嘛", "不知道", "别问", "认真")):
            return a
    return "……你问这个干嘛。"
