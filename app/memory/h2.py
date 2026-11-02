"""衡忆 2.0 · 记忆公共 API（本项目唯一的记忆入口）。

引擎本体是 `vendor/symvolve_memory/`（Symvolve Memory 的只读副本）。
本模块只做三件事：

1. **语义对齐**：把旧 `symemory` 的调用契约（`add / retrieve / pin / stats / effective_heat`）
   映射到引擎的 `Store / MemoryLifecycle / MemoryRecall`，业务代码与面板零改动。
2. **分区合并**：旧系统只有两层（user_id、chat_type 空=不分场合）；引擎强制严格相等过滤。
   这里用「最多 4 次检索 + 去重合并」把旧语义补回来，并且只写一条召回审计。
3. **老数据的活口**：`memories` 视图由 `app/core/db.py` 建，写入永远只走本模块。

铁律：
- 不写第二套存储、不手搓 SQL 改记忆（引擎的 `Store` 是唯一写入口）。
- `mem_event` 原文永不进 prompt；prompt 只吃 `mem_item.text`（视图的 content）。
- 钉住 = 承诺：`pinned` 豁免衰减与遗忘，`heat=1.0`。
﻿​﻿﻿﻿﻿​﻿‌​‍﻿﻿‍​​‍‌​​‍‌​﻿​​‍‌‌‍‍‌﻿﻿"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from app.core import db, memengine  # noqa: F401  (memengine 负责把 vendor 放进 sys.path)

from symvolve_memory import (  # noqa: E402  vendor 路径前置，必须在 memengine 之后导入
    MemoryEvent,
    MemoryItem,
)
from symvolve_memory import decay as _decay  # noqa: E402
from symvolve_memory.models import clamp_heat  # noqa: E402

# ---- 温度参数（引擎口径：0.04/天线性衰减，召回 +0.28） ----
INITIAL_HEAT = 0.90
HOT_THRESHOLD = 0.50        # ≥ 此值：每轮进上下文
COLD_THRESHOLD = 0.12       # ≤ 此值：要靠线索才捞得动（也是冷压缩的准入门槛）
REHEAT = 0.28
DECAY_PER_DAY = 0.97        # 旧口径，只留在文档与兼容常量里

# ---- 线索词 ----
RECALL_WORDS = (
    "记得", "记不记得", "还记得", "想起来", "想起", "上次", "那次", "那天", "那年",
    "以前", "去年", "前年", "小时候", "那时候", "之前", "上次说", "你说过",
    "照片", "照片里", "歌", "音乐", "梦到", "梦见", "味道", "雨", "声音",
)
PIN_WORDS = (
    "记住这个", "记住这件事", "别忘了", "别忘记", "你要记得", "要记住", "记着",
    "永远记得", "千万别忘", "不要忘记", "永久记住",
)
_STOP_BIGRAMS = {
    "什么", "怎么", "这样", "那样", "这个", "那个", "我们", "你们", "他们", "自己",
    "知道", "觉得", "可以", "应该", "现在", "刚才", "时候", "真的", "有点", "一下",
    "是不", "不是", "没有", "一个", "一些", "如果", "因为", "所以", "但是", "然后",
}

#: 不参与冷压缩的种类：雷点、约定是承诺，quote 是语气锚点
PROTECTED_KINDS = ("boundary", "promise", "quote")

_TIME_CUE = re.compile(r"(?:\d{4}\s*年|\d{1,2}\s*月|二十[一二三四五六七八九]|十[一二三四五六七八九]|[一二三四五六七八九]十)\s*(?:年|岁|月)?")


# ---------------------------------------------------------------- 基础换算 --

def _now() -> int:
    return int(time.time())


def _uid(user_id: int | str) -> str:
    """引擎 user_id 是字符串；旧系统 0 = 共用/她的。"""
    return str(int(user_id or 0))


def _int_user(uid: str) -> int:
    try:
        return int(str(uid))
    except (TypeError, ValueError):
        return 0


def _chat(chat_type: str | None) -> str:
    """空串 = 不分场合（引擎 schema 没有 CHECK，可以原样落库）。"""
    return str(chat_type or "")


def _scope(chat_type: str | None) -> str:
    ct = _chat(chat_type)
    return ct or "shared"


def _importance_engine(value: float | int) -> int:
    return max(1, min(100, int(round(float(value or 0.5) * 100))))


def _importance_old(value: int | float) -> float:
    return round(float(value or 0) / 100.0, 4)


def _iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts)).astimezone().isoformat(timespec="seconds")


def _epoch(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp())


def _title(text: str) -> str:
    head = re.split(r"[。！？!?\n]", text.strip(), maxsplit=1)[0]
    return head[:24] or text.strip()[:24]


# ---------------------------------------------------------------- 线索 ----

def bigrams(text: str) -> set[str]:
    s = "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    grams = {s[i: i + 2] for i in range(len(s) - 1)}
    return {g for g in grams if g not in _STOP_BIGRAMS}


def extract_cues(text: str, limit: int = 12) -> list[str]:
    """把一句话拆成"可以挂钩子的碎片"：词、时间、感官。"""
    cues: list[str] = list(bigrams(text))
    for word in RECALL_WORDS:
        if word in text:
            cues.append(word)
    for m in _TIME_CUE.finditer(text):
        cues.append(m.group(0).strip())
    seen: set[str] = set()
    out: list[str] = []
    for c in cues:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:limit]


def _cue_kind(cue: str) -> str:
    if _TIME_CUE.fullmatch(cue or ""):
        return "time"
    if cue in RECALL_WORDS:
        return "sensory"
    if any(ch.isdigit() for ch in cue):
        return "entity"
    return "content"


def wants_pin(text: str) -> bool:
    return any(w in (text or "") for w in PIN_WORDS)


# ---------------------------------------------------------------- 温度 ----

def temperature(heat: float) -> str:
    if heat >= HOT_THRESHOLD:
        return "hot"
    if heat >= COLD_THRESHOLD:
        return "warm"
    return "cold"


def _heat_of(item: MemoryItem, now: int | None = None) -> float:
    if item.pinned:
        return 1.0
    return clamp_heat(_decay.decayed_heat(item, _now() if now is None else int(now)))


def effective_heat(row: Any, now: datetime | None = None) -> float:
    """兼容旧调用：传行（dict/sqlite3.Row）或 MemoryItem 都行。"""
    moment = int((now or datetime.now().astimezone()).timestamp())
    item: MemoryItem | None = None
    if isinstance(row, MemoryItem):
        item = row
    else:
        try:
            mid = int(dict(row).get("id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid:
            item = memengine.store().get_item(mid)
    if item is None:
        try:
            return clamp_heat(float(dict(row).get("heat") or 0.0))
        except (TypeError, ValueError):
            return 0.0
    return _heat_of(item, moment)


# ---------------------------------------------------------------- 行转换 --

def to_old(item: MemoryItem, *, cues: list[str] | None = None,
           now: int | None = None) -> dict[str, Any]:
    """引擎条目 → 旧记忆行（面板/循环认这套字段）。"""
    moment = _now() if now is None else int(now)
    heat = _heat_of(item, moment)
    if cues is None:
        cues = [c.cue for c in memengine.store().list_cues(int(item.id or 0))]
    return {
        "id": int(item.id or 0),
        "kind": item.kind,
        "content": item.text,
        "title": item.title,
        "importance": _importance_old(item.importance),
        "stability": 0.5,
        "source_ids": list(item.source_event_ids),
        "first_seen": _iso(item.created_at),
        "last_seen": _iso(item.last_seen or item.created_at),
        "recall_count": int(item.recall_count),
        "last_recall": _iso(item.last_recall),
        "heat": heat,
        "pinned": bool(item.pinned),
        "cues": list(cues),
        "last_touch": _iso(item.last_recall or item.last_seen or item.created_at),
        "user_id": _int_user(item.user_id),
        "chat_type": item.chat_type,
        "clarity": item.clarity,
        "temperature": temperature(heat),
    }


def _rows(items: Iterable[MemoryItem], now: int | None = None) -> list[dict[str, Any]]:
    items = list(items)
    if not items:
        return []
    store = memengine.store()
    cue_map = store.cues_map([int(i.id or 0) for i in items])
    moment = _now() if now is None else int(now)
    out: list[dict[str, Any]] = []
    for item in items:
        mid = int(item.id or 0)
        cues = [c.cue for c in cue_map.get(mid, [])]
        out.append(to_old(item, cues=cues, now=moment))
    return out


# ---------------------------------------------------------------- 写入 ----

def _find_duplicate(user_id: str, chat_type: str, kind: str, content: str) -> int:
    store = memengine.store()
    for item in store.list_items(user_id=user_id, chat_type=chat_type, kinds=[kind]):
        if item.text == content:
            return int(item.id or 0)
    return 0


def add(
    kind: str,
    content: str,
    importance: float = 0.6,
    stability: float = 0.5,
    pin: bool = False,
    cues: list[str] | None = None,
    source_ids: list[int] | None = None,
    user_id: int = 0,
    chat_type: str = "",
    extra_meta: dict[str, Any] | None = None,
) -> int:
    """写一条记忆。同一 (kind, content, user, chat) 重复写 = 加温，不新增行。"""
    content = (content or "").strip()
    if not content:
        return 0
    store = memengine.store()
    life = memengine.lifecycle()
    uid = _uid(user_id)
    ct = _chat(chat_type)
    sc = _scope(chat_type)
    legacy_ids = [int(s) for s in (source_ids or [])]

    existing = _find_duplicate(uid, ct, kind, content)
    if existing:
        item = store.get_item(existing)
        if item is not None:
            item.importance = max(int(item.importance), _importance_engine(importance))
            item.heat = clamp_heat(max(float(item.heat), INITIAL_HEAT) + 0.1)
            item.pinned = bool(item.pinned) or bool(pin)
            store.update_item(item)
            life.mark_seen(existing)
            if cues:
                store.add_cues(existing, [(c, _cue_kind(c), 1.0) for c in cues if c])
        return existing

    meta: dict[str, Any] = {"kind": kind}
    # `stability` 在 vendor 引擎里没有对应字段（衰减走 clarity + heat），
    # 但收了不丢：写进 meta 留痕，调用方与审计都能看到自己传了什么
    # （docs/23 §3.2 第 9 条：以前是纯死参数）。
    meta["stability"] = float(stability)
    if extra_meta:
        meta.update(dict(extra_meta))
    if legacy_ids:
        # 旧 source_ids 指向 events 表；引擎的 source_event_ids 指向 mem_event，两套 id 不混用
        meta["ai_life_source_ids"] = legacy_ids
    event_id = store.append_event(
        MemoryEvent(
            user_id=uid,
            chat_type=ct or "private",
            scope=sc,
            raw_text=content,
            meta=dict(meta),
        )
    )
    item = MemoryItem(
        user_id=uid,
        chat_type=ct,
        scope=sc,
        kind=kind,
        title=_title(content),
        text=content,
        importance=_importance_engine(importance),
        heat=1.0 if pin else INITIAL_HEAT,
        pinned=bool(pin),
        source_event_ids=[event_id],
        meta=dict(meta),
    )
    mid = store.insert_item(item)
    chosen = cues if cues is not None else extract_cues(content)
    if chosen:
        store.add_cues(mid, [(c, _cue_kind(c), 1.0) for c in chosen if c])
    return mid


def pin(mid: int, pinned: bool = True) -> bool:
    """钉住 = 强制热记忆、永不衰减。这是"承诺"，不是权重。"""
    store = memengine.store()
    item = store.get_item(int(mid))
    if item is None:
        return False
    item.pinned = bool(pinned)
    item.heat = 1.0 if pinned else INITIAL_HEAT
    store.update_item(item)
    return True


def touch(ids: list[int]) -> None:
    """想起一次，就热一次。"""
    if not ids:
        return
    life = memengine.lifecycle()
    for mid in ids:
        try:
            life.mark_recalled(int(mid))
        except Exception:  # noqa: BLE001 - 条目被合并/遗忘了不算错
            continue


def mark_recalled(ids: list[int]) -> None:
    touch(ids)


def find_by_content(content: str, user_id: int = 0, chat_type: str = "") -> int:
    """给 facts.py 之类做去重用：返回已有条目 id，没有就 0。"""
    return _find_duplicate(_uid(user_id), _chat(chat_type), "fact", (content or "").strip())


# ---------------------------------------------------------------- 召回 ----

@dataclass
class Recall:
    hot: list[dict[str, Any]] = field(default_factory=list)
    recalled: list[dict[str, Any]] = field(default_factory=list)
    cues: list[str] = field(default_factory=list)
    status: str = "none"          # none | hit | uncertain | miss
    confidence: float = 0.0
    is_recall_attempt: bool = False
    in_hot: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "in_hot": self.in_hot,
            "confidence": round(self.confidence, 2),
            "cues": self.cues[:6],
            "hot": [m["id"] for m in self.hot],
            "recalled": [
                {"id": m["id"], "content": m["content"], "kind": m["kind"]}
                for m in self.recalled
            ],
        }


def _partitions(user_id: int, chat_type: str | None) -> list[tuple[str, str | None]]:
    """旧语义：这个人 + 共用；这个场合 + 不分场合。最多 4 个分区。

    `chat_type` 为空 = 旧代码的"不筛场合"（面板主对话就是这条），对应引擎的
    `chat_type=None`（不限会话类型），不是"只查空串分区"。

    `user_id == 0` 是旧系统的"单用户面板"：面板不筛人，全库都算她的记忆；
    `user_id < 0` 是调试口专用的同义写法（`/v1/memory/recall` 历来是"全库检索"）。
    真正的隔离只在 QQ 那侧：`user_id > 0` 只看「本人 + 共用」。
    """
    ct: str | None = None if not chat_type else str(chat_type)

    def variants(uid: str) -> list[tuple[str, str | None]]:
        parts = [(uid, ct)]
        if ct:
            # 不分场合写下来的记忆（chat_type="") 在两种场合里都该被想起
            parts.append((uid, ""))
        return parts

    if int(user_id) <= 0:
        parts: list[tuple[str, str | None]] = []
        for uid in all_user_ids():
            parts.extend(variants(uid))
        return parts or [("0", ct)]
    uid = _uid(user_id)
    parts = variants(uid)
    if uid != "0":
        parts.extend(variants("0"))
    return parts


def all_user_ids() -> list[str]:
    """所有出现过的记忆归属（读视图，不碰引擎内部）。"""
    try:
        rows = db.rows("SELECT DISTINCT user_id FROM memories ORDER BY user_id")
    except Exception:  # noqa: BLE001 - 视图还没建好就当成只有共用分区
        return ["0"]
    out: list[str] = []
    for row in rows:
        try:
            out.append(str(int(dict(row).get("user_id") or 0)))
        except (TypeError, ValueError):
            continue
    return out or ["0"]


def _hot_from(partitions: list[tuple[str, str]], hot_limit: int,
              now: int) -> list[dict[str, Any]]:
    store = memengine.store()
    picked: dict[int, MemoryItem] = {}
    for uid, ct in partitions:
        for item in store.list_items(user_id=uid, chat_type=ct):
            mid = int(item.id or 0)
            if mid in picked:
                continue
            if item.kind in ("quote", "thought"):
                # 原话（quote）是语气锚点，thought 是她自己的自言自语：
                # 都不占"记得的"位置，别让内心戏变成"用户说过的话"
                continue
            if item.pinned or _heat_of(item, now) >= HOT_THRESHOLD:
                picked[mid] = item
    items = _rows(picked.values(), now=now)
    items.sort(key=lambda m: (-int(bool(m["pinned"])), -m["heat"], -m["importance"], -m["id"]))
    return items[:hot_limit]


def retrieve(text: str, hot_limit: int = 10, user_id: int = 0,
             chat_type: str = "") -> Recall:
    """一次完整的"想起来"：先看手边（热记忆），再靠线索捞（引擎召回）。"""
    maybe_tick()
    now = _now()
    store = memengine.store()
    recall_engine = memengine.recall()
    parts = _partitions(user_id, chat_type)
    uid = _uid(user_id)
    ct = _chat(chat_type)

    attempt = any(w in (text or "") for w in RECALL_WORDS)
    result = Recall(hot=_hot_from(parts, hot_limit, now),
                    is_recall_attempt=attempt,
                    cues=extract_cues(text or ""))

    best_status = "none"
    best_conf = 0.0
    hits: dict[int, Any] = {}
    cues: list[str] = []
    for part_uid, part_ct in parts:
        res = recall_engine.search(
            user_id=part_uid,
            query=text or "",
            chat_type=part_ct,
            limit=5,      # 多取两条：quote 会在后面被滤掉，不占位置
            log=False,
            bump_heat=True,
        )
        if not cues:
            cues = list(res.cues)
        if res.status == "hit":
            best_status = "hit"
            best_conf = max(best_conf, res.confidence)
        elif res.status == "uncertain" and best_status != "hit":
            best_status = "uncertain"
            best_conf = max(best_conf, res.confidence)
        for hit in res.hits:
            if hit.item.kind in ("quote", "thought"):
                continue
            prev = hits.get(hit.item_id)
            if prev is None or hit.score > prev.score:
                hits[hit.item_id] = hit

    if best_status == "miss" and not attempt:
        best_status = "none"
    if cues:
        result.cues = cues[:12]

    ranked = sorted(hits.values(), key=lambda h: (-h.score, h.item_id))
    hot_ids = {m["id"] for m in result.hot}
    if best_status in ("hit", "uncertain"):
        take = 2 if best_status == "hit" else 1
        # 已经在热层里的不再进「刚想起来的」，否则 prompt 里同一句话出现两遍
        chosen = [h for h in ranked if h.item_id not in hot_ids][:take]
        result.recalled = _rows([h.item for h in chosen], now=now)
        result.in_hot = any(h.item_id in hot_ids for h in ranked)

    result.status = best_status
    result.confidence = round(best_conf, 4) if best_status in ("hit", "uncertain") else 0.0

    # 审计只记一条：4 次分区检索合并后的真实结论
    store.log_recall(
        user_id=uid,
        chat_type=ct or "private",
        query_text=str(text or ""),
        cues=list(result.cues),
        status="miss" if best_status == "none" else best_status,
        confidence=result.confidence,
        item_ids=[m["id"] for m in result.recalled] or [h.item_id for h in ranked[:3]],
        created_at=now,
    )
    return result


def recall_debug(query: str, user_id: int = 0, chat_type: str = "") -> dict[str, Any]:
    """/v1/memory/recall 调试口：给面板看"这句话会想起什么"。"""
    return retrieve(query, user_id=user_id, chat_type=chat_type).as_dict()


# ---------------------------------------------------------------- 查询 ----

def get_memory(mid: int) -> dict[str, Any] | None:
    item = memengine.store().get_item(int(mid))
    if item is None or item.forgotten or item.superseded_by or item.merged_into:
        return None
    return to_old(item)


def search_memories(q: str = "", kind: str | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
    store = memengine.store()
    kinds = [kind] if kind else None
    scan = max(int(limit), 200) if q else int(limit)
    items = store.list_items(kinds=kinds, limit=scan, order_by="importance")
    if q:
        needle = str(q).strip()
        items = [i for i in items if needle in i.text]
    return _rows(items[: int(limit)])


def recent_memories(limit: int = 5) -> list[dict[str, Any]]:
    """原话（quote）只做风格锚点，别挤满"最近记忆"的展示位。"""
    items = memengine.store().list_items(order_by="created_at")
    ordered = [i for i in items if i.kind != "quote"] + [i for i in items if i.kind == "quote"]
    return _rows(ordered[: int(limit)])


def memory_stats() -> dict[str, Any]:
    items = memengine.store().list_items()
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    return {"total": len(items), "by_kind": by_kind}


def stats() -> dict[str, Any]:
    """面板那四个数：热 / 温 / 冷 / 钉住（口径 = 引擎温度分层）。"""
    now = _now()
    items = memengine.store().list_items()
    counts = {"hot": 0, "warm": 0, "cold": 0, "pinned": 0}
    for item in items:
        counts[temperature(_heat_of(item, now))] += 1
        if item.pinned:
            counts["pinned"] += 1
    return {"total": len(items), **counts}


def counts_for_user(user_id: int) -> dict[str, Any]:
    items = memengine.store().list_items(user_id=_uid(user_id))
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    return {"total": len(items), "by_kind": by_kind}


def list_for_user(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """控制台用：某个人的记忆列表（新的在前）。"""
    items = memengine.store().list_items(
        user_id=_uid(user_id), limit=max(1, int(limit)), order_by="created_at"
    )
    return _rows(items)


# ---------------------------------------------------------------- 删除 / 遗忘 --

def delete_memory(mid: int) -> bool:
    """软删除：行还在档案里，视图里不再出现。

    已经遗忘 / 被取代 / 被合并的条目再删一次返回 False（HTTP 层给 404），
    跟旧实现 `DELETE ... rowcount == 0` 的语义一致。
    """
    item = memengine.store().get_item(int(mid))
    if item is None or not item.is_active:
        return False
    life = memengine.lifecycle()
    try:
        life.forget(int(mid), reason="manual")
        return True
    except Exception:  # noqa: BLE001
        return False


def forget_user(user_id: int) -> int:
    """针对性失忆：只清这个人的分区。"""
    life = memengine.lifecycle()
    n = 0
    for item in memengine.store().list_items(user_id=_uid(user_id)):
        try:
            life.forget(int(item.id or 0), reason="user_reset")
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def forget_all() -> int:
    life = memengine.lifecycle()
    n = 0
    for item in memengine.store().list_items():
        try:
            life.forget(int(item.id or 0), reason="reset")
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


# ---------------------------------------------------------------- 维护 ----

_TICK_KEY = "memory_engine_last_tick"
_TICK_INTERVAL_S = 3600


def maybe_tick(force: bool = False) -> dict[str, Any] | None:
    """稳定衰减 + 遗忘，最多每小时跑一次（惰性推进，不拖慢聊天）。"""
    now = _now()
    last = db.kv_get(_TICK_KEY, "")
    if not force and last:
        try:
            if now - int(last) < _TICK_INTERVAL_S:
                return None
        except ValueError:
            pass
    try:
        report = memengine.lifecycle().tick(merge=False)
    except Exception:  # noqa: BLE001 - 维护失败不影响当轮对话
        return None
    db.kv_set(_TICK_KEY, str(now))
    return report


def engine_stats() -> dict[str, Any]:
    return memengine.stats()


def export_archive(path) -> str:
    """整份记忆档案导出（引擎原生格式，可被 standalone 项目直接 import）。"""
    out = memengine.store().export_file(path)
    return str(out)


def import_archive(path, mode: str = "merge") -> dict[str, Any]:
    return memengine.store().import_file(path, mode=mode)


def health() -> dict[str, Any]:
    """自检：库能不能开、schema 版本、指针是否一致。"""
    st = memengine.store()
    s = st.stats()
    return {
        "ok": int(s.get("schema_version") or 0) >= 2,
        "db": str(memengine.engine_path()),
        "schema_version": s.get("schema_version"),
        "items": s.get("items"),
        "items_active": s.get("items_active"),
        "events": s.get("events"),
        "cues": s.get("cues"),
        "recall_logs": s.get("recall_logs"),
    }


__all__ = [
    "INITIAL_HEAT", "HOT_THRESHOLD", "COLD_THRESHOLD", "REHEAT", "DECAY_PER_DAY",
    "PROTECTED_KINDS", "RECALL_WORDS", "PIN_WORDS", "Recall",
    "add", "pin", "touch", "mark_recalled", "retrieve", "recall_debug", "stats",
    "effective_heat", "temperature", "wants_pin", "extract_cues", "bigrams",
    "get_memory", "search_memories", "recent_memories", "memory_stats",
    "counts_for_user", "delete_memory", "forget_user", "forget_all",
    "find_by_content", "maybe_tick", "engine_stats", "export_archive",
    "import_archive", "health", "to_old",
]
