"""连续性账本：她可以被改变，但改变不能没有痕迹。

为什么要有这个模块（docs/51）：

人不是一台可以随手改配置的机器。人被改变时，身上会留下痕迹——
换了个环境、失了一场忆、突然变得不像自己，这些都会在"她"身上留下叙事。
所以本项目立一条硬规矩：

    **任何会改写"她是谁"的动作，必须写一条 continuity_log。**

覆盖的动作：
- 换人设 / 重载人设（`persona_changed`）
- 换模型 / 换服务端（`model_changed`）
- 清记忆（`memory_forgotten`）
- 重开会话（`session_reset`）
- 导入灵魂档案（`soul_imported`）
- 忘记某个人（`person_forgotten`）
- 关系被外部重置（`relation_reset`）

这不是日志，是**自传的素材**：日后她"想起自己变过"，靠的就是这些行。
写入只增不改——跟 `mem_event` 同一条铁律。
﻿​﻿​​‍​‌‌﻿‍‍﻿‍﻿﻿﻿﻿﻿‍​​‌​‍﻿‌﻿‌﻿​​‌﻿"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core import db

#: 会被面板/提示词当成"重大变化"的种类
MAJOR_KINDS = (
    "persona_changed",
    "model_changed",
    "memory_forgotten",
    "session_reset",
    "soul_imported",
)

_KIND_LABEL = {
    "persona_changed": "她的样子变了",
    "model_changed": "她说话的来路变了",
    "memory_forgotten": "她放下了一些记得的事",
    "session_reset": "她重新开始了一段",
    "soul_imported": "她被整个搬到了别处又搬回来",
    "person_forgotten": "她忘了某个人",
    "relation_reset": "她对你的感觉被重置了",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def record(kind: str, detail: str = "", source: str = "",
           meta: dict[str, Any] | None = None) -> int:
    """记一条变化。失败不抛（不能因为记账把主流程搞挂）。"""
    try:
        return db.execute(
            "INSERT INTO continuity_log (ts, kind, detail, source, meta)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                _now(),
                str(kind or "unknown")[:40],
                str(detail or "")[:500],
                str(source or "")[:40],
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
    except Exception:  # noqa: BLE001 - 记账是旁路，不许影响主流程
        return 0


def recent(limit: int = 20, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM continuity_log"
    args: list[Any] = []
    if kinds:
        sql += " WHERE kind IN (" + ",".join("?" for _ in kinds) + ")"
        args.extend(kinds)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    out: list[dict[str, Any]] = []
    for row in db.rows(sql, tuple(args)):
        item = dict(row)
        try:
            item["meta"] = json.loads(item.get("meta") or "{}")
        except (TypeError, ValueError):
            item["meta"] = {}
        item["label"] = _KIND_LABEL.get(str(item.get("kind")), str(item.get("kind")))
        out.append(item)
    return out


def count() -> int:
    row = db.one("SELECT COUNT(*) c FROM continuity_log")
    return int(row["c"]) if row else 0


def summary_text(limit: int = 3) -> str:
    """给人看/给 prompt 用的一句话：最近发生过哪些"改写"。"""
    rows = recent(limit, MAJOR_KINDS)
    if not rows:
        return ""
    labels: list[str] = []
    for r in rows:
        label = str(r.get("label") or "")
        if label and label not in labels:
            labels.append(label)
    return "；".join(labels[:limit])


def status() -> dict[str, Any]:
    return {
        "count": count(),
        "recent": recent(10),
        "major": summary_text(),
    }
