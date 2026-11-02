"""锚点池：从真实语料里抽出"她会说的话"。

两条原则：
1. 频次 + 分层并用。纯频次会把她压成"哈哈哈 + 服了"，纯随机又抓不到她的口头禅。
2. 内容不过滤，但按关系阶段放行。脏话、亲昵、露骨的话都留着，
   只是在她还不熟的时候不会说出来——这不是审查，是节奏。
﻿‍‌‌﻿‌﻿﻿‍‌‍​﻿‌‌‍​﻿‍‍﻿​‍﻿‌‍‍​​‍‍‌‍﻿"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.core import db

# 场景判定：越靠前优先级越高
SCENES: list[tuple[str, tuple[str, ...]]] = [
    ("亲密", ("宝宝", "宝贝", "老婆", "老公", "爱你", "想你", "亲亲", "抱抱", "mua", "么么", "乖")),
    ("撒娇", ("好不好", "求求", "呜呜", "不理你", "要抱", "要亲", "贴贴", "人家", "嘛？", "嘛?", "嘛。", "嘛~")),
    ("生气", ("服了", "无语", "有病", "滚", "烦", "气死", "恶心", "特码", "妈了个", "奶奶滴",
              "别烦", "不想说", "随便你", " bbll", "焯", "卧槽", "草", "裂开")),
    ("关心", ("早点睡", "别熬夜", "喝口水", "吃药", "冷不冷", "饿不饿", "注意", "照顾好",
              "感冒", "疼", "休息", "别硬撑", "吃饭")),
    ("自嘲", ("我笨", "我好蠢", "我疯了", "我不行", "救命", "废物", "我怀疑", "我好菜", "我裂开")),
    ("深夜", ("睡不着", "晚安", "睡了", "困", "凌晨", "梦", "夜里")),
]

STAGE_ORDER = ["observer", "swimmer", "echo", "samefreq", "lover", "bond"]

# 称呼类锚点：到「同频者」才解锁
INTIMATE_WORDS = ("宝宝", "宝贝", "老婆", "老公", "亲亲", "抱抱", "mua", "么么", "爱你", "想你")
# 露骨内容：到「恋人」才解锁
EXPLICIT_WORDS = (
    "操", "射", "抽插", "做爱", "内射", "舔", "揉", "骚", "涩涩", "色", "射射",
    "阴蒂", "阴道", "阴茎", "鸡巴", "奶子", "高潮", "淫", "脱光", "裸", "叫床",
    "口交", "插", "硬", "湿透", "摸", "屁股", "舔", "亲热", "做", "睡你",
)
# 脏话：到「回声」才解锁（会损人了才说得出口）
PROFANE_WORDS = ("特码", "妈了个", "奶奶滴", "焯", "卧槽", "草泥", "妈的", "bbll", "傻逼", "有病")

MIN_STAGE = {"intimate": 3, "explicit": 4, "profane": 2, "plain": 0}

MAX_LEN = 42
MIN_LEN = 4


@dataclass
class Pool:
    items: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# 纯语气/纯提问，做声纹没有辨识度
GENERIC = {
    "哈哈哈哈", "哈哈哈哈哈", "哈哈哈哈哈哈", "哈哈哈哈哈哈哈", "哈哈哈哈哈哈哈哈",
    "笑死我了", "在吗", "在干嘛", "你在干嘛", "你在干嘛呢", "在干嘛呢", "干嘛呢",
    "好", "好的", "嗯", "嗯嗯", "哦", "嗯呢", "是啊", "是啊是啊", "没有", "不是",
    "晚安", "早安", "我要睡了", "我要吃饭了", "你吃饭了吗", "吃饭了吗", "我吃饭啊",
    "我也爱你", "爱你", "想你", "么么", "mua",
}


def quality(text: str, freq: int) -> float:
    """声纹质量：有内容 > 有语气 > 纯笑声。频次只是加分项，不是主项。"""
    import math

    t = text.strip()
    score = math.log1p(freq) * 0.8
    if len(t) < 6:
        score -= 1.2
    if len(set(t)) <= 3:
        score -= 2.0
    if t in GENERIC:
        score -= 2.2
    if 7 <= len(t) <= 24:
        score += 0.9
    if any(ch in t for ch in "我你"):
        score += 0.7
    else:
        score -= 0.6
    if any(ch in t for ch in "，。？…！"):
        score += 0.3
    if t.endswith(("。", "…", "？", "?")):
        score += 0.2
    return score


def scene_of(text: str) -> str:
    if any(w in text for w in EXPLICIT_WORDS):
        return "亲密"
    for scene, words in SCENES:
        if any(w in text for w in words):
            return scene
    return "日常"


def flags_of(text: str) -> dict[str, bool]:
    return {
        "intimate": any(w in text for w in INTIMATE_WORDS),
        "explicit": any(w in text for w in EXPLICIT_WORDS),
        "profane": any(w in text for w in PROFANE_WORDS),
    }


SCENE_STAGE = {"日常": 0, "关心": 0, "深夜": 0, "自嘲": 1, "生气": 2, "撒娇": 3, "亲密": 3}


def stage_min_for(text: str, scene: str) -> int:
    f = flags_of(text)
    base = SCENE_STAGE.get(scene, 0)
    if f["explicit"]:
        return max(base, MIN_STAGE["explicit"])
    if f["intimate"]:
        return max(base, MIN_STAGE["intimate"])
    if f["profane"]:
        return max(base, MIN_STAGE["profane"])
    return base


_REJECT_MARKERS = (
    "引用", "转发", "撤回", "拒收", "新消息", "位置共享", "已经结束", "以下",
    "[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[名片]", "[转账]", "[红包]",
)


def is_usable(text: str) -> bool:
    t = text.strip()
    if not (MIN_LEN <= len(t) <= MAX_LEN):
        return False
    if "{" in t or "}" in t:          # 含去敏占位符，说明提到别人
        return False
    if "http" in t:
        return False
    if any(m in t for m in _REJECT_MARKERS):
        return False
    if t.count("：") >= 1 or t.count(":") >= 2:   # 转发/引用的残留结构
        return False
    if "他" in t or "她" in t:        # 第三方指代：严格版的核心禁令
        return False
    if "  " in t:                     # 多行拼接残留
        return False
    if t.startswith(("比如", "然后", "所以", "而且", "但是", "因为", "如果", "就是", "还有", "而不是", "比如 ")):
        return False
    return True


def build(min_per_scene: int = 24, sample_extra: int = 40, seed: int = 20261028) -> dict[str, Any]:
    """从 ref_turns 里重建锚点池。"""
    rows = db.rows(
        "SELECT text, COUNT(*) AS c FROM ref_turns"
        " WHERE role = 'her' AND period IN ('honeymoon','stable')"
        " GROUP BY text ORDER BY c DESC LIMIT 40000"
    )
    candidates: list[tuple[str, int]] = []
    for r in rows:
        text = str(r["text"]).strip()
        if not is_usable(text):
            continue
        candidates.append((text, int(r["c"])))

    by_scene: dict[str, list[tuple[str, int]]] = {}
    for text, c in candidates:
        by_scene.setdefault(scene_of(text), []).append((text, c))

    rng = random.Random(seed)
    picked: list[tuple[str, int, str]] = []
    for scene, items in by_scene.items():
        items.sort(key=lambda x: -x[1])
        head = items[:min_per_scene]
        rest = items[min_per_scene:]
        tail = rng.sample(rest, min(sample_extra, len(rest))) if rest else []
        for text, c in head + tail:
            picked.append((text, c, scene))

    with db.cursor() as cur:
        cur.execute("DELETE FROM anchors")
    db.executemany(
        "INSERT OR IGNORE INTO anchors (text, scene, stage_min, explicit, profane, freq, enabled)"
        " VALUES (?, ?, ?, ?, ?, ?, 1)",
        [
            (
                text,
                scene,
                stage_min_for(text, scene),
                1 if flags_of(text)["explicit"] else 0,
                1 if flags_of(text)["profane"] else 0,
                c,
            )
            for text, c, scene in picked
        ],
    )

    stats = {
        "candidates": len(candidates),
        "written": len(picked),
        "by_scene": dict(Counter(s for _, _, s in picked)),
        "by_stage": dict(Counter(stage_min_for(t, s) for t, _, s in picked)),
        "explicit": sum(1 for t, _, _ in picked if flags_of(t)["explicit"]),
        "profane": sum(1 for t, _, _ in picked if flags_of(t)["profane"]),
        "intimate": sum(1 for t, _, _ in picked if flags_of(t)["intimate"]),
    }
    return stats


def _stage_idx(stage_key: str) -> int:
    return STAGE_ORDER.index(stage_key) if stage_key in STAGE_ORDER else 0


def pick_fixed(stage_index: int, n: int = 10) -> list[str]:
    """常驻声纹：跨场景轮流取，避免十条全是同一类。"""
    rows = db.rows(
        "SELECT text, scene, freq FROM anchors WHERE enabled = 1 AND stage_min <= ?",
        (stage_index,),
    )
    scored = sorted(rows, key=lambda r: -quality(str(r["text"]), int(r["freq"])))
    by_scene: dict[str, list[str]] = {}
    for r in scored:
        by_scene.setdefault(str(r["scene"]), []).append(str(r["text"]))
    out: list[str] = []
    laughs = 0
    questions = 0
    order = ["日常", "亲密", "关心", "撒娇", "自嘲", "生气", "深夜"]
    while len(out) < n:
        progressed = False
        for scene in order:
            bucket = by_scene.get(scene) or []
            while bucket:
                cand = bucket.pop(0)
                is_laugh = len(set(cand)) <= 3 or "哈哈" in cand or "笑死" in cand
                is_q = cand.endswith(("？", "?"))
                if is_laugh and laughs >= 2:
                    continue
                if is_q and questions >= 2:
                    continue
                if is_laugh:
                    laughs += 1
                if is_q:
                    questions += 1
                out.append(cand)
                progressed = True
                break
            if len(out) >= n:
                break
        if not progressed:
            break
    return out


def pick_dynamic(scene: str, stage_index: int, n: int = 6) -> list[str]:
    rows = db.rows(
        "SELECT text, freq FROM anchors WHERE enabled = 1 AND scene = ? AND stage_min <= ?",
        (scene, stage_index),
    )
    scored = sorted(rows, key=lambda r: -quality(str(r["text"]), int(r["freq"])))
    return [str(r["text"]) for r in scored[:n]]


def scene_for_text(text: str, mood: int = 0) -> str:
    s = scene_of(text)
    if s == "日常" and mood <= -1:
        return "关心"
    return s


def stats() -> dict[str, Any]:
    total = db.one("SELECT COUNT(*) c FROM anchors")
    by_scene = {r["scene"]: int(r["c"]) for r in db.rows("SELECT scene, COUNT(*) c FROM anchors GROUP BY scene")}
    return {"total": int(total["c"]) if total else 0, "by_scene": by_scene}
