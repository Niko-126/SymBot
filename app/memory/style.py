"""语料风格取样层：把 `data/human_life/_derived` 的语料压成"给 prompt 用的一点点东西"。

**为什么要有这一层**：语料库里有 partner（100 片段，相爱:吵架 = 85:15）、stranger（60 片段，
陌生人/服务）、csds（60 条客服）。直接把它们的原文成段塞进 prompt 有两个问题：
1) 又长又贵，几段就把上下文吃满；
2) 私聊原文本身就是用户的隐私，越少进 prompt 越好。

所以这里只吐三样：
- `partner_examples(limit=2)`：最多两条**极短**示范（每条 1–2 句、单句 ≤40 字），
  并且按 85% 相爱 / 15% 拌嘴 的权重抽——比例是策略，不是巧合，防止越聊越苦；
- `stranger_rules()`：一段固定中文规则（怎么跟炸毛的陌生人、客服打交道）；
- `service_note()`：一句"客服腔降权"提醒。

其余API：`load_dir()` 只读加载 + 60 秒缓存，`snapshot()` 给测试/面板看数量。

设计约束：
- 纯标准库 + `app.core.paths`，不联网、不写文件、不打印聊天原文；
- 任何异常都不外抛：目录没了 / 行坏了 / 字段缺了，一律降级成空列表与 `ok=False`，让人物照常说话；
- 抽样用传入的 `rng`，方便测试复现。

用法：
    from app.memory import style
    style.partner_examples(2)      # ['她：「…」；他：「…」', ...]
    style.stranger_rules()         # 固定规则文本
    style.snapshot()               # {'ok': True, 'dir': ..., 'counts': {...}, ...}
﻿​​‍‍‍‍​‍﻿‌‍‍﻿﻿‍‍‌‌​﻿​​‌‍‌​​​‍‌​‍﻿"""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from app.core.paths import DATA_ROOT

#: 默认语料目录；环境变量优先，方便测试/面板指到别处
DEFAULT_DIR: Path = DATA_ROOT / "data" / "human_life" / "_derived"
DIR_ENV: str = "AI_LIFE_STYLE_DIR"

#: 四个要读的文件（key -> 文件名）
FILES: dict[str, str] = {
    "partner_snippet": "partner.snippet.jsonl",
    "partner_scene": "partner.social_scene.jsonl",
    "stranger_snippet": "stranger.snippet.jsonl",
    "csds_snippet": "csds.snippet.jsonl",
}

CACHE_TTL_SECONDS: float = 60.0

#: 相爱 / 拌嘴的抽样权重（与 partner_mix.json 的 policy 一致）
LOVE_WEIGHT: float = 0.85
FIGHT_WEIGHT: float = 1.0 - LOVE_WEIGHT   # 别写死 0.15：改 LOVE 时这里自动跟着变

#: note 里的来源标记：相爱语料 = love，冲突样本 = fight
LOVE_MARK: str = "相爱语料"
FIGHT_MARK: str = "冲突样本"

MAX_LINE_CHARS: int = 40       # 单句上限（prompt 里只给味道，不给段落）
MAX_EXAMPLE_CHARS: int = 80    # 一条样本总长上限（她+他两侧都满 40 时兜底）
MAX_STRANGER_RULES: int = 400  # 规则文本字数上限

#: 显式性内容词：命中就整条样本丢弃（语料管线也有一条同名单，这里是第二道闸）。
#: 注意别用裸子串（"胸/下面/插/射"会误杀"胸口疼"），用短语；谐音词也收进来。
EXPLICIT_WORDS: tuple[str, ...] = (
    "做爱", "性欲", "裸", "胸部", "床事", "乳房", "内裤", "亲热", "开房",
    "上床", "湿了", "硬了", "叫床", "大又粗", "榨干", "嘴巴酸",
    "好涩", "爆炒", "香草泥",
)

_cache: tuple[str, float, dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def _resolve_dir() -> Path:
    """语料目录：环境变量覆盖 > 默认目录。"""
    override = os.environ.get(DIR_ENV)
    if override:
        return Path(override)
    return DEFAULT_DIR


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """读一个 JSONL：坏行跳过并计数。返回 (记录列表, 跳过行数)。"""
    if not path.exists():
        return [], 0
    records: list[dict[str, Any]] = []
    skipped = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(payload, dict):
            skipped += 1
            continue
        records.append(payload)
    return records, skipped


def load_dir(*, use_cache: bool = True) -> dict[str, Any]:
    """只读加载语料目录（缓存 60 秒）。

    返回 ``{"ok", "dir", "records", "counts", "skipped", "partner_love", "partner_fight"}``：
    - ``ok``：目录存在且至少读到一个文件；
    - ``records``：按 :data:`FILES` 的 key 分组的记录列表；
    - ``counts``：同名键的条数；
    - ``skipped``：坏行总数。
    目录不存在时不会抛异常，返回全空结构。
    """
    global _cache
    directory = _resolve_dir()
    try:
        key = str(directory.resolve())
    except OSError:
        key = str(directory)
    now = time.monotonic()
    if use_cache and _cache is not None:
        cached_key, cached_at, cached_value = _cache
        if cached_key == key and (now - cached_at) <= CACHE_TTL_SECONDS:
            return cached_value

    empty: dict[str, Any] = {
        "ok": False,
        "dir": key,
        "records": {name: [] for name in FILES},
        "counts": {name: 0 for name in FILES},
        "skipped": 0,
        "partner_love": 0,
        "partner_fight": 0,
    }
    try:
        if not directory.is_dir():
            _cache = (key, now, empty)
            return empty
    except OSError:
        return empty

    records: dict[str, list[dict[str, Any]]] = {}
    skipped_total = 0
    for name, filename in FILES.items():
        try:
            items, skipped = _read_jsonl(directory / filename)
        except Exception:  # noqa: BLE001 - 单个文件出问题不影响其它文件
            items, skipped = [], 0
        records[name] = items
        skipped_total += skipped

    partner_records = records.get("partner_snippet", [])
    love = [item for item in partner_records if LOVE_MARK in str(item.get("note") or "")]
    fight = [item for item in partner_records if FIGHT_MARK in str(item.get("note") or "")]
    payload: dict[str, Any] = {
        "ok": any(records[name] for name in FILES),
        "dir": key,
        "records": records,
        "counts": {name: len(records[name]) for name in FILES},
        "skipped": skipped_total,
        "partner_love": len(love),
        "partner_fight": len(fight),
    }
    _cache = (key, now, payload)
    return payload


def clear_cache() -> None:
    """清掉缓存（测试/换目录时用）。"""
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# 风格样本
# ---------------------------------------------------------------------------


def _text_of(record: dict[str, Any], role: str) -> str:
    """从一条 snippet 里取某角色第一句（截断到 MAX_LINE_CHARS）。"""
    messages = record.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role")) != role:
            continue
        text = " ".join(str(message.get("text") or "").split())
        # 女主场景语料是分词切出来的，汉字之间会夹空格；prompt 里要连起来才像人话
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        if text:
            return text[:MAX_LINE_CHARS]
    return ""


def _format_line(record: dict[str, Any], *, fight: bool) -> str:
    """压成一行：她：「…」；他：「…」；拌嘴样本只留她这一句并标注。"""
    self_text = _text_of(record, "self")
    other_text = _text_of(record, "other")
    if fight:
        line = f"她：「{self_text or '…'}」（偶尔闹别扭）"
    else:
        parts: list[str] = []
        if self_text:
            parts.append(f"她：「{self_text}」")
        if other_text:
            parts.append(f"他：「{other_text}」")
        line = "；".join(parts)
    # 两侧都满 40 时可能到 89 字；总长再兜一道，并把切开的引号补闭合
    if len(line) > MAX_EXAMPLE_CHARS:
        head = line[: MAX_EXAMPLE_CHARS - 2]
        if head.count("「") > head.count("」"):
            head += "」"
        line = head + "…"
    return line


def _has_explicit(record: dict[str, Any]) -> bool:
    """样本里有没有显式性内容：有就整条不进 prompt（第二道闸）。"""
    messages = record.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text") or "")
        if any(word in text for word in EXPLICIT_WORDS):
            return True
    return False


def _partner_pools() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    loaded = load_dir()
    records = loaded.get("records", {}).get("partner_snippet", [])
    love = [
        item for item in records
        if LOVE_MARK in str(item.get("note") or "") and not _has_explicit(item)
    ]
    fight = [
        item for item in records
        if FIGHT_MARK in str(item.get("note") or "") and not _has_explicit(item)
    ]
    return love, fight


def partner_examples(limit: int = 2, rng: random.Random | None = None) -> list[str]:
    """抽 1–2 条极短的伴侣风格示范（85% 相爱 / 15% 拌嘴）。

    - 相亲相爱样本里各取一句「她」「他」，单句 ≤40 字，**绝不整段**；
    - 拌嘴样本只出她的一句，并标「偶尔闹别扭」，让人物知道这是少数情况；
    - 语料目录没了/为空时返回 ``[]``，不抛异常。
    """
    try:
        count = int(limit)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return []
    love, fight = _partner_pools()
    if not love and not fight:
        return []

    picker = rng if rng is not None else random.Random()
    lines: list[str] = []
    for _ in range(min(count, 3)):   # prompt 里最多 3 条，再多就变味了
        wants_fight = bool(fight) and picker.random() < FIGHT_WEIGHT
        if wants_fight and fight:
            line = _format_line(picker.choice(fight), fight=True)
        elif love:
            line = _format_line(picker.choice(love), fight=False)
        else:
            line = _format_line(picker.choice(fight), fight=True)
        if line and line not in lines:   # 小语料库时别把同一条复读两遍
            lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# 固定规则
# ---------------------------------------------------------------------------


def stranger_rules() -> str:
    """跟陌生人/服务人员打交道的行为规则（固定文本，≤400 字）。

    来自用户的真实相处经验：先降火、别硬刚、问具体事、不交底、话短。
    """
    rules = (
        "遇到炸毛的陌生人（司机、骑手、店员、客服）：先降火，共情或卖惨都行，别硬刚、别讲大道理，"
        "也别争对错；顺手问一个他乐意聊的具体问题（车、价、路），话题一转移气就消一半。"
        "跟平台客服申诉：对方也是打工人，骂他只会让你继续转圈；把诉求说清楚、留好证据，"
        "不纠缠重复拉扯。陌生人问住址、单位、真名这类个人信息：一句都不交底，含糊带过就行。"
        "话短一点，别过度热情，别替自己承诺具体时间（说「马上到」不如说「在路上了」）。"
    )
    return rules[:MAX_STRANGER_RULES]


def service_note() -> str:
    """一句人话提点：别把每个回复都写成客服腔（只给行为，不暴露语料内部口径）。"""
    return (
        "别把日常回复写成客服腔：少说「稍后回复您」「请您耐心等待」「建议您耐心等待」这类话，"
        "能直接给结论、能直接说事就别绕流程；别句句加敬语，也别像在处理工单。"
    )


# ---------------------------------------------------------------------------
# 面板/测试
# ---------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """给测试和面板用的数量快照。

    ``partner_love/fight`` 是**原始**条数（按 note 标记），``*_usable`` 是过完显式内容闸后
    真正能进 prompt 的条数——面板别把"能读"当成"能用"。
    """
    loaded = load_dir()
    love, fight = _partner_pools()
    return {
        "ok": bool(loaded.get("ok")),
        "dir": str(loaded.get("dir") or ""),
        "counts": dict(loaded.get("counts") or {}),
        "partner_love": int(loaded.get("partner_love") or 0),
        "partner_fight": int(loaded.get("partner_fight") or 0),
        "partner_love_usable": len(love),
        "partner_fight_usable": len(fight),
        "skipped": int(loaded.get("skipped") or 0),
        "love_weight": LOVE_WEIGHT,
        "fight_weight": FIGHT_WEIGHT,
    }
