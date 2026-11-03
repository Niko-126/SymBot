"""点歌翻唱管线：搜歌 → 下载 → 人声分离 → RVC 换声 → 混音 → mp3 + QQ 语音条。

上层用法（QQ 里点歌时）::

    from app.voice import cover

    job = cover.run_cover("雨爱", progress=lambda s: say(f"…{s}…"))
    if job["ok"]:
        send_file(job["mp3"])        # 整首翻唱
        send_file(job["clip_amr"])  # 55 秒语音条（最响的那段，大概率是副歌）
    else:
        say(f"唱不了：{job['reason']}")

纪律：
- **同步阻塞**纯本地；调用方自己 ``await asyncio.to_thread(cover.run_cover, …)``，别堵主链。
- 任何失败都返回 ``{"ok": False, "reason": …, "detail": …}``，绝不抛异常。
- 中间产物全在 ``workdir`` 里（默认 ``data/covers/<时间戳-歌名>/``），留着方便复听和换参数重跑。
- 只读配置（``cfg.voice``）：``base_url`` / ``api_key`` / ``voice`` / ``cover_enabled`` / ``cover_max_wait_s``。

已知坑（2026-11-02 实测）：
- demucs 自带的 ``torchaudio.save`` 在这台机器上要 TorchCodec（装不上），
  所以分离脚本用 ``soundfile`` 读写 + ``demucs.apply_model`` 自己算。
- B 站搜索接口 ``/x/web-interface/search/type`` **不带 Referer 会返 412**；带上就 200。
  万一哪天连 Referer 也过不去，自动降级走 wbi 签名的 ``/x/web-interface/wbi/search/type``。
- RVC 输出是 **40 kHz 单声道**，混音时统一 aresample 到 44100 再对齐。
﻿‌﻿﻿﻿‍‍‌​‌​‍‌‍​‌﻿﻿​‍‍﻿﻿﻿‌﻿‍‍‍‌​﻿‍﻿"""

from __future__ import annotations

import html
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Callable
from typing import Any

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
_BILI_REFERER = "https://www.bilibili.com/"
_BILI_ORIGIN = "https://www.bilibili.com"

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_EM_RE = re.compile(r"</?em[^>]*>")
_BAD_NAME_RE = re.compile(r'[\\/:*?"<>|\s]+')
_VOL_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")

_MAX_SECONDS = 8 * 60      # 超过 8 分钟的视频基本不是"一首歌"，粗排时压下去
_CLIP_DEFAULT_S = 55.0     # QQ 语音条上限约 60 秒，留点余量
_MIN_CLIP_S = 5.0

_DEFAULT_BASE = "http://127.0.0.1:9880"
_SEP_PY_DEFAULT = r"C:\feiyu\sep\Scripts\python.exe"
_SEP_ENV = ("SYMEM_SEP_PY", "FEIYU_SEP_PY")

# 分离脚本：由本模块在 workdir 里生成，再用「分离环境」的解释器执行。
# 刻意不走 demucs CLI（它内部会 torchaudio.save → 要 TorchCodec）。
_SEP_SCRIPT = '''# -*- coding: utf-8 -*-
"""demucs 分离（soundfile 读写，绕开 TorchCodec）。由 app/voice/cover.py 自动生成。"""
from __future__ import annotations

import pathlib
import sys

import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"device {device}")
model = get_model("htdemucs")
model.eval()
model.to(device)

data, sr = sf.read(str(src), always_2d=True, dtype="float32")
if sr != model.samplerate:
    raise SystemExit(f"sample rate {sr} != model {model.samplerate}")
print(f"input {data.shape[0] / sr:.1f}s {sr}Hz {data.shape[1]}ch")

wav = torch.from_numpy(data.T.copy())
ref = wav.mean(0)
wav = (wav - ref.mean()) / (ref.std() + 1e-8)

with torch.no_grad():
    sources = apply_model(model, wav[None], device=device, shifts=1, split=True,
                          overlap=0.25, progress=True)[0]

sources = sources * ref.std() + ref.mean()
for name, stem in zip(model.sources, sources):
    p = out / f"{name}.wav"
    sf.write(str(p), stem.cpu().numpy().T, model.samplerate, subtype="PCM_24")
    print(f"stem {name} {p.stat().st_size}")
print("OK")
'''


# ------------------------------------------------------------------ 小工具

def _elapsed(t0: float) -> float:
    return round(time.monotonic() - t0, 1)


def _fail(reason: str, detail: Any = "", t0: float = 0.0, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "reason": str(reason or "unknown"),
        "detail": str(detail or "")[:500],
        "cost_s": _elapsed(t0) if t0 else 0.0,
    }
    out.update(extra)
    return out


def _emit(progress: Callable[[str], Any] | None, text: str) -> None:
    """给上层发一句进度；回调自己炸了也不能拖垮管线。"""
    if progress is None:
        return
    try:
        progress(text)
    except Exception:  # noqa: BLE001 - 进度糖，失败忽略
        return


def _run(cmd: list[str], *, timeout: float, cwd: pathlib.Path | None = None) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, 合并输出的尾巴)。永远不抛。"""
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5.0, float(timeout)),
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return 127, f"FileNotFoundError: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout:.0f}s: {cmd[0]}"
    except OSError as exc:
        return 126, f"{type(exc).__name__}: {exc}"
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if len(tail) > 3000:
        tail = tail[-3000:]
    return proc.returncode, tail


def _ffmpeg() -> str:
    """优先用随包 ffmpeg（tools/ffmpeg.exe），其次 PATH 里的。"""
    try:
        from app.core.paths import candidate_roots

        for base in candidate_roots():
            cand = pathlib.Path(base) / "tools" / "ffmpeg.exe"
            if cand.is_file():
                return str(cand)
    except Exception:  # noqa: BLE001 - 拿不到项目路径就退回 PATH
        return shutil.which("ffmpeg") or "ffmpeg"
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    exe = _ffmpeg()
    if exe.endswith("ffmpeg.exe"):
        cand = exe[: -len("ffmpeg.exe")] + "ffprobe.exe"
        if pathlib.Path(cand).is_file():
            return cand
    return shutil.which("ffprobe") or "ffprobe"


def _python() -> str:
    return sys.executable or shutil.which("python") or "python"


def _sep_python() -> str:
    for name in _SEP_ENV:
        cand = (os.environ.get(name) or "").strip()
        if cand and pathlib.Path(cand).is_file():
            return cand
    if pathlib.Path(_SEP_PY_DEFAULT).is_file():
        return _SEP_PY_DEFAULT
    return ""


def _duration(path: pathlib.Path) -> float:
    """读容器时长（秒）；读不到返回 0.0。"""
    rc, out = _run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        timeout=30.0,
    )
    if rc != 0:
        return 0.0
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            val = float(line)
        except ValueError:
            continue
        return val if val > 0 else 0.0
    return 0.0


def _slug(name: str) -> str:
    s = _BAD_NAME_RE.sub("_", (name or "").strip())[:40].strip("_.")
    return s or "cover"


def _default_workdir(name: str) -> pathlib.Path:
    from app.core.paths import DATA_ROOT

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return pathlib.Path(DATA_ROOT) / "data" / "covers" / f"{stamp}-{_slug(name)}"


# ------------------------------------------------------------------ 搜歌

def _clean_title(raw: str) -> str:
    return html.unescape(_EM_RE.sub("", raw or "")).strip()


def _dur_seconds(text: str) -> float:
    """B 站搜索返回的时长是 "4:23" / "1:02:03" 这种字符串。"""
    parts = [p for p in str(text or "").split(":") if p.strip()]
    if not parts:
        return 0.0
    total = 0.0
    try:
        for part in parts:
            total = total * 60 + float(part)
    except ValueError:
        return 0.0
    return total


def _pick_fields(items: Any, limit: int) -> list[dict[str, Any]]:
    """把 B 站原始条目洗成 {"title","bvid","url","duration","author"}（+ duration_s）。"""
    out: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        bvid = str(raw.get("bvid") or "").strip()
        if not bvid:
            continue
        dur = str(raw.get("duration") or "")
        out.append({
            "title": _clean_title(str(raw.get("title") or "")),
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "duration": dur,
            "duration_s": _dur_seconds(dur),
            "author": _clean_title(str(raw.get("author") or "")),
        })
        if len(out) >= limit:
            break
    return out


def _search_plain(query: str, limit: int) -> list[dict[str, Any]]:
    """公开搜索接口。带 Referer 是硬要求——不带就是 412 风控页。"""
    try:
        import httpx

        with httpx.Client(
            timeout=15.0,
            trust_env=False,
            follow_redirects=True,
            headers={
                "User-Agent": _UA,
                "Referer": _BILI_REFERER,
                "Origin": _BILI_ORIGIN,
                "Accept": "application/json, text/plain, */*",
            },
        ) as client:
            resp = client.get(_SEARCH_API, params={"search_type": "video", "keyword": query, "page": 1})
            if resp.status_code != 200:
                return []
            try:
                payload = resp.json()
            except ValueError:
                return []
    except Exception:  # noqa: BLE001 - 断网/超时都算"没搜到"
        return []
    try:
        if int(payload.get("code") or 0) != 0:
            return []
    except (TypeError, ValueError):
        return []
    return _pick_fields((payload.get("data") or {}).get("result"), limit)


def _search_wbi(query: str, limit: int) -> list[dict[str, Any]]:
    """降级通道：复用仓库里已有的 wbi 签名实现（app/web/bili.py，只读不改）。"""
    try:
        from app.web import bili

        return _pick_fields(bili.search_videos(query, limit), limit)
    except Exception:  # noqa: BLE001
        return []


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """B 站搜视频。返回 [{title, bvid, url, duration, author, duration_s}]；搜不到返回 []。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        lim = max(1, min(50, int(limit or 5)))
    except (TypeError, ValueError):
        lim = 5
    hits = _search_plain(q, lim)
    if hits:
        return hits
    return _search_wbi(q, lim)


def pick_best(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """粗排：标题包含关键词优先、时长 < 8 分钟优先、越靠前越优先。"""
    if not results:
        return None
    key = _norm(query)
    best: dict[str, Any] | None = None
    best_score = -1e18
    for idx, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        title = _norm(str(item.get("title") or ""))
        try:
            dur = float(item.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        score = 0.0
        if key and key in title:
            score += 100.0
        if title.startswith(key or "\0"):
            score += 10.0
        if dur > 0:
            if dur <= _MAX_SECONDS:
                score += 30.0
            else:
                score -= 60.0
            if dur < 30:
                score -= 40.0
        score -= idx * 0.5
        if score > best_score:
            best_score, best = score, item
    if best is None:
        return None
    out = dict(best)
    out["_score"] = round(best_score, 1)
    return out


def _title_of(bvid: str) -> str:
    """按 BV 号取标题（只有走 BV 号/直链时才需要，给文件起名和给用户报幕用）。"""
    if not _BV_RE.fullmatch(bvid or ""):
        return ""
    try:
        import httpx

        with httpx.Client(timeout=10.0, trust_env=False, headers={
            "User-Agent": _UA, "Referer": _BILI_REFERER,
        }) as client:
            resp = client.get("https://api.bilibili.com/x/web-interface/view",
                              params={"bvid": bvid})
            if resp.status_code != 200:
                return ""
            payload = resp.json() or {}
    except Exception:  # noqa: BLE001 - 取不到就算了，标题只是锦上添花
        return ""
    try:
        if int(payload.get("code") or 0) != 0:
            return ""
    except (TypeError, ValueError):
        return ""
    return _clean_title(str((payload.get("data") or {}).get("title") or ""))


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def resolve(target: str) -> dict[str, Any]:
    """把「BV 号 / 链接 / 关键词」统一成一条视频 URL。

    返回 {"ok": True, "url", "bvid", "title", "how"} 或 {"ok": False, "reason"}。
    """
    raw = (target or "").strip()
    if not raw:
        return {"ok": False, "reason": "empty-target", "detail": "点歌得给个歌名或 BV 号"}
    hit = _BV_RE.search(raw)
    if hit:
        bvid = hit.group(1)
        return {"ok": True, "url": f"https://www.bilibili.com/video/{bvid}", "bvid": bvid,
                "title": "", "how": "bvid"}
    low = raw.lower()
    if low.startswith(("http://", "https://")):
        return {"ok": True, "url": raw, "bvid": "", "title": "", "how": "url"}
    hits = search(raw, limit=5)
    if not hits:
        return {"ok": False, "reason": "not-found", "detail": f"B 站搜不到：{raw}", "candidates": []}
    best = pick_best(raw, hits)
    if not best:
        return {"ok": False, "reason": "not-found", "detail": f"搜到 {len(hits)} 条但挑不出：{raw}",
                "candidates": hits}
    return {"ok": True, "url": str(best["url"]), "bvid": str(best["bvid"]),
            "title": str(best.get("title") or ""), "how": "search",
            "score": float(best.get("_score") or 0.0),
            "matched": bool(_norm(raw) and _norm(raw) in _norm(str(best.get("title") or ""))),
            "candidates": hits}


# ------------------------------------------------------------------ 各步骤

def _load_cfg() -> Any:
    try:
        from app.core.config import load

        return load()
    except Exception:  # noqa: BLE001 - 配置读不到就全靠兜底值
        return None


def _download(url: str, workdir: pathlib.Path, *, timeout: float) -> dict[str, Any]:
    """yt-dlp 抽音频轨成 wav。"""
    dl = workdir / "dl"
    try:
        dl.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "reason": "io", "detail": str(exc)}
    cmd = [
        _python(), "-m", "yt_dlp",
        "--no-playlist", "--no-warnings", "--no-progress", "--newline",
        "--add-header", f"Referer:{_BILI_REFERER}",
        "-f", "bestaudio/best",
        "-x", "--audio-format", "wav", "--audio-quality", "0",
        "-o", str(dl / "source.%(ext)s"),
        url,
    ]
    rc, tail = _run(cmd, timeout=timeout)
    files = sorted(p for p in dl.glob("source.*") if p.is_file() and p.stat().st_size > 0)
    if rc != 0 or not files:
        return {"ok": False, "reason": "download-failed", "detail": tail[-400:]}
    return {"ok": True, "path": files[0], "detail": tail[-200:]}


def _resample(src: pathlib.Path, dst: pathlib.Path, *, timeout: float) -> dict[str, Any]:
    """统一成 44.1 kHz 立体声（demucs 和混音都按这个口径）。"""
    rc, tail = _run(
        [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(dst)],
        timeout=timeout,
    )
    if rc != 0 or not dst.is_file() or dst.stat().st_size <= 0:
        return {"ok": False, "reason": "ffmpeg-resample", "detail": tail[-400:]}
    return {"ok": True, "path": dst, "detail": tail[-200:]}


def _separate(src44: pathlib.Path, out_dir: pathlib.Path, *, timeout: float) -> dict[str, Any]:
    """demucs 四轨分离（子进程跑「分离环境」的解释器）。"""
    py = _sep_python()
    if not py:
        tried = ", ".join([*(os.environ.get(n, "") for n in _SEP_ENV), _SEP_PY_DEFAULT])
        return {"ok": False, "reason": "sep-missing",
                "detail": f"找不到分离环境的解释器（试过：{tried}）；可用 SYMEM_SEP_PY 覆盖"}
    script = out_dir.parent / "_separate_demucs.py"
    try:
        script.write_text(_SEP_SCRIPT, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "reason": "io", "detail": str(exc)}
    rc, tail = _run([py, str(script), str(src44), str(out_dir)], timeout=timeout)
    stems: dict[str, pathlib.Path] = {}
    for name in ("vocals", "drums", "bass", "other"):
        cand = out_dir / f"{name}.wav"
        if cand.is_file() and cand.stat().st_size > 0:
            stems[name] = cand
    if rc != 0 or "vocals" not in stems:
        return {"ok": False, "reason": "separate-failed", "detail": tail[-700:], "rc": rc}
    return {"ok": True, "stems": stems, "detail": tail[-300:]}


def _svc_settings(cfg: Any) -> tuple[str, str, str]:
    """(base_url, api_key, voice)。配置读不到就从 api_key.txt 兜底。"""
    box = getattr(cfg, "voice", None) if cfg is not None else None
    base = str(getattr(box, "base_url", "") or "").strip() or _DEFAULT_BASE
    key = str(getattr(box, "api_key", "") or "").strip()
    voice = str(getattr(box, "voice", "") or "").strip() or "feiyu"
    if not key:
        key = _key_from_file()
    return base.rstrip("/"), key, voice


def _key_from_file() -> str:
    """翻唱用的 key：和 TTS 共用同一套解析（环境变量 → 服务自己写的 api_key.txt）。"""
    from app.voice.auth import resolve_api_key

    return resolve_api_key()


def _rvc(cfg: Any, vocals: pathlib.Path, dest: pathlib.Path, *,
         voice: str, timeout: float) -> dict[str, Any]:
    """上传人声 → 提交换声 → 轮询 → 下载换声后的 WAV。"""
    base, key, dflt_voice = _svc_settings(cfg)
    if not key:
        return {"ok": False, "reason": "no-api-key",
                "detail": "声音服务 API key 没找到（配置 voice.api_key 或 api_key.txt）"}
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "no-httpx", "detail": str(exc)}

    job_id = ""
    asset_id = ""
    info: dict[str, Any] = {}
    try:
        with httpx.Client(base_url=base, timeout=90.0, trust_env=False,
                          headers={"Authorization": f"Bearer {key}"}) as client:
            with vocals.open("rb") as fh:
                up = client.post("/v1/assets", files={"file": (vocals.name, fh, "audio/wav")})
            if up.is_error:
                return {"ok": False, "reason": f"upload-{up.status_code}", "detail": up.text[:300]}
            asset_id = str((up.json() or {}).get("id") or "")
            if not asset_id:
                return {"ok": False, "reason": "no-asset-id", "detail": up.text[:300]}

            sub = client.post("/v1/voice-conversion",
                              json={"voice": (voice or dflt_voice), "audio_id": asset_id})
            if sub.is_error:
                return {"ok": False, "reason": f"convert-{sub.status_code}", "detail": sub.text[:300],
                        "asset_id": asset_id}
            job_id = str((sub.json() or {}).get("id") or "")
            if not job_id:
                return {"ok": False, "reason": "no-job-id", "detail": sub.text[:300],
                        "asset_id": asset_id}

            started = time.monotonic()
            status = "queued"
            while True:
                poll = client.get(f"/v1/jobs/{job_id}")
                if poll.is_error:
                    return {"ok": False, "reason": f"poll-{poll.status_code}",
                            "detail": poll.text[:300], "job_id": job_id}
                info = poll.json() or {}
                status = str(info.get("status") or "")
                if status == "success":
                    break
                if status in ("failed", "interrupted"):
                    return {"ok": False, "reason": f"rvc-{status}",
                            "detail": str(info.get("error") or "")[:400], "job_id": job_id}
                if time.monotonic() - started > timeout:
                    return {"ok": False, "reason": "rvc-timeout",
                            "detail": f"等了 {timeout:.0f}s，状态还是 {status or 'unknown'}",
                            "job_id": job_id}
                time.sleep(2.0)

            got = client.get(f"/v1/jobs/{job_id}/audio")
            if got.is_error:
                return {"ok": False, "reason": f"audio-{got.status_code}", "detail": got.text[:300],
                        "job_id": job_id}
            blob = got.content or b""
    except Exception as exc:  # noqa: BLE001 - 服务没起/超时/断连都当失败
        return {"ok": False, "reason": "rvc-error",
                "detail": f"{type(exc).__name__}: {exc}"[:300], "job_id": job_id,
                "base_url": base}

    if len(blob) < 1024:
        return {"ok": False, "reason": "audio-too-small", "detail": f"{len(blob)} bytes",
                "job_id": job_id}
    if blob[:4] != b"RIFF":
        return {"ok": False, "reason": "not-wav", "detail": repr(blob[:8]), "job_id": job_id}
    try:
        dest.write_bytes(blob)
    except OSError as exc:
        return {"ok": False, "reason": "io", "detail": str(exc), "job_id": job_id}

    meta = info.get("audio") or {}
    try:
        seconds = float(meta.get("seconds") or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    try:
        sample_rate = int(meta.get("sample_rate") or 0)
    except (TypeError, ValueError):
        sample_rate = 0
    return {"ok": True, "path": dest, "job_id": job_id, "asset_id": asset_id,
            "bytes": len(blob), "seconds": seconds, "sample_rate": sample_rate}


def _mean_db(path: pathlib.Path, *, timeout: float) -> float | None:
    """整轨平均电平（dBFS）。用来把换声人声的电平拉回原位。"""
    _rc, out = _run(
        [_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        timeout=max(20.0, min(90.0, timeout)),
    )
    hit = _VOL_RE.search(out or "")
    if not hit:
        return None
    try:
        return float(hit.group(1))
    except (TypeError, ValueError):
        return None


def _mix(vocals: pathlib.Path, stems: dict[str, pathlib.Path], out_mp3: pathlib.Path,
         *, timeout: float, vocal_gain_db: float = 2.0,
         auto_level: bool = True) -> dict[str, Any]:
    """drums+bass+other → 伴奏；叠换声人声（默认 +2 dB）→ mp3 192k。"""
    # RVC 出来的电平通常比 demucs 人声低一大截（2026-11-02 实测 -23.9 vs -16.6 dB），
    # 直接 +2 dB 会被人声压住 → 先把电平拉回原人声的位置，再按 vocal_gain_db 微调。
    gain_db = float(vocal_gain_db)
    auto_db = 0.0
    ref = stems.get("vocals")
    if auto_level and ref is not None and ref.is_file():
        cur = _mean_db(vocals, timeout=timeout / 3)
        org = _mean_db(ref, timeout=timeout / 3)
        if cur is not None and org is not None:
            auto_db = max(-12.0, min(9.0, org - cur))
    total_db = gain_db + auto_db

    inst_names = [n for n in ("drums", "bass", "other") if stems.get(n)]
    inputs = [str(vocals)] + [str(stems[n]) for n in inst_names]
    chain: list[str] = []
    if inst_names:
        if len(inst_names) == 1:
            chain.append(
                "[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[inst]"
            )
        else:
            labels = "".join(f"[{i + 1}:a]" for i in range(len(inst_names)))
            chain.append(
                f"{labels}amix=inputs={len(inst_names)}:duration=longest:normalize=0,"
                "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[inst]"
            )
        chain.append(
            "[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={total_db:.2f}dB[voc]"
        )
        chain.append(
            "[voc][inst]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.97[out]"
        )
    else:
        # 分离没给出伴奏（极端情况）：只放换声人声，也还能听
        chain.append(
            "[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={total_db:.2f}dB,alimiter=limit=0.97[out]"
        )
    cmd = [_ffmpeg(), "-y", "-loglevel", "error"]
    for path in inputs:
        cmd.extend(["-i", path])
    cmd.extend(["-filter_complex", ";".join(chain), "-map", "[out]",
                "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", str(out_mp3)])
    rc, tail = _run(cmd, timeout=timeout)
    if rc != 0 or not out_mp3.is_file() or out_mp3.stat().st_size <= 0:
        return {"ok": False, "reason": "mix-failed", "detail": tail[-500:]}
    return {"ok": True, "path": out_mp3, "detail": tail[-200:],
            "vocal_gain_db": round(total_db, 1), "auto_gain_db": round(auto_db, 1)}


def _rms_per_second(path: pathlib.Path, sr: int) -> list[float]:
    """每秒一格的平均 RMS（0–1）。numpy 没装就用 array 硬算。"""
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.readframes(w.getnframes())
    except (OSError, wave.Error):
        return []
    if not frames:
        return []
    try:
        import numpy as np

        data = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
        nsec = int(len(data) // sr)
        if nsec <= 0:
            return []
        block = data[: nsec * sr].reshape(nsec, sr)
        return [float(v) for v in np.sqrt((block.astype("float64") ** 2).mean(axis=1))]
    except ImportError:
        import array

        arr = array.array("h")
        arr.frombytes(frames)
        nsec = len(arr) // sr
        out: list[float] = []
        for i in range(nsec):
            base = i * sr
            acc = 0
            for v in arr[base: base + sr]:
                acc += v * v
            out.append(math.sqrt(acc / max(1, sr)) / 32768.0)
        return out


def _loudest_window(scan: pathlib.Path, total: float, window: float) -> tuple[float, float]:
    """扫每秒 RMS，取最响的连续 window 秒。返回 (起点秒, 长度秒)。"""
    cap = min(float(window), _CLIP_DEFAULT_S)
    if total > 0:
        cap = min(cap, total)
    rms = _rms_per_second(scan, 8000)
    if len(rms) < 2:
        return 0.0, max(_MIN_CLIP_S, cap)
    win = int(min(cap, len(rms)))
    win = max(int(_MIN_CLIP_S), win)
    if len(rms) <= win:
        return 0.0, float(len(rms))
    sums = [0.0]
    for v in rms:
        sums.append(sums[-1] + v)
    best_i, best_v = 0, -1e18
    for i in range(len(rms) - win + 1):
        score = (sums[i + win] - sums[i]) / win
        if score > best_v:
            best_i, best_v = i, score
    return float(best_i), float(win)


def _clip(src: pathlib.Path, out_amr: pathlib.Path, *, window: float,
          timeout: float) -> dict[str, Any]:
    """从成品里切一段 ≤55 秒的语音条（amr 12.2k，QQ 最认这个）。"""
    total = _duration(src)
    scan = src.parent / "_scan8k.wav"
    start, length = 0.0, min(float(window), _CLIP_DEFAULT_S)
    rc, tail = _run(
        [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", str(scan)],
        timeout=max(20.0, timeout),
    )
    if rc == 0 and scan.is_file():
        start, length = _loudest_window(scan, total, window)
    scan.unlink(missing_ok=True)
    if length <= 0:
        length = min(float(window), _CLIP_DEFAULT_S)
    rc, tail = _run(
        [_ffmpeg(), "-y", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src),
         "-ac", "1", "-ar", "8000", "-c:a", "libopencore_amrnb", "-b:a", "12.2k",
         str(out_amr)],
        timeout=max(20.0, timeout),
    )
    if rc != 0 or not out_amr.is_file() or out_amr.stat().st_size <= 0:
        return {"ok": False, "reason": "amr-failed", "detail": tail[-400:]}
    return {"ok": True, "path": out_amr, "start_s": round(start, 1),
            "clip_s": round(length, 1), "bytes": out_amr.stat().st_size}


# ------------------------------------------------------------------ 主流程

def run_cover(target: str, *, workdir: pathlib.Path | None = None,
              progress: Callable[[str], Any] | None = None,
              cfg: Any = None, voice: str = "",
              vocal_gain_db: float = 2.0, auto_level: bool = True) -> dict[str, Any]:
    """点歌 → 翻唱。同步阻塞；`progress` 每步回调一次（"下载中"/"分离人声"/"换声"/"混音"）。

    成功::

        {"ok": True, "title", "bvid", "url", "mp3", "clip_amr",
         "seconds", "clip_seconds", "clip_start_s", "cost_s", "workdir",
         "vocal_gain_db", "auto_gain_db", "steps": {...}}

    `vocal_gain_db` 是「规格里的 +2 dB」，`auto_gain_db` 是为了抵消 RVC 掉电平
    额外补的自动增益（实测约 +7 dB）；不想自动补就传 ``auto_level=False``。

    失败::

        {"ok": False, "reason": "...", "detail": "...", "cost_s": 0.0, ...}
    """
    t0 = time.monotonic()
    if cfg is None:
        cfg = _load_cfg()
    box = getattr(cfg, "voice", None) if cfg is not None else None
    if box is not None and not bool(getattr(box, "cover_enabled", True)):
        return _fail("disabled", "配置里 voice.cover_enabled=false", t0)
    try:
        limit = float(getattr(box, "cover_max_wait_s", 240) or 240) if box is not None else 240.0
    except (TypeError, ValueError):
        limit = 240.0
    deadline = t0 + max(60.0, limit)

    def budget(floor: float = 15.0) -> float:
        """这条管线最多还能花多少秒（给每个子步骤当 timeout）。"""
        return max(floor, deadline - time.monotonic())

    steps: dict[str, float] = {}

    # a) 认出是哪首歌
    found = resolve(target)
    if not found.get("ok"):
        return _fail(found.get("reason") or "not-found", found.get("detail", ""), t0,
                     candidates=found.get("candidates") or [])
    url = str(found["url"])
    bvid = str(found.get("bvid") or "")
    title = str(found.get("title") or "")
    if not title and bvid:
        title = _title_of(bvid)

    wd = pathlib.Path(workdir).expanduser() if workdir else _default_workdir(title or bvid or "cover")
    try:
        wd.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail("io", str(exc), t0)

    # b) 下载
    mark = time.monotonic()
    _emit(progress, "下载中")
    got = _download(url, wd, timeout=budget())
    steps["download_s"] = round(time.monotonic() - mark, 1)
    if not got["ok"]:
        return _fail(got["reason"], got.get("detail", ""), t0, step="download",
                     url=url, workdir=str(wd))
    src = pathlib.Path(got["path"])

    # c) 统一采样率
    mark = time.monotonic()
    src44 = wd / "src44.wav"
    res = _resample(src, src44, timeout=budget(30))
    steps["resample_s"] = round(time.monotonic() - mark, 1)
    if not res["ok"]:
        return _fail(res["reason"], res.get("detail", ""), t0, step="resample", workdir=str(wd))

    # d) 分离
    mark = time.monotonic()
    _emit(progress, "分离人声")
    sep = _separate(src44, wd / "stems", timeout=budget(30))
    steps["separate_s"] = round(time.monotonic() - mark, 1)
    if not sep["ok"]:
        return _fail(sep["reason"], sep.get("detail", ""), t0, step="separate", workdir=str(wd))
    stems: dict[str, pathlib.Path] = sep["stems"]

    # e) 换声
    mark = time.monotonic()
    _emit(progress, "换声")
    rvc = _rvc(cfg, stems["vocals"], wd / "vocals_feiyu.wav",
               voice=voice, timeout=budget(20))
    steps["rvc_s"] = round(time.monotonic() - mark, 1)
    if not rvc["ok"]:
        return _fail(rvc["reason"], rvc.get("detail", ""), t0, step="rvc",
                     job_id=rvc.get("job_id", ""), workdir=str(wd))

    # f) 混音
    mark = time.monotonic()
    _emit(progress, "混音")
    slug = _slug(title or bvid or "cover")
    mp3 = wd / f"{slug}.mp3"
    out = _mix(pathlib.Path(rvc["path"]), stems, mp3, timeout=budget(30),
               vocal_gain_db=vocal_gain_db, auto_level=auto_level)
    steps["mix_s"] = round(time.monotonic() - mark, 1)
    if not out["ok"]:
        return _fail(out["reason"], out.get("detail", ""), t0, step="mix", workdir=str(wd))

    # g) 语音条
    mark = time.monotonic()
    amr = wd / f"{slug}_clip55.amr"
    clip = _clip(mp3, amr, window=_CLIP_DEFAULT_S, timeout=budget(20))
    steps["clip_s"] = round(time.monotonic() - mark, 1)
    if not clip["ok"]:
        # 语音条失败不算整单失败：整首 mp3 已经是成品
        clip = {"ok": False, "path": None, "start_s": 0.0, "clip_s": 0.0, "bytes": 0}

    seconds = round(_duration(mp3), 1)
    return {
        "ok": True,
        "title": title or slug,
        "bvid": bvid,
        "url": url,
        "mp3": str(mp3),
        "mp3_bytes": mp3.stat().st_size,
        "clip_amr": str(clip.get("path") or ""),
        "clip_bytes": int(clip.get("bytes") or 0),
        "clip_start_s": float(clip.get("start_s") or 0.0),
        "clip_seconds": float(clip.get("clip_s") or 0.0),
        "seconds": seconds,
        "cost_s": _elapsed(t0),
        "workdir": str(wd),
        "rvc_job": rvc.get("job_id", ""),
        "vocal_gain_db": out.get("vocal_gain_db", vocal_gain_db),
        "auto_gain_db": out.get("auto_gain_db", 0.0),
        "steps": steps,
        "how": found.get("how", ""),
        "matched": bool(found.get("matched", False)),
    }


__all__ = ["pick_best", "resolve", "run_cover", "search"]
