"""校验软水印：有没有、对不对、缺哪些（docs/33）。

用法：

    python scripts/verify_watermark.py --scan app
    python scripts/verify_watermark.py --explain app/core/continuity.py

输出纪律：只报"命中 / 不命中 / 缺哪几个文件"，**不打印指纹本体，也不打印密钥**。
拿指纹去跟别人的代码比、或者把指纹贴进 issue，都等于自己把水印暴露了。

退出码：0 全命中 / 1 有缺失 / 2 密钥不可用。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.core import watermark as wm  # noqa: E402

try:  # 锚点表在注入脚本里，能借就借，借不到就只校验 docstring 水印
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from apply_watermark import ANCHOR_SITES  # noqa: E402
except Exception:  # noqa: BLE001 - 校验不该因为别人的表坏了就跑不起来
    ANCHOR_SITES = {}


def _load_text(path: pathlib.Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _relpath(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _kind_names(text: str, rel: str, report: dict[str, Any]) -> list[str]:
    """哪些种类的水印在这个文件里（只说种类，不说内容）。"""
    kinds: list[str] = []
    if report.get("found"):
        kinds.append(f"文本水印（{report.get('state')}）")
    recipe = ANCHOR_SITES.get(rel)
    if recipe and str(recipe["new"]) in text:
        kinds.append(f"派生常量锚（{recipe['const']} 在位）")
    elif recipe:
        kinds.append("派生常量锚（未注入）")
    if wm.META_FIELD in text:
        kinds.append("结构指纹字段（被引用）")
    return kinds


def _scan(target: str, include_vendor: bool) -> int:
    status = wm.key_status()
    if not status["signing"]:
        print("密钥不可用：既没有 SYMEM_WM_KEY，也没有可用的密钥文件。只能校验、不能认证。")
        print(f"（密钥文件应放在 {status['key_file']}）")
        return 2

    base = pathlib.Path(target)
    if not base.is_absolute():
        base = ROOT / base
    files = wm.source_files(base, include_vendor=include_vendor)
    hit = 0
    missing: list[str] = []
    skipped = 0
    for path in files:
        rel = _relpath(path)
        text = _load_text(path)
        if text is None:
            skipped += 1
            continue
        if wm.verify(text, rel):
            hit += 1
        else:
            missing.append(rel)

    print(f"扫描：{target}")
    print(f"可查文件 {len(files)}（跳过多余二进制 {skipped}）；命中 {hit}；缺失 {len(missing)}")
    for rel in missing:
        print("  - " + rel)
    return 0 if not missing else 1


def _explain(target: str) -> int:
    path = pathlib.Path(target)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        print(f"找不到文件：{target}")
        return 1
    rel = _relpath(path)
    text = _load_text(path)
    if text is None:
        print(f"{rel}：读不了（二进制或非 utf-8）")
        return 1

    report = wm.verify_report(text, rel)
    kinds = _kind_names(text, rel, report)
    print(f"文件：{rel}")
    print(f"文本水印：{report.get('state')}"
          f"（候选 {report.get('count', 0)} 个，其它文件串 {report.get('stray', 0)} 个）")
    if report.get("state") == "unverified":
        print("  说明：当前是派生盐模式，只能认出「像自己」，不能认证。")
    print("种类：" + ("、".join(kinds) if kinds else "无"))
    state = report.get("state")
    if state == "unverified":
        print("  退出码 2：密钥不可用，只能看结构，不能认证。")
        return 2
    return 0 if state == "hit" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SymBot 软水印校验（docs/33）")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", metavar="DIR", help="扫描目录，报命中数 + 缺失清单")
    group.add_argument("--explain", metavar="FILE", help="说明单个文件的水印种类")
    ap.add_argument("--include-vendor", action="store_true", help="连 vendor/ 一起扫")
    args = ap.parse_args(argv)

    if args.scan:
        target = args.scan
        p = pathlib.Path(target)
        if not p.is_absolute() and not p.exists():
            p = ROOT / target
        return _scan(str(p), args.include_vendor)
    return _explain(args.explain)


if __name__ == "__main__":
    raise SystemExit(main())
