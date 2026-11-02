"""恋爱机制接线：表白 / 分手 / 她的主动提议 / 复合冷却话术。

表与规则在 `app/relation/users.py`；这里负责"聊天里怎么发生"：

- 用户表白 → 她按关系与冷却决定接受或拒绝（人类话术，不出现"系统/冷却/CD"）
- 用户提分手 → 结束恋人并进入冷却（默认 7 天）
- 她到了 100% 想主动提 → 先挂一个 `pending`，用户答应才真正绑定
- 每次触发都返回完整 state/meta，交回聊天接口，不再走模型（保证确定、快、不崩）
﻿﻿‍﻿‍‍​‍‍﻿‍‍‍‍‍‍‌﻿‍​‍‌﻿‍‍​‍‌‌‌‍‌​﻿"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core import config as config_mod
from app.core import db
from app.mind import models  # noqa: F401 - 保留给后续"她主动提议"用模型润色
from app.relation import engine
from app.relation import session as session_mod
from app.relation import users as users_mod

CONFESS_WORDS = (
    "我喜欢你", "喜欢你", "爱你", "我们在一起", "在一起吧", "做我女朋友", "当我女朋友",
    "想和你谈", "表白",
)
BREAKUP_WORDS = ("分手", "不合适", "分开吧", "别联系了", "我们算了吧", "到此为止")

#: 表白误判（真机实测）：日常句里也含"喜欢你/跟你说个事"，会被当成表白直接拒绝
#: - 「跟你说个事，我最近在学做饭」→ 「跟你说个事」原本在 CONFESS_WORDS 里（已删）
#: - 「我挺喜欢你现在的样子的」→ 是夸人，不是表白
_COMPLIMENT_ONLY = re.compile(
    r"(喜欢|爱)你(现在|这个|这样|的样|说话|刚才|笑|的脾气|的细心|的认真|这股)")
_CONFESS_LEAD = re.compile(r"跟你(说|讲)个?事")
_STRONG_CONFESS = re.compile(r"(我们在一起|在一起吧|做我女朋友|当我女朋友|表个?白|想和你谈)")


def _is_confession(text: str) -> bool:
    """判断这句是不是真·表白。日常夸奖/搭话不算。"""
    t = str(text or "").strip()
    if not t:
        return False
    if _STRONG_CONFESS.search(t):
        return True
    if _CONFESS_LEAD.search(t):
        return False
    if _COMPLIMENT_ONLY.search(t):
        return False
    return any(w in t for w in CONFESS_WORDS)

AGREE_WORDS = ("好", "愿意", "在一起", "可以", "嗯", "行", "答应", "同意")

# 答应她的主动提议：**只认"回答"，不认"提问"**
# 对抗训练实测（data/_adv）：一句「你到底还愿不愿意」被当成同意，直接把关系绑成恋人。
_QUESTION_TAIL = re.compile(r"[?？]\s*$|(吗|呢|么)\s*[?？…。!！]*\s*$")
_QUESTION_HINTS = (
    "愿不愿", "要不要", "可不可以", "会不会", "是不是", "能不能",
    "对不对", "好不好", "行不行", "想不想", "答不答应", "做不做",
)
_NEGATIONS = ("不", "没", "别", "算了", "拒绝", "拉倒", "再说吧")


#: 夸奖/评价里的"好"，不是同意（真机实测：一句「你刚才那句话说得挺好的」直接把关系绑成恋人）
_PRAISE_ONLY = re.compile(r"(挺好|很好|不错|还好|好点|好感|好意思|好听|好看)")
#: 同意必须**以同意词开头**（允许"我"前缀）：问句/否定/夸奖一律不算
_AGREE_START = re.compile(
    r"^(?:我)?(?:好|嗯|行|可以|愿意|同意|答应|没问题|就这个|确定|成)")


def _is_agreement(text: str) -> bool:
    """判断这句是不是"同意"——必须是简短、肯定、以同意词开头的回答。"""
    t = str(text or "").strip()
    if not t or len(t) > 16:
        return False
    if t.endswith(("?", "？")) or _QUESTION_TAIL.search(t):
        return False
    if any(h in t for h in _QUESTION_HINTS):
        return False
    if any(n in t for n in _NEGATIONS):
        return False
    if _PRAISE_ONLY.search(t):
        return False
    if not _AGREE_START.search(t):
        return False
    return any(w in t for w in AGREE_WORDS) or t.startswith(("没问题", "就这个", "确定"))

# 家人关系：这些人不会触发恋爱线（她是女儿，不谈恋爱）
# 2026-11-02 配置外置化：号码/别名不再写死在代码里（仓库要公开），
# 名单走 db kv `family_roles`（面板写入）→ config.local.yaml 的 `family.roles`。
#: 家人名单的 kv 键（JSON 数组；面板保存时同时写 kv 与配置）
FAMILY_KV = "family_roles"


def _role_sources() -> list[list[dict]]:
    """按优先级给出家人清单：db kv（面板即时值）→ config.family.roles。

    每项形如 `{openid, role, aliases}`。JSON 写坏了只当没有，绝不抛：
    `family_role()` 在每轮聊天的主链上。
    """
    sources: list[list[dict]] = []
    try:
        raw = db.kv_get(FAMILY_KV, "") or ""
    except Exception:  # noqa: BLE001 - kv 读失败就回落配置，别挡住聊天
        raw = ""
    if raw.strip():
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, list):
            sources.append(config_mod.normalize_family_roles(data))
    try:
        cfg = config_mod.get()
    except Exception:  # noqa: BLE001 - 配置半截写坏也不该让主链崩
        cfg = None
    if cfg is not None:
        sources.append(config_mod.normalize_family_roles(cfg.family.roles))
    return [items for items in sources if items]


def _match_role(sources: list[list[dict]], openid: str, nickname: str) -> str:
    """在清单里匹配：先 openid 精确，再别名/角色名（如「生父」「干爹」）。"""
    for items in sources:
        for item in items:
            if openid and item.get("openid") and item["openid"] == openid:
                return str(item.get("role") or "")
    if nickname:
        for items in sources:
            for item in items:
                names = set(item.get("aliases") or [])
                if item.get("role"):
                    names.add(item["role"])
                if nickname in names:
                    return str(item.get("role") or "")
    return ""


def family_role(user_id: int = 0, openid: str = "", nickname: str = "") -> str:
    """返回这个人在家庭里的角色（生父/干爹/空）。

    优先级：
      1. db kv `family_roles`（面板写的，JSON 数组）
      2. config.local.yaml 的 `family.roles`
      3. 别名 / 昵称命中（每项的 aliases + 角色名本身）
      4. user_id 反查 `qq_user`，再按 1–3 走一遍
    这样清库后 user_id 变了，身份也不会丢。
    """
    rid = str(openid or "").strip()
    nick = str(nickname or "").strip()
    role = _match_role(_role_sources(), rid, nick)
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    if role or uid <= 0:
        return role
    row = db.one("SELECT openid, nickname FROM qq_user WHERE id = ?", (uid,))
    if not row:
        return ""
    return _match_role(
        _role_sources(), str(row["openid"] or "").strip(), str(row["nickname"] or "").strip()
    )


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _lines(persona, scene: str, n: int = 2) -> list[str]:
    got = persona.anchors([scene], per_scene=n) or []
    return got or ["……嗯。"]


def _state(persona, user_id: int) -> dict[str, Any]:
    rel = users_mod.relation_of(user_id)
    payload = engine.stage_payload(persona, float(rel["affinity"]))
    return {
        "affinity": round(float(rel["affinity"]), 2),
        "trust": round(float(rel["trust"]), 2),
        "attraction": round(float(rel["attraction"]), 2),
        "aversion": round(float(rel["aversion"]), 1),
        "stage": payload.get("stage"),
        "stage_label": payload.get("stage_label"),
        "call_user": payload.get("call_user"),
        "next_stage": payload.get("next_stage"),
        "delta": {"affinity": 0.0, "trust": 0.0, "attraction": 0.0},
        "session": session_mod.session_payload(persona),
    }


def _result(persona, user_id: int, reply: list[str], action: str, extra: dict | None = None) -> dict[str, Any]:
    meta = {"love": action, "user_id": int(user_id), "delays_ms": [700 + len(x) * 30 for x in reply]}
    if extra:
        meta.update(extra)
    return {"reply": reply, "state": _state(persona, user_id), "meta": meta}


def _pending_key(user_id: int) -> str:
    return f"love_pending:{int(user_id)}"


def try_turn(rt, persona, text: str, user_id: int) -> dict[str, Any] | None:
    """QQ 群成员的恋爱触发。返回 None = 这轮不是恋爱事件，继续正常聊天。"""
    user_id = int(user_id or 0)
    if user_id <= 0:
        return None
    # 家人关系不触发恋爱线（她是女儿，生父/干爹都是家人）
    role = family_role(user_id)
    if role:
        if _is_confession(text):
            return _result(
                persona, user_id,
                ["……你是我爹啊，说什么呢。", "这种话别乱讲。"],
                "rejected_family",
                {"family": role},
            )
        return None
    _seq = session_mod.current_seq()
    lover = users_mod.lover_of(user_id, _seq)
    cooling = users_mod.cooldown_state(user_id, _seq)
    ok, reason, days = users_mod.can_confess(user_id, _seq)

    if _is_confession(text):
        if lover is not None:
            # 已经是这个人的恋人：再说“我爱你”不能被当成“别人来抢”
            if int(lover["user_id"]) == int(user_id):
                return _result(
                    persona, user_id,
                    ["我也爱你。", "别一直说……我会当真的。"],
                    "lover_again",
                )
            return _result(persona, user_id, ["我现在心里只有一个人了。", "你别往下说了。"], "rejected_taken")
        if cooling["active"]:
            reply = ["我最近不想谈这个。", f"……再给我 {int(days)} 天吧。"]
            return _result(persona, user_id, reply, "rejected_cooldown", {"days_left": int(days)})
        if reason == "已经有恋人了":
            return _result(persona, user_id, ["我现在心里只有一个人了。", "你别往下说了。"], "rejected_taken")
        if reason:
            return _result(persona, user_id, ["……我们还不到那一步。", "别急。"], "rejected_early")
        users_mod.bind_lover(_seq, user_id)
        # 绑上了先问一句"要不要公开"（2026-11-02 owner 要求）：
        # 标记挂 kv，下一轮他回"要/不要"时落答案，别替人决定。
        try:
            if bool(getattr(rt.cfg.relationship.lover, "ask_public", True)):
                from app.core import db as _db

                _db.kv_set(f"lover_public_pending:{int(user_id)}", "1")
        except Exception:  # noqa: BLE001 - 标记失败不影响绑定
            pass
        return _result(persona, user_id, _lines(persona, "被撒娇", 2), "bound")

    if _has(text, BREAKUP_WORDS):
        if lover is not None:
            cd = int(rt.cfg.relationship.lover.cooldown_days) if getattr(rt, "cfg", None) else 7
            users_mod.breakup(_seq, reason="user", cooldown_days=cd)
            return _result(persona, user_id, ["好。", "那就到这儿吧。"], "breakup", {"cooldown_days": cd})
        return _result(persona, user_id, ["我们又没在一起。", "……你在说什么。"], "breakup_none")

    # 答应她的主动提议：只认肯定回答（问句/否定句不算，见 _is_agreement）
    if db.kv_get(_pending_key(user_id), "") and _is_agreement(text) and ok and lover is None:
        users_mod.bind_lover(_seq, user_id)
        db.kv_set(_pending_key(user_id), "")
        return _result(persona, user_id, ["那我们就算在一起了。", "你别后悔。"], "bound_active")

    return None


def maybe_set_pending(persona, user_id: int) -> str:
    """她到 100% 且没有恋人时，挂一个"想主动提"的标记，并给模型的暗示句。"""
    user_id = int(user_id or 0)
    if user_id <= 0:
        return ""
    _seq = session_mod.current_seq()
    if users_mod.current_lover(_seq) is not None or users_mod.lover_of(user_id, _seq) is not None:
        return ""
    rel = users_mod.relation_of(user_id)
    if float(rel["affinity"]) < float(users_mod.threshold()):
        return ""
    if db.kv_get(_pending_key(user_id), ""):
        return ""
    db.kv_set(_pending_key(user_id), "1")
    return "你已经很确定自己的心意了。可以主动提一句，但别把话说得太满；他答应再算数。"
