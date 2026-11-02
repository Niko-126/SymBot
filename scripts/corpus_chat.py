"""把本机微信聊天导出（`Chat/`，已 gitignore）转成"人类社交作息"语料。

用途（docs/52 的社交包）：从真实私聊里抽两样东西——
1) 聚合统计（`chat_stats.json`）：几点睡、几点回消息、谁先开口、周末 vs 工作日，
   全部是数字和哈希前缀，**不含任何聊天原文**；
2) 可训练的对话片段（`chat.social_scene.jsonl` / `chat.snippet.jsonl`）：
   每句话先按项目 `app/lifecorpus/pii.mask_text` 掩码，再把已知昵称/账号整体替换掉。

铁律（隐私红线）：
- 只读 `Chat/`，绝不复制、移动、提交原始 json；
- 屏幕输出只有数字、时间、哈希前缀，**不打印昵称 / wxid / 正文**；
- 产出只写 `data/human_life/_derived/`（`data/` 已在 .gitignore）；
- 单个文件读坏了就跳过并计数，不中断整轮、不向外抛异常。

判定"自己"的方法：**跨所有导出里出现次数最多的 sender**。导出里 owner 的 platformId 是 uuid，
而消息的 sender 是 wxid，两者对不上；同一个人在所有会话里都是同一个 wxid，所以按频次取最大
最稳。脚本会把该 wxid 的 sha256 前缀（不解密、不显示原值）写进统计里当证据。

用法（仓库根目录）：
    python scripts/corpus_chat.py            # dry-run：只打印聚合与基线对比，不写文件
    python scripts/corpus_chat.py --write    # 写 _derived 并自动跑 life_corpus.py validate --strict
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/corpus_chat.py`
    sys.path.insert(0, str(ROOT))

from app.lifecorpus.pii import mask_text  # noqa: E402

CHAT_DIR: Path = ROOT / "Chat"
OUT_DIR: Path = ROOT / "data" / "human_life" / "_derived"
STATS_PATH: Path = OUT_DIR / "chat_stats.json"
# 文件名必须带 kind token，否则 app/lifecorpus/validate.py 的 infer_kind 认不出类型
SCENE_PATH: Path = OUT_DIR / "chat.social_scene.jsonl"
SNIPPET_PATH: Path = OUT_DIR / "chat.snippet.jsonl"

# 导出里的 timestamp 是 UTC 秒，展示时必须换成本地时间（中国固定 UTC+8，不依赖 tzdata）
TZ = timezone(timedelta(hours=8), "UTC+8")

SESSION_GAP_SECONDS: int = 30 * 60          # 相邻消息间隔超过 30 分钟 = 新会话
MAX_SCENES: int = 300                       # social_scene 上限
MAX_SNIPPETS: int = 200                     # snippet 上限
MAX_TEXT_CHARS: int = 120                   # 单句最多保留多少字（掩码后截断）
SNIPPET_MIN_MSGS: int = 4                   # 片段最少几条消息（2 轮问答）
SNIPPET_MAX_MSGS: int = 6                   # 片段最多几条消息（3 轮）
MAX_SCENE_TURNS: int = 200                  # 与 schema.MAX_SCENE_TURNS 保持一致

# 《2026 年度报告》里的口径，用于自检（数值取自报告图片）
BASELINE: dict[str, Any] = {
    "self_start_ratio": 533 / 1437,         # 你开启 37% 对话（533/1437）
    "self_reply_seconds": 9 * 60 + 47,      # 你平均 9分47秒 回复（10,307 次）
    "other_reply_seconds": 17 * 60 + 21,    # 对方平均 17分21秒（9,977 次）
    "self_reply_count": 10307,
    "other_reply_count": 9977,
    "deep_night_ratio": 0.15,               # 深夜 00:00-05:59 占 15%
    "deep_night_count": 8268,
    "top_cell_weekday": 4,                  # 周五（周一=0）
    "top_cell_hour": 19,                    # 19:00-20:00
    "top_cell_count": 1251,
}

WEEKDAY_NAMES: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 正文里还需要另外打码的东西：链接、wxid、uuid、长数字串（QQ/单号/日期）、@ 提及
_NOISE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"https?://\S+"), "[链接]"),
    (re.compile(r"wxid_[0-9A-Za-z_\-]{3,}"), "[账号]"),
    (
        re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
        "[账号]",
    ),
    (re.compile(r"(?<!\d)\d{6,}(?!\d)"), "****"),
    (re.compile(r"@[\w\u4e00-\u9fff]{1,20}"), "@某人"),
)

# 话题分类：只做关键词打分，**绝不把原文写进 topic**（避免昵称/隐私随 topic 泄露）
_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("问候寒暄", ("在吗", "在不在", "你好", "哈喽", "晚安", "早安", "睡了", "醒了", "早上好")),
    ("约时间见面", ("几点", "什么时间", "明天", "后天", "周末", "见面", "出发", "等我", "到了", "过来")),
    ("吃饭饮食", ("吃", "饭", "外卖", "奶茶", "喝", "夜宵", "早餐", "午饭", "晚饭", "零食")),
    ("工作事务", ("上班", "加班", "工作", "老板", "公司", "客户", "项目", "店里", "单子", "开会")),
    ("金钱转账", ("转账", "红包", "多少钱", "付款", "收款", "借", "还钱", "运费", "价格")),
    ("学习课程", ("作业", "考试", "上课", "课程", "学习", "老师", "学校", "复习", "教程")),
    ("物流快递", ("快递", "发货", "顺丰", "取件", "地址", "到货", "签收", "包裹")),
    ("游戏娱乐", ("游戏", "王者", "开黑", "上号", "打一把", "视频", "电影", "看剧", "直播")),
)
_TOPIC_FALLBACK: str = "日常闲聊"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _ensure_utf8_stdout() -> None:
    """重定向到管道/文件时强制 UTF-8，避免 Windows 控制台默认 cp936 把中文写成乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):  # 极少数环境不允许重配，忽略即可
        pass


def _sha_prefix(value: str, length: int = 10) -> str:
    """只保留哈希前缀：够用来当证据，又不可逆。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _local_time(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=TZ)


def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("\u3000", " ").split())


def _fmt_seconds(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}分{total % 60}秒"


def _percentile(values: list[float], q: float) -> float:
    """分位数（最近秩法，够用且稳定）；空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    return float(ordered[max(0, min(index, len(ordered) - 1))])


def _ratio(part: int, whole: int) -> float:
    return (part / whole) if whole else 0.0


# ---------------------------------------------------------------------------
# 读取导出
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """一个会话：同一导出文件内、相邻间隔不超过 30 分钟的一段连续聊天。"""

    file_no: int                 # 匿名化后的导出编号（01、02…），不是原文件名
    seq_in_file: int             # 该文件里的第几段
    start_ts: int
    end_ts: int
    roles: list[str] = field(default_factory=list)   # "self" / "other"
    texts: list[str] = field(default_factory=list)   # 原文，只在内存里，落盘前必过 scrub
    senders: set[str] = field(default_factory=set)
    #: 从导出目录名后缀推出来的关系（用户 2026-10-31 用"名字 空格 关系"标注）
    relation: str = "friend"
    relation_labeled: bool = False
    #: 这一段对话里的"被打断时怎么处理"（关键词推定；没有信号就是 allow）
    interrupt_policy: str = "allow"
    policy_evidence: bool = False

    @property
    def message_count(self) -> int:
        return len(self.roles)

    @property
    def self_turns(self) -> int:
        return sum(1 for role in self.roles if role == "self")

    @property
    def start_local(self) -> datetime:
        return _local_time(self.start_ts)


@dataclass
class ExportFile:
    """一个导出文件读出来后的最小信息 + 全部消息。"""

    file_no: int
    path: Path
    chat_type: str
    messages: list[dict[str, Any]]
    relation: str = "friend"
    relation_labeled: bool = False


#: 目录名后缀 -> schema 关系枚举。用户把关系写在文件夹名最后，空格隔开（如「XXX 同学」）。
#: 顺序 = 优先级：一个目录同时写了「同学、死党」时按 classmate 算（更具体的那类）。
_RELATION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("family", ("一家人", "家人", "爸爸", "妈妈", "奶奶", "姑姑", "岳父", "father", "mother", "sister")),
    ("partner", ("老公", "老婆")),
    ("service", ("快递员", "维修师傅", "律师", "银行客户经理", "客户经理", "客服", "师傅")),
    ("colleague", ("工作群聊", "工作号", "客户", "老板", "甲方", "同事")),
    ("classmate", ("同学",)),
    ("online_friend", ("游戏好友", "网友")),
    ("stranger", ("陌生人",)),
    ("friend", ("死党", "好朋友", "朋友", "闺蜜", "军师")),
)
#: 「老婆的闺蜜 / 老婆的好朋友」算朋友，不算伴侣（先剔掉再判 partner）
_PARTNER_EXCLUDE = re.compile(r"老婆的(?:闺蜜|好朋友|朋友)|老公的(?:死党|朋友|兄弟)")

#: 打断策略关键词：只在会话里出现才推定，没信号就 allow（不硬造）
_POLICY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ask_later", re.compile(r"(明天再说|回头说|改天聊|下次聊|晚点聊|回头看|再说吧)")),
    ("defer", re.compile(r"(在忙|开会|稍等|等一下|等会|晚点|回聊|忙完)")),
    ("deny", re.compile(r"(不想聊|别发了|别说了|没空|不想理|闭嘴)")),
)


def _detect_policy(texts: list[str]) -> tuple[str, bool]:
    """从会话里的第一人称口吻信号推"被打断时怎么处理"。没有信号返回 (allow, False)。"""
    blob = " ".join(str(text or "") for text in texts[:60])
    for policy, pattern in _POLICY_RULES:
        if pattern.search(blob):
            return policy, True
    return "allow", False


def _finalize_session(session: Session) -> None:
    session.interrupt_policy, session.policy_evidence = _detect_policy(session.texts)


def relation_from_folder(folder_name: str) -> tuple[str, bool]:
    """从导出目录名推关系。返回 (schema 关系, 是否真的读到标签)。

    只认枚举词；没有标签就按 friend。目录名里的真名不会进输出。
    """
    raw = str(folder_name or "")
    text = _PARTNER_EXCLUDE.sub(" ", raw)
    for relation, keywords in _RELATION_RULES:
        if any(keyword in text for keyword in keywords):
            return relation, True
    # 「老婆的闺蜜」这类被剔掉后什么都不剩，但仍算有标签 → friend
    if any(keyword in raw for keyword in ("闺蜜", "朋友", "死党", "老婆", "老公")):
        return "friend", True
    return "friend", False


def _looks_like_export(payload: Any) -> bool:
    """只认含 meta / members / messages 的结构，别的 json 一律跳过。"""
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("meta"), dict) and isinstance(payload.get("messages"), list)


def load_exports(chat_dir: Path = CHAT_DIR) -> tuple[list[ExportFile], int, int]:
    """按路径顺序扫描导出。返回 (文件列表, 跳过的坏文件数, 跳过的不像导出的文件数)。

    编号按**扫描顺序**（01、02…）；原文件名与真实昵称不会出现在任何输出里。
    """
    exports: list[ExportFile] = []
    if not chat_dir.exists():
        return exports, 0, 0

    broken = 0
    ignored = 0
    candidates = sorted(
        (path for path in chat_dir.rglob("*.json") if path.is_file()),
        key=lambda path: str(path).lower(),
    )
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            broken += 1
            continue
        if not _looks_like_export(payload):
            ignored += 1
            continue
        meta = payload.get("meta") or {}
        relation, labeled = relation_from_folder(path.parent.name)
        exports.append(
            ExportFile(
                file_no=len(exports) + 1,
                path=path,
                chat_type=str(meta.get("type") or ""),
                messages=[m for m in payload.get("messages") or [] if isinstance(m, dict)],
                relation=relation,
                relation_labeled=labeled,
            )
        )
    return exports, broken, ignored


def _collect_sensitive_terms(exports: list[ExportFile]) -> set[str]:
    """收集所有已知标识（会话名、成员账号、成员 id、每条消息的 sender），用于正文替换。

    只在这个进程的内存里用，不落盘、不打印。
    """
    terms: set[str] = set()
    for export in exports:
        meta = {}
        try:
            with export.path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            meta = payload.get("meta") or {}
            members = payload.get("members") or []
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            members = []
        for value in (meta.get("name"), meta.get("ownerId")):
            if isinstance(value, str) and len(value) >= 2:
                terms.add(value)
        for member in members if isinstance(members, list) else []:
            if not isinstance(member, dict):
                continue
            for value in (member.get("accountName"), member.get("platformId"), member.get("name")):
                if isinstance(value, str) and len(value) >= 2:
                    terms.add(value)
        for message in export.messages:
            for value in (message.get("sender"), message.get("accountName")):
                if isinstance(value, str) and len(value) >= 2:
                    terms.add(value)
    return terms


class Scrubber:
    """正文脱敏：已知昵称/账号 -> [某人]，再打掉链接/wxid/长数字，最后走项目 PII 掩码。"""

    def __init__(self, terms: set[str], replacement: str = "[某人]") -> None:
        ordered = sorted((term for term in terms if len(term) >= 2), key=len, reverse=True)
        self._names = re.compile("|".join(re.escape(term) for term in ordered)) if ordered else None
        self._replacement = replacement

    def __call__(self, text: str) -> str:
        value = _clean(text)
        if not value:
            return ""
        if self._names is not None:
            value = self._names.sub(self._replacement, value)
        for pattern, replacement in _NOISE_PATTERNS:
            value = pattern.sub(replacement, value)
        value, _ = mask_text(value)  # 手机号 / 身份证 / 邮箱
        value = _clean(value)
        return value[:MAX_TEXT_CHARS]


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def detect_self(exports: list[ExportFile]) -> tuple[str, dict[str, Any]]:
    """判定"自己"：跨所有导出出现次数最多的 sender（并给出可复核的证据）。"""
    totals: Counter[str] = Counter()
    per_file: dict[str, int] = Counter()  # type: ignore[assignment]
    for export in exports:
        senders_in_file: set[str] = set()
        for message in export.messages:
            sender = message.get("sender")
            if not isinstance(sender, str) or not sender:
                continue
            totals[sender] += 1
            senders_in_file.add(sender)
        for sender in senders_in_file:
            per_file[sender] = per_file.get(sender, 0) + 1

    if not totals:
        return "", {"sha256_prefix": "", "message_count": 0, "total_messages": 0,
                    "share": 0.0, "files_covered": 0, "files_total": len(exports),
                    "runner_up_sha256_prefix": "", "runner_up_message_count": 0}

    ranked = totals.most_common(2)
    self_id, self_count = ranked[0]
    total_messages = sum(totals.values())
    evidence: dict[str, Any] = {
        "sha256_prefix": _sha_prefix(self_id),
        "message_count": self_count,
        "total_messages": total_messages,
        "share": round(_ratio(self_count, total_messages), 4),
        "files_covered": per_file.get(self_id, 0),
        "files_total": len(exports),
        "runner_up_sha256_prefix": _sha_prefix(ranked[1][0]) if len(ranked) > 1 else "",
        "runner_up_message_count": ranked[1][1] if len(ranked) > 1 else 0,
    }
    return self_id, evidence


def build_sessions(export: ExportFile, self_id: str) -> list[Session]:
    """按时间排序后切会话：相邻间隔 > 30 分钟就开新的一段。"""
    rows: list[tuple[int, str, str, str]] = []
    for index, message in enumerate(export.messages):
        sender = message.get("sender")
        content = message.get("content")
        timestamp = message.get("timestamp")
        if not isinstance(sender, str) or not isinstance(content, str):
            continue
        if not isinstance(timestamp, int):
            continue
        text = _clean(content)
        if not text:
            continue
        role = "self" if sender == self_id else "other"
        rows.append((timestamp, role, text, sender))

    rows.sort(key=lambda row: row[0])
    sessions: list[Session] = []
    current: Session | None = None
    for timestamp, role, text, sender in rows:
        if current is None or timestamp - current.end_ts > SESSION_GAP_SECONDS:
            if current is not None:
                _finalize_session(current)
                sessions.append(current)
            current = Session(
                file_no=export.file_no,
                seq_in_file=len(sessions) + 1,
                start_ts=timestamp,
                end_ts=timestamp,
                relation=export.relation,
                relation_labeled=export.relation_labeled,
            )
        current.end_ts = max(current.end_ts, timestamp)
        current.roles.append(role)
        current.texts.append(text)
        current.senders.add(sender)
    if current is not None:
        _finalize_session(current)
        sessions.append(current)
    return sessions


def _classify_topic(session: Session) -> str:
    """按关键词给会话打一个**固定标签**（不输出任何原文）。"""
    scores: Counter[str] = Counter()
    for role, text in zip(session.roles, session.texts):
        if role != "self":
            continue
        for label, keywords in _TOPIC_RULES:
            if any(keyword in text for keyword in keywords):
                scores[label] += 1
    if not scores:
        return _TOPIC_FALLBACK
    best = max(scores.items(), key=lambda item: (item[1], item[0]))
    return best[0]


def analyze(exports: list[ExportFile]) -> dict[str, Any]:
    """跑全部聚合统计。返回值里全是数字/时间/哈希前缀，没有任何聊天文本。"""
    self_id, self_evidence = detect_self(exports)

    grid: list[list[int]] = [[0] * 24 for _ in range(7)]   # [weekday][hour]
    deep_night = 0
    workday = 0
    restday = 0
    private_messages = 0
    group_messages = 0
    text_lengths: list[int] = []
    sessions: list[Session] = []
    self_started = 0
    self_reply: list[float] = []
    other_reply: list[float] = []
    # 对照口径：不切会话，任何相邻角色翻转都算一次回复（年度报告的数更像这个口径）
    no_split_self: list[float] = []
    no_split_other: list[float] = []
    total_messages = 0

    for export in exports:
        for message in export.messages:
            content = message.get("content")
            timestamp = message.get("timestamp")
            if not isinstance(content, str) or not isinstance(timestamp, int):
                continue
            text = _clean(content)
            if not text:
                continue
            total_messages += 1
            text_lengths.append(len(text))
            moment = _local_time(timestamp)
            weekday = moment.weekday()
            grid[weekday][moment.hour] += 1
            if moment.hour < 6:
                deep_night += 1
            if weekday >= 5:
                restday += 1
            else:
                workday += 1
            if export.chat_type == "private":
                private_messages += 1
            elif export.chat_type:
                group_messages += 1

        file_sessions = build_sessions(export, self_id)
        sessions.extend(file_sessions)

        for session in file_sessions:
            if session.roles and session.roles[0] == "self":
                self_started += 1

    # 回复时延：同会话内相邻两条、角色翻转。需要逐条时间戳，所以在导出层再扫一遍
    # （sessions 只留了段落边界，没留每条消息的时间）。
    for export in exports:
        rows: list[tuple[int, str]] = []
        for message in export.messages:
            sender = message.get("sender")
            content = message.get("content")
            timestamp = message.get("timestamp")
            if not isinstance(sender, str) or not isinstance(content, str) or not isinstance(timestamp, int):
                continue
            if not _clean(content):
                continue
            rows.append((timestamp, "self" if sender == self_id else "other"))
        rows.sort(key=lambda row: row[0])
        previous: tuple[int, str] | None = None
        for timestamp, role in rows:
            if previous is not None:
                previous_ts, previous_role = previous
                gap = float(max(0, timestamp - previous_ts))
                if role != previous_role:
                    (no_split_self if role == "self" else no_split_other).append(gap)
                    if gap <= SESSION_GAP_SECONDS:
                        (self_reply if role == "self" else other_reply).append(gap)
            previous = (timestamp, role)

    session_count = len(sessions)
    relation_counts: dict[str, int] = {}
    labeled_exports = 0
    for export in exports:
        relation_counts[export.relation] = relation_counts.get(export.relation, 0) + 1
        if export.relation_labeled:
            labeled_exports += 1
    top_cell_weekday, top_cell_hour, top_cell_count = 0, 0, 0
    for weekday in range(7):
        for hour in range(24):
            if grid[weekday][hour] > top_cell_count:
                top_cell_weekday, top_cell_hour, top_cell_count = weekday, hour, grid[weekday][hour]

    stats: dict[str, Any] = {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "source": "local_chat_export_aggregate",
        "exports_scanned": len(exports),
        "messages_total": total_messages,
        "messages_private": private_messages,
        "messages_group": group_messages,
        "self_detection": self_evidence,
        "relations": {
            "by_export": relation_counts,
            "labeled_exports": labeled_exports,
            "total_exports": len(exports),
        },
        "hourly_by_weekday": grid,
        "hourly_totals": [sum(grid[weekday][hour] for weekday in range(7)) for hour in range(24)],
        "sessions": {
            "count": session_count,
            "gap_seconds": SESSION_GAP_SECONDS,
            "self_started": self_started,
            "self_start_ratio": round(_ratio(self_started, session_count), 4),
            "mean_messages": round(_ratio(total_messages, session_count), 2),
        },
        "reply_latency_seconds": {
            "self": {
                "count": len(self_reply),
                "mean": round(mean(self_reply), 1) if self_reply else 0.0,
                "median": round(median(self_reply), 1) if self_reply else 0.0,
                "p90": round(_percentile(self_reply, 0.9), 1),
                "count_no_split": len(no_split_self),
                "mean_no_split": round(mean(no_split_self), 1) if no_split_self else 0.0,
            },
            "other": {
                "count": len(other_reply),
                "mean": round(mean(other_reply), 1) if other_reply else 0.0,
                "median": round(median(other_reply), 1) if other_reply else 0.0,
                "p90": round(_percentile(other_reply, 0.9), 1),
                "count_no_split": len(no_split_other),
                "mean_no_split": round(mean(no_split_other), 1) if no_split_other else 0.0,
            },
        },
        "message_length": {
            "count": len(text_lengths),
            "mean": round(mean(text_lengths), 2) if text_lengths else 0.0,
            "median": round(median(text_lengths), 2) if text_lengths else 0.0,
            "p90": round(_percentile([float(value) for value in text_lengths], 0.9), 2),
        },
        "deep_night": {
            "hours": "0-5",
            "count": deep_night,
            "ratio": round(_ratio(deep_night, total_messages), 4),
        },
        "day_type": {
            "workday_messages": workday,
            "restday_messages": restday,
            "restday_ratio": round(_ratio(restday, total_messages), 4),
        },
        "top_cell": {
            "weekday": top_cell_weekday,
            "hour": top_cell_hour,
            "count": top_cell_count,
        },
        "baseline": {
            "self_start_ratio": round(BASELINE["self_start_ratio"], 4),
            "self_reply_seconds": BASELINE["self_reply_seconds"],
            "other_reply_seconds": BASELINE["other_reply_seconds"],
            "deep_night_ratio": BASELINE["deep_night_ratio"],
            "top_cell_weekday": BASELINE["top_cell_weekday"],
            "top_cell_hour": BASELINE["top_cell_hour"],
            "top_cell_count": BASELINE["top_cell_count"],
        },
    }
    stats["_sessions"] = sessions  # 只给写文件阶段用，落盘前会删掉
    return stats


# ---------------------------------------------------------------------------
# 打印（dry-run）
# ---------------------------------------------------------------------------


def _verdict(actual: float, expected: float, *, absolute: bool = False, tolerance: float = 0.25) -> str:
    """对齐 / 偏差判定：比率用绝对差，时长用相对差。"""
    if absolute:
        ok = abs(actual - expected) <= tolerance
    else:
        ok = abs(actual - expected) <= max(1e-9, expected * tolerance)
    return "对齐" if ok else "偏差"


def render_report(stats: dict[str, Any], broken: int, ignored: int) -> str:
    evidence = stats["self_detection"]
    sessions = stats["sessions"]
    self_latency = stats["reply_latency_seconds"]["self"]
    other_latency = stats["reply_latency_seconds"]["other"]
    no_split_self = {"mean": self_latency.get("mean_no_split", 0.0),
                     "count": self_latency.get("count_no_split", 0)}
    no_split_other = {"mean": other_latency.get("mean_no_split", 0.0),
                      "count": other_latency.get("count_no_split", 0)}
    length = stats["message_length"]
    deep_night = stats["deep_night"]
    day_type = stats["day_type"]
    top_cell = stats["top_cell"]
    rel = stats.get("relations") or {}
    rel_counts = rel.get("by_export") or {}

    lines: list[str] = [
        "聊天导出聚合（dry-run，不写文件）",
        f"  扫描到导出 {stats['exports_scanned']} 个；坏文件跳过 {broken} 个；不像导出的 json 忽略 {ignored} 个",
        f"  消息总数 {stats['messages_total']}（私聊 {stats['messages_private']} / 群聊 {stats['messages_group']}）",
        f"  关系标签（读目录名后缀）：{rel.get('labeled_exports', 0)}/{rel.get('total_exports', 0)} 个导出有标签",
        "    " + "、".join(f"{name} {count}" for name, count in sorted(rel_counts.items())),
        "",
        "self 判定（跨所有导出出现次数最多的 sender）：",
        f"  选中 sender 的 sha256 前缀 {evidence['sha256_prefix']}：{evidence['message_count']} 条"
        f" / 共 {evidence['total_messages']} 条（{evidence['share']:.1%}），覆盖 {evidence['files_covered']}/"
        f"{evidence['files_total']} 个导出",
        f"  第二名 sha256 前缀 {evidence['runner_up_sha256_prefix']}：仅 {evidence['runner_up_message_count']} 条",
        "",
        "会话切分（间隔 > 30 分钟算新会话）：",
        f"  会话数 {sessions['count']}，你先开口 {sessions['self_started']} 次（{sessions['self_start_ratio']:.1%}）",
        f"  平均每个会话 {sessions['mean_messages']} 条消息",
        "",
        "回复时延（同会话内相邻、角色翻转）：",
        f"  你回别人：均值 {_fmt_seconds(self_latency['mean'])}（{self_latency['mean']:.0f}s）"
        f" 中位 {_fmt_seconds(self_latency['median'])} P90 {_fmt_seconds(self_latency['p90'])}"
        f" 共 {self_latency['count']} 次",
        f"  别人回你：均值 {_fmt_seconds(other_latency['mean'])}（{other_latency['mean']:.0f}s）"
        f" 中位 {_fmt_seconds(other_latency['median'])} P90 {_fmt_seconds(other_latency['p90'])}"
        f" 共 {other_latency['count']} 次",
        f"  对照（不切会话、跨天也算回复）：你 {_fmt_seconds(no_split_self['mean'])}"
        f" / 对方 {_fmt_seconds(no_split_other['mean'])}"
        f"（{no_split_self['count']} / {no_split_other['count']} 次）",
        "",
        "消息长度：",
        f"  中位 {length['median']:.0f} 字，均值 {length['mean']:.1f} 字，P90 {length['p90']:.0f} 字"
        f"（{length['count']} 条）",
        "",
        "作息分布：",
        f"  深夜 00:00-05:59 {deep_night['count']} 条（{deep_night['ratio']:.1%}）",
        f"  工作日 {day_type['workday_messages']} 条 / 休息日 {day_type['restday_messages']} 条"
        f"（休息日占 {day_type['restday_ratio']:.1%}）",
        f"  最热格子 {WEEKDAY_NAMES[top_cell['weekday']]} {top_cell['hour']:02d}:00-"
        f"{top_cell['hour'] + 1:02d}:59 共 {top_cell['count']} 条",
        "",
        "7×24 热力（行=周一到周日，列=0-23 时）：",
    ]
    grid = stats["hourly_by_weekday"]
    lines.append("       " + "".join(f"{hour:>5d}" for hour in range(24)))
    for weekday in range(7):
        cells = "".join(f"{grid[weekday][hour]:>5d}" for hour in range(24))
        lines.append(f"  {WEEKDAY_NAMES[weekday]}{cells}")

    lines.extend(["", "与《2026 年度报告》基线对比："])
    start_ok = _verdict(sessions["self_start_ratio"], BASELINE["self_start_ratio"],
                        absolute=True, tolerance=0.03)
    lines.append(
        f"  [{start_ok}] 你先开口：本导出 {sessions['self_start_ratio']:.1%}"
        f" vs 报告 {BASELINE['self_start_ratio']:.1%}（533/1437）"
    )
    self_ok = _verdict(self_latency["mean"], BASELINE["self_reply_seconds"], tolerance=0.25)
    lines.append(
        f"  [{self_ok}] 你回别人：本导出 {_fmt_seconds(self_latency['mean'])}"
        f" vs 报告 {_fmt_seconds(BASELINE['self_reply_seconds'])}"
        f"（次数 {self_latency['count']} vs {BASELINE['self_reply_count']}）"
    )
    other_ok = _verdict(other_latency["mean"], BASELINE["other_reply_seconds"], tolerance=0.25)
    lines.append(
        f"  [{other_ok}] 别人回你：本导出 {_fmt_seconds(other_latency['mean'])}"
        f" vs 报告 {_fmt_seconds(BASELINE['other_reply_seconds'])}"
        f"（次数 {other_latency['count']} vs {BASELINE['other_reply_count']}）"
    )
    night_ok = _verdict(deep_night["ratio"], BASELINE["deep_night_ratio"], absolute=True, tolerance=0.03)
    lines.append(
        f"  [{night_ok}] 深夜占比：本导出 {deep_night['ratio']:.1%} vs 报告 {BASELINE['deep_night_ratio']:.0%}"
        f"（条数 {deep_night['count']} vs {BASELINE['deep_night_count']}）"
    )
    cell_weekday_ok = top_cell["weekday"] == BASELINE["top_cell_weekday"]
    cell_hour_ok = top_cell["hour"] == BASELINE["top_cell_hour"]
    lines.append(
        f"  [{'对齐' if cell_weekday_ok and cell_hour_ok else '偏差'}] 最热格子：本导出"
        f" {WEEKDAY_NAMES[top_cell['weekday']]} {top_cell['hour']:02d} 点"
        f" vs 报告 {WEEKDAY_NAMES[BASELINE['top_cell_weekday']]} {BASELINE['top_cell_hour']:02d} 点"
        f"（条数 {top_cell['count']} vs {BASELINE['top_cell_count']}）"
    )
    lines.append(
        "  说明：报告口径覆盖全年全部聊天，本导出只是 49 个会话的快照，"
        "条数绝对值天然偏小，看占比与均值更有意义。"
    )
    lines.append(
        "  时延口径说明：报告 9分47秒 / 17分21秒 无法用「同会话内翻转」复现（本导出中位 7 秒），"
        "更像不切会话、把隔几小时的续聊也算一次回复；两种口径差距见上面的对照行。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _day_type(moment: datetime) -> str:
    return "restday" if moment.weekday() >= 5 else "workday"


def _pick_stratified(sessions: list[Session], limit: int,
                     per_relation: int = 12) -> list[Session]:
    """按关系分层挑：先保证每种关系都有样本，再按会话大小补满。

    coverage 要的是"关系多样性"，纯按消息数取 top-N 会把伴侣/陌生人这种小会话全挤掉。
    """
    buckets: dict[str, list[Session]] = {}
    for session in sessions:
        buckets.setdefault(session.relation, []).append(session)
    picked: list[Session] = []
    picked_ids: set[int] = set()
    for relation in sorted(buckets):
        items = sorted(buckets[relation], key=lambda item: (-item.message_count, item.start_ts))
        for session in items[: max(1, per_relation)]:
            picked.append(session)
            picked_ids.add(id(session))
    if len(picked) < limit:
        rest = [item for item in sessions if id(item) not in picked_ids]
        rest.sort(key=lambda item: (-item.message_count, item.start_ts))
        picked.extend(rest[: max(0, limit - len(picked))])
    return picked[:limit]


def build_scenes(sessions: list[Session], limit: int = MAX_SCENES) -> list[dict[str, Any]]:
    """会话 -> social_scene 记录。schema 里 turns 是整数（轮数），不装消息数组。"""
    ranked = _pick_stratified(sessions, limit)
    rows: list[dict[str, Any]] = []
    for index, session in enumerate(ranked, start=1):
        initiator = "self" if session.roles and session.roles[0] == "self" else "other"
        participants = max(2, min(30, len(session.senders) or 2))
        rows.append(
            {
                "id": f"chat_scene_{index:03d}",
                "kind": "social_scene",
                "day_type": _day_type(session.start_local),
                "relation": session.relation,
                "setting": "online",
                "participants": participants,
                "turns": min(MAX_SCENE_TURNS, session.self_turns),
                "initiator": initiator,
                "interrupt_policy": session.interrupt_policy,
                "topic": _classify_topic(session),
                "outcome": "未知",
                "note": (
                    f"来自导出会话 #{session.file_no:02d}（该会话第 {session.seq_in_file} 段），"
                    + ("关系取自目录标签" if session.relation_labeled else "关系未标注（按朋友）")
                    + ("；打断策略由关键词推定" if session.policy_evidence else "")
                ),
            }
        )
    return rows


def _collect_windows(sessions: list[Session], scrub: Scrubber) -> list[dict[str, Any]]:
    """把会话切成 4–6 条消息的窗口（逐条 scrub，至少一来一回）。"""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for session in sessions:
        if session.message_count < SNIPPET_MIN_MSGS:
            continue
        for start in range(0, session.message_count - SNIPPET_MIN_MSGS + 1, SNIPPET_MAX_MSGS):
            window_roles = session.roles[start : start + SNIPPET_MAX_MSGS]
            window_texts = session.texts[start : start + SNIPPET_MAX_MSGS]
            if len(window_roles) < SNIPPET_MIN_MSGS:
                break
            if len(set(window_roles)) < 2:  # 必须有来有回
                continue
            messages: list[dict[str, str]] = []
            for role, raw in zip(window_roles, window_texts):
                text = scrub(raw)
                if not text:
                    messages = []
                    break
                messages.append({"role": role, "text": text})
            if len(messages) < SNIPPET_MIN_MSGS:
                continue
            key = tuple(message["text"] for message in messages)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"session": session, "messages": messages})
    return candidates


def build_snippets(
    sessions: list[Session],
    scrub: Scrubber,
    limit: int = MAX_SNIPPETS,
    per_relation: int = 25,
) -> list[dict[str, Any]]:
    """会话 -> snippet 记录：连续 4-6 条消息的窗口，scrub 后再落盘。

    选材按关系分层：每种关系先最多取 ``per_relation`` 条，再按会话大小补满，
    免得 200 条全被同学群榨干、伴侣/家人一条都进不来。
    """
    candidates = _collect_windows(sessions, scrub)
    by_relation: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        relation = candidate["session"].relation
        by_relation.setdefault(relation, []).append(candidate)
    picked: list[dict[str, Any]] = []
    picked_ids: set[int] = set()
    for relation in sorted(by_relation):
        items = sorted(by_relation[relation], key=lambda item: -len(item["messages"]))
        for candidate in items[: max(1, per_relation)]:
            picked.append(candidate)
            picked_ids.add(id(candidate))
    if len(picked) < limit:
        rest = [item for item in candidates if id(item) not in picked_ids]
        picked.extend(rest[: max(0, limit - len(picked))])
    picked = picked[:limit]

    rows: list[dict[str, Any]] = []
    for candidate in picked:
        session = candidate["session"]
        rows.append(
            {
                "id": f"chat_snip_{len(rows) + 1:03d}",
                "kind": "snippet",
                "day_type": _day_type(session.start_local),
                "relation": session.relation,
                "context": "线上私聊",
                "interrupt_policy": session.interrupt_policy,
                "messages": candidate["messages"],
                "note": (
                    f"来自导出会话 #{session.file_no:02d}，已做昵称与 PII 掩码；"
                    + ("关系取自目录标签" if session.relation_labeled else "关系未标注（按朋友）")
                    + ("；打断策略由关键词推定" if session.policy_evidence else "")
                ),
            }
        )
    return rows


def run_validate(out_dir: Path = OUT_DIR) -> int:
    """调用项目自带校验器；返回它的退出码（不抛异常）。"""
    command = [sys.executable, str(ROOT / "scripts" / "life_corpus.py"), "validate", str(out_dir), "--strict"]
    print("")
    print("自动校验：" + " ".join(command))
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"校验没跑起来：{exc}")
        return 2
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    print(f"validate --strict 退出码：{result.returncode}")
    return result.returncode


def write_outputs(
    stats: dict[str, Any],
    scenes: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    """落盘三个文件；返回 (stats, scenes, snippets) 路径。"""
    payload = dict(stats)
    payload.pop("_sessions", None)
    payload["derived"] = {
        "scenes": len(scenes),
        "snippets": len(snippets),
        "scene_path": SCENE_PATH.name,
        "snippet_path": SNIPPET_PATH.name,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_jsonl(SCENE_PATH, scenes)
    _write_jsonl(SNIPPET_PATH, snippets)
    return STATS_PATH, SCENE_PATH, SNIPPET_PATH


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    global OUT_DIR, STATS_PATH, SCENE_PATH, SNIPPET_PATH
    parser = argparse.ArgumentParser(
        prog="corpus_chat",
        description="把本机聊天导出（Chat/，gitignore）转成聚合统计与脱敏对话片段，"
        "只写 data/human_life/_derived/",
    )
    parser.add_argument("--write", action="store_true", help="真正落盘，并自动跑 validate --strict")
    parser.add_argument("--chat-dir", default=str(CHAT_DIR), help="导出目录（只读）")
    parser.add_argument("--out", default=str(OUT_DIR), help="输出目录，默认 data/human_life/_derived")
    parser.add_argument("--max-scenes", type=int, default=MAX_SCENES, help=f"场景上限，默认 {MAX_SCENES}")
    parser.add_argument("--max-snippets", type=int, default=MAX_SNIPPETS, help=f"片段上限，默认 {MAX_SNIPPETS}")
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()

    OUT_DIR = Path(args.out)
    STATS_PATH = OUT_DIR / "chat_stats.json"
    SCENE_PATH = OUT_DIR / "chat.social_scene.jsonl"
    SNIPPET_PATH = OUT_DIR / "chat.snippet.jsonl"

    chat_dir = Path(args.chat_dir)
    exports, broken, ignored = load_exports(chat_dir)
    if not exports:
        print(f"没找到任何聊天导出：{chat_dir}（坏文件 {broken} 个）")
        return 2

    stats = analyze(exports)
    sessions: list[Session] = stats["_sessions"]
    print(render_report(stats, broken, ignored))

    if not args.write:
        print("")
        print("dry-run 结束：没有写任何文件。加 --write 才会写 "
              f"{OUT_DIR}（data/ 已 gitignore）。")
        return 0

    scrub = Scrubber(_collect_sensitive_terms(exports))
    scenes = build_scenes(sessions, limit=max(1, args.max_scenes))
    snippets = build_snippets(sessions, scrub, limit=max(1, args.max_snippets))
    stats_path, scene_path, snippet_path = write_outputs(stats, scenes, snippets)

    print("")
    print("已写入：")
    print(f"  {stats_path}（聚合数字，无聊天文本）")
    print(f"  {scene_path}（{len(scenes)} 条 social_scene，不含原文）")
    print(f"  {snippet_path}（{len(snippets)} 条 snippet，已掩码）")
    code = run_validate(OUT_DIR)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
