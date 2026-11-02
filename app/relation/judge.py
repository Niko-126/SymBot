"""语言分析式情感评估（多信号）。

规则词表只能看"说了什么词"；这里让模型看"怎么说的"：
语气、意图、是否被尊重、有没有把对方当人、前后是否一致。

输出四个方向的增量建议，由 engine 统一裁剪、封顶、落库。
失败就返回 None，整体退回规则评估——宁可用旧机制，也不让错误判断写进关系。
﻿‍​﻿‍‍‌‍​﻿​‌‍‌﻿﻿﻿﻿​‍﻿​‍‌﻿﻿﻿﻿‌​‌‌‌﻿"""

from __future__ import annotations

import json
import re

_SYSTEM = (
    "你在判断一段对话对关系的影响。只输出 JSON，不要解释。\n"
    "结构：{\"valence\":\"positive|negative|neutral\",\"intensity\":0-1,"
    "\"tags\":[\"attack|conflict|test|comparison|sweetness|self_disclosure|care|companionship\"],"
    "\"affinity\":-3..3,\"trust\":-3..3,\"attraction\":-3..3,\"aversion\":-4..4,"
    "\"reason\":\"一句中文，20字以内，说明为什么加/减\"}\n"
    "规则：\n"
    "- 只评估【他说的话】对关系的影响；她说的话只是背景，不能因为她答得好就给他加分。\n"
    "- 好感看亲近与投入；信任看是否坦诚、是否被当人；心动看亲密表达；厌恶看是否被冒犯。\n"
    "- 不要因为用词礼貌就给高分，也不要因为一句话短就不给分。\n"
    "- 一次普通寒暄三项都接近 0；明显的攻击/试探/比较才给负分。\n"
    "- 普通调侃、开玩笑、问「你是不是AI」不要给 aversion 正数；只有明确辱骂/威胁才给。\n"
    "- aversion 正数表示厌恶增加，负数表示缓和。\n"
)


def _clamp(v, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 0.0


async def judge(rt, user_text: str, her_reply: str, relation: dict, aversion: float,
                session_seq: int = 1) -> dict | None:
    """返回 {valence,intensity,tags,affinity,trust,attraction,aversion,reason} 或 None。"""
    if getattr(rt.cfg.provider, "is_mock", True):
        return None

    brief = (
        f"当前：好感{relation.get('affinity', 0)} 信任{relation.get('trust', 0)} "
        f"心动{relation.get('attraction', 0)} 厌恶{aversion}\n"
        f"【他说】（要评估的是这句）：{user_text}\n【她回】（只作背景）：{her_reply}"
    )
    try:
        from app.mind import models

        spec = models.pick("summary")
        r = await rt.llm.chat(_SYSTEM, [{"role": "user", "content": brief}],
                              max_tokens=160, temperature=0.2, model=spec.provider_model,
                              thinking=False)
        models.record(spec.key, "summary", r.tokens_in, r.tokens_out, session_seq)
        match = re.search(r"\{.*\}", r.text or "", re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return None

    tags = [str(t) for t in (data.get("tags") or [])][:4]
    out = {
        "valence": str(data.get("valence", "neutral")),
        "intensity": _clamp(data.get("intensity", 0.4), 0.05, 1.2),
        "tags": tags,
        "affinity": _clamp(data.get("affinity", 0), -3, 3),
        "trust": _clamp(data.get("trust", 0), -3, 3),
        "attraction": _clamp(data.get("attraction", 0), -3, 3),
        "aversion": _clamp(data.get("aversion", 0), -4, 4),
        "reason": str(data.get("reason", ""))[:40],
    }
    # 空信号就当没判断出来，别写零分也不会更差
    if not tags and all(abs(out[k]) < 0.05 for k in ("affinity", "trust", "attraction", "aversion")):
        return None
    return out
