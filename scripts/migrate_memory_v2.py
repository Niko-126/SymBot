"""衡忆 2.0 迁移 / 诊断 CLI（docs/49 §6）。

正常情况下不用手动跑：`app/core/db.py` 的 `init()` 每次启动都会幂等地调
`memv2.ensure_ready()`。这个脚本是给"想在启动前先看一眼 / 想在服务器上单独迁移"用的。

用法：
    python scripts/migrate_memory_v2.py --status      # 只诊断，不写
    python scripts/migrate_memory_v2.py --dry-run     # 预演：会搬多少行、要不要备份
    python scripts/migrate_memory_v2.py --apply       # 真迁移（默认先备份整库）
    python scripts/migrate_memory_v2.py --apply --no-backup

退出码：0 成功 / 1 失败。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="衡忆 2.0：旧 memories 表 → 引擎 mem_item")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="只诊断当前状态")
    group.add_argument("--dry-run", action="store_true", help="预演，不写库")
    group.add_argument("--apply", action="store_true", help="执行迁移（默认动作）")
    ap.add_argument("--no-backup", action="store_true", help="不备份整库（不建议）")
    args = ap.parse_args(argv)

    from app.core import config
    from app.memory import memv2

    db_path = config.get().db_file
    print(f"库文件：{db_path}")

    try:
        if args.status:
            result = memv2.status()
        elif args.dry_run:
            result = memv2.ensure_ready(dry_run=True)
        else:
            result = memv2.ensure_ready(backup=not args.no_backup)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] {type(exc).__name__}: {exc}")
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    ok = bool(result.get("ok", True))
    print("[OK]" if ok else "[失败]")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
