"""人设与记忆的导出 / 导入。

为什么需要：换电脑、重装、或者想把"她"带走。
导出的是一个自包含档案：人设原文、关系、人格、记忆、对话、会话状态。
导入默认覆盖当前库——先备份再导入。
﻿‌‍‌‌​​‌﻿‍﻿﻿‌‌‌‍‍​﻿​​‍﻿‍​‍​‌‌‌﻿﻿​﻿"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from typing import Any

from app.core import db, memengine
from app.core.paths import DATA_ROOT

ACTIVE_PERSONA = DATA_ROOT / "persona-active.yaml"
EXPORT_DIR = DATA_ROOT / "exports"

# 记忆本体在引擎表里，走 h2 / 引擎归档（见 docs/49）；这里的表只放业务数据
_TABLES = ("events", "relation_log", "traits_log", "summaries")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def export_payload(rt) -> dict[str, Any]:
    persona_path = rt.cfg.persona_file
    persona_yaml = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""
    payload = {
        "format": "ai-life/persona-transfer",
        "version": 1,
        "exported_at": _now(),
        "persona_name": rt.persona.name,
        "persona_yaml": persona_yaml,
        "session": dict(db.one("SELECT * FROM session WHERE id = 1") or {}),
        "relation": dict(db.one("SELECT * FROM relation WHERE id = 1") or {}),
        "traits": [dict(r) for r in db.rows("SELECT * FROM traits")],
        "memories": [dict(r) for r in db.rows("SELECT * FROM memories ORDER BY id")],
        # 引擎原生归档：搬库不丢线索、热度、召回审计；老版本 Excel 式 rows 只作可读快照
        "memory_archive": memengine.store().dump(),
        "events": [dict(r) for r in db.rows("SELECT * FROM events ORDER BY id")],
        "summaries": [dict(r) for r in db.rows("SELECT * FROM summaries ORDER BY id")],
        # 两本账：import 会 DELETE 它们，导出体里必须带上，否则搬家即归零
        "relation_log": [dict(r) for r in db.rows("SELECT * FROM relation_log ORDER BY id")],
        "traits_log": [dict(r) for r in db.rows("SELECT * FROM traits_log ORDER BY id")],
    }
    return payload


def export_file(rt) -> str:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = str(rt.persona.name or "persona")
    path = EXPORT_DIR / f"{name}-{stamp}.life.json"
    path.write_text(json.dumps(export_payload(rt), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _rows(table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in db.rows(f"SELECT * FROM {table} ORDER BY id")]


def import_payload(rt, payload: dict[str, Any], keep_events: bool = True) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != "ai-life/persona-transfer":
        raise ValueError("不是人设档案文件")

    persona_yaml = str(payload.get("persona_yaml") or "")
    if persona_yaml.strip():
        ACTIVE_PERSONA.write_text(persona_yaml, encoding="utf-8")

    # 替换当前数据
    with db.cursor() as cur:
        for table in _TABLES:
            cur.execute(f"DELETE FROM {table}")
        cur.execute("DELETE FROM traits")

    def _insert(table: str, rows: list[dict[str, Any]], skip: tuple[str, ...] = ("id",)) -> None:
        for row in rows:
            cols = [c for c in row.keys() if c not in skip]
            placeholders = ",".join("?" for _ in cols)
            db.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                tuple(row[c] for c in cols),
            )

    rel = payload.get("relation") or {}
    if rel:
        db.execute(
            "UPDATE relation SET affinity=?, trust=?, attraction=?, aversion=?, updated_at=?"
            " WHERE id = 1",
            (float(rel.get("affinity", 0)), float(rel.get("trust", 0)),
             float(rel.get("attraction", 0)), float(rel.get("aversion", 0)), _now()),
        )
    _insert("traits", payload.get("traits") or [])
    sess = payload.get("session") or {}
    if sess:
        db.execute(
            "UPDATE session SET seq=?, her_name=?, name_locked=?, name_pending=?, status=?,"
            " started_at=?, ended_at=?, end_reason=? WHERE id = 1",
            (int(sess.get("seq", 1)), sess.get("her_name"), int(sess.get("name_locked", 0)),
             sess.get("name_pending"), sess.get("status", "active"), sess.get("started_at"),
             sess.get("ended_at"), sess.get("end_reason")),
        )

    imported = 0
    from app.memory import h2

    archive = payload.get("memory_archive")
    h2.forget_all()
    if isinstance(archive, dict) and archive.get("format"):
        try:
            memengine.store().load(archive, mode="replace")
            imported = len(archive.get("items") or [])
        except Exception:  # noqa: BLE001 - 归档坏了就退回逐条重建
            archive = None
    if not isinstance(archive, dict) or not archive.get("format"):
        for row in payload.get("memories") or []:
            mid = h2.add(
                str(row.get("kind") or "fact"),
                str(row.get("content") or ""),
                importance=float(row.get("importance") or 0.5),
                pin=bool(int(row.get("pinned") or 0)),
                user_id=int(row.get("user_id") or 0),
                chat_type=str(row.get("chat_type") or ""),
            )
            if mid:
                imported += 1
    if keep_events:
        for row in payload.get("events") or []:
            _insert("events", [row])
    for row in payload.get("summaries") or []:
        _insert("summaries", [row])
    # 两本账：老档案里没有这两个键就跳过（向后兼容），有就原样恢复
    for row in payload.get("relation_log") or []:
        _insert("relation_log", [row])
    for row in payload.get("traits_log") or []:
        _insert("traits_log", [row])

    from app.core import continuity

    continuity.record(
        "soul_imported", "整份灵魂档案被导入：记忆与对话换了一套",
        "transfer", {"memories": imported, "persona_file": str(ACTIVE_PERSONA)},
    )
    return {"ok": True, "memories": imported, "persona_file": str(ACTIVE_PERSONA)}


def snapshot_for_archive() -> dict[str, Any]:
    """关系结束时，把人设原文也存进档案，便于日后恢复。"""
    return {
        "persona_yaml": ACTIVE_PERSONA.read_text(encoding="utf-8") if ACTIVE_PERSONA.exists() else "",
        "exported_at": _now(),
    }


def remove_active_persona() -> None:
    if ACTIVE_PERSONA.exists():
        ACTIVE_PERSONA.unlink()
