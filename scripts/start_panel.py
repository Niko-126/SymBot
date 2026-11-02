"""桌面快捷方式的启动垫片：快速登录 QQ 小号 → 起内核 → 开面板窗口。

流程（2026-11-02 定）：
1. **NapCat 快速登录**：如果 3000 端口没开、且配置里给了小号，就用
   `launcher.bat <QQ>` 提权拉起 NapCat（NapCat 的"快速登录"= 复用本机已登录的
   QQ 客户端，不用扫码）。这一步必须先杀掉残留的 QQ.exe，否则注入失败。
2. **内核**：`python -m app.cli serve`（CREATE_NO_WINDOW 派生，日志落
   `data/last_serve.log`）。
3. **面板**：轮询 `/v1/health` 确认真的起来了，再开浏览器窗口。

为什么需要它（而不是快捷方式直接指 `python -m app.cli serve`）：
- `serve` 是**阻塞**的常驻进程，用户点了图标只会看到一个黑框，还得自己去开浏览器；
- 三个进程的**先后顺序**（NapCat → 内核 → 浏览器）没人替你等，只能脚本等。

两个必须在 pythonw 下守住的规矩（2026-11-02 真机踩出来的）：
1. **绝不假设有 stdout**：`pythonw.exe` 没有控制台时 `sys.stdout is None`，
   `print()` 会抛 `AttributeError` 把脚本直接弄死（而快捷方式点下去什么也看不到，
   用户只会觉得"点了没反应"）。所以全部输出走 `_log()`，有 stdout 才 echo。
2. **任何异常都要弹窗**：没有控制台就没有 traceback，只能 `MessageBox` 告诉用户
   出了什么事、日志在哪。

小号从哪来（优先级从高到低）：`--qq` 参数 → `SYMEM_QQ` 环境变量 →
`config.local.yaml` 的 `launcher.qq`。**不硬编码**，因为仓库是要公开的。

用法（快捷方式里就是这么写的）：
    .venv\\Scripts\\pythonw.exe scripts\\start_panel.py
    .venv\\Scripts\\pythonw.exe scripts\\start_panel.py --qq 123456789
    .venv\\Scripts\\pythonw.exe scripts\\start_panel.py --no-napcat    # 只起内核 + 面板
    .venv\\Scripts\\pythonw.exe scripts\\start_panel.py --port 8788 --no-browser
"""

from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data"
#: 启动器自己的流水（跟内核 stdout 分开写：内核那个文件被子进程长期持有，
#: 两拨人写同一个文件在真机上排查时读数会串行错乱）
LOG_FILE = LOG_DIR / "launcher.log"
#: 内核进程的 stdout/stderr 落这里
SERVE_LOG = LOG_DIR / "last_serve.log"
#: 声音服务的 stdout+stderr 落这里（跟面板的「服务控制」看同一个文件）
VOICE_LOG = LOG_DIR / "voice_service.log"

#: 等内核起来的上限（首次启动要读人设 + 建库 + 预检；实测 2–6 秒，留足余量）
WAIT_SECONDS = 45.0
#: 等 NapCat 登录 + 开 3000 端口的上限（QQ 启动 + 注入 + 扫码兜底都要时间）
NAPCAT_WAIT_SECONDS = 120.0
NAPCAT_PORT = 3000

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

_LOCAL_CONFIG_PATHS = (
    LOG_DIR / "config.local.yaml",   # data/ 下（不入库；测试用临时 SYMEM_DATA 读不到它）
    ROOT / "config.local.yaml",      # 仓库根（也被 .gitignore 挡住）
)


# ---------------------------------------------------------------- 基础设施


def _log(message: str) -> None:
    """写日志文件；只有真有 stdout 时才 echo（pythonw 下 stdout 是 None）。"""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line)
        except Exception:  # noqa: BLE001, S110 - 输出失败不该影响启动
            pass


def _alert(message: str, title: str = "小鲸 · 启动失败") -> None:
    """没有控制台，出错只能弹窗——否则用户点了图标"没反应"。"""
    text = f"{message}\n\n日志：{LOG_FILE}"
    _log(f"弹窗提示：{message}")
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:  # noqa: BLE001, S110 - 非 Windows 就只留日志
        pass


def _url(port: int, path: str = "") -> str:
    return f"http://127.0.0.1:{port}/{path}"


def _healthy(port: int, timeout: float = 1.5) -> bool:
    """`/v1/health` 答得上 200 = 内核已经在听了。

    用 HTTP 而不是 socket 探测：端口被别人占着也不会误判成"起好了"。
    """
    try:
        with urllib.request.urlopen(_url(port, "v1/health"), timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)) == 200
    except Exception:  # noqa: BLE001 - 连不上就是没起好，不用区分原因
        return False


# ---------------------------------------------------------------- 起内核


def _spawn_server(port: int) -> subprocess.Popen | None:
    """派生一个不占窗口的内核进程，stdout/stderr 落同一个日志文件。"""
    exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if not exe.exists():
        exe = pathlib.Path(sys.executable)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(SERVE_LOG, "ab", buffering=0)  # noqa: SIM115 - 子进程要它活着
    log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 serve --port {port} =====\n".encode())
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        return subprocess.Popen(
            [str(exe), "-m", "app.cli", "serve", "--port", str(port)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        _log(f"派生内核失败：{exc}")
        return None


# ---------------------------------------------------------------- NapCat 快速登录


def _port_open(port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_local_yaml() -> dict:
    """读 config.local.yaml（data/ 优先，仓库根兜底），失败就当空。"""
    for p in _LOCAL_CONFIG_PATHS:
        if not p.exists():
            continue
        try:
            import yaml

            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                _log(f"读本地配置：{p}")
                return data
        except Exception as exc:  # noqa: BLE001 - 配置坏了不该挡住启动
            _log(f"本地配置读不了（{p}）：{exc}")
    return {}


def _qq_account(cli_qq: str | None) -> str:
    """小号从哪来：`--qq` → `SYMEM_QQ` → `config.local.yaml: launcher.qq`。

    不硬编码在脚本里：仓库要公开，小号是私人信息。
    """
    if cli_qq:
        return str(cli_qq).strip()
    env = (os.environ.get("SYMEM_QQ") or "").strip()
    if env:
        return env
    cfg = _read_local_yaml()
    raw = (cfg.get("launcher") or {}).get("qq") if isinstance(cfg.get("launcher"), dict) else ""
    return str(raw or "").strip()


def _napcat_dir() -> pathlib.Path | None:
    """NapCat 根目录：环境变量 `NAPCAT_DIR` → `config.local.yaml: launcher.napcat_dir` → 空。

    **不猜默认安装位置**：装在哪是用户的私事，猜错比不猜更糟（仓库要公开）。
    """
    env = (os.environ.get("NAPCAT_DIR") or "").strip()
    if env:
        return pathlib.Path(env)
    cfg = _read_local_yaml()
    seg = cfg.get("launcher") if isinstance(cfg.get("launcher"), dict) else {}
    raw = str(seg.get("napcat_dir") or "").strip()
    return pathlib.Path(raw) if raw else None


def _kill_leftover_qq() -> None:
    """NapCat 的注入要求 QQ 完全没在跑；不清理就会"登录了但 3000 不开"。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in ("QQ", "NapCatWinBootMain"):
        proc = subprocess.run(
            ["taskkill", "/F", "/IM", f"{name}.exe"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=flags,
        )
        if proc.returncode == 0:
            _log(f"已结束残留进程：{name}.exe")


def _start_napcat(qq: str) -> tuple[bool, str]:
    """提权拉起 NapCat 并指定小号（NapCat 的"快速登录"= 复用本机已登录的 QQ）。

    返回 `(是否真的拉起了, 说明)`——只有真的拉起了才值得去等 3000 端口。
    """
    if not qq:
        return False, "跳过（没配小号：config.local.yaml 的 launcher.qq 或 SYMEM_QQ）"
    napcat_dir = _napcat_dir()
    if napcat_dir is None:
        return False, "跳过（没配 NapCat 目录：环境变量 NAPCAT_DIR 或 config.local.yaml 的 launcher.napcat_dir）"
    launcher = napcat_dir / "launcher.bat"
    if not launcher.exists():
        return False, f"跳过（找不到 {launcher}）"

    _kill_leftover_qq()
    time.sleep(2)  # 等 QQ 真的退干净，否则注入必失败

    # 外层再套一层引号，是因为 cmd /c 后面跟带引号的路径必须这么写
    params = f'/c ""{launcher}" {qq}"'
    rc = int(ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", params, str(napcat_dir), 7))
    if rc <= 32:
        if rc == 1223:
            return False, "已取消 UAC（NapCat 没拉起）"
        return False, f"提权拉起失败（ShellExecuteW 返回 {rc}）"
    return True, f"已提权拉起 NapCat（快速登录 {qq}）"


def _ensure_napcat(qq: str) -> None:
    if _port_open(NAPCAT_PORT):
        _log(f"NapCat 已在跑（{NAPCAT_PORT} 端口通），跳过快速登录")
        return
    _log(f"准备快速登录小号：{qq or '（未配置）'}")
    launched, msg = _start_napcat(qq)
    _log(msg)
    if not launched:
        _log("没有真的拉起 NapCat，就不空等了，直接起内核")
        return
    deadline = time.time() + NAPCAT_WAIT_SECONDS
    while time.time() < deadline:
        if _port_open(NAPCAT_PORT):
            _log(f"NapCat 就绪：127.0.0.1:{NAPCAT_PORT}")
            return
        time.sleep(1.0)
    # 没起来不拦着内核：面板照样能看，只是 QQ 侧不通
    _alert(
        f"NapCat 等了 {int(NAPCAT_WAIT_SECONDS)} 秒还没开 {NAPCAT_PORT} 端口。\n"
        "内核仍会照常启动；QQ 侧要手动检查 NapCat 控制台（可能需要在里面手动点快速登录）。"
    )


# ---------------------------------------------------------------- 声音服务


def _ensure_voice_service() -> None:
    """声音服务（肥鱼 CosyVoice3，127.0.0.1:9880）没起就顺手拉起来。

    为什么放在启动器里：它是个独立进程，没人替你开机自启；不在这起，
    面板里点"试听"只会得到 voice-unavailable。
    """
    cfg = _read_local_yaml()
    voice = cfg.get("voice") if isinstance(cfg.get("voice"), dict) else {}
    if not voice.get("enabled"):
        _log("voice.enabled 没开，跳过声音服务")
        return
    port = 9880
    if _port_open(port):
        _log(f"声音服务已在跑（{port} 端口通）")
        return
    srv = ROOT / ".third_party" / "feiyu_voice" / "server"
    python = srv / ".venv" / "Scripts" / "python.exe"
    if not python.exists() or not (srv / "configs" / "voice_service.json").exists():
        _log("声音服务没装（.third_party/feiyu_voice 或配置缺失），跳过")
        return
    _log(f"拉起声音服务：{python}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # stdout+stderr 写同一个文件：面板「服务控制」只看 data/voice_service.log
        log = open(VOICE_LOG, "ab", buffering=0)  # noqa: SIM115 - 子进程要它活着
        subprocess.Popen(
            [
                str(python), "-m", "feiyu_voice_service",
                "--config", "configs/voice_service.json",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=str(srv),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError as exc:
        _log(f"声音服务拉起失败：{exc}")
        return
    for _ in range(60):  # 服务起得比较快，模型是每次任务才加载
        if _port_open(port):
            _log(f"声音服务就绪：127.0.0.1:{port}")
            return
        time.sleep(1.0)
    _log(f"声音服务等了 60 秒还没开 {port} 端口（面板试听会不可用）")


# ---------------------------------------------------------------- 开面板


def _find_browser() -> pathlib.Path | None:
    for raw in _BROWSER_CANDIDATES:
        p = pathlib.Path(raw)
        if p.exists():
            return p
    # 注册表 App Paths 才是权威位置（Edge/Chrome 装在非默认盘也能找到）
    try:
        import winreg

        for hive, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    value, _ = winreg.QueryValueEx(k, "")
                    if value and pathlib.Path(value).exists():
                        return pathlib.Path(value)
            except OSError:
                continue
    except ImportError:
        pass
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return pathlib.Path(found)
    return None


def _focus_browser(title_keys: tuple[str, ...] = ("衡忆", "小鲸", "小涵", "127.0.0.1:8787")) -> bool:
    """把面板窗口叫到前台，并确认"现在前台真的是它"。

    为什么必须做：`webbrowser.open` / `--new-window` 在"浏览器已经开着"的机器上
    都是把 URL 交给已有进程处理——窗口最小化或被别的窗口盖住时，用户看到的就是
    "点了没反应"。真机上就是这么被坑的（2026-11-02）。

    为什么不能只调 SetForegroundWindow：Windows 会拒绝"后台进程抢焦点"，
    调用返回 True 但窗口并没有真的到前台。必须先 AttachThreadInput 挂到
    当前前台线程上，再 SetForegroundWindow；仍失败就 SwitchToThisWindow 兜底。
    最后用 GetForegroundWindow 复核，别自欺欺人。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        SW_RESTORE = 9

        targets: list[int] = []

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if any(k in buf.value for k in title_keys):
                targets.append(hwnd)
            return True

        user32.EnumWindows(enum_cb(_cb), 0)
        if not targets:
            return False
        hwnd = targets[0]

        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(tid_me, tid_fg, False)
        if not ok:
            user32.SwitchToThisWindow(hwnd, True)
        return int(user32.GetForegroundWindow()) == int(hwnd)
    except Exception as exc:  # noqa: BLE001 - 叫到前台只是体验，失败不影响面板已开
        _log(f"把浏览器叫到前台失败：{exc}")
        return False


def _open_panel(port: int) -> str:
    """打开面板，返回用了哪种方式（写进日志，出问题好定位）。

    用 `--new-window` 而不是 `--app=`：真机实测（Edge 已运行时）
    `--app=` 会把 URL 丢给已有进程、窗口不一定冒出来，而 `--new-window`
    每次都能得到一扇看得见的新窗口。开完再主动 `SetForegroundWindow`
    抢一次前台，双保险。
    """
    url = _url(port)
    browser = _find_browser()
    how = "失败"
    if browser is not None:
        try:
            subprocess.Popen(
                [str(browser), "--new-window", url],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            how = f"新窗口（{browser.name}）"
        except OSError as exc:
            _log(f"--new-window 失败，退回默认浏览器：{exc}")
    if how == "失败":
        try:
            ok = webbrowser.open(url)
            how = "默认浏览器" if ok else "默认浏览器（返回 False）"
        except Exception as exc:  # noqa: BLE001
            _log(f"打开默认浏览器失败：{exc}")
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
                how = "start 命令兜底"
            except Exception as exc2:  # noqa: BLE001
                _log(f"start 兜底也失败：{exc2}")
                return "失败"
    # 给浏览器一点时间把窗口画出来，再抢前台
    for _ in range(10):
        if _focus_browser():
            return how + " + 已置前"
        time.sleep(0.3)
    return how + "（未能置前）"


# ---------------------------------------------------------------- 主流程


def _run(port: int, open_browser: bool, qq: str, with_napcat: bool) -> int:
    # 1) NapCat 快速登录（先做，它最慢；3000 已通就跳过）
    if with_napcat:
        _ensure_napcat(qq)
    else:
        _log("--no-napcat：跳过 NapCat")

    # 1.5) 声音服务（独立进程，没人替它开机自启）
    _ensure_voice_service()

    # 2) 内核
    if _healthy(port):
        _log(f"内核已在 {_url(port)} 上跑着，直接开面板。")
        if open_browser:
            _log(f"打开面板：{_open_panel(port)}")
        return 0

    _log(f"内核没在跑，派生一个（端口 {port}）……")
    proc = _spawn_server(port)
    if proc is None:
        _alert("内核进程没起来（派生失败）。")
        return 1
    _log(f"内核进程已派生：pid={proc.pid}，开始等 /v1/health")

    deadline = time.time() + WAIT_SECONDS
    tries = 0
    while time.time() < deadline:
        tries += 1
        if _healthy(port):
            _log(f"内核已就绪：{_url(port)}")
            if open_browser:
                _log(f"打开面板：{_open_panel(port)}")
            return 0
        if proc.poll() is not None:
            _alert(f"内核进程退出了（退出码 {proc.returncode}）。")
            return 1
        if tries % 25 == 0:
            _log(f"还在等内核……（已试探 {tries} 次，约 {tries * 0.4:.0f}s）")
        time.sleep(0.4)

    _alert(f"等了 {int(WAIT_SECONDS)} 秒内核还没就绪（端口 {port}）。")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="start_panel", description="快速登录小号并起内核面板")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SYMEM_PORT") or 8787))
    ap.add_argument("--no-browser", action="store_true", help="只起服务，不开面板")
    ap.add_argument("--qq", default="", help="要快速登录的 QQ 小号（默认读 SYMEM_QQ / config.local.yaml: launcher.qq）")
    ap.add_argument("--no-napcat", action="store_true", help="跳过 NapCat 快速登录，只起内核")
    args = ap.parse_args(argv)
    # pythonw 下硬崩溃（访问违例之类）没有任何输出，留一份原生栈
    try:
        import faulthandler

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        faulthandler.enable(open(LOG_DIR / "faulthandler.log", "a", encoding="utf-8"))  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110 - 诊断用，失败不影响启动
        pass
    qq = _qq_account(args.qq)
    _log(f"启动器起来了：argv={argv!r} exec={sys.executable!r} qq={qq or '（无）'}")
    try:
        return _run(int(args.port), not args.no_browser, qq, not args.no_napcat)
    except BaseException:  # noqa: BLE001 - pythonw 下没有 traceback，兜住并弹窗
        detail = traceback.format_exc()
        _log("未捕获异常：\n" + detail)
        _alert("启动时抛出异常：\n" + detail.splitlines()[-1])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
