"""夜里独处：让她自己想点事。

引擎的陪伴层只给「想什么」（`ThoughtSeed`）和「写回台账」，不生成文字；
生成归宿主。这里就是宿主那一半：

- 夜里（**23:00–06:00**）试"一整件事"：额度与冷却（同一颗念头 24h 一次、
  24h 最多 3 条、重要度封顶）；
- 白天（**08:00–22:00**，不含 22 点）试"碎片"：挤地铁/吃外卖/等电梯时脑子里闪一下，
  间隔 4 小时、每天 2 条，**额度与夜里那套分开记**，不跟夜里抢；
- 写回的条目 `kind=thought`，**不进召回热层**（她不会把自言自语当成用户说过的话）；
- 生成走她的模型（mock 下也能跑），失败就不写，绝不硬编。

调用方式：
- 主链里 `should_try()` 为真时丢一个后台任务（不拖慢这一轮回复）；
- 手动/调试：`maybe_ruminate(..., force=True)`。
﻿​‌‌‌﻿‌‌​‌‍‌‍​‍‍﻿​﻿​‌‍​‌‍‌‍‍﻿‍﻿‌‍﻿"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any

from app.core import db, memengine
from app.life import clock
from app.memory import companion, h2
from symvolve_memory.companion.emotion import detect_emotion
from symvolve_memory.companion.rumination import thought_signature

THOUGHT_KIND = "thought"
_LAST_KEY = "idle_thought_last"
_INTERVAL_SECONDS = 3 * 3600
_NIGHT_HOURS = frozenset([23, 0, 1, 2, 3, 4, 5])

# ---------------------------------------------------------------- 白天碎片 --
# 夜里那套（_LAST_KEY / _INTERVAL_SECONDS / THOUGHT_KIND / 台账 3 条/24h）原样保留；
# 白天是最小的一小片念头，另开一套 kv，跟夜里互不抢额度。
_DAY_HOURS = frozenset(range(8, 22))          # 白天窗口：08–21 点整（22 点整就不想了）
_FRAG_LAST_KEY = "idle_fragment_last"         # 上一次碎片的时间戳
_FRAG_ENABLED_KEY = "idle_fragment_enabled"   # "0" = 白天碎片关掉
_FRAG_INTERVAL_KEY = "idle_fragment_interval_hours"
_FRAG_MAX_KEY = "idle_fragment_daily_max"
_FRAG_COUNT_PREFIX = "idle_fragment_count"    # <uid>:<日期> → 当天已想了几条碎片
_FRAG_INTERVAL_HOURS = 4                      # 默认间隔：4 小时（别比夜里还密）
_FRAG_DAILY_MAX = 2                           # 默认额度：每天 2 条


def _enabled() -> bool:
    return os.environ.get("AI_LIFE_IDLE", "1") != "0"


def _kv_int(key: str, default: int) -> int:
    """读一个整数 kv；缺省/坏值一律退回 default（不抛）。"""
    try:
        return int(db.kv_get(key, str(default)) or default)
    except (TypeError, ValueError):
        return int(default)


def _fragment_enabled() -> bool:
    """白天碎片总开关（kv 里写 "0" 就关掉；缺省是开）。"""
    try:
        return db.kv_get(_FRAG_ENABLED_KEY, "1") != "0"
    except Exception:  # noqa: BLE001
        return False


def _fragment_interval_seconds() -> int:
    """白天两次碎片之间至少隔多久；配坏了退回 4 小时（最少 1 小时，别刷屏）。"""
    hours = _kv_int(_FRAG_INTERVAL_KEY, _FRAG_INTERVAL_HOURS)
    return max(1, hours) * 3600


def _fragment_daily_max() -> int:
    """白天每天最多几条碎片；配坏了按 2 条，负数当 0。"""
    return max(0, _kv_int(_FRAG_MAX_KEY, _FRAG_DAILY_MAX))


def _fragment_cooling(moment: int) -> int:
    """离下一次允许想碎片还差多少秒；0 = 现在就可以。"""
    try:
        last = int(db.kv_get(_FRAG_LAST_KEY, "0") or 0)
    except (TypeError, ValueError):
        last = 0
    left = _fragment_interval_seconds() - (int(moment) - last)
    return int(left) if left > 0 else 0


def _fragment_date(moment: int) -> str:
    """碎片额度按自然日记（本地时区），跨零点自然重置。"""
    try:
        return datetime.fromtimestamp(int(moment)).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return datetime.now().strftime("%Y-%m-%d")


def _fragment_count(uid: str, moment: int) -> int:
    """这个用户当天已经想了几条碎片。"""
    return max(0, _kv_int(f"{_FRAG_COUNT_PREFIX}:{uid}:{_fragment_date(moment)}", 0))


def _bump_fragment_count(uid: str, moment: int) -> int:
    """记一条碎片，返回今天的新计数。"""
    used = _fragment_count(uid, moment) + 1
    db.kv_set(f"{_FRAG_COUNT_PREFIX}:{uid}:{_fragment_date(moment)}", str(used))
    return used


def _fragment_namespace(uid: str) -> str:
    """碎片在念头台账里单独占一个命名空间：白天写多少都不吃夜里那 3 条/24h。"""
    return f"{uid}#fragment"


def _day_part(hour: int) -> str:
    """白天三档（写进 meta，也用来给提示词定分寸）。"""
    if hour < 11:
        return "morning"      # 08–10 点
    if hour < 14:
        return "noon"         # 11–13 点
    return "afternoon"        # 14–21 点


def _hour(now_ts: int | None = None) -> int:
    if now_ts is None:
        return int(clock.now().hour)
    return int(time.localtime(int(now_ts)).tm_hour)


def should_try(now_ts: int | None = None) -> bool:
    """现在要不要试着让她想点事（便宜判断，主链每轮都可以问）。

    夜里（23–06）照旧走原来那套；白天（08–22，不含 22 点）走碎片那套；
    两个窗口任一成立就是 True。真正的额度由 ``maybe_ruminate`` 动手前再卡一道。
    """
    if not _enabled():
        return False
    moment = int(now_ts if now_ts is not None else time.time())
    hour = _hour(moment)
    if hour in _NIGHT_HOURS:
        if db.kv_get("idle_thought_enabled", "1") == "0":
            return False
        try:
            last = int(db.kv_get(_LAST_KEY, "0") or 0)
        except ValueError:
            last = 0
        return moment - last >= _INTERVAL_SECONDS
    if hour in _DAY_HOURS:
        if not _fragment_enabled():
            return False
        return _fragment_cooling(moment) == 0
    return False


def _system(persona) -> str:
    name = getattr(persona, "name", "小涵")
    return (
        f"你是{name}。现在是夜里，你一个人待着，脑子里浮起一件旧事。\n"
        "用一两句自言自语把它写下来，不超过 40 字：可以是回想、嘀咕、担心、或者忽然明白。\n"
        "**从给你的真实片段出发想**：可以记岔、可以模糊、可以像做梦；"
        "但不要凭空造出完全不相干的人和事。\n"
        "如果你记不太清，就写成好像/大概/记不清的样子；如果是梦，就写成梦里的样子。\n"
        "不要写成对别人说的话，不要提问，不要提「记忆」「记录」「系统」这些词。"
    )


def _system_fragment(persona) -> str:
    """白天碎片版提示词：一两句、像脑子里闪一下、不交代"我想起"。夜里那版不动。"""
    name = getattr(persona, "name", "小涵")
    return (
        f"你是{name}。现在是白天，你正忙着自己的事（挤地铁、吃外卖、等电梯这一类）。\n"
        "脑子里忽然闪过一小片念头，用一两句写下来，不超过 30 字。\n"
        "要的就是那种闪一下：短、碎、没头没尾；**不要**拿「我想起」「刚刚」「对了」"
        "这种交代开头，也别写成一整段回忆。\n"
        "**从给你的真实片段出发想**：可以模糊、可以记岔；"
        "但不要凭空造出完全不相干的人和事。\n"
        "不要写成对别人说的话，不要提问，不要提「记忆」「记录」「系统」这些词。"
    )


_GROUND_MIN = 0.20   # 有据线：达到就算"联想有据"
_CLEAR_MIN = 0.45    # 清醒级：大部分细节都对得上
# 低于 _GROUND_MIN 但仍有共享片段 → 梦境/遗忘级（允许，但要说得不确定）


def _evidence(user_id: int, seed) -> list[dict[str, Any]]:
    """把"能作证的东西"捞出来：种子指向的记忆 + 它们的来源原话 + 最近聊天。"""
    store = memengine.store()
    out: list[dict[str, Any]] = []
    for mid in list(seed.item_ids)[:3]:
        item = store.get_item(int(mid))
        if item is None:
            continue
        out.append({"kind": "memory", "id": int(mid), "text": str(item.text)[:240]})
        for eid in list(item.source_event_ids)[:2]:
            ev = store.get_event(int(eid))
            if ev is not None and str(ev.raw_text or "").strip():
                out.append({"kind": "event", "id": int(eid), "text": str(ev.raw_text)[:240]})
    try:
        rows = db.rows(
            "SELECT role, text FROM events WHERE user_id = ? ORDER BY id DESC LIMIT 8",
            (int(user_id or 0),),
        )
    except Exception:  # noqa: BLE001
        rows = []
    for row in reversed(rows):
        body = " ".join(str(row["text"] or "").split())
        if body:
            out.append({"kind": "chat", "id": 0, "text": f"{row['role']}: {body[:200]}"})
    seen: set[tuple] = set()
    uniq: list[dict[str, Any]] = []
    for e in out:
        key = (e["kind"], e["id"], e["text"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return uniq[:6]


def _blob(evidence: list[dict[str, Any]]) -> str:
    return "\n".join(str(e.get("text") or "") for e in evidence)


def _bigrams(text: str) -> set[str]:
    body = "".join(ch for ch in str(text) if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return {body[i:i + 2] for i in range(len(body) - 1)} or ({body} if body else set())


def _grounding_detail(text: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """把"有多像真的"拆成光谱：共享片段数 + 重合度 + 新出现的数字。

    人记错、做梦、遗忘都是常态；这里不再一票否决，只负责给这次联想定级：
      clear  ≥0.45   清醒：大部分对得上
      fuzzy  0.20~0.45  朦胧：只记得大概
      dream  >0 但 <0.20  梦/遗忘：只有一点影子
      none   0 个共享片段  纯编：连影子都没有，退化成复述或不写
    """
    blob = _blob(evidence)
    body = str(text or "").strip()
    if not blob or not body:
        return {"score": 0.0, "shared": 0, "new_digits": [], "mode": "none"}
    grams, base = _bigrams(body), _bigrams(blob)
    shared = grams & base
    score = (len(shared) / len(grams)) if grams else 0.0
    new_digits = [n for n in re.findall(r"\d{2,}", body) if n not in blob]
    if not shared:
        mode = "none"
    elif score >= _CLEAR_MIN:
        mode = "clear"
    elif score >= _GROUND_MIN:
        mode = "fuzzy"
    else:
        mode = "dream"
    return {
        "score": round(score, 3),
        "shared": len(shared),
        "new_digits": new_digits[:5],
        "mode": mode,
    }


def _grounding_score(text: str, evidence: list[dict[str, Any]]) -> float:
    """给测试/调试用的单值：0 = 连影子都没有；越高越像真事。"""
    return float(_grounding_detail(text, evidence)["score"])


def _fallback_thought(evidence: list[dict[str, Any]]) -> str:
    """生成被判"编"时的退路：直接用真实片段复述，不添油加醋。"""
    for item in evidence:
        body = " ".join(str(item.get("text") or "").split())
        body = re.sub(r"^(user|her|我|她)\s*[:：]\s*", "", body)
        body = body.strip("。！？… ，,")
        if len(body) >= 4:
            return f"又想起{body[:26]}。"
    return ""


def _fallback_fragment(evidence: list[dict[str, Any]]) -> str:
    """白天判"编"时的退路：照真实片段复述一句，不写「又想起」（碎片没有来龙去脉）。"""
    for item in evidence:
        body = " ".join(str(item.get("text") or "").split())
        body = re.sub(r"^(user|her|我|她)\s*[:：]\s*", "", body)
        body = body.strip("。！？… ，,")
        if len(body) >= 4:
            return f"{body[:26]}。"
    return ""


async def _generate(rt, persona, seed, evidence: list[dict[str, Any]],
                    *, fragment: bool = False) -> str | None:
    try:
        from app.mind import models

        spec = models.pick("summary")
        facts = "\n".join(f"- {e['text']}" for e in evidence)
        system = _system_fragment(persona) if fragment else _system(persona)
        reply = await rt.llm.chat(
            system,
            [{"role": "user", "content": f"【真实片段】\n{facts}\n\n【此刻浮起的】\n{seed.prompt}"}],
            max_tokens=40 if fragment else 100,   # 碎片本来就短，别给模型写长的空间
            model=spec.provider_model,
        )
        text = " ".join(str(reply.text or "").strip().split())
        return (text[:40] if fragment else text[:80]) or None
    except Exception:  # noqa: BLE001 - 想事失败不该惊动任何人
        return None


def _pick_fragment_seed(cm, uid: str, moment: int):
    """白天挑料：优先挑今天还没想过的那颗（碎片一天最多两条，别重样）。

    去重借念头台账，但用 ``<uid>#fragment`` 这个独立命名空间，
    所以白天想过的种子既不会重复，也不会去占夜里那 3 条/24h。
    """
    try:
        seeds = cm.idle(user_id=uid, limit=3)
    except Exception:  # noqa: BLE001
        return None
    ns = _fragment_namespace(uid)
    for seed in seeds:
        try:
            sig = thought_signature(seed)
        except Exception:  # noqa: BLE001
            continue
        if cm.thoughts.cooling(user_id=ns, signature=sig, now=moment) is None:
            return seed
    return None


async def maybe_ruminate(rt, persona, user_id: int = 0, *, force: bool = False,
                         now: int | None = None) -> dict[str, Any] | None:
    """想一轮。返回 None = 这次不该/没能想；否则返回结算（ok 可能为 False）。

    按 ``now`` 的时段自动选模式：

    - 夜里（23–06）：原来那一整套，行为一字不改（3h 间隔、3 条/24h、
      重要度 0.15/0.18、grounded 光谱）；
    - 白天（08–22，不含 22 点）：碎片模式——同一条生成链，但间隔/额度用白天那套，
      ``extra_meta`` 多带 ``fragment=True`` 与 ``day_part``，提示词换成"闪一下"版。

    ``force=True`` 只跳过窗口判断，**间隔与额度照样守**（同一天连着两次还是会被拦）。
    """
    moment = int(now if now is not None else time.time())
    if not force and not should_try(moment):
        return None

    hour = _hour(moment)
    fragment = hour in _DAY_HOURS      # 白天 = 碎片；其余时段按夜里那套（含 06–08 点）
    uid = str(int(user_id or 0))
    cm = companion.get(uid)

    if fragment:
        left = _fragment_cooling(moment)
        if left > 0:
            return {"ok": False, "fragment": True,
                    "reason": f"白天碎片还在冷却（{left} 秒）"}
        used = _fragment_count(uid, moment)
        if used >= _fragment_daily_max():
            return {"ok": False, "fragment": True, "reason": "今天的碎片额度用完了"}
        seed = _pick_fragment_seed(cm, uid, moment)
        if seed is None:
            db.kv_set(_FRAG_LAST_KEY, str(moment))
            return {"ok": False, "fragment": True,
                    "reason": "没有可想的料（或今天已经想过了）"}
        signature = thought_signature(seed)
    else:
        seeds = cm.idle(user_id=uid, limit=1)
        if not seeds:
            db.kv_set(_LAST_KEY, str(moment))
            return {"ok": False, "reason": "没有可想的料"}
        seed = seeds[0]
        signature = thought_signature(seed)

        left = cm.thoughts.cooling(user_id=uid, signature=signature, now=moment)
        if left is not None:
            return {"ok": False, "reason": f"这颗念头还在冷却（{int(left)} 秒）"}
        try:
            used = int(cm.thoughts.recent_count(user_id=uid, now=moment))
        except Exception:  # noqa: BLE001
            used = 0
        if used >= int(cm.thoughts.daily_limit):
            return {"ok": False, "reason": "今天的念头额度用完了"}

    evidence = _evidence(int(uid), seed)
    if not evidence:
        fail: dict[str, Any] = {"ok": False, "reason": "没有可依据的聊天记录"}
        if fragment:
            fail["fragment"] = True
        return fail
    text = await _generate(rt, persona, seed, evidence, fragment=fragment)
    if not text:
        return None
    detail = _grounding_detail(text, evidence)
    fallback = False
    if detail["mode"] == "none":
        # 连一个共同片段都没有：不是"记错"，是"凭空编"，退化成有据的复述
        safe = _fallback_fragment(evidence) if fragment else _fallback_thought(evidence)
        if not safe:
            fail = {"ok": False, "reason": "想出来的东西跟聊天记录毫无关系"}
            if fragment:
                fail["fragment"] = True
            return fail
        text = safe
        fallback = True
        detail = _grounding_detail(text, evidence)
        detail["mode"] = "fallback"

    importance = 0.15 if detail["mode"] == "dream" else 0.18
    extra_meta: dict[str, Any] = {
        "grounded": {
            "score": detail["score"],
            "mode": detail["mode"],
            "shared": detail["shared"],
            "new_digits": detail["new_digits"],
            "fallback": bool(fallback),
            "evidence_ids": sorted({int(e["id"]) for e in evidence if int(e["id"] or 0) > 0}),
            "evidence_kinds": sorted({str(e["kind"]) for e in evidence}),
        }
    }
    day_part = ""
    if fragment:
        day_part = _day_part(hour)
        extra_meta["fragment"] = True
        extra_meta["day_part"] = day_part
    mid = h2.add(
        THOUGHT_KIND,
        text,
        importance=importance,
        cues=["念头"],
        user_id=int(uid),
        chat_type="",
        extra_meta=extra_meta,
    )
    if not mid:
        return None

    if fragment:
        # 白天：额度/间隔/去重都记在白天那套上；台账只写 #fragment 命名空间，
        # 所以同一天的夜里照样能想满它自己的 3 条。
        used = _bump_fragment_count(uid, moment)
        try:
            cm.thoughts.record(
                user_id=_fragment_namespace(uid), signature=signature, at=moment,
                item_id=int(mid), seed_kind=str(seed.kind),
            )
            companion.save_ledgers()
        except Exception:  # noqa: BLE001 - 台账写失败不影响这碎片
            pass
        db.kv_set(_FRAG_LAST_KEY, str(moment))
        return {
            "ok": True,
            "fragment": True,
            "day_part": day_part,
            "item_id": int(mid),
            "text": text,
            "seed_kind": str(seed.kind),
            "seed_item_ids": [int(x) for x in seed.item_ids],
            "used_today": used,
            "grounded": {"score": detail["score"], "mode": detail["mode"],
                         "fallback": bool(fallback)},
        }

    try:
        cm.thoughts.record(
            user_id=uid, signature=signature, at=moment,
            item_id=int(mid), seed_kind=str(seed.kind),
        )
        companion.save_ledgers()
    except Exception:  # noqa: BLE001 - 台账写失败不影响这条念头
        pass
    db.kv_set(_LAST_KEY, str(moment))
    return {
        "ok": True,
        "item_id": int(mid),
        "text": text,
        "seed_kind": str(seed.kind),
        "seed_item_ids": [int(x) for x in seed.item_ids],
        "used_today": used + 1,
        "grounded": {"score": detail["score"], "mode": detail["mode"],
                     "fallback": bool(fallback)},
    }


# ---------------------------------------------------------------- 第二天浮现 --

_SURFACE_START_HOUR = 7       # 她醒着之后
_SURFACE_END_HOUR = 23        # 睡前不再翻旧账
_SURFACE_MAX_AGE_HOURS = 36   # 只翻"昨天/今天凌晨"想的，太旧的沉下去
_SURFACE_GAP_HOURS = 6        # 两次主动提念头之间至少隔 6 小时

# ---- 主动带出：不调模型时用的确定性模板（同一颗念头每次说出来都一样）----
_LINE_FRAGMENT_MAX = 60       # 片段最多留 60 字
_LINE_TOTAL_MAX = 90          # 一整句封顶 90 字，别一开口就是长文
_LINE_TRIM_CHARS = "。．.！!？?…，,、；;：:~～ \t\r\n\"'“”‘’」』】]}）)"  # 首尾都剃掉的标点
_LINE_STOP_CHARS = "。！？…!?"  # 已经收尾了就不再补句号
#: 念头正文开头的"交代词"（是她想的，不用再说一遍是什么时候想的）
_LINE_LEAD_RE = re.compile(
    r"^(?:又想起|又想到|又记起|忽然想起|突然想起|刚刚想起|刚才想起|想起|想到|"
    r"刚刚|刚才|昨晚|昨天|今早|今天|这两天|前几天|前阵子|记得|还记得|好像)+"
)


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


#: 引擎情绪词表很窄（认得"累"，不认得"撑不住"），这里补一层本地口语判断。
_DISTRESS_WORDS = (
    "难受", "撑不住", "崩溃", "想哭", "不想活", "受不了", "绝望", "委屈", "害怕",
    "吵架", "分手", "被骂", "压力", "烦死", "好累", "太累", "心累", "失眠",
    "疼", "痛", "生气", "难过", "孤独", "想死",
)


def _user_is_struggling(text: str) -> bool:
    """对方是不是正在难受：先接住他，别翻自己的旧账。"""
    body = str(text or "").strip()
    if not body:
        return False
    if any(word in body for word in _DISTRESS_WORDS):
        return True
    try:
        signal = detect_emotion(body)
    except Exception:  # noqa: BLE001
        return False
    return bool(signal.negative) and float(signal.intensity) >= 0.35


def _surfaced_ids(limit: int = 200) -> set[int]:
    rows = db.rows(
        "SELECT thought_id FROM thought_surface ORDER BY id DESC LIMIT ?", (int(limit),)
    )
    return {int(r["thought_id"]) for r in rows}


def _last_surface_epoch(user_id: int = 0) -> int:
    """这个人（含共用 uid=0）最近一次提念头的时间。

    按用户隔离：多人共用一个小鲸时，A 提过念头不该让 B 干等 6 小时（旧实现是全局的）。
    """
    row = db.one(
        "SELECT ts FROM thought_surface WHERE user_id = ? OR user_id = 0"
        " ORDER BY id DESC LIMIT 1",
        (int(user_id or 0),),
    )
    return _epoch(row["ts"]) if row else 0


def _surface_cooling(moment: int, user_id: int = 0) -> bool:
    """间隔：同一个人两次提念头之间至少隔 6 小时，别一上线就翻旧账。"""
    try:
        last = _last_surface_epoch(int(user_id or 0))
    except Exception:  # noqa: BLE001
        return False
    return bool(last) and moment - last < _SURFACE_GAP_HOURS * 3600


def _surface_candidates(user_id: int, moment: int,
                        max_age_hours: int) -> dict[str, Any] | None:
    """扫一遍库里还没提过、且还在年龄窗里的念头，返回最新的一条（没有就 None）。

    只负责"哪条能端出去"：未提过 → 年龄窗 → 必须有 grounded 出处。
    时段/对方情绪/全局间隔这些门控留在 pick_surface / pick_for_proactive 里，
    因为"回复时顺口提"和"主动开口提"对这几条的要求不一样。

    返回：id / content / first_seen / age_hours / mode / score / fallback。
    """
    try:
        surfaced = _surfaced_ids()
    except Exception:  # noqa: BLE001
        surfaced = set()
    try:
        rows = db.rows(
            "SELECT id, content, first_seen FROM memories"
            " WHERE kind = ? AND (user_id = ? OR user_id = 0) ORDER BY id DESC LIMIT 20",
            (THOUGHT_KIND, int(user_id or 0)),
        )
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        mid = int(row["id"])
        if mid in surfaced:
            continue
        try:
            item = memengine.store().get_item(mid)
        except Exception:  # noqa: BLE001
            item = None
        grounded = (item.meta or {}).get("grounded") if item is not None else None
        if not grounded:
            continue  # 旧版本/来路不明的念头一律不端出去
        created = _epoch(row["first_seen"])
        if not created:
            continue
        age_hours = (moment - created) / 3600.0
        if age_hours < 0 or age_hours > max_age_hours:
            continue
        return {
            "id": mid,
            "content": str(row["content"]),
            "first_seen": str(row["first_seen"]),
            "age_hours": round(age_hours, 1),
            "mode": str(grounded.get("mode") or "clear"),
            "score": float(grounded.get("score") or 0.0),
            "fallback": bool(grounded.get("fallback")),
        }
    return None


def pick_surface(user_id: int = 0, *, now_ts: int | None = None,
                 text: str = "") -> dict[str, Any] | None:
    """挑一条"第二天值得顺口提"的念头；没有就返回 None。

    门控顺序：时段 → 对方情绪 → 全局间隔 → 未提过 → 年龄窗。
    """
    moment = int(now_ts if now_ts is not None else time.time())
    hour = _hour(moment)
    if not (_SURFACE_START_HOUR <= hour < _SURFACE_END_HOUR):
        return None
    if _user_is_struggling(text):
        return None
    if _surface_cooling(moment, int(user_id or 0)):
        return None
    return _surface_candidates(int(user_id or 0), moment, _SURFACE_MAX_AGE_HOURS)


def pick_for_proactive(user_id: int = 0, *, now_ts: int | None = None,
                       max_age_hours: int = _SURFACE_MAX_AGE_HOURS,
                       text: str = "") -> dict[str, Any] | None:
    """她主动开口时，挑一条能带出去的念头；没有就返回 None。

    跟 pick_surface 同一套"哪条能端出去"的判断，只放开一处：
    - **不看 07–23 时段**：夜里要不要静默由主动消息的闸门（proactive）管，这里不放夜禁。
    对方情绪仍然要看：他最后一句如果在难受，先接住他，别翻自己的旧账
    （调用方把他最后一句话传进 ``text``；不传就当没情绪可判）。
    仍然守：同一人 6 小时间隔 → 未提过 → 年龄窗 → 必须有 grounded。
    """
    moment = int(now_ts if now_ts is not None else time.time())
    if _user_is_struggling(text):
        return None
    if _surface_cooling(moment, int(user_id or 0)):
        return None
    try:
        age = int(max_age_hours)
    except (TypeError, ValueError):
        age = _SURFACE_MAX_AGE_HOURS
    return _surface_candidates(int(user_id or 0), moment, age)


def surface_hint(item: dict[str, Any] | None) -> str:
    """把"要提的念头"翻成一句 prompt 提示；按有据级别换说法。"""
    if not item:
        return ""
    text = " ".join(str(item.get("content") or "").split())[:90]
    if not text:
        return ""
    mode = str(item.get("mode") or "clear")
    if mode == "dream":
        return (
            f"你昨晚半梦半醒，只记得一个模糊的影子：{text}\n"
            "如果这会儿气氛合适，可以当回忆碎片提一句；说得不确定、像在做梦，"
            "别当成确定发生过的事。对方正在难受/说正事就先不提。"
        )
    if mode == "fuzzy":
        return (
            f"你记得不太清，只记得大概：{text}\n"
            "如果气氛合适，可以提一句，但带上「好像」「记不清」这种不确定；"
            "别说得像板上钉钉。对方正在难受/说正事就先不提。"
        )
    return (
        f"你昨晚一个人时想过：{text}\n"
        "如果这会儿气氛合适，可以顺口提一句；不用交代是什么时候想的，也别生硬复述，一句就够。"
        "如果对方正在难受/说正事，就先不提。"
    )


def _line_fragment(text: Any) -> str:
    """把念头正文洗成能直接接在模板后面的片段。

    剃掉开头的"又想起/刚刚/昨晚"这类交代（她自己知道是什么时候想的）
    与首尾标点，超 60 字截断；洗空了返回 ""。
    """
    body = " ".join(str(text or "").split()).strip()
    if not body:
        return ""
    for _ in range(3):  # 交代词可能叠着："刚刚又想起…"
        cleaned = _LINE_LEAD_RE.sub("", body, count=1).strip()
        if cleaned == body:
            break
        body = cleaned
    body = body.strip(_LINE_TRIM_CHARS)
    return body[:_LINE_FRAGMENT_MAX].strip(_LINE_TRIM_CHARS)


def proactive_line(item: dict[str, Any] | None) -> str:
    """把一条念头变成她主动发出的第一句（确定性模板，不调模型）。

    只搬念头正文，不添新事实/新时间/新数字；总长封顶 90 字。
    空内容或出错一律返回 ""，由调用方兜（比如干脆这次不带念头）。
    """
    try:
        if not item:
            return ""
        frag = _line_fragment(item.get("content") or item.get("text") or "")
        if not frag:
            return ""
        mode = str(item.get("mode") or "clear")
        if mode == "dream":
            # 梦只留影子，接一句"昨晚好像梦到"，不加句号（契约模板如此）
            line = f"昨晚好像梦到一点东西——{frag}"
        elif mode == "fuzzy":
            line = f"对了，好像{frag}"
        else:  # clear / fallback / 其它：当正常回忆说
            line = f"对了，{frag}"
        if mode != "dream" and not line.endswith(_LINE_STOP_CHARS):
            line += "。"
        return line[:_LINE_TOTAL_MAX]
    except Exception:  # noqa: BLE001
        return ""


def speak_prompt(item: dict[str, Any] | None, *, persona_name: str = "") -> tuple[str, str]:
    """她主动开口时用：把一条念头交给模型，说成一条她自己发的消息。

    返回 (system, user)；念头正文为空时返回 ("", "")，调用方兜
    （比如直接用 proactive_line 的模板句）。
    """
    try:
        body = " ".join(str((item or {}).get("content") or "").split())
        if not body:
            return "", ""
        mode = str((item or {}).get("mode") or "clear")
        name = str(persona_name or "").strip() or "小涵"
        system = (
            f"你是{name}。这会儿没人跟你说话，是你自己想找他——这不是回复，"
            "是你主动发过去的一条消息。\n"
            "把此刻心里浮起的那件事，用你自己的口吻说成一条消息：一两句，总共不超过 40 字。\n"
            "口语，像随手打字：不要旁白、不要括号动作、不要解释你为什么想起来，"
            "也不要写「我想起一件事」这种交代。\n"
            "**别提你脑子里存着东西这件事**：不要说记·忆、记·录、系·统，"
            "也别把它叫成念头。\n"
            "级别 dream 或 fuzzy：说得不确定，用「好像」「记不清」「梦」这种口气；"
            "级别 clear：当正常回忆说。\n"
            "不要编新的人、新的时间、新的数字；想不起来的细节就直接跳过。\n"
            "只输出你要发出去的那句话。"
        )
        label = {
            "dream": "dream（只有影子，必须说得不确定）",
            "fuzzy": "fuzzy（只记得大概，带一点不确定）",
            "clear": "clear（记得清，当正常回忆说）",
            "fallback": "fallback（照着原话复述，别添油加醋）",
        }.get(mode, f"{mode}（按普通回忆处理）")
        user = (
            f"【此刻心里浮起的】\n{body}\n\n"
            f"【级别】{label}\n\n"
            "只输出要发出去的话。"
        )
        return system, user
    except Exception:  # noqa: BLE001
        return "", ""


def mark_surface(thought_id: int, user_id: int = 0, chat_type: str = "",
                 ts: str | None = None) -> bool:
    """登记"这条念头已经跟他提过"（同一 id 只会成功一次）。

    ``ts`` 只在测试/补发时用：跟这次决策用的时刻对齐，6h 间隔才不会跟真实时钟打架。
    """
    try:
        # 唯一索引是 thought_id 单列：先查再插，撞了必须返回 False。
        # 以前用 INSERT OR IGNORE 且无条件 return True，调用方以为"已经跟她提过"
        # 照扣额度，念头却没真的浮现（docs/23 §3.1 第 3 条）。
        if db.one("SELECT 1 FROM thought_surface WHERE thought_id = ?", (int(thought_id),)) is not None:
            return False
        db.execute(
            "INSERT INTO thought_surface (thought_id, user_id, chat_type, ts)"
            " VALUES (?, ?, ?, ?)",
            (
                int(thought_id),
                int(user_id or 0),
                str(chat_type or ""),
                ts or datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def surface_stats() -> dict[str, Any]:
    row = db.one("SELECT COUNT(*) c FROM thought_surface")
    return {"surfaced": int(row["c"]) if row else 0}


def _iso(ts: int) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


def recent(user_id: int = 0, limit: int = 5) -> list[dict[str, Any]]:
    """她最近想过的念头（面板用；只读，带"是否有据/是否已经提过"）。"""
    uid = str(int(user_id or 0))
    try:
        items = memengine.store().list_items(
            kinds=[THOUGHT_KIND], limit=200, order_by="created_at"
        )
    except Exception:  # noqa: BLE001
        return []
    picked = [it for it in items if str(it.user_id) in (uid, "0")][: int(limit)]
    ids = [int(it.id or 0) for it in picked]
    surfaced: set[int] = set()
    if ids:
        marks = ",".join("?" for _ in ids)
        try:
            surfaced = {
                int(r["thought_id"])
                for r in db.rows(
                    f"SELECT thought_id FROM thought_surface WHERE thought_id IN ({marks})",
                    tuple(ids),
                )
            }
        except Exception:  # noqa: BLE001
            surfaced = set()
    out: list[dict[str, Any]] = []
    for item in picked:
        mid = int(item.id or 0)
        meta = dict(item.meta or {})
        out.append(
            {
                "id": mid,
                "content": str(item.text),
                "first_seen": _iso(int(item.created_at or 0)),
                "surfaced": mid in surfaced,
                "grounded": meta.get("grounded") or {},
            }
        )
    return out
