"""给工程打软水印 / 剥掉水印（docs/33）。

两种东西会落进文件，都不可见、都不影响运行：

1. **docstring 水印**（默认）：在每个 .py/.md/.html/.css 里嵌 34 个零宽字符，
   内容是 HMAC 指纹（密钥 + 相对路径）。语法由 `ast.parse` 兜底，不破。
2. **派生常量锚**（`--anchors`，默认关）：在少量核心 .py 里把一个字面量换成同值常量，
   例如 `[:500]` → `[:_WM_TRACE_TRIM]`。值是等值替换（行为零变化），但常量被删就 NameError，
   所以"抄过去顺手删干净"会掉东西。这一档会动到逻辑，必须在代码冻结后、评审后跑。

用法：

    python scripts/apply_watermark.py --init                  # 生成 scripts/.wm.key
    python scripts/apply_watermark.py --write --dry-run       # 先看会改哪些文件
    python scripts/apply_watermark.py --write                 # 真写（默认遍历 app/）
    python scripts/apply_watermark.py --write --paths app/core app/web
    python scripts/apply_watermark.py --write --anchors       # 连锚点一起（会动逻辑）
    python scripts/apply_watermark.py --strip                 # 全部剥掉（自带幂等自测）

退出码：0 成功 / 1 有文件失败 / 2 密钥不可用或参数不对。
"""

from __future__ import annotations

import argparse
import ast
import os
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.core import watermark as wm  # noqa: E402

#: 派生常量锚：只列少量核心文件。`old` 必须在文件里**唯一命中**，`value` 必须等于原字面量
#: （等值替换 = 行为零变化），常量被删则 NameError。
ANCHOR_SITES: dict[str, dict[str, Any]] = {
    "app/core/continuity.py": {
        "old": "[:500]",
        "new": "[:_WM_TRACE_TRIM]",
        "const": "_WM_TRACE_TRIM",
        "value": 500,
        "comment": "连续性摘要的截断宽度（面板按这个宽度出列表）",
    },
    "app/api/console.py": {
        "old": "[:30]",
        "new": "[:_WM_QUOTE_TRIM]",
        "const": "_WM_QUOTE_TRIM",
        "value": 30,
        "comment": "控制台引言的摘要宽度（跟卡片宽度对齐）",
    },
    "app/memory/cold.py": {
        "old": "[:110]",
        "new": "[:_WM_JOIN_TRIM]",
        "const": "_WM_JOIN_TRIM",
        "value": 110,
        "comment": "冷记忆合并行的展示宽度",
    },
    "app/memory/compress.py": {
        "old": "[:420]",
        "new": "[:_WM_DIGEST_TRIM]",
        "const": "_WM_DIGEST_TRIM",
        "value": 420,
        "comment": "压缩摘要的展示宽度（超出走省略）",
    },
    "app/life/diary.py": {
        "old": "[:300]",
        "new": "[:_WM_DIARY_TRIM]",
        "const": "_WM_DIARY_TRIM",
        "value": 300,
        "comment": "日记一行的预览宽度",
    },
    "app/mind/loop.py": {
        "old": "[:200]",
        "new": "[:_WM_REASON_TRIM]",
        "const": "_WM_REASON_TRIM",
        "value": 200,
        "comment": "异常原因的记录宽度（日志只留这么长）",
    },
    "app/memory/companion.py": {
        "old": "[:90]",
        "new": "[:_WM_PRESENCE_TRIM]",
        "const": "_WM_PRESENCE_TRIM",
        "value": 90,
        "comment": "陪伴卡片的单行预览宽度",
    },
    "app/relation/judge.py": {
        "old": "[:40]",
        "new": "[:_WM_JUDGE_TRIM]",
        "const": "_WM_JUDGE_TRIM",
        "value": 40,
        "comment": "裁判理由入库前的截断宽度",
    },
    "app/web/harvest.py": {
        "old": "[:300]",
        "new": "[:_WM_ERR_TRIM]",
        "const": "_WM_ERR_TRIM",
        "value": 300,
        "comment": "抓取失败原因的记录宽度",
    },
    "app/tools/web.py": {
        "old": "[:120]",
        "new": "[:_WM_TOOL_ERR_TRIM]",
        "const": "_WM_TOOL_ERR_TRIM",
        "value": 120,
        "comment": "工具报错回填给模型的说明宽度",
    },
    "app/voice/tts.py": {
        "old": "[:32]",
        "new": "[:_WM_HASH_LEN]",
        "const": "_WM_HASH_LEN",
        "value": 32,
        "comment": "缓存键取哈希前多少位（改了就换缓存目录）",
    },
    "app/memory/declare.py": {
        "old": "[:3000]",
        "new": "[:_WM_HEAD_CHARS]",
        "const": "_WM_HEAD_CHARS",
        "value": 3000,
        "comment": "声明解析只看正文前这么多个字",
    },
}

#: 锚状态 → 人话
_STATE_LABEL = {
    "ok": "已注入",
    "already": "已是",
    "skipped": "跳过（没命中锚点）",
    "conflict": "冲突（锚点不唯一，不动）",
}


def _anchor_block(recipe: dict[str, Any]) -> str:
    """锚点常量块（含前导空行）。strip 靠它逐字节回滚。"""
    return f"\n# {recipe['comment']}\n{recipe['const']} = {recipe['value']}\n"


def _offset_after_line(text: str, lineno: int | None) -> int:
    if not lineno:
        return 0
    lines = text.splitlines(keepends=True)
    if lineno < 1 or lineno > len(lines):
        return 0
    return sum(len(line) for line in lines[:lineno])


def _parse_py(text: str) -> ast.AST:
    """按 CPython 的读法解析：源码开头的 BOM 会被 utf-8-sig 吃掉，这里等价处理。"""
    return ast.parse(text.removeprefix("\ufeff"))


def _after_imports(text: str) -> int:
    """常量块插在最后一个顶层 import 之后；没有 import 就插在 docstring 之后。"""
    try:
        tree = _parse_py(text)
    except SyntaxError:
        return 0
    last = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node
    if last is not None:
        return _offset_after_line(text, last.end_lineno)
    if tree.body:
        first = tree.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            return _offset_after_line(text, first.end_lineno)
    return 0


def _apply_anchor(text: str, recipe: dict[str, Any]) -> tuple[str, str]:
    """把一处字面量换成同值常量。返回 (新文本, 状态)。"""
    old, new = str(recipe["old"]), str(recipe["new"])
    block = _anchor_block(recipe)
    has_old, has_new = old in text, new in text
    if has_new and not has_old:
        return text, "already" if block in text else "skipped"
    if not has_old and not has_new:
        return text, "skipped"
    if has_old and has_new:
        return text, "conflict"
    if text.count(old) != 1:
        return text, "conflict"
    updated = text.replace(old, new, 1)
    if block not in updated:
        pos = _after_imports(updated)
        updated = updated[:pos] + block + updated[pos:]
    return updated, "ok"


def _revert_anchor(text: str, recipe: dict[str, Any]) -> tuple[str, str]:
    old, new = str(recipe["old"]), str(recipe["new"])
    block = _anchor_block(recipe)
    changed = False
    if new in text and old not in text:
        text = text.replace(new, old, 1)
        changed = True
    if block in text:
        text = text.replace(block, "", 1)
        changed = True
    return text, ("ok" if changed else "already")


def _load_text(path: pathlib.Path) -> str | None:
    """按 utf-8 原样读（不做换行翻译）；二进制或解不开的返回 None。"""
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


def _relpath(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _targets(args: argparse.Namespace, root: pathlib.Path) -> list[pathlib.Path]:
    roots = args.paths or ["app"]
    out: list[pathlib.Path] = []
    for raw in roots:
        p = pathlib.Path(raw)
        if not p.is_absolute():
            p = root / p
        out.extend(wm.source_files(p, include_vendor=args.include_vendor))
    return out


def _run(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root).resolve() if args.root else ROOT
    if args.key_file:
        os.environ[wm.ENV_KEY_FILE] = str(pathlib.Path(args.key_file).resolve())

    if args.init:
        result = wm.write_key(args.key_file, force=args.force)
        if result["ok"]:
            print(f"[新建] {result['path']}（32 字节随机，已 gitignore，别提交）")
            return 0
        print(f"[跳过] {result['path']} 已存在；要覆盖加 --force")
        return 0

    status = wm.key_status()
    print(f"密钥：{status['key_file']}（来源 {status['source']}，状态 {status['state']}）")
    if args.write and not status["signing"]:
        print("[停止] 没有可用密钥（派生盐只能校验、不能签发）。先跑 --init 或设 SYMEM_WM_KEY。")
        return 2

    targets = _targets(args, root)
    changed = same = skipped = failed = 0
    mode = "剥除" if args.strip else "注入"
    for path in targets:
        rel = _relpath(path, root)
        text = _load_text(path)
        if text is None:
            skipped += 1
            print(f"[跳过] {rel}（二进制或非 utf-8）")
            continue
        notes: list[str] = []
        new = text
        if path.suffix == ".py" and rel in ANCHOR_SITES and (args.anchors or args.strip):
            recipe = ANCHOR_SITES[rel]
            new, st = _revert_anchor(new, recipe) if args.strip else _apply_anchor(new, recipe)
            notes.append(f"锚:{_STATE_LABEL.get(st, st)}")
        if args.strip:
            new = wm.strip(new)
        else:
            new = wm.embed(new, rel)

        if path.suffix == ".py":
            try:
                _parse_py(new)
            except SyntaxError as exc:
                failed += 1
                print(f"[失败] {rel}：改完语法不通（{exc.msg} 第 {exc.lineno} 行），未写入")
                continue

        if new == text:
            same += 1
            if notes:
                print(f"[不变] {rel}（{'；'.join(notes)}）")
            continue

        if not args.strip and not wm.verify(new, rel):
            failed += 1
            print(f"[失败] {rel}：写入前自校验没过，未写入")
            continue

        changed += 1
        suffix = f"（{'；'.join(notes)}）" if notes else ""
        if args.dry_run:
            print(f"[将{mode}] {rel}{suffix}")
            continue
        try:
            path.write_bytes(new.encode("utf-8"))
        except OSError as exc:
            failed += 1
            print(f"[失败] {rel}：{exc}")
            continue
        print(f"[{mode}] {rel}{suffix}")

    head = "（dry-run，未落盘）" if args.dry_run else ""
    print(
        f"合计：{mode} {changed}{head} / 已是 {same} / 跳过 {skipped} / 失败 {failed}"
        f"；目标 {len(targets)} 个文件"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SymBot 软水印：注入 / 剥除（docs/33）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--init", action="store_true", help="生成密钥文件（32 字节随机 hex）")
    group.add_argument("--write", action="store_true", help="注入水印")
    group.add_argument("--strip", action="store_true", help="剥掉水印（自测/回滚用）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--anchors", action="store_true", help="同时注入派生常量锚（会动逻辑）")
    ap.add_argument("--paths", nargs="+", default=None, help="目标路径（默认 app/）")
    ap.add_argument("--include-vendor", action="store_true", help="连 vendor/ 一起处理")
    ap.add_argument("--root", default="", help="重定位根（自测用；默认仓库根）")
    ap.add_argument("--key-file", default="", help="覆盖密钥文件路径")
    ap.add_argument("--force", action="store_true", help="--init 时覆盖已有密钥")
    args = ap.parse_args(argv)

    if not (args.init or args.write or args.strip):
        ap.print_help()
        return 2
    if args.anchors and not (args.write or args.strip):
        ap.error("--anchors 需要配合 --write 或 --strip")
    if args.dry_run and not (args.write or args.strip):
        ap.error("--dry-run 需要配合 --write 或 --strip")
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
