from __future__ import annotations

import json
from datetime import date

from app.core import db
from app.persona.load import Persona

# 标签权重：好感不是"聊得多就高"，是"做了什么"。﻿﻿​‍﻿​﻿‌﻿​‍‍‍​‍‌‍‍​‌​‌﻿‍﻿‍‍﻿‌﻿﻿‍​﻿
TAG_WEIGHTS: dict[str, tuple[float, float, float]] = {
    # (affinity, trust, attraction)
    "care": (0.90, 0.30, 0.15),
    "self_disclosure": (0.70, 0.95, 0.20),
    "companionship": (0.75, 0.20, 0.10),
    "night": (0.55, 0.35, 0.45),
    "sweetness": (0.40, 0.05, 0.85),
    "repair": (0.55, 0.90, 0.30),
    "attack": (-1.60, -0.60, -0.30),
    "conflict": (-1.00, -0.35, -0.25),
    "neglect": (-0.45, -0.30, -0.15),
    "test": (-0.70, -0.45, -0.40),
    "comparison": (-0.90, -0.40, -0.35),
}

DAILY_POS_CAP = 5.0
#: caps 关着时也生效的硬日上限（一天最多涨这么多好感总和）
HARD_DAILY_POS = 12.0
DAILY_NEG_CAP = -8.0


def get_relation() -> dict:
    r = db.one("SELECT * FROM relation WHERE id = 1")
    if not r:
        return {"affinity": 0.0, "trust": 0.0, "attraction": 0.0}
    return {
        "affinity": float(r["affinity"]),
        "trust": float(r["trust"]),
        "attraction": float(r["attraction"]),
    }


def relation_for(user_id: int = 0) -> dict:
    """聊这一轮该看谁的关系：QQ 用户看他自己，面板/单人线看全局。

    以前一律 engine.get_relation()，结果 QQ 里 A/B 两个人的好感
    永远显示全局那一条——数据写了库，但她的 prompt 从来没读到。
    """
    uid = int(user_id or 0)
    if uid > 0:
        from app.relation import users as users_mod

        rel = users_mod.relation_of(uid)
        return {
            "affinity": float(rel["affinity"]),
            "trust": float(rel["trust"]),
            "attraction": float(rel["attraction"]),
            "aversion": float(rel.get("aversion") or 0),
        }
    rel = get_relation()
    try:
        from app.relation import session as session_mod

        aversion = float(session_mod.get_aversion())
    except Exception:  # noqa: BLE001
        aversion = 0.0
    return {**rel, "aversion": aversion}


def stage_payload(persona: Persona, affinity: float) -> dict:
    st = persona.stage_for(affinity)
    nxt = persona.next_stage(affinity)
    return {
        "stage": st.get("key", "observer"),
        "stage_label": st.get("label", ""),
        "call_user": st.get("call_user", "你"),
        "next_stage": (
            {"key": nxt.get("key"), "label": nxt.get("label"), "at": float(nxt.get("at", 0))}
            if nxt
            else None
        ),
        "stage_table": [
            {
                "key": s.get("key"),
                "label": s.get("label"),
                "at": float(s.get("at", 0)),
                "call_user": s.get("call_user", ""),
                "behavior": s.get("behavior", ""),
                "unlock": list(s.get("unlock") or []),
                "reached": affinity >= float(s.get("at", 0)),
            }
            for s in persona.stages()
        ],
    }


def _cap_key(user_id: int = 0) -> str:
    suffix = f":{int(user_id)}" if user_id else ""
    return f"relation_cap:{date.today().isoformat()}{suffix}"


def _load_cap(user_id: int = 0) -> dict:
    raw = db.kv_get(_cap_key(user_id), "")
    if not raw:
        return {"pos": 0.0, "neg": 0.0}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"pos": 0.0, "neg": 0.0}


def _save_cap(cap: dict, user_id: int = 0) -> None:
    db.kv_set(_cap_key(user_id), json.dumps(cap))


def caps_limits() -> tuple[float, float, bool]:
    """每日上限：返回 (正上限, 负上限, 是否启用)。默认关闭 = 不限。"""
    try:
        from app.core import config as config_mod

        caps = config_mod.get().relationship.caps
        return (float(caps.daily_pos), float(caps.daily_neg), bool(caps.enabled))
    except Exception:  # noqa: BLE001
        return (DAILY_POS_CAP, DAILY_NEG_CAP, False)


def log(trigger: str, reason: str, da: float, dt: float, dr: float,
        user_id: int | None = None) -> None:
    db.execute(
        "INSERT INTO relation_log (ts, user_id, d_affinity, d_trust, d_attraction, trigger, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), int(user_id or 0), da, dt, dr, trigger, reason),
    )


def _now() -> str:
    from app.memory.store import now_iso

    return now_iso()


def apply_appraisal(appraisal: dict, persona: Persona, trigger: str = "chat",
                    user_id: int | None = None) -> dict:
    """LLM 只有提议权。这里是唯一的数值入口。

    `user_id` 给定时（QQ 群多用户）：读/写该用户独立关系与厌恶，不碰全局 relation。
    数值规则、限幅、安全阀完全一致——只是落在不同的人身上。
    """
    if not appraisal:
        return {"d_affinity": 0.0, "d_trust": 0.0, "d_attraction": 0.0, "capped": False}

    intensity = max(0.0, min(1.5, float(appraisal.get("intensity", 0.4))))
    tags = [str(t) for t in (appraisal.get("tags") or [])]
    valence = str(appraisal.get("valence", "neutral"))

    # 修复奖励：上一轮是冲突/试探/比较，这一轮转好了，信任要比平时涨得多。
    # 理由是"吵完还在"比"一直很好"更像一段真实关系。
    prev_raw = db.kv_get("last_appraisal", "")
    prev_tags: set[str] = set()
    if prev_raw:
        try:
            prev_tags = set(json.loads(prev_raw).get("tags") or [])
        except json.JSONDecodeError:
            prev_tags = set()
    repair = bool(prev_tags & {"conflict", "test", "comparison"}) and valence == "positive"
    if repair and "repair" not in tags:
        tags.append("repair")
    db.kv_set("last_appraisal", json.dumps(appraisal, ensure_ascii=False))

    base_tags = set(tags)  # 规则词表识别的标签（在模型标签合并前），用于安全限幅
    w_a = w_t = w_r = 0.0
    for tag in tags:
        a, t, r = TAG_WEIGHTS.get(tag, (0.0, 0.0, 0.0))
        w_a += a
        w_t += t
        w_r += r
    if valence == "negative" and w_a > 0:
        w_a = -w_a * 0.7
    if valence == "neutral" and not tags:
        w_a = 0.15

    raw_a = w_a * intensity * 1.2
    raw_t = w_t * intensity * 1.2
    raw_r = w_r * intensity * 1.0
    if repair:
        raw_a += 0.35
        raw_t += 0.55

    # 语言分析（可选）：规则 40% + 模型判断 60%，两边都封顶后取平均效果
    llm = appraisal.get("llm") if isinstance(appraisal, dict) else None
    risky = bool({"attack", "test", "comparison", "conflict"} & set(tags))
    if isinstance(llm, dict) and risky:
        # 被攻击/试探/比较时，不允许模型把它判成加分
        llm = dict(llm)
        llm["affinity"] = min(0.0, float(llm.get("affinity", 0)))
        llm["trust"] = min(0.0, float(llm.get("trust", 0)))
        llm["attraction"] = min(0.0, float(llm.get("attraction", 0)))
        if float(llm.get("aversion", 0)) <= 0:
            llm["aversion"] = 0.0
    if isinstance(llm, dict):
        raw_a = raw_a * 0.4 + float(llm.get("affinity", 0)) * 0.6
        raw_t = raw_t * 0.4 + float(llm.get("trust", 0)) * 0.6
        raw_r = raw_r * 0.4 + float(llm.get("attraction", 0)) * 0.6
        if llm.get("tags"):
            tags = list(dict.fromkeys(tags + [str(t) for t in llm["tags"]]))[:6]
        if llm.get("reason"):
            appraisal = {**appraisal, "reason": llm["reason"]}

    # 厌恶值：不看热度，只看"他有没有把我当人"。
    # 校准（2026-10-28）：平常不涨；玩笑式冲撞小涨；只有明确辱骂才大幅涨。
    aversion_delta = 0.0
    if "attack" in base_tags:
        aversion_delta += 6.0 * intensity  # 明确辱骂：大幅
    if "conflict" in tags and "attack" not in base_tags:
        aversion_delta += 0.8 * intensity  # 一般冲撞/玩笑：很小
    if "comparison" in tags or "test" in tags:
        aversion_delta += 0.35 * intensity  # 试探/比较：几乎只是提醒
    if isinstance(llm, dict) and "aversion" in llm:
        llm_av = float(llm.get("aversion", 0))
        if risky:
            # 模型只有提议权：非辱骂最多 1.5，明确辱骂最多 4
            cap = 4.0 if "attack" in base_tags else 1.5
            aversion_delta = max(aversion_delta, min(llm_av, cap))
        else:
            # 普通聊天模型不许把厌恶顶起来
            aversion_delta = max(aversion_delta, min(llm_av, 0.5))
    elif repair or (valence == "positive" and not {"attack", "conflict"} & set(tags)):
        aversion_delta -= 1.2

    # 单轮上限：辱骂仍可到 6；非辱骂最多 1.5
    aversion_delta = max(-6.0, min(6.0, aversion_delta))
    if "attack" not in base_tags:
        aversion_delta = min(aversion_delta, 1.5)
    if {"test", "comparison"} & base_tags and not {"attack", "conflict"} & base_tags:
        aversion_delta = min(aversion_delta, 0.8)

    # 性格调制：越敏感，越经不起攻击；自持越高，越压得住
    sensitivity = persona.trait_value("敏感") if persona.trait_value("敏感") else 60
    steadiness = persona.trait_value("自持") if persona.trait_value("自持") else 70
    if aversion_delta > 0:
        aversion_delta *= 0.7 + (sensitivity / 100) * 0.6 - (steadiness / 100) * 0.3
    aversion = 0.0
    if user_id:
        from app.relation import users as users_mod

        current = users_mod.relation_of(int(user_id))
        aversion = max(0.0, min(100.0, float(current["aversion"]) + aversion_delta))
    else:
        try:
            from app.relation import session as session_mod

            aversion = session_mod.adjust_aversion(aversion_delta)
        except Exception:  # noqa: BLE001
            pass
    cap = _load_cap(int(user_id or 0))
    pos_cap, neg_cap, caps_on = caps_limits()

    def _bypassed() -> bool:
        """控制台可以对某个用户单独关掉上限（kv relation_cap_off:<uid>=1）。"""
        try:
            return db.kv_get(f"relation_cap_off:{int(user_id or 0)}", "0") == "1"
        except Exception:  # noqa: BLE001
            return False

    _off = _bypassed()

    def _pos(delta: float) -> tuple[float, float]:
        # caps 关着时以前是"完全不限"，于是一天就能冲满。
        # 现在即使 caps 关着，也有一个硬日上限（HARD_DAILY_POS），只是比手工配的宽。
        if _off:
            cap["pos"] += delta
            return delta, cap["pos"]
        limit = pos_cap if caps_on else HARD_DAILY_POS
        d = max(0.0, min(delta, limit - cap["pos"]))
        cap["pos"] += d
        return d, cap["pos"]

    def _neg(delta: float) -> float:
        if _off or not caps_on:
            cap["neg"] += delta
            return delta
        d = min(0.0, max(delta, neg_cap - cap["neg"]))
        cap["neg"] += d
        return d

    raw_a = max(-3.5, min(3.5, raw_a))
    raw_t = max(-3.5, min(3.5, raw_t))
    raw_r = max(-3.5, min(3.5, raw_r))

    # ---- 边际递减 + 强制日上限（2026-11-02）----
    # 问题：一天聊几十句就能从 0 冲到 90、直接绑定恋人，不像人。
    # 两条刹车：①越往上越难涨（按当前好感分档打折）；②不管 caps 开没开，
    # 每天正向增幅都有硬上限，冲不上去就得多花几天。
    if user_id:
        _cur = None  # 占位：真正的当前值在下面统一取
    _cur_rel = None
    try:
        if user_id:
            from app.relation import users as _users_mod

            _cur_rel = _users_mod.relation_of(int(user_id))
        else:
            _cur_rel = get_relation()
    except Exception:  # noqa: BLE001
        _cur_rel = None
    _cur_aff = float((_cur_rel or {}).get("affinity", 0.0))

    def _decay(cur: float) -> float:
        if cur < 30:
            return 1.0
        if cur < 50:
            return 0.7
        if cur < 70:
            return 0.45
        if cur < 85:
            return 0.25
        return 0.12

    _f = _decay(_cur_aff)
    raw_a *= _f
    raw_t *= _f
    raw_r *= _f

    if raw_a >= 0:
        da, _ = _pos(raw_a)
    else:
        da = _neg(raw_a)
    # 三维共用同一套日上限。以前只有好感走 _pos：信任正数直接取 raw_t、
    # 吸引整个跳过，开了 caps 也等于只限住一维（docs/23 §3.1 第 7 条）。
    if raw_t >= 0:
        dt, _ = _pos(raw_t)
    else:
        dt = _neg(raw_t)
    if raw_r >= 0:
        dr, _ = _pos(raw_r)
    else:
        dr = _neg(raw_r)
    _save_cap(cap, int(user_id or 0))

    if user_id:
        from app.relation import users as users_mod

        current = users_mod.relation_of(int(user_id))
        new_a = max(0.0, min(100.0, float(current["affinity"]) + da))
        new_t = max(0.0, min(100.0, float(current["trust"]) + dt))
        new_r = max(0.0, min(100.0, float(current["attraction"]) + dr))
        users_mod.update_relation(
            int(user_id), affinity=new_a, trust=new_t, attraction=new_r, aversion=aversion
        )
    else:
        rel = get_relation()
        new_a = max(0.0, min(100.0, rel["affinity"] + da))
        new_t = max(0.0, min(100.0, rel["trust"] + dt))
        new_r = max(0.0, min(100.0, rel["attraction"] + dr))
        db.execute(
            "UPDATE relation SET affinity = ?, trust = ?, attraction = ?, updated_at = ? WHERE id = 1",
            (new_a, new_t, new_r, _now()),
        )
    log(trigger, str(appraisal.get("reason", "")), da, dt, dr, user_id=int(user_id or 0))

    return {
        "aversion": round(aversion, 1),
        "d_aversion": round(aversion_delta, 2),
        "aversion_phase": _phase(aversion),
        "d_affinity": round(da, 2),
        "d_trust": round(dt, 2),
        "d_attraction": round(dr, 2),
        "affinity": round(new_a, 2),
        "trust": round(new_t, 2),
        "attraction": round(new_r, 2),
        "capped": any(
            abs(x - y) > 0.01 for x, y in ((da, raw_a), (dt, raw_t), (dr, raw_r))
        ),
        **stage_payload(persona, new_a),
    }


def _phase(av: float) -> str:
    if av >= 100:
        return "ended"
    if av >= 85:
        return "cold"
    if av >= 60:
        return "irritated"
    if av >= 30:
        return "uneasy"
    return "calm"


def set_relation(
    affinity: float | None = None,
    trust: float | None = None,
    attraction: float | None = None,
    reason: str = "手动校准",
) -> dict:
    rel = get_relation()
    a = rel["affinity"] if affinity is None else max(0.0, min(100.0, affinity))
    t = rel["trust"] if trust is None else max(0.0, min(100.0, trust))
    r = rel["attraction"] if attraction is None else max(0.0, min(100.0, attraction))
    db.execute(
        "UPDATE relation SET affinity = ?, trust = ?, attraction = ?, updated_at = ? WHERE id = 1",
        (a, t, r, _now()),
    )
    log("manual", reason, a - rel["affinity"], t - rel["trust"], r - rel["attraction"], user_id=0)
    return {"affinity": a, "trust": t, "attraction": r}


def recent_log(limit: int = 20) -> list[dict]:
    return [
        dict(r)
        for r in db.rows("SELECT * FROM relation_log ORDER BY id DESC LIMIT ?", (limit,))
    ]


# ---------------------------------------------------------------- traits

def init_traits(persona: Persona) -> None:
    """初始化并同步人格向量。

    人物包改过基线时：基线跟着改；当前值若还停在旧基线（没漂移过），也一起跟过去。
    已经漂移的值保留——那是经历，不该被改设定覆盖。
    """
    for dim, spec in persona.traits().items():
        base = float(spec.get("baseline", 50))
        mn = float(spec.get("min", base - 15))
        mx = float(spec.get("max", base + 15))
        row = db.one("SELECT * FROM traits WHERE dim = ?", (dim,))
        if row is None:
            db.execute(
                "INSERT INTO traits (dim, value, baseline, min_value, max_value) VALUES (?, ?, ?, ?, ?)",
                (dim, base, base, mn, mx),
            )
            continue
        value = float(row["value"])
        old_base = float(row["baseline"])
        if abs(value - old_base) < 0.01:
            value = base
        value = max(mn, min(mx, value))
        db.execute(
            "UPDATE traits SET value = ?, baseline = ?, min_value = ?, max_value = ? WHERE dim = ?",
            (value, base, mn, mx, dim),
        )


def get_traits() -> list[dict]:
    return [
        {
            "dim": r["dim"],
            "value": float(r["value"]),
            "baseline": float(r["baseline"]),
            "min": float(r["min_value"]),
            "max": float(r["max_value"]),
        }
        for r in db.rows("SELECT * FROM traits ORDER BY rowid")
    ]
