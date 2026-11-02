"""人类日常语料采集脚手架：只读 GET、守 robots、默认离线。

设计边界（写死在代码里，不提供开关）：
- 只发 GET；不带 Cookie / Authorization；不登录、不提交表单、不跟随验证码；
- 每个 host 第一次抓取前读一次 ``/robots.txt``，Disallow 命中直接拒绝；
- 不并发：``fetch_many`` 顺序执行，两次请求之间必须 sleep，同 host 更保守；
- ``plan_fetch`` 完全不碰网络，CLI 的 dry-run 只走它，保证默认零请求；
- 任何异常都不外抛，统一进返回值的 ``error`` 字段，由调用方决定跳过还是终止。

标准库 only：urllib / urllib.robotparser / gzip / re / html / ssl / hashlib / datetime。
﻿﻿​﻿​‍﻿​‍‌‌﻿​​‍﻿﻿‍﻿﻿﻿​‍​﻿﻿﻿​﻿​‍﻿‍﻿"""

from __future__ import annotations

import gzip
import hashlib
import html
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Final
from urllib.robotparser import RobotFileParser

from .pii import scan_text

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT: Final[str] = "symemory-corpus/1.0 (local research; contact: local)"
ROBOTS_MAX_BYTES: Final[int] = 512_000
_ALLOWED_SCHEMES: Final[tuple[str, ...]] = ("http", "https")

#: host -> {"status": int | None, "text": str | None, "error": str | None}
_ROBOTS_CACHE: dict[str, dict[str, Any]] = {}

# HTML → 纯文本
_HTML_COMMENT: Final = re.compile(r"<!--.*?-->", re.S)
_HTML_HIDDEN: Final = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.I | re.S
)
_HTML_BLOCK: Final = re.compile(
    r"<\s*(?:br|hr)\s*/?\s*>"
    r"|<\s*/\s*(?:p|div|li|tr|td|th|h[1-6]|section|article|header|footer|ul|ol|table"
    r"|blockquote|pre|dd|dt|nav|main|aside)\s*>"
    r"|<\s*(?:p|div|li|h[1-6]|section|article|blockquote|pre|tr|nav|main|aside)\b[^>]*>",
    re.I,
)
_HTML_TAG: Final = re.compile(r"<[^>]*>")
_HTML_CHARSET: Final = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", re.I
)
_WHITESPACE: Final = re.compile(r"[ \t\r\f\v\u00a0]+")

# 广告 / 联系方式判定（只抓“明显”的营销行，避免误伤日常口语）
_URLISH: Final = re.compile(r"(?:https?://|www\.)", re.I)
_CONTACT_CTA: Final = re.compile(r"(?:加|扫|关注|私信|联系)\s*(?:微信|公众号|QQ|qq|vx|VX|V信|群)")
_AD_WORDS: Final[tuple[str, ...]] = (
    "优惠",
    "促销",
    "限时",
    "抢购",
    "立即购买",
    "点击购买",
    "领红包",
    "扫码",
    "下载APP",
    "推广",
    "代购",
    "客服QQ",
    "戳这里",
    "广告",
)
_PURE_PUNCT: Final = re.compile(r"^[\W_]+$")


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """一次采集会话的礼貌参数；全部有保守默认值。"""

    delay_seconds: float = 2.0
    timeout_s: float = 10.0
    user_agent: str = DEFAULT_USER_AGENT
    max_bytes: int = 2_000_000
    respect_robots: bool = True

    def as_dict(self) -> dict[str, Any]:
        """给 manifest 用的可序列化快照。"""
        return {
            "delay_seconds": self.delay_seconds,
            "timeout_s": self.timeout_s,
            "user_agent": self.user_agent,
            "max_bytes": self.max_bytes,
            "respect_robots": self.respect_robots,
        }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """UTC 秒级 ISO 时间，manifest 用。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _status_of(response: Any) -> int | None:
    for name in ("status", "code"):
        value = getattr(response, name, None)
        if isinstance(value, int):
            return value
    return None


def _header(headers: Any, name: str) -> str:
    """大小写不敏感地取一个响应头，兼容真实 HTTPMessage 与测试用的 dict。"""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            value = getter(name)
        except Exception:
            value = None
        if isinstance(value, str):
            return value
    items = getattr(headers, "items", None)
    if callable(items):
        try:
            for key, value in items():
                if str(key).lower() == name.lower() and isinstance(value, str):
                    return value
        except Exception:
            return ""
    return ""


def _close_quietly(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _urlopen(request: urllib.request.Request, policy: FetchPolicy) -> Any:
    """唯一的出口；测试通过 patch ``urllib.request.urlopen`` 来完全离线。"""
    return urllib.request.urlopen(request, timeout=policy.timeout_s)


def _read_limited(response: Any, limit: int) -> bytes:
    """最多读 ``limit`` 字节，避免把超大页面整个拉进内存。"""
    reader = getattr(response, "read", None)
    if not callable(reader):
        return b""
    bound = max(1, int(limit))
    try:
        chunk = reader(bound)
    except TypeError:  # 少数假对象不接受参数
        chunk = reader()
    if not chunk:
        return b""
    return bytes(chunk)[:bound]


def _looks_gzip(content_encoding: str, raw: bytes) -> bool:
    return "gzip" in content_encoding.lower() or raw[:2] == b"\x1f\x8b"


def _gunzip(raw: bytes) -> bytes:
    """解 gzip；被 max_bytes 截断的半截包解不开时保留原字节，交给上层记 truncated。"""
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError):
        return raw


def _charset_from_meta(raw: bytes) -> str | None:
    match = _HTML_CHARSET.search(raw[:4096])
    if not match:
        return None
    return match.group(1).decode("ascii", errors="ignore") or None


def _charset_from_content_type(content_type: str) -> str | None:
    for piece in content_type.split(";")[1:]:
        key, _, value = piece.partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip("\"'") or None
    return None


def decode_body(raw: bytes, content_type: str = "") -> tuple[str, str]:
    """按 Content-Type → meta charset → utf-8 → gbk/gb18030 兜底解码。

    返回 ``(文本, 实际编码名)``；一个都解不开时用 utf-8 + replace 兜底。
    """
    candidates: list[str] = []
    for candidate in (
        _charset_from_content_type(content_type),
        _charset_from_meta(raw),
        "utf-8",
        "gbk",
        "gb18030",
    ):
        if candidate and candidate.lower() not in [item.lower() for item in candidates]:
            candidates.append(candidate)
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def _normalize_lines(text: str) -> str:
    """逐行压空白、去空行；保留换行结构，方便后面按行切片。"""
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def html_to_text(markup: str) -> str:
    """HTML → 纯文本：去注释 / script / style，块级标签转换行，其余标签去掉。"""
    text = _HTML_COMMENT.sub("", markup)
    text = _HTML_HIDDEN.sub("", text)
    text = _HTML_BLOCK.sub("\n", text)
    text = _HTML_TAG.sub("", text)
    text = html.unescape(text)
    return _normalize_lines(text)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def clear_robots_cache() -> None:
    """清空 host 级 robots 缓存（测试用；生产也可在长跑前清）。"""
    _ROBOTS_CACHE.clear()


def _fetch_robots(robots_url: str, policy: FetchPolicy) -> dict[str, Any]:
    """读一次 robots.txt；失败不抛，返回带 ``error`` 的记录。"""
    request = urllib.request.Request(
        robots_url,
        method="GET",
        headers={
            "User-Agent": policy.user_agent,
            "Accept": "text/plain, */*;q=0.1",
        },
    )
    response: Any = None
    try:
        response = _urlopen(request, policy)
        status = _status_of(response)
        raw = _read_limited(response, ROBOTS_MAX_BYTES)
        return {
            "status": status,
            "text": raw.decode("utf-8", errors="replace"),
            "error": None,
        }
    except Exception as exc:  # 取不到就按允许处理，但一定要记录
        return {"status": None, "text": None, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _close_quietly(response)


def _robots_parser(text: str) -> RobotFileParser:
    parser = RobotFileParser()
    try:
        parser.parse(text.splitlines())
    except Exception:  # 解析器对畸形行偶尔会炸，坏了当空白规则
        pass
    return parser


def robots_verdict(url: str, policy: FetchPolicy | None = None) -> dict[str, Any]:
    """判断一条 URL 是否被 robots.txt 允许；按 host 缓存，``allowed`` 三态。

    - ``True``  ：robots 明确允许，或 policy 关闭了检查；
    - ``False`` ：Disallow 命中（含 ``User-agent: *``），调用方必须拒绝；
    - ``None``  ：robots.txt 取不到（403/404/超时/网络错），按允许处理但记录原因。
    """
    policy = policy or FetchPolicy()
    parts = urllib.parse.urlsplit(url)
    verdict: dict[str, Any] = {
        "url": url,
        "robots_url": None,
        "allowed": None,
        "reason": "",
        "checked": False,
        "status": None,
    }
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        verdict["allowed"] = False
        verdict["reason"] = "只支持 http/https，URL 无效或非网页协议"
        return verdict
    if not policy.respect_robots:
        verdict["allowed"] = True
        verdict["reason"] = "policy.respect_robots=False（未检查 robots）"
        return verdict

    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    verdict["robots_url"] = robots_url
    record = _ROBOTS_CACHE.get(parts.netloc)
    if record is None:
        record = _fetch_robots(robots_url, policy)
        _ROBOTS_CACHE[parts.netloc] = record
    verdict["status"] = record["status"]
    verdict["checked"] = record["error"] is None

    if record["error"]:
        verdict["reason"] = "robots 取不到（按允许处理但记录）"
        return verdict
    parser = _robots_parser(record["text"] or "")
    if parser.can_fetch(policy.user_agent, url):
        verdict["allowed"] = True
        verdict["reason"] = "robots.txt 未禁止"
    else:
        verdict["allowed"] = False
        verdict["reason"] = "robots.txt Disallow 命中"
    return verdict


# ---------------------------------------------------------------------------
# 单页抓取
# ---------------------------------------------------------------------------


def _empty_result(url: str) -> dict[str, Any]:
    return {
        "ok": False,
        "url": url,
        "status": None,
        "bytes": 0,
        "encoding": "",
        "text": "",
        "error": None,
        "fetched_at": _now_iso(),
        "robots": None,
        "truncated": False,
    }


def fetch_page(url: str, policy: FetchPolicy | None = None) -> dict[str, Any]:
    """单次 GET → 纯文本。任何异常都进 ``error`` 字段，绝不外抛。

    返回字段：``ok / url / status / bytes / encoding / text / error / fetched_at``，
    另外附带 ``robots``（判定记录）与 ``truncated``（是否被 max_bytes 截断）。
    """
    policy = policy or FetchPolicy()
    result = _empty_result(url)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        result["error"] = "只支持 http/https，URL 无效或非网页协议"
        return result

    verdict = robots_verdict(url, policy)
    result["robots"] = {
        "allowed": verdict.get("allowed"),
        "reason": verdict.get("reason", ""),
        "checked": verdict.get("checked", False),
        "status": verdict.get("status"),
        "robots_url": verdict.get("robots_url"),
    }
    if verdict.get("allowed") is False:
        result["error"] = f"robots 拒绝：{verdict.get('reason', '')}"
        return result

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": policy.user_agent,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Encoding": "gzip",
            "Accept-Language": "zh-CN,zh;q=0.9",
            # 不带 Cookie / Referer / Authorization：不登录、不续会话
        },
    )
    response: Any = None
    try:
        response = _urlopen(request, policy)
        status = _status_of(response)
        result["status"] = status
        headers = getattr(response, "headers", None)
        content_type = _header(headers, "Content-Type")
        content_encoding = _header(headers, "Content-Encoding")
        raw = _read_limited(response, policy.max_bytes)
        truncated = len(raw) >= policy.max_bytes
        if _looks_gzip(content_encoding, raw):
            raw = _gunzip(raw)
        if len(raw) > policy.max_bytes:
            raw = raw[: policy.max_bytes]
            truncated = True
        result["bytes"] = len(raw)
        result["truncated"] = truncated

        text, encoding = decode_body(raw, content_type)
        result["encoding"] = encoding
        if "html" in content_type.lower() or "xml" in content_type.lower():
            text = html_to_text(text)
        elif text.lstrip().startswith("<"):
            text = html_to_text(text)
        else:
            text = _normalize_lines(text)
        result["text"] = text

        if status is None or status < 400:
            result["ok"] = True
            result["error"] = None
        else:
            result["ok"] = False
            result["error"] = f"HTTP {status}"
    except urllib.error.HTTPError as exc:  # 403 / 404 也属于正常返回，记下来继续
        result["status"] = exc.code
        result["ok"] = False
        result["error"] = f"HTTP {exc.code} {exc.reason}"
    except (ssl.SSLError, TimeoutError, OSError) as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # 兜底：任何实现层的意外都不能炸掉整批采集
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _close_quietly(response)
    return result


# ---------------------------------------------------------------------------
# 批量 / dry-run
# ---------------------------------------------------------------------------


def plan_fetch(urls: list[str], policy: FetchPolicy | None = None) -> list[dict[str, Any]]:
    """dry-run 计划：只做本地解析，**不**联网、不读文件、不写文件。"""
    policy = policy or FetchPolicy()
    plan: list[dict[str, Any]] = []
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        plan.append(
            {
                "url": url,
                "host": parts.netloc,
                "scheme": parts.scheme,
                "valid": parts.scheme in _ALLOWED_SCHEMES and bool(parts.netloc),
                "robots": {
                    "allowed": None,
                    "checked": False,
                    "reason": "dry-run：未检查 robots（不联网）",
                    "status": None,
                },
                "policy": policy.as_dict(),
            }
        )
    return plan


def format_plan(plan: list[dict[str, Any]]) -> str:
    """把 plan 渲染成给人看的若干行。"""
    lines: list[str] = []
    for item in plan:
        flag = "OK  " if item["valid"] else "跳过"
        lines.append(f"  [{flag}] {item['url']}  host={item['host'] or '-'}  robots=未检查")
    return "\n".join(lines)


def fetch_many(
    urls: list[str],
    policy: FetchPolicy | None = None,
    *,
    sleeper: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """顺序抓取：请求之间 sleep；同 host 更保守（delay × 1.5）。

    单条失败（robots 拒绝 / 403 / 404 / 超时）只记进结果的 ``error``，继续下一条。
    ``sleeper`` 可注入，便于离线测试不真的等待。
    """
    policy = policy or FetchPolicy()
    sleep = sleeper or time.sleep
    results: list[dict[str, Any]] = []
    previous_host: str | None = None
    for index, url in enumerate(urls):
        host = urllib.parse.urlsplit(url).netloc
        if index > 0:
            delay = policy.delay_seconds
            if host and host == previous_host:
                delay = policy.delay_seconds * 1.5
            if delay > 0:
                sleep(delay)
        results.append(fetch_page(url, policy))
        previous_host = host
    return results


# ---------------------------------------------------------------------------
# 片段切分
# ---------------------------------------------------------------------------


def _ad_reason(line: str) -> str | None:
    """判断一行是否是明显广告/联系方式；是就返回原因，否返回 None。"""
    if _URLISH.search(line):
        return "url"
    if _CONTACT_CTA.search(line):
        return "contact"
    if any(word in line for word in _AD_WORDS):
        return "ad"
    if scan_text(line):  # 复用 pii 的检测：手机号 / 身份证 / 邮箱
        return "pii"
    if _PURE_PUNCT.fullmatch(line):
        return "noise"
    return None


def filter_lines(text: str) -> tuple[list[str], dict[str, int]]:
    """按行过滤广告/联系方式/噪声，返回 ``(保留行, 丢弃原因计数)``。"""
    kept: list[str] = []
    dropped: dict[str, int] = {}
    for raw_line in text.splitlines():
        line = _WHITESPACE.sub(" ", raw_line).strip()
        if not line:
            continue
        reason = _ad_reason(line)
        if reason is None:
            kept.append(line)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    return kept, dropped


def strip_ads(text: str) -> str:
    """去掉明显广告 / 联系方式 / 纯符号行，其余按行保留。"""
    kept, _ = filter_lines(text)
    return "\n".join(kept)


def split_snippets(text: str, max_len: int = 120) -> list[str]:
    """切成短片段：按行、去重、超长截断到 ``max_len``、丢掉广告行。

    只产出纯文本片段，不做任何 schema/表映射；要不要入库由人工决定。
    """
    if max_len <= 0:
        raise ValueError("max_len 必须为正整数")
    kept, _ = filter_lines(text)
    snippets: list[str] = []
    seen: set[str] = set()
    for line in kept:
        snippet = line[:max_len].strip()
        if len(snippet) < 2 or snippet in seen:
            continue
        seen.add(snippet)
        snippets.append(snippet)
    return snippets


# ---------------------------------------------------------------------------
# 落盘辅助
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """正文的 sha256（hex），写进 manifest 便于复查是否被抓页变更影响。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_filename(url: str, stamp: str | None = None) -> str:
    """``<host>-<YYYYmmdd-HHMMSS>.txt``；host 里的非法字符压成下划线。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1].split(":")[0] or "unknown"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", host).strip("._-") or "unknown"
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{safe}-{stamp}.txt"


def manifest_entry(
    result: dict[str, Any],
    policy: FetchPolicy,
    snippet_count: int,
) -> dict[str, Any]:
    """按契约拼一行 manifest：url/status/bytes/encoding/sha256/fetched_at/snippet_count/robots/policy。"""
    robots = result.get("robots") or {}
    return {
        "url": result.get("url"),
        "status": result.get("status"),
        "bytes": result.get("bytes", 0),
        "encoding": result.get("encoding", ""),
        "sha256": sha256_text(result.get("text", "")),
        "fetched_at": result.get("fetched_at"),
        "snippet_count": snippet_count,
        "robots": {
            "allowed": robots.get("allowed"),
            "reason": robots.get("reason", ""),
            "checked": robots.get("checked", False),
            "status": robots.get("status"),
            "robots_url": robots.get("robots_url"),
        },
        "policy": policy.as_dict(),
    }


__all__ = [
    "DEFAULT_USER_AGENT",
    "FetchPolicy",
    "clear_robots_cache",
    "decode_body",
    "fetch_many",
    "fetch_page",
    "filter_lines",
    "format_plan",
    "html_to_text",
    "manifest_entry",
    "plan_fetch",
    "raw_filename",
    "robots_verdict",
    "sha256_text",
    "split_snippets",
    "strip_ads",
]
