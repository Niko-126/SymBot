"""记录 schema：四种语料（rhythm / activity / social_scene / snippet）的字段定义与校验。

设计约定：
- 校验函数只返回 :class:`FieldError` 列表，不抛异常；空列表表示记录合法。
- 枚举统一写成 ``dict[str, str]``（取值 -> 中文说明），既用于成员判断，也用于 README/报告渲染。
- 错误码（``code``）保持稳定，CLI、测试和后续训练脚本都可以直接断言，例如
  ``missing`` / ``bad_type`` / ``bad_enum`` / ``out_of_range`` / ``rhythm_gap``。
- 本模块只依赖标准库，不导入项目其它模块，方便单独复用。
﻿​﻿‍‍‍‌‍​‍‍​﻿‌‍‍‌‍​‍‌‌‌​‍﻿‌‌​﻿‍​‌﻿"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

# ---------------------------------------------------------------------------
# 枚举定义（取值 -> 中文说明）
# ---------------------------------------------------------------------------

DAY_TYPES: Final[dict[str, str]] = {
    "workday": "工作日（上班或上学）",
    "restday": "休息日（周末在家）",
    "holiday": "节假日（法定假日、出行）",
    "travel": "出差或旅行在外",
    "special": "特殊日（生病、通宵、加班等异常作息）",
}

ACTIVITY_TYPES: Final[dict[str, str]] = {
    "sleep": "睡眠",
    "meal": "吃饭",
    "work": "工作",
    "study": "学习",
    "commute": "通勤",
    "exercise": "运动",
    "chores": "家务",
    "leisure": "休闲娱乐",
    "social": "线下社交",
    "screen": "刷手机/看视频",
    "selfcare": "洗漱打扮等自我照料",
    "errand": "跑腿办事",
    "other": "其它",
}

TIME_OF_DAY: Final[dict[str, str]] = {
    "early_morning": "清晨（05:00-08:00）",
    "morning": "上午（08:00-11:00）",
    "noon": "中午（11:00-13:00）",
    "afternoon": "下午（13:00-17:00）",
    "evening": "傍晚（17:00-21:00）",
    "night": "夜间（21:00-24:00）",
    "late_night": "深夜（00:00-05:00）",
}

LOCATIONS: Final[dict[str, str]] = {
    "home": "家里",
    "office": "公司",
    "school": "学校",
    "cafe": "咖啡馆",
    "restaurant": "餐馆",
    "street": "街头",
    "transit": "路上/交通工具",
    "gym": "健身房",
    "store": "商店/超市",
    "outdoor": "户外",
    "online": "线上",
    "other": "其它",
}

RELATIONS: Final[dict[str, str]] = {
    "family": "家人",
    "partner": "伴侣",
    "friend": "朋友",
    "colleague": "同事",
    "classmate": "同学",
    "stranger": "陌生人",
    "service": "服务人员（店员、外卖员等）",
    "online_friend": "网友",
    "neighbor": "邻居",
}

SETTINGS: Final[dict[str, str]] = {
    "home": "家里",
    "workplace": "工作场所",
    "school": "学校",
    "restaurant": "餐馆",
    "cafe": "咖啡馆",
    "street": "街头",
    "transit": "交通工具上",
    "party": "聚会场合",
    "online": "线上",
    "store": "商店",
    "other": "其它",
}

INITIATORS: Final[dict[str, str]] = {
    "self": "本人先开口",
    "other": "对方先开口",
    "mutual": "双方同时/自然开始",
    "unknown": "无法判断",
}

INTERRUPT_POLICIES: Final[dict[str, str]] = {
    "allow": "可以随时打断并切换话题",
    "defer": "先记下，等当前事情做完再回应",
    "deny": "当下不接话，直接拒绝或无视",
    "ask_later": "先答应，约好稍后再说",
}

ROLES: Final[dict[str, str]] = {
    "self": "语料主角（AI 扮演的一方）",
    "other": "对话中的另一方",
    "unknown": "角色不明（旁白、群聊里的第三人）",
}

# ---------------------------------------------------------------------------
# 语料类型
# ---------------------------------------------------------------------------

KIND_RHYTHM: Final[str] = "rhythm"
KIND_ACTIVITY: Final[str] = "activity"
KIND_SOCIAL_SCENE: Final[str] = "social_scene"
KIND_SNIPPET: Final[str] = "snippet"

KINDS: Final[tuple[str, ...]] = (KIND_RHYTHM, KIND_ACTIVITY, KIND_SOCIAL_SCENE, KIND_SNIPPET)

KIND_LABELS: Final[dict[str, str]] = {
    KIND_RHYTHM: "作息切片（一天的时间分配）",
    KIND_ACTIVITY: "单条活动记录",
    KIND_SOCIAL_SCENE: "社交场景（谁、在哪、聊什么、结果如何）",
    KIND_SNIPPET: "对话片段（可直接喂给模型的短对话）",
}

# ---------------------------------------------------------------------------
# 数值与长度边界
# ---------------------------------------------------------------------------

DAY_MINUTES: Final[int] = 24 * 60

MIN_SEGMENTS: Final[int] = 2
MAX_SEGMENTS: Final[int] = 48
MIN_PARTICIPANTS: Final[int] = 2
MAX_PARTICIPANTS: Final[int] = 30
MAX_SCENE_TURNS: Final[int] = 200
MIN_DURATION_MIN: Final[int] = 1
MAX_DURATION_MIN: Final[int] = 24 * 60
MIN_MESSAGES: Final[int] = 2
MAX_MESSAGES: Final[int] = 40
MAX_MESSAGE_LEN: Final[int] = 500
MAX_NOTE_LEN: Final[int] = 300
MAX_TOPIC_LEN: Final[int] = 120
MAX_CONTEXT_LEN: Final[int] = 200
MAX_OUTCOME_LEN: Final[int] = 300
MIN_ENERGY: Final[int] = 1
MAX_ENERGY: Final[int] = 5

# ---------------------------------------------------------------------------
# 字段白名单
# ---------------------------------------------------------------------------

_COMMON_FIELDS: Final[frozenset[str]] = frozenset({"id", "kind", "note", "synthetic"})

_SPEC_FIELDS: Final[dict[str, frozenset[str]]] = {
    KIND_RHYTHM: _COMMON_FIELDS | {"day_type", "segments"},
    KIND_ACTIVITY: _COMMON_FIELDS
    | {"day_type", "activity_type", "time_of_day", "duration_min", "location", "alone", "energy"},
    KIND_SOCIAL_SCENE: _COMMON_FIELDS
    | {
        "day_type",
        "relation",
        "setting",
        "participants",
        "turns",
        "initiator",
        "interrupt_policy",
        "topic",
        "outcome",
    },
    KIND_SNIPPET: _COMMON_FIELDS
    | {"day_type", "relation", "context", "messages", "interrupt_policy"},
}

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_\-]{1,63}$")
_HHMM_PATTERN: Final = re.compile(r"^([0-9]{1,2}):([0-5][0-9])$")


def parse_hhmm(value: str) -> int | None:
    """把 ``HH:MM`` 解析成当天分钟数（00:00 -> 0，24:00 -> 1440）；非法返回 ``None``。"""
    match = _HHMM_PATTERN.match(value)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour == 24:
        return DAY_MINUTES if minute == 0 else None
    if hour > 23:
        return None
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    """分钟数转回 ``HH:MM`` 文本（1440 显示为 24:00）。"""
    bounded = max(0, min(int(minutes), DAY_MINUTES))
    return f"{bounded // 60:02d}:{bounded % 60:02d}"


def snippet_turns(messages: Any) -> int:
    """对话轮数：以 ``role == "self"`` 的回应次数计（一问一答算 1 轮）。"""
    if not isinstance(messages, list):
        return 0
    return sum(1 for message in messages if isinstance(message, Mapping) and message.get("role") == "self")


# ---------------------------------------------------------------------------
# 错误对象与检查器
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldError:
    """单条字段错误。``field`` 为空表示整条记录级错误（如类型未知）。"""

    field: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}" if self.field else self.message


class _Checker:
    """累积式字段检查器：一次把一条记录的所有问题报全，而不是遇到第一个错误就停。"""

    def __init__(self, record: Mapping[str, Any], allowed: frozenset[str]) -> None:
        self._record = record
        self._allowed = allowed
        self.errors: list[FieldError] = []

    # -- 基础 ------------------------------------------------------------

    def add(self, field: str, code: str, message: str) -> None:
        self.errors.append(FieldError(field=field, code=code, message=message))

    def reject_unknown(self) -> None:
        for key in self._record:
            if key not in self._allowed:
                self.add(str(key), "unknown_field", f"未知字段，允许的字段为：{', '.join(sorted(self._allowed))}")

    # -- 单字段 ----------------------------------------------------------

    def text(
        self,
        field: str,
        *,
        maximum: int,
        minimum: int = 1,
        required: bool = True,
    ) -> None:
        if field not in self._record:
            if required:
                self.add(field, "missing", "缺少必填字段")
            return
        value = self._record[field]
        if not isinstance(value, str):
            self.add(field, "bad_type", f"应为字符串，实际是 {type(value).__name__}")
            return
        if len(value.strip()) < minimum:
            self.add(field, "too_short", f"长度不足（至少 {minimum} 个字符）")
        elif len(value) > maximum:
            self.add(field, "too_long", f"长度超限（最多 {maximum} 个字符，实际 {len(value)}）")

    def enum(self, field: str, allowed: Mapping[str, str], *, required: bool = True) -> None:
        if field not in self._record:
            if required:
                self.add(field, "missing", "缺少必填字段")
            return
        value = self._record[field]
        if not isinstance(value, str):
            self.add(field, "bad_type", f"应为字符串，实际是 {type(value).__name__}")
            return
        if value not in allowed:
            self.add(field, "bad_enum", f"取值 {value!r} 不在允许集合 {{{', '.join(allowed)}}} 内")

    def integer(
        self,
        field: str,
        *,
        minimum: int,
        maximum: int,
        required: bool = True,
    ) -> None:
        if field not in self._record:
            if required:
                self.add(field, "missing", "缺少必填字段")
            return
        value = self._record[field]
        # bool 是 int 的子类，这里显式排除，避免 true/false 被当成 1/0
        if isinstance(value, bool) or not isinstance(value, int):
            self.add(field, "bad_type", f"应为整数，实际是 {type(value).__name__}")
            return
        if value < minimum or value > maximum:
            self.add(field, "out_of_range", f"数值 {value} 超出范围 [{minimum}, {maximum}]")

    def boolean(self, field: str, *, required: bool = False) -> None:
        if field not in self._record:
            if required:
                self.add(field, "missing", "缺少必填字段")
            return
        if not isinstance(self._record[field], bool):
            self.add(field, "bad_type", f"应为布尔值，实际是 {type(self._record[field]).__name__}")


# ---------------------------------------------------------------------------
# 各语料类型的校验
# ---------------------------------------------------------------------------


def _validate_common(checker: _Checker, record: Mapping[str, Any], kind: str) -> None:
    if "kind" in record and record["kind"] != kind:
        checker.add("kind", "kind_mismatch", f"kind 字段应为 {kind!r}，实际是 {record['kind']!r}")
    checker.text("id", maximum=64, minimum=2)
    identifier = record.get("id")
    if isinstance(identifier, str) and not _ID_PATTERN.match(identifier):
        checker.add("id", "bad_id", "id 需以小写字母开头，仅含小写字母/数字/下划线/连字符，长度 2-64")
    checker.boolean("synthetic")


def _validate_rhythm(checker: _Checker, record: Mapping[str, Any]) -> None:
    checker.enum("day_type", DAY_TYPES)
    checker.text("note", maximum=MAX_NOTE_LEN, required=False)

    segments = record.get("segments")
    if "segments" not in record:
        checker.add("segments", "missing", "缺少必填字段")
        return
    if not isinstance(segments, list):
        checker.add("segments", "bad_type", f"应为数组，实际是 {type(segments).__name__}")
        return
    if len(segments) < MIN_SEGMENTS or len(segments) > MAX_SEGMENTS:
        checker.add("segments", "out_of_range", f"时间段数量 {len(segments)} 超出范围 [{MIN_SEGMENTS}, {MAX_SEGMENTS}]")

    previous_end = 0
    tiling_ok = True
    for index, segment in enumerate(segments):
        prefix = f"segments[{index}]"
        if not isinstance(segment, Mapping):
            checker.add(prefix, "bad_type", "每个时间段必须是 JSON 对象")
            tiling_ok = False
            continue
        extra = set(segment) - {"start", "end", "label"}
        if extra:
            checker.add(prefix, "unknown_field", f"未知字段：{', '.join(sorted(extra))}")
            tiling_ok = False

        start_raw = segment.get("start")
        end_raw = segment.get("end")
        label = segment.get("label")
        start = parse_hhmm(start_raw) if isinstance(start_raw, str) else None
        end = parse_hhmm(end_raw) if isinstance(end_raw, str) else None

        if start is None:
            checker.add(f"{prefix}.start", "bad_time", f"时间格式应为 HH:MM，实际是 {start_raw!r}")
            tiling_ok = False
        if end is None:
            checker.add(f"{prefix}.end", "bad_time", f"时间格式应为 HH:MM，实际是 {end_raw!r}")
            tiling_ok = False
        if not isinstance(label, str):
            code = "missing" if "label" not in segment else "bad_type"
            checker.add(f"{prefix}.label", code, "label 必须是活动类型字符串")
            tiling_ok = False
        elif label not in ACTIVITY_TYPES:
            checker.add(f"{prefix}.label", "bad_enum", f"label {label!r} 不在活动类型枚举内")
            tiling_ok = False

        if start is None or end is None:
            continue
        if start >= end:
            checker.add(prefix, "bad_range", f"结束时间必须晚于开始时间（{start_raw} -> {end_raw}）")
            tiling_ok = False
        if index == 0 and start != 0:
            checker.add(prefix, "rhythm_gap", f"第一个时间段必须从 00:00 开始，实际是 {start_raw}")
            tiling_ok = False
        elif index > 0 and start != previous_end:
            checker.add(prefix, "rhythm_gap", f"时间段不连续：应从 {format_hhmm(previous_end)} 开始，实际是 {start_raw}")
            tiling_ok = False
        previous_end = end

    if tiling_ok and segments:
        last = segments[-1]
        if isinstance(last, Mapping):
            last_end = last.get("end")
            if isinstance(last_end, str) and parse_hhmm(last_end) != DAY_MINUTES:
                checker.add("segments", "rhythm_gap", f"最后一个时间段必须到 24:00 结束，实际是 {last_end}")


def _validate_activity(checker: _Checker, record: Mapping[str, Any]) -> None:
    checker.enum("day_type", DAY_TYPES)
    checker.enum("activity_type", ACTIVITY_TYPES)
    checker.enum("time_of_day", TIME_OF_DAY)
    checker.integer("duration_min", minimum=MIN_DURATION_MIN, maximum=MAX_DURATION_MIN)
    checker.enum("location", LOCATIONS, required=False)
    checker.boolean("alone")
    checker.integer("energy", minimum=MIN_ENERGY, maximum=MAX_ENERGY, required=False)
    checker.text("note", maximum=MAX_NOTE_LEN, required=False)


def _validate_social_scene(checker: _Checker, record: Mapping[str, Any]) -> None:
    checker.enum("day_type", DAY_TYPES, required=False)
    checker.enum("relation", RELATIONS)
    checker.enum("setting", SETTINGS)
    checker.integer("participants", minimum=MIN_PARTICIPANTS, maximum=MAX_PARTICIPANTS)
    checker.integer("turns", minimum=0, maximum=MAX_SCENE_TURNS)
    checker.enum("initiator", INITIATORS)
    checker.enum("interrupt_policy", INTERRUPT_POLICIES)
    checker.text("topic", maximum=MAX_TOPIC_LEN)
    checker.text("outcome", maximum=MAX_OUTCOME_LEN, required=False)
    checker.text("note", maximum=MAX_NOTE_LEN, required=False)


def _validate_snippet(checker: _Checker, record: Mapping[str, Any]) -> None:
    checker.enum("day_type", DAY_TYPES, required=False)
    checker.enum("relation", RELATIONS)
    checker.text("context", maximum=MAX_CONTEXT_LEN)
    checker.enum("interrupt_policy", INTERRUPT_POLICIES)
    checker.text("note", maximum=MAX_NOTE_LEN, required=False)

    messages = record.get("messages")
    if "messages" not in record:
        checker.add("messages", "missing", "缺少必填字段")
        return
    if not isinstance(messages, list):
        checker.add("messages", "bad_type", f"应为数组，实际是 {type(messages).__name__}")
        return
    if len(messages) < MIN_MESSAGES or len(messages) > MAX_MESSAGES:
        checker.add(
            "messages",
            "out_of_range",
            f"消息条数 {len(messages)} 超出范围 [{MIN_MESSAGES}, {MAX_MESSAGES}]",
        )

    roles: list[str] = []
    for index, message in enumerate(messages):
        prefix = f"messages[{index}]"
        if not isinstance(message, Mapping):
            checker.add(prefix, "bad_type", "每条消息必须是 JSON 对象")
            continue
        extra = set(message) - {"role", "text"}
        if extra:
            checker.add(prefix, "unknown_field", f"未知字段：{', '.join(sorted(extra))}")
        role = message.get("role")
        if not isinstance(role, str):
            code = "missing" if "role" not in message else "bad_type"
            checker.add(f"{prefix}.role", code, "role 必须是字符串")
        elif role not in ROLES:
            checker.add(f"{prefix}.role", "bad_enum", f"role {role!r} 不在允许集合 {{{', '.join(ROLES)}}} 内")
        else:
            roles.append(role)
        text = message.get("text")
        if not isinstance(text, str):
            code = "missing" if "text" not in message else "bad_type"
            checker.add(f"{prefix}.text", code, "text 必须是字符串")
        elif not text.strip():
            checker.add(f"{prefix}.text", "too_short", "text 不能为空")
        elif len(text) > MAX_MESSAGE_LEN:
            checker.add(f"{prefix}.text", "too_long", f"text 超过 {MAX_MESSAGE_LEN} 个字符")

    if "self" not in roles:
        checker.add("messages", "no_self_turn", "至少需要一条 role=self 的回应")
    if "other" not in roles:
        checker.add("messages", "no_partner", "至少需要一条 role=other 的对方发言")


_VALIDATORS: Final[dict[str, Any]] = {
    KIND_RHYTHM: _validate_rhythm,
    KIND_ACTIVITY: _validate_activity,
    KIND_SOCIAL_SCENE: _validate_social_scene,
    KIND_SNIPPET: _validate_snippet,
}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def validate_record(kind: str, record: Any) -> list[FieldError]:
    """校验一条记录；返回错误列表，空列表表示合法。"""
    if kind not in KINDS:
        return [FieldError("", "bad_kind", f"未知语料类型 {kind!r}，可选：{', '.join(KINDS)}")]
    if not isinstance(record, Mapping):
        return [FieldError("", "bad_type", f"记录必须是 JSON 对象，实际是 {type(record).__name__}")]

    allowed = _SPEC_FIELDS[kind]
    checker = _Checker(record, allowed)
    checker.reject_unknown()
    _validate_common(checker, record, kind)
    _VALIDATORS[kind](checker, record)
    return checker.errors


def field_spec(kind: str) -> frozenset[str]:
    """返回某类记录允许出现的全部字段名（供文档/报告使用）。"""
    return _SPEC_FIELDS.get(kind, frozenset())
