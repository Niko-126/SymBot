"""服务控制：看和管三个常驻服务（内核 / NapCat / 语音）。

为什么单独一个模块、而不是全塞进 `app/api/console.py`：
面板接口只是壳；"端口是多少、进程是谁、日志在哪、怎么重启"这些是内核自己的常识，
启动器（scripts/start_panel.py）将来也要复用同一套判断。接口层只做参数校验和转发。

三条铁律（都是真机踩出来的）：
1. **状态一律现探**：端口 + HTTP 探活 + `get_login_info`，绝不读缓存。缓存会让面板
   在你杀了进程之后还显示绿灯。
2. **重启内核不能杀自己再指望自己回来**：先回 200，再让一个 detached 的 PowerShell
   睡 2 秒、杀掉老 pid、重新派生 `python -m app.cli serve`。接口必须立刻返回。
3. **TTS 不许弹黑框**：`CREATE_NO_WINDOW` + stdout/stderr 重定向到
   `data/voice_service.log`，面板只看这个文件。
﻿﻿‍​﻿﻿​﻿‍‌‍﻿​​‌‍﻿﻿​‌‌‌​​﻿‍‍​﻿‌​‌﻿﻿"""

from __future__ import annotations

import ctypes
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

from app.core.paths import BUNDLE_ROOT, DATA_ROOT, RESOURCE_ROOT, candidate_roots

#: 内核进程启动时刻（本模块随内核一起 import，等价于"内核启动时间"）
_KERNEL_BOOT_TS = time.time()
#: 内核进程的启动命令（重启时照着再来一遍）；app.cli 会再解析 --port
_KERNEL_ARGV = ["-m", "app.cli", "serve"]
#: 语音服务日志（面板只看它）
VOICE_LOG = pathlib.Path(DATA_ROOT) / "voice_service.log"
#: 语音服务默认端口（跟 voice.base_url 对齐，配置改了以配置为准）
VOICE_DEFAULT_PORT = 9880
#: NapCat OneBot v11 默认端口
NAPCAT_DEFAULT_PORT = 3000

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)


def _cfg() -> Any:
    from app.core.runtime import get as get_runtime

    return get_runtime().cfg


# ---------------------------------------------------------------- 通用探测


def _port_open(port: int, timeout: float = 0.6) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _url_port(url: str, fallback: int) -> int:
    try:
        return int(urlparse(str(url)).port or fallback)
    except (ValueError, TypeError):
        return fallback


def _alive(pid: int) -> bool:
    """Windows 下判断进程还在不在（os.kill(pid, 0) 在 Windows 上不可靠）。"""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    process_query_limited = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited, False, int(pid))
    if not handle:
        return False
    # 再确认不是"已退出但句柄还挂着"
    code = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok) and int(code.value) == 259  # STILL_ACTIVE


def _pid_on_port(port: int) -> int:
    """谁在监听这个端口（netstat 解析，不引 psutil）。

    `.stdout or ""` 不是洁癖：真机上刚重启内核时会遇到 stdout 为 None 的怪状态
    （探测进程被外部抢先回收），直接 splitlines 会 500 掉整个服务卡片。
    """
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, check=False,
            creationflags=_NO_WINDOW, timeout=8,
        ).stdout or ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0
    needle = f":{int(port)}"
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or "LISTENING" not in line.upper():
            continue
        if needle not in parts[1]:
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return 0


def _kill(pid: int) -> bool:
    if not _alive(pid):
        return True
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(int(pid))],
            capture_output=True, check=False, creationflags=_NO_WINDOW, timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _iso(ts: float | None = None) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts or time.time()).astimezone().isoformat(timespec="seconds")


def _kv_set(key: str, value: str) -> None:
    try:
        from app.core import db

        db.kv_set(key, value)
    except Exception:  # noqa: BLE001 - 记账失败不该挡住控制
        pass


def _kv_get(key: str, default: str = "") -> str:
    try:
        from app.core import db

        return str(db.kv_get(key, default) or default)
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------- 内核


def kernel_status(port: int | None = None) -> dict[str, Any]:
    cfg = _cfg()
    port = int(port or getattr(cfg.server, "port", 8787) or 8787)
    uptime = max(0.0, time.time() - _KERNEL_BOOT_TS)
    return {
        "up": True,          # 能回答这个接口，说明内核当然在跑
        "pid": os.getpid(),
        "port": port,
        "uptime_s": round(uptime, 1),
        "started_at": _iso(_KERNEL_BOOT_TS),
        "python": sys.executable,
        "root": str(RESOURCE_ROOT),
    }


def _restart_script(pid: int, port: int) -> pathlib.Path:
    """生成"延迟重启"脚本：睡 2 秒 → 杀老 pid → 重新起 serve。

    为什么是脚本而不是内联 `-Command`：路径里有中文/空格时，引号嵌套是灾难；
    落成一个 .ps1 再 `-File` 执行，参数边界清楚，也不会被 `cmd /c` 再解释一遍。

    为什么每一步都写日志：第一次真机验证时脚本"看起来跑了但没杀成"，
    没有日志就完全瞎猜（2026-11-02 踩的）。日志在 data/restart_kernel.log。
    """
    data = pathlib.Path(DATA_ROOT)
    data.mkdir(parents=True, exist_ok=True)
    script = data / "restart_kernel.ps1"
    log = data / "restart_kernel.log"
    python = sys.executable
    out = data / "last_serve.log"
    err = data / "last_serve.err.log"
    body = f"""# 由面板「重启内核」生成（旧 pid {int(pid)} / 端口 {int(port)}）
$ErrorActionPreference = 'Continue'
$logFile = '{log}'
function Say($m) {{ "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $m" | Out-File -FilePath $logFile -Append -Encoding utf8 }}
Say 'restart script start'
Start-Sleep -Seconds 2
try {{
  Stop-Process -Id {int(pid)} -Force -ErrorAction Stop
  Say 'old process killed'
}} catch {{
  Say "kill failed: $($_.Exception.Message)"
}}
Start-Sleep -Milliseconds 1500
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try {{
  Start-Process -FilePath '{python}' `
    -ArgumentList @('-m','app.cli','serve','--port','{int(port)}') `
    -WorkingDirectory '{pathlib.Path(RESOURCE_ROOT)}' `
    -WindowStyle Hidden `
    -RedirectStandardOutput '{out}' `
    -RedirectStandardError '{err}'
  Say 'new process spawned'
}} catch {{
  Say "spawn failed: $($_.Exception.Message)"
}}
Say 'restart script done'
"""
    script.write_text(body, encoding="utf-8")
    return script


def restart_kernel(port: int | None = None, delay_s: float = 2.0) -> dict[str, Any]:
    """排一次"延迟重启"，**立刻返回**。真正的杀进程由 detached PowerShell 干。"""
    cfg = _cfg()
    port = int(port or getattr(cfg.server, "port", 8787) or 8787)
    pid = os.getpid()
    script = _restart_script(pid, port)
    ps = shutil.which("powershell") or str(
        pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    # 用 CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP，**不要** DETACHED_PROCESS：
    # 真机实测（2026-11-02）DETACHED 下 powershell.exe 会立刻以 rc=0 退出、脚本一步都不跑；
    # 而 NO_WINDOW 的 PowerShell 在内核被杀之后照样活着（Windows 不会因为父进程死了杀子进程），
    # 足够把新内核拉起来。
    flags = _NO_WINDOW | _NEW_GROUP
    try:
        proc = subprocess.Popen(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(RESOURCE_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        return {"ok": False, "target": "kernel", "action": "restart",
                "reason": f"派生重启脚本失败：{exc}"}
    time.sleep(0.25)  # 给 PowerShell 一点时间真的起来（只是确认它没秒退，不影响接口延迟）
    if not _alive(proc.pid):
        tail = _read_tail(pathlib.Path(DATA_ROOT) / "restart_kernel.log", 6)
        return {"ok": False, "target": "kernel", "action": "restart",
                "reason": "重启脚本没起来（PowerShell 立刻退出了）", "log": tail}
    _kv_set("kernel_restart_at", _iso())
    return {
        "ok": True,
        "target": "kernel",
        "action": "restart",
        "scheduled": True,
        "delay_s": float(delay_s),
        "old_pid": pid,
        "new_pid": proc.pid,
        "port": port,
        "note": "2 秒后旧进程退出、新进程起来；面板会在 5–15 秒内自己恢复",
    }


def _read_tail(path: pathlib.Path, n: int = 6) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


# ---------------------------------------------------------------- NapCat


def _napcat_dir() -> pathlib.Path | None:
    """NapCat 根目录：环境变量 > 配置 > 不猜。"""
    from app.core import napcat_webui

    root, _ = napcat_webui.webui_paths()
    return root


def napcat_status() -> dict[str, Any]:
    cfg = _cfg()
    base = str(getattr(cfg.onebot, "api_base", "") or "")
    port = _url_port(base, NAPCAT_DEFAULT_PORT)
    up = _port_open(port)
    out: dict[str, Any] = {
        "up": up,
        "port": port,
        "api_base": base.rstrip("/"),
        "enabled": bool(getattr(cfg.onebot, "enabled", False)),
        "logged_in": False,
        "uin": "",
        "nickname": "",
        "online": False,
        "version": "",
        "protocol": "",
        "dir": str(_napcat_dir() or ""),
        "reason": "" if up else f"{port} 端口没开：NapCat 没起，或者小号没登录",
    }
    if up:
        try:
            import httpx

            with httpx.Client(timeout=3, trust_env=False) as client:
                head = {"Authorization": f"Bearer {cfg.onebot.access_token}"} if getattr(cfg.onebot, "access_token", "") else {}
                st = client.post(f"{base.rstrip('/')}/get_status", json={}, headers=head)
                st_data = (st.json().get("data") or {}) if st.content else {}
                out["online"] = bool(st_data.get("online"))
                out["good"] = bool(st_data.get("good"))
                info = client.post(f"{base.rstrip('/')}/get_login_info", json={}, headers=head)
                info_data = (info.json().get("data") or {}) if info.content else {}
                if info_data.get("user_id"):
                    out["logged_in"] = True
                    out["uin"] = str(info_data.get("user_id"))
                    out["nickname"] = str(info_data.get("nickname") or "")
                ver = client.post(f"{base.rstrip('/')}/get_version_info", json={}, headers=head)
                ver_data = (ver.json().get("data") or {}) if ver.content else {}
                out["version"] = str(ver_data.get("app_version") or "")
                out["protocol"] = str(ver_data.get("protocol_version") or "")
        except Exception as exc:  # noqa: BLE001 - 端口通了但接口抽风，如实说
            out["reason"] = f"端口通但接口没答：{type(exc).__name__}"
    # 顺带把 WebUI 的登录态带上（二维码模块要用，也方便判断"扫没扫"）
    try:
        from app.core import napcat_webui

        webui = napcat_webui.login_status()
        out["webui"] = {
            "available": bool(webui.get("webui")),
            "port": napcat_webui.webui_port(),
            "is_login": bool(webui.get("is_login")),
            "phase": str(webui.get("phase") or ""),
            "reason": str(webui.get("reason") or "") if not webui.get("webui") else "",
        }
        if not out["logged_in"] and webui.get("is_login"):
            out["logged_in"] = True
    except Exception:  # noqa: BLE001
        out["webui"] = {"available": False, "port": 0, "is_login": False, "phase": "", "reason": ""}
    return out


def _napcat_launcher() -> pathlib.Path | None:
    root = _napcat_dir()
    if root is None:
        return None
    for name in ("launcher.bat", "launcher-user.bat"):
        p = root / name
        if p.exists():
            return p
    return None


def stop_napcat() -> dict[str, Any]:
    """停 NapCat：优先 KillQQ.bat（作者的脚本会连注入进程一起清），否则 taskkill。"""
    root = _napcat_dir()
    killed: list[str] = []
    if root is not None and (root / "KillQQ.bat").exists():
        try:
            subprocess.run(
                ["cmd", "/c", str(root / "KillQQ.bat")],
                cwd=str(root), capture_output=True, check=False,
                creationflags=_NO_WINDOW, timeout=30,
            )
            killed.append("KillQQ.bat")
        except (OSError, subprocess.SubprocessError):
            pass
    if not killed:
        for name in ("QQ", "NapCatWinBootMain"):
            try:
                proc = subprocess.run(
                    ["taskkill", "/F", "/IM", f"{name}.exe"],
                    capture_output=True, text=True, check=False,
                    creationflags=_NO_WINDOW, timeout=15,
                )
                if proc.returncode == 0:
                    killed.append(f"{name}.exe")
            except (OSError, subprocess.SubprocessError):
                pass
    # 端口还通就是没杀干净（可能是另一个 QQ 实例）
    port = _url_port(str(getattr(_cfg().onebot, "api_base", "") or ""), NAPCAT_DEFAULT_PORT)
    deadline = time.time() + 8
    while time.time() < deadline and _port_open(port):
        time.sleep(0.4)
    ok = not _port_open(port)
    return {
        "ok": ok,
        "target": "napcat",
        "action": "stop",
        "killed": killed,
        "port": port,
        "reason": "" if ok else f"{port} 端口还在监听（可能有别的 QQ/NapCat 实例）",
    }


def start_napcat(qq: str = "", *, wait_s: float = 90.0) -> dict[str, Any]:
    """提权拉起 NapCat（会弹 UAC；用户点取消要如实回报，不能装成功）。"""
    cfg = _cfg()
    port = _url_port(str(getattr(cfg.onebot, "api_base", "") or ""), NAPCAT_DEFAULT_PORT)
    if _port_open(port):
        return {"ok": True, "target": "napcat", "action": "start",
                "already": True, "port": port, "reason": ""}
    launcher = _napcat_launcher()
    if launcher is None:
        return {"ok": False, "target": "napcat", "action": "start",
                "reason": "没配 NapCat 目录，或目录里没有 launcher.bat（设置 → 连接与账号）"}
    qq = str(qq or "").strip()
    if not qq:
        from app.core import config as config_mod

        launcher_cfg = getattr(config_mod.get(), "launcher", None)
        qq = str(getattr(launcher_cfg, "qq", "") or "").strip()
    if not qq:
        return {"ok": False, "target": "napcat", "action": "start",
                "reason": "没配小号 QQ（设置 → 连接与账号）"}

    # 残留 QQ.exe 不杀干净，注入必失败
    for name in ("QQ", "NapCatWinBootMain"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", f"{name}.exe"],
                           capture_output=True, check=False, creationflags=_NO_WINDOW, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    time.sleep(2.0)

    params = f'/c ""{launcher}" {qq}"'
    try:
        rc = int(ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", params, str(launcher.parent), 7
        ))
    except Exception as exc:  # noqa: BLE001 - 非 Windows / 没有 shell32
        return {"ok": False, "target": "napcat", "action": "start",
                "reason": f"提权调用失败：{type(exc).__name__}"}
    if rc <= 32:
        reason = "已取消 UAC（NapCat 没拉起）" if rc == 1223 else f"提权拉起失败（ShellExecuteW={rc}）"
        return {"ok": False, "target": "napcat", "action": "start", "reason": reason}

    deadline = time.time() + float(wait_s)
    while time.time() < deadline:
        if _port_open(port):
            return {"ok": True, "target": "napcat", "action": "start",
                    "port": port, "qq": qq, "waited_s": round(float(wait_s) - (deadline - time.time()), 1),
                    "reason": ""}
        time.sleep(1.0)
    return {"ok": False, "target": "napcat", "action": "start",
            "reason": f"提权成功了，但等了 {int(wait_s)} 秒 {port} 端口还没开"
                      "（多半卡在 QQ 登录：面板点「登录二维码」扫一下）"}


# ---------------------------------------------------------------- 语音服务

#: 语音服务相对位置（相对「安装根」，不是相对 _MEIPASS）
_VOICE_REL = pathlib.Path(".third_party") / "feiyu_voice" / "server"
#: venv 解释器相对语音服务根
_VOICE_PY = pathlib.Path(".venv") / "Scripts" / "python.exe"
#: 安装器写下的权威记录（data\voice-service-env.json）
_VOICE_RECORD = "voice-service-env.json"


def _voice_dir_candidates() -> list[pathlib.Path]:
    """语音服务目录候选，按可信度排序。

    为什么不是一行 `RESOURCE_ROOT / ".third_party" / ...`：
    打包成 onefile 后 RESOURCE_ROOT 是 `%TEMP%\\_MEIxxxxxx`，而 `.third_party`
    有 9 GB、根本不在包里——它躺在 `SymBotKernel.exe` **旁边**。
    写死 RESOURCE_ROOT，面板点「启动」就必然报
    「语音服务没装：C:\\...\\_MEI0000e7742\\.third_party\\... 不存在」。
    """
    out: list[pathlib.Path] = []

    def add(p: pathlib.Path) -> None:
        p = pathlib.Path(p)
        if p not in out:
            out.append(p)

    # 1) 显式配置：voice.service_dir（相对路径按数据根解析）
    cfg_dir = str(getattr(_cfg().voice, "service_dir", "") or "").strip()
    if cfg_dir:
        p = pathlib.Path(cfg_dir).expanduser()
        add(p if p.is_absolute() else pathlib.Path(DATA_ROOT) / p)

    # 2) 环境变量：装到奇怪位置时兜底，不必改配置
    env_dir = (os.environ.get("SYMEM_VOICE_DIR") or "").strip()
    if env_dir:
        add(pathlib.Path(env_dir))

    # 3) 安装器留下的记录（data\voice-service-env.json，安装时已核对过一遍）
    record: Any = {}
    for candidate in (pathlib.Path(DATA_ROOT) / "data" / _VOICE_RECORD,
                      pathlib.Path(DATA_ROOT) / _VOICE_RECORD,
                      pathlib.Path(BUNDLE_ROOT) / "data" / _VOICE_RECORD):
        try:
            record = json.loads(candidate.read_text("utf-8"))
            break
        except (OSError, ValueError):
            continue
    if isinstance(record, dict):
        root = str(record.get("voiceServiceRoot") or "").strip()
        if root:
            add(pathlib.Path(root))
        venv_py = str(record.get("venvPython") or "").strip()
        if venv_py:
            p = pathlib.Path(venv_py)
            if len(p.parents) >= 3:  # ...\\server\\.venv\\Scripts\\python.exe → ...\\server
                add(p.parents[2])

    # 4) 各候选根下的标准位置（安装根 = exe 旁边，排在数据根和解包目录前面）
    for base in candidate_roots():
        add(pathlib.Path(base) / _VOICE_REL)
    return out


def _voice_dir() -> pathlib.Path:
    """定位语音服务根目录：第一个 `.venv\\Scripts\\python.exe` 真存在的候选。"""
    cands = _voice_dir_candidates()
    for cand in cands:
        try:
            if (cand / _VOICE_PY).is_file():
                return cand
        except OSError:
            continue
    for cand in cands:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return cands[0] if cands else pathlib.Path(BUNDLE_ROOT) / _VOICE_REL


def _voice_port() -> int:
    return _url_port(str(getattr(_cfg().voice, "base_url", "") or ""), VOICE_DEFAULT_PORT)


def _voice_http(path: str, *, auth: bool = False, timeout: float = 6.0) -> dict[str, Any] | None:
    try:
        import httpx

        box = _cfg().voice
        base = str(getattr(box, "base_url", "") or "").rstrip("/")
        if not base:
            return None
        headers = {}
        if auth:
            # key 不只在配置里：声音服务第一次启动会把 key 写进
            # <服务根>\.runtime\voice-service\api_key.txt，要能自己找出来。
            from app.voice.auth import resolve_api_key

            key = resolve_api_key(box)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.get(f"{base}{path}", headers=headers)
            if not resp.is_success:
                return None
            return resp.json()
    except Exception:  # noqa: BLE001 - 没起/超时都算不可用
        return None


def voice_status() -> dict[str, Any]:
    port = _voice_port()
    up = _port_open(port)
    out: dict[str, Any] = {
        "up": up,
        "port": port,
        "url": str(getattr(_cfg().voice, "base_url", "") or ""),
        "pid": 0,
        "version": "",
        "model": "",
        "engine": "",
        "cuda": False,
        "backend": "",
        "queue": {},
        "log_path": str(VOICE_LOG),
        "log_bytes": 0,
        "last": {},
        "reason": "" if up else "语音服务没起（点「启动」，或看日志找原因）",
    }
    if up:
        health = _voice_http("/health", timeout=3) or {}
        out["version"] = str(health.get("version") or "")
        caps = _voice_http("/v1/capabilities", auth=True, timeout=8) or {}
        tts_list = caps.get("tts") or []
        first = tts_list[0] if isinstance(tts_list, list) and tts_list else {}
        if isinstance(first, dict):
            out["model"] = str(first.get("voice") or "")
            out["engine"] = str(first.get("engine") or "")
            out["backend"] = str(first.get("runtime_backend") or "")
            out["cuda"] = out["backend"].lower().startswith("cuda")
        queue = caps.get("queue") or {}
        out["queue"] = queue if isinstance(queue, dict) else {}
        pid = _pid_on_port(port)
        if not pid:
            tracked = _kv_get("voice_pid", "")
            pid = int(tracked) if tracked.isdigit() else 0
        out["pid"] = pid
    try:
        if VOICE_LOG.exists():
            out["log_bytes"] = int(VOICE_LOG.stat().st_size)
    except OSError:
        pass
    raw = _kv_get("voice_last_synth", "")
    if raw:
        try:
            out["last"] = json.loads(raw)
        except ValueError:
            out["last"] = {}
    return out


def voice_log(tail: int = 200) -> dict[str, Any]:
    """日志尾（面板那个小窗口）。只读，不轮转、不截断。"""
    tail = max(1, min(int(tail or 200), 2000))
    path = VOICE_LOG
    if not path.exists():
        return {"path": str(path), "exists": False, "lines": [], "total_lines": 0,
                "reason": "还没有日志：语音服务没启动过"}
    try:
        size = path.stat().st_size
        read_from = max(0, size - 256 * 1024)
        with open(path, "rb") as fh:
            if read_from:
                fh.seek(read_from)
            data = fh.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        return {
            "path": str(path),
            "exists": True,
            "bytes": int(size),
            "total_lines": int(total),
            "lines": lines[-tail:],
        }
    except OSError as exc:
        return {"path": str(path), "exists": True, "lines": [], "total_lines": 0,
                "reason": f"读日志失败：{exc}"}


def start_voice(*, wait_s: float = 90.0) -> dict[str, Any]:
    """拉起语音服务：不弹窗口，输出进 data/voice_service.log。"""
    port = _voice_port()
    if _port_open(port):
        return {"ok": True, "target": "voice", "action": "start", "already": True, "port": port}
    srv = _voice_dir()
    python = srv / _VOICE_PY
    if not python.is_file():
        tried = "；".join(str(c) for c in _voice_dir_candidates()) or "（无候选）"
        return {"ok": False, "target": "voice", "action": "start",
                "reason": f"语音服务没装：{python} 不存在"
                          f"（第三方权重不随本项目分发，见 docs/30 第六节；"
                          f"已按顺序找过：{tried}。"
                          "装到别处就在 config.local.yaml 里加 voice.service_dir 指过去）"}
    config_file = srv / "configs" / "voice_service.json"
    if not config_file.exists():
        return {"ok": False, "target": "voice", "action": "start",
                "reason": f"语音服务缺配置：{config_file}"}
    VOICE_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(VOICE_LOG, "ab", buffering=0)  # noqa: SIM115 - 子进程要它活着
    log.write(f"\n===== {_iso()} 由面板拉起 voice =====\n".encode())
    try:
        proc = subprocess.Popen(
            [str(python), "-m", "feiyu_voice_service",
             "--config", "configs/voice_service.json",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(srv),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW | _NEW_GROUP,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        return {"ok": False, "target": "voice", "action": "start",
                "reason": f"派生失败：{exc}"}
    _kv_set("voice_pid", str(proc.pid))
    deadline = time.time() + float(wait_s)
    while time.time() < deadline:
        if _port_open(port):
            return {"ok": True, "target": "voice", "action": "start",
                    "pid": proc.pid, "port": port, "waited_s": round(float(wait_s) - (deadline - time.time()), 1)}
        if proc.poll() is not None:
            return {"ok": False, "target": "voice", "action": "start", "pid": proc.pid,
                    "reason": f"进程退出了（退出码 {proc.returncode}），看日志：{VOICE_LOG}"}
        time.sleep(1.0)
    return {"ok": False, "target": "voice", "action": "start", "pid": proc.pid,
            "reason": f"等了 {int(wait_s)} 秒 {port} 端口还没开，看日志：{VOICE_LOG}"}


def stop_voice() -> dict[str, Any]:
    port = _voice_port()
    pid = _pid_on_port(port)
    if not pid:
        tracked = _kv_get("voice_pid", "")
        if tracked.isdigit() and _alive(int(tracked)):
            pid = int(tracked)
    if not pid:
        return {"ok": True, "target": "voice", "action": "stop", "already": True,
                "reason": "本来就没在跑"}
    ok = _kill(pid)
    deadline = time.time() + 8
    while time.time() < deadline and _port_open(port):
        time.sleep(0.3)
    still = _port_open(port)
    _kv_set("voice_pid", "")
    return {
        "ok": ok and not still,
        "target": "voice",
        "action": "stop",
        "pid": pid,
        "reason": "" if not still else f"进程 {pid} 没退出（可能不是我们起的，权限不够）",
    }


# ---------------------------------------------------------------- 汇总


def status(port: int | None = None) -> dict[str, Any]:
    """三个服务的实时状态（面板每 5 秒拉一次）。

    每一路都单独兜异常：NapCat 抽风不能把语音那一路也带着 500，
    面板要的是"哪一路坏了"，不是整张卡片消失。
    """
    def _safe(fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 观测接口永远不许 500
            data = dict(default)
            data["reason"] = f"探测失败：{type(exc).__name__}: {exc}"[:200]
            return data

    kernel = _safe(lambda: kernel_status(port), {"up": False, "reason": ""})
    napcat = _safe(napcat_status, {"up": False, "reason": ""})
    voice = _safe(voice_status, {"up": False, "reason": ""})
    return {
        "ok": True,
        "at": _iso(),
        "kernel": kernel,
        "napcat": napcat,
        "voice": voice,
        "ports": {
            "kernel": int(kernel.get("port") or 0),
            "napcat": int(napcat.get("port") or 0),
            "voice": int(voice.get("port") or 0),
        },
    }


def action(target: str, act: str, *, qq: str = "", port: int | None = None) -> dict[str, Any]:
    """统一动作入口。target=kernel|napcat|voice；act=start|stop|restart。

    `restart` 一律是"先停再起"，但内核的停=延迟自杀（见 restart_kernel）。
    """
    target = str(target or "").strip().lower()
    act = str(act or "").strip().lower()
    if target == "kernel":
        if act not in ("start", "stop", "restart", "login"):
            return {"ok": False, "reason": f"内核不支持 {act}（只能 restart）"}
        if act == "stop":
            return restart_kernel(port) | {"action": "restart", "note": "内核不能真停，只能重启"}
        # start / login 都当重启：内核已经在这个接口里跑着了
        return restart_kernel(port)
    if target == "napcat":
        if act == "stop":
            return stop_napcat()
        if act == "restart":
            stopped = stop_napcat()
            if not stopped.get("ok"):
                return stopped
            return start_napcat(qq)
        if act == "login":
            from app.core import napcat_webui

            return napcat_webui.quick_login(qq or _default_qq())
        if act == "start":
            return start_napcat(qq)
        return {"ok": False, "reason": f"NapCat 不支持 {act}"}
    if target == "voice":
        if act == "stop":
            return stop_voice()
        if act == "restart":
            stop_voice()
            return start_voice()
        if act == "start":
            return start_voice()
        return {"ok": False, "reason": f"语音服务不支持 {act}"}
    return {"ok": False, "reason": f"未知的服务：{target}"}


def _default_qq() -> str:
    from app.core import config as config_mod

    launcher_cfg = getattr(config_mod.get(), "launcher", None)
    return str(getattr(launcher_cfg, "qq", "") or "").strip()


__all__ = [
    "VOICE_LOG", "action", "kernel_status", "napcat_status", "restart_kernel",
    "start_napcat", "start_voice", "status", "stop_napcat", "stop_voice",
    "voice_log", "voice_status",
]
