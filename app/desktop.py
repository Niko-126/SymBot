"""独立桌面应用入口。

做三件事：
1. 后台起内核（uvicorn）
2. 等端口就绪
3. 开一个原生窗口（Edge WebView2），不带浏览器地址栏

运行：
    python -m app.desktop
无界面自测：
    python -m app.desktop --no-gui
﻿‌‍‌‍‌​﻿‌﻿﻿‌​​‍﻿﻿‌‌​​‌​​‌﻿‍﻿‌‍​﻿‌﻿"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

from app.core import runtime


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _wait_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _serve(host: str, port: int) -> None:
    import uvicorn

    from app.main import app as asgi_app

    # 打包后不能按字符串导入模块，直接把 app 对象交给 uvicorn
    uvicorn.run(asgi_app, host=host, port=port, log_level="warning")


def _redirect_logs() -> None:
    """打包后没有控制台，stdout/stderr 写到 data/app.log。"""
    import io
    import os

    if not getattr(sys, "frozen", False):
        return
    from app.core.paths import DATA_ROOT

    try:
        (DATA_ROOT / "data").mkdir(parents=True, exist_ok=True)
        f = open(DATA_ROOT / "data" / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = f  # type: ignore[assignment]
        sys.stderr = f  # type: ignore[assignment]
        _ = io, os
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("衡忆 desktop")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-gui", action="store_true", help="只起服务，不开窗口（自测用）")
    args = parser.parse_args(argv)

    _redirect_logs()
    rt = runtime.init()
    host = "127.0.0.1"
    port = args.port

    if not _port_free(host, port):
        print(f"端口 {port} 被占用。可能已经有一个实例在运行。", file=sys.stderr)
        return 1

    threading.Thread(target=_serve, args=(host, port), daemon=True).start()
    if not _wait_port(host, port):
        print("内核没有起来，检查日志。", file=sys.stderr)
        return 1

    mode = "mock（未配置 API Key，回复为本地模拟）" if rt.cfg.provider.is_mock else rt.cfg.provider.model
    print(f"内核已启动：http://{host}:{port}/　模型：{mode}")

    if args.no_gui:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    try:
        import webview  # type: ignore
    except ImportError:
        print("没有安装 pywebview，无法开窗口。安装：pip install pywebview", file=sys.stderr)
        print(f"可以先用浏览器打开 http://{host}:{port}/", file=sys.stderr)
        return 1

    class Api:
        """给页面里的自定义标题栏用：最小化 / 关闭。"""

        def minimize(self) -> None:
            try:
                window.minimize()
            except Exception:  # noqa: BLE001
                pass

        def close(self) -> None:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

    window = webview.create_window(
        f"{rt.persona.name} · 衡忆",
        f"http://{host}:{port}/",
        width=1180,
        height=800,
        x=90,
        y=70,
        min_size=(960, 640),
        frameless=True,
        easy_drag=True,
        text_select=True,
        js_api=Api(),
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
