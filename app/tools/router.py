"""工具路由：按"话术 + 附件"确定性地决定调哪个工具，再把结果喂回 prompt。

为什么不让模型自己 function-call：
1. 主链已经 20 步，再加一轮模型调用 = 延迟翻倍、成本翻倍；
2. 国产兼容端点对 `tools` 参数的支持参差不齐，行为不可控；
3. 我们要的其实只有三件事——"查一下"、"读这个链接"、"看这张图"，
   话术规则足够准，而且**可测**（见 adv_regress 的 D26–D28）。

产出是一段中文提示（【刚查到的】），模型只负责把它说成人话。
﻿‌‌﻿﻿﻿﻿​‍‍​﻿‌‌﻿‍​​​‌﻿​‌​‍﻿‍‍​​​﻿​﻿"""

from __future__ import annotations

import asyncio
import re
from typing import Any

_URL_RE = re.compile(r"https?://[^\s，。；！？、）)\]】\"']+")
# 明确的"去查一下"话术：宁可漏判（就当普通聊天），也别把每句话都送去搜索
_SEARCH_RE = re.compile(
    r"(?:帮我|给我|你)?(?:查一下|查查|查一查|搜一下|搜搜|搜一搜|搜索一下|上网查|去查|百度一下)"
)
_FACT_RE = re.compile(r"(?:是什么|是谁|多少钱|怎么用|什么梗|什么意思|最新消息|现在怎么样了)")

_MAX_HINT_CHARS = 1200
_MAX_STEPS = 2


def plan(text: str, attachments: list[dict[str, Any]] | None = None, *, cfg: Any = None) -> list[dict[str, Any]]:
    """返回要执行的工具步骤：[{"tool": name, "args": {...}, "why": "…"}]。

    纯函数、不联网、不抛：测试直接断言它。
    """
    body = (text or "").strip()
    steps: list[dict[str, Any]] = []

    if cfg is None:
        from app.core import config as config_mod

        cfg = config_mod.get()

    tools_cfg = getattr(cfg, "tools", None)
    vision_cfg = getattr(tools_cfg, "vision", None)
    web_cfg = getattr(tools_cfg, "web", None)

    # 1) 有图 → 先看图（图比文字更可能是这轮的重点）
    imgs = [str((a or {}).get("url") or "") for a in (attachments or [])
            if str((a or {}).get("type") or "image") == "image"]
    imgs = [x for x in imgs if x]
    if imgs and bool(getattr(vision_cfg, "enabled", True)):
        max_images = max(1, int(getattr(vision_cfg, "max_images", 2) or 2))
        steps.append({
            "tool": "image_describe",
            "args": {"images": imgs[:max_images], "question": body[:60]},
            "why": "attachments",
        })

    # 2) 话里带链接 → 读那个链接（只读第一个，别连环抓）
    links = _URL_RE.findall(body)
    if links and bool(getattr(web_cfg, "enabled", True)):
        steps.append({"tool": "web_fetch", "args": {"url": links[0]}, "why": "url-in-text"})

    # 3) 明确的"查一下" → 搜索。太短/纯情绪/已经有链接时不搜
    if not links and bool(getattr(web_cfg, "enabled", True)) and len(body) <= 80:
        query = _query_of(body)
        if query and (("查" in body or "搜" in body) or _FACT_RE.search(body)):
            steps.append({"tool": "web_search", "args": {"query": query}, "why": "search-intent"})

    return steps[:_MAX_STEPS]


def _query_of(text: str) -> str:
    """把"帮我查一下 XXX 是什么"削成能搜的 XXX。"""
    q = _SEARCH_RE.sub(" ", text)
    q = re.sub(r"^(?:帮我|给我|你|麻烦|请问|那个|这个)+", " ", q)
    q = re.sub(r"[？?！!。，,、；;：:\s]+", " ", q)
    q = re.sub(r"(?:是什么|是谁|多少钱|怎么用|什么梗|什么意思|吗|呢|啊|呀)$", "", q.strip())
    return q.strip()[:40]


def _format(results: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for res in results:
        if not res.get("ok"):
            continue
        name = res.get("name")
        if name == "web_search":
            items = res.get("items") or []
            lines = [f"· {i.get('title','')}：{i.get('snippet','')}"[:120] for i in items[:3]]
            if lines:
                parts.append(f"搜「{res.get('query','')}」的结果：\n" + "\n".join(lines))
        elif name == "web_fetch":
            text = str(res.get("text") or "")[:400]
            if text:
                parts.append(f"网页 {res.get('url','')} 的正文：\n{text}")
        elif name == "image_describe":
            text = str(res.get("text") or "")
            if text:
                parts.append(f"图里的内容：\n{text}")
    return "\n\n".join(parts)[:_MAX_HINT_CHARS]


def hint_sync(cfg: Any, text: str, attachments: list[dict[str, Any]] | None = None) -> str:
    """同步版：跑完 plan 里的工具，拼成一段提示。失败就返回空串。"""
    try:
        steps = plan(text, attachments, cfg=cfg)
    except Exception:  # noqa: BLE001 - 规划失败就当没有工具
        return ""
    if not steps:
        return ""

    from app.tools import registry

    out: list[dict[str, Any]] = []
    for step in steps:
        res = registry.run(step["tool"], step["args"], cfg=cfg)
        if res.get("ok"):
            out.append(res)
    return _format(out)


async def hint(rt: Any, text: str, attachments: list[dict[str, Any]] | None = None) -> str:
    """异步版：主链用这个。真出网时丢线程跑，别卡事件循环。"""
    cfg = getattr(rt, "cfg", rt)
    from app.tools import registry

    try:
        steps = plan(text, attachments, cfg=cfg)
    except Exception:  # noqa: BLE001
        return ""
    if not steps:
        return ""
    # 先看闸：mock / 关掉工具时一步都不跑，也不起线程
    ok_kinds = {str(step["tool"]): ("vision" if step["tool"] == "image_describe" else "web")
                for step in steps}
    if not any(registry.gate(cfg, kind)[0] for kind in ok_kinds.values()):
        return ""
    return await asyncio.to_thread(hint_sync, cfg, text, attachments)


__all__ = ["plan", "hint", "hint_sync"]
