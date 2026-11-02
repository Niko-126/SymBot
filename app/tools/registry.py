"""工具注册表：她能用的"手"。

- 联网工具：`web_search`（查资料）、`web_fetch`（读链接）
- 识图工具：`image_describe`（看用户发来的图）

三条约定：
1. **所有执行都走 `run()`**：它负责兜异常，并守住"mock 模式永不出网"这道闸。
2. `SPECS` 只是目录（名字/说明/参数），不含任何密钥，可以安全地给面板或模型看。
3. 工具失败**永远不抛给主链**——返回 `{"ok": False, "reason": …}`，主链当没查到处理。
﻿‍‌‍‌‍‍‍‌‌‍​‌‌‍‍﻿‌‍‍‍​‍﻿﻿﻿‍​‌‍‌‌﻿﻿"""

from __future__ import annotations

from typing import Any

SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "web_search",
        "kind": "web",
        "desc": "联网搜索：查一个词或一件事的公开资料，返回标题 + 摘要。",
        "params": {"query": "要查的内容", "limit": "条数（默认取配置）"},
    },
    {
        "name": "web_fetch",
        "kind": "web",
        "desc": "抓一个网页的正文（去标签、按配置截断），用来读链接里的内容。",
        "params": {"url": "http(s) 链接"},
    },
    {
        "name": "image_describe",
        "kind": "vision",
        "desc": "看一张图并客观描述：谁、在哪、在做什么、图里的文字。",
        "params": {"images": "图片地址（http/data/本地路径）列表", "question": "想问的问题，可空"},
    },
)

_BY_NAME = {s["name"]: s for s in SPECS}


def specs() -> list[dict[str, Any]]:
    """工具目录（副本，调用方改不到 SPECS）。"""
    return [dict(s) for s in SPECS]


def is_mock(cfg: Any) -> bool:
    try:
        return bool(cfg.provider.is_mock)
    except AttributeError:
        return True


def gate(cfg: Any, kind: str) -> tuple[bool, str]:
    """这道闸决定"现在能不能真的出网"。返回 (可用?, 原因)。"""
    tools = getattr(cfg, "tools", None)
    if tools is None or not bool(getattr(tools, "enabled", False)):
        return False, "tools-disabled"
    if is_mock(cfg):
        # mock = 测试/无 key：一个字节都不许出网，也不许烧钱
        return False, "mock"
    if kind == "web" and not bool(getattr(getattr(tools, "web", None), "enabled", False)):
        return False, "web-disabled"
    if kind == "vision" and not bool(getattr(getattr(tools, "vision", None), "enabled", False)):
        return False, "vision-disabled"
    return True, ""


def run(name: str, args: dict[str, Any] | None = None, *, cfg: Any = None) -> dict[str, Any]:
    """执行一个工具。永不抛：失败统一返回 {"ok": False, "reason": …}。"""
    spec = _BY_NAME.get(str(name))
    if spec is None:
        return {"ok": False, "name": str(name), "reason": "unknown-tool"}

    if cfg is None:
        from app.core import config as config_mod

        cfg = config_mod.get()

    ok, why = gate(cfg, str(spec["kind"]))
    if not ok:
        return {"ok": False, "name": str(name), "reason": why}

    args = args or {}
    try:
        if name == "web_search":
            from app.tools import web as web_mod

            return web_mod.search(
                str(args.get("query") or ""),
                limit=int(args.get("limit") or cfg.tools.web.max_results),
                cfg=cfg,
            )
        if name == "web_fetch":
            from app.tools import web as web_mod

            return web_mod.fetch(str(args.get("url") or ""), cfg=cfg)
        if name == "image_describe":
            from app.tools import vision as vision_mod

            return vision_mod.describe(
                [str(x) for x in (args.get("images") or []) if str(x).strip()],
                question=str(args.get("question") or ""),
                cfg=cfg,
            )
        return {"ok": False, "name": str(name), "reason": "not-implemented"}
    except Exception as exc:  # noqa: BLE001 - 工具失败不许拖垮主链
        return {"ok": False, "name": str(name), "reason": f"{type(exc).__name__}: {exc}"[:160]}


__all__ = ["SPECS", "gate", "is_mock", "run", "specs"]
