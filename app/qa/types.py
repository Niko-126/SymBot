"""对抗式训练的数据契约：Item / Finding / TurnRecord / PhaseResult。

所有 phase 模块只产出这几种对象，report 只消费这几种对象。
禁止各写一套 dict —— 字段名一旦漂移，报告就会静默丢数据。
﻿﻿﻿​‌﻿‌‍​﻿‌​‍‌​﻿​‍‌﻿﻿‍​‌﻿﻿‌﻿‍﻿‍‍﻿﻿"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

# Item.status
OK = "OK"
RAISED = "RAISED"      # 调用/请求抛异常
SKIP = "SKIP"          # 按规则跳过（危险/缺参/依赖外部），必须给 reason
HANG = "HANG"          # 超时
NOTRUN = "NOTRUN"      # 列了但没轮到（截断/中止）
EXPECTED = "EXPECTED"  # 该抛且抛了（负向用例通过）

# Finding.severity
P0 = "P0"   # 数据损坏 / 安全 / 崩溃 / 静默失败
P1 = "P1"   # 功能错误 / 契约不符 / 明显出戏
P2 = "P2"   # 体验/健壮性/口径不一致
INFO = "INFO"

SEVERITY_ORDER = {P0: 0, P1: 1, P2: 2, INFO: 3}


@dataclass
class Item:
    """一条被测试项。kind 决定怎么跑。"""

    id: str                        # 唯一：FN:app.memory.h2:add / ROUTE:POST /v1/chat / CLI:serve / TBL:mem_item
    kind: str                      # fn | method | route | cli | table | config | persona | scenario | module
    name: str                      # 展示名
    module: str = ""               # 所属模块（app.memory.h2 / panel / -）
    line: int = 0
    risk: str = "read"             # read | write | net | llm | hang | danger
    plan: str = ""                 # L0 静态 / L1 进程内 / L2 子进程 / L3 会话 / L4 路由
    status: str = NOTRUN
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    id: str
    severity: str
    area: str                      # 模块/功能域
    title: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    repro: str = ""
    item_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnRecord:
    scenario: str
    index: int
    role: str                      # user | her
    text: str
    latency_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d


@dataclass
class PhaseResult:
    phase: str                     # inventory | routes | functions | duel | coverage
    started: float = field(default_factory=time.time)
    ended: float = 0.0
    items: list[Item] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: str = ""

    def finish(self) -> "PhaseResult":
        self.ended = time.time()
        return self

    @property
    def secs(self) -> float:
        return round((self.ended or time.time()) - self.started, 2)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items:
            out[it.status] = out.get(it.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "secs": self.secs,
            "counts": self.counts(),
            "notes": self.notes,
            "items": [i.to_dict() for i in self.items],
            "findings": [f.to_dict() for f in self.findings],
        }


def sev_key(f: Finding) -> tuple[int, str]:
    return (SEVERITY_ORDER.get(f.severity, 9), f.id)
