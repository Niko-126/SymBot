"""群聊主动调度：定期检查是否该开口，通过 OneBot 发到群里。

只在满足全部条件时开口：
- 未被静默（用户说过"别发了"）
- 配额 OK（非夜间、冷却过、今天没超）
- 群有历史可参照（不空谈）
- 连续几条都是别人在聊且她没参与（避免插话）
﻿‌﻿​﻿﻿‌﻿‌﻿​​‌​​‌‌‌​‍​‌‍​​​‍‌‌‌​‌​﻿"""

from __future__ import annotations

import asyncio
import random
import threading
from typing import Any

import httpx

from app.core import db
from app.core.runtime import get as get_runtime
from app.group import context as gctx
from app.group import proactive as gpro
from app.memory import store

_task: asyncio.Task | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


async def _send_group(group_id: Any, text: str) -> bool:
    cfg = get_runtime().cfg.onebot
    base = str(cfg.api_base).rstrip("/")
    headers = {"Authorization": f"Bearer {cfg.access_token}"} if cfg.access_token else {}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            r = await client.post(
                f"{base}/send_group_msg",
                json={"group_id": group_id, "message": [{"type": "text", "data": {"text": text}}]},
                headers=headers,
            )
            if not r.is_success:
                return False
            return str((r.json() or {}).get("status")) == "ok"
    except Exception:  # noqa: BLE001
        return False


def _groups_seen() -> list[Any]:
    """从 kv 里读最近有互动的 group_id（OneBot 上报时记下）。"""
    raw = db.kv_get("onebot_groups", "") or ""
    return [g for g in raw.split(",") if g.strip()]


def remember_group(group_id: Any) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    groups = _groups_seen()
    if gid in groups:
        groups.remove(gid)
    groups.insert(0, gid)
    db.kv_set("onebot_groups", ",".join(groups[:5]))


def _should_speak(gid: Any = "") -> tuple[bool, str]:
    silent, reason = gpro.is_silenced(gid)
    if silent:
        return False, reason
    # 连续 2~3 条没人接 → 立刻闭嘴，不再主动
    quiet, why_quiet = gpro.should_be_quiet(gid)
    if quiet:
        return False, f"没人接话（{why_quiet}），进入待机"
    ok, why = gpro.quota_ok(gid)
    if not ok:
        return False, why
    if not _groups_seen():
        return False, "还没有群互动记录"
    # 最近 10 分钟内她已说过话 → 不插话（按群看，别被别的群的主动卡住）
    g = str(gid or "").strip()
    if g:
        last_her = db.one(
            "SELECT ts FROM events WHERE role='her' AND source='group_proactive'"
            " AND group_id = ? ORDER BY id DESC LIMIT 1", (g,)
        )
    else:
        last_her = db.one(
            "SELECT ts FROM events WHERE role='her' AND source='group_proactive'"
            " ORDER BY id DESC LIMIT 1"
        )
    if last_her:
        from datetime import datetime

        try:
            gap = (gpro.now() - datetime.fromisoformat(str(last_her["ts"]))).total_seconds() / 60
            if gap < 30:
                return False, "刚主动过"
        except ValueError:
            pass
    return True, ""


async def tick_once() -> dict[str, Any]:
    """检查一次，满足条件就主动说一句。"""
    groups = _groups_seen()
    if not groups:
        return {"spoke": False, "reason": "还没有群互动记录"}
    group_id = groups[0]
    ok, why = _should_speak(group_id)
    if not ok:
        return {"spoke": False, "reason": why}

    topic = gpro.build_topic(group_id)
    if topic is None:
        return {"spoke": False, "reason": "没有合适话题"}

    sent = await _send_group(group_id, topic["text"])
    if sent:
        gpro.mark_proactive(group_id)
        store.add_event("her", topic["text"], source="group_proactive", chat_type="group",
                        group_id=str(group_id or ""))
        return {"spoke": True, "group_id": group_id, "kind": topic["kind"], "text": topic["text"]}
    return {"spoke": False, "reason": "发送失败"}


async def loop(interval_seconds: int = 600) -> None:
    """后台循环：每 10 分钟检查一次，是否开口由配额与话题决定。"""
    while not _stop.is_set():
        try:
            await tick_once()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(max(120, interval_seconds))


def start(interval_seconds: int = 600) -> bool:
    """启动后台调度（重复调用安全）。

    正常走事件循环（FastAPI 的 async 端点、OneBot 异步处理）；
    如果没有运行中的循环（CLI、同步脚本、线程池里调的同步端点），退化成
    daemon 线程自己跑一个循环——不然 `create_task` 会抛 "no running event loop"。
    """
    global _task, _thread
    if (_task is not None and not _task.done()) or (_thread is not None and _thread.is_alive()):
        return False
    _stop.clear()
    try:
        _task = asyncio.create_task(loop(interval_seconds))
    except RuntimeError:
        _thread = threading.Thread(target=lambda: asyncio.run(loop(interval_seconds)),
                                   name="group-active", daemon=True)
        _thread.start()
    return True


def stop() -> None:
    global _task
    _stop.set()
    if _task is not None:
        _task.cancel()
        _task = None
