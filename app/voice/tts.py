"""文字转语音：对接本地「声音服务」。

契约（与「肥鱼声音 / astrbot_plugin_feiyu_voice」的服务端一致，本项目**自己实现客户端**）：

    POST {base_url}/v1/speech   {"voice": "feiyu", "text": "...", "style": "cute"}
         → {"id": "<32hex>", "status": "pending"}
    GET  {base_url}/v1/jobs/{id}
         → {"status": "pending|running|success|failed|interrupted", "error": "..."}
    GET  {base_url}/v1/jobs/{id}/audio
         → WAV（RIFF）

服务端是第三方项目、没有授权再分发，所以**代码不进本仓库**：
用 `scripts/fetch_voice_service.ps1` 自己拉，或指向任何兼容这套接口的服务。

本模块的纪律：
- 默认关（`voice.enabled=false`），关着就一个字节都不出网；
- 不开线程池不代表能阻塞主链——调用方用 `asyncio.to_thread(synth_sync, ...)`；
- 任何失败都返回 `{"ok": False, "reason": …}`，绝不抛。
﻿‍​﻿​‍﻿‍‍﻿​﻿‌‌‍﻿﻿​﻿​‌‌‍‍﻿﻿﻿﻿‌﻿‌‍‌﻿"""

from __future__ import annotations

import hashlib
import json
import pathlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.voice.auth import resolve_api_key

_MAX_AUDIO_BYTES = 16 * 1024 * 1024

# 独立的合成线程池：TTS 走自己的一条线，**不跟聊天模型/其它 to_thread 抢默认线程池**。
_EXEC: Any = None
_EXEC_LOCK = threading.Lock()


def _cache_dir() -> pathlib.Path:
    from app.core.paths import DATA_ROOT

    return pathlib.Path(DATA_ROOT) / "data" / "voices" / "cache"


def cache_key(text: str, voice: str, style: str) -> str:
    """缓存键：同一句话 + 同一音色 + 同一风格 → 同一个文件（省算力）。"""
    raw = f"{voice}|{style}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def cache_path(cfg: Any, text: str, voice: str, style: str) -> pathlib.Path | None:
    box = getattr(cfg, "voice", None)
    if box is None or not bool(getattr(box, "cache_enabled", True)):
        return None
    p = _cache_dir() / f"{cache_key(text, voice, style)}.wav"
    return p if p.is_file() else None


def _rate_ok(cfg: Any) -> tuple[bool, str]:
    """每分钟最多 `voice.max_per_min` 条。用 kv 计数，跨进程也生效。"""
    box = getattr(cfg, "voice", None)
    cap = int(getattr(box, "max_per_min", 0) or 0) if box is not None else 0
    if cap <= 0:
        return True, ""
    try:
        from app.core import db

        bucket = "tts_rate:" + time.strftime("%Y%m%d%H%M")
        used = int(db.kv_get(bucket, "0") or 0)
        if used >= cap:
            return False, "rate-limited"
        db.kv_set(bucket, str(used + 1))
    except Exception:  # noqa: BLE001 - 计数失败不该挡住合成
        return True, ""
    return True, ""


def _executor(cfg: Any) -> Any:
    """懒加载一个**专属**线程池（默认 2 个 worker）。"""
    global _EXEC
    if _EXEC is not None:
        return _EXEC
    with _EXEC_LOCK:
        if _EXEC is None:
            workers = int(getattr(getattr(cfg, "voice", None), "workers", 2) or 2)
            _EXEC = ThreadPoolExecutor(max_workers=max(1, min(4, workers)),
                                       thread_name_prefix="tts")
    return _EXEC


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def available(cfg: Any) -> tuple[bool, str]:
    """现在能不能合成。返回 (可用?, 原因)。"""
    box = getattr(cfg, "voice", None)
    if box is None or not bool(getattr(box, "enabled", False)):
        return False, "voice-disabled"
    try:
        if bool(cfg.provider.is_mock):
            # 离线测试：不连本地服务（它可能没起，也可能在别人机器上）
            return False, "mock"
    except AttributeError:
        return False, "mock"
    if not str(getattr(box, "base_url", "") or "").strip():
        return False, "no-endpoint"
    return True, ""


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _record_synth(text: str, seconds: float, *, ok: bool, cached: bool = False,
                  voice: str = "", style: str = "", reason: str = "") -> None:
    """记"上一条合成耗时"，给面板服务卡片看（owner 2026-11-02 要的）。

    只写一条 kv，失败了也不影响合成本身——观测数据不该拖垮主流程。
    """
    from datetime import datetime

    try:
        from app.core import db

        db.kv_set("voice_last_synth", json.dumps({
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "text": (text or "")[:40],
            "seconds": round(float(seconds), 2),
            "ok": bool(ok),
            "cached": bool(cached),
            "voice": voice,
            "style": style,
            "reason": reason,
        }, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - 记账失败就算了
        pass


def synth_sync(cfg: Any, text: str, *, voice: str = "", style: str = "",
               out_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """合成一段语音，落盘成 WAV。同步版（调用方负责丢线程）。"""
    ok, why = available(cfg)
    if not ok:
        return {"ok": False, "name": "tts", "reason": why}

    box = cfg.voice
    body = (text or "").strip()
    if not body:
        return {"ok": False, "name": "tts", "reason": "empty-text"}
    body = body[: max(1, int(getattr(box, "max_chars", 200) or 200))]
    _t0 = time.monotonic()

    base = str(getattr(box, "base_url", "") or "").rstrip("/")
    # key：配置 → 环境变量 → 服务自己写的 api_key.txt（不然 /v1/speech 必然 401）
    key = resolve_api_key(box)
    timeout = float(getattr(box, "timeout_s", 60.0) or 60.0)
    payload = {
        "voice": (voice or str(getattr(box, "voice", "feiyu") or "feiyu")).strip(),
        "text": body,
        "style": (style or str(getattr(box, "style", "cute") or "cute")).strip(),
    }

    # 命中缓存直接复用：省算力，也避免同一句话反复排队
    hit = cache_path(cfg, body, payload["voice"], payload["style"])
    if hit is not None:
        _record_synth(body, time.monotonic() - _t0, ok=True, cached=True,
                      voice=payload["voice"], style=payload["style"])
        return {"ok": True, "name": "tts", "path": str(hit), "bytes": hit.stat().st_size,
                "voice": payload["voice"], "style": payload["style"],
                "text": body, "cached": True}

    rate_ok, rate_why = _rate_ok(cfg)
    if not rate_ok:
        return {"ok": False, "name": "tts", "reason": rate_why}

    import httpx

    try:
        with httpx.Client(timeout=min(20.0, timeout), headers=_headers(key),
                          follow_redirects=False, trust_env=False) as client:
            r = client.post(f"{base}/v1/speech", json=payload)
            if r.is_error:
                try:
                    detail = str(r.json().get("detail") or "")[:120]
                except ValueError:
                    detail = ""
                return {"ok": False, "name": "tts", "reason": f"http-{r.status_code}",
                        "detail": detail}
            job = r.json()
            job_id = str(job.get("id") or "")
            if not job_id:
                return {"ok": False, "name": "tts", "reason": "no-job-id"}

            # 轮询到出结果（服务端是异步作业队列）
            started = time.monotonic()
            status = str(job.get("status") or "pending")
            while status not in ("success", "failed", "interrupted"):
                if time.monotonic() - started > timeout:
                    return {"ok": False, "name": "tts", "reason": "timeout", "job_id": job_id}
                time.sleep(1.5)
                poll = client.get(f"{base}/v1/jobs/{job_id}")
                if poll.is_error:
                    return {"ok": False, "name": "tts", "reason": f"poll-{poll.status_code}",
                            "job_id": job_id}
                info = poll.json()
                status = str(info.get("status") or "")
                if status in ("failed", "interrupted"):
                    return {"ok": False, "name": "tts", "reason": status,
                            "detail": str(info.get("error") or "")[:120], "job_id": job_id}

            audio = client.get(f"{base}/v1/jobs/{job_id}/audio")
            if audio.is_error:
                return {"ok": False, "name": "tts", "reason": f"audio-{audio.status_code}",
                        "job_id": job_id}
            data = audio.content or b""
    except Exception as exc:  # noqa: BLE001 - 服务没起/超时都当"没合成"
        return {"ok": False, "name": "tts", "reason": f"{type(exc).__name__}: {exc}"[:120]}

    if len(data) > _MAX_AUDIO_BYTES:
        return {"ok": False, "name": "tts", "reason": "too-large", "job_id": job_id}
    if data[:4] != b"RIFF":
        return {"ok": False, "name": "tts", "reason": "not-wav", "job_id": job_id}

    if out_dir is None:
        from app.core.paths import DATA_ROOT

        out_dir = pathlib.Path(DATA_ROOT) / "data" / "voices" / "tts"
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:6]
    path = out_dir / f"{_now_stamp()}-{suffix}.wav"
    path.write_bytes(data)

    # 回写缓存（缓存目录是独立的一份，跟 tts/ 下的成品分开）
    keep = cache_path(cfg, body, payload["voice"], payload["style"])  # 通常 None（还没写）
    cache_hit = None
    try:
        if bool(getattr(box, "cache_enabled", True)):
            cdir = _cache_dir()
            cdir.mkdir(parents=True, exist_ok=True)
            cpath = cdir / f"{cache_key(body, payload['voice'], payload['style'])}.wav"
            cpath.write_bytes(data)
            cache_hit = str(cpath)
    except OSError:
        cache_hit = None
    _ = keep

    _record_synth(body, time.monotonic() - _t0, ok=True, cached=False,
                  voice=payload["voice"], style=payload["style"])
    return {"ok": True, "name": "tts", "path": str(path), "bytes": len(data),
            "voice": payload["voice"], "style": payload["style"],
            "text": body, "job_id": job_id, "cache_path": cache_hit}


async def synth(cfg: Any, text: str, *, voice: str = "", style: str = "",
                out_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """异步版：走**专属线程池**，不跟聊天主链争默认线程池。"""
    import asyncio
    import functools

    ok, why = available(cfg)
    if not ok:
        return {"ok": False, "name": "tts", "reason": why}
    loop = asyncio.get_running_loop()
    fn = functools.partial(synth_sync, cfg, text, voice=voice, style=style, out_dir=out_dir)
    return await loop.run_in_executor(_executor(cfg), fn)


def cleanup_cache(cfg: Any, *, now: float | None = None) -> dict[str, Any]:
    """按 `voice.cache_ttl_days` 清缓存。0 = 不清理。

    返回 {"ok", "removed", "kept", "ttl_days"}；任何异常都当"没清"（不影响启动）。
    """
    box = getattr(cfg, "voice", None)
    ttl = int(getattr(box, "cache_ttl_days", 15) or 0) if box is not None else 0
    if ttl <= 0:
        return {"ok": True, "removed": 0, "kept": 0, "ttl_days": 0}
    cutoff = (time.time() if now is None else float(now)) - ttl * 86400
    removed = kept = 0
    try:
        cdir = _cache_dir()
        if not cdir.is_dir():
            return {"ok": True, "removed": 0, "kept": 0, "ttl_days": ttl}
        for p in cdir.glob("*.wav"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
                else:
                    kept += 1
            except OSError:
                continue
    except OSError:
        return {"ok": False, "reason": "io", "removed": removed, "kept": kept, "ttl_days": ttl}
    return {"ok": True, "removed": removed, "kept": kept, "ttl_days": ttl}

def status(cfg: Any) -> dict[str, Any]:
    """给面板看的状态快照：开没开、缓存放了几个、限流额度。"""
    box = getattr(cfg, "voice", None)
    ok, why = available(cfg)
    cached = 0
    try:
        cdir = _cache_dir()
        if cdir.is_dir():
            cached = sum(1 for _ in cdir.glob("*.wav"))
    except OSError:
        cached = 0
    cap = int(getattr(box, "max_per_min", 0) or 0) if box is not None else 0
    used = 0
    if cap > 0:
        try:
            from app.core import db

            used = int(db.kv_get("tts_rate:" + time.strftime("%Y%m%d%H%M"), "0") or 0)
        except Exception:  # noqa: BLE001
            used = 0
    return {
        "available": bool(ok),
        "reason": why,
        "enabled": bool(getattr(box, "enabled", False)) if box is not None else False,
        "send_in_chat": bool(getattr(box, "send_in_chat", False)) if box is not None else False,
        "cache_enabled": bool(getattr(box, "cache_enabled", True)) if box is not None else False,
        "cached_files": cached,
        "max_per_min": cap,
        "used_this_minute": used,
        "workers": int(getattr(box, "workers", 2) or 2) if box is not None else 2,
        "cache_ttl_days": int(getattr(box, "cache_ttl_days", 15) or 0) if box is not None else 0,
    }


__all__ = ["available", "cache_key", "cache_path", "cleanup_cache", "status", "synth", "synth_sync"]
