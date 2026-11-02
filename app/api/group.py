"""群聊控制接口：静默 / 主动说话 / 昵称 / 待机状态。﻿‌‍​﻿﻿‌‌‍﻿‍﻿﻿‌‌﻿﻿‌​﻿​​​‍‍﻿‌﻿‍﻿﻿﻿‌﻿"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.runtime import get as get_runtime
from app.group import active as gactive
from app.group import context as gctx
from app.group import intro as gintro
from app.group import proactive as gpro

router = APIRouter(prefix="/v1/group")


class IntroIn(BaseModel):
    group_id: Any = None
    mention_all: bool = True


@router.post("/introduce")
async def introduce(inp: IntroIn) -> dict[str, Any]:
    """让她在群里做自我介绍，可 @所有人。"""
    rt = get_runtime()
    gid = inp.group_id
    if gid in ("", 0):
        gid = None
    return await gintro.send_intro(rt, rt.persona, gid, inp.mention_all)


@router.get("/status")
def group_status(group_id: str = "") -> dict[str, Any]:
    from app.group import context as ctx

    return ctx.standby_state(group_id)


class SilenceIn(BaseModel):
    minutes: int = gpro.STOP_MINUTES
    reason: str = "手动"
    group_id: str = ""  # 留空 = 全局静默（单群场景）；填了 = 只静默这个群


@router.post("/silence")
def set_silence(inp: SilenceIn) -> dict[str, Any]:
    until = gpro.silence(inp.minutes, inp.reason, gid=inp.group_id)
    return {"ok": True, "until": until, "group_id": inp.group_id}


@router.post("/unsilence")
def clear_silence(group_id: str = "") -> dict[str, Any]:
    gpro.unsilence(group_id)
    return {"ok": True, "group_id": group_id}


@router.post("/speak")
async def speak_now() -> dict[str, Any]:
    """立刻让她主动说一句（忽略部分配额，但仍遵守静默）。"""
    from app.group import active as _ga

    _gid = (_ga._groups_seen() or [""])[0]
    silent, reason = gpro.is_silenced(_gid)
    if silent:
        return {"ok": False, "reason": reason}
    return await gactive.tick_once()


@router.post("/start")
async def start_active(interval: int = 600) -> dict[str, Any]:
    # async：同步端点跑在线程池里，没有运行中的事件循环，create_task 会炸
    started = gactive.start(interval)
    return {"ok": True, "started": started, "interval": interval}


@router.post("/stop")
async def stop_active() -> dict[str, Any]:
    gactive.stop()
    return {"ok": True}


@router.get("/nick/{user_id}")
def get_nick(user_id: int) -> dict[str, Any]:
    return {"user_id": user_id, "nick": gctx.nick_of(user_id)}


class NickIn(BaseModel):
    user_id: int
    nick: str


@router.post("/nick")
def set_nick(inp: NickIn) -> dict[str, Any]:
    n = gctx.set_nick(inp.user_id, inp.nick, by="user")
    if not n:
        raise HTTPException(status_code=400, detail="昵称不能为空")
    return {"ok": True, "nick": n}


@router.post("/nick/{user_id}/suggest")
def suggest_nick(user_id: int) -> dict[str, Any]:
    return {"ok": True, "nick": gctx.suggest_nick(user_id)}
