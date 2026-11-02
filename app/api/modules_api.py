"""外置模块的接口（面板「模块」页用）。

挂在 `/v1/console/modules*`。鉴权口径和 `app/api/console.py` 完全一致：
`console.token` 配了就必须带 `X-Console-Token`（或 `?token=`），没配只靠回环地址。

**故意不 import `console._console_auth`**：两个 router 互相 import 会绕成环
（console 迟早要 import 本模块的 registry 来展示工具），这 8 行就地抄一份更省事。
﻿​‌‍‍‌‌‌​​‍﻿﻿﻿‌﻿​‍﻿‍​‌‌​‍﻿‌‍‍‌‌﻿​﻿"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import modules as modules_mod
from app.core.runtime import get as get_runtime


def _console_auth(request: Request) -> None:
    """配了口令就校验；没配就只靠回环地址（同 console.py）。"""
    token = str(getattr(get_runtime().cfg.console, "token", "") or "")
    if not token:
        return
    given = (
        request.headers.get("x-console-token")
        or request.query_params.get("token")
        or ""
    )
    if not hmac.compare_digest(str(given), token):
        raise HTTPException(status_code=401, detail="控制台口令不对")


router = APIRouter(prefix="/v1/console", dependencies=[Depends(_console_auth)])


class ToggleIn(BaseModel):
    enabled: bool


def _find(module_id: str) -> modules_mod.ModuleInfo | None:
    for info in modules_mod.discover():
        if info.id == module_id:
            return info
    return None


@router.get("/modules")
def list_modules(request: Request) -> dict[str, Any]:
    """模块清单。启动时没接线的话，第一次请求这里就补加载（不抛）。"""
    modules_mod.ensure_loaded(request.app)
    return modules_mod.snapshot()


@router.post("/modules/reload")
def reload_modules(request: Request) -> dict[str, Any]:
    """重新扫目录 + 重新 import，热生效；不需要重启内核。"""
    return modules_mod.reload(app=request.app)


@router.post("/modules/{module_id}/toggle")
def toggle_module(module_id: str, body: ToggleIn, request: Request) -> dict[str, Any]:
    """停用/启用一个模块：写 kv，然后立刻重载，让工具和路由当场上下架。"""
    if _find(module_id) is None:
        raise HTTPException(status_code=404, detail=f"没有这个模块：{module_id}")
    try:
        modules_mod.set_enabled(module_id, bool(body.enabled))
    except Exception as exc:  # noqa: BLE001 - 库写不进去，如实报
        raise HTTPException(status_code=500, detail=f"写启用状态失败：{exc}") from exc
    modules_mod.reload(app=request.app)
    info = modules_mod.registry().modules.get(module_id)
    return {
        "ok": True,
        "id": module_id,
        "enabled": modules_mod.is_enabled(module_id),
        "module": info.to_dict() if info is not None else None,
    }


__all__ = ["router"]
