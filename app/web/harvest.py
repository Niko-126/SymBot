"""热梗自动抓取（路线 A）：后台每天抓一轮 B站热梗评论。

为什么要有这一层（而不是让面板点按钮）：
- 她的「最近流行怎么说」来自 `gags` 表，表里没新东西就会一直说旧梗；
- 抓取是慢操作（实测一轮 ~30s、上百次外网请求），必须离开请求线程；
- 失败要能退避、要能查到"上次成功是什么时候"，否则鼓包了没人知道。

设计约束（与 docs/28 离线兜底一致）：
- 只读公开接口：不登录、不带 cookie、不绕验证码；
- **mock（测试/无 key）直接跳过**：测试套件永远不联网；
- 每天最多一轮成功；失败 30 分钟后重试，单日最多 3 次；
- 状态全写 kv，接口只读；抓取在 `asyncio.to_thread` 里跑，不阻塞事件循环。
﻿‌‌‌‍‌‍‍​​​‌​‌﻿​﻿​‍﻿​​﻿​‌‌﻿﻿​‍​‌﻿﻿"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

from app.core import db
from app.web import bili, gags

KV_ENABLED = "gag_harvest_enabled"
KV_LAST_TRY = "gag_harvest_last_try"
KV_LAST_OK = "gag_harvest_last_ok"
KV_LAST_ERROR = "gag_harvest_last_error"
KV_TRIES_DAY = "gag_harvest_tries_day"
KV_TRIES = "gag_harvest_tries"

DEFAULT_INTERVAL_H = 24
RETRY_MINUTES = 30
MAX_TRIES_PER_DAY = 3
TICK_SECONDS = 600
STATUS_KEYS = (KV_ENABLED, KV_LAST_TRY, KV_LAST_OK, KV_LAST_ERROR, KV_TRIES_DAY, KV_TRIES)


def run(limit: int = 20, keyword: str = "") -> dict[str, Any]:
    """抓一轮「人怎么说话」：热梗/整活/沙雕类视频的高赞评论（同步、会阻塞网络）。

    逻辑与旧的 `POST /v1/trend/harvest` 完全一致，只是抽出来给后台复用。
    """
    added = 0
    sources = [keyword.strip()] if keyword.strip() else list(gags.MEME_SOURCES)
    videos: list[dict[str, Any]] = []
    for kw in sources:
        try:
            videos.extend(bili.search_videos(kw, min(20, max(6, limit))))
        except Exception:  # noqa: BLE001 - 单个源失败不影响其它源
            continue
    videos.sort(key=lambda v: int(v.get("play") or 0), reverse=True)
    seen: set[str] = set()
    scanned = 0
    empty = 0
    for v in videos[:60]:
        bvid = v.get("bvid") or ""
        if not bvid or bvid in seen:
            continue
        seen.add(bvid)
        gags.save_video(v)
        info = bili.video_info(bvid)
        aid = (info or {}).get("aid")
        if not aid:
            continue
        cs = bili.comments(int(aid), limit=20)
        scanned += 1
        if not cs:
            empty += 1
            continue
        for c in cs:
            if gags.add_gag(
                c["text"], source_title=v.get("title", ""), bvid=bvid,
                likes=c.get("likes", 0), kind="comment",
            ):
                added += 1
    return {
        "ok": True,
        "added": added,
        "videos": len(seen),
        "scanned": scanned,
        "empty": empty,
        "sources": sources,
        **gags.stats(),
    }


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _today() -> str:
    return _now().date().isoformat()


def _parse(ts: str) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def _config_interval(rt: Any) -> int:
    try:
        return max(1, int(getattr(rt.cfg.trend, "interval_hours", DEFAULT_INTERVAL_H)))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_INTERVAL_H


def status(rt: Any = None) -> dict[str, Any]:
    """只读状态：给面板/控制台看"上次成功是什么时候、下次什么时候试"。"""
    now = _now()
    last_ok = db.kv_get(KV_LAST_OK, "")
    last_try = db.kv_get(KV_LAST_TRY, "")
    tries_day = db.kv_get(KV_TRIES_DAY, "")
    tries = int(db.kv_get(KV_TRIES, "0") or 0)
    if tries_day != _today():
        tries = 0
    interval = _config_interval(rt) if rt is not None else DEFAULT_INTERVAL_H
    ok_at = _parse(last_ok)
    due = ok_at is None or (now - ok_at) >= _dt.timedelta(hours=interval)
    return {
        "enabled": _kv_enabled(rt),
        "mock": bool(getattr(rt.cfg.provider, "is_mock", False)) if rt is not None else None,
        "last_ok": last_ok,
        "last_try": last_try,
        "last_error": db.kv_get(KV_LAST_ERROR, ""),
        "tries_today": tries,
        "max_tries_per_day": MAX_TRIES_PER_DAY,
        "interval_hours": interval,
        "due": bool(due),
        "tick_seconds": TICK_SECONDS,
        "gags": gags.stats(),
    }


def _cfg_enabled(rt: Any) -> bool:
    try:
        return bool(getattr(rt.cfg.trend, "harvest_enabled", False))
    except (AttributeError, TypeError):
        return False


def _kv_enabled(rt: Any) -> bool:
    """kv 开关 `gag_harvest_enabled` 的唯一口径。

    空串 = 跟随配置（`trend.harvest_enabled`）；`0`/`false` = 关；其它 = 开。
    以前 `status()` 把空串当"关"、`_should_run()` 把空串当"开"，
    手工清空这个开关会得到互相矛盾的两票（docs/23 §3.1 第 13 条）。
    """
    raw = str(db.kv_get(KV_ENABLED, "") or "").strip().lower()
    if raw == "":
        return _cfg_enabled(rt)
    return raw not in ("0", "false")


def _should_run(rt: Any) -> tuple[bool, str]:
    """到点了吗？返回 (该跑吗, 原因)。"""
    if rt is None:
        return False, "no-runtime"
    if not _cfg_enabled(rt):
        return False, "disabled"
    if getattr(rt.cfg.provider, "is_mock", False):
        return False, "mock"  # 测试/无 key 永远不联网
    if not _kv_enabled(rt):
        return False, "kv-disabled"
    now = _now()
    today = _today()
    tries = int(db.kv_get(KV_TRIES, "0") or 0)
    if db.kv_get(KV_TRIES_DAY, "") != today:
        tries = 0
    if tries >= MAX_TRIES_PER_DAY:
        return False, "quota"
    last_ok = _parse(db.kv_get(KV_LAST_OK, ""))
    if last_ok is not None and (now - last_ok) < _dt.timedelta(hours=_config_interval(rt)):
        return False, "fresh"
    last_try = _parse(db.kv_get(KV_LAST_TRY, ""))
    if last_try is not None and (now - last_try) < _dt.timedelta(minutes=RETRY_MINUTES):
        return False, "backoff"
    return True, "due"


async def tick(rt: Any) -> dict[str, Any]:
    """后台一轮：到点就抓（放线程里），抓完写 kv。永不抛。"""
    ok, why = _should_run(rt)
    if not ok:
        return {"ran": False, "reason": why}
    now = _now()
    db.kv_set(KV_LAST_TRY, now.isoformat(timespec="seconds"))
    db.kv_set(KV_TRIES_DAY, _today())
    tries = int(db.kv_get(KV_TRIES, "0") or 0) + 1
    db.kv_set(KV_TRIES, str(tries))
    try:
        limit = int(getattr(rt.cfg.trend, "limit", 20))
        stats = await asyncio.to_thread(run, limit, "")
    except Exception as exc:  # noqa: BLE001 - 后台任务不许拖垮服务
        db.kv_set(KV_LAST_ERROR, f"{type(exc).__name__}: {exc}"[:300])
        return {"ran": True, "ok": False, "error": str(exc)[:200], "tries_today": tries}
    db.kv_set(KV_LAST_OK, _now().isoformat(timespec="seconds"))
    db.kv_set(KV_LAST_ERROR, "")
    return {"ran": True, "ok": True, "added": stats.get("added"), "videos": stats.get("videos"),
            "tries_today": tries}
