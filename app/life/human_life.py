"""她的一天：把 data/human_life/_derived 里的作息语料读进运行时（只读接管）。

语料是 lifecorpus 从时间利用调查公报推出来的"推定的一天"，**不是真实个体**：
- ``rhythm.<day_type>.jsonl``：一行一份作息，``segments`` 是按分钟平铺的分段
- ``activity*.jsonl``：一条条活动条目（官方统计 ``activity.jsonl`` + 她自己的一天
  ``activity.user_ad.jsonl``），v2 只统计条数，暂不参与 resolve

她自己的工作日在 v2 起优先：``rhythm.user_ad_workday.jsonl`` 的 ``user_ad_day_a/b/c``
（26 岁广告策划自述）盖过 ``stat_*`` 的公报均值；日型嫌按星期判不准时，可用 kv
``human_life_force_day_type`` 临时覆盖（桌宠/演示用）。

设计约束（docs/52 §六）：
- **默认关闭**：kv ``human_life_enabled`` 默认 "0"，关着时 clock 行为一个字都不变；
- 只读：这里不写库、不动 persona 作息表，用户确认后才谈"写进作息"；
- 异常不外抛：目录没了、行坏了，统统降级成 ``ok=False``，让上层继续跑老逻辑。

缓存：内存里存一份，60 秒内且文件 mtime/大小没变就复用；语料是静态文件，避免每次
``get_state`` 都去读盘。
﻿﻿‌‌‍‌‍‍﻿‌​​‌‌​‍‌‌‍‍‍﻿‍﻿﻿‌﻿‍​​‍‍‌﻿"""

from __future__ import annotations

import json
import os
import pathlib
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core import db
from app.core.paths import DATA_ROOT

# 展示统一用 UTC+08（跟 clock 一个时区），桌宠/机器人念时间都按这个
_TZ = ZoneInfo("Asia/Shanghai")

# 开关：kv 里存 "1"/"0"，默认 "0"（默认关，用户确认后才开）
KV_ENABLED = "human_life_enabled"

# 日型覆盖（给桌宠/演示用，也为"生病日/节假日"调度留口）：
# kv 存 ""（自动按星期判）或下面 5 个值之一
KV_FORCE_DAY_TYPE = "human_life_force_day_type"
DAY_TYPES: tuple[str, ...] = ("workday", "restday", "holiday", "travel", "special")

# 她的工作日 v2：同一个 day_type 内优先选"她自己的一天"（26 岁广告策划自述），
# 其次才是官方统计推定的 stat_*。键是 day_type，值是候选 id 优先级顺序。
PREFERRED_IDS: dict[str, tuple[str, ...]] = {
    "workday": ("user_ad_day_a", "user_ad_day_b", "user_ad_day_c"),
    "special": ("user_sick_day",),
}

# 语料目录：DATA_ROOT 在测试里是临时目录，所以再给一个环境变量出口方便测试
ENV_DIR = "AI_LIFE_HUMAN_LIFE_DIR"
DEFAULT_SUBDIR = ("data", "human_life", "_derived")
# 默认语料目录（打包/开发都跟着 DATA_ROOT 走）
CORPUS_DIR: pathlib.Path = pathlib.Path(DATA_ROOT).joinpath(*DEFAULT_SUBDIR)

# 读盘缓存：60 秒内同一目录、同一文件指纹就直接复用
CACHE_TTL = 60.0
_CACHE: dict[str, Any] = {"dir": "", "sig": (), "at": 0.0, "data": None}

# segment.label（ACTIVITY_TYPES 枚举）→ (中文口语短语, 精力 0-100, 睡没睡)
# 精力不给满：睡觉 10（低）、运动 30（累但要动）、休闲/上班 55 上下
ACTIVITY_MAP: dict[str, tuple[str, int, bool]] = {
    "sleep": ("睡着了", 10, True),
    "meal": ("在吃饭", 45, False),
    "work": ("在上班", 55, False),
    "commute": ("在路上", 35, False),
    "chores": ("在收拾屋子", 40, False),
    "study": ("在看书学东西", 45, False),
    "leisure": ("在放松", 55, False),
    "social": ("在跟人说话", 50, False),
    "screen": ("在刷手机", 40, False),
    "selfcare": ("在洗漱收拾", 45, False),
    "errand": ("在外面办事", 35, False),
    "exercise": ("在运动", 30, False),
    "other": ("在发呆", 50, False),
}

# 认不出的 label 统一按 other 落，别让一行脏数据把整个时刻弄成 None
_FALLBACK = ACTIVITY_MAP["other"]

#: 她自己的说法（比枚举映射更像人；同一个段内按日期固定，不会每刷一次换一句）
ACTIVITY_PHRASES: dict[str, tuple[str, ...]] = {
    "sleep": ("在补觉", "睡着了", "再眯一会儿"),
    "meal": ("在吃饭", "在扒饭", "在弄点吃的"),
    "work": ("在改稿", "在写方案", "在开会", "在等客户反馈", "在赶 PPT"),
    "commute": ("在路上", "在挤地铁", "在等车"),
    "chores": ("在收拾屋子", "在洗衣服", "在收外卖盒"),
    "study": ("在看点东西", "在学新东西"),
    "leisure": ("在摸鱼", "在刷剧", "在放空"),
    "social": ("在跟人吃饭", "在跟朋友聊天"),
    "screen": ("在刷手机", "在看素材", "在看微博找灵感"),
    "selfcare": ("在洗漱", "在收拾自己"),
    "errand": ("在外面办事", "在买东西"),
    "exercise": ("在运动", "在走路"),
    "other": ("在发呆", "在放空"),
}


def _phrase_for(label: str, moment: datetime, start_min: int) -> str:
    """同一段 + 同一天给同一句（按 label/日期/段起点做哈希），避免状态卡每秒乱跳。"""
    options = ACTIVITY_PHRASES.get(label) or ACTIVITY_PHRASES["other"]
    seed = f"{label}:{moment.date().isoformat()}:{int(start_min)}"
    return options[hash(seed) % len(options)]


# ------------------------------------------------------------------ 目录/开关 --


def corpus_dir(dir: Any = None) -> pathlib.Path:
    """语料目录：入参 > 环境变量 AI_LIFE_HUMAN_LIFE_DIR > CORPUS_DIR（DATA_ROOT 下）。"""
    if dir is not None:
        return pathlib.Path(dir)
    env = os.environ.get(ENV_DIR)
    if env:
        return pathlib.Path(env)
    return CORPUS_DIR


def enabled() -> bool:
    """读 kv 开关。库还没初始化/读不到就当关着，绝不抛。"""
    try:
        return db.kv_get(KV_ENABLED, "0") == "1"
    except Exception:
        return False


def set_enabled(flag: bool) -> None:
    """写 kv 开关。写失败也不抛，大不了下次读到的还是旧值。"""
    try:
        db.kv_set(KV_ENABLED, "1" if flag else "0")
    except Exception:
        pass


def reset_cache() -> None:
    """清缓存（测试换目录时用）。"""
    _CACHE.update({"dir": "", "sig": (), "at": 0.0, "data": None})


def force_day_type() -> str:
    """日型覆盖：kv 里是合法值才认；空串/非法/读不到都当"自动"。"""
    try:
        value = db.kv_get(KV_FORCE_DAY_TYPE, "") or ""
    except Exception:
        return ""
    return value if value in DAY_TYPES else ""


def set_force_day_type(value: str) -> None:
    """写日型覆盖："" = 自动按星期判；其余必须是 DAY_TYPES 里的值，否则抛 ValueError。"""
    text = "" if value is None else str(value).strip()
    if text and text not in DAY_TYPES:
        raise ValueError("日型只能是 " + "/".join(DAY_TYPES) + "，或空串表示自动")
    try:
        db.kv_set(KV_FORCE_DAY_TYPE, text)
    except Exception:
        pass


# ---------------------------------------------------------------------- 读盘 --


def _signature(d: pathlib.Path) -> tuple:
    """文件指纹：目录里参与解析的文件名 + mtime_ns + 大小。目录不在就空元组。"""
    sig: list[tuple[str, int, int]] = []
    try:
        if not d.is_dir():
            return ()
        # rhythm.*.jsonl + activity*.jsonl（她自己的 activity.user_ad.jsonl 也算）
        for p in sorted(list(d.glob("rhythm.*.jsonl")) + list(d.glob("activity*.jsonl"))):
            try:
                st = p.stat()
            except OSError:
                continue
            sig.append((p.name, int(st.st_mtime_ns), int(st.st_size)))
    except OSError:
        return ()
    return tuple(sig)


def _read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]:
    """逐行读 jsonl：坏行、非 dict 行都跳过并计数，不抛。返回 (记录, 坏行数)。"""
    records: list[dict] = []
    bad = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    bad += 1
    except OSError:
        return [], 1
    return records, bad


def _to_minute(text: Any) -> int | None:
    """把 "HH:MM" 换成当天第几分钟；"24:00" = 1440。认不出返回 None。"""
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    total = hour * 60 + minute
    if total > 1440:
        return None
    return total


def _clean_segments(raw: Any) -> list[dict]:
    """把一份 rhythm 的 segments 洗成 [{start, end, label}]（分钟制）；坏段丢掉。"""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        start = _to_minute(seg.get("start"))
        end = _to_minute(seg.get("end"))
        label = seg.get("label")
        if start is None or end is None or not isinstance(label, str) or not label:
            continue
        if end <= start:  # 空段/倒挂段没意义
            continue
        out.append({"start": start, "end": end, "label": label})
    return out


def _day_type_of(record: dict, filename: str) -> str:
    """先看记录自带的 day_type，没有就从文件名 rhythm.<day_type>.jsonl 里抠。"""
    dt = record.get("day_type")
    if isinstance(dt, str) and dt:
        return dt
    parts = filename.split(".")
    if len(parts) >= 3:
        return parts[1]
    return "workday"


def _read(d: pathlib.Path) -> dict:
    """真正读盘：目录不存在/全坏都给 ok=False，内存里只留能用的记录。"""
    data: dict[str, Any] = {
        "ok": False,
        "dir": str(d),
        "rhythms": 0,
        "activities": 0,
        "by_day_type": {},
        "loaded_at": None,
        "errors": 0,
        "rhythm_records": [],
        "activity_records": [],
    }
    if not d.is_dir():
        data["errors"] = 1
        return data

    # 1) 作息：每个 day_type 一份，取文件顺序第一条当主档（average 排在前面）
    try:
        rhythm_files = sorted(d.glob("rhythm.*.jsonl"))
    except OSError:
        rhythm_files = []
    for p in rhythm_files:
        records, bad = _read_jsonl(p)
        data["errors"] += bad
        for rec in records:
            segments = _clean_segments(rec.get("segments"))
            if not segments:
                data["errors"] += 1
                continue
            day_type = _day_type_of(rec, p.name)
            data["rhythm_records"].append(
                {
                    "id": str(rec.get("id") or p.stem),
                    "day_type": day_type,
                    "segments": segments,
                }
            )
            data["rhythms"] += 1
            data["by_day_type"][day_type] = int(data["by_day_type"].get(day_type, 0)) + 1

    # 2) 活动条目：v1 只数条数，解析成记录留给下一轮
    #    两个来源都读：官方统计 activity.jsonl + 她自己的一天 activity.user_ad.jsonl
    try:
        activity_files = sorted(d.glob("activity*.jsonl"))
    except OSError:
        activity_files = []
    for act_path in activity_files:
        if not act_path.is_file():
            continue
        records, bad = _read_jsonl(act_path)
        data["errors"] += bad
        for rec in records:
            data["activity_records"].append(rec)
            data["activities"] += 1

    data["ok"] = bool(data["rhythms"] > 0)
    if data["ok"]:
        data["loaded_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return data


def load(dir: Any = None) -> dict:
    """读语料（带 60 秒缓存）。同一目录 + 文件没变就复用上一份；永不抛。"""
    d = corpus_dir(dir)
    try:
        sig = _signature(d)
    except Exception:
        sig = ()
    cached = _CACHE.get("data")
    if (
        isinstance(cached, dict)
        and _CACHE.get("dir") == str(d)
        and _CACHE.get("sig") == sig
        and (time.monotonic() - float(_CACHE.get("at") or 0.0)) < CACHE_TTL
    ):
        return cached
    try:
        data = _read(d)
    except Exception:
        data = {
            "ok": False,
            "dir": str(d),
            "rhythms": 0,
            "activities": 0,
            "by_day_type": {},
            "loaded_at": None,
            "errors": 1,
            "rhythm_records": [],
            "activity_records": [],
        }
    _CACHE.update({"dir": str(d), "sig": sig, "at": time.monotonic(), "data": data})
    return data


def status() -> dict:
    """给接口/面板看的摘要：能不能读、读了几条、开关开没开、有没有日型覆盖。"""
    data = load()
    return {
        "ok": bool(data.get("ok")),
        "dir": data.get("dir"),
        "rhythms": int(data.get("rhythms") or 0),
        "activities": int(data.get("activities") or 0),
        "by_day_type": dict(data.get("by_day_type") or {}),
        "enabled": enabled(),
        "loaded_at": data.get("loaded_at"),
        "errors": int(data.get("errors") or 0),
        "force_day_type": force_day_type(),
    }


# ---------------------------------------------------------------------- 时刻 --


def day_type_for(moment: datetime) -> str:
    """周六周日算休息日，其余算工作日。"""
    return "restday" if moment.weekday() >= 5 else "workday"


def _pick_rhythm(data: dict, day_type: str, moment: datetime | None = None) -> dict | None:
    """按 day_type 挑一份作息。

    - 同一天型内先看 PREFERRED_IDS（她自己的一天：user_ad_day_* / user_sick_day），
      找不到才按文件顺序取第一份（stat_*_average 排最前）；
    - 工作日有多份自述时按**传入日期**轮换（今天 a、明天 b），同一时刻结果稳定；
    - 该日型没货就退回工作日（老的 restday→workday 逻辑不变），再没有就 None。
    """
    target = day_type
    records = [r for r in (data.get("rhythm_records") or []) if r.get("day_type") == day_type]
    if not records and day_type != "workday":
        records = [r for r in (data.get("rhythm_records") or []) if r.get("day_type") == "workday"]
        target = "workday"
    if not records:
        return None
    # 她自己的一天优先；候选项没在语料里就跳过。
    # 工作日有多份自述（a/b/c）时按日期轮换，别让她每天都一模一样。
    by_id = {str(r.get("id") or ""): r for r in records}
    preferred = [pid for pid in PREFERRED_IDS.get(target, ()) if pid in by_id]
    if preferred:
        stamp = moment if moment is not None else now_local()
        return by_id[preferred[stamp.date().toordinal() % len(preferred)]]
    # 文件顺序第一条就是主档，保证同一时刻永远给同一个答案
    return records[0]


def resolve(moment: datetime, day_type: str | None = None) -> dict | None:
    """把某个时刻翻译成"她在干嘛"：拿不到（没语料/落缝里）就 None，永不抛。

    选档顺序：kv 日型覆盖 > 显式 day_type 参数 > 按星期自动判。
    """
    try:
        data = load()
        if not data.get("ok"):
            return None
        kind = force_day_type() or day_type or day_type_for(moment)
        rhythm = _pick_rhythm(data, kind, moment)
        if rhythm is None:
            return None
        minute = int(moment.hour) * 60 + int(moment.minute)
        for seg in rhythm.get("segments") or []:
            if int(seg["start"]) <= minute < int(seg["end"]):
                label = str(seg["label"])
                activity, energy, sleeping = ACTIVITY_MAP.get(label, _FALLBACK)
                return {
                    "activity": activity,
                    "phrase": _phrase_for(label, moment, int(seg["start"])),
                    "energy": float(energy),
                    "sleeping": bool(sleeping),
                    "label": label,
                    "source": str(rhythm.get("id") or ""),
                    "day_type": str(rhythm.get("day_type") or kind),
                    "segment": [
                        f"{int(seg['start']) // 60:02d}:{int(seg['start']) % 60:02d}",
                        f"{int(seg['end']) // 60:02d}:{int(seg['end']) % 60:02d}",
                    ],
                }
        return None
    except Exception:
        return None


# ------------------------------------------------------------------ 展示状态卡 --


def now_local() -> datetime:
    """当前 UTC+08 本地时间（桌宠 / QQ 机器人展示统一走这个）。"""
    return datetime.now(_TZ)


def snapshot(moment: datetime | None = None) -> dict:
    """紧凑状态卡：给桌宠 / QQ 机器人展示"她此刻在干嘛"。

    - 永远能返回：没开开关、没语料、时刻落空，都只是 enabled/ok 为 false、字段给 null，
      绝不抛异常（调用方可以无条件 200）；
    - ``seconds_left``：当前分段还有多少秒结束（到 24:00 封顶，不会是负数）；
    - ``corpus``：语料健康度（几份作息 / 几条活动 / 几条坏行），开关关着也照给。
    """
    now = moment if moment is not None else now_local()
    if now.tzinfo is None:
        # 传进来的 naive 时刻按 UTC+08 补齐，保证 ts 永远带时区
        now = now.replace(tzinfo=_TZ)
    card: dict[str, Any] = {
        "enabled": False,
        "ok": False,
        "ts": now.isoformat(timespec="seconds"),
        "day_type": "workday",
        "day_type_forced": False,
        "activity": None,
        "energy": 0.0,
        "sleeping": False,
        "label": None,
        "source": None,
        "segment": None,
        "seconds_left": 0,
        "corpus": {"rhythms": 0, "activities": 0, "errors": 0},
    }
    try:
        data = load()
        forced = force_day_type()
        card["corpus"] = {
            "rhythms": int(data.get("rhythms") or 0),
            "activities": int(data.get("activities") or 0),
            "errors": int(data.get("errors") or 0),
        }
        card["day_type"] = forced or day_type_for(now)
        card["day_type_forced"] = bool(forced)
        card["enabled"] = enabled()
        # 关着开关就只报"关"，不去猜她在干嘛（面板/机器人都以 enabled 为准）
        if not card["enabled"] or not data.get("ok"):
            return card
        hit = resolve(now, card["day_type"])
        if not hit:
            return card
        card["ok"] = True
        card["activity"] = hit.get("activity")
        card["phrase"] = hit.get("phrase") or hit.get("activity")
        card["energy"] = float(hit.get("energy") or 0.0)
        card["sleeping"] = bool(hit.get("sleeping"))
        card["label"] = hit.get("label")
        card["source"] = hit.get("source")
        seg = hit.get("segment") or []
        if len(seg) == 2:
            card["segment"] = {"start": str(seg[0]), "end": str(seg[1])}
            end_minute = _to_minute(seg[1])
            if end_minute is not None:
                seconds_of_day = int(now.hour) * 3600 + int(now.minute) * 60 + int(now.second)
                card["seconds_left"] = max(0, min(86400, end_minute * 60) - seconds_of_day)
        return card
    except Exception:
        return card


# -------------------------------------------------------------------- 今天 --

#: day_type → 她的说法（给 prompt 的【你今天】用）
_DAY_TYPE_WORDS = {
    "workday": "工作日",
    "restday": "休息日",
    "holiday": "节假日",
    "travel": "出差/旅行",
    "special": "特殊的一天（不太舒服/熬夜）",
}


def today_line(moment: datetime | None = None) -> str:
    """给 prompt 的一句话"你今天"：她在干嘛 + 这会儿/接下来大概什么状态。

    只描述**今天的生活状态**，不写成回忆、不带具体客户/方案/人名；
    开关关着、没语料、时刻落缝 → 返回 ""（调用方直接不注入）。
    """
    try:
        if not enabled():
            return ""
        now = moment if moment is not None else now_local()
        hit = resolve(now)
        if not hit:
            return ""
        day_word = _DAY_TYPE_WORDS.get(str(hit.get("day_type")), "今天")
        phrase = str(hit.get("phrase") or hit.get("activity") or "").strip()
        if not phrase:
            return ""
        energy = float(hit.get("energy") or 0.0)
        sleeping = bool(hit.get("sleeping"))
        tail = ""
        if sleeping:
            tail = "；这个点你本来该睡着，被抓到会有点不好意思"
        elif energy <= 25:
            tail = "；你没什么力气，说话会短一点"
        elif energy >= 60:
            tail = "；你精神还行"
        return (
            f"今天是{day_word}，你现在的状态：{phrase}。"
            "这就是你今天的日常，不是他跟你讲过的事；聊到工作时可以说具体在忙什么，"
            "但不要编客户名、方案名、同事名。"
            + tail
        )
    except Exception:
        return ""
