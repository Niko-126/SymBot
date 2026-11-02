"""把一次对抗训练的全部条目摊开成清单（给人看 / 给 AI 复核）。

用法：
    .\.venv\Scripts\python.exe scripts\adv_list.py                     # 最新一次 run
    .\.venv\Scripts\python.exe scripts\adv_list.py --run data/_adv/run-20261031-201500
    .\.venv\Scripts\python.exe scripts\adv_list.py --status RAISED,NOTRUN --kind fn,route
    .\.venv\Scripts\python.exe scripts\adv_list.py --out list.md

它只读 `items.json`，不执行任何东西。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def latest_run() -> pathlib.Path | None:
    base = ROOT / "data" / "_adv"
    if not base.exists():
        return None
    runs = sorted([p for p in base.iterdir() if (p / "items.json").exists()], key=lambda p: p.name)
    return runs[-1] if runs else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("adv_list")
    ap.add_argument("--run", default="")
    ap.add_argument("--status", default="", help="逗号分隔，如 RAISED,NOTRUN,SKIP")
    ap.add_argument("--kind", default="", help="逗号分隔，如 fn,method,route,cli,table,config,scenario")
    ap.add_argument("--module", default="", help="模块前缀过滤，如 app.memory")
    ap.add_argument("--max", type=int, default=0, help="最多显示多少条（0=全部）")
    ap.add_argument("--out", default="", help="写成 markdown 文件")
    a = ap.parse_args(argv)

    run = pathlib.Path(a.run) if a.run else latest_run()
    if run is None or not (run / "items.json").exists():
        print("没有找到 items.json，先跑 scripts/adv_full.py --phase inventory", file=sys.stderr)
        return 2
    data = json.loads((run / "items.json").read_text(encoding="utf-8"))
    items = data.get("items") or []

    st = {x.strip() for x in a.status.split(",") if x.strip()}
    kd = {x.strip() for x in a.kind.split(",") if x.strip()}
    if st:
        items = [i for i in items if i.get("status") in st]
    if kd:
        items = [i for i in items if i.get("kind") in kd]
    if a.module:
        items = [i for i in items if str(i.get("module", "")).startswith(a.module)]
    if a.max:
        items = items[: a.max]

    lines = [
        f"# 条目清单 · {run.name}",
        "",
        f"- 生成：{data.get('generated_at')}",
        f"- 总计：{len(items)} 条（过滤后）",
        f"- 状态分布：{json.dumps(data.get('counts') or {}, ensure_ascii=False)}",
        "",
        "| ID | kind | risk | plan | status | module:line | name | detail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i in items:
        loc = f"{i.get('module','')}:{i.get('line','')}" if i.get("line") else str(i.get("module", ""))
        detail = str(i.get("detail", "")).replace("|", "\\|")[:120]
        name = str(i.get("name", "")).replace("|", "\\|")[:80]
        lines.append(
            f"| {i.get('id')} | {i.get('kind')} | {i.get('risk')} | {i.get('plan')} "
            f"| {i.get('status')} | {loc} | {name} | {detail} |"
        )
    text = "\n".join(lines) + "\n"

    if a.out:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")
        print(f"written {a.out}  items={len(items)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
