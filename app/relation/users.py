"""QQ 群多用户：群成员、每人独立关系、唯一恋人绑定。

设计见 docs/20。三条硬规则：

1. **好感 100 只是资格**，不自动绑定；绑定要一次表白 + 她接受。
2. **唯一性**：同一 session 只有一个 `status='active'` 的恋人。
3. **分手释放 + 冷却**：分手方在 `cooldown_days` 天内不能再绑定；
   对外说人话（「我最近不想谈这个」），不出现「冷却/CD/状态机」。

这里只做数据与规则，不碰 prompt/LLM；数值入口仍是 engine.apply_appraisal（后续加 user_id）。
﻿‍‌‍‍﻿‌‌﻿‌‍​‌‍‌‌‌‍​﻿‍‌‌﻿‍﻿‌‌‍‍‌‌‍﻿"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from app.core import config as config_mod
from app.core import db

STAGE_AT = (("observer", 0.0), ("swimmer", 20.0), ("echo", 40.0), ("samefreq", 60.0), ("lover", 80.0), ("bond", 95.0))
# 恋爱绑定门槛的**默认值**：与阶段表里「恋人」那一档对齐（80）。
# 运行时请用 threshold()——它会先读 config.relationship.lover.threshold，
# 否则 config.yaml 里改了阈值等于白改（docs/23 §3.2 第 6 条）。
THRESHOLD = 80.0


def threshold() -> float:
    """恋人门槛：配置优先，读不到（配置未加载/字段缺失）回落模块常量 80。"""
    try:
        return float(config_mod.get().relationship.lover.threshold)
    except Exception:  # noqa: BLE001
        return THRESHOLD


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def stage_for(affinity: float) -> str:
    stage = "observer"
    for key, at in STAGE_AT:
        if affinity >= at:
            stage = key
    return stage


# ---------------------------------------------------------------- 用户


def ensure_user(openid: str, nickname: str = "") -> dict[str, Any]:
    """按 QQ openid 建/取用户，顺手更新昵称与最后活跃时间。"""
    openid = str(openid or "").strip()
    if not openid:
        raise ValueError("openid 不能为空")
    ts = now_iso()
    row = db.one("SELECT * FROM qq_user WHERE openid = ?", (openid,))
    if row is None:
        db.execute(
            "INSERT INTO qq_user (openid, nickname, first_seen, last_seen) VALUES (?, ?, ?, ?)",
            (openid, nickname or "", ts, ts),
        )
        row = db.one("SELECT * FROM qq_user WHERE openid = ?", (openid,))
    else:
        db.execute(
            "UPDATE qq_user SET nickname = ?, last_seen = ? WHERE id = ?",
            (nickname or row["nickname"] or "", ts, int(row["id"])),
        )
        row = db.one("SELECT * FROM qq_user WHERE id = ?", (int(row["id"]),))
    return dict(row)


def get_user(user_id: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM qq_user WHERE id = ?", (int(user_id),))
    return dict(row) if row else None


def get_user_by_openid(openid: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM qq_user WHERE openid = ?", (str(openid),))
    return dict(row) if row else None


# ---------------------------------------------------------------- 每人关系


def relation_of(user_id: int) -> dict[str, Any]:
    """取某人的关系；没有就建一条全 0 的。"""
    row = db.one("SELECT * FROM user_relation WHERE user_id = ?", (int(user_id),))
    if row is None:
        ts = now_iso()
        db.execute(
            "INSERT INTO user_relation (user_id, affinity, trust, attraction, aversion, stage,"
            " hit_100_at, updated_at) VALUES (?, 0, 0, 0, 0, 'observer', NULL, ?)",
            (int(user_id), ts),
        )
        row = db.one("SELECT * FROM user_relation WHERE user_id = ?", (int(user_id),))
    return dict(row)


def update_relation(
    user_id: int,
    *,
    affinity: float | None = None,
    trust: float | None = None,
    attraction: float | None = None,
    aversion: float | None = None,
) -> dict[str, Any]:
    """写关系数值（0-100 夹紧）。第一次到 100 记下 hit_100_at，保留"谁最早到"。"""
    rel = relation_of(user_id)
    ts = now_iso()

    def clamp(v: float, old: float) -> float:
        return max(0.0, min(100.0, float(v) if v is not None else float(old)))

    new_aff = clamp(affinity, rel["affinity"])
    new_trust = clamp(trust, rel["trust"])
    new_attr = clamp(attraction, rel["attraction"])
    new_av = clamp(aversion, rel["aversion"])
    hit_100 = rel["hit_100_at"]
    if new_aff >= threshold() and not hit_100:
        hit_100 = ts
    db.execute(
        "UPDATE user_relation SET affinity = ?, trust = ?, attraction = ?, aversion = ?,"
        " stage = ?, hit_100_at = ?, updated_at = ? WHERE user_id = ?",
        (new_aff, new_trust, new_attr, new_av, stage_for(new_aff), hit_100, ts, int(user_id)),
    )
    return relation_of(user_id)


# ---------------------------------------------------------------- 恋人


def current_lover(session_seq: int = 1) -> dict[str, Any] | None:
    row = db.one(
        "SELECT * FROM lover WHERE session_seq = ? AND status = 'active'"
        " ORDER BY id DESC LIMIT 1",
        (int(session_seq),),
    )
    return dict(row) if row else None


def lover_of(user_id: int, session_seq: int = 1) -> dict[str, Any] | None:
    row = db.one(
        "SELECT * FROM lover WHERE user_id = ? AND session_seq = ? AND status = 'active'"
        " ORDER BY id DESC LIMIT 1",
        (int(user_id), int(session_seq)),
    )
    return dict(row) if row else None


def bind_lover(session_seq: int, user_id: int) -> dict[str, Any]:
    """绑定唯一恋人。已有 active 恋人、或自己已是恋人时抛 ValueError。"""
    if current_lover(session_seq) is not None:
        raise ValueError("已经有恋人了")
    rel = relation_of(user_id)
    if float(rel["affinity"]) < threshold():
        raise ValueError("好感还没到")
    if rel["hit_100_at"] is None:
        update_relation(user_id, affinity=float(rel["affinity"]))
    ts = now_iso()
    db.execute(
        "INSERT INTO lover (session_seq, user_id, status, bound_at) VALUES (?, ?, 'active', ?)",
        (int(session_seq), int(user_id), ts),
    )
    return current_lover(session_seq) or {}


def breakup(session_seq: int, reason: str = "", cooldown_days: int = 7) -> dict[str, Any] | None:
    """结束当前恋人。分手方进入冷却（默认 7 天）；恋人位释放给其他人。"""
    lover = current_lover(session_seq)
    if lover is None:
        return None
    ts = now_iso()
    until = (datetime.now().astimezone() + timedelta(days=max(0, int(cooldown_days)))).isoformat(
        timespec="seconds"
    )
    db.execute(
        "UPDATE lover SET status = 'ended', ended_at = ?, end_reason = ?, cooldown_until = ?"
        " WHERE id = ?",
        (ts, reason or "", until, int(lover["id"])),
    )
    return dict(db.one("SELECT * FROM lover WHERE id = ?", (int(lover["id"]),)) or {})


def cooldown_state(user_id: int, session_seq: int = 1) -> dict[str, Any]:
    """该用户的复合冷却状态（按最近一次分手算）。"""
    row = db.one(
        "SELECT * FROM lover WHERE user_id = ? AND session_seq = ? AND cooldown_until IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (int(user_id), int(session_seq)),
    )
    if row is None:
        return {"active": False, "until": "", "days_left": 0}
    until = _parse(row["cooldown_until"])
    if until is None:
        return {"active": False, "until": "", "days_left": 0}
    now = datetime.now().astimezone()
    if until <= now:
        return {"active": False, "until": str(row["cooldown_until"]), "days_left": 0}
    days_left = max(1, int((until - now).total_seconds() // 86400) + 1)
    return {"active": True, "until": str(row["cooldown_until"]), "days_left": days_left}


def can_confess(user_id: int, session_seq: int = 1) -> tuple[bool, str, int]:
    """能否接受这个人的表白/复合：返回 (可不可以, 原因, 剩余天数)。"""
    lover = current_lover(session_seq)
    if lover is not None and int(lover["user_id"]) != int(user_id):
        return (False, "已经有恋人了", 0)
    cd = cooldown_state(user_id, session_seq)
    if cd["active"]:
        return (False, "冷却中", int(cd["days_left"]))
    rel = relation_of(user_id)
    if float(rel["affinity"]) < threshold():
        return (False, "好感还没到", 0)
    # 「认识够久」这道门（2026-11-02）：一天就处上对象不像人。
    # 从他第一次跟她说话算起，至少满 lover.min_days 天才接受表白。
    try:
        need_days = int(config_mod.get().relationship.lover.min_days)
    except Exception:  # noqa: BLE001 - 配置读不到就不卡这道门
        need_days = 0
    if need_days > 0:
        # mock/测试环境不卡这道门：那是拿假数据跑闸门，没有"认识多少天"这回事
        try:
            if not str(getattr(config_mod.get().provider, "api_key", "") or "").strip():
                need_days = 0
        except Exception:  # noqa: BLE001
            pass
    if need_days > 0:
        row = db.one("SELECT first_seen FROM qq_user WHERE id = ?", (int(user_id),))
        first = str(dict(row).get("first_seen") or "") if row else ""
        if first:
            try:
                since = datetime.fromisoformat(first)
                if since.tzinfo is None:
                    since = since.astimezone()
                days = (datetime.now().astimezone() - since).total_seconds() / 86400
                if days < need_days:
                    left = max(1, int(math.ceil(need_days - days)))
                    return (False, f"刚认识没多久（还差 {left} 天）", 0)
            except ValueError:
                pass
    return (True, "", 0)


# ---------------------------------------------------------------- 拉黑 / 遗忘


def is_blocked(user_id: int) -> bool:
    row = db.one("SELECT blocked FROM qq_user WHERE id = ?", (int(user_id),))
    return bool(row and int(row["blocked"] or 0))


def block_user(user_id: int, reason: str = "aversion") -> dict[str, Any]:
    """只拉黑这一个人：厌恶拉满不再终结整个会话（多人共用一个小鲸）。"""
    uid = int(user_id)
    db.execute(
        "UPDATE qq_user SET blocked = 1, blocked_reason = ? WHERE id = ?",
        (str(reason or "aversion")[:80], uid),
    )
    update_relation(uid, aversion=100)
    return dict(db.one("SELECT * FROM qq_user WHERE id = ?", (uid,)) or {})


def unblock_user(user_id: int) -> dict[str, Any]:
    uid = int(user_id)
    db.execute(
        "UPDATE qq_user SET blocked = 0, blocked_reason = '' WHERE id = ?", (uid,)
    )
    return dict(db.one("SELECT * FROM qq_user WHERE id = ?", (uid,)) or {})


def purge_self(openid: str) -> int:
    """把她自己的小号从"群成员"里删掉：她不该观察自己、也不该出现在花名册里。

    注意：`memories` 是 `mem_item` 的**只读视图**（见 app/memory/memv2.py），
    对它 `DELETE` 会抛 `cannot modify memories because it is a view`，而且异常发生在
    一半的表删完之后（半途删除）。所以记忆部分必须走 `h2.forget_user()` 的软删接口。
    """
    oid = str(openid or "").strip()
    if not oid:
        return 0
    row = db.one("SELECT id FROM qq_user WHERE openid = ?", (oid,))
    if row is None:
        return 0
    uid = int(row["id"])

    # 1) 记忆：只走引擎软删（可还原），绝不碰视图
    try:
        from app.memory import h2

        h2.forget_user(uid)
    except Exception:  # noqa: BLE001 - 引擎不可用时退化为直接软删引擎表
        try:
            db.execute("UPDATE mem_item SET forgotten = 1 WHERE user_id = ?", (str(uid),))
        except Exception:  # noqa: BLE001
            pass

    # 2) 其余业务表：硬删（都是普通表，没有视图）
    for table in ("user_relation", "member_impression", "human_notes",
                  "intimate_context", "intimate_profile", "lover"):
        db.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM events WHERE user_id = ? AND role = 'user'", (uid,))
    db.execute("DELETE FROM qq_user WHERE id = ?", (uid,))
    return uid


def forget_user(user_id: int) -> dict[str, Any]:
    """针对性失忆：清掉这个人的关系、记忆、印象、观察、私密记录、聊天原文。

    她是"忘了他"，不是"死了"：qq_user 这行留着（身份还在），
    但好感/信任/吸引/厌恶归零，重新按陌生人开始。
    """
    uid = int(user_id)
    counts: dict[str, int] = {}

    def _del(sql: str, args: tuple) -> None:
        with db.cursor() as cur:
            cur.execute(sql, args)
            counts[sql.split()[2]] = int(cur.rowcount or 0)  # DELETE FROM <table>

    # 恋人位：只结束这个人这条
    lover = lover_of(uid)
    if lover is not None:
        db.execute(
            "UPDATE lover SET status = 'ended', ended_at = ?, end_reason = ?, cooldown_until = NULL"
            " WHERE id = ?",
            (now_iso(), "forgotten", int(lover["id"])),
        )
    _del("DELETE FROM events WHERE user_id = ?", (uid,))
    from app.memory import h2

    counts["memories"] = h2.forget_user(uid)
    _del("DELETE FROM member_impression WHERE user_id = ?", (uid,))
    _del("DELETE FROM human_notes WHERE user_id = ?", (uid,))
    _del("DELETE FROM intimate_context WHERE user_id = ?", (uid,))
    _del("DELETE FROM intimate_profile WHERE user_id = ?", (uid,))
    _del("DELETE FROM user_relation WHERE user_id = ?", (uid,))
    db.execute(
        "UPDATE qq_user SET blocked = 0, blocked_reason = '', last_seen = ? WHERE id = ?",
        (now_iso(), uid),
    )
    for key in ("intimate_mode", "intimate_mode_ts", "love_pending",
                "proactive_last_ts", "proactive_window", "proactive_nag",
                "proactive_angry"):
        db.kv_set(f"{key}:{uid}", "")
    urow = db.one("SELECT openid FROM qq_user WHERE id = ?", (uid,))
    if urow and urow["openid"]:
        db.kv_set(f"greeted:{urow['openid']}", "")
    counts["user_id"] = uid
    return counts
