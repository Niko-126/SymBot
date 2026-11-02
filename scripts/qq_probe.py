"""QQ 官方开放平台凭据探测（不连 WebSocket，只打两个 HTTP 端点）。

解决的问题：`config.local.yaml` 里的 `qq.token` 到底是
**Secret（开发设置里的机器人密钥）** 还是 **access_token（2 小时过期）**。
两种东西长得很像，填错时日志只会给一个笼统的 401。

判定流程：
  1) `POST https://bots.qq.com/app/getAppAccessToken`  {appId, clientSecret}
     - 成功：token 就是 Secret，拿到 access_token（expires_in 秒）
     - 失败：把腾讯的 code/message 原样打出来，进入第 2 步
  2) `GET https://api.sgroup.qq.com/gateway`  `Authorization: QQBot <access_token>`
     - 200：凭证链可用，WS 能连
     - 401：11244=access_token 无效/过期，11242=Token 错误，11005=机器人不存在……

用法（仓库根目录）：
    .venv\\Scripts\\python.exe scripts\\qq_probe.py
    .venv\\Scripts\\python.exe scripts\\qq_probe.py --appid 102xxxxxx
    .venv\\Scripts\\python.exe scripts\\qq_probe.py --appid 102xxxxxx --token-file .qq_token

安全：token 只从 config.local.yaml / 文件 / 环境变量读，**不接受命令行明文**；
输出里一律只露前 8 位 + 长度，可直接贴到聊天里。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway"
TIMEOUT = 15


def _mask(value: str) -> str:
    value = value or ""
    if not value:
        return "(空)"
    head = value[:8]
    return f"{head}…（长度 {len(value)}）"


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, _loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _loads(exc.read())


def _get_json(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, _loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _loads(exc.read())


def _loads(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {"raw": data}
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")[:400]}


def _err(data: dict) -> str:
    code = data.get("code")
    message = data.get("message") or data.get("msg") or ""
    if code is None and not message:
        return json.dumps(data, ensure_ascii=False)[:300]
    return f"code={code} message={message}"


def _cfg_qq() -> tuple[str, str]:
    """返回 (appid, token)：环境变量 > config.local.yaml。"""
    appid = os.environ.get("AI_LIFE_QQ_APPID", "").strip()
    token = os.environ.get("AI_LIFE_QQ_TOKEN", "").strip()
    path = ROOT / "config.local.yaml"
    if path.exists():
        try:
            from app.core import config as config_mod

            cfg = config_mod.load()
            appid = appid or str(cfg.qq.appid or "").strip()
            token = token or str(cfg.qq.token or "").strip()
        except Exception as exc:  # noqa: BLE001 - 探测脚本要能把错误说清楚
            print(f"[warn] 读 config.local.yaml 失败：{type(exc).__name__}: {exc}")
    return appid, token


def probe(appid: str, token: str) -> int:
    print("== 输入 ==")
    print(f"appid   : {appid or '(空)'}")
    print(f"token   : {_mask(token)}")
    if not token:
        print("\n没有 token，先在 config.local.yaml 填 qq.token。")
        return 2
    if not appid:
        print("\n没有 AppID。到 https://q.qq.com/ → 机器人 → 开发设置 复制 AppID 再跑。")
        return 2

    print("\n== 1) token 当 Secret 换 access_token ==")
    status, data = _post_json(TOKEN_URL, {"appId": appid, "clientSecret": token})
    access_token = str(data.get("access_token") or "")
    if status == 200 and access_token:
        print(f"OK  -> 这是 Secret，换到 access_token（有效期 {data.get('expires_in')}s）")
        print(f"     access_token: {_mask(access_token)}")
        print("\n结论：config.local.yaml 的 qq.token 填 Secret 是对的，把 qq.appid 填上、"
              "qq.enabled 改 true 即可。")
    else:
        print(f"FAIL -> HTTP {status} {_err(data)}")
        print("     说明这串不是 Secret（或 AppID/Secret 不匹配）。")

    print("\n== 2) 拿 access_token 调 /gateway ==")
    if access_token:
        candidate, source = access_token, "刚换到的 access_token"
    else:
        candidate, source = token, "config 里那串原值"
        print(f"     （用原值试，来源：{source}）")
    gstatus, gdata = _get_json(GATEWAY_URL, {"Authorization": f"QQBot {candidate}"})
    if gstatus == 200 and gdata.get("url"):
        print(f"OK  -> gateway 可连：{gdata['url'][:60]}…")
        return 0
    print(f"FAIL -> HTTP {gstatus} {_err(gdata)}")
    if not access_token:
        print("     原值不是有效 access_token。若确认它是 Secret，请先填 AppID 再跑一次。")
        print("     11244 = access_token 无效/过期；11242 = Token 错误；10004 = 机器人不存在。")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qq_probe", description="QQ 官方平台凭据探测")
    parser.add_argument("--appid", default="", help="覆盖 config 里的 qq.appid")
    parser.add_argument("--token-file", default="", help="从文件读 token（避免命令行明文）")
    parser.add_argument("--print-token-source", action="store_true",
                        help="只打印当前 token 来源与掩码后长度，不联网")
    args = parser.parse_args(argv)

    appid, token = _cfg_qq()
    if args.token_file:
        token = pathlib.Path(args.token_file).read_text(encoding="utf-8").strip()
    if args.appid:
        appid = args.appid.strip()
    if args.print_token_source:
        print(f"appid : {appid or '(空)'}")
        print(f"token : {_mask(token)}")
        return 0
    return probe(appid, token)


if __name__ == "__main__":
    raise SystemExit(main())
