"""衡忆 2.0 引擎接线（Symvolve Memory vendored 副本的唯一入口）。

分工：
- `vendor/symvolve_memory/`：引擎本体，**只读副本**，本项目不改它一个字节。
- 本模块：把 vendor 插进 `sys.path`、按配置打开**同一个** `data/life.db`、缓存单例。
- `app/memory/h2.py`：旧 API 语义兼容层，业务代码只认它，不直接碰引擎。

为什么把引擎表塞进 `life.db` 而不是单独开一个库：
一份灵魂档案 = 一个文件。导出、备份、搬家、删库都只有一个对象，
也不会出现"记忆库跟聊天记录对不上时间线"这种破事。
﻿‌﻿‌﻿‍﻿‍‍‍​‍​‍‌‌‌﻿‌﻿‍‍​‍‌﻿​‍‍﻿‍‍‌﻿"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import sys
import threading
from typing import Any

from app.core import config
from app.core.paths import RESOURCE_ROOT

_VENDOR = RESOURCE_ROOT / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# 闭源分发时的岔路（P2-1）：仓库里只留 app/memory/api.py + 加密容器时，
# 这里先把容器解封、把路径插进 sys.path，下面那三个 import 才找得到引擎。
# vendor 源码还在的时候它直接返回源码模块，等于什么都没做。
from app.memory.api import ensure_core  # noqa: E402

ensure_core()

from symvolve_memory import (  # noqa: E402
    MemoryLifecycle,
    MemoryRecall,
    MemoryRenderer,
)
from symvolve_memory.db.repo import Store  # noqa: E402

_LOCK = threading.RLock()
_store: Store | None = None
_life: MemoryLifecycle | None = None
_recall: MemoryRecall | None = None
_renderer: MemoryRenderer | None = None
_path: pathlib.Path | None = None

#: 迁移文件的"旧 → 新"checksum 对照（只用于把老库的账本修正到当前文件）。
#:
#: 2026-10-31 换到 companion-emotion（并 main）时，上游做过一次**行尾归一**：
#: `0001_init.sql` 从 CRLF 变 LF，字节数 4243 → 4151，SHA256 变了；
#: 但去掉行尾/空白后 SQL 完全一致（已逐字比对）。老库 `schema_migrations` 里记的是旧值，
#: 引擎启动会按 checksum 拦下（MigrationError）。这里在打开库之前把这一行修正为新值——
#: 只改账本、不碰任何表结构，且仅当"旧值 == 已知旧版 且 新值 == 当前文件"时才动手。
_KNOWN_CHECKSUM_REWRITES: dict[int, dict[str, str]] = {
    1: {
        "b5496e7ea734bfb24489a7fe7d706688fe133844234c6c334335a018945ed113":
        "b264a8757e610114c9f0e9dc39cd604d0f4a15276ec6055145808e8d993498a1",
    },
}


def engine_path() -> pathlib.Path:
    """引擎库路径 = 项目主库（life.db）。"""
    return config.get().db_file


def _migration_files() -> dict[int, pathlib.Path]:
    root = _VENDOR / "symvolve_memory" / "db" / "migrations"
    out: dict[int, pathlib.Path] = {}
    for f in sorted(root.glob("*.sql")):
        try:
            out[int(f.name.split("_", 1)[0])] = f
        except ValueError:
            continue
    return out


def heal_legacy_migration_checksums(path: pathlib.Path | None = None) -> int:
    """把老库的迁移账本对齐到当前迁移文件（只修已知的行尾归一）。

    返回修正的行数；库不存在、没有账本表、或 checksum 不在已知对照里时安静返回 0。
    不改任何业务表，不删数据。
    """
    target = pathlib.Path(path or engine_path())
    if not target.exists():
        return 0
    files = _migration_files()
    try:
        con = sqlite3.connect(str(target), timeout=5)
    except sqlite3.Error:
        return 0
    try:
        try:
            rows = list(con.execute("SELECT version, checksum FROM schema_migrations"))
        except sqlite3.Error:
            return 0
        fixed = 0
        for version, stored in rows:
            mapping = _KNOWN_CHECKSUM_REWRITES.get(int(version))
            if not mapping or str(stored) not in mapping:
                continue
            f = files.get(int(version))
            if f is None:
                continue
            current = hashlib.sha256(f.read_bytes()).hexdigest()
            if current != mapping[str(stored)]:
                continue  # 文件也不是我们认识的新版，别猜
            con.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = ? AND checksum = ?",
                (current, int(version), str(stored)),
            )
            fixed += 1
        if fixed:
            con.commit()
        return fixed
    finally:
        con.close()


def _open() -> Store:
    global _store, _life, _recall, _renderer, _path
    want = engine_path()
    with _LOCK:
        if _store is not None and _path == want:
            return _store
        if _store is not None:
            _store.close()
        heal_legacy_migration_checksums(want)
        store = Store(want)
        _store = store
        _path = want
        _life = MemoryLifecycle(store)
        _recall = MemoryRecall(store, lifecycle=_life)
        _renderer = MemoryRenderer(store, recall=_recall, lifecycle=_life)
    return _store


def store() -> Store:
    return _open()


def lifecycle() -> MemoryLifecycle:
    _open()
    assert _life is not None
    return _life


def recall() -> MemoryRecall:
    _open()
    assert _recall is not None
    return _recall


def renderer() -> MemoryRenderer:
    _open()
    assert _renderer is not None
    return _renderer


def stats() -> dict[str, Any]:
    """引擎自身计数（诊断用，面板可只读展示）。"""
    raw = store().stats()
    keep = (
        "schema_version", "events", "items", "items_active", "cues",
        "recall_logs", "distinct_users", "db_bytes",
    )
    return {k: raw.get(k) for k in keep}


def reset() -> None:
    """关掉单例，下次调用重新开库（测试换库、迁移后都需要）。"""
    global _store, _life, _recall, _renderer, _path
    with _LOCK:
        if _store is not None:
            try:
                _store.close()
            except Exception:  # noqa: BLE001 - 关不上也不能挡住重启
                pass
        _store = _life = _recall = _renderer = None
        _path = None


__all__ = [
    "engine_path", "store", "lifecycle", "recall", "renderer", "stats", "reset",
]
