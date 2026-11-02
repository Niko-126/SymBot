"""启动预检：`base_url` 与 `model/force_model` 必须同源。

为什么要有它（2026-10-31 实测踩过）：
    base_url = https://api.deepseek.com/v1
    force_model = deepseek-ai/DeepSeek-V4.1-Flash   ← 硅基流动的名字
→ 官方 API 直接 400，`loop.py` 的兜底把异常吞掉、改说"先去吃口饭"，
   40 条回复全是罐头，面板上看起来只是"她在忙"。
预检不做网络请求（零 token），只按已知 provider 的命名规则判断；
结果写进 `/v1/health.preflight`，不匹配时启动日志打 WARNING。
﻿﻿​​‍​‌​﻿​‌‌‍‌​​﻿​‌‍‍﻿​‌‍‌‌‌‌‍‌​‍﻿"""

from __future__ import annotations

import datetime as _dt
from typing import Any

#: DeepSeek 官方 API 接受的模型名（2026-10 实测：其它名字一律 400）
_DEEPSEEK_OFFICIAL = ("deepseek-flash", "deepseek-chat", "deepseek-reasoner")


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def check(cfg: Any) -> dict[str, Any]:
    """返回 {ok, base_url, model, force_model, warnings, checked_at}；不抛。"""
    provider = getattr(cfg, "provider", None)
    base = str(getattr(provider, "base_url", "") or "").strip().lower()
    model = str(getattr(provider, "model", "") or "")
    force = str(getattr(provider, "force_model", "") or "")
    warnings: list[str] = []

    def _judge(name: str, where: str) -> None:
        if not name:
            return
        if "api.deepseek.com" in base:
            if "/" in name or name not in _DEEPSEEK_OFFICIAL:
                warnings.append(
                    f"{where}={name} 不是 DeepSeek 官方模型名（官方只认 "
                    + " / ".join(_DEEPSEEK_OFFICIAL) + "）"
                )
        elif "siliconflow" in base:
            if "/" not in name:
                warnings.append(
                    f"{where}={name} 不像硅基流动的模型名（通常形如 deepseek-ai/DeepSeek-V4.1-Flash）"
                )

    _judge(model, "model")
    if force and force != model:
        _judge(force, "force_model")

    # 控制台暴露面：token 为空时控制台接口不鉴权，只允许绑回环地址。
    # 面板里全是私人对话与她的记忆，绑到 0.0.0.0 等于把它们给同网段任何人看。
    host = str(getattr(getattr(cfg, "server", None), "host", "") or "").strip().lower()
    token = str(getattr(getattr(cfg, "console", None), "token", "") or "").strip()
    if not token and host not in ("127.0.0.1", "localhost", "::1", ""):
        warnings.append(
            f"控制台没有 token 却绑定在 {host}：任何能访问这个端口的人都能读写她的记忆/关系/会话。"
            "要么绑回 127.0.0.1，要么设 SYMEM_CONSOLE_TOKEN"
        )

    return {
        "ok": not warnings,
        "base_url": base,
        "model": model,
        "force_model": force,
        "warnings": warnings,
        "checked_at": _now(),
    }
