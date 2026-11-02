"""B 站匿名接口客户端（无需登录）：搜索视频、热门、评论、视频详情。

只做 GET，不下载视频，不碰需要登录的接口。
wbi 签名参考 B 站前端公开算法（img_key + sub_key 重排取前 32 位再 md5）。
﻿﻿‍​‍​﻿‍﻿‌​﻿‍﻿​‍‌​‍﻿‍﻿​‍﻿‍﻿​﻿​​‍‌﻿"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}

_MIXIN = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

_cache: dict[str, Any] = {"keys": None, "ts": 0.0}


def _get(url: str, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _wbi_keys() -> tuple[str, str]:
    """缓存 10 分钟，避免每次请求都打 nav 接口。"""
    now = time.time()
    if _cache["keys"] and now - _cache["ts"] < 600:
        return _cache["keys"]  # type: ignore[return-value]
    raw = _get("https://api.bilibili.com/x/web-interface/nav")
    data = raw.get("data") or {}
    img = (data.get("wbi_img") or {})
    img_key = str(img.get("img_url", "")).rsplit("/", 1)[-1].split(".")[0]
    sub_key = str(img.get("sub_url", "")).rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise RuntimeError("拿不到 B 站 wbi key")
    _cache["keys"] = (img_key, sub_key)
    _cache["ts"] = now
    return img_key, sub_key


def _sign(params: dict[str, Any]) -> str:
    img_key, sub_key = _wbi_keys()
    raw = img_key + sub_key
    mixin_key = "".join(raw[i] for i in _MIXIN if i < len(raw))[:32]
    p = dict(params)
    p["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(p.items()))
    p["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(p.items()))


def trending() -> list[dict[str, Any]]:
    """B 站热搜榜（真实的网络热词来源，质量比评论区高得多）。"""
    out = []
    for url in (
        "https://api.bilibili.com/x/web-interface/search/square?limit=50",
        "https://api.bilibili.com/x/web-interface/wbi/search/square?limit=50",
    ):
        try:
            raw = _get(url)
        except Exception:  # noqa: BLE001
            continue
        data = raw.get("data") or {}
        if isinstance(data, dict):
            trend = data.get("trending") or {}
            items = trend.get("list") if isinstance(trend, dict) else trend
            items = items or data.get("list") or []
        else:
            items = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            kw = str(it.get("keyword") or it.get("show_name") or it.get("name") or "").strip()
            if not kw:
                continue
            out.append(
                {
                    "keyword": kw,
                    "heat": int(it.get("heat_score") or it.get("heat") or 0),
                    "source": "bili_square",
                }
            )
        if out:
            break
    return out


def hot_comments(bvid: str, limit: int = 20) -> list[dict[str, Any]]:
    """按 bvid 取评论（内部转 aid）。"""
    info = video_info(bvid)
    if not info or not info.get("aid"):
        return []
    return comments(int(info["aid"]), limit=limit)


# ---------------------------------------------------------------- 对外


def search_videos(keyword: str, limit: int = 12) -> list[dict[str, Any]]:
    """关键词搜视频。返回干净的列表（标题去高亮标签）。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    qs = _sign({"search_type": "video", "keyword": keyword, "page": 1})
    raw = _get(f"https://api.bilibili.com/x/web-interface/wbi/search/type?{qs}")
    if raw.get("code") != 0:
        raise RuntimeError(f"B 站搜索失败：{raw.get('message')}")
    items = (raw.get("data") or {}).get("result") or []
    out = []
    for v in items[: max(1, min(50, limit))]:
        title = str(v.get("title", ""))
        title = title.replace('<em class="keyword">', "").replace("</em>", "")
        pic = str(v.get("pic", ""))
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append(
            {
                "bvid": v.get("bvid"),
                "title": title.strip(),
                "author": v.get("author"),
                "play": int(v.get("play") or 0),
                "duration": v.get("duration"),
                "pubdate": int(v.get("pubdate") or 0),
                "pic": pic,
                "url": f"https://www.bilibili.com/video/{v.get('bvid')}",
            }
        )
    return out


def popular(limit: int = 10) -> list[dict[str, Any]]:
    """热门视频（拿来当热梗来源）。"""
    raw = _get(f"https://api.bilibili.com/x/web-interface/popular?ps={max(1, min(50, limit))}&pn=1")
    items = (raw.get("data") or {}).get("list") or []
    out = []
    for v in items:
        pic = str(v.get("pic", ""))
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append(
            {
                "aid": v.get("aid"),
                "bvid": v.get("bvid"),
                "title": str(v.get("title", "")).strip(),
                "author": (v.get("owner") or {}).get("name"),
                "play": int((v.get("stat") or {}).get("view") or 0),
                "pic": pic,
                "url": f"https://www.bilibili.com/video/{v.get('bvid')}",
            }
        )
    return out


def video_info(bvid: str) -> dict[str, Any] | None:
    try:
        raw = _get(f"https://api.bilibili.com/x/web-interface/view?bvid={urllib.parse.quote(bvid)}")
    except Exception:  # noqa: BLE001
        return None
    if raw.get("code") != 0:
        return None
    d = raw.get("data") or {}
    pic = str(d.get("pic", ""))
    if pic.startswith("//"):
        pic = "https:" + pic
    return {
        "aid": d.get("aid"),
        "bvid": d.get("bvid"),
        "title": d.get("title"),
        "desc": (d.get("desc") or "")[:200],
        "author": (d.get("owner") or {}).get("name"),
        "pic": pic,
        "play": int((d.get("stat") or {}).get("view") or 0),
        "url": f"https://www.bilibili.com/video/{d.get('bvid')}",
    }


def comments(aid: int, limit: int = 20, hot: bool = True) -> list[dict[str, Any]]:
    """取某个视频的评论（热门优先），只返回文本与点赞。

    注意：旧接口 /x/v2/reply 现在对匿名请求返回 412，改用 /x/v2/reply/main。
    部分视频仍会返回 -352（风控）或空列表，直接当没有。
    """
    mode = "3" if hot else "2"
    urls = (
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={int(aid)}&mode={mode}"
        f"&ps={max(1, min(50, limit))}&next=0",
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={int(aid)}&mode=3&ps=20&next=0",
    )
    raw: dict[str, Any] = {}
    for url in urls:
        try:
            raw = _get(url)
        except Exception:  # noqa: BLE001 - 412 等直接跳过
            continue
        if raw.get("code") == 0:
            break
    if raw.get("code") != 0:
        return []
    data = raw.get("data") or {}
    reps = data.get("replies") or []
    if not reps:
        # 新版可能把热评放在 top_replies
        reps = data.get("top_replies") or []
    out = []
    for r in reps:
        if not isinstance(r, dict):
            continue
        msg = str((r.get("content") or {}).get("message", "")).strip()
        if not msg:
            continue
        out.append(
            {
                "text": " ".join(msg.split())[:120],
                "likes": int(r.get("like") or 0),
                "rpid": r.get("rpid"),
            }
        )
    return out
