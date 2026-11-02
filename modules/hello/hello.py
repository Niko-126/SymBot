"""示例模块：注册一个工具 + 一个路由，用来验收热插拔。

照这个文件抄就行：

1. 顶层随便写，import 时就会执行（别在这里做慢操作，更别起线程）；
2. 必须暴露 `register(api)`，内核调它一次；
3. 想挂什么就 `api.register_tool / register_router / register_hook`。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/v1/modules/hello", tags=["module"])


def hello_ping() -> dict[str, Any]:
    """模块工具示例：回一个 pong 和当前时间戳。"""
    return {"ok": True, "pong": time.time()}


@router.get("/ping")
def route_ping() -> dict[str, Any]:
    """路由示例：`GET /v1/modules/hello/ping`。"""
    return {"ok": True, "module": "hello", "ts": time.time()}


def register(api: Any) -> None:
    api.register_tool("hello_ping", hello_ping, desc="示例工具：回一个 pong 和时间戳。")
    api.register_router(router)
