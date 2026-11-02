"""生成交接快照。

新会话不要靠回忆：先跑这个脚本，把输出贴进 docs/交接-会话交接.md，
或者直接发给下一个会话当开场材料。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\handoff.py            # 快照
    .\\.venv\\Scripts\\python.exe scripts\\handoff.py --test     # 顺带跑 smoke
"""

from __future__ import annotations

import argparse
import os
import datetime as dt
import json
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8787"

# 被管道/工具捕获时强制 UTF-8：否则 GBK 控制台会在 ⚠ 这类字符上抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _run(*args: str) -> str:
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120, env=env)
        return (out.stdout or out.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"<失败：{exc}>"


def _http(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(API + path, timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _mtime(path: pathlib.Path) -> str:
    if not path.exists():
        return "不存在"
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="顺带跑 smoke")
    args = ap.parse_args(argv)

    print(f"# 交接快照 · {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    print(f"- HEAD：`{_run('git', 'log', '-1', '--format=%h %s (%ad)', '--date=short')}`")
    dirty = _run("git", "status", "--porcelain")
    print(f"- 工作树：{'干净' if not dirty else '有未提交改动'}")
    if dirty:
        for line in dirty.splitlines()[:10]:
            print(f"  - `{line}`")

    print()
    print("## 最近提交")
    for line in _run("git", "log", "--oneline", "-8").splitlines():
        print(f"- {line}")

    print()
    print("## 产物与新鲜度")
    exe = ROOT / "dist/ai-life/ai-life.exe"
    head_ts = _run("git", "log", "-1", "--format=%ct")
    try:
        exe_ts = str(int(exe.stat().st_mtime)) if exe.exists() else ""
    except OSError:
        exe_ts = ""
    stale = bool(exe_ts and head_ts and int(exe_ts) < int(head_ts))
    print(f"- 桌面版 exe：{_mtime(exe)}{'  ⚠ 落后于最新提交，需要重新打包' if stale else '（最新）'}")
    zips = sorted((ROOT / "dist").glob("ai-life-*-win64.zip"))
    if zips:
        z = zips[-1]
        print(f"- 分发包：{z.name} · {z.stat().st_size // 1024 // 1024} MB · {_mtime(z)}")
    print(
        f"- 面板：{_mtime(ROOT / 'panel/index.html')}"
        f" · 设置页：{_mtime(ROOT / 'panel/settings.html')}"
    )

    print()
    print("## 运行状态")
    health = _http("/v1/health")
    if health:
        print(f"- 内核：{health.get('provider')} | 实际模型：{health.get('active_model')} | 人设：{health.get('persona')}")
        st = _http("/v1/settings") or {}
        flags = " ".join(f"{k}={st.get(k)}" for k in ("mix_mode", "deep_think", "show_delta", "llm_appraisal", "style"))
        print(f"- 设置：{flags}")
        board = _http("/v1/board") or {}
        rel = board.get("relation") or {}
        print(f"- 会话：事件 {len(board.get('history') or [])} 条 | 记忆 {(board.get('memories') or {}).get('total')} 条"
              f" | 好感 {rel.get('affinity')} | 阶段 {rel.get('stage_label')}")
    else:
        print("- 内核未运行（桌面版/`python -m app.cli serve`）")

    print()
    print("## 文档")
    docs = sorted(p.name for p in (ROOT / "docs").glob("*.md"))
    print(f"- 共 {len(docs)} 篇：" + "、".join(docs))

    if args.test:
        print()
        print("## 自检")
        code = _run(str(ROOT / ".venv/Scripts/python.exe"), "scripts/smoke.py")
        tail = [ln for ln in code.splitlines() if "结果" in ln or "FAIL" in ln]
        print("- smoke：" + ("；".join(tail) if tail else "<无输出>"))

    print()
    print("## 新会话必读（按顺序）")
    print("1. `docs/交接-会话交接.md`（本文）")
    print("2. `README.md`")
    print("3. 只读你这次要改的模块对应的 docs/0x，不要通读全部")
    return 0


if __name__ == "__main__":
    sys.exit(main())
