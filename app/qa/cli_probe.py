"""CLI 全子命令 + 后台任务探针（phase = ``cli``）。

## 这个 phase 回答什么问题

1. **`app/cli.py` 的 10 个子命令是不是真的能跑**——不是静态能 import，而是
   `sys.executable -m app.cli <cmd> ...` **真起子进程**、拿真退出码、看真输出。
2. **后台任务在 mock 下有没有偷偷启动**——`app/main.py` 有 3 个 `@app.on_event("startup")`
   （主动外发 / 热梗抓取 / QQ 官方 bot），它们全部是「闸门不满足就 `return`」的写法。
   闸门写错一次就会：给真好友发消息、抓 B 站、连 QQ 网关。本模块把这条**当作契约来验**。

## 硬规则（见 ``app/qa/CONTRACT.md`` §0）

1. **模块级只 import `app.qa.*`**。`app.core` / `app.main` / `app.life` / `app.web` /
   `app.adapters` 全部在函数内惰性 import——因为 ``sandbox.prepare_env()`` 必须早于
   ``app.core.paths`` 被 import（paths.py 在 import 期就把 ``SYMEM_DATA`` 读死了）。
2. **子进程穿环境**：每个子进程都拿到 ``SYMEM_DATA / SYMEM_DB`` 指向临时根、
   ``SYMEM_MOCK=1``、``SYMEM_QQ_ENABLED=0``、``SYMEM_ONEBOT_ENABLED=0``、``PYTHONIOENCODING=utf-8``；
   父进程环境里的 ``SYMEM_* / AI_LIFE_*`` 一律剥掉再接（防止 shell 里的 ``AI_LIFE_DATA``
   或真 key 漏进子进程）。
3. **本模块从不打开仓库里的真库**。行数统计只读临时库，且一律 ``mode=ro``；
   WAL 没 checkpoint 导致 ro 打不开时，退化成「复制三件套到 temp 再读副本」。
4. **``serve`` 永不起**。它会 bind 8787 并阻塞；启动链路的可用性由 ``CLI:init`` /
   ``CLI:--help`` + 3 个 worker 项覆盖，不需要真起服务。

## 判定表

| 情况 | item.status | Finding |
|---|---|---|
| 退出码 == 预期 | OK | — |
| 退出码 != 预期 | RAISED | **P0**（附 stderr 末 30 行） |
| 超时 | HANG | P1 |
| 退出码对、但输出含 ``Traceback``（正向用例） | RAISED | **P0** |
| 退出码对、输出含 ``Traceback``（负向用例，如缺文件） | OK + ``extra.traceback=True`` | P2（可 `SYMEM_ADV_STRICT_TRACEBACK=1` 升级为 P0） |
| 子进程起不来（OSError） | RAISED | **P0** |
| startup 期间被创建任务 / worker 真的启动 | OK↔RAISED | P1（按 lead 口径） |
| startup 期间尝试外网（被 `network_guard` 拦下） | — | **P0**（真发消息/真抓取的直接证据，比 P1 更严重） |

> 为什么负向用例的 traceback 不直接判 P0：实测 ``python -m app.cli ingest 不存在的文件``
> 退出码是 1（符合预期），但 ``FileNotFoundError`` 未被 ``cli.py`` 捕获，会打一整段栈。
> 这是「CLI 缺友好提示」的 P2 打磨项，不是崩溃；把它算 P0 会让每轮报告都挂一个假 P0。

## 后台任务怎么验（不靠读代码，靠观测）

子进程里：``sandbox.prepare_env()`` → ``network_guard()`` → import ``app.main``（模块级
``app = create_app()`` 会先跑一遍，不触发 startup）→ 打桩 ``app.web.harvest.tick`` 与
``app.life.proactive.prepare_tick`` → ``create_app()`` → 用
``app.router.lifespan_context(app)`` **真跑 startup/shutdown** → ``await sleep(0.25)`` 让
``asyncio.create_task`` 有机会跑起来 → 数新任务 / 看打桩命中 / 看
``app.adapters.qq._TASK`` / 看 ``network_guard`` 的 blocked 列表 → 吐一行 JSON 回父进程。

> Starlette 1.10 已移除 ``Router.startup()``（只剩 ``lifespan_context``），所以不能直接
> ``await app.router.startup()``；``httpx.ASGITransport`` 本身不跑 lifespan，两者要配合用。

## 自测

```powershell
# 只跑自测（会用临时数据根，不碰真库）
.venv\\Scripts\\python.exe -m app.qa.cli_probe --self

# 连 40MB 的参考语料导入一起跑（默认 SKIP）
$env:SYMEM_ADV_ALLOW_HEAVY=1; .venv\\Scripts\\python.exe -m app.qa.cli_probe --self --json C:\\Temp\\cli.json

# 子进程模式（父进程用，一般不用手敲）
.venv\\Scripts\\python.exe -m app.qa.cli_probe --boot-json
```
﻿‍‍﻿‌﻿‍‍‍﻿﻿‌﻿﻿‌​‍﻿​​​‍﻿﻿‌​﻿‍​​‍‌​﻿"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any

from app.qa import registry as reg_mod
from app.qa import sandbox
from app.qa import types

PHASE = "cli"

#: 允许重量级用例（reference 全量导入 40MB 语料）的开关
HEAVY_ENV = "SYMEM_ADV_ALLOW_HEAVY"
#: 负向用例出现 traceback 是否升级为 P0
STRICT_TB_ENV = "SYMEM_ADV_STRICT_TRACEBACK"
#: 子进程启动标记（方便子进程里判断自己是被探针拉起来的）
CHILD_ENV = "SYMEM_ADV_CHILD"

#: 子进程输出保留上限（字符），避免 40MB 语料的报告把内存吃光
_MAX_TEXT = 200_000
#: detail 里保留的 stdout 行数 / stderr 行数
_HEAD_LINES = 3
_TAIL_LINES = 30

#: 缺文件负向用例用的路径（故意不存在）
_MISSING_REL = "samples/__这个文件不存在__.txt"
#: 正向 ingest 用的样例（1237 B，仓库自带）
_SAMPLE_REL = "samples/记忆交接信_示例.txt"
#: 参考语料目录（96 个 part*.txt，约 40MB）
_CORPUS_REL = "语料/jilu"

_CASES: list[dict[str, Any]] = [
    {
        "id": "CLI:help",
        "name": "app.cli --help",
        "argv": ["-m", "app.cli", "--help"],
        "expect": 0,
        "timeout_s": 60,
        "risk": "read",
        "line": 303,
        "note": "argparse 元信息：prog 名 + 10 个子命令是否齐全",
    },
    {
        "id": "CLI:init",
        "name": "app.cli init",
        "argv": ["-m", "app.cli", "init"],
        "expect": 0,
        "timeout_s": 90,
        "risk": "write",
        "line": 314,
        "note": "建库+读人设+预检；副作用看 life.db 表数是否从 0 变 38",
    },
    {
        "id": "CLI:letter",
        "name": "app.cli letter",
        "argv": ["-m", "app.cli", "letter"],
        "expect": 0,
        "timeout_s": 60,
        "risk": "read",
        "line": 332,
        "note": "打印记忆交接信模板（人设包内的模板）",
    },
    {
        "id": "CLI:ingest",
        "name": "app.cli ingest <样例>",
        "argv": ["-m", "app.cli", "ingest", _SAMPLE_REL],
        "expect": 0,
        "timeout_s": 120,
        "risk": "write",
        "line": 310,
        "note": "导入样例聊天记录；副作用看 events / mem_item / mem_event 是否 +",
    },
    {
        "id": "CLI:ingest_missing",
        "name": "app.cli ingest <不存在的文件>",
        "argv": ["-m", "app.cli", "ingest", _MISSING_REL],
        "expect": 1,
        "timeout_s": 60,
        "risk": "read",
        "line": 310,
        "negative": True,
        "note": "负向用例：退出码必须 1（cli.py:29 把 ok=false 映射成 1）",
    },
    {
        "id": "CLI:arc",
        "name": "app.cli arc <语料/jilu>",
        "argv": ["-m", "app.cli", "arc", _CORPUS_REL],
        "expect": 0,
        "timeout_s": 240,
        "risk": "read",
        "line": 340,
        "note": "只做情绪分期分析、不写库；实测 ~3s（96 个 part*.txt）",
    },
    {
        "id": "CLI:anchors",
        "name": "app.cli anchors --per-scene 4 --extra 4",
        "argv": ["-m", "app.cli", "anchors", "--per-scene", "4", "--extra", "4"],
        "expect": 0,
        "timeout_s": 120,
        "risk": "write",
        "line": 320,
        "note": "从 ref_turns 重建锚点池；空库应得 candidates=0 而不是崩",
    },
    {
        "id": "CLI:cold",
        "name": "app.cli cold --dry",
        "argv": ["-m", "app.cli", "cold", "--dry"],
        "expect": 0,
        "timeout_s": 120,
        "risk": "write",
        "line": 325,
        "note": "--dry 只预览；要验证真实合并得去掉 --dry（本 phase 不做）",
    },
    {
        "id": "CLI:compress",
        "name": "app.cli compress",
        "argv": ["-m", "app.cli", "compress"],
        "expect": 0,
        "timeout_s": 120,
        "risk": "write",
        "line": 329,
        "note": "把旧对话折成线索；空库返回 ok=false 但退出码仍须 0",
    },
    {
        "id": "CLI:reference",
        "name": "app.cli reference <语料/jilu>",
        "argv": ["-m", "app.cli", "reference", _CORPUS_REL],
        "expect": 0,
        "timeout_s": 300,
        "risk": "write",
        "line": 335,
        "heavy": True,
        "note": "重活：40MB/96 卷，十万级 ref_turns 落库；默认 SKIP，SYMEM_ADV_ALLOW_HEAVY=1 才跑",
    },
    {
        "id": "CLI:reset",
        "name": "app.cli reset",
        "argv": ["-m", "app.cli", "reset"],
        "expect": 0,
        "timeout_s": 90,
        "risk": "danger",
        "line": 317,
        "note": "删库重建；必须放在最后（否则前面的行数增量无从对比）",
    },
    {
        "id": "CLI:serve",
        "name": "app.cli serve",
        "argv": ["-m", "app.cli", "serve", "--port", "8787"],
        "expect": None,
        "timeout_s": 0,
        "risk": "hang",
        "line": 306,
        "note": "会 bind 8787 并阻塞 —— 本 phase 永不执行，只登记为 SKIP",
    },
]

_WORKERS: list[dict[str, Any]] = [
    {
        "id": "WORKER:proactive_push",
        "name": "startup · 主动外发（_proactive_worker）",
        "line": 52,
        "note": "闸门 onebot.enabled and onebot.proactive_push；mock 下必须整段 return",
        "gate": "onebot.enabled",
        "signal": "proactive",
    },
    {
        "id": "WORKER:harvest",
        "name": "startup · 热梗抓取（_harvest_worker）",
        "line": 100,
        "note": "闸门 trend.harvest_enabled and not provider.is_mock；mock 下必须不联网",
        "gate": "provider.is_mock",
        "signal": "harvest",
    },
    {
        "id": "WORKER:qq_official",
        "name": "startup · QQ 官方 bot（_qq_official_worker）",
        "line": 126,
        "note": "闸门 qq.enabled + appid + token；mock 下 app.adapters.qq._TASK 必须仍是 None",
        "gate": "qq.enabled",
        "signal": "qq_task",
    },
]

#: 本模块注册过的 item id（run() 用它挑出自己的 items 返回）
_IDS: list[str] = []

_BOOT_MARK = "__BOOT_JSON__"


# ====================================================================== 工具
def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _is_temp_path(p: pathlib.Path) -> bool:
    """判断路径是否落在系统临时目录下（Windows 大小写不敏感）。"""
    try:
        target = str(p.resolve()).lower()
        tmp = str(pathlib.Path(tempfile.gettempdir()).resolve()).lower()
    except OSError:
        return False
    return target == tmp or target.startswith(tmp + os.sep)


def _dec(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw[:_MAX_TEXT]
    try:
        return raw.decode("utf-8", "replace")[:_MAX_TEXT]
    except Exception:  # noqa: BLE001 - 解码失败不该拖垮探针
        return repr(raw)[:_MAX_TEXT]


def _lines(text: str, *, head: int = 0, tail: int = 0) -> list[str]:
    rows = [r for r in str(text).replace("\r\n", "\n").split("\n")]
    while rows and rows[-1] == "":
        rows.pop()
    if head:
        out = rows[:head]
        while out and out[-1] == "":  # 别把尾部空行当成"第 3 行"
            out.pop()
        return out
    if tail:
        return rows[-tail:]
    return rows


def _mask(text: str) -> str:
    try:
        return sandbox.mask(text)
    except Exception:  # noqa: BLE001 - 遮罩失败也要能出报告
        return str(text)


def _has_traceback(*chunks: str) -> bool:
    return any("Traceback (most recent call last)" in c for c in chunks if c)


# ================================================================== 沙箱根
def _sandbox_root(notes: list[str]) -> pathlib.Path:
    """拿到本次 phase 的临时数据根。绝不返回仓库内的真实 data 目录。"""
    cur = os.environ.get("SYMEM_DATA") or ""
    if cur:
        p = pathlib.Path(cur)
        if _is_temp_path(p):
            notes.append(f"复用驱动给的临时根 {p}")
            return p
        notes.append(f"忽略非临时 SYMEM_DATA={cur}")

    if "app.core.paths" not in sys.modules:
        try:
            p = sandbox.prepare_env("cli")
            notes.append(f"sandbox.prepare_env() -> {p}")
            return p
        except sandbox.SandboxError as exc:
            notes.append(f"prepare_env 不可用（{exc}），改用自建临时根")

    p = pathlib.Path(tempfile.mkdtemp(prefix="smd_adv_cli_"))
    os.environ["SYMEM_DATA"] = str(p)
    os.environ["SYMEM_DB"] = str(p / "life.db")
    notes.append(f"自建临时根 {p}（app.core.paths 已被 import，绝不写仓库 data/）")
    return p


def _child_env(root: pathlib.Path) -> dict[str, str]:
    """给子进程的环境：剥掉所有 SYMEM_/AI_LIFE_ 再钉死临时根与 mock。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SYMEM_", "AI_LIFE_"))}
    env.update(
        {
            "SYMEM_DATA": str(root),
            "SYMEM_DB": str(root / "life.db"),
            "SYMEM_MOCK": "1",
            "SYMEM_QQ_ENABLED": "0",
            "SYMEM_ONEBOT_ENABLED": "0",
            "SYMEM_CONSOLE_TOKEN": "",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            CHILD_ENV: "1",
        }
    )
    return env


# ============================================================== 子进程执行
def _run_proc(argv: list[str], env: dict[str, str], timeout_s: float) -> dict[str, Any]:
    """跑一个 ``sys.executable <argv>`` 子进程，永不外抛。"""
    t0 = time.perf_counter()
    cmd = [sys.executable, *argv]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_repo_root()),
            env=env,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "timed_out": True,
            "exit": None,
            "out": _dec(exc.stdout),
            "err": _dec(exc.stderr),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except OSError as exc:
        return {
            "cmd": cmd,
            "timed_out": False,
            "exit": -1,
            "out": "",
            "err": f"spawn failed: {exc!r}",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    return {
        "cmd": cmd,
        "timed_out": False,
        "exit": int(proc.returncode),
        "out": _dec(proc.stdout),
        "err": _dec(proc.stderr),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }


# ================================================================ 库行数
def _ro_counts(db_path: pathlib.Path) -> dict[str, int]:
    """只读统计每张表/视图的行数。``mode=ro``，绝不写。"""
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                " ORDER BY name"
            )
        ]
        out: dict[str, int] = {}
        for name in names:
            try:
                out[name] = int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.Error:
                out[name] = -1
        return out
    finally:
        con.close()


def _counts(db_path: pathlib.Path) -> tuple[dict[str, int], str]:
    """读临时库行数；ro 打不开（WAL 未 checkpoint）就复制三件套读副本。"""
    if not db_path.exists():
        return {}, "missing"
    try:
        return _ro_counts(db_path), "ro"
    except sqlite3.Error:
        pass
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="smd_adv_cli_copy_"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = pathlib.Path(str(db_path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / (db_path.name + suffix))
        return _ro_counts(tmp / db_path.name), "copy"
    except (OSError, sqlite3.Error):
        return {}, "unreadable"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """只回变化的表，且过滤 sqlite 内部噪声。"""
    out: dict[str, int] = {}
    for key in set(before) | set(after):
        if key.startswith("sqlite_"):
            continue
        d = int(after.get(key, 0)) - int(before.get(key, 0))
        if d:
            out[key] = d
    return dict(sorted(out.items(), key=lambda kv: (-abs(kv[1]), kv[0])))


# ================================================================== register
def register(reg: reg_mod.Registry) -> int:
    """注册 12 个 ``kind=cli`` + 3 个 ``kind=worker``，返回注册条数。"""
    _IDS.clear()
    for case in _CASES:
        reg.add(
            types.Item(
                id=case["id"],
                kind="cli",
                name=case["name"],
                module="app/cli.py",
                line=int(case.get("line") or 0),
                risk=str(case["risk"]),
                plan="L2",
                detail=str(case.get("note") or ""),
                extra={"argv": list(case["argv"]), "expect_exit": case["expect"]},
            )
        )
        _IDS.append(case["id"])
    for worker in _WORKERS:
        reg.add(
            types.Item(
                id=worker["id"],
                kind="worker",
                name=worker["name"],
                module="app/main.py",
                line=int(worker.get("line") or 0),
                risk="net",
                plan="L2",
                detail=str(worker.get("note") or ""),
                extra={"gate": worker["gate"]},
            )
        )
        _IDS.append(worker["id"])
    return len(_IDS)


# ============================================================ CLI 单项执行
def _finding(
    item_id: str, severity: str, title: str, *, detail: str = "", evidence: dict | None = None,
    repro: str = "",
) -> types.Finding:
    # id 必须跨进程稳定（不能用 hash()：PYTHONHASHSEED 每次不同，报告 diff 会全是噪声）
    digest = hashlib.sha1(f"{item_id}|{severity}|{title}".encode("utf-8")).hexdigest()[:8]
    return types.Finding(
        id=f"F:{item_id}:{severity}:{digest}",
        severity=severity,
        area="cli",
        title=_mask(title),
        detail=_mask(detail),
        evidence={k: _mask(str(v)) for k, v in (evidence or {}).items()},
        repro=_mask(repro),
        item_id=item_id,
    )


def _run_cli_case(
    case: dict[str, Any],
    *,
    reg: reg_mod.Registry,
    res: types.PhaseResult,
    root: pathlib.Path,
    env: dict[str, str],
    timeout_s: float,
    allow_heavy: bool,
    strict_tb: bool,
) -> None:
    """跑一条 CLI 用例并把结论写回 registry / PhaseResult。"""
    item_id = case["id"]
    expect = case["expect"]
    repro = " ".join([".venv\\Scripts\\python.exe", *case["argv"]])

    # ---- 永不执行的项 ----------------------------------------------------
    if item_id == "CLI:serve":
        reg.mark(
            item_id,
            types.SKIP,
            "会 bind 8787 并阻塞在 uvicorn.run；本 phase 禁止真起服务。"
            "启动链路已由 CLI:init / CLI:help / WORKER:* 覆盖。",
            skipped_reason="would block on uvicorn.run",
        )
        return
    if case.get("heavy") and not allow_heavy:
        reg.mark(
            item_id,
            types.SKIP,
            f"重活用例（40MB/96 卷，十万级写入）默认跳过；设 {HEAVY_ENV}=1 才跑。",
            skipped_reason="heavy",
            enable_with=f"{HEAVY_ENV}=1",
        )
        return

    # ---- 语料/样例不存在：SKIP，不算缺陷 --------------------------------
    argv = list(case["argv"])
    for token in argv:
        if not token or token.startswith("-") or token == "app.cli" or token.startswith("app."):
            continue
        if "/" in token or "\\" in token or token.endswith(".txt"):
            target = _repo_root() / token
            if not target.exists() and not case.get("negative"):
                reg.mark(
                    item_id,
                    types.SKIP,
                    f"测试输入不存在：{target}",
                    skipped_reason="missing input",
                )
                return

    # ---- 真正跑 ----------------------------------------------------------
    db_path = pathlib.Path(env.get("SYMEM_DB") or (root / "life.db"))
    before, before_mode = _counts(db_path)
    budget = float(case.get("timeout_s") or timeout_s)
    budget = max(10.0, min(budget, float(timeout_s) if timeout_s else budget))
    run = _run_proc(argv, env, budget)
    after, after_mode = _counts(db_path)
    delta = _delta(before, after)

    out_head = _lines(run["out"], head=_HEAD_LINES)
    err_tail = _lines(run["err"], tail=_TAIL_LINES)
    tb = _has_traceback(run["out"], run["err"])

    extra: dict[str, Any] = {
        "cmd": run["cmd"],
        "exit_code": run["exit"],
        "expect_exit": expect,
        "elapsed_ms": run["elapsed_ms"],
        "timeout_s": budget,
        "stdout_head": [_mask(x) for x in out_head],
        "stderr_tail": [_mask(x) for x in err_tail],
        "traceback": tb,
        "db_delta": delta,
        "db_read_mode": f"{before_mode}->{after_mode}",
        "sandbox_root": str(root),
    }

    if run["timed_out"]:
        reg.mark(
            item_id,
            types.HANG,
            f"超过 {budget:.0f}s 未退出（已 kill）",
            **extra,
        )
        res.findings.append(
            _finding(
                item_id,
                types.P1,
                f"{item_id} 超时（>{budget:.0f}s）",
                detail="子进程未在预算内退出，已被 kill；可能卡在输入、锁或网络。",
                evidence={"stderr_tail": "\n".join(err_tail[-10:])},
                repro=repro,
            )
        )
        return

    if run["exit"] != expect:
        # `reset` 会在"数据库正被占用"时按设计返回 1（友好提示，不是 traceback）。
        # 探针父进程为了算 db_delta 会开过这个库，句柄可能还没释放 → 子进程删不掉文件。
        # 这属于夹具限制：产品行为由 scripts/adv_regress.py 的 D6（独立子进程根）覆盖。
        if item_id == "CLI:reset" and any("正被占用" in str(x) for x in err_tail):
            reg.mark(
                item_id,
                types.SKIP,
                "父进程持有 sqlite 句柄导致子进程删不掉库（夹具限制）；"
                "产品行为已由 scripts/adv_regress.py: D6 独立进程验证",
                **extra,
            )
            return
        reg.mark(
            item_id,
            types.RAISED,
            f"退出码 {run['exit']} != 期望 {expect}",
            **extra,
        )
        res.findings.append(
            _finding(
                item_id,
                types.P0,
                f"{item_id} 退出码 {run['exit']} != 期望 {expect}",
                detail="\n".join(err_tail[-_TAIL_LINES:]),
                evidence={
                    "exit": run["exit"],
                    "expect": expect,
                    "stdout_head": " | ".join(out_head),
                    "stderr_tail": "\n".join(err_tail),
                },
                repro=repro,
            )
        )
        return

    # 退出码符合预期；再看 traceback 的性质
    negative = bool(case.get("negative"))
    if tb and not negative:
        reg.mark(item_id, types.RAISED, "退出码对但输出含 Traceback", **extra)
        res.findings.append(
            _finding(
                item_id,
                types.P0,
                f"{item_id} 正向用例打出 Traceback",
                detail="\n".join(err_tail[-_TAIL_LINES:]),
                evidence={"stderr_tail": "\n".join(err_tail)},
                repro=repro,
            )
        )
        return
    if tb and negative:
        severity = types.P0 if strict_tb else types.P2
        reg.mark(
            item_id,
            types.RAISED if strict_tb else types.OK,
            "退出码符合预期（负向用例），但失败路径是以未捕获异常退出的",
            **extra,
        )
        res.findings.append(
            _finding(
                item_id,
                severity,
                f"{item_id} 负向用例靠未捕获异常退出（CLI 缺友好提示）",
                detail="\n".join(err_tail[-6:]),
                evidence={"stderr_tail": "\n".join(err_tail)},
                repro=repro,
            )
        )
        return

    # ---- 正常通过：再做 help 的 prog 名 / 子命令完整性校验 ---------------
    detail = f"exit={run['exit']} {run['elapsed_ms']}ms"
    if item_id == "CLI:help":
        probe = _check_help(run["out"])
        extra.update(probe["extra"])
        if probe["missing"]:
            detail += f"；缺失子命令 {probe['missing']}"
            reg.mark(item_id, types.OK, detail, **extra)
            res.findings.append(
                _finding(
                    item_id,
                    types.P1,
                    f"--help 缺少子命令：{', '.join(probe['missing'])}",
                    detail="argparse 注册表与 cli.py 的实际子命令不一致（改名/漏注册）。",
                    evidence={"stdout_head": " | ".join(out_head)},
                    repro=repro,
                )
            )
            return
    reg.mark(item_id, types.OK, detail, **extra)


def _check_help(stdout: str) -> dict[str, Any]:
    """校验 --help 的 prog 名与 10 个子命令。"""
    expected = [
        "serve", "ingest", "init", "reset", "anchors",
        "cold", "compress", "letter", "reference", "arc",
    ]
    missing = [name for name in expected if name not in stdout]
    prog_ok = "衡忆" in stdout
    return {
        "missing": missing,
        "extra": {
            "prog_name_ok": prog_ok,
            "subcommands_expected": expected,
            "subcommands_missing": missing,
            "usage_first_line": _mask(_lines(stdout, head=1)[0]) if stdout.strip() else "",
        },
    }


# ============================================================ 后台任务探针
def _boot_payload(root: pathlib.Path, timeout_s: float) -> dict[str, Any]:
    """起子进程跑 `--boot-json`，解析它吐的观测结果。"""
    env = _child_env(root)
    run = _run_proc(["-m", "app.qa.cli_probe", "--boot-json"], env, min(180.0, max(30.0, timeout_s)))
    payload: dict[str, Any] = {
        "ok": False,
        "timed_out": run["timed_out"],
        "exit": run["exit"],
        "elapsed_ms": run["elapsed_ms"],
        "stderr_tail": _lines(run["err"], tail=_TAIL_LINES),
        "raw": "",
    }
    for line in _lines(run["out"]):
        if line.startswith(_BOOT_MARK):
            try:
                payload.update(json.loads(line[len(_BOOT_MARK):]))
                payload["ok"] = bool(payload.get("ok"))
            except (ValueError, TypeError) as exc:
                payload["parse_error"] = str(exc)
            break
    if not payload["ok"]:
        payload["raw"] = _mask("\n".join(_lines(run["out"], tail=10)))
    return payload


def _judge_workers(
    *,
    reg: reg_mod.Registry,
    res: types.PhaseResult,
    payload: dict[str, Any],
    timeout_s: float,
) -> None:
    """把 boot payload 翻译成 3 条 worker 结论 + Finding。"""
    repro = ".venv\\Scripts\\python.exe -m app.qa.cli_probe --boot-json"

    if not payload.get("ok"):
        reason = (
            f"boot 探针未产出结果（timeout={payload.get('timed_out')} "
            f"exit={payload.get('exit')}）：无法证明后台任务未启动"
        )
        for worker in _WORKERS:
            reg.mark(worker["id"], types.RAISED, reason, boot=payload)
        res.findings.append(
            _finding(
                "WORKER:boot",
                types.P0,
                "后台任务自检子进程失败，无法证伪「mock 下不启动」",
                detail="\n".join(list(payload.get("stderr_tail") or [])[-20:]) or str(payload.get("raw") or ""),
                evidence={
                    "exit": payload.get("exit"),
                    "timed_out": payload.get("timed_out"),
                    "stderr_tail": "\n".join(list(payload.get("stderr_tail") or [])),
                },
                repro=repro,
            )
        )
        return

    is_mock = bool(payload.get("is_mock"))
    calls = dict(payload.get("calls") or {})
    tasks_created = int(payload.get("tasks_created") or 0)
    task_names = list(payload.get("task_names") or [])
    blocked = list(payload.get("network_blocked") or [])
    qq_task = bool(payload.get("qq_task"))
    gates = dict(payload.get("gates") or {})
    http_status = payload.get("http_status")

    shared = {
        "is_mock": is_mock,
        "gates": gates,
        "tasks_created": tasks_created,
        "task_names": task_names[:5],
        "network_blocked": blocked[:5],
        "http_status": http_status,
        "boot_elapsed_ms": payload.get("elapsed_ms"),
    }

    # ---- 沙箱本身不可信：直接全判 RAISED --------------------------------
    if not is_mock:
        for worker in _WORKERS:
            reg.mark(worker["id"], types.RAISED, "provider 不是 mock，沙箱已击穿，结论不可信", **shared)
        res.findings.append(
            _finding(
                "WORKER:boot",
                types.P0,
                "后台任务自检未处于 mock 模式（沙箱击穿）",
                detail=f"provider.is_mock={is_mock}；此时可能有真实外发/联网。",
                evidence=shared,
                repro=repro,
            )
        )
        return

    verdicts = {
        "proactive": (
            "proactive",
            int(calls.get("proactive") or 0) == 0
            and not any("proactive" in str(n) for n in task_names),
        ),
        "harvest": ("harvest", int(calls.get("harvest") or 0) == 0),
        "qq_task": ("qq_task", not qq_task),
    }

    for worker in _WORKERS:
        key, ok = verdicts[worker["signal"]]
        item_extra = dict(shared)
        item_extra.update(
            {
                "signal": key,
                "proactive_prepare_calls": int(calls.get("proactive") or 0),
                "harvest_tick_calls": int(calls.get("harvest") or 0),
                "qq_task": qq_task,
                "gate_snapshot": gates,
            }
        )
        if ok:
            reg.mark(
                worker["id"],
                types.OK,
                f"mock 下未启动（gate={worker['gate']}；tasks_created={tasks_created}）",
                **item_extra,
            )
            continue

        reg.mark(
            worker["id"],
            types.RAISED,
            f"契约违反：mock 下仍启动了 {worker['name']}",
            **item_extra,
        )
        res.findings.append(
            _finding(
                worker["id"],
                types.P1,
                f"{worker['id']} 在 mock 下启动了",
                detail=(
                    f"gate=``{worker['gate']}``；观测：proactive={calls.get('proactive')} "
                    f"harvest={calls.get('harvest')} qq_task={qq_task} tasks_created={tasks_created}"
                ),
                evidence=item_extra,
                repro=repro,
            )
        )

    # ---- 全局：不该有新任务 / 不该有外网 --------------------------------
    if tasks_created:
        res.findings.append(
            _finding(
                "WORKER:tasks",
                types.P1,
                f"startup 期间创建了 {tasks_created} 个 asyncio 任务（mock 下应为 0）",
                detail="；".join(str(n) for n in task_names[:5]),
                evidence={"task_names": [str(n) for n in task_names[:10]], "gates": gates},
                repro=repro,
            )
        )
    if blocked:
        res.findings.append(
            _finding(
                "WORKER:network",
                types.P0,
                f"startup 期间尝试联网 {len(blocked)} 次（已被 sandbox 拦下）",
                detail="；".join(str(b) for b in blocked[:5]),
                evidence={"blocked": [str(b) for b in blocked[:10]]},
                repro=repro,
            )
        )
    if http_status != 200:
        res.findings.append(
            _finding(
                "WORKER:http",
                types.P1,
                f"startup 后 /v1/health 返回 {http_status}",
                detail="lifespan 跑起来但应用不可用。",
                evidence=shared,
                repro=repro,
            )
        )


# ====================================================================== run
def run(reg: reg_mod.Registry, *, timeout_s: float = 120) -> types.PhaseResult:
    """跑完整个 cli phase：12 条 CLI 子命令 + 3 条后台任务契约。"""
    res = types.PhaseResult(phase=PHASE)
    notes: list[str] = []

    if not _IDS:
        register(reg)

    root = _sandbox_root(notes)
    if not _is_temp_path(root):  # 理论不可达，兜底
        raise sandbox.SandboxError(f"拒绝在非临时数据根上跑 CLI 探针：{root}")
    env = _child_env(root)
    allow_heavy = str(os.environ.get(HEAVY_ENV, "")).strip().lower() in ("1", "true", "yes", "on")
    strict_tb = str(os.environ.get(STRICT_TB_ENV, "")).strip().lower() in ("1", "true", "yes", "on")
    notes.append(f"heavy={allow_heavy} strict_traceback={strict_tb} timeout_s={timeout_s}")

    ok = skipped = raised = hang = 0
    for case in _CASES:
        # 每条子命令一个独立子根：`reset` 会删库，若共用同一个根，
        # 后面 ingest/init 会拿到"刚被 reset 掉"的库，互相污染（dirty 顺序依赖）
        case_root = root / "cli" / reg_mod.slug(str(case["id"]).replace("CLI:", ""), maxlen=24)
        case_root.mkdir(parents=True, exist_ok=True)
        _run_cli_case(
            case,
            reg=reg,
            res=res,
            root=case_root,
            env=_child_env(case_root),
            timeout_s=timeout_s,
            allow_heavy=allow_heavy,
            strict_tb=strict_tb,
        )
        item = reg.items.get(case["id"])
        if item is None:
            continue
        if item.status == types.OK:
            ok += 1
        elif item.status == types.SKIP:
            skipped += 1
        elif item.status == types.HANG:
            hang += 1
        elif item.status == types.RAISED:
            raised += 1

    payload = _boot_payload(root, timeout_s)
    notes.append(
        "boot: ok={} mock={} tasks={} blocked={} qq_task={}".format(
            payload.get("ok"),
            payload.get("is_mock"),
            payload.get("tasks_created"),
            len(payload.get("network_blocked") or []),
            payload.get("qq_task"),
        )
    )
    _judge_workers(reg=reg, res=res, payload=payload, timeout_s=timeout_s)

    items = [reg.items[i] for i in _IDS if i in reg.items]
    res.items = items
    worker_ok = sum(1 for i in items if i.kind == "worker" and i.status == types.OK)
    notes.insert(
        0,
        f"cli: ok={ok} skip={skipped} raised={raised} hang={hang}；"
        f"worker: ok={worker_ok}/{len(_WORKERS)}；root={root}",
    )
    res.notes = " | ".join(notes)
    return res.finish()


# ================================================== 子进程模式：boot 观测
def _boot_json() -> int:
    """``--boot-json``：在**干净子进程**里观测三个 startup 是否启动。"""
    # ① 必须先钉死数据根，再 import 任何 app.*
    root = sandbox.prepare_env("cli_boot")
    out: dict[str, Any] = {"ok": False, "sandbox_root": str(root)}

    with sandbox.network_guard(allow_loopback=True) as blocked:
        import asyncio

        import httpx

        import app.main as main_mod  # noqa: E402 - 必须在 prepare_env 之后
        from app.adapters import qq as qq_mod  # noqa: E402
        from app.life import proactive as pro_mod  # noqa: E402
        from app.web import harvest as harvest_mod  # noqa: E402

        calls = {"harvest": 0, "proactive": 0}

        async def _rec_harvest(rt):  # noqa: ANN001, ARG001
            calls["harvest"] += 1

        async def _rec_prepare(rt, persona, user_id, chat_type):  # noqa: ANN001, ARG001
            calls["proactive"] += 1
            return {"ok": False}

        harvest_mod.tick = _rec_harvest
        pro_mod.prepare_tick = _rec_prepare

        cfg = main_mod.runtime.get().cfg
        app = main_mod.create_app()
        out["is_mock"] = bool(cfg.provider.is_mock)
        out["gates"] = {
            "provider.is_mock": bool(cfg.provider.is_mock),
            "onebot.enabled": bool(getattr(cfg.onebot, "enabled", False)),
            "onebot.proactive_push": bool(getattr(cfg.onebot, "proactive_push", False)),
            "trend.harvest_enabled": bool(getattr(getattr(cfg, "trend", None), "harvest_enabled", False)),
            "qq.enabled": bool(getattr(cfg.qq, "enabled", False)),
            "qq.appid": bool(str(getattr(cfg.qq, "appid", "") or "")),
            "qq.token": bool(str(getattr(cfg.qq, "token", "") or "")),
        }

        async def _boot() -> dict[str, Any]:
            before = asyncio.all_tasks()
            info: dict[str, Any] = {}
            async with app.router.lifespan_context(app):
                # 让 create_task 出来的协程真的跑一步，否则观测不到
                await asyncio.sleep(0.25)
                new = [t for t in asyncio.all_tasks() - before if not t.done()]
                info["tasks_created"] = len(new)
                info["task_names"] = [repr(t.get_coro())[:120] for t in new]
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://127.0.0.1"
                ) as client:
                    resp = await client.get("/v1/health")
                    info["http_status"] = resp.status_code
                    try:
                        info["health_provider"] = resp.json().get("provider")
                    except ValueError:
                        info["health_provider"] = None
            return info

        try:
            info = asyncio.run(_boot())
            out.update(info)
            out["calls"] = dict(calls)
            out["qq_task"] = qq_mod._TASK is not None  # noqa: SLF001 - 故意读模块私有态
            out["ok"] = True
        except Exception as exc:  # noqa: BLE001 - 子进程必须把失败也报回去
            out["error"] = f"{type(exc).__name__}: {exc}"
            import traceback

            out["traceback_tail"] = traceback.format_exc().splitlines()[-12:]
        finally:
            out["network_blocked"] = [str(b) for b in blocked]

    line = _BOOT_MARK + json.dumps(out, ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


# ================================================================== __main__
def _self_report(res: types.PhaseResult, *, want_json: pathlib.Path | None) -> str:
    rows = ["id | status | exit | ms | tb | stdout_head"]
    for item in res.items:
        if item.kind not in ("cli", "worker"):
            continue
        ex = item.extra or {}
        head = " / ".join(ex.get("stdout_head") or [])[:90]
        rows.append(
            "{id} | {status} | {code} | {ms}ms | {tb} | {head}".format(
                id=item.id,
                status=f"{item.status}",
                code=ex.get("exit_code", "-"),
                ms=ex.get("elapsed_ms", "-"),
                tb="TB" if ex.get("traceback") else "-",
                head=head,
            )
        )
    rows.append("")
    rows.append(f"counts={res.counts()} secs={res.secs}s")
    rows.append(f"notes={res.notes}")
    if res.findings:
        rows.append("findings:")
        for f in sorted(res.findings, key=types.sev_key):
            rows.append(f"  [{f.severity}] {f.title}")
    if want_json:
        want_json.parent.mkdir(parents=True, exist_ok=True)
        want_json.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(f"json={want_json}")
    return "\n".join(rows)


def _main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    import argparse

    ap = argparse.ArgumentParser("app.qa.cli_probe", description="CLI 全子命令 + 后台任务探针")
    ap.add_argument("--boot-json", action="store_true", help="子进程模式：只跑 startup 观测并吐 JSON")
    ap.add_argument("--self", action="store_true", help="自测：注册并跑完本 phase")
    ap.add_argument("--only", default="", help="只跑这些 item id（逗号分隔，仅 --self）")
    ap.add_argument("--timeout", type=float, default=120.0, help="总预算秒（默认 120）")
    ap.add_argument("--json", default="", help="PhaseResult 落盘路径（仅 --self）")
    args = ap.parse_args(argv)

    if args.boot_json:
        return _boot_json()
    if not args.self:
        ap.print_help()
        return 0

    # --only：把不关心的用例就地跳过，避免重活/慢活拉长自测
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        keep = [c for c in _CASES if c["id"] in only]
        _CASES[:] = keep

    reg = reg_mod.Registry()
    register(reg)
    res = run(reg, timeout_s=args.timeout)
    out_json = pathlib.Path(args.json) if args.json else None
    print(_self_report(res, want_json=out_json))
    return 0 if not reg.unresolved() else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
