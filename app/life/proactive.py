"""时间机制：她会等你，也会烦。

四个作用：
1. 一段时间没说话，她会主动找你（只在高关系阶段）。
2. 她发过消息你还不回，她会问"在干嘛，为什么不理我"。
3. 一直被晾着，厌恶值上升，进入生气支线。
4. **她主动开口时，把昨晚自己想的念头带出去**（有据念头，见 app/life/thoughts.py）。

三道闸门焊在 `gate()` 里，任何按人主动外发都必须先过：
- **夜间静默**：默认 23:00–08:00 一条主动消息都不发（`proactive_quiet_start` / `proactive_quiet_end`）。
- **免打扰**：他一句"在开会 / 别吵我 / 睡了"就进免打扰，到期前不主动；说"忙完了"才解除。
- **配额**：冷却 + 每人每天上限（默认 6 条）+ 带念头上限（默认 2 条）+ 连发没人接就闭嘴。
另有身体闸门：日程说她睡着时不许主动（docs/51 阶段一"把身体写进所有出口"）。

主动消息的优先级：**有据念头 > 恋人暗示 > 锚点寒暄**（有念头就先说念头，别拿"在干嘛"占掉机会）。
﻿﻿‌​‌﻿‍‌​‍‌​​﻿​‍﻿‍‌‌﻿‍﻿‍‍‍‌﻿﻿​‌‌‍﻿"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any

from app.core import db
from app.memory import anchors as anchors_mod
from app.memory import store
from app.relation import session as session_mod

# 各阶段"等她主动"的分钟数（5 = bond/恋人之后，黏得更早些；0 = 观察者低压主动）
IDLE_MINUTES = {5: 30, 4: 40, 3: 90, 2: 240, 1: 480, 0: 360}

_NAG = ["你在干嘛。", "为什么不理我。"]
_ANGER = ["你是一点都不想回我。", "算了，你忙。"]

# 夜间静默默认 23:00–08:00；改这两个 kv 就能调
_QUIET_START_KEY = "proactive_quiet_start"
_QUIET_END_KEY = "proactive_quiet_end"
_DEFAULT_QUIET_START = 23
_DEFAULT_QUIET_END = 8

# 主动消息的两种来源（配额要一起算）
PROACTIVE_SOURCES = ("proactive", "proactive_thought")


def _now() -> datetime:
    return datetime.now().astimezone()


def _ts_of(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.astimezone()
    except (ValueError, TypeError):
        return None


def _last_user_ts() -> datetime | None:
    r = db.one("SELECT ts FROM events WHERE role = 'user' ORDER BY id DESC LIMIT 1")
    return _ts_of(str(r["ts"])) if r else None


def _last_any_ts() -> datetime | None:
    r = db.one("SELECT ts FROM events ORDER BY id DESC LIMIT 1")
    return _ts_of(str(r["ts"])) if r else None


def _append(lines: list[str], source: str = "proactive") -> list[dict[str, Any]]:
    db.kv_set("proactive_last_ts", _now().isoformat(timespec="seconds"))
    out = []
    for line in lines:
        eid = store.add_event("her", line, source=source)
        out.append({"id": eid, "text": line})
    return out


def _decay_aversion(now: datetime) -> None:
    """三天不联系，情绪自己会淡一点。这是人的记性，不是原谅。"""
    last = _last_any_ts() or now
    idle_days = (now - last).total_seconds() / 86400
    if idle_days < 3:
        return
    key = f"aversion_decay:{now.date().isoformat()}"
    if db.kv_get(key) == "done":
        return
    delta = -min(10.0, (idle_days - 3) * 2.5)
    session_mod.adjust_aversion(delta)
    db.kv_set(key, "done")


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(db.kv_get(key, "") or default)
    except ValueError:
        return default


def enabled() -> bool:
    return db.kv_get("proactive_enabled", "1") == "1"


def cooldown_min() -> int:
    return max(1, _cfg_int("proactive_cooldown_min", 20))


def daily_max() -> int:
    """0 = 不限；其余为每人每天最多几条主动消息。"""
    return max(0, _cfg_int("proactive_daily_max", 6))


def thought_daily_max() -> int:
    """每人每天最多把几条"夜里想的念头"主动带出去。0 = 不许带。"""
    return max(0, _cfg_int("proactive_thought_max", 2))


def thought_carry_enabled() -> bool:
    """念头主动带出总开关（环境变量优先，方便测试/临时关）。"""
    if os.environ.get("AI_LIFE_PROACTIVE_THOUGHT", "1") == "0":
        return False
    return db.kv_get("proactive_thought_enabled", "1") != "0"


def today_count() -> int:
    """今天她主动说了几句（`proactive` + `proactive_thought` 一起算）。

    只数 `proactive` 会漏掉夜里自己冒出来的念头，配额就会算少、超额打扰——
    `PROACTIVE_SOURCES` 是这条口径的唯一出处，别再手写字符串。
    """
    today = _now().date().isoformat()
    placeholders = ",".join("?" for _ in PROACTIVE_SOURCES)
    count = 0
    for row in db.rows(
        f"SELECT ts FROM events WHERE source IN ({placeholders}) ORDER BY id DESC LIMIT 200",
        PROACTIVE_SOURCES,
    ):
        ts = str(row["ts"] or "")
        if ts[:10] == today:
            count += 1
        elif ts and ts[:10] < today:
            break
    return count


# ---------------------------------------------------------------- 夜间静默


def quiet_hours() -> tuple[int, int]:
    """夜间静默区间（起, 止）；跨零点也支持。"""
    start = _cfg_int(_QUIET_START_KEY, _DEFAULT_QUIET_START) % 24
    end = _cfg_int(_QUIET_END_KEY, _DEFAULT_QUIET_END) % 24
    return start, end


def in_quiet_hours(now: datetime | None = None) -> bool:
    """现在是不是静默时段。23–08 这种跨零点的也算。"""
    start, end = quiet_hours()
    if start == end:
        return False
    hour = int((now or _now()).hour)
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ---------------------------------------------------------------- 免打扰


_DND_UNTIL = "proactive_dnd_until:{}"
_DND_REASON = "proactive_dnd_reason:{}"

# 他说"要睡了" → 免打扰到早上静默结束（白天说困 → 90 分钟）；
# "别吵" → 4 小时；在忙 → 2 小时；"不聊了/先这样" → 30 分钟。
# 注意别误伤问句：「你在忙吗」「你睡了吗」「你困了吗」是他在关心她，不是他要去忙/睡。
_DND_SLEEP = re.compile(r"(先睡了|去睡了|要睡了|我睡了|我先睡|睡了晚安|我困了|有点困|困死|困得|"
                        r"睁不开眼|去睡觉|睡觉去)")
_DND_NAP = re.compile(r"(眯一会儿|眯一会|睡一会儿|睡一会|躺一会儿|躺一会|打个盹)")
_DND_QUIET = re.compile(r"(别吵|别打扰|别烦|别找我|先别找|不要发消息|别发消息|别发信息|"
                        r"先别发|别发我|安静点|消停)")
_DND_SHORT = re.compile(r"(不聊了|先这样|我下了|我下线|改天聊|晚点聊|回头聊)")
_DND_BUSY = re.compile(r"(我在开会|在开会|开会呢|开个会|马上开会|要开会|有个会|我上课|在上课|要上课|"
                       r"面试|见客户|赶工|赶项目|要上线|我这边忙|我在忙|我忙着|忙死了|忙疯了|"
                       r"加班|出差|应酬|在开车|我在路上)")
# 解除：他自己说忙完了 / 回来了（"好了"太容易误伤，不收）
_DND_CLEAR = re.compile(r"(忙完了|忙好了|我回来了|我到家了|下班了|我下班了|闲下来了|有空了|"
                        r"你人呢|在吗|在不在)")


def _minutes_until_morning(now: datetime) -> int:
    """离"早上静默结束"还有多少分钟（默认到 08:00）。"""
    end = quiet_hours()[1] % 24
    target = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds() // 60))


def detect_dnd(text: str, now: datetime | None = None) -> tuple[int, str] | None:
    """读他这句话是不是在要求安静。返回 (分钟, 理由)；0 分钟 = 解除。没命中返回 None。

    这是"免打扰"的唯一入口：主链每收到他一句话就调一次。
    时段影响时长：夜里说"困了/要睡了"→ 到早上；白天说 → 90 分钟（他可能只是午睡）。
    """
    body = str(text or "").strip()
    if not body:
        return None
    if _DND_CLEAR.search(body):
        return (0, "他说他忙完了")
    moment = now or _now()
    night = in_quiet_hours(moment) or int(moment.hour) >= 21 or int(moment.hour) < 6
    if _DND_SLEEP.search(body):
        return (_minutes_until_morning(moment) if night else 90, "他说要睡了")
    if _DND_NAP.search(body):
        return (60, "他说要眯一会儿")
    if _DND_QUIET.search(body):
        return (240, "他说别吵他")
    if _DND_SHORT.search(body):
        return (30, "他说先不聊了")
    if _DND_BUSY.search(body):
        return (120, "他说在忙")
    return None


def set_dnd(user_id: int, minutes: int, reason: str = "",
            *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or _now()
    minutes = max(1, int(minutes))
    until = moment + timedelta(minutes=minutes)
    db.kv_set(_DND_UNTIL.format(int(user_id)), until.isoformat(timespec="seconds"))
    db.kv_set(_DND_REASON.format(int(user_id)), str(reason or ""))
    return {"until": until.isoformat(timespec="seconds"), "minutes": minutes,
            "reason": str(reason or "")}


def clear_dnd(user_id: int) -> None:
    db.kv_set(_DND_UNTIL.format(int(user_id)), "")
    db.kv_set(_DND_REASON.format(int(user_id)), "")


def dnd_status(user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or _now()
    raw = db.kv_get(_DND_UNTIL.format(int(user_id)), "")
    reason = db.kv_get(_DND_REASON.format(int(user_id)), "")
    if not raw:
        return {"active": False, "until": "", "reason": "", "left_min": 0}
    until = _ts_of(raw)
    if until is None or until <= moment:
        clear_dnd(user_id)
        return {"active": False, "until": "", "reason": "", "left_min": 0}
    return {
        "active": True,
        "until": until.isoformat(timespec="seconds"),
        "reason": reason,
        "left_min": int((until - moment).total_seconds() // 60) + 1,
    }


def apply_dnd(user_id: int, detected: tuple[int, str] | None,
              *, now: datetime | None = None) -> dict[str, Any]:
    """把 detect_dnd 的结果落盘（0 分钟 = 解除）。返回最新状态。"""
    moment = now or _now()
    if not detected:
        return dnd_status(int(user_id), now=moment)
    minutes, reason = detected
    if int(minutes) <= 0:
        clear_dnd(int(user_id))
    else:
        set_dnd(int(user_id), int(minutes), str(reason), now=moment)
    return dnd_status(int(user_id), now=moment)


# ---------------------------------------------------------------- 身体闸门


def _sleeping_at(persona, moment: datetime) -> bool:
    """日程说她"那一刻"睡着没有（含开发者覆盖）。读失败就当醒着，别把主动全堵死。

    不调 clock.get_state：那个函数按真实时钟算还会写库，主动闸门要能按传入时刻判（测试/补发）。
    """
    try:
        if db.kv_get("dev_awake", "0") == "1":
            return False
        slot = persona.activity_at(int(moment.hour), int(moment.minute))
        return bool(slot.get("sleeping", False))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 按用户主动（QQ 私聊）


def _ukey(kind: str, user_id: int) -> str:
    return f"proactive_{kind}:{int(user_id)}"


def _thought_key(user_id: int, now: datetime) -> str:
    return f"proactive_thought_count:{int(user_id)}:{now.date().isoformat()}"


def _user_today_thoughts(user_id: int, *, now: datetime | None = None) -> int:
    try:
        return int(db.kv_get(_thought_key(user_id, now or _now()), "0") or 0)
    except ValueError:
        return 0


def _bump_thought_count(user_id: int, *, now: datetime | None = None) -> int:
    moment = now or _now()
    n = _user_today_thoughts(user_id, now=moment) + 1
    db.kv_set(_thought_key(user_id, moment), str(n))
    return n


def _user_last_event(user_id: int, chat_type: str = "private") -> tuple[datetime | None, int]:
    """他最后一次说话的时刻与事件 id（不是她自己的，否则主动一次就把窗口顶掉）。"""
    row = db.one(
        "SELECT id, ts FROM events WHERE user_id = ? AND role = 'user'"
        " AND (chat_type = ? OR chat_type = '') ORDER BY id DESC LIMIT 1",
        (int(user_id), str(chat_type)),
    )
    if not row:
        return None, 0
    return _ts_of(str(row["ts"])), int(row["id"])


def _user_last_ts(user_id: int, chat_type: str = "private") -> datetime | None:
    return _user_last_event(user_id, chat_type)[0]


def _last_user_text(user_id: int, chat_type: str = "private") -> str:
    """他最后一句话的原文（用来判断他是不是正在难受：难受就先别翻自己的旧账）。"""
    row = db.one(
        "SELECT text FROM events WHERE user_id = ? AND role = 'user'"
        " AND (chat_type = ? OR chat_type = '') ORDER BY id DESC LIMIT 1",
        (int(user_id), str(chat_type)),
    )
    return str(row["text"] or "") if row else ""


_DAY_TYPE_WORDS = {
    "workday": "工作日",
    "restday": "休息日",
    "holiday": "节假日",
    "travel": "出差/旅行",
    "special": "特殊的一天",
}


def _day_state(moment: datetime) -> dict[str, Any] | None:
    """她今天正在干嘛（human_life 语料接管；开关关/没语料/正在睡 → None）。

    这是主动开口的**第二优先话题**：没有"有据念头"时，拿她今天的日常当由头。
    """
    try:
        from app.life import human_life as human_life_mod

        if not human_life_mod.enabled():
            return None
        hit = human_life_mod.resolve(moment)
        if not hit or hit.get("sleeping"):
            return None
        phrase = str(hit.get("phrase") or hit.get("activity") or "").strip()
        if not phrase:
            return None
        energy = float(hit.get("energy") or 0.0)
        tail = ""
        if energy <= 30:
            tail = "，有点撑不住"
        elif energy >= 60:
            tail = "，还行"
        return {
            "phrase": phrase,
            "label": str(hit.get("label") or ""),
            "day_type": str(hit.get("day_type") or "workday"),
            "energy": energy,
            "tail": tail,
            "source": str(hit.get("source") or ""),
        }
    except Exception:  # noqa: BLE001 - 没有语料就退回寒暄
        return None


def _user_today_count(user_id: int, *, now: datetime | None = None) -> int:
    today = (now or _now()).date().isoformat()
    row = db.one(
        "SELECT COUNT(*) c FROM events WHERE user_id = ? AND ts >= ?"
        " AND source IN ('proactive','proactive_thought')",
        (int(user_id), today),
    )
    return int(row["c"] or 0) if row else 0


def _unanswered_proactive(user_id: int) -> int:
    """她连着发了几条主动消息、中间他一句话没说（再发就是骚扰）。"""
    rows = db.rows(
        "SELECT role, source FROM events WHERE user_id = ? AND chat_type = 'private'"
        " ORDER BY id DESC LIMIT 12",
        (int(user_id),),
    )
    n = 0
    for r in rows:
        if str(r["role"]) == "user":
            break
        if str(r["source"]) in PROACTIVE_SOURCES:
            n += 1
    return n


def _sleeping_gate_message() -> str:
    return "她这个点睡着了"


def gate(rt, persona, user_id: int, chat_type: str = "private",
         *, now: datetime | None = None) -> tuple[bool, str]:
    """三闸门 + 身体闸门 + 冷却/配额。返回 (能不能主动, 原因)。顺序即优先级。"""
    uid = int(user_id or 0)
    if uid <= 0 or str(chat_type) != "private":
        return False, "只对私聊主动"
    from app.relation import engine, users

    if users.is_blocked(uid):
        return False, "他已经被拉黑"
    if not enabled():
        return False, "主动开关关着"
    moment = now or _now()
    if in_quiet_hours(moment):
        start, end = quiet_hours()
        return False, f"夜间静默（{start:02d}:00–{end:02d}:00）"
    dnd = dnd_status(uid, now=moment)
    # 面板那条线只有一个"全局免打扰"（uid=0）：面板上点一下，QQ 私聊也不许打扰他
    global_dnd = dnd_status(0, now=moment) if uid != 0 else {"active": False}
    active = dnd if dnd["active"] else (global_dnd if global_dnd.get("active") else None)
    if active:
        return False, (f"免打扰（{active.get('reason') or '他要求安静'}，"
                       f"还剩 {active.get('left_min')} 分钟）")
    if _sleeping_at(persona, moment):
        return False, _sleeping_gate_message()
    last, last_id = _user_last_event(uid, "private")
    if last is None:
        return False, "还没聊过"
    rel = engine.relation_for(uid)
    stage = persona.stage_index(float(rel["affinity"]))
    threshold = IDLE_MINUTES.get(stage)
    if threshold is None:
        return False, "关系还没到会主动的阶段"
    idle_min = (moment - last).total_seconds() / 60
    if idle_min < threshold:
        return False, f"才安静了 {int(idle_min)} 分钟（这个阶段要 {threshold} 分钟）"
    last_pro = db.kv_get(_ukey("last_ts", uid), "")
    if last_pro:
        try:
            if (moment - datetime.fromisoformat(last_pro)).total_seconds() < cooldown_min() * 60:
                return False, "冷却中"
        except ValueError:
            pass
    window = f"{uid}:{last_id}"
    if db.kv_get(_ukey("window", uid), "") == window:
        return False, "这个静默窗口已经主动过一次"
    cap = daily_max()
    used = _user_today_count(uid, now=moment)
    if cap > 0 and used >= cap:
        return False, f"今天的主动次数用完了（{used}/{cap}）"
    streak = _unanswered_proactive(uid)
    if streak >= 2:
        return False, f"连着发了 {streak} 条还没人接"
    return True, "可以主动"


def _mark_window(user_id: int, last_id: int) -> None:
    """记下"这条用户消息的窗口已经主动过一次"（用事件 id，秒级时间戳会撞车）。"""
    db.kv_set(_ukey("window", int(user_id)), f"{int(user_id)}:{int(last_id)}")


def _commit_thought(user_id: int, item: dict[str, Any],
                    *, now: datetime | None = None) -> bool:
    """这条念头已经说出口：落浮现台账 + 记当天额度。台账写失败就不扣额度。"""
    if not item:
        return False
    moment = now or _now()
    ok = False
    try:
        from app.life import thoughts as thought_mod

        ok = bool(thought_mod.mark_surface(
            int(item.get("id") or 0), int(user_id), "private",
            ts=moment.isoformat(timespec="seconds"),
        ))
    except Exception:  # noqa: BLE001 - 台账失败最多以后再说一次
        ok = False
    if ok:
        _bump_thought_count(int(user_id), now=moment)
    return ok


def decide(rt, persona, user_id: int, chat_type: str = "private",
           *, now: datetime | None = None) -> dict[str, Any]:
    """她这次该不该主动开口、带什么。返回 {ok, reason, kind, item, stage}。"""
    uid = int(user_id or 0)
    ok, reason = gate(rt, persona, uid, chat_type, now=now)
    moment = now or _now()
    out: dict[str, Any] = {"ok": bool(ok), "reason": reason, "kind": "none",
                           "item": None, "user_id": uid, "stage": 0}
    if not ok:
        return out
    from app.relation import engine

    stage = persona.stage_index(float(engine.relation_for(uid)["affinity"]))
    out["stage"] = stage
    if thought_carry_enabled():
        cap = thought_daily_max()
        if cap > 0 and _user_today_thoughts(uid, now=moment) < cap:
            item = None
            try:
                from app.life import thoughts as thought_mod

                item = thought_mod.pick_for_proactive(
                    uid, now_ts=int(moment.timestamp()), text=_last_user_text(uid)
                )
            except Exception:  # noqa: BLE001 - 挑不到就退回寒暄
                item = None
            if item is not None:
                out["kind"] = "thought"
                out["item"] = item
                return out
    # 没有旧事可说 → 用她今天的生活当由头（"今天在改稿，脑子有点木"）
    day_item = _day_state(moment)
    if day_item is not None:
        out["kind"] = "day"
        out["item"] = day_item
        return out
    from app.relation import users

    if users.lover_of(uid) is not None and proactive_intimate():
        out["kind"] = "intimate"
        return out
    out["kind"] = "anchor"
    return out


def _day_line(item: dict[str, Any] | None) -> str:
    """确定性模板：把"她今天在干嘛"说成一条主动消息（不添新事实）。"""
    if not item:
        return ""
    phrase = str(item.get("phrase") or "").strip()
    if not phrase:
        return ""
    tail = str(item.get("tail") or "").strip()
    return f"今天{phrase}{tail}。"[:80]


def _fallback_lines(user_id: int, dec: dict[str, Any]) -> list[str]:
    """没有念头可带时的老路：今天的生活 → 恋人暗示 → 锚点寒暄。"""
    stage = int(dec.get("stage") or 0)
    if dec.get("kind") == "day":
        line = _day_line(dec.get("item"))
        if line:
            return [line]
    if dec.get("kind") == "intimate":
        try:
            from app.intimacy import exemplars as iex

            lines = iex.by_kind("metaphor")[:1]
            if lines:
                return [lines[0]]
        except Exception:  # noqa: BLE001
            pass
    scene = "撒娇" if stage >= 3 else "关心"
    lines = anchors_mod.pick_dynamic(scene, max(stage, 0), 2)
    if not lines:
        lines = ["在干嘛。" if stage >= 3 else "今天还好吗。"]
    return lines[:2]


def _append_for_user(lines: list[str], user_id: int, chat_type: str = "private",
                     source: str = "proactive",
                     *, now: datetime | None = None) -> list[str]:
    """写进事件表。``now`` 传了就按那一刻记（配额/窗口/冷却同一口径）。"""
    moment = now or _now()
    stamp = moment.isoformat(timespec="seconds")
    for line in lines:
        store.add_event("her", line, source=source, user_id=int(user_id),
                        chat_type=chat_type, ts=stamp)
    db.kv_set(_ukey("last_ts", user_id), stamp)
    return lines


def tick_user(rt, persona, user_id: int, chat_type: str = "private",
              *, now: datetime | None = None) -> list[str]:
    """按"这个人"推进主动消息（只私聊）。三道闸门都在 `gate()` 里。

    优先把昨晚的念头带出去（确定性模板，不调模型）；没有再退回暗示/寒暄。
    真实外发走 `prepare_tick` + `commit_tick`（发成功才记账）。
    """
    dec = decide(rt, persona, user_id, chat_type, now=now)
    if not dec["ok"]:
        return []
    uid = int(user_id or 0)
    moment = now or _now()
    _last, last_id = _user_last_event(uid, "private")
    if dec["kind"] == "thought":
        try:
            from app.life import thoughts as thought_mod

            line = thought_mod.proactive_line(dec["item"])
        except Exception:  # noqa: BLE001
            line = ""
        if line:
            _commit_thought(uid, dec["item"], now=moment)
            _mark_window(uid, last_id)
            return _append_for_user([line], uid, "private",
                                    source="proactive_thought", now=moment)
    lines = _fallback_lines(uid, dec)
    if not lines:
        return []
    _mark_window(uid, last_id)
    return _append_for_user(lines, uid, "private", now=moment)


# 主动消息里不许出现的元信息词（模型偶尔会写"我想起一条记忆"）
_SPEAK_BAN = ("记忆", "记录", "系统", "念头", "数据库", "提示词", "模型", "训练")


def _clean_speak(text: str) -> str:
    """把模型输出洗成一条能直接发出去的话；不合格返回""。"""
    body = " ".join(str(text or "").replace("\n", " ").split())
    body = body.strip("「」“”\"'` ")
    if not body:
        return ""
    for bad in _SPEAK_BAN:
        if bad in body:
            return ""
    if len(body) > 60:
        body = body[:60].rstrip("，,、 ")
    return body if len(body) >= 3 else ""


async def _speak_thought(rt, persona, item: dict[str, Any]) -> str:
    """让她的模型把念头说成一条主动消息；失败返回""（调用方退回模板）。"""
    try:
        from app.life import thoughts as thought_mod

        system, user = thought_mod.speak_prompt(
            item, persona_name=str(getattr(persona, "name", "") or "小涵")
        )
        reply = await rt.llm.chat(system, [{"role": "user", "content": user}], max_tokens=120)
        return _clean_speak(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 - 主动开口失败就用模板，不许崩
        return ""


async def _speak_day(rt, persona, item: dict[str, Any]) -> str:
    """让她用自己的口吻说"今天在干嘛"；失败返回""（调用方退回模板）。"""
    try:
        item = item or {}
        phrase = str(item.get("phrase") or "").strip()
        if not phrase:
            return ""
        name = str(getattr(persona, "name", "") or "小涵")
        day_word = _DAY_TYPE_WORDS.get(str(item.get("day_type") or "workday"), "今天")
        system = (
            f"你是{name}。{phrase}{item.get('tail') or ''}。\n"
            "你想主动找他说话（不是回复他）。用一两句说今天正在做的事或感受，总共不超过 40 字。\n"
            "口语、像随手发的；不要解释你为什么说这个，不要提问，不要提「记忆」「记录」「系统」「念头」。\n"
            "不要编具体的客户名、方案名、同事名；想不起来的细节就跳过。\n"
            "只输出你要发出去的那句话。"
        )
        user = f"现在是{day_word}。你正在{phrase}。"
        reply = await rt.llm.chat(system, [{"role": "user", "content": user}], max_tokens=120)
        return _clean_speak(getattr(reply, "text", "") or "")
    except Exception:  # noqa: BLE001 - 主动开口失败就用模板，不许崩
        return ""


async def prepare_tick(rt, persona, user_id: int, chat_type: str = "private",
                       *, now: datetime | None = None) -> dict[str, Any]:
    """算出"这次要发什么、之后要记什么账"，但**先不落库**。

    真实外发（`app/main.py`）是 prepare → 发 QQ → 成功了才 commit：
    没发出去就不扣配额、不记窗口，下一轮还有机会（不会出现"她说了但你没收到"）。
    """
    dec = decide(rt, persona, user_id, chat_type, now=now)
    uid = int(user_id or 0)
    moment = now or _now()
    if not dec["ok"]:
        return {"ok": False, "reason": dec["reason"], "user_id": uid, "kind": "none"}
    _last, last_id = _user_last_event(uid, "private")
    lines: list[str] = []
    item = None
    if dec["kind"] == "day":
        text = await _speak_day(rt, persona, dec["item"])
        if not text:
            text = _day_line(dec["item"])
        text = _clean_speak(text)
        if text:
            lines = [text]
    elif dec["kind"] == "thought":
        text = await _speak_thought(rt, persona, dec["item"])
        if not text:
            try:
                from app.life import thoughts as thought_mod

                text = thought_mod.proactive_line(dec["item"])
            except Exception:  # noqa: BLE001
                text = ""
        text = _clean_speak(text)
        if text:
            lines = [text]
            item = dec["item"]
    if not lines:
        lines = _fallback_lines(uid, dec)
    if not lines:
        return {"ok": False, "reason": "没有可说的", "user_id": uid, "kind": dec["kind"]}
    # 生成期间（模型调用可能几百毫秒）他可能又说话了：窗口变了这次就作废
    if _user_last_event(uid, "private")[1] != last_id:
        return {"ok": False, "reason": "他刚说话了，这次不主动", "user_id": uid,
                "kind": dec["kind"]}
    return {"ok": True, "lines": lines, "item": item,
            "kind": "thought" if item else dec["kind"],
            "user_id": uid, "last_id": last_id, "now": moment}


def commit_tick(prepared: dict[str, Any]) -> list[str]:
    """真的发出去了才记账：念头浮现台账、窗口、事件、冷却时间。"""
    if not prepared or not prepared.get("ok"):
        return []
    uid = int(prepared.get("user_id") or 0)
    moment = prepared.get("now") or _now()
    last_id = int(prepared.get("last_id") or 0)
    item = prepared.get("item")
    lines = [str(x) for x in (prepared.get("lines") or []) if str(x).strip()]
    if not lines:
        return []
    if item:
        _commit_thought(uid, item, now=moment)
    _mark_window(uid, last_id)
    source = "proactive_thought" if item else "proactive"
    return _append_for_user(lines, uid, "private", source=source, now=moment)


async def tick_user_async(rt, persona, user_id: int, chat_type: str = "private",
                          *, now: datetime | None = None) -> list[str]:
    """一步到位版（面板/测试用）：prepare + commit。

    真实外发请分两步调（`prepare_tick` → 发 → `commit_tick`），否则发失败也会记账。
    """
    prepared = await prepare_tick(rt, persona, user_id, chat_type, now=now)
    return commit_tick(prepared)


def proactive_intimate() -> bool:
    return db.kv_get("proactive_intimate", "0") == "1"


def set_proactive_intimate(on: bool) -> None:
    db.kv_set("proactive_intimate", "1" if on else "0")


def settings() -> dict[str, Any]:
    q_start, q_end = quiet_hours()
    return {
        "enabled": enabled(),
        "cooldown_min": cooldown_min(),
        "daily_max": daily_max(),
        "thought_daily_max": thought_daily_max(),
        "thought_carry": thought_carry_enabled(),
        "quiet_start": q_start,
        "quiet_end": q_end,
        "night_silent_now": in_quiet_hours(),
        "dnd": dnd_status(0),
        "idle_minutes": {str(k): v for k, v in IDLE_MINUTES.items()},
        "last_ts": db.kv_get("proactive_last_ts", ""),
        "today_count": today_count(),
        "intimate": proactive_intimate(),
    }


def settings_for(user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """面板/调试：这个人身上的主动状态（三道闸门各自为什么拦）。"""
    moment = now or _now()
    uid = int(user_id or 0)
    dnd = dnd_status(uid, now=moment)
    return {
        "user_id": uid,
        "daily_max": daily_max(),
        "today_count": _user_today_count(uid, now=moment),
        "thought_daily_max": thought_daily_max(),
        "today_thoughts": _user_today_thoughts(uid, now=moment),
        "quiet_start": quiet_hours()[0],
        "quiet_end": quiet_hours()[1],
        "night_silent_now": in_quiet_hours(moment),
        "dnd": dnd,
        "unanswered": _unanswered_proactive(uid),
        "cooldown_min": cooldown_min(),
        "last_ts": db.kv_get(_ukey("last_ts", uid), ""),
    }


def tick(rt, persona) -> list[dict[str, Any]]:
    """每次请求顺手推进时间（面板单用户那条线）。返回本次新增的她的话。"""
    st = session_mod.get(rt.default_name)
    if st.get("status") != "active" or not st.get("name_locked"):
        return []
    if not enabled():
        return []

    now = _now()
    _decay_aversion(now)
    # 夜间静默 / 免打扰 / 身体：面板这条线也不许半夜或他说别吵的时候冒出来
    if in_quiet_hours(now):
        return []
    if dnd_status(0, now=now).get("active"):
        return []
    if _sleeping_at(persona, now):
        return []

    last_user = _last_user_ts()
    if last_user is None:
        return []
    idle_min = (now - last_user).total_seconds() / 60

    from app.relation import engine

    rel = engine.get_relation()
    stage = persona.stage_index(float(rel["affinity"]))
    threshold = IDLE_MINUTES.get(stage)
    if threshold is None or idle_min < threshold:
        return []

    # 冷却：主动消息之间至少隔 cooldown_min 分钟，不要连珠炮
    last_pro = db.kv_get("proactive_last_ts", "")
    if last_pro:
        try:
            if (now - datetime.fromisoformat(last_pro)).total_seconds() < cooldown_min() * 60:
                return []
        except ValueError:
            pass

    window = last_user.isoformat(timespec="seconds")
    sent = db.kv_get("proactive_window", "")

    if sent != window:
        cap = daily_max()
        if cap > 0 and today_count() >= cap:
            return []
        db.kv_set("proactive_window", window)
        db.kv_set("proactive_nag", "")
        scene = "撒娇" if stage >= 3 else "关心"
        lines = anchors_mod.pick_dynamic(scene, max(stage, 0), 2)
        if not lines:
            lines = ["在干嘛。" if stage >= 3 else "今天还好吗。"]
        return _append(lines[:2])

    # 已经主动过一次，还没等到回复
    if db.kv_get("proactive_nag", "") != window and idle_min >= threshold * 2:
        db.kv_set("proactive_nag", window)
        session_mod.adjust_aversion(1.5)
        return _append(_NAG)

    if idle_min >= threshold * 4 and db.kv_get("proactive_angry", "") != window:
        db.kv_set("proactive_angry", window)
        session_mod.adjust_aversion(4.0)
        return _append(_ANGER)

    return []


def pending_messages(after_id: int = 0) -> list[dict[str, Any]]:
    rows = db.rows(
        "SELECT id, text, ts FROM events WHERE role = 'her'"
        " AND source IN ('proactive','proactive_thought') AND id > ? ORDER BY id",
        (after_id,),
    )
    return [{"id": int(r["id"]), "text": str(r["text"]), "ts": str(r["ts"])} for r in rows]
