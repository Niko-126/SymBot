"""群聊进化：人类观察、每人看法、周期报告、记忆系统自评指标。

设计见 docs/22。三张新表：

- `human_notes`        她观察到的人类习惯/说话方式（只学"人怎么相处"，不做真人画像）
- `member_impression`  她对每个群成员的看法（随群聊更新）
- `reflect_report`     思考报告（daily_think）与记忆系统评估（memory_eval，默认每 7 天）

本模块只负责**数据与指标**；真正调模型生成在 `app/memory/evolve.py`（下一步）。
﻿​​​​﻿﻿‌‍‌‍​‍﻿﻿​‌​​‍​‍﻿​﻿​﻿‌​‌‍‌﻿﻿"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.core import db

NOTE_TOPICS = ("习惯", "说话方式", "情绪", "仪式", "相处")
REPORT_THINK = "daily_think"
REPORT_MEMORY_EVAL = "memory_eval"
KEY_THINK_DAY = "reflect_think_date"
KEY_EVAL_DAY = "reflect_eval_date"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _json(row_value: Any, default: Any) -> Any:
    try:
        return json.loads(str(row_value)) if row_value else default
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------- 人类观察


def add_note(
    topic: str,
    content: str,
    *,
    confidence: float = 0.6,
    user_id: int | None = None,
    session_seq: int = 1,
    chat_type: str = "",
) -> int:
    topic = topic if topic in NOTE_TOPICS else "习惯"
    content = str(content).strip()
    if not content:
        return 0
    ts = now_iso()
    return db.execute(
        "INSERT INTO human_notes (ts, day, topic, content, confidence, user_id, session_seq, chat_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, ts[:10], topic, content[:400], max(0.0, min(1.0, float(confidence))), user_id,
         int(session_seq), str(chat_type or "")),
    )


def recent_notes(days: int = 7, limit: int = 80) -> list[dict[str, Any]]:
    cutoff = (datetime.now().astimezone().date() - timedelta(days=max(1, int(days)))).isoformat()
    rows = db.rows(
        "SELECT * FROM human_notes WHERE day >= ? ORDER BY id DESC LIMIT ?", (cutoff, int(limit))
    )
    return [dict(r) for r in reversed(rows)]


def notes_for_user(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT * FROM human_notes WHERE user_id = ? ORDER BY id DESC LIMIT ?", (int(user_id), int(limit))
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 每人看法


def upsert_impression(
    user_id: int,
    summary: str,
    *,
    traits_seen: list[str] | None = None,
    habits: list[str] | None = None,
) -> dict[str, Any]:
    """更新"她对某个人的看法"。每次覆盖 summary，traits/habits 合并去重。"""
    row = db.one("SELECT * FROM member_impression WHERE user_id = ?", (int(user_id),))
    merged_traits = _json(row["traits_seen"] if row else "[]", [])
    merged_habits = _json(row["habits"] if row else "[]", [])
    for item in traits_seen or []:
        s = str(item).strip()
        if s and s not in merged_traits:
            merged_traits.append(s[:40])
    for item in habits or []:
        s = str(item).strip()
        if s and s not in merged_habits:
            merged_habits.append(s[:60])
    ts = now_iso()
    version = int(row["version"] or 1) + 1 if row else 1
    if row is None:
        db.execute(
            "INSERT INTO member_impression (user_id, summary, traits_seen, habits, version, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), str(summary or "")[:600], json.dumps(merged_traits[-12:], ensure_ascii=False),
             json.dumps(merged_habits[-12:], ensure_ascii=False), version, ts),
        )
    else:
        db.execute(
            "UPDATE member_impression SET summary = ?, traits_seen = ?, habits = ?, version = ?,"
            " updated_at = ? WHERE user_id = ?",
            (str(summary or "")[:600], json.dumps(merged_traits[-12:], ensure_ascii=False),
             json.dumps(merged_habits[-12:], ensure_ascii=False), version, ts, int(user_id)),
        )
    return get_impression(user_id) or {}


def get_impression(user_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM member_impression WHERE user_id = ?", (int(user_id),))
    if row is None:
        return None
    data = dict(row)
    data["traits_seen"] = _json(data.get("traits_seen"), [])
    data["habits"] = _json(data.get("habits"), [])
    return data


def recent_impressions(limit: int = 20) -> list[dict[str, Any]]:
    rows = db.rows("SELECT user_id FROM member_impression ORDER BY updated_at DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        item = get_impression(int(r["user_id"]))
        if item:
            out.append(item)
    return out


# ---------------------------------------------------------------- 报告


def add_report(
    kind: str,
    period_start: str,
    period_end: str,
    content: str,
    metrics: dict[str, Any] | None = None,
) -> int:
    if kind not in (REPORT_THINK, REPORT_MEMORY_EVAL):
        raise ValueError("未知报告类型")
    return db.execute(
        "INSERT INTO reflect_report (kind, period_start, period_end, content, metrics, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (kind, period_start, period_end, str(content)[:4000],
         json.dumps(metrics or {}, ensure_ascii=False), now_iso()),
    )


def latest_report(kind: str) -> dict[str, Any] | None:
    row = db.one(
        "SELECT * FROM reflect_report WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
    )
    if row is None:
        return None
    data = dict(row)
    data["metrics"] = _json(data.get("metrics"), {})
    return data


def reports(kind: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    if kind:
        rows = db.rows(
            "SELECT * FROM reflect_report WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, int(limit))
        )
    else:
        rows = db.rows("SELECT * FROM reflect_report ORDER BY id DESC LIMIT ?", (int(limit),))
    out = []
    for row in rows:
        data = dict(row)
        data["metrics"] = _json(data.get("metrics"), {})
        out.append(data)
    return out


# ---------------------------------------------------------------- 记忆系统指标（自评用）


def _count(sql: str, args: tuple = ()) -> int:
    try:
        row = db.one(sql, args)
        return int((row or {})[0] if isinstance(row, tuple) else (list(row.values())[0] if row else 0))
    except Exception:  # noqa: BLE001 - 指标不能让主流程挂
        return 0


def _safe_one(sql: str, args: tuple = ()) -> dict[str, Any]:
    try:
        row = db.one(sql, args)
        return dict(row) if row else {}
    except Exception:  # noqa: BLE001
        return {}


def memory_metrics() -> dict[str, Any]:
    """给 7 天自评报告用的量化指标（缺表缺列都不抛错）。"""
    by_kind: dict[str, int] = {}
    try:
        for r in db.rows("SELECT kind, COUNT(*) c FROM memories GROUP BY kind"):
            by_kind[str(r["kind"])] = int(r["c"])
    except Exception:  # noqa: BLE001
        pass
    heat = _safe_one("SELECT COUNT(*) total, SUM(pinned) pinned, AVG(heat) heat_avg,"
                     " SUM(recall_count) recalls FROM memories")
    stale = _count("SELECT COUNT(*) FROM memories WHERE last_seen < ?",
                   ((datetime.now().astimezone() - timedelta(days=14)).isoformat(timespec="seconds"),))
    users = _count("SELECT COUNT(DISTINCT user_id) FROM memories WHERE user_id > 0")
    metrics = {
        "总记忆": int(heat.get("total") or sum(by_kind.values())),
        "按类型": by_kind,
        "钉住": int(heat.get("pinned") or 0),
        "平均热度": round(float(heat.get("heat_avg") or 0), 3),
        "累计召回": int(heat.get("recalls") or 0),
        "14天未触碰": stale,
        "归属用户数": users,
        "摘要条数": _count("SELECT COUNT(*) FROM summaries"),
        "冷记忆合并": _count("SELECT COUNT(*) FROM memory_merges"),
        "参考轮次": _count("SELECT COUNT(*) FROM ref_turns"),
        "参考原话": _count("SELECT COUNT(*) FROM ref_quotes"),
        "事件条数": _count("SELECT COUNT(*) FROM events"),
        "人类观察条数": _count("SELECT COUNT(*) FROM human_notes"),
        "各人看法条数": _count("SELECT COUNT(*) FROM member_impression"),
    }
    total = metrics["总记忆"] or 1
    metrics["钉住占比"] = round(metrics["钉住"] / total, 3)
    metrics["未触碰占比"] = round(metrics["14天未触碰"] / total, 3)
    return metrics


def eval_period(now: datetime | None = None) -> tuple[str, str, str]:
    """自评周期：自然周（周一→周日）。返回 (上周一, 上周日, 本周一)。"""
    now = now or datetime.now().astimezone()
    this_monday = now.date() - timedelta(days=now.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday.isoformat(), last_sunday.isoformat(), this_monday.isoformat()


def eval_due() -> bool:
    """记忆自评是否到期：上一个自然周还没出过报告，就该出。"""
    _start, end, _monday = eval_period()
    last = latest_report(REPORT_MEMORY_EVAL)
    if last and str(last.get("period_end") or "") >= end:
        return False
    return True


def week_label(period: tuple[str, str, str] | None = None) -> str:
    start, end, _ = period or eval_period()
    return f"{start} – {end}"


def think_due() -> bool:
    return db.kv_get(KEY_THINK_DAY, "") != today()


def mark_think_done() -> None:
    db.kv_set(KEY_THINK_DAY, today())


def mark_eval_done() -> None:
    db.kv_set(KEY_EVAL_DAY, today())
