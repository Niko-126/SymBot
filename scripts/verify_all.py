"""全功能自检总控：一条命令跑完所有自检，产出可复核的 Markdown 报告。

为什么要有它：交接/发布前最容易出的事不是功能坏，而是"只跑了 smoke 就以为全绿"。
这个脚本按依赖顺序跑全部离线自检，逐项记录退出码、耗时、关键结论行，
失败就直接退出 1，方便 CI 或人工复核。

用法（仓库根目录）：
    python scripts/verify_all.py            # 全跑（含全量体检，约 3–5 分钟）
    python scripts/verify_all.py --quick    # 跳过 fullcheck / handoff
    python scripts/verify_all.py --no-report  # 只打印，不写报告

约束：不改数据库（各脚本自己用临时库）、不联网、不打包。
"""

from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
REPORT_DIR = ROOT / "data" / "human_life" / "_reports"

#: (名字, 命令, 超时秒, 是否 quick 也跑)
CHECKS: tuple[tuple[str, list[str], int, bool], ...] = (
    ("编译检查", [PY, "-m", "compileall", "-q", "app", "scripts"], 180, True),
    ("smoke", [PY, "scripts/smoke.py"], 900, True),
    ("语料自测", [PY, "scripts/test_lifecorpus.py"], 300, True),
    ("亲密", [PY, "scripts/test_intimacy.py"], 300, True),
    ("全量体检", [PY, "scripts/fullcheck.py"], 900, False),
    ("拟人度", [PY, "scripts/humanity_check.py"], 300, True),
    ("交接快照", [PY, "scripts/handoff.py", "--test"], 300, False),
    ("语料校验", [PY, "scripts/life_corpus.py", "validate", "data/human_life/_derived", "--strict"], 120, True),
    ("语料覆盖", [PY, "scripts/life_corpus.py", "coverage", "data/human_life/_derived", "--strict"], 120, True),
    ("记忆引擎", [PY, "scripts/migrate_memory_v2.py", "--status"], 120, True),
    ("面板自检", [PY, "scripts/panel_check.py"], 300, True),
)

#: 需要外部语料才跑得动的检查：路径不存在 → SKIP（带 reason），不判红
_OPTIONAL_INPUTS: dict[str, str] = {
    "语料校验": "data/human_life/_derived",
    "语料覆盖": "data/human_life/_derived",
}

#: 从输出里抓"结论行"，报告里直接用，避免人工二次解读
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("smoke", re.compile(r"结果：(\d+) 通过，(\d+) 失败")),
    ("语料自测", re.compile(r"通过 (\d+) / 失败 (\d+)")),
    ("亲密", re.compile(r"结果：(\d+) 通过，(\d+) 失败")),
    ("全量体检", re.compile(r"总计：(\d+)/(\d+) 通过[^\n]*")),
    ("拟人度", re.compile(r"拟人度总分：(\d+)/(\d+)")),
    ("面板自检", re.compile(r"结果：(\d+) 通过，(\d+) 失败")),
)


def _run(name: str, command: list[str], timeout: int) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        output = f"[timeout {timeout}s] {exc}"
    except OSError as exc:
        code = 2
        output = f"[spawn failed] {exc}"
    elapsed = round(time.perf_counter() - started, 1)

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    tail = lines[-6:]
    verdict = ""
    for check_name, pattern in _PATTERNS:
        if check_name != name:
            continue
        for line in lines:
            match = pattern.search(line)
            if match:
                verdict = match.group(0)
                break
    return {
        "name": name,
        "command": " ".join(command[1:]),
        "code": code,
        "elapsed": elapsed,
        "verdict": verdict,
        "tail": tail,
        "output": output,
    }


def _render_markdown(results: list[dict], *, started_at: str) -> str:
    ok = sum(1 for item in results if item["code"] == 0)
    lines = [
        "# 全功能自检报告",
        "",
        f"- 生成时间：{started_at}",
        f"- 仓库：`{ROOT}`",
        f"- 结果：**{ok}/{len(results)} 项退出码为 0**",
        "- 说明：本报告由 `scripts/verify_all.py` 自动生成；各项输出只留结论行与末尾几行，"
        "完整输出请重跑对应命令。",
        "",
        "| 检查 | 退出码 | 耗时 | 结论行 |",
        "| --- | --- | --- | --- |",
    ]
    for item in results:
        verdict = item["verdict"].replace("|", "/") or "（无解析到的结论行，见 tail）"
        lines.append(
            f"| {item['name']} | {item['code']} | {item['elapsed']}s | {verdict} |"
        )
    lines.extend(["", "## 明细", ""])
    for item in results:
        lines.append(f"### {item['name']}（exit {item['code']}，{item['elapsed']}s）")
        lines.append("")
        lines.append(f"```text\n$ {item['command']}\n" + "\n".join(item["tail"]) + "\n```")
        lines.append("")
    failed = [item["name"] for item in results if item["code"] != 0]
    lines.append("## 未通过" if failed else "## 结论")
    lines.append("")
    # 条件表达式优先级低于 +：先算 summary 再拼尾巴。
    # 以前写成一行，failed 非空时后面那整段解释文字会被吞掉（docs/23 §3.4 记过）。
    summary = ("未通过：" + "、".join(failed)) if failed else "全部检查退出码为 0。"
    lines.append(
        summary
        + " \"退出码 0\" 只代表脚本自己判定通过；打包、真实模型、QQ 实机、浏览器的真实渲染不在本脚本范围内"
        "（面板契约由 panel_check 静态校验）。"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_all", description="全功能自检总控（离线）")
    parser.add_argument("--quick", action="store_true", help="跳过 fullcheck 与 handoff（约 1 分钟）")
    parser.add_argument("--no-report", action="store_true", help="只打印，不写 Markdown 报告")
    args = parser.parse_args(argv)

    selected = [item for item in CHECKS if (not args.quick or item[3])]
    results: list[dict] = []
    started_at = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"全功能自检：{len(selected)} 项（{'quick' if args.quick else 'full'}）")
    for name, command, timeout, _ in selected:
        # 需要外部语料的检查：干净 clone 上 data/ 根本不存在（已 gitignore），
        # 这时按 SKIP 记账并写清 reason，而不是判红——否则新环境永远"9/11"。
        need = _OPTIONAL_INPUTS.get(name)
        if need is not None and not (ROOT / need).exists():
            results.append({
                "name": name, "code": 0, "elapsed": 0.0,
                "verdict": f"SKIP: {need} 不存在",
                "tail": [f"跳过：{need} 不存在（data/ 不入库，需自己准备语料）"],
                "command": " ".join(command), "skipped": True,
            })
            print(f"  → {name} ... SKIP  {need} 不存在")
            continue
        print(f"  → {name} ...", end="", flush=True)
        item = _run(name, command, timeout)
        results.append(item)
        mark = "OK" if item["code"] == 0 else f"FAIL({item['code']})"
        extra = f"  {item['verdict']}" if item["verdict"] else ""
        print(f" {mark}  {item['elapsed']}s{extra}")

    skipped = [item["name"] for item in results if item.get("skipped")]
    ok = sum(1 for item in results if item["code"] == 0 and not item.get("skipped"))
    print(f"\n汇总：{ok}/{len(results) - len(skipped)} 项通过", end="")
    if skipped:
        print(f"（另有 {len(skipped)} 项 SKIP：{'、'.join(skipped)}）")
    else:
        print()

    if not args.no_report:
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = REPORT_DIR / f"verify-{stamp}.md"
            path.write_text(_render_markdown(results, started_at=started_at), encoding="utf-8")
            print(f"报告：{path}")
        except OSError as exc:
            print(f"报告写入失败：{exc}")
    return 0 if ok == (len(results) - len(skipped)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
