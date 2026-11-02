"""热梗库：从 B 站评论区/视频标题学"网络热语"，**独立存放、绝不进她的记忆**。

设计原则（docs/25）：
1. 物理隔离：热梗存 `gags` 表，`memories` 表永远不写热梗；她只在合适的语境下引用。
2. 只学表达方式，不学立场：存的是"怎么说"，不存"对某事的看法"。
3. 有边界：骂人、地域、人身攻击类直接丢弃；不学脏话。
4. 可撤销：控制台能单条删除、整库清空。

表：
  gags(id, text, source_title, bvid, likes, kind, enabled, harvested_at)
  web_videos(bvid PK, title, author, pic, play, url, first_seen)
﻿‌﻿​‍﻿‍‌‍‍﻿‌‍​﻿﻿‌﻿‍﻿‍​​‍‌​﻿‍﻿​﻿​​﻿"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core import db

# 不学：脏话/辱骂/人身攻击（只挡真正的脏话，不做敏感词过滤——那是审核的事）
_BLOCK = re.compile(
    r"(傻逼|傻b|煞笔|沙比|妈的|尼玛|你妈死|操你|狗日|贱人|去死|死全家|"
    r"草泥马|日你|干你娘|全家死光|不得好死)",
)

# 热梗的典型特征：短、口语、有梗（重复字/网络缩写/感叹）
_MEME_HINT = re.compile(
    r"(6{3,}|哈{3,}|艹|草|绝了|离谱|yyds|awsl|xswl|栓q|芭比q|"
    r"我就问|这不是|什么叫|真就|绷不住|破防|会玩|懂的都懂|"
    r"已经开始|给我|这波|属于是|笑死|无语|麻了|寄了|"
    r"急了|急了|典|乐子|抽象|整活|活了|梗|"
    r"确实|好家伙|好活|神了|逆天|离谱|我超|就这|"
    r"\\\\d{3,}|[?!！？]{2,})",
    re.IGNORECASE,
)

# 新闻/时事类评论（不是热梗，学了会让她变成键政机器人）
_NEWS_LIKE = re.compile(
    r"(视频|up主|UP主|作者|希望能|保护|举报|封杀|官方|国家|媒体|"
    r"愤怒|致敬|加油|支持|反对|真的|应该|必须|希望)"
)

# 热梗来源：搜这些词拿到的评论才像"网络热语"
MEME_SOURCES = (
    "网络热梗", "热梗合集", "谐音梗", "抽象视频", "整活", "沙雕动画",
    "表情包", "网络热词", "梗百科", "B站热梗",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(text: str) -> bool:
    if not text:
        return False
    if _BLOCK.search(text):
        return False
    # 太长的不像梗
    if len(text) > 60:
        return False
    return True


def _is_gag(text: str) -> bool:
    """像不像"人怎么说话"：短、口语、有情绪；排除时间戳/艾特/纯数字这类噪音。"""
    t = (text or "").strip()
    if not (2 <= len(t) <= 50):
        return False
    # 噪音：纯时间戳、纯艾特、纯符号
    if re.fullmatch(r"[\d:：\s\.\-/]+", t):
        return False
    if t.startswith("@"):
        return False
    # 整条只有表情标签
    if re.fullmatch(r"([\[［].{1,6}[\]］])+", t):
        return False
    if len(t) < 4:
        return False
    # 只剩表情/标点混数字
    if not re.search(r"[\u4e00-\u9fff]{2,}", t):
        return False
    # 新闻腔且无梗感（学来会变键政机器人）
    if _NEWS_LIKE.search(t) and not _MEME_HINT.search(t):
        return False
    # 长且完全无梗感的陈述句，也不收
    if len(t) > 30 and not _MEME_HINT.search(t):
        return False
    return True


def add_gag(
    text: str, *, source_title: str = "", bvid: str = "", likes: int = 0, kind: str = "comment",
    require_meme: bool = True,
) -> int:
    """写入一条热梗；重复内容只更新点赞数。require_meme=True 时只收像热梗的。"""
    text = " ".join((text or "").split()).strip()
    if not _safe(text):
        return 0
    if require_meme and not _is_gag(text):
        return 0
    existing = db.one("SELECT id FROM gags WHERE text = ?", (text,))
    if existing:
        db.execute(
            "UPDATE gags SET likes = MAX(likes, ?), harvested_at = ? WHERE id = ?",
            (int(likes), _now(), int(existing["id"])),
        )
        return int(existing["id"])
    return db.execute(
        "INSERT INTO gags (text, source_title, bvid, likes, kind, enabled, harvested_at)"
        " VALUES (?, ?, ?, ?, ?, 1, ?)",
        (text, source_title[:80], bvid, int(likes), kind, _now()),
    )


def save_video(v: dict[str, Any]) -> None:
    bvid = str(v.get("bvid") or "")
    if not bvid:
        return
    row = db.one("SELECT bvid FROM web_videos WHERE bvid = ?", (bvid,))
    if row:
        db.execute(
            "UPDATE web_videos SET title = ?, play = ? WHERE bvid = ?",
            (str(v.get("title") or "")[:120], int(v.get("play") or 0), bvid),
        )
        return
    db.execute(
        "INSERT INTO web_videos (bvid, title, author, pic, play, url, first_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            bvid,
            str(v.get("title") or "")[:120],
            str(v.get("author") or "")[:40],
            str(v.get("pic") or "")[:200],
            int(v.get("play") or 0),
            str(v.get("url") or f"https://www.bilibili.com/video/{bvid}"),
            _now(),
        ),
    )


def list_gags(limit: int = 100, q: str = "") -> list[dict[str, Any]]:
    if q.strip():
        rows = db.rows(
            "SELECT * FROM gags WHERE enabled = 1 AND text LIKE ? ORDER BY likes DESC LIMIT ?",
            (f"%{q.strip()}%", int(limit)),
        )
    else:
        rows = db.rows(
            "SELECT * FROM gags WHERE enabled = 1 ORDER BY likes DESC, id DESC LIMIT ?",
            (int(limit),),
        )
    return [dict(r) for r in rows]


def delete_gag(gag_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute("DELETE FROM gags WHERE id = ?", (int(gag_id),))
        return cur.rowcount > 0


def clear_gags() -> int:
    with db.cursor() as cur:
        cur.execute("DELETE FROM gags")
        return cur.rowcount


def stats() -> dict[str, Any]:
    total = db.one("SELECT COUNT(*) c FROM gags WHERE enabled = 1")["c"]
    kinds = {r["kind"]: int(r["c"]) for r in db.rows("SELECT kind, COUNT(*) c FROM gags GROUP BY kind")}
    videos = db.one("SELECT COUNT(*) c FROM web_videos")["c"]
    last = db.one("SELECT MAX(harvested_at) m FROM gags")
    return {
        "gags": {"total": int(total), "by_kind": kinds},
        "videos": int(videos),
        "last_harvest": (last["m"] if last else "") or "",
    }


def pick_gag(scene: str | None = None, exclude: set[str] | None = None) -> dict[str, Any] | None:
    """给对话挑一条可用热梗（按点赞权重随机）。"""
    import random

    rows = db.rows("SELECT * FROM gags WHERE enabled = 1 ORDER BY likes DESC LIMIT 200")
    if not rows:
        return None
    pool = [
        dict(r) for r in rows
        if str(r["id"]) not in (exclude or set()) and _MEME_HINT.search(str(r["text"]) or "")
    ]
    if not pool:
        pool = [dict(r) for r in rows]
    return random.choice(pool[:80])


def prompt_hint(limit: int = 6) -> str:
    """给模型的一段提示：可以用哪些网络热语，但别硬塞。"""
    gags = list_gags(limit)
    if not gags:
        return ""
    lines = [str(g["text"]) for g in gags]
    return (
        "【最近流行的说法】\n"
        + "\n".join(f"- {x}" for x in lines)
        + "\n可以偶尔借用这种说法，但要自然；一句里最多用一个，别硬塞。"
    )
