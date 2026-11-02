"""覆盖矩阵与缺口清单：24 小时、日型、活动类型、社交关系、场景、对话轮数。

用法：
    records = load_records("samples/life_corpus")
    report = build_coverage(records)
    print(format_coverage(report))

判定口径：
- 24 小时：rhythm 的 ``segments`` 合并后应正好铺满 1440 分钟，缺一分钟都算缺口。
- 活动类型：activity 记录的 ``activity_type`` 与 rhythm 片段的 ``label`` 合并统计，
  因为作息切片本身就是"什么时间在做什么"。
- 社交关系：social_scene 与 snippet 的 ``relation`` 合并统计。
- 对话轮数：social_scene 的 ``turns`` 字段 + snippet 中 ``role=self`` 的发言次数。
﻿﻿﻿‍‌﻿​﻿﻿‌‌​‌​‍﻿‌‌‌﻿‍​‌‌‍‍‌​‍‌​‌‌﻿"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, Final

from .schema import (
    ACTIVITY_TYPES,
    DAY_MINUTES,
    DAY_TYPES,
    INTERRUPT_POLICIES,
    KIND_ACTIVITY,
    KIND_RHYTHM,
    KIND_SNIPPET,
    KIND_SOCIAL_SCENE,
    KINDS,
    LOCATIONS,
    RELATIONS,
    SETTINGS,
    format_hhmm,
    parse_hhmm,
    snippet_turns,
)
from .validate import infer_kind, iter_jsonl_files, read_jsonl

# ---------------------------------------------------------------------------
# 目标值与核心枚举
# ---------------------------------------------------------------------------

#: 骨架阶段的最低目标，可按产品需要调整；数字写在 gaps 的说明里，避免"魔法数字"。
TARGETS: Final[dict[str, int]] = {
    "rhythm_records": 4,
    "activity_types": 10,
    "relations": 6,
    "scenes": 10,
    "snippets": 8,
    "turns": 40,
    "locations": 3,
}

#: 必须覆盖到的活动类型（缺一个就会在日常行为上出现盲区）。
CORE_ACTIVITY_TYPES: Final[tuple[str, ...]] = (
    "sleep",
    "meal",
    "work",
    "commute",
    "leisure",
    "selfcare",
    "social",
    "chores",
)

#: 必须覆盖到的社交关系（缺一个就会在关系维度上欠采样）。
CORE_RELATIONS: Final[tuple[str, ...]] = (
    "family",
    "partner",
    "friend",
    "colleague",
    "stranger",
    "service",
)

#: 没有任何样本就必须报严重缺口的日型。
CORE_DAY_TYPES: Final[tuple[str, ...]] = ("workday", "restday")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """一条覆盖缺口。``level`` 取 ``critical``（阻塞训练）或 ``warn``（建议补齐）。"""

    dimension: str
    item: str
    detail: str
    level: str = "warn"

    @property
    def critical(self) -> bool:
        return self.level == "critical"

    def format(self) -> str:
        tag = "严重" if self.critical else "提示"
        return f"[{tag}] {self.dimension}/{self.item}：{self.detail}"


@dataclass(slots=True)
class CoverageReport:
    """覆盖矩阵快照。所有计数都是"记录条数"，不是去重后的种类数。"""

    record_counts: dict[str, int] = dc_field(default_factory=dict)
    synthetic_count: int = 0
    day_types: dict[str, int] = dc_field(default_factory=dict)
    day_types_by_kind: dict[str, dict[str, int]] = dc_field(default_factory=dict)
    activity_types: dict[str, int] = dc_field(default_factory=dict)
    locations: dict[str, int] = dc_field(default_factory=dict)
    relations: dict[str, int] = dc_field(default_factory=dict)
    settings: dict[str, int] = dc_field(default_factory=dict)
    interrupt_policies: dict[str, int] = dc_field(default_factory=dict)
    scene_count: int = 0
    snippet_count: int = 0
    scene_turns_total: int = 0
    snippet_turns_total: int = 0
    turns_by_relation: dict[str, int] = dc_field(default_factory=dict)
    rhythm_coverage: dict[str, int] = dc_field(default_factory=dict)
    gaps: list[CoverageGap] = dc_field(default_factory=list)

    # -- 派生量 ----------------------------------------------------------

    @property
    def total_records(self) -> int:
        return sum(self.record_counts.values())

    @property
    def turns_total(self) -> int:
        return self.scene_turns_total + self.snippet_turns_total

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    @property
    def critical_gap_count(self) -> int:
        return sum(1 for gap in self.gaps if gap.critical)

    @property
    def ok(self) -> bool:
        return self.critical_gap_count == 0


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def load_records(directory: str | Path) -> dict[str, list[dict[str, Any]]]:
    """读取目录下所有可解析的 JSONL，按 kind 分组；坏行与未知文件名直接跳过（校验交给 validate）。"""
    records: dict[str, list[dict[str, Any]]] = {kind: [] for kind in KINDS}
    root = Path(directory)
    if not root.is_dir():
        return records
    for path in iter_jsonl_files(root):
        kind = infer_kind(path.name)
        if kind is None:
            continue
        for _line_no, record, error in read_jsonl(path):
            if error is not None or record is None:
                continue
            records[kind].append(record)
    return records


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------


def union_minutes(segments: Any) -> int:
    """把时间段合并成并集后返回覆盖分钟数（重叠只算一次，超出当天部分裁掉）。"""
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes, bytearray)):
        return 0

    intervals: list[tuple[int, int]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        start_raw = segment.get("start")
        end_raw = segment.get("end")
        start = parse_hhmm(start_raw) if isinstance(start_raw, str) else None
        end = parse_hhmm(end_raw) if isinstance(end_raw, str) else None
        if start is None or end is None or end <= start:
            continue
        intervals.append((max(0, start), min(DAY_MINUTES, end)))

    intervals.sort()
    total = 0
    current_start = -1
    current_end = -1
    for start, end in intervals:
        if start > current_end:
            if current_end > current_start >= 0:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end > current_start >= 0:
        total += current_end - current_start
    return min(total, DAY_MINUTES)


def _bump(counter: dict[str, int], key: Any, amount: int = 1) -> None:
    if isinstance(key, str) and key:
        counter[key] = counter.get(key, 0) + amount


def build_coverage(records: Mapping[str, Sequence[Mapping[str, Any]]]) -> CoverageReport:
    """根据已读取的记录生成覆盖矩阵与缺口清单。"""
    report = CoverageReport()

    for kind in KINDS:
        items = [record for record in records.get(kind, ()) if isinstance(record, Mapping)]
        report.record_counts[kind] = len(items)
        by_day = report.day_types_by_kind.setdefault(kind, {})
        for record in items:
            if record.get("synthetic") is True:
                report.synthetic_count += 1
            _bump(report.day_types, record.get("day_type"))
            _bump(by_day, record.get("day_type"))

    # 作息：24 小时覆盖 + 片段里的活动类型
    for record in records.get(KIND_RHYTHM, ()):
        if not isinstance(record, Mapping):
            continue
        record_id = record.get("id")
        label = record_id if isinstance(record_id, str) and record_id else f"<无 id 第 {len(report.rhythm_coverage) + 1} 条>"
        report.rhythm_coverage[label] = union_minutes(record.get("segments"))
        segments = record.get("segments")
        if isinstance(segments, Sequence) and not isinstance(segments, (str, bytes, bytearray)):
            for segment in segments:
                if isinstance(segment, Mapping):
                    _bump(report.activity_types, segment.get("label"))

    # 活动
    for record in records.get(KIND_ACTIVITY, ()):
        if not isinstance(record, Mapping):
            continue
        _bump(report.activity_types, record.get("activity_type"))
        _bump(report.locations, record.get("location"))

    # 社交场景
    for record in records.get(KIND_SOCIAL_SCENE, ()):
        if not isinstance(record, Mapping):
            continue
        report.scene_count += 1
        _bump(report.relations, record.get("relation"))
        _bump(report.settings, record.get("setting"))
        _bump(report.interrupt_policies, record.get("interrupt_policy"))
        turns = record.get("turns")
        if isinstance(turns, int) and not isinstance(turns, bool) and turns > 0:
            report.scene_turns_total += turns
            _bump(report.turns_by_relation, record.get("relation"), turns)

    # 对话片段
    for record in records.get(KIND_SNIPPET, ()):
        if not isinstance(record, Mapping):
            continue
        report.snippet_count += 1
        _bump(report.relations, record.get("relation"))
        _bump(report.interrupt_policies, record.get("interrupt_policy"))
        turns = snippet_turns(record.get("messages"))
        if turns > 0:
            report.snippet_turns_total += turns
            _bump(report.turns_by_relation, record.get("relation"), turns)

    report.gaps = _compute_gaps(report)
    return report


def _compute_gaps(report: CoverageReport) -> list[CoverageGap]:
    gaps: list[CoverageGap] = []

    # 1) 24 小时作息
    if not report.rhythm_coverage:
        gaps.append(CoverageGap("24h", "rhythm", "没有任何 rhythm 记录，24 小时作息完全未覆盖", "critical"))
    for record_id, minutes in report.rhythm_coverage.items():
        if minutes < DAY_MINUTES:
            gaps.append(
                CoverageGap(
                    "24h",
                    record_id,
                    f"只覆盖 {minutes}/{DAY_MINUTES} 分钟（{format_hhmm(minutes)} 之后断开），缺 {DAY_MINUTES - minutes} 分钟",
                    "critical",
                )
            )
    if 0 < len(report.rhythm_coverage) < TARGETS["rhythm_records"]:
        gaps.append(
            CoverageGap(
                "24h",
                "rhythm_records",
                f"仅 {len(report.rhythm_coverage)} 条作息记录，目标 {TARGETS['rhythm_records']} 条（按日型分别采样）",
                "warn",
            )
        )

    # 2) 日型
    for day_type, label in DAY_TYPES.items():
        if not report.day_types.get(day_type):
            level = "critical" if day_type in CORE_DAY_TYPES else "warn"
            gaps.append(CoverageGap("day_type", day_type, f"{label}没有任何记录", level))
    rhythm_days = report.day_types_by_kind.get(KIND_RHYTHM, {})
    for day_type, label in DAY_TYPES.items():
        if report.day_types.get(day_type) and not rhythm_days.get(day_type):
            gaps.append(CoverageGap("day_type×rhythm", day_type, f"{label}缺 rhythm 作息样本", "warn"))

    # 3) 活动类型
    for activity in CORE_ACTIVITY_TYPES:
        if not report.activity_types.get(activity):
            label = ACTIVITY_TYPES.get(activity, activity)
            gaps.append(CoverageGap("activity", activity, f"{label}缺失", "warn"))
    distinct_activities = len(report.activity_types)
    if distinct_activities < TARGETS["activity_types"]:
        level = "critical" if distinct_activities < 8 else "warn"
        gaps.append(
            CoverageGap(
                "activity",
                "distinct",
                f"活动类型仅 {distinct_activities} 种，目标 {TARGETS['activity_types']} 种",
                level,
            )
        )

    # 4) 社交关系
    for relation in CORE_RELATIONS:
        if not report.relations.get(relation):
            label = RELATIONS.get(relation, relation)
            gaps.append(CoverageGap("relation", relation, f"{label}缺失", "warn"))
    distinct_relations = len(report.relations)
    if distinct_relations < TARGETS["relations"]:
        level = "critical" if distinct_relations < 3 else "warn"
        gaps.append(
            CoverageGap(
                "relation",
                "distinct",
                f"社交关系仅 {distinct_relations} 种，目标 {TARGETS['relations']} 种",
                level,
            )
        )
    if not report.turns_by_relation.get("stranger"):
        gaps.append(CoverageGap("relation", "stranger_turns", "陌生人场景没有对话轮数样本", "warn"))

    # 5) 场景、片段、轮数
    if report.scene_count == 0:
        gaps.append(CoverageGap("scene", KIND_SOCIAL_SCENE, "没有任何 social_scene 场景记录", "critical"))
    elif report.scene_count < TARGETS["scenes"]:
        gaps.append(
            CoverageGap(
                "scene",
                KIND_SOCIAL_SCENE,
                f"场景仅 {report.scene_count} 个，目标 {TARGETS['scenes']} 个",
                "warn",
            )
        )
    if report.snippet_count == 0:
        gaps.append(CoverageGap("dialogue", KIND_SNIPPET, "没有任何 snippet 对话片段", "critical"))
    elif report.snippet_count < TARGETS["snippets"]:
        gaps.append(
            CoverageGap(
                "dialogue",
                KIND_SNIPPET,
                f"对话片段仅 {report.snippet_count} 条，目标 {TARGETS['snippets']} 条",
                "warn",
            )
        )
    if report.turns_total == 0:
        gaps.append(CoverageGap("dialogue", "turns", "对话轮数为 0，无法评估对话覆盖", "critical"))
    elif report.turns_total < TARGETS["turns"]:
        gaps.append(
            CoverageGap(
                "dialogue",
                "turns",
                f"对话轮数合计 {report.turns_total}，目标 {TARGETS['turns']}",
                "warn",
            )
        )

    # 6) 地点与打断策略
    if len(report.locations) < TARGETS["locations"]:
        gaps.append(
            CoverageGap(
                "location",
                "distinct",
                f"活动地点仅 {len(report.locations)} 种，建议至少 {TARGETS['locations']} 种",
                "warn",
            )
        )
    for policy, label in INTERRUPT_POLICIES.items():
        if not report.interrupt_policies.get(policy):
            gaps.append(CoverageGap("interrupt_policy", policy, f"打断策略「{label}」没有样本", "warn"))

    return gaps


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def _enum_table(counter: Mapping[str, int], labels: Mapping[str, str]) -> list[str]:
    return [f"  {key:<22} {counter.get(key, 0):>3}  {label}" for key, label in labels.items()]


def _observed_table(counter: Mapping[str, int], labels: Mapping[str, str]) -> list[str]:
    if not counter:
        return ["  （暂无）"]
    return [f"  {key:<22} {counter[key]:>3}  {labels.get(key, key)}" for key in sorted(counter)]


def format_coverage(report: CoverageReport) -> str:
    """把覆盖矩阵渲染成多行中文文本（CLI 与 report 共用）。"""
    lines: list[str] = []
    total = report.total_records
    counts = " / ".join(f"{kind} {report.record_counts.get(kind, 0)}" for kind in KINDS)
    lines.append("覆盖报告（life corpus）")
    lines.append(f"记录总量：{total}（{counts}）")
    lines.append(f"合成数据：{report.synthetic_count}/{total} 条标记 synthetic=true")
    lines.append("")

    lines.append("[1] 24 小时作息（rhythm）")
    if not report.rhythm_coverage:
        lines.append("  （没有任何 rhythm 记录）")
    for record_id, minutes in report.rhythm_coverage.items():
        status = "完整" if minutes >= DAY_MINUTES else f"缺 {DAY_MINUTES - minutes} 分钟"
        lines.append(f"  {record_id:<28} {minutes:>4}/{DAY_MINUTES} 分钟  {status}")
    lines.append("")

    lines.append("[2] 日型 × 记录类型")
    header = "  " + "日型".ljust(12) + "".join(kind.ljust(14) for kind in KINDS) + "合计"
    lines.append(header)
    for day_type, label in DAY_TYPES.items():
        cells = "".join(
            f"{report.day_types_by_kind.get(kind, {}).get(day_type, 0):<14}" for kind in KINDS
        )
        lines.append(f"  {day_type:<12}{cells}{report.day_types.get(day_type, 0):>3}  {label}")
    lines.append("")

    lines.append("[3] 活动类型（activity 记录 + rhythm 片段）")
    lines.extend(_enum_table(report.activity_types, ACTIVITY_TYPES))
    lines.append("")

    lines.append("[4] 社交关系（social_scene + snippet）")
    lines.extend(_enum_table(report.relations, RELATIONS))
    lines.append("")

    lines.append("[5] 场景与对话轮数")
    lines.append(f"  social_scene 场景：{report.scene_count} 个")
    lines.append(f"  snippet 片段：{report.snippet_count} 条")
    lines.append(
        f"  对话轮数合计：{report.turns_total}"
        f"（场景 {report.scene_turns_total} + 片段 {report.snippet_turns_total}）"
    )
    if report.scene_count:
        lines.append(f"  平均每场景轮数：{report.scene_turns_total / report.scene_count:.1f}")
    if report.turns_by_relation:
        lines.append("  关系 × 轮数：")
        for relation, turns in sorted(report.turns_by_relation.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"    {relation:<22} {turns:>4}  {RELATIONS.get(relation, '')}")
    lines.append("")

    lines.append("[6] 场景设定与打断策略")
    lines.append("  场景设定（setting）：")
    lines.extend(_observed_table(report.settings, SETTINGS))
    lines.append("  打断策略（interrupt_policy）：")
    lines.extend(_enum_table(report.interrupt_policies, INTERRUPT_POLICIES))
    lines.append("  活动地点（location）：")
    lines.extend(_observed_table(report.locations, LOCATIONS))
    lines.append("")

    lines.append(f"[7] 缺口清单（共 {len(report.gaps)} 项，其中严重 {report.critical_gap_count} 项）")
    if not report.gaps:
        lines.append("  无缺口，覆盖达标。")
    else:
        for gap in report.gaps:
            lines.append(f"  {gap.format()}")
    return "\n".join(lines)
