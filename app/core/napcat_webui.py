"""NapCat WebUI 客户端：登录二维码 / 快速登录 / 登录状态。

为什么不能只走 OneBot v11（2026-11-02 在真机 NapCat 4.18.41 上逐个探过）：
- `POST /get_qrcode`、`/get_login_qrcode`、`/qrcode` 一律返回
  `{"status":"failed","message":"不支持的Api ..."}` —— v11 那侧**没有**扫码接口；
- 二维码在 NapCat 自己的 WebUI（默认 127.0.0.1:6099）里：
  `POST /api/auth/login {"hash": sha256(token + ".napcat")}` → `data.Credential`，
  之后 `Authorization: Bearer <Credential>`；
  `POST /api/QQLogin/GetQQLoginQrcode` → `data.qrcode`（未登录时才有图）；
  `POST /api/QQLogin/CheckLoginStatus` → `{isLogin,isOffline,loginPhase,qrcodeurl,...}`；
  `POST /api/QQLogin/SetQuickLogin {"uin": "..."}` → 快速的"一键登录"；
  `POST /api/QQLogin/GetQuickLoginList` → `["<uin>", ...]`。

安全：webui.json 里的 token 是**这台机器 NapCat 的管理口令**，只在服务端内存里用，
绝不通过任何接口回给面板；对外只给二维码图、登录状态、人话错误。
﻿​​‌​‍﻿‍‌‍‌﻿‌‍‍﻿﻿​‌‍‍﻿‍‌‌﻿​​​​﻿﻿‌﻿"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import time
from typing import Any

import httpx

#: 凭据缓存多久（NapCat 的 Credential 是 HMAC 串，没有明确过期时间；
#: 5 分钟换一次足够，失败了会自动重登）
_CRED_TTL_S = 300.0
#: WebUI 默认端口（webui.json 里通常写 6099）
_DEFAULT_WEBUI_PORT = 6099

_cred_cache: dict[str, Any] = {"key": "", "value": "", "at": 0.0}


def webui_paths() -> tuple[pathlib.Path | None, pathlib.Path | None]:
    """返回 (NapCat 根目录, webui.json 路径)；没配就 (None, None)。

    目录优先级：环境变量 `NAPCAT_DIR` → `launcher.napcat_dir` 配置 → 空。
    **不猜默认安装位置**：装在哪是用户的私事，猜错比不猜更糟。
    """
    from app.core import config as config_mod

    raw = ""
    cfg = config_mod.get()
    launcher = getattr(cfg, "launcher", None)
    if launcher is not None:
        raw = str(getattr(launcher, "napcat_dir", "") or "").strip()
    import os

    raw = (os.environ.get("NAPCAT_DIR") or raw).strip()
    if not raw:
        # 配置模型还没升级（老版本）/本地 yaml 直读，兜底一把
        try:
            import yaml

            for p in (config_mod.DATA_ROOT / "config.local.yaml", config_mod.CONFIG_PATH.parent / "config.local.yaml"):
                if not p.exists():
                    continue
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                seg = data.get("launcher") if isinstance(data.get("launcher"), dict) else {}
                raw = str(seg.get("napcat_dir") or "").strip()
                if raw:
                    break
        except Exception:  # noqa: BLE001 - 配置坏了就当没配
            raw = ""
    if not raw:
        return None, None
    root = pathlib.Path(raw)
    return root, root / "config" / "webui.json"


def webui_port() -> int:
    """WebUI 端口：webui.json 里写的最准，其次 `launcher.webui_port`，最后 6099。"""
    _, wpath = webui_paths()
    if wpath is not None and wpath.exists():
        try:
            data = json.loads(wpath.read_text(encoding="utf-8"))
            port = int(data.get("port") or 0)
            if port > 0:
                return port
        except (OSError, ValueError, TypeError):
            pass
    from app.core import config as config_mod

    launcher = getattr(config_mod.get(), "launcher", None)
    port = int(getattr(launcher, "webui_port", 0) or 0) if launcher is not None else 0
    return port or _DEFAULT_WEBUI_PORT


def webui_token() -> str:
    """从 webui.json 读管理口令；读不到就空（= WebUI 不可用）。"""
    _, wpath = webui_paths()
    if wpath is None or not wpath.exists():
        return ""
    try:
        data = json.loads(wpath.read_text(encoding="utf-8"))
        return str(data.get("token") or "").strip()
    except (OSError, ValueError):
        return ""


def base_url() -> str:
    return f"http://127.0.0.1:{webui_port()}/api"


def available() -> tuple[bool, str]:
    """WebUI 能不能用。`reason` 是给人看的一句人话。"""
    root, wpath = webui_paths()
    if root is None:
        return False, "没配 NapCat 目录（设置 → 连接与账号 → NapCat 安装目录）"
    if not root.exists():
        return False, f"NapCat 目录不存在：{root}"
    if wpath is None or not wpath.exists():
        return False, "找不到 NapCat 的 config/webui.json（NapCat 没启动过？）"
    if not webui_token():
        return False, "webui.json 里没有 token（NapCat 关闭了 WebUI 口令？）"
    return True, ""


def _login() -> tuple[str, str]:
    """用 webui token 换 Credential。返回 (credential, 错误)。缓存 5 分钟。"""
    ok, why = available()
    if not ok:
        return "", why
    token = webui_token()
    cache_key = f"{webui_port()}:{token[:12]}"
    now = time.time()
    if _cred_cache["key"] == cache_key and (now - float(_cred_cache["at"] or 0)) < _CRED_TTL_S:
        return str(_cred_cache["value"] or ""), ""
    digest = hashlib.sha256(f"{token}.napcat".encode("utf-8")).hexdigest()
    try:
        with httpx.Client(timeout=6, trust_env=False) as client:
            resp = client.post(f"{base_url()}/auth/login", json={"hash": digest})
            data = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001 - 连不上就是 WebUI 没开
        return "", f"连不上 NapCat WebUI（{webui_port()} 端口）：{type(exc).__name__}"
    if int(data.get("code") if data.get("code") is not None else -1) != 0:
        return "", f"NapCat WebUI 登录失败：{data.get('message') or '未知原因'}"
    cred = str((data.get("data") or {}).get("Credential") or "")
    if not cred:
        return "", "NapCat WebUI 没返回凭据"
    _cred_cache.update({"key": cache_key, "value": cred, "at": now})
    return cred, ""


def _post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """带凭据 POST 到 WebUI；401 自动重登一次。返回 {"ok","data"|"reason"}。"""
    cred, why = _login()
    if not cred:
        return {"ok": False, "reason": why or "WebUI 不可用"}
    body = payload or {}
    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                resp = client.post(
                    f"{base_url()}/{path.lstrip('/')}",
                    json=body,
                    headers={"Authorization": f"Bearer {cred}"},
                )
                data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"WebUI 请求失败：{type(exc).__name__}"}
        if str(data.get("message") or "") == "Unauthorized" and attempt == 1:
            _cred_cache["at"] = 0.0  # 凭据过期：重登一次
            cred, why = _login()
            if not cred:
                return {"ok": False, "reason": why}
            continue
        code = data.get("code")
        if code is not None and int(code) != 0:
            return {"ok": False, "reason": str(data.get("message") or f"code={code}")}
        return {"ok": True, "data": data.get("data")}
    return {"ok": False, "reason": "WebUI 鉴权反复失败"}


def login_status() -> dict[str, Any]:
    """NapCat 侧登录状态（不依赖 OneBot 端口是否已开）。"""
    ok, why = available()
    if not ok:
        return {"webui": False, "reason": why}
    res = _post("QQLogin/CheckLoginStatus")
    if not res.get("ok"):
        return {"webui": True, "ok": False, "reason": res.get("reason"), "is_login": False}
    data = res.get("data") or {}
    return {
        "webui": True,
        "ok": True,
        "is_login": bool(data.get("isLogin")),
        "offline": bool(data.get("isOffline")),
        "phase": str(data.get("loginPhase") or ""),
        "core_ready": bool(data.get("coreReady")),
        "qr_accepted": bool(data.get("qrLoginAccepted")),
        "login_error": str(data.get("loginError") or ""),
        "qrcode_url": str(data.get("qrcodeurl") or ""),
    }


def qrcode() -> dict[str, Any]:
    """取登录二维码，统一成 `data:image/png;base64,...`。

    返回 {"ok": bool, "image": data_url|"", "reason": 人话, "already_login": bool}。
    NapCat 的 data.qrcode 在不同版本里可能是 base64、可能是 URL、也可能是本地文件路径，
    三种都归一化；认不出来就把原始串回给调用方自己去开（面板给"去 WebUI 扫码"的退路）。
    """
    st = login_status()
    if st.get("webui") is False:
        return {"ok": False, "image": "", "reason": st.get("reason") or "WebUI 不可用", "already_login": False}
    if st.get("is_login"):
        return {"ok": False, "image": "", "reason": "小号已经登录了，不需要扫码", "already_login": True}
    res = _post("QQLogin/GetQQLoginQrcode")
    if not res.get("ok"):
        reason = str(res.get("reason") or "")
        if "Logined" in reason or "Logged" in reason:
            return {"ok": False, "image": "", "reason": "小号已经登录了，不需要扫码", "already_login": True}
        return {"ok": False, "image": "", "reason": reason or "NapCat 没给二维码", "already_login": False}
    raw = (res.get("data") or {}).get("qrcode")
    raw = raw.get("qrcode") if isinstance(raw, dict) else raw
    image, how = _normalize_qrcode(raw)
    if not image:
        return {
            "ok": False,
            "image": "",
            "reason": "NapCat 给的二维码格式认不出来，请直接打开它的 WebUI 扫码",
            "raw_hint": str(raw)[:120],
            "webui": f"http://127.0.0.1:{webui_port()}/webui",
            "already_login": False,
        }
    return {"ok": True, "image": image, "kind": how, "reason": "", "already_login": False}


def _normalize_qrcode(raw: Any) -> tuple[str, str]:
    """把 NapCat 给的二维码整成 data URL。返回 (data_url, 来源类型)。"""
    if not raw:
        return "", ""
    text = str(raw).strip()
    if text.startswith("data:image"):
        return text, "data-url"
    # 纯 base64（可能带换行）
    try:
        compact = "".join(text.split())
        head = base64.b64decode(compact[:16] + "==" * 2, validate=False)[:4]
        if head.startswith(b"\x89PNG"):
            return "data:image/png;base64," + compact, "base64-png"
        if head.startswith(b"\xff\xd8"):
            return "data:image/jpeg;base64," + compact, "base64-jpg"
    except Exception:  # noqa: BLE001 - 不是 base64 就往下走
        pass
    if text.startswith(("http://", "https://")):
        # NapCat 可能给的是自己 static 下的图片/页面：能直接抓成字节就抓
        try:
            with httpx.Client(timeout=8, trust_env=False, follow_redirects=True) as client:
                resp = client.get(text)
                ctype = str(resp.headers.get("content-type") or "")
                if resp.is_success and ctype.startswith("image/"):
                    return f"data:{ctype.split(';')[0]};base64," + base64.b64encode(resp.content).decode(), "url-fetched"
        except Exception:  # noqa: BLE001
            pass
        return "", ""
    # 本地文件路径
    p = pathlib.Path(text)
    if p.exists() and p.is_file() and p.stat().st_size < 2_000_000:
        try:
            data = p.read_bytes()
            if data[:4] == b"\x89PNG":
                return "data:image/png;base64," + base64.b64encode(data).decode(), "file"
        except OSError:
            pass
    return "", ""


def quick_login_list() -> list[str]:
    res = _post("QQLogin/GetQuickLoginList")
    if not res.get("ok"):
        return []
    data = res.get("data") or []
    return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []


def quick_login(uin: str) -> dict[str, Any]:
    """NapCat 一键快速登录（复用本机已登录过的 QQ 凭据）。"""
    uin = str(uin or "").strip()
    if not uin:
        return {"ok": False, "reason": "没给 QQ 号"}
    known = quick_login_list()
    if known and uin not in known:
        return {
            "ok": False,
            "reason": f"这台机器没存过 {uin} 的快速登录记录（可选：{'、'.join(known) or '无'}）",
            "known": known,
        }
    res = _post("QQLogin/SetQuickLogin", {"uin": uin})
    if not res.get("ok"):
        return {"ok": False, "reason": str(res.get("reason") or "快速登录失败")}
    return {"ok": True, "uin": uin, "known": known}


def restart_napcat() -> dict[str, Any]:
    res = _post("QQLogin/RestartNapCat")
    if not res.get("ok"):
        return {"ok": False, "reason": str(res.get("reason") or "NapCat 重启失败")}
    return {"ok": True}


__all__ = [
    "available", "base_url", "login_status", "qrcode", "quick_login",
    "quick_login_list", "restart_napcat", "webui_paths", "webui_port", "webui_token",
]
