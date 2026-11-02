"""联网工具：搜索 + 抓正文。

两条都走 `urllib.request`（与 `app/web/lookup.py` 同源），好处是离线测试里
把 `urllib.request.urlopen` 垫掉就整条链断网，不用 mock HTTP 客户端。

安全口径：
- 只允许 `http` / `https`；
- 配了 `tools.web.allow_hosts` 就只允许这些域名（含子域）；
- 单次响应最多读 2 MB，抓回来的正文按 `tools.web.max_chars` 截断。
﻿‍‌‍﻿﻿‌‌‌﻿‌​﻿‌​​﻿‍‌‌﻿‍﻿‍﻿‌﻿‌​‌‌​‍﻿"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

_MAX_BYTES = 2_000_000
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _allowed(url: str, allow: Any) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False, "bad-url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme"
    hosts = tuple(str(h).strip().lower() for h in (allow or ()) if str(h).strip())
    if not hosts:
        return True, ""
    host = (parsed.hostname or "").lower()
    for h in hosts:
        if host == h or host.endswith("." + h):
            return True, ""
    return False, "host-not-allowed"


def _clean(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = _HTML_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    )
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip()


def search(query: str, *, limit: int = 5, cfg: Any = None) -> dict[str, Any]:
    """搜索一个词。百度打头，空了退 Bing——与 `lookup.lookup()` 同一套后端。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "name": "web_search", "reason": "empty-query"}

    box = getattr(getattr(cfg, "tools", None), "web", None)
    limit = max(1, min(10, int(limit or getattr(box, "max_results", 5) or 5)))
    allow = getattr(box, "allow_hosts", ())

    from app.web import lookup as lookup_mod

    items: list[dict[str, str]] = []
    source = ""
    for name, fn in (("baidu", lookup_mod.baidu_search), ("bing", lookup_mod.bing_search)):
        rows = fn(q, limit) or []
        if rows:
            items = [{"title": str(r.get("title") or ""), "snippet": str(r.get("snippet") or "")}
                     for r in rows[:limit]]
            source = name
            break

    if not items:
        return {"ok": False, "name": "web_search", "reason": "no-result", "query": q}
    # 搜索结果里的链接可能被后续 fetch 用到：这里不校验 allow_hosts（结果是搜索给的）
    _ = allow
    return {"ok": True, "name": "web_search", "query": q, "source": source, "items": items}


def fetch(url: str, *, cfg: Any = None) -> dict[str, Any]:
    """抓一个网页的正文。只允许 http/https，按配置截断。"""
    link = (url or "").strip()
    if not link:
        return {"ok": False, "name": "web_fetch", "reason": "empty-url"}

    box = getattr(getattr(cfg, "tools", None), "web", None)
    ok, why = _allowed(link, getattr(box, "allow_hosts", ()))
    if not ok:
        return {"ok": False, "name": "web_fetch", "reason": why, "url": link}

    timeout = float(getattr(box, "timeout_s", 8.0) or 8.0)
    max_chars = int(getattr(box, "max_chars", 1500) or 1500)

    req = urllib.request.Request(link, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 已校验 scheme
            raw = resp.read(_MAX_BYTES)
            charset = ""
            try:
                charset = str(resp.headers.get_content_charset() or "")
            except Exception:  # noqa: BLE001
                charset = ""
    except Exception as exc:  # noqa: BLE001 - 抓不到就当没有
        return {"ok": False, "name": "web_fetch", "reason": f"{type(exc).__name__}: {exc}"[:120],
                "url": link}

    text = ""
    for enc in (charset, "utf-8", "gbk"):
        if not enc:
            continue
        try:
            text = raw.decode(enc, errors="replace")
            break
        except (LookupError, UnicodeDecodeError):
            continue
    body = _clean(text)[:max_chars]
    if not body:
        return {"ok": False, "name": "web_fetch", "reason": "empty-body", "url": link}
    return {"ok": True, "name": "web_fetch", "url": link, "text": body}


__all__ = ["fetch", "search"]
