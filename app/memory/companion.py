"""衡忆 2.0 陪伴层（companion-emotion 分支）接入。

引擎侧（`vendor/symvolve_memory/companion/`）只做三件事：
1. 读这句话的情绪（强度/极性/标签）；
2. 召回旧事后按情绪做一次共振重排；
3. 给出"要不要主动提、提哪条、用陈述还是问句"的决定 + 冷却防复读。

本项目在这层之上只做**宿主该做的事**：
- `hint_for()` 把决定翻译成一句 prompt 提示（不说"记忆"两个字，像人突然想起来）；
- `idle()/ruminate()` 留给主动/独处场景用（只挑料，文字由宿主模型给）；
- 冷却账本按用户分开（`CompanionMemory` 单例池）。

冷却/念头账本会**持久化到 kv**（`companion:ledger`），启动时按快照回放；
重启后她仍然记得"这件事刚提过"。回放只重建账本，不写记忆、不动业务表。
﻿﻿​‌​‌​‌‌‍﻿‍‌​‌​‍‍‌‍﻿​​‍‌‍‍‍​​​‌﻿﻿"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import Any

from app.core import db, memengine
from symvolve_memory.companion import BringUpLedger, CompanionMemory, ThoughtLedger
from symvolve_memory.companion.emotion import detect_emotion
from symvolve_memory.companion.triggers import ASK

_POOL_LOCK = threading.RLock()
_POOL_DATA: dict[str, CompanionMemory] = {}
_LAST: dict[str, dict[str, Any]] = {}
_LEDGER_KEY = "companion:ledger"
_LEDGER_LOCK = threading.RLock()
_BRINGUP: BringUpLedger | None = None
_THOUGHTS: ThoughtLedger | None = None


def _restore_ledgers() -> tuple[BringUpLedger, ThoughtLedger]:
    """从 kv 回放账本；坏数据就当空的，绝不让陪伴层拖垮主流程。"""
    bring = BringUpLedger()
    thoughts = ThoughtLedger()
    try:
        raw = db.kv_get(_LEDGER_KEY, "")
    except Exception:  # noqa: BLE001
        raw = ""
    if not raw:
        return bring, thoughts
    try:
        snap = json.loads(raw)
    except (TypeError, ValueError):
        return bring, thoughts
    b = snap.get("bringup") or {}
    try:
        bring.turn = int(b.get("turn") or 0)
        for e in b.get("entries") or []:
            bring.record(
                user_id=str(e["user_id"]), item_id=int(e["item_id"]),
                at=int(e["at"]), turn=int(e.get("turn") or bring.turn),
            )
    except (KeyError, TypeError, ValueError):
        pass
    th = snap.get("thoughts") or {}
    try:
        if th.get("window_seconds") is not None:
            thoughts.window_seconds = float(th["window_seconds"])
        if th.get("daily_limit") is not None:
            thoughts.daily_limit = int(th["daily_limit"])
        for e in th.get("entries") or []:
            thoughts.record(
                user_id=str(e["user_id"]), signature=str(e["signature"]),
                at=int(e["at"]), item_id=int(e["item_id"]),
                seed_kind=str(e.get("seed_kind") or ""),
            )
    except (KeyError, TypeError, ValueError):
        pass
    return bring, thoughts


def _ledgers() -> tuple[BringUpLedger, ThoughtLedger]:
    global _BRINGUP, _THOUGHTS
    with _LEDGER_LOCK:
        if _BRINGUP is None or _THOUGHTS is None:
            _BRINGUP, _THOUGHTS = _restore_ledgers()
        return _BRINGUP, _THOUGHTS


def save_ledgers() -> int:
    """把冷却/念头账本写回 kv，返回写入字节数；失败返回 0（不抛）。"""
    try:
        bring, thoughts = _ledgers()
        payload = json.dumps(
            {"bringup": bring.snapshot(), "thoughts": thoughts.snapshot()},
            ensure_ascii=False,
        )
        db.kv_set(_LEDGER_KEY, payload)
        return len(payload)
    except Exception:  # noqa: BLE001
        return 0


def ledger_counts() -> dict[str, int]:
    bring, thoughts = _ledgers()
    return {"bringup_entries": len(bring.entries), "thought_writes": len(thoughts.writes)}


def _uid(user_id: int | str) -> str:
    return str(int(user_id or 0))


def _chat(chat_type: str | None) -> str:
    return str(chat_type or "private") or "private"


def _build() -> CompanionMemory:
    """复用引擎单例（Store/Lifecycle/Recall/Renderer）与共享账本，别开第二套。"""
    bring, thoughts = _ledgers()
    return CompanionMemory(
        memengine.store(),
        lifecycle=memengine.lifecycle(),
        recall=memengine.recall(),
        renderer=memengine.renderer(),
        cooldown=bring,
        thoughts=thoughts,
    )


def _pool(uid: str) -> CompanionMemory:
    with _POOL_LOCK:
        if uid not in _POOL_DATA:
            _POOL_DATA[uid] = _build()
        return _POOL_DATA[uid]


def get(user_id: int | str = 0) -> CompanionMemory:
    """按用户取一个陪伴层门面（每个用户独立冷却/念头账本）。"""
    return _pool(_uid(user_id))


def _to_dict(turn) -> dict[str, Any]:
    return {
        "action": str(turn.decision.action),
        "should_initiate": bool(turn.should_initiate),
        "reason": str(turn.decision.reason),
        "title": str(turn.decision.title),
        "item_id": turn.decision.item_id,
        "confidence": round(float(turn.decision.confidence), 3),
        "fragment": str(turn.fragment or ""),
        "tokens": int(turn.tokens or 0),
        "status": str(turn.recall.status),
        "recall_confidence": round(float(turn.recall.confidence), 3),
        "emotion": {
            "intensity": round(float(turn.emotion.intensity), 3),
            "valence": round(float(turn.emotion.valence), 3),
            "labels": list(turn.emotion.labels),
        },
        "boosted": [int(h.item_id) for h in (turn.boosted or ())],
    }


def hear(text: str, user_id: int | str = 0, chat_type: str = "") -> dict[str, Any]:
    """听一句：情绪 → 召回 → 共振 → 决定。返回可 JSON 化的结果。"""
    uid = _uid(user_id)
    turn = _pool(uid).hear(
        user_id=uid, text=str(text or ""), chat_type=_chat(chat_type)
    )
    out = _to_dict(turn)
    _LAST[uid] = out
    save_ledgers()
    return out


def last_turn(user_id: int | str = 0) -> dict[str, Any]:
    return dict(_LAST.get(_uid(user_id), {}))


def _clean_fragment(fragment: str) -> str:
    """把渲染片段洗成一句能塞进 prompt 的话（去掉标题行/项目符号/重复正文）。"""
    body = str(fragment or "").replace("【记忆】", " ").strip()
    lines = [ln.strip(" -·\t") for ln in body.splitlines() if ln.strip(" -·\t")]
    if not lines:
        return ""
    line = lines[-1]
    # 形如「标题：正文」时只留正文；正文跟标题一样就只留一份
    if "：" in line:
        head, _, tail = line.partition("：")
        tail = tail.strip()
        line = tail if tail and tail != head.strip() else head.strip()
    return " ".join(line.split())[:90]


def hint_for(text: str, user_id: int | str = 0, chat_type: str = "") -> str:
    """把陪伴层的决定翻成一句 prompt 提示；没情绪/不该说就返回空串。"""
    if not (text or "").strip():
        return ""
    signal = detect_emotion(str(text))
    if not signal.charged:
        return ""
    turn = hear(text, user_id=user_id, chat_type=chat_type)
    fragment = _clean_fragment(str(turn.get("fragment") or ""))
    if not turn.get("should_initiate") or not fragment:
        return ""
    if turn.get("action") == ASK:
        return (
            f"你忽然想起一件旧事（{fragment}）。可以顺势问一句，像刚想起来，别像在背资料；"
            "不要提「记忆」「记录」这类词。"
        )
    return (
        f"你可以主动提一句旧事（{fragment}），语气自然，说成你刚想起来的样子；"
        "不要提「记忆」「记录」这类词。"
    )


def idle(user_id: int | str = 0, limit: int = 3) -> list[dict[str, Any]]:
    """独处时该想什么（只挑料，不生成文字）。"""
    seeds = _pool(_uid(user_id)).idle(user_id=_uid(user_id), limit=int(limit))
    return [
        {
            "kind": str(s.kind),
            "item_ids": [int(x) for x in s.item_ids],
            "prompt": str(s.prompt),
            "reason": str(s.reason),
        }
        for s in seeds
    ]


def ruminate(user_id: int | str = 0, limit: int = 2,
             generator=None) -> dict[str, Any]:
    """独处那一整轮：挑料 → 去重/限额 → 写回。``generator`` 给了就用它展开文字。"""
    rep = _pool(_uid(user_id)).ruminate(
        user_id=_uid(user_id), limit=int(limit), generator=generator
    )
    save_ledgers()
    return {
        "seeds": len(rep.seeds),
        "written": [int(x) for x in rep.written],
        "count": int(rep.count),
        "reason": str(rep.reason),
        "skipped": [list(x) for x in rep.skipped],
    }


def status(user_id: int | str = 0) -> dict[str, Any]:
    """面板/调试用：策略、冷却、念头额度、最近一轮决定。"""
    uid = _uid(user_id)
    cm = _pool(uid)
    policy = cm.policy
    policy_dict = asdict(policy) if is_dataclass(policy) else dict(vars(policy))
    now = int(time.time())
    try:
        recent_thoughts = int(cm.thoughts.recent_count(user_id=uid, now=now))
    except Exception:  # noqa: BLE001
        recent_thoughts = -1
    return {
        "user_id": int(uid),
        "policy": policy_dict,
        "thoughts_recent_24h": recent_thoughts,
        "ledger": ledger_counts(),
        "last_turn": last_turn(uid),
        "engine_version": _engine_version(),
    }


def _engine_version() -> str:
    try:
        import symvolve_memory

        return str(getattr(symvolve_memory, "__version__", ""))
    except Exception:  # noqa: BLE001
        return ""


def reset(clear_persisted: bool = False) -> None:
    """测试用：清掉进程内池子；``clear_persisted=True`` 连 kv 账本一起清。

    默认**不清 kv**——这样 `reset()` 等价于"重启进程"，冷却仍然记得。
    """
    global _BRINGUP, _THOUGHTS
    with _POOL_LOCK:
        _POOL_DATA.clear()
        _LAST.clear()
    with _LEDGER_LOCK:
        _BRINGUP = None
        _THOUGHTS = None
    if clear_persisted:
        try:
            db.kv_set(_LEDGER_KEY, "")
        except Exception:  # noqa: BLE001
            pass
