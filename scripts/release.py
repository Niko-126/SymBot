"""源码备份打包（两个 zip，纯备份，不生成任何额外文件）。

用法：
    .venv\\Scripts\\python.exe scripts\\release.py
    .venv\\Scripts\\python.exe scripts\\release.py --tag 20261101

产出（dist/）：
    SymBot-src-<tag>.zip         主程序源码（不含 R 级插件）
    SymBot-r18-src-<tag>.zip     R 级插件源码（独立备份）

不打包：data/、.venv/、__pycache__/、Chat/、Girl-memory/、语料/、config.local.yaml、*.log
不生成：INSTALL / CHANGELOG / VERSION / 安装脚本 —— 备份就是备份。
"""
from __future__ import annotations

import argparse
import pathlib
import time
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
DIST = REPO / "dist"

EXCLUDE_DIRS = {
    ".venv", "__pycache__", "dist", "build", ".git", ".idea", ".pytest_cache",
    "data", "Chat", "Girl-memory", "语料", "logs", ".ai_analysis",
}
EXCLUDE_NAMES = {
    "config.local.yaml", "botpy.log", ".DS_Store",
    "life.db", "life.db-wal", "life.db-shm", "persona-active.yaml",
}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log")


def iter_files(*, skip_plugin: bool = False, only_plugin: bool = False):
    for p in sorted(REPO.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO)
        if set(rel.parts) & EXCLUDE_DIRS:
            continue
        if p.name in EXCLUDE_NAMES or p.name.endswith(EXCLUDE_SUFFIX):
            continue
        in_r18 = rel.parts[:3] == ("app", "intimacy", "r18")
        if skip_plugin and in_r18:
            continue
        if only_plugin and not in_r18:
            continue
        yield p, rel


def pack(zip_path: pathlib.Path, root_name: str, *, skip_plugin=False, only_plugin=False) -> int:
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, rel in iter_files(skip_plugin=skip_plugin, only_plugin=only_plugin):
            z.write(src, f"{root_name}/{rel.as_posix()}")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=time.strftime("%Y%m%d"), help="备份标记（默认当天日期）")
    args = ap.parse_args()
    tag = str(args.tag).strip() or time.strftime("%Y%m%d")
    DIST.mkdir(parents=True, exist_ok=True)

    core = DIST / f"SymBot-src-{tag}.zip"
    n1 = pack(core, f"SymBot-src-{tag}", skip_plugin=True)
    print(f"[core] {core.name}  {core.stat().st_size/1024/1024:.2f} MB / {n1} 文件")

    plug = DIST / f"SymBot-r18-src-{tag}.zip"
    n2 = pack(plug, f"SymBot-r18-src-{tag}", only_plugin=True)
    print(f"[r18 ] {plug.name}  {plug.stat().st_size/1024:.1f} KB / {n2} 文件")

    print("\nDONE")
    print(" ", core)
    print(" ", plug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
