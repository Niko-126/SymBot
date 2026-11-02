"""Item 注册表：先列全清单，再逐条执行。

规则：
1. **先 add 后跑**：任何 phase 都必须先把本阶段所有 Item 注册进来（status=NOTRUN），
   再执行 → 报告里"列了没跑"会显性暴露为 NOTRUN，而不是消失。
2. ID 唯一；重复 add 只累加 `extra["dup"]`，不覆盖已有结果。
3. 所有写盘都走 `save()`，JSON 可复核。
﻿‌‍​​﻿‍‌‌﻿‍​​‌﻿﻿‌​‌﻿﻿﻿﻿​​‍‌‍‍﻿‍﻿‍﻿"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Iterable

from app.qa import types


class Registry:
    def __init__(self) -> None:
        self.items: dict[str, types.Item] = {}
        self.dups: dict[str, int] = {}
        self.started = time.time()

    # ------------------------------------------------------------------ add
    def add(self, item: types.Item) -> types.Item:
        if item.id in self.items:
            self.dups[item.id] = self.dups.get(item.id, 0) + 1
            return self.items[item.id]
        self.items[item.id] = item
        return item

    def add_many(self, items: Iterable[types.Item]) -> None:
        for it in items:
            self.add(it)

    # ----------------------------------------------------------------- mark
    def mark(self, item_id: str, status: str, detail: str = "", **extra: Any) -> None:
        it = self.items.get(item_id)
        if it is None:
            it = self.add(types.Item(id=item_id, kind="unknown", name=item_id))
        it.status = status
        it.detail = str(detail)[:2000]
        if extra:
            it.extra.update(extra)
        it.ts = time.time()

    # -------------------------------------------------------------- queries
    def by_kind(self, kind: str) -> list[types.Item]:
        return [i for i in self.items.values() if i.kind == kind]

    def pending(self) -> list[types.Item]:
        return [i for i in self.items.values() if i.status == types.NOTRUN]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items.values():
            out[it.status] = out.get(it.status, 0) + 1
        return out

    def risk_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items.values():
            out[it.risk] = out.get(it.risk, 0) + 1
        return out

    def unresolved(self) -> list[types.Item]:
        """跑了但没通过 / 没跑：报告里必须逐条列出来。"""
        bad = {types.RAISED, types.HANG, types.NOTRUN}
        return [i for i in self.items.values() if i.status in bad]

    # ----------------------------------------------------------------- file
    def save(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "counts": self.counts(),
            "risk": self.risk_counts(),
            "dups": self.dups,
            "items": [i.to_dict() for i in sorted(self.items.values(), key=lambda x: x.id)],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "risk": self.risk_counts(),
            "items": [i.to_dict() for i in self.items.values()],
        }


def slug(text: str, *, maxlen: int = 80) -> str:
    s = "".join(ch if ch.isalnum() or ch in "._-/ :" else "_" for ch in str(text))
    s = s.replace(" ", "_")
    return s[:maxlen]
