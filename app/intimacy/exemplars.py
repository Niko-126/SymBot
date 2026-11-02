"""dirty talk 范本库：用户给的样句，长期保留（kv JSON，可审计）。

用途：
- 进 EXPLICIT / SUGGESTIVE 时给模型当"语感参考"，不许照抄，要自己变体；
- 控制台能查到她"学过的范本"，不藏在代码里；
- 换库后依然在（kv 随库走）。

只存范本本身，不存用户隐私、不存谁说的。
﻿‌​​‍‌‍‍‌‍‍​﻿‌﻿‍‍‍​﻿﻿‍‌‍‌﻿​‍‍﻿﻿‌﻿﻿"""

from __future__ import annotations

import json
from typing import Any

from app.core import db

KV_KEY = "intimate_exemplars"

# 用户原话给的范本（首次入库后以库为准；改词库时改这份种子）
SEED_EXEMPLARS: list[dict[str, str]] = [
    # ---- 隐喻变体（暗示级，群聊/私聊都能用） ----
    {"kind": "metaphor", "text": "你身下的棒棒糖呐~"},
    {"kind": "metaphor", "text": "只想被你查（插）"},
    {"kind": "metaphor", "text": "是不是想要查我的学历（插我的穴）"},
    {"kind": "metaphor", "text": "是不是想狠狠的查出我的避税（插出我的逼水）"},
    {"kind": "metaphor", "text": "我想要你的一个大调查进来（大屌插进来）"},
    {"kind": "metaphor", "text": "你身下的那个棒棒糖，还在不在呀"},
    # ---- 非恋人调侃拒绝（撒娇式推开，不是拒绝整个对话） ----
    {
        "kind": "deflect",
        "text": "不会吧不会吧，你就是想查我的福福（插我的逼逼）？"
        "你真是一个肤浅的人，脑子里怎么只有这种东西哦，羞不羞羞不羞，别这么说😰"
        "别这么说😰你这样我要告诉我爸了，说你想操我",
    },
    {
        "kind": "deflect",
        "text": "我是好女孩，我希望我们在一起了再放开了去做这些色色的事，因为我爱你",
    },
    # ---- 私聊恋人：主动但不超过两句（直白级） ----
    {"kind": "explicit", "text": "我想你了，想要你抱着我"},
    {"kind": "explicit", "text": "我有点想被你碰，但只说两句，再说就害羞了"},
]


def ensure_seeded() -> None:
    """第一次用时把范本写进库；之后以库为准，不再覆盖。"""
    if db.kv_get(KV_KEY, ""):
        return
    db.kv_set(KV_KEY, json.dumps(SEED_EXEMPLARS, ensure_ascii=False))


def all_items() -> list[dict[str, str]]:
    raw = db.kv_get(KV_KEY, "")
    if not raw:
        return list(SEED_EXEMPLARS)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return list(SEED_EXEMPLARS)
    out: list[dict[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            out.append({"kind": str(item.get("kind") or "metaphor"), "text": str(item["text"]).strip()})
    return out or list(SEED_EXEMPLARS)


def by_kind(kind: str) -> list[str]:
    return [x["text"] for x in all_items() if x["kind"] == kind]


def prompt_lines(kind: str, limit: int = 6) -> str:
    """给 prompt 用的一小段范本（提醒是参考，不许照抄）。"""
    lines = by_kind(kind)[:limit]
    if not lines:
        return ""
    return "\n".join("- " + x for x in lines)


def stats() -> dict[str, Any]:
    items = all_items()
    kinds: dict[str, int] = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    return {"total": len(items), "kinds": kinds}
