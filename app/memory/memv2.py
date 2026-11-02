"""衡忆 2.0 落地：引擎表接管 · 旧 `memories` 表归档 · 只读视图。

这个模块只干三件事，跑完就闭嘴：

1. 打开引擎（`app/core/memengine.py`）——`mem_item / mem_cue / mem_event / mem_recall_log`
   由引擎自己的迁移建好，本项目不手搓 schema。
2. 老库里的 `memories` **表**逐行搬进引擎（原文进 `mem_event`、正文进 `mem_item`、线索进 `mem_cue`），
   搬完把表改名成 `memories_legacy_20261030` 留档。
3. 建一个叫 `memories` 的**只读视图**，让仓库里那二十几处老 SQL 继续能读；写入永远走 `app/memory/h2.py`。

为什么不是"把老表删了重建"：
老表是唯一的历史档案，删了就没后悔药。改名留档 + 视图 = 读得到、写不进、随时能对账。

幂等：已经是视图就直接返回；搬到一半断了，重跑会靠 `(user_id, chat_type, kind, text)` 去重接着搬。
﻿​‍‍﻿‌‍‌‍‌‌​‌‌‍‍﻿‍‌‍​‌﻿​​‍‍‍‌‍﻿‌​﻿"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import datetime
from typing import Any

from app.core import memengine

LEGACY_TABLE = "memories_legacy_20261030"
VIEW_NAME = "memories"
KV_KEY = "memory_engine_migrated_at"

_VIEW_SQL = """
CREATE VIEW memories AS
SELECT
    m.id                                                  AS id,
    m.kind                                                AS kind,
    m.text                                                AS content,
    m.importance / 100.0                                  AS importance,
    0.5                                                   AS stability,
    m.source_event_ids                                    AS source_ids,
    strftime('%Y-%m-%dT%H:%M:%S', m.created_at, 'unixepoch', 'localtime') AS first_seen,
    strftime('%Y-%m-%dT%H:%M:%S', m.last_seen, 'unixepoch', 'localtime')  AS last_seen,
    m.recall_count                                        AS recall_count,
    CASE WHEN m.last_recall IS NULL THEN NULL
         ELSE strftime('%Y-%m-%dT%H:%M:%S', m.last_recall, 'unixepoch', 'localtime') END AS last_recall,
    m.heat                                                AS heat,
    m.pinned                                              AS pinned,
    COALESCE((SELECT json_group_array(c.cue) FROM mem_cue c WHERE c.item_id = m.id), '[]') AS cues,
    CASE WHEN m.last_recall IS NULL
         THEN strftime('%Y-%m-%dT%H:%M:%S', m.last_seen, 'unixepoch', 'localtime')
         ELSE strftime('%Y-%m-%dT%H:%M:%S', m.last_recall, 'unixepoch', 'localtime') END AS last_touch,
    CAST(m.user_id AS INTEGER)                            AS user_id,
    m.chat_type                                           AS chat_type,
    m.clarity                                             AS clarity
FROM mem_item m
WHERE m.forgotten = 0
  AND m.superseded_by IS NULL
  AND m.merged_into IS NULL
"""


# ---------------------------------------------------------------- 状态探测 --

def _table_kind(con: sqlite3.Connection, name: str) -> str:
    row = con.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return str(row[0]) if row else ""


def _backup(path) -> str | None:
    """迁移前整库备份一次（WAL 也带上）。已有备份就不覆盖。"""
    target = path.parent / (path.name + ".before-memv2")
    if target.exists():
        return str(target)
    try:
        shutil.copy2(path, target)
        for suffix in ("-wal", "-shm"):
            side = path.parent / (path.name + suffix)
            if side.exists():
                shutil.copy2(side, target.parent / (target.name + suffix))
    except OSError:
        return None
    return str(target)


# ---------------------------------------------------------------- 视图 ----

def _create_view(con: sqlite3.Connection) -> None:
    if _table_kind(con, VIEW_NAME) == "view":
        con.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
    elif _table_kind(con, VIEW_NAME) == "table":
        raise RuntimeError("memories 还是表，不能建同名视图；先跑迁移")
    con.executescript(_VIEW_SQL)


# ---------------------------------------------------------------- 搬家 ----

def _epoch(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp())


def _clarity_for(last_seen: int, created_at: int) -> str:
    from symvolve_memory import decay as _decay

    stamp = last_seen or created_at or int(time.time())
    days = max(0.0, (int(time.time()) - int(stamp)) / 86400.0)
    return _decay.clarity_for_age(days)


def _cues_of(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(c) for c in raw if str(c or "").strip()][:12]


def _already_there(store, user_id: str, chat_type: str, kind: str, text: str) -> bool:
    for item in store.list_items(user_id=user_id, chat_type=chat_type, kinds=[kind], limit=200):
        if item.text == text:
            return True
    return False


def _migrate_rows(store, con: sqlite3.Connection) -> dict[str, int]:
    """把旧表逐行搬进引擎。已存在的跳过（幂等）。"""
    from symvolve_memory import MemoryEvent, MemoryItem, clamp_heat
    from symvolve_memory.models import clamp_importance

    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {VIEW_NAME} ORDER BY id")]
    moved = skipped = cues_added = 0
    for row in rows:
        text = str(row.get("content") or "").strip()
        if not text:
            skipped += 1
            continue
        kind = str(row.get("kind") or "fact")
        uid = str(int(row.get("user_id") or 0))
        ct = str(row.get("chat_type") or "")
        if _already_there(store, uid, ct, kind, text):
            skipped += 1
            continue
        created_at = _epoch(row.get("first_seen")) or int(time.time())
        last_seen = _epoch(row.get("last_seen")) or created_at
        last_recall = _epoch(row.get("last_recall")) or None
        try:
            legacy_sources = json.loads(str(row.get("source_ids") or "[]"))
        except ValueError:
            legacy_sources = []
        meta = {"kind": kind, "legacy_row_id": row.get("id"), "legacy": True}
        if legacy_sources:
            meta["ai_life_source_ids"] = legacy_sources

        event_id = store.append_event(
            MemoryEvent(
                user_id=uid,
                chat_type=ct or "private",
                scope=ct or "shared",
                raw_text=text,
                created_at=created_at,
                meta=dict(meta),
            )
        )
        item = MemoryItem(
            user_id=uid,
            chat_type=ct,
            scope=ct or "shared",
            kind=kind,
            title=text[:24],
            text=text,
            importance=clamp_importance(int(round(float(row.get("importance") or 0.5) * 100))),
            clarity=_clarity_for(last_seen, created_at),
            heat=clamp_heat(float(row.get("heat") or 0.0)),
            pinned=bool(int(row.get("pinned") or 0)),
            source_event_ids=[event_id],
            created_at=created_at,
            last_seen=last_seen,
            last_recall=last_recall,
            recall_count=int(row.get("recall_count") or 0),
            meta=dict(meta),
        )
        mid = store.insert_item(item)
        cues = _cues_of(row.get("cues"))
        if cues:
            cues_added += store.add_cues(mid, [(c, "content", 1.0) for c in cues])
        moved += 1
    return {"legacy_rows": len(rows), "moved": moved, "skipped": skipped, "cues": cues_added}


def _archive_table(con: sqlite3.Connection) -> str:
    name = LEGACY_TABLE
    n = 2
    while _table_kind(con, name):
        name = f"{LEGACY_TABLE}_{n}"
        n += 1
    con.execute(f"ALTER TABLE {VIEW_NAME} RENAME TO {name}")
    return name


# ---------------------------------------------------------------- 入口 ----

def ensure_ready(*, parent=None, backup: bool = True, dry_run: bool = False,
                 force: bool = False) -> dict[str, Any]:
    """幂等入口：db.init() 每次启动都会调它。"""
    path = memengine.engine_path()

    probe = sqlite3.connect(str(path))
    try:
        kind = _table_kind(probe, VIEW_NAME)
        legacy_rows = 0
        if kind == "table":
            legacy_rows = int(probe.execute(f"SELECT COUNT(*) FROM {VIEW_NAME}").fetchone()[0])
        engine_ready = bool(_table_kind(probe, "mem_item"))
    finally:
        probe.close()

    if kind == "view" and engine_ready and not force:
        return {"ok": True, "state": "ready", "db": str(path)}

    if dry_run:
        return {
            "ok": True, "state": "dry-run", "db": str(path),
            "memories_kind": kind or "(none)", "legacy_rows": legacy_rows,
            "would_backup": kind == "table" and backup,
        }

    backup_path = _backup(path) if kind == "table" and backup else None

    store = memengine.store()          # 引擎迁移在这里跑
    from app.core import db as _db

    con = store.connection
    info: dict[str, Any] = {"ok": True, "db": str(path), "backup": backup_path}
    if kind == "table":
        moved = _migrate_rows(store, con)
        con.commit()
        archived_as = _archive_table(con)
        con.commit()
        info.update(moved)
        info["archived_as"] = archived_as
        info["state"] = "migrated"
    else:
        info["state"] = "view_created"

    _create_view(con)
    con.commit()
    try:
        _db.kv_set(KV_KEY, datetime.now().astimezone().isoformat(timespec="seconds"))
    except Exception:  # noqa: BLE001 - kv 写不进去不影响记忆可用
        pass
    info["engine_items"] = int(store.stats().get("items") or 0)
    return info


def status() -> dict[str, Any]:
    """诊断：表/视图状态、引擎计数、是否已迁移。"""
    path = memengine.engine_path()
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        kind = _table_kind(con, VIEW_NAME)
        legacy = _table_kind(con, LEGACY_TABLE)
        rows = 0
        if kind:
            try:
                rows = int(con.execute(f"SELECT COUNT(*) FROM {VIEW_NAME}").fetchone()[0])
            except sqlite3.Error:
                rows = 0
        out = {
            "db": str(path), "memories_kind": kind or "(none)", "view_rows": rows,
            "legacy_table": legacy or "(none)", "engine_ready": bool(_table_kind(con, "mem_item")),
        }
        if out["engine_ready"]:
            stats = memengine.store().stats()
            out.update({k: stats.get(k) for k in ("schema_version", "items", "items_active",
                                                  "events", "cues", "recall_logs")})
        return out
    finally:
        con.close()
