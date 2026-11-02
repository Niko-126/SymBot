"""腾讯频道 CLI 探针：一条命令看清「登录态 / 通知 / 频道数据」的真实形状。

背景：`tencent-channel-cli` 是腾讯频道的官方命令行（Go 二进制，npm 包分发），
所有子命令都支持 `--json`，所以桥接不需要 OpenClaw，也不需要重写内核——
Python 直接 subprocess 调它就行。本脚本只读，不发消息、不改任何东西。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe scripts\\tcc_probe.py
    .venv\\Scripts\\python.exe scripts\\tcc_probe.py --out data/_tcc/probe.json

它会依次跑：
    version / doctor / login status / notices-status /
    get-recent-notices / check-notices / check-new-notices
并把原始 JSON 存一份，方便按真实字段写解析。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIMEOUT = 60


def _cli_path() -> str:
    """找 CLI：PATH 里优先 .cmd（Windows 上 .ps1 会被执行策略卡住）。"""
    candidates: list[str] = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.append(str(pathlib.Path(appdata) / "npm" / "tencent-channel-cli.cmd"))
    found = shutil.which("tencent-channel-cli")
    if found:
        candidates.insert(0, found)
    for cand in candidates:
        if cand and pathlib.Path(cand).exists():
            return cand
    raise SystemExit("没找到 tencent-channel-cli：先 npm install -g tencent-channel-cli")


def run(cli: str, *args: str) -> dict:
    try:
        proc = subprocess.run(
            [cli, *args, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"_error": "timeout", "_args": list(args)}
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"_raw": out[:1500], "_stderr": (proc.stderr or "").strip()[:800]}
    if isinstance(data, dict):
        data.setdefault("_exit", proc.returncode)
    return data


def _short(value: object, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tcc_probe", description="腾讯频道 CLI 探针（只读）")
    parser.add_argument("--out", default="", help="原始 JSON 落盘路径（默认 data/_tcc/probe-<时间>.json）")
    args = parser.parse_args(argv)

    cli = _cli_path()
    steps = [
        ("version", ("version",)),
        ("doctor", ("doctor",)),
        ("login-status", ("login", "status")),
        ("notices-status", ("manage", "notices-status")),
        ("recent-notices", ("manage", "get-recent-notices")),
        ("check-notices", ("manage", "check-notices")),
        ("check-new-notices", ("manage", "check-new-notices")),
    ]

    results: dict[str, dict] = {}
    print(f"CLI: {cli}")
    for name, cmd in steps:
        data = run(cli, *cmd)
        results[name] = data
        ok = data.get("success", False)
        print(f"\n[{name}] exit={data.get('_exit')} success={ok}")
        print("  " + _short(data))

    out = pathlib.Path(args.out) if args.out else (
        ROOT / "data" / "_tcc" / f"probe-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始结果：{out}")

    if not results.get("login-status", {}).get("success"):
        print("\n结论：还没登录。先跑：tencent-channel-cli login --json，把二维码给用户扫，再 poll-token。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
