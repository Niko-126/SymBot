"""隔离沙箱：临时数据根 + mock provider + 断网 + 密钥遮罩。

**必须在 import app.* 之前调用 `prepare_env()`** —— `app/core/paths.py` 在 import 时就把
`SYMEM_DATA` 读死了，晚一步设置就等于没设（见 AI接管手册 §2.6 / §4.6）。

用途：让"任何函数都敢调"成为可能——真库不动，网络不通，模型走 mock，不花钱。
﻿‌‍‍﻿‍​​‌​‍​‍​﻿‌﻿﻿‍‌‌﻿﻿‌‌﻿‌‍‌‍‌‍‌﻿"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import shutil
import sys
import tempfile

MARKER = "app.core.paths"

_MASK_RULES: list[tuple[re.Pattern[str], str]] = [
    # 裸 token：整个替换掉，不能"保留原文再追加 ***"
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\.=]{12,}"), r"\1 ***"),
    # key = value 形式：只留键名
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?token|app[_-]?token|secret|password|passwd|token)\"?\s*[:=]\s*\"?)"
            r"([A-Za-z0-9_\-\.=/+]{8,})"
        ),
        r"\1***",
    ),
    # appid 这类纯数字凭据
    (re.compile(r"(?i)(appid\"?\s*[:=]\s*\"?)(\d{6,})"), r"\1***"),
    # 明确标注的账号字段（self_id / openid / user_id / qq 号）；openid 可能是十六进制串
    (
        re.compile(
            r"(?i)((?:self_id|openid|user_id|group_id|qq)\"?\s*[:=]\s*\"?)([A-Za-z0-9_\-]{5,})"
        ),
        r"\1***",
    ),
]


class SandboxError(RuntimeError):
    pass


class NetworkBlocked(RuntimeError):
    pass


def prepare_env(tag: str = "adv", *, keep: bool = True) -> pathlib.Path:
    """建临时数据根并把环境变量钉死；返回临时目录。"""
    if MARKER in sys.modules:
        raise SandboxError(
            "prepare_env() 调用太晚：app.core.paths 已 import，SYMEM_DATA 已定死。"
            "所有 phase 脚本必须在 import app.* 之前调用它。"
        )
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"smd_adv_{tag}_"))
    env = {
        "SYMEM_DATA": str(tmp),
        "SYMEM_DB": str(tmp / "life.db"),
        "SYMEM_MOCK": "1",              # config.py:242 只认 "1"
        "SYMEM_QQ_ENABLED": "0",
        "SYMEM_ONEBOT_ENABLED": "0",
        "SYMEM_CONSOLE_TOKEN": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    os.environ.update(env)
    os.environ.setdefault("SYMEM_ADV_KEEP", "1" if keep else "0")
    return tmp


def env_report() -> dict[str, str]:
    keys = [k for k in os.environ if k.startswith(("SYMEM_", "AI_LIFE_", "PYTHONIOENCODING"))]
    return {k: os.environ[k] for k in sorted(keys)}


def reset_db() -> None:
    """清掉临时库（同一进程内重复 phase 之间用；不碰真库）。"""
    root = pathlib.Path(os.environ.get("SYMEM_DATA", ""))
    db = pathlib.Path(os.environ.get("SYMEM_DB", ""))
    if not root or not str(root).startswith(str(pathlib.Path(tempfile.gettempdir()))):
        raise SandboxError(f"拒绝清理非临时目录：{root}")
    for p in (db, pathlib.Path(str(db) + "-wal"), pathlib.Path(str(db) + "-shm")):
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


def cleanup(tmp: pathlib.Path) -> None:
    """按需删除临时目录（默认保留，方便复现）。"""
    if os.environ.get("SYMEM_ADV_KEEP", "1") != "0":
        return
    shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def network_guard(*, allow_loopback: bool = True):
    """拦掉一切外网访问；命中抛 NetworkBlocked 并记一条证据。

    只 patch 传输层（socket.connect / httpx 传输 / urllib），不动 app 代码。
    """
    import socket

    original_conn = socket.socket.connect
    original_conn_ex = socket.socket.connect_ex
    blocked: list[str] = []

    def _is_local(addr) -> bool:
        try:
            host = addr[0] if isinstance(addr, (tuple, list)) else str(addr)
        except Exception:  # noqa: BLE001
            return False
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0")

    def _guard(addr):
        if allow_loopback and _is_local(addr):
            return
        blocked.append(str(addr))
        raise NetworkBlocked(f"network blocked: {addr}")

    def connect(self, addr):
        _guard(addr)
        return original_conn(self, addr)

    def connect_ex(self, addr):
        try:
            _guard(addr)
        except NetworkBlocked:
            return 111
        return original_conn_ex(self, addr)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

    # httpx：拦 send（很多模块直接用 httpx.Client/AsyncClient）
    patched = []
    try:
        import httpx

        for cls in (httpx.Client, httpx.AsyncClient):
            orig = cls.send

            def make(orig):
                def send(self, request, *a, **kw):
                    url = str(getattr(request, "url", ""))
                    if allow_loopback and ("127.0.0.1" in url or "localhost" in url):
                        return orig(self, request, *a, **kw)
                    blocked.append(url)
                    raise NetworkBlocked(f"network blocked: {url}")

                return send

            cls.send = make(orig)
            patched.append((cls, orig))
    except Exception:  # noqa: BLE001
        pass

    try:
        yield blocked
    finally:
        socket.socket.connect = original_conn
        socket.socket.connect_ex = original_conn_ex
        for cls, orig in patched:
            cls.send = orig


def mask(text: str, *, limit: int = 4000) -> str:
    """把疑似密钥/账号打成 ***；用于写进报告/转录前。

    注意：替换必须是**剥离**（token 本体不能留在结果里），不是追加后缀。
    """
    s = str(text)[:limit]
    for pat, repl in _MASK_RULES:
        s = pat.sub(repl, s)
    return s


def summarize_paths() -> dict[str, str]:
    """延后调用：import 完 app.core.paths 后给报告用。"""
    from app.core import paths

    return {
        "RESOURCE_ROOT": str(paths.RESOURCE_ROOT),
        "DATA_ROOT": str(paths.DATA_ROOT),
        "frozen": str(getattr(paths, "FROZEN", False)),
    }
