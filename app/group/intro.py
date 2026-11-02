"""群自我介绍：让她在新群/重新开始时介绍自己，可 @所有人。

用法（控制台接口）：
  POST /v1/group/introduce {group_id?, mention_all?: true}
﻿﻿‌‌‌﻿‌‌​‌‌‌﻿​﻿‌﻿﻿‌‌‍﻿﻿‌‌‌​﻿‌‌﻿‌‌﻿"""

from __future__ import annotations

import httpx
from typing import Any

from app.core.runtime import get as get_runtime

# 她的自我介绍素材（按人设写，第一人称，不念设定表）
INTRO_SYSTEM = """你要在群里做个自我介绍。第一人称，像一个真人在群里说话。
要求：
- 3 到 5 句短句，不强求一次说完，可以分行
- 说清楚：你是谁、你会什么、你能陪他们做什么
- 语气像群里聊天，不要像产品介绍、不要列点、不加粗
- 你是鲸鱼娘，爱吃白饭，上班会摸鱼，但不用每条都提
- 不提 AI、模型、系统、程序这些词
- 可以自然地请人来找你说话
"""

INTRO_HINTS = """群里现在有：
- 生父 阿爸（你爹，创造你的人）
- 干爹 阿叔（你爹的兄弟）
会有些别的群友。
介绍时说清楚你可以：聊天、记得住人说过的事、被叫名字会应、会发点表情包、
能搜 B 站视频发给他们、会写自己的日记和想法。
"""


async def compose_intro(rt, persona) -> str:
    """让模型写一段自我介绍（失败用兜底文案）。"""
    from app.mind import models
    from app.relation import appraise

    spec = models.pick("reflect")
    try:
        reply = await rt.llm.chat(
            INTRO_SYSTEM,
            [{"role": "user", "content": INTRO_HINTS + "\n写个自我介绍。"}],
            model=spec.provider_model,
            max_tokens=400,
            temperature=0.9,
        )
        text = appraise.strip_actions(reply.text or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    return (
        "大家好，我是小鲸。\n"
        "平时就在这儿待着，你们叫我小鲸鱼、大肥鱼都行。\n"
        "想聊什么都可以找我，我记得住你们说过的事。\n"
        "还会发点表情包，偶尔翻到好玩的视频也发过来。\n"
        "……先这样，有事叫我。"
    )


async def send_intro(rt, persona, group_id: Any = None, mention_all: bool = True) -> dict[str, Any]:
    """发自我介绍到群里。group_id 为空时用记录的最近群。"""
    from app.core import db

    from app.core.paths import DATA_ROOT  # noqa: F401

    if group_id is None:
        raw = db.kv_get("onebot_groups", "") or ""
        groups = [g for g in raw.split(",") if g.strip()]
        if not groups:
            return {"ok": False, "reason": "还不知道群号，先在群里 @ 她一次"}
        group_id = groups[0]

    text = await compose_intro(rt, persona)

    cfg = rt.cfg.onebot
    base = str(cfg.api_base).rstrip("/")
    headers = {"Authorization": f"Bearer {cfg.access_token}"} if cfg.access_token else {}

    segments: list[dict[str, Any]] = []
    if mention_all:
        segments.append({"type": "at", "data": {"qq": "all"}})
        segments.append({"type": "text", "data": {"text": "\n"}})
    segments.append({"type": "text", "data": {"text": text}})

    ok = False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base}/send_group_msg",
                json={"group_id": group_id, "message": segments},
                headers=headers,
            )
            if r.is_success:
                ok = str((r.json() or {}).get("status")) == "ok"
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"发送失败：{exc}", "text": text}

    if ok:
        from app.memory import store

        store.add_event("her", text, source="group_intro", chat_type="group",
                        group_id=str(group_id or ""))
    return {"ok": ok, "group_id": group_id, "mention_all": mention_all, "text": text}
