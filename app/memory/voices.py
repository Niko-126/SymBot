"""本地语音/录音素材：先用现成音频，不走语音模型。

用法（docs/24）：
- 把录音放 `data/voices/`，文件名即触发词，如 `晚安.mp3`、`想你.wav`、`笑.mp3`
- 也可以放 `data/voices/<场景>/`，如 `关心/多喝热水.mp3`
- OneBot 回复时若命中触发词，就追加一条 `record` 段（QQ 语音）

支持的格式：mp3 / wav / amr / silk / m4a / ogg。QQ 官方对语音消息通常是 silk/amr，
NapCat 多数版本会自动转码；若发送失败，先转成 amr/silk 再试（文档里有命令）。
﻿‍‍‍‌‍﻿﻿‌‌‍﻿‌‍‌‍﻿‌​‍‍​﻿‍‍﻿‍​‌‌​‍﻿﻿"""

from __future__ import annotations

import pathlib
from typing import Any

from app.core.paths import DATA_ROOT, RESOURCE_ROOT

AUDIO_EXT = (".mp3", ".wav", ".amr", ".silk", ".m4a", ".ogg", ".flac")
_SEARCH = (DATA_ROOT / "data" / "voices", RESOURCE_ROOT / "data" / "voices")

#: 这两个目录是 `app/voice/tts.py` 的合成缓存与产物，**不是**她准备好的语音素材。
#: 以前 `list_voices()` 用 rglob 会把它们一起扫进来：count 虚高、`names` 全是哈希名，
#: 更糟的是"唱歌"兜底会随机发一段缓存里的某句话（2026-11-02 实机踩到）。
_SKIP_DIRS = {"cache", "tts"}


def _dir() -> pathlib.Path | None:
    for base in _SEARCH:
        if base.exists():
            return base
    return None


def list_voices() -> list[pathlib.Path]:
    base = _dir()
    if base is None:
        return []
    out: list[pathlib.Path] = []
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXT:
            continue
        rel = p.relative_to(base)
        if rel.parts and rel.parts[0].lower() in _SKIP_DIRS:
            continue
        out.append(p)
    return sorted(out)


def stats() -> dict[str, Any]:
    base = _dir()
    items = list_voices()
    return {
        "dir": str(base or ""),
        "count": len(items),
        "names": [p.stem for p in items[:20]],
    }


# 唱歌/哼歌/情书/表演 这类请求 → 优先发 audio/<同名>目录里的歌
SING_HINTS = (
    "唱", "唱歌", "来一首", "来首歌", "唱首", "唱个", "哼", "哼歌", "歌", "情书",
    "表演", "发声", "语音", "声音", "听你", "唱给我",
)
# 明确要"唱"的词（要求她必须真发音频，而不是文字敷衍）
STRONG_SING = ("唱", "来一首", "唱首", "唱个", "哼歌", "情书", "唱给我")


def wants_audio(text: str) -> bool:
    haystack = (text or "").lower()
    return any(k in haystack for k in SING_HINTS)


def wants_sing(text: str) -> bool:
    haystack = (text or "").lower()
    return any(k in haystack for k in STRONG_SING)


def pick_for_text(text: str, scene: str | None = None) -> pathlib.Path | None:
    """命中顺序：1) 文件名出现触发词  2) 唱歌类请求 → 任一带音频  3) 场景目录。"""
    items = list_voices()
    if not items:
        return None
    haystack = (text or "").lower()
    # 1) 文件名即触发词（如 情书.m4a 命中"情书"）
    for path in items:
        if path.stem and path.stem.lower() in haystack:
            return path
    # 2) 唱歌/表演类请求：优先 `audio/` 或 `歌/` 目录
    if wants_audio(text):
        for folder in ("audio", "歌", "sing"):
            for path in items:
                if path.parent.name == folder:
                    return path
        # 没有专门的"歌"素材就返回 None：真正的点歌交给 app/voice/cover.py 去翻唱，
        # 别再随手甩一条触发词语音糊弄（那是"晚安"不是"歌"）。
        return None
    # 3) 场景目录
    if scene:
        for path in items:
            if path.parent.name == scene:
                return path
    return None


# ---------------------------------------------------------------- 格式兜底

def _ffmpeg() -> str | None:
    """优先用随包 ffmpeg（tools/ffmpeg.exe），其次 PATH 里的。"""
    import shutil

    for cand in (DATA_ROOT / "tools" / "ffmpeg.exe", RESOURCE_ROOT / "tools" / "ffmpeg.exe"):
        if cand.exists():
            return str(cand)
    return shutil.which("ffmpeg")


def to_amr(path: pathlib.Path) -> pathlib.Path | None:
    """把 mp3/m4a 等转成 amr（QQ 语音更稳）。没有 ffmpeg 就返回 None。"""
    exe = _ffmpeg()
    if exe is None:
        return None
    out = path.with_suffix(".amr")
    if out.exists() and out.stat().st_size > 0 and out.stat().st_mtime >= path.stat().st_mtime:
        return out
    import subprocess

    try:
        proc = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", str(path),
             "-ar", "8000", "-ac", "1", "-ab", "12.2k", str(out)],
            capture_output=True, timeout=180,
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def sendable(path: pathlib.Path) -> pathlib.Path:
    """发送前选 QQ 最稳的格式：amr/silk 直接用，否则尝试转 amr，失败就原样发。"""
    if path.suffix.lower() in (".amr", ".silk"):
        return path
    return to_amr(path) or path
