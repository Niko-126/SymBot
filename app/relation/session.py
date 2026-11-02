"""会话生命周期：命名、厌恶、终止、重启。

这里有三件不可逆的事：
1. 她被用户起名之后，名字锁定。
2. 厌恶到 100，她会主动结束这段关系。
3. 关系结束后，关于这个用户的记忆会被清除，换一个人重新开始。

所以所有破坏性动作都先归档。误触发可以复原，被忘记的找不回来。
﻿‍﻿‍‌‌﻿﻿‍‍‍﻿﻿​​​﻿‌﻿﻿﻿​﻿‍​‍‌​​﻿‍‌﻿﻿"""

from __future__ import annotations

import json
import pathlib
import re
import random
from datetime import datetime
from typing import Any

from app.core import db

ARCHIVE_DIR = pathlib.Path("data/archives")

# 新身份的候选名：始终是「小X」
_NAME_POOL = ["小满", "小悠", "小禾", "小岚", "小舟", "小穗", "小湘", "小渝", "小黎", "小棠", "小栩", "小栀"]

# 命名流程的四个状态
NAME_STEPS = {"idle": 0, "asked": 1, "pending": 2, "locked": 3}

_AFFIRM = (
    "确认", "确定", "嗯", "对", "是", "好", "可以", "行", "就这个", "就它", "成",
    "没错", "是的", "准", "没问题", "就这样", "ok", "OK", "Okay", "可以啊", "好啊",
)
_AFFIRM_EXTRA = ("就这么定", "听你的", "都行", "随你")
_DECLINE = ("随便", "你定", "不知道", "算了", "不要", "换一个", "改", "不用", "不", "别")

_COMMON = {
    "你好", "在吗", "在不在", "hi", "hello", "干嘛", "什么", "怎么", "为什么",
    "好的", "谢谢", "再见", "晚安", "早安", "嗯嗯", "哈哈", "是", "不",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def current_seq() -> int:
    """当前会话序号（`session.seq`）。恋人绑定/分手必须按它记账，别写死 1。

    `terminate()` 会把 seq +1；history 全部按 seq 分档，写死 1 会让换人之后
    的恋爱线记到上一段会话上，新会话看到的是"上一任的恋人位"。
    """
    row = db.one("SELECT seq FROM session WHERE id = 1")
    try:
        return int(row["seq"]) if row else 1
    except (KeyError, TypeError, ValueError):
        return 1


def ensure(default_name: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM session WHERE id = 1")
    if row is None:
        db.execute(
            "INSERT INTO session (id, seq, her_name, name_locked, status, started_at)"
            " VALUES (1, 1, ?, 0, 'active', ?)",
            (default_name, _now()),
        )
        row = db.one("SELECT * FROM session WHERE id = 1")
    return dict(row)


def get(default_name: str) -> dict[str, Any]:
    return ensure(default_name)


def her_name(default_name: str) -> str:
    st = get(default_name)
    return str(st.get("her_name") or default_name)


def set_pending(name: str) -> None:
    db.execute("UPDATE session SET name_pending = ? WHERE id = 1", (name,))


def confirm_name() -> str:
    st = get("小涵")
    name = str(st.get("name_pending") or st.get("her_name") or "小涵")
    db.execute(
        "UPDATE session SET her_name = ?, name_locked = 1, name_pending = NULL WHERE id = 1",
        (name,),
    )
    return name


_NAME_PATTERNS = [
    re.compile(r"叫你(?:做)?\s*([\u4e00-\u9fffA-Za-z]{1,6})"),
    re.compile(r"你就叫\s*([\u4e00-\u9fffA-Za-z]{1,6})"),
    re.compile(r"名字(?:就)?(?:叫|是)\s*([\u4e00-\u9fffA-Za-z]{1,6})"),
    re.compile(r"起(?:个|一个)?名[字]?[，,\s]*(?:就)?叫\s*([\u4e00-\u9fffA-Za-z]{1,6})"),
    re.compile(r"^(?:就叫|叫)\s*([\u4e00-\u9fffA-Za-z]{1,6})$"),
    # 真机实测：「就叫小满。」带句号就认不出来（锚定 $ 太严）
    re.compile(r"^(?:就叫|叫)\s*([\u4e00-\u9fffA-Za-z]{1,6})[\s。.！!？?，,…]*$"),
]
_NAME_TAIL = ("好了", "吧", "呗", "啊", "呀", "吗", "的", "就行", "就好", "算了", "可以", "嗯")

#: 一眼不像名字的候选（实测被"这一长串数字"骗过一次）：含这些词就丢
_NAME_BAD_WORDS = (
    "这", "那", "什么", "名字", "数字", "以后", "一个", "这句", "那串", "这串",
    "刚才", "一直", "这么", "那么",
)


def extract_name(text: str) -> str:
    """从一句自然的话里挑出名字。认不出就返回空串。"""
    t = text.strip()
    for pat in _NAME_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        cand = m.group(1)
        for tail in _NAME_TAIL:
            if cand.endswith(tail) and len(cand) > len(tail):
                cand = cand[: -len(tail)]
        cand = cand.strip("「」\"'“” ")
        if _plausible_name(cand) and cand not in _COMMON and not is_answer_word(cand):
            return cand
    if looks_like_name(t) and not is_answer_word(t):
        bare = t.strip("「」\"'“” ")
        if _plausible_name(bare):
            return bare
    return ""


def _plausible_name(cand: str) -> bool:
    """名字的基本面：2–6 字、无标点、不含"这/那/什么/数字"这类词。"""
    if not (2 <= len(cand) <= 6):
        return False
    if any(ch in cand for ch in "？?。，,！!…、\n\t "):
        return False
    return not any(w in cand for w in _NAME_BAD_WORDS)


def is_answer_word(text: str) -> bool:
    """确认/拒绝/寒暄这类词，永远不该被当成名字。"""
    t = text.strip()
    if is_affirm(t) or is_decline(t):
        return True
    if t.lower() in ("yes", "no", "ok", "okay"):
        return True
    return False


_BAD_NAME_CHARS = "在吗什么怎么干嘛你我他她它的了是有没不呢吧啊呀嘛哦嗯"


def looks_like_name(text: str) -> bool:
    """裸名字的判定要严：2-4 字，不含功能词，不含标点。

    "你在干嘛"这种句子不能被当成名字——那是上一版的坑。
    """
    t = text.strip().strip("「」\"'“” ")
    if not (2 <= len(t) <= 4):
        return False
    if any(ch in t for ch in "？?。，,！!…"):
        return False
    if any(ch in t for ch in _BAD_NAME_CHARS):
        return False
    if t.lower() in _COMMON or t in _COMMON:
        return False
    return True


#: 只有"要给她改名"才算；"你叫什么名字"是在问她的名字，不是命名意图（实测踩过）
_NAMING_INTENT = (
    "起个名", "起个名字", "起名", "取个名", "改个名", "改名",
    "以后就叫你", "以后叫你", "给你起", "帮你起",
)


def wants_naming(text: str) -> bool:
    """只有用户主动提出"给她"起名，才进入命名流程。

    "以后就叫我阿辰"是他在给自己起名——不能当成给她改名（实测踩过）。
    """
    t = text.strip()
    if "叫我" in t and "叫你" not in t:
        return False
    if any(k in t for k in _NAMING_INTENT):
        return True
    return False


def is_affirm(text: str) -> bool:
    t = text.strip().lower()
    if any(w.lower() in t for w in _AFFIRM + _AFFIRM_EXTRA):
        return True
    return False


def is_decline(text: str) -> bool:
    return any(w in text for w in _DECLINE)


# ---------------------------------------------------------------- 厌恶

def get_aversion() -> float:
    r = db.one("SELECT aversion FROM relation WHERE id = 1")
    return float(r["aversion"]) if r else 0.0


def set_aversion(value: float) -> float:
    v = max(0.0, min(100.0, value))
    db.execute("UPDATE relation SET aversion = ? WHERE id = 1", (v,))
    return v


def adjust_aversion(delta: float) -> float:
    return set_aversion(get_aversion() + delta)


def aversion_phase(value: float) -> str:
    if value >= 100:
        return "ended"
    if value >= 85:
        return "cold"
    if value >= 60:
        return "irritated"
    if value >= 30:
        return "uneasy"
    return "calm"


# ---------------------------------------------------------------- 归档

def archive(reason: str = "aversion_limit") -> pathlib.Path:
    """把这段关系的全部痕迹写成一份档案，给人看，也留着可恢复。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    st = get("小涵")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    seq = int(st.get("seq") or 1)
    path = ARCHIVE_DIR / f"session-{seq:03d}-{stamp}.json"

    payload = {
        "archived_at": _now(),
        "reason": reason,
        "session": st,
        "relation": dict(db.one("SELECT * FROM relation WHERE id = 1") or {}),
        "traits": [dict(r) for r in db.rows("SELECT * FROM traits")],
        "events": [dict(r) for r in db.rows("SELECT * FROM events ORDER BY id")],
        "memories": [dict(r) for r in db.rows("SELECT * FROM memories ORDER BY id")],
        "relation_log": [dict(r) for r in db.rows("SELECT * FROM relation_log ORDER BY id")],
        "traits_log": [dict(r) for r in db.rows("SELECT * FROM traits_log ORDER BY id")],
        # 这封信是写给谁的：恋人位决定用哪一份关系流水（多用户时不串）
        "lover": [dict(r) for r in db.rows("SELECT * FROM lover ORDER BY id")],
    }
    try:
        from app.persona import transfer

        payload["persona_snapshot"] = transfer.snapshot_for_archive()
    except Exception:  # noqa: BLE001
        pass
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _new_name(exclude: str) -> str:
    pool = [n for n in _NAME_POOL if n != exclude]
    return random.choice(pool or _NAME_POOL)


def terminate(persona, reason: str = "aversion_limit") -> dict[str, Any]:
    """结束这段关系：归档 → 清除当前记忆 → 换一个人。"""
    old_name = her_name(getattr(persona, "name", "小涵"))
    path = archive(reason)
    old_seq = current_seq()

    with db.cursor() as cur:
        cur.execute("DELETE FROM events")
        cur.execute("DELETE FROM relation_log")
        cur.execute("DELETE FROM traits_log")
        cur.execute("UPDATE relation SET affinity = 0, trust = 0, attraction = 0, aversion = 0 WHERE id = 1")
        cur.execute("UPDATE session SET status = 'ended', ended_at = ?, end_reason = ? WHERE id = 1", (_now(), reason))
        # 上一段会话的恋人位要跟着结束：不然 lover 表会留下永远是 active 的孤儿行，
        # 而 love.py 用 current_seq() 查，换人后旧行会被当成"现任"。
        cur.execute(
            "UPDATE lover SET status = 'ended', ended_at = ?, end_reason = ?"
            " WHERE session_seq = ? AND status = 'active'",
            (_now(), f"session_terminated:{reason}", int(old_seq)),
        )
    # 记忆本体在引擎里：软删除（档案留档，视图清空），不写 memories 视图
    from app.memory import h2

    h2.forget_all()

    new_name = _new_name(old_name)
    st = get("小涵")
    db.execute(
        "UPDATE session SET seq = ?, her_name = ?, name_locked = 1, name_pending = NULL,"
        " status = 'active', started_at = ?, ended_at = NULL, end_reason = NULL WHERE id = 1",
        (int(st.get("seq") or 1) + 1, new_name, _now()),
    )
    return {"archive": str(path), "old_name": old_name, "new_name": new_name}


def finale(persona, old_name: str) -> list[str]:
    """分手时说的话。短、冷、不留口子。"""
    end = (persona.raw.get("end") or {})
    lines = [str(x) for x in (end.get("finale") or []) if str(x).strip()]
    if not lines:
        lines = ["就这样吧。", "别再找我了。", "我当你没来过。"]
    return lines


def session_payload(persona) -> dict[str, Any]:
    st = get(getattr(persona, "name", "小涵"))
    return {
        "seq": int(st.get("seq") or 1),
        "her_name": her_name(persona.name),
        "name_locked": bool(st.get("name_locked")),
        "name_pending": st.get("name_pending"),
        "status": st.get("status") or "active",
        "started_at": st.get("started_at"),
        "aversion": round(get_aversion(), 1),
        "phase": aversion_phase(get_aversion()),
    }
