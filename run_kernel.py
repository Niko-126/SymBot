"""SymBot 内核入口（无控制台，给 Electron 起调用）。

和 `app.desktop` 的区别：这里只起 HTTP 内核，不碰 pywebview，也不解析 `--xxx` 参数。
Electron 的 `desktop/main.js` 是这么起的：

    spawn(kernelExe, [String(PORT)], { cwd: <安装根>, env: { SYMEM_DATA: <安装根> } })

所以第一个位置参数就是端口；不带参数时退回配置里的 `server.port`（默认 8787）。
"""

from __future__ import annotations

import os
import sys


def _quiet_stdio() -> None:
    """--noconsole 下 sys.stdout/stderr 是 None，任何 print 都直接抛异常。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _port() -> int:
    """面板传的是裸端口（`SymBotKernel.exe 8787`）；也认 `--port 8787` 这种写法。"""
    argv = [a for a in sys.argv[1:]]
    for i, arg in enumerate(argv):
        if arg in ("--port", "-p") and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                break
        if arg.startswith("--port="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                break
    for arg in argv:
        if arg.isdigit():
            return int(arg)
    return 0


def main() -> int:
    _quiet_stdio()
    import uvicorn

    from app.core import runtime

    rt = runtime.init()
    try:
        port = _port()
    except (TypeError, ValueError):
        port = 0
    if not port:
        port = int(getattr(rt.cfg.server, "port", 8787) or 8787)
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
