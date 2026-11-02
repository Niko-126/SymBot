"""热梗库 / B 站搜索接口（panel/trend.html 用）。

与记忆的关系：热梗单独存 `gags` 表，**不写 memories**；
对话里只是偶尔给模型一段可选提示，不参与召回、不影响好感。
﻿‌﻿﻿‍​‍​​﻿‌﻿‍﻿‍‍​﻿‌‍​‍﻿‌‍‍‌‍‍‍‌﻿​﻿"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core import db
from app.core.runtime import get as get_runtime
from app.web import bili, gags, harvest

router = APIRouter(prefix="/v1/trend")


# ---------------------------------------------------------------- 搜索视频


@router.get("/search")
def trend_search(q: str = Query(..., min_length=1), limit: int = Query(12, ge=1, le=30)) -> dict[str, Any]:
    try:
        items = bili.search_videos(q, limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"B 站搜索失败：{exc}") from exc
    for v in items:
        gags.save_video(v)
    return {"ok": True, "keyword": q, "items": items, "total": len(items)}


class SendVideoIn(BaseModel):
    user_id: int
    bvid: str


@router.post("/send_video")
async def send_video(inp: SendVideoIn) -> dict[str, Any]:
    """把某个 B 站视频发到 QQ（先发卡片链接，再发文字标题）。"""
    from app.api.console import _openid_of, _qq_send

    info = bili.video_info(inp.bvid)
    if info is None:
        raise HTTPException(status_code=404, detail="查不到这个视频")
    qq = _openid_of(inp.user_id)
    text = f"{info['title']}\n{info['url']}"
    ok = await _qq_send({"user_id": qq, "message": text})
    from app.memory import store

    store.add_event("her", f"[视频] {info['title'][:40]} {info['url']}", source="console", user_id=inp.user_id)
    return {"ok": True, "sent": ok, "title": info["title"], "url": info["url"]}


# ---------------------------------------------------------------- 热梗库


@router.get("/stats")
def trend_stats() -> dict[str, Any]:
    # 在后台上抓取状态（只读）：上次成功/上次尝试/失败原因/今日次数/是否到点
    return {**gags.stats(), "harvest": harvest.status(get_runtime())}


@router.get("/gags")
def list_gags(limit: int = Query(100, ge=1, le=500), q: str = Query("")) -> dict[str, Any]:
    items = gags.list_gags(limit, q)
    return {"items": items, "total": len(items)}


@router.delete("/gags/{gag_id}")
def remove_gag(gag_id: int) -> dict[str, Any]:
    if not gags.delete_gag(gag_id):
        raise HTTPException(status_code=404, detail="没有这条热梗")
    return {"ok": True}


@router.post("/gags/clear")
def clear_gags(password: str = Query("")) -> dict[str, Any]:
    from app.api.routes import _dev_ok

    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    n = gags.clear_gags()
    return {"ok": True, "removed": n}


class HarvestIn(BaseModel):
    limit: int = 20
    keyword: str = ""


@router.post("/harvest")
def harvest_now(inp: HarvestIn) -> dict[str, Any]:
    """手动抓一轮热梗（同 `app/web/harvest.py`，后台每天也会自动跑一次）。"""
    return harvest.run(inp.limit, inp.keyword)


@router.get("/hint")
def hint() -> dict[str, Any]:
    """给前端/调试看：这段提示词会注入到她的对话里。"""
    return {"hint": gags.prompt_hint(), "sample": gags.list_gags(5)}


# ---------------------------------------------------------------- 联网查词


@router.get("/lookup")
def web_lookup(kw: str = Query(..., min_length=1), context: str = Query("")) -> dict[str, Any]:
    """查一个词（黑话表 → 缓存 → 百度 → Bing）。"""
    from app.web import lookup as lookup_mod

    return lookup_mod.lookup(kw, context)


@router.get("/glossary")
def glossary() -> dict[str, Any]:
    """内置 AI 圈黑话表。"""
    from app.web import lookup as lookup_mod

    return {"items": lookup_mod.AI_NICKNAMES, "total": len(lookup_mod.AI_NICKNAMES)}


@router.get("/learned")
def learned_list(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """自动学到的新词（按可信度排序）。"""
    from app.web import lookup as lookup_mod

    items = lookup_mod.learned_gags(limit)
    return {"items": items, "total": len(items)}


@router.delete("/learned/{gag_id}")
def learned_delete(gag_id: int) -> dict[str, Any]:
    from app.web import lookup as lookup_mod

    if not lookup_mod.delete_learned(gag_id):
        raise HTTPException(status_code=404, detail="没有这条")
    return {"ok": True}


# ---------------------------------------------------------------- 关键词对话触发


def match_search_intent(text: str) -> str | None:
    """判断用户是不是在让她"搜个视频"。返回关键词或 None。"""
    t = (text or "").strip()
    verbs = ("搜一下", "搜个", "搜一搜", "找一下", "找个视频", "发个视频", "来个视频", "b站搜", "B站搜", "帮我搜", "搜搜")
    for v in verbs:
        if v in t:
            kw = t.split(v, 1)[1].strip(" 一下个的，。！？")
            return kw or None
    return None
