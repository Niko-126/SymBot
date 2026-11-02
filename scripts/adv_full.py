r"""对抗式全量训练 · 编排入口。

用法：
    .\.venv\Scripts\python.exe scripts\adv_full.py                 # 全量（offline，零成本）
    .\.venv\Scripts\python.exe scripts\adv_full.py --phase inventory,routes
    .\.venv\Scripts\python.exe scripts\adv_full.py --phase duel --only S01,S03 --turns 6
    .\.venv\Scripts\python.exe scripts\adv_full.py --phase functions --levels L0,L1 --limit 200
    .\.venv\Scripts\python.exe scripts\adv_full.py --allow-live --mode live --turns 4   # 真模型（花钱）

产物：data/_adv/<run-id>/{manifest.json,items.json,findings.json,report.md,report.json,phases/*.json,transcripts/*}
退出码：有 P0 → 1；否则 0；Phase 崩溃 → 2。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 1) 沙箱必须最先进：把 SYMEM_DATA / SYMEM_DB / MOCK 钉死，之后 import app.* 才安全
from app.qa import sandbox  # noqa: E402  （不 import app.core，安全）

TMP = sandbox.prepare_env("full", keep=("--no-keep" not in sys.argv))

from app.qa import registry as registry_mod  # noqa: E402
from app.qa import types  # noqa: E402

PHASES = ("inventory", "static", "routes", "functions", "cli", "duel", "coverage", "report")


# --------------------------------------------------------------------- helpers
def _git(field: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=false", *field.split()],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _phase_result(name: str, *, items=None, findings=None, notes: str = "") -> types.PhaseResult:
    pr = types.PhaseResult(phase=name, items=list(items or []), findings=list(findings or []), notes=notes)
    return pr.finish()


def _say(msg: str) -> None:
    print(f"[adv] {msg}", flush=True)


def _dump(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _collect(phases: dict[str, types.PhaseResult]) -> tuple[list[types.Finding], list[str]]:
    findings: list[types.Finding] = []
    notes: list[str] = []
    for name, pr in phases.items():
        findings.extend(pr.findings)
        if pr.notes:
            notes.append(f"{name}: {pr.notes}")
    return findings, notes


# ---------------------------------------------------------------------- phases
def phase_inventory(reg: registry_mod.Registry) -> types.PhaseResult:
    t0 = time.time()
    try:
        from app.qa import inventory
    except Exception as exc:  # noqa: BLE001
        return _phase_result("inventory", notes=f"模块不可用：{type(exc).__name__}: {exc}")

    items = inventory.build()
    reg.add_many(items)
    man = inventory.manifest()
    _say(f"inventory: {len(items)} 条清单，{time.time() - t0:.2f}s  {man.get('counts')}")
    return _phase_result("inventory", items=items,
                         notes=f"manifest={json.dumps(man, ensure_ascii=False)[:1500]}")


def phase_static(reg: registry_mod.Registry) -> types.PhaseResult:
    """module/class/config/persona/table 这些"非函数"条目的真实验证。"""
    try:
        from app.qa import static_probe
    except Exception as exc:  # noqa: BLE001
        return _phase_result("static", notes=f"模块不可用：{type(exc).__name__}: {exc}")
    n = static_probe.register(reg)
    _say(f"static: 待验 {n} 条（module/class/config/persona/table）")
    res = static_probe.run(reg)
    _say(f"static: {res.counts()}  {res.secs}s")
    return res


def phase_routes(reg: registry_mod.Registry) -> types.PhaseResult:
    try:
        from app.qa import routes_fuzz
    except Exception as exc:  # noqa: BLE001
        return _phase_result("routes", notes=f"模块不可用：{type(exc).__name__}: {exc}")
    n = routes_fuzz.register(reg)
    _say(f"routes: 注册 {n} 条，开始逐个打…")
    res = asyncio.run(routes_fuzz.run(reg))
    _say(f"routes: {res.counts()}  {res.secs}s")
    return res


def phase_functions(reg: registry_mod.Registry, args) -> types.PhaseResult:
    try:
        from app.qa import probe
    except Exception as exc:  # noqa: BLE001
        return _phase_result("functions", notes=f"模块不可用：{type(exc).__name__}: {exc}")
    targets = None
    try:
        from app.qa import inventory

        targets = inventory.probe_targets()
    except Exception:  # noqa: BLE001
        targets = None
    n = probe.register(reg, targets)
    levels = tuple(x.strip() for x in str(args.levels).split(",") if x.strip())
    _say(f"functions: 注册 {n} 个可调用对象，levels={levels} limit={args.limit}")
    res = asyncio.run(probe.run(reg, levels=levels, limit=args.limit))
    _say(f"functions: {res.counts()}  {res.secs}s")
    return res


def phase_cli(reg: registry_mod.Registry) -> types.PhaseResult:
    try:
        from app.qa import cli_probe
    except Exception as exc:  # noqa: BLE001
        return _phase_result("cli", notes=f"模块不可用：{type(exc).__name__}: {exc}")
    n = cli_probe.register(reg)
    _say(f"cli: 注册 {n} 条（子命令 + 后台任务），开始子进程跑…")
    res = cli_probe.run(reg)
    _say(f"cli: {res.counts()}  {res.secs}s")
    return res


def phase_duel(reg: registry_mod.Registry, args) -> tuple[types.PhaseResult, list[types.TurnRecord]]:
    try:
        from app.qa import duel
    except Exception as exc:  # noqa: BLE001
        return _phase_result("duel", notes=f"模块不可用：{type(exc).__name__}: {exc}"), []
    try:
        from app.qa import scenarios

        n = scenarios.register(reg)
        _say(f"duel: 场景 {n} 条")
    except Exception as exc:  # noqa: BLE001
        n = -1
        _say(f"duel: scenarios 不可用（{type(exc).__name__}），交给 duel 自己兜底")

    only = [x.strip() for x in str(args.only).split(",") if x.strip()] if args.only else None
    mode = args.mode
    if mode == "live" and not args.allow_live:
        _say("duel: 未加 --allow-live，强制回落 offline（省钱/防误触）")
        mode = "offline"
    tdir = args.out / "transcripts"
    res, turns = asyncio.run(duel.run(reg, mode=mode, only=only, turns=args.turns,
                                     budget=args.budget, transcripts_dir=tdir))
    _say(f"duel: {res.counts()}  {res.secs}s  轮数={len(turns)}  mode={mode}")
    return res, turns


def phase_coverage(reg: registry_mod.Registry, tracer) -> types.PhaseResult:
    try:
        from app.qa import coverage
    except Exception as exc:  # noqa: BLE001
        return _phase_result("coverage", notes=f"模块不可用：{type(exc).__name__}: {exc}")
    try:
        rep = coverage.report(reg)
    except Exception as exc:  # noqa: BLE001
        return _phase_result("coverage", notes=f"report 失败：{type(exc).__name__}: {exc}")
    try:
        if tracer is not None:
            rep["runtime_tracer_hits"] = len(getattr(tracer, "hits", {}) or {})
    except Exception:  # noqa: BLE001
        pass
    _say(f"coverage: {rep.get('covered')}/{rep.get('total')} ({rep.get('ratio')})")
    return _phase_result("coverage", notes=json.dumps(rep, ensure_ascii=False)[:2000])


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("adv_full", description="对抗式全量训练编排器")
    ap.add_argument("--phase", default="all", help="all 或逗号分隔：" + ",".join(PHASES))
    ap.add_argument("--mode", default="offline", choices=["offline", "live"])
    ap.add_argument("--allow-live", action="store_true", help="允许真模型（花钱）")
    ap.add_argument("--only", default="", help="只跑指定场景 id，如 S01,S03")
    ap.add_argument("--turns", type=int, default=0, help="0=用场景脚本全量")
    ap.add_argument("--budget", type=int, default=200000, help="live 模式累计 token 上限")
    ap.add_argument("--levels", default="L0,L1,L2")
    ap.add_argument("--limit", type=int, default=0, help="functions 阶段最多跑多少个（0=全部）")
    ap.add_argument("--out", default="", help="产物目录（默认 data/_adv/<run-id>）")
    ap.add_argument("--no-keep", action="store_true", help="结束后删临时数据根")
    args = ap.parse_args(argv)
    args.limit = args.limit or None

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path(args.out) if args.out else (ROOT / "data" / "_adv" / f"run-{run_id}")
    args.out = out
    out.mkdir(parents=True, exist_ok=True)
    (out / "phases").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    want = PHASES if args.phase == "all" else tuple(x.strip() for x in args.phase.split(",") if x.strip())
    _say(f"run-id={run_id}  产物={out}")
    _say(f"临时数据根={TMP}")

    reg = registry_mod.Registry()
    phases: dict[str, types.PhaseResult] = {}
    turns: list[types.TurnRecord] = []
    tracer = None

    # 覆盖率要包住所有 phase
    try:
        from app.qa import coverage as coverage_mod

        tracer = coverage_mod.Tracer()
        tracer.__enter__()
    except Exception as exc:  # noqa: BLE001
        _say(f"coverage tracer 未启用：{type(exc).__name__}: {exc}")
        tracer = None

    started = time.time()
    try:
        if "inventory" in want:
            phases["inventory"] = phase_inventory(reg)
        if "static" in want:
            phases["static"] = phase_static(reg)
        if "routes" in want:
            phases["routes"] = phase_routes(reg)
        if "functions" in want:
            phases["functions"] = phase_functions(reg, args)
        if "cli" in want:
            phases["cli"] = phase_cli(reg)
        if "duel" in want:
            pr, turns = phase_duel(reg, args)
            phases["duel"] = pr
        if "coverage" in want:
            phases["coverage"] = phase_coverage(reg, tracer)
    except KeyboardInterrupt:
        _say("被中断，已跑完的 phase 会照样落盘")
    except Exception as exc:  # noqa: BLE001
        _say(f"编排异常：{type(exc).__name__}: {exc}")
        traceback.print_exc()
        phases.setdefault("_error", _phase_result("_error", notes=f"{type(exc).__name__}: {exc}"))
    finally:
        if tracer is not None:
            try:
                tracer.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        try:
            elapsed = round(time.time() - started, 2)
            last_phase = phase_coverage(reg, tracer)
            _dump(out / "phases" / "coverage.json", last_phase.to_dict())
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 落盘
    findings, notes = _collect(phases)
    try:
        reg.save(out / "items.json")
    except Exception as exc:  # noqa: BLE001
        _say(f"items.json 落盘失败：{exc}")
    _dump(out / "findings.json", [f.to_dict() for f in findings])
    for name, pr in phases.items():
        _dump(out / "phases" / f"{name}.json", pr.to_dict())
    _dump(out / "turns.json", [t.to_dict() for t in turns])

    manifest = {
        "run_id": run_id,
        "out": str(out),
        "tmp_data_root": str(TMP),
        "cwd": str(ROOT),
        "argv": sys.argv[1:],
        "phases": list(phases),
        "head": _git("rev-parse HEAD"),
        "branch": _git("rev-parse --abbrev-ref HEAD"),
        "status_porcelain_lines": len([x for x in _git("status --porcelain").splitlines() if x]),
        "python": sys.version,
        "env": {k: sandbox.mask(v) for k, v in sandbox.env_report().items()},
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "elapsed_s": round(time.time() - started, 2),
        "registry": reg.counts(),
        "findings": _severity_counts(findings),
        "notes": notes,
    }
    _dump(out / "manifest.json", manifest)

    # ---------------------------------------------------------------- 报告
    if "report" in want or args.phase == "all":
        try:
            from app.qa import report as report_mod

            path = report_mod.write(out, registry=reg, phases=phases, findings=findings,
                                   turns=turns, meta=manifest)
            _say(f"报告：{path}")
        except Exception as exc:  # noqa: BLE001
            _say(f"报告生成失败：{type(exc).__name__}: {exc}")
            traceback.print_exc()

    # ---------------------------------------------------------------- 摘要
    sev = _severity_counts(findings)
    _say("=" * 60)
    _say(f"条目 {len(reg.items)}  状态 {reg.counts()}")
    _say(f"缺陷 P0={sev.get('P0', 0)} P1={sev.get('P1', 0)} P2={sev.get('P2', 0)} INFO={sev.get('INFO', 0)}")
    for it in sorted(reg.unresolved(), key=lambda x: x.id)[:40]:
        _say(f"  ! {it.status:6} {it.id}  {it.detail[:100]}")
    _say(f"耗时 {round(time.time() - started, 1)}s  产物 {out}")
    return 1 if sev.get("P0", 0) > 0 else 0


def _severity_counts(findings: list[types.Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
