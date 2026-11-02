"""表情包选择器：从本地 `data/stickers` 库里按场景挑一张安全图。

库由 `scripts/fetch_stickers.py` 抓取（全量 2449 张，safe=true 为安全子集）。
选择规则（朴素但够用）：
1. 只要 safe=true（档案馆里有成人/攻击性梗，默认不发）
2. 有场景就优先同场景（不高兴/关心/被撒娇/日常/她自己），没有就随机
3. 不回同一张（调用方可传 exclude_ids）
﻿​‍‌﻿‌​​‍‍‍​‍‍‌‌​‍‍​‌​‌‌‌​​‌​‌‍﻿‍﻿"""

from __future__ import annotations

import json
import pathlib
import random
from typing import Any

from app.core.paths import DATA_ROOT, RESOURCE_ROOT

_SEARCH = (DATA_ROOT / "data" / "stickers", RESOURCE_ROOT / "data" / "stickers")
_cache: tuple[pathlib.Path, list[dict[str, Any]]] | None = None


def _index_path() -> pathlib.Path | None:
    for base in _SEARCH:
        path = base / "index.json"
        if path.exists():
            return path
    return None


def load_index(use_cache: bool = True) -> list[dict[str, Any]]:
    global _cache
    path = _index_path()
    if path is None:
        return []
    if use_cache and _cache is not None and _cache[0] == path:
        return _cache[1]
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = [e for e in entries if isinstance(e, dict) and e.get("id")]
    _cache = (path, entries)
    return entries


def file_path(entry: dict[str, Any]) -> pathlib.Path | None:
    rel = str(entry.get("file") or "")
    if not rel:
        return None
    for base in _SEARCH:
        path = base / rel
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def stats() -> dict[str, Any]:
    entries = load_index()
    scenes: dict[str, int] = {}
    for e in entries:
        scenes[str(e.get("scene") or "未分类")] = scenes.get(str(e.get("scene") or "未分类"), 0) + 1
    return {
        "total": len(entries),
        "safe": sum(1 for e in entries if e.get("safe")),
        "scenes": scenes,
        "index": str(_index_path() or ""),
    }


def pick(
    scene: str | None = None,
    *,
    safe_only: bool = True,
    exclude_ids: set[str] | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """挑一张表情包；库不存在/没有可用图时返回 None。"""
    entries = load_index()
    if not entries:
        return None
    rng = rng or random.Random()
    excluded = {str(x) for x in (exclude_ids or set())}
    candidates = [
        e for e in entries
        if str(e.get("id")) not in excluded
        and (not safe_only or bool(e.get("safe")))
        and file_path(e) is not None
    ]
    if not candidates:
        return None
    if scene:
        preferred = [e for e in candidates if e.get("scene") == scene]
        if preferred:
            candidates = preferred
    return rng.choice(candidates)
