"""运行环境与依赖自检（面板「运行环境」卡片用）。

为什么要有：用户装完第一件事就是"为什么语音/翻唱不工作"——十次里有八次是某个可选包没装。
这里把 `requirements.txt` 的必装项和 NOTICE 里列的可选增强项都摊开，缺哪个当场标出来，
并且**只做 find_spec，不 import**：`demucs` 这种包 import 一次要好几秒，面板不能为它卡住。
﻿‍‍​‍​​﻿‍​‌﻿‍‍‌​‌‌‌​‍‍‌‍﻿﻿​‌‌﻿﻿‌﻿﻿"""

from __future__ import annotations

import importlib.util
import platform
import sys
from typing import Any

#: 必装（requirements.txt）：缺了内核起不来
REQUIRED: list[tuple[str, str]] = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("yaml", "PyYAML"),
    ("tzdata", "tzdata"),
]

#: 可选增强：缺了不影响聊天，只影响对应功能
OPTIONAL: list[tuple[str, str, str]] = [
    ("numpy", "numpy", "数值计算（部分语音/信号处理）"),
    ("PIL", "Pillow", "图标/图片处理"),
    ("pypdf", "pypdf", "PDF 读取"),
    ("reportlab", "reportlab", "PDF 生成"),
    ("tiktoken", "tiktoken", "更准的 token 估算"),
    ("yt_dlp", "yt-dlp", "点歌翻唱的音轨下载"),
    ("dotenv", "python-dotenv", ".env 加载"),
    ("websockets", "websockets", "WebSocket 通道"),
    ("demucs", "demucs", "翻唱的人声分离"),
]


def _probe(module: str, package: str) -> dict[str, Any]:
    """只查在不在 + 版本号，不 import。"""
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    version = ""
    if found:
        try:
            from importlib.metadata import version as pkg_version

            version = pkg_version(package)
        except Exception:  # noqa: BLE001 - 版本读不到不影响"装没装"的判断
            version = ""
    return {"module": module, "package": package, "ok": found, "version": version}


def check() -> dict[str, Any]:
    """一次性把环境摊开给面板。"""
    required = [_probe(m, p) for m, p in REQUIRED]
    optional = []
    for module, package, why in OPTIONAL:
        item = _probe(module, package)
        item["why"] = why
        optional.append(item)
    missing_required = [i["package"] for i in required if not i["ok"]]
    missing_optional = [i["package"] for i in optional if not i["ok"]]
    return {
        "ok": not missing_required,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "required": required,
        "optional": optional,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "notes": (
            [] if not missing_required
            else [f"缺必装依赖：{'、'.join(missing_required)} → pip install -r requirements.txt"]
        ),
    }


__all__ = ["REQUIRED", "OPTIONAL", "check"]
