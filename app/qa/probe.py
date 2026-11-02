"""逐函数动态探针（probe）：把仓库里**每一个可调用对象**真调一次。

三层执行计划（`Item.plan` 决定它落在哪一层）：

| 层 | 做什么 | 覆盖对象 | 失败判定 |
|---|---|---|---|
| **L0** 静态反射 | `import_module` → `getattr` 逐级取到对象 → `inspect.signature` → async 标记与 AST 比对 → docstring 记录 | 只认 `FN:` / `METH:` 里可执行的项 | **只有两种算缺陷**：模块 import 失败、模块级可解析对象 getattr 失败。取不到签名 / async 标记不一致 → `SKIP`（留痕不染红） |
| **L1** 进程内调用 | 按签名合成参数真调一次；async 走 `wait_for(20s)`，sync 走 `to_thread` + `wait_for(20s)` | `risk in (read, write, llm)` | 函数体内抛异常 → `RAISED`；参数合不出来/在调用边界被 Python 拒掉 → `SKIP` |
| **L2** 子进程 | `sys.executable -m app.qa.probe --module X --batch`，60s 硬超时，日志落 `logs/probe-subprocess/` | `risk in (net, hang, danger)` | 退非 0 / 超时 → `HANG`（超时）或 `RAISED`（崩溃） |

**本 phase 不负责的项（一律 `SKIP`，不出 Finding，不污染覆盖率分母）**：

| 项 | 原因 |
|---|---|
| `CLS:` 类对象 | 交给 `static_probe`（getattr + isinstance 已验），避免同一个类报两次 |
| `qualname` 含 `<locals>` | 定义在函数体内，模块级 `getattr` 永远取不到 |
| `@property` / `@x.setter` / `cached_property` | descriptor，`callable()` 为 False |
| `extra["private"] == True` | inventory 给的是 `plan=L0`，本 phase 不跑（`include_private=True` 可放开） |
| 其它 `plan` 不在 L1/L2 的项 | 归静态阶段 / 会话阶段 / 路由阶段 |

**SKIP 与 RAISED 的判据（契约要求必须写清，代码里落在 `_raise_origin()` 与 `_SKIP_*` 常量上）**：

1. 值合成不出来（未知类型 / 无法实例化）→ `SKIP`，reason 前缀 `synthesize:`。
2. `sig.bind()` 都过不去（缺必填参数、多参数）→ `SKIP`，reason 前缀 `bind:`。
3. 异常 traceback 的**最深帧仍在本文件**（说明参数没进函数体，被 Python 在调用边界拒了）→ `SKIP`，reason 前缀 `calltype:`；`extra["param_mismatch"]=True`。
4. 异常最深帧落在被测模块里（函数体真的跑了）→ `RAISED`，带 `traceback 首帧` + 参数 repr（过 `sandbox.mask()`）。
5. 同模块前一项 `HANG`（sync 调用卡死会遗留线程）→ 后续项 `SKIP`，reason 前缀 `poisoned:`，交 L2。

**为什么默认不 `reset_db()`**：`runtime` / `memengine` 单例持有连接与缓存，反复重建会制造一堆假异常（用 `db.reset()` 也拦不住 `relation.engine` 等模块级缓存）。临时库里的脏数据对"这个函数到底能不能调通"这个目标无影响，所以默认不 reset；需要干净库时传 `reset_db=True`。

**用法**：
```python
from app.qa import sandbox
sandbox.prepare_env("probe")          # 必须在 import app.core.* 之前
from app.qa import probe, registry
reg = registry.Registry()
print(probe.register(reg))            # 无 targets 时自动 ast 扫 app/**
res = asyncio.run(probe.run(reg, levels=("L0", "L1")))
print(res.counts(), res.secs)
```

命令行：
```
.venv\\Scripts\\python.exe -m app.qa.probe                     # L0+L1 全量（自建临时根）
.venv\\Scripts\\python.exe -m app.qa.probe --levels L0         # 只做静态反射
.venv\\Scripts\\python.exe -m app.qa.probe --module app.memory.h2 --levels L0,L1
.venv\\Scripts\\python.exe -m app.qa.probe --module app.memory.h2 --batch   # 子进程入口
```

纪律：不改 `app/` 既有文件；不联网（`sandbox.network_guard()` 拦 socket/httpx）；不写真库
（`sandbox.prepare_env()` 把 `SYMEM_DATA` 钉到临时目录，且 `app/core/paths.py` 还没被 import）。
﻿﻿‌﻿​‌‍‍​‌﻿‍‌‌‌‌​﻿﻿﻿​﻿‍‍‍﻿​‌﻿‌‍‌​﻿"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import importlib
import inspect
import io
import json
import os
import pathlib
import re
import sys
import time
import traceback
from typing import Any

from app.qa import registry, sandbox, types

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 单次调用超时（秒）
CALL_TIMEOUT = 20.0
#: 子进程硬超时（秒）
SUBPROCESS_TIMEOUT = 60.0
#: `--batch` 子进程内部的单次调用超时：比外层短，撞到 hang 还能继续跑后面几项
BATCH_CALL_TIMEOUT = 8.0

# ---------------------------------------------------------------- 风险常量 --
RISK_READ = "read"
RISK_WRITE = "write"
RISK_NET = "net"
RISK_LLM = "llm"
RISK_HANG = "hang"
RISK_DANGER = "danger"

# ---------------------------------------------------------------- SKIP 码 --
#: reason 前缀就是判据，报告里按前缀聚合即可区分"缺参"和"真缺陷"
SKIP_SYNTH = "synthesize:"
SKIP_BIND = "bind:"
SKIP_CALLTYPE = "calltype:"
SKIP_POISON = "poisoned:"
SKIP_ABSTRACT = "abstract:"
#: 入口函数（CLI/脚本）被调起来就 `sys.exit()`：那是它的设计，判 SKIP 不判缺陷
SKIP_SYSEXIT = "sysexit:"
#: 参数默认值是 FastAPI 依赖注入对象（Query/Path/Body/Depends…）：直调会拿到描述符而不是值
SKIP_ROUTE = "route:"
#: 直调 route handler 抛 HTTPException（404/403…）：语义上要经 ASGI，归 routes phase
SKIP_HTTP = "http:"

#: 校验函数按设计拒绝非法入参（"日型只能是 workday/…"）：算"该抛且抛了"→ EXPECTED，不记缺陷
_VALIDATION_HINTS = (
    "只能是", "必须是", "不支持", "不合法", "取值范围", "不在允许",
    "invalid", "not supported", "out of range", "must be",
)
#: 同一类"校验拒绝"，但措辞是"未知报告类型 / 不是人设档案文件"这种
_VALIDATION_RE = re.compile(r"未知.{0,4}(类型|种类|模式|报告|格式|字段)|不是.{0,6}(文件|档案|人设)")
#: 业务前置条件（"好感还没到"）：临时库凑不出那个状态，判 SKIP 不判缺陷
SKIP_PRECOND = "precond:"
#: 定义在函数体内（qualname 含 `<locals>`）：模块级不可达，L0 直接 SKIP
SKIP_NESTED = "nested:"
#: `@property` / setter / cached_property：描述符不是可调用对象
SKIP_PROPERTY = "property:"
#: 私有实现（`_x`）：inventory 给的是 plan=L0，本 phase 不跑
SKIP_PRIVATE = "private:"
#: 类对象：交给 static 阶段（getattr + isinstance 已验），避免同一个类被报两次
SKIP_STATIC = "static:"
#: L0 取不到签名（C 扩展/builtin）：按 Lead 口径算 SKIP，不算缺陷
SKIP_SIG = "sig:"
#: inspect 与 AST 的 async 标记不一致：留痕但不算 L0 缺陷
SKIP_ASYNC = "async:"
#: 计划层级不归本 phase（plan=L0/L3/L4）
SKIP_TIER = "tier:"
#: 外网被沙箱拦（预期，不是缺陷）：bili / harvest / 真实 LLM 都归这里
SKIP_NET = "net:"

#: 按设计常驻循环 / 只在主线程跑的函数：进程内调用没有意义
_BY_DESIGN_LOOPS = {"loop", "run_forever", "main_loop", "watch", "serve_forever"}

#: 探针环境问题（不是产品缺陷）：线程 / 事件循环 / 运行期未初始化 / 前一项把 store 关了
#: 中文那批是"业务前置条件"（还没到好感阈值、未绑定等），同样不是缺陷。
_PRECOND_HINTS = (
    "还没到", "还没有", "需要先", "前置条件", "不足", "先绑定", "未绑定",
    "没有可", "未开启", "没有配置",
    "no current event loop",
    "must be run on a main thread",
    "store is closed",
    "db.init() 未被调用",
    "no such table: memories",
    "event loop is closed",
    "attached to a different loop",
    "cannot be called from a running event loop",
)


def _is_precondition(msg: str) -> bool:
    low = str(msg or "").lower()
    return any(h.lower() in low for h in _PRECOND_HINTS)

#: FastAPI 依赖注入默认值类型名（`fastapi.params.*` 的实例）
_FASTAPI_DEFAULTS = frozenset({
    "Query", "Path", "Body", "Header", "Cookie", "Form", "File",
    "Depends", "Security", "Request", "WebSocket",
})

#: 探针自身不参与动态调用（会自递归）
_SELF_MODULE = "app.qa.probe"
#: 测试底座自身的模块只做 L0：调 `registry.save()` 之类会写盘/递归
_QA_PREFIX = "app.qa."

# ------------------------------------------------------------ 风险启发式 --
#: 顺序即优先级：越靠前越危险，命中即停（danger > hang > net > write > llm）。
_RISK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (RISK_DANGER, re.compile(
        r"shutil\.rmtree|os\.remove|\.unlink\(|os\.system|subprocess|Popen|"
        r"pyinstaller|--apply|\bexec\(|\beval\(|os\.rmdir|send2trash",
    )),
    (RISK_HANG, re.compile(
        r"while\s+True|time\.sleep\(|await\s+asyncio\.sleep\(\s*[1-9]|"
        r"\.wait\(\)|\.join\(\)|input\(",
    )),
    (RISK_NET, re.compile(
        r"httpx|requests\.|urllib|urlopen|socket\.|bilibili|api\.bili|"
        r"search_video|harvest|websocket|ws_connect|botpy",
    )),
    (RISK_WRITE, re.compile(
        r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
        r"h2\.add|store\.add|kv_set|\.commit\(|write_text|write_bytes|"
        r"forget|terminate|compact|restore|ingest|push_|\.send\(|send_message|"
        r"\bsave\b|\breset\b|\bclear\b|delete_|_save\(",
    )),
    (RISK_LLM, re.compile(
        r"\.llm\.|llm\.chat|judge\(|models\.pick|appraise|embedding",
    )),
)

_FUNC_KINDS = ("fn", "method")


# ============================================================== AST 扫描 ==
def _module_of(path: pathlib.Path, root: pathlib.Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _risk_of(src_lines: str, *, name: str = "") -> str:
    """按源码文本猜风险档。函数名也算证据（`delete_x` 比函数体更直白）。"""
    blob = f"{name}\n{src_lines}" if name else src_lines
    for risk, rx in _RISK_RULES:
        if rx.search(blob):
            return risk
    return RISK_READ


def _walk_defs(body: list[ast.stmt], prefix: tuple[str, ...] = ()):
    """递归产出 (node, qualname, in_class, is_class)。类嵌套也支持。"""
    for node in body:
        if isinstance(node, ast.ClassDef):
            qual = ".".join(prefix + (node.name,))
            yield node, qual, False, True
            yield from _walk_defs(node.body, prefix + (node.name,))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = ".".join(prefix + (node.name,))
            yield node, qual, bool(prefix), False


def _is_descriptor(node: ast.AST) -> bool:
    """`@property` / `@x.setter` / `@cached_property` 不是可调用对象（`callable()` 为 False）。

    它们被排除在探针清单外：`getattr(cls, name)` 拿到的是 descriptor，既没有签名也不能调。
    """
    for dec in getattr(node, "decorator_list", []) or []:
        text = ast.unparse(dec)
        if text in ("property", "functools.cached_property", "cached_property"):
            return True
        if text.endswith((".setter", ".deleter", ".getter")):
            return True
    return False


def _seg(src_lines: list[str], node: ast.AST) -> str:
    start = max(int(getattr(node, "lineno", 1)) - 1, 0)
    end = int(getattr(node, "end_lineno", start + 1) or start + 1)
    return "\n".join(src_lines[start:end])


def _ast_sig(node: ast.AST) -> str:
    """把 AST 里的参数表渲染成可读签名（只用于记录/与 inspect 对照）。"""
    try:
        args = node.args  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - ClassDef
        return "()"
    parts: list[str] = []
    all_pos = list(getattr(args, "posonlyargs", [])) + list(args.args)
    defaults = [None] * (len(all_pos) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(all_pos, defaults):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            piece += f" = {ast.unparse(default)[:24]}"
        parts.append(piece)
    if args.vararg is not None:
        parts.append("*" + args.vararg.arg)
    if args.kwonlyargs:
        if args.vararg is None:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            piece = arg.arg
            if arg.annotation is not None:
                piece += f": {ast.unparse(arg.annotation)}"
            if default is not None:
                piece += f" = {ast.unparse(default)[:24]}"
            parts.append(piece)
    if args.kwarg is not None:
        parts.append("**" + args.kwarg.arg)
    ret = ""
    if getattr(node, "returns", None) is not None:
        ret = f" -> {ast.unparse(node.returns)}"
    return "(" + ", ".join(parts) + ")" + ret


def scan(*, root: pathlib.Path | None = None, modules: list[str] | None = None) -> list[dict]:
    """ast 扫 `app/**/*.py`（**不 import**），产出 probe targets。

    返回的 dict 与 `app/qa/CONTRACT.md` §2 中 `inventory.probe_targets()` 同构：
    `{"module", "qualname", "is_async", "sig", "risk", "line", "kind", "id", "name", "private"}`。
    多给了 `kind/id/name/private`，方便没有 inventory.py 时也能直接 register。
    """
    base = root or ROOT
    app_dir = base / "app"
    want = set(modules or [])
    out: list[dict] = []
    seen: set[str] = set()
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mod = _module_of(path, base)
        if not mod or mod == _SELF_MODULE:
            continue
        if want and mod not in want:
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        lines = src.splitlines()
        for node, qual, in_class, is_class in _walk_defs(tree.body):
            if not is_class and _is_descriptor(node):
                continue  # property/setter 不是可调用对象
            name = qual.rsplit(".", 1)[-1]
            where = _seg(lines, node)
            if is_class:
                item_id = f"CLS:{mod}:{qual}"
                kind = "module"
                is_async = False
            elif in_class:
                item_id = f"METH:{mod}:{qual}"
                kind = "method"
                is_async = isinstance(node, ast.AsyncFunctionDef)
            else:
                item_id = f"FN:{mod}:{qual}"
                kind = "fn"
                is_async = isinstance(node, ast.AsyncFunctionDef)
            if item_id in seen:
                continue
            seen.add(item_id)
            risk = _risk_of(where, name=name)
            if mod.startswith(_QA_PREFIX) or is_class and risk in (RISK_READ, RISK_LLM):
                # 测试底座自身：只做 L0（防自递归 / 防写盘）
                plan = "L0"
            elif is_class:
                plan = "L2" if risk in (RISK_DANGER, RISK_HANG, RISK_NET) else "L1"
            elif risk in (RISK_DANGER, RISK_HANG, RISK_NET):
                plan = "L2"
            else:
                plan = "L1"
            out.append({
                "id": item_id,
                "kind": kind,
                "name": f"{qual}",
                "module": mod,
                "qualname": qual,
                "line": int(getattr(node, "lineno", 0)),
                "risk": risk,
                "plan": plan,
                "is_async": bool(is_async),
                "sig": _ast_sig(node) if not is_class else "()",
                "private": name.startswith("_"),
                "is_class": bool(is_class),
                "file": str(path.relative_to(base)),
            })
    return out


# ============================================================== 注册 ==
def _target_to_item(t: dict) -> types.Item:
    """把 target dict 变成 Item；兼容 inventory 的瘦格式（只有 module/qualname）。"""
    mod = str(t.get("module") or "")
    qual = str(t.get("qualname") or t.get("name") or "")
    is_class = bool(t.get("is_class"))
    item_id = t.get("id")
    if not item_id:
        if is_class:
            item_id = f"CLS:{mod}:{qual}"
        elif "." in qual:
            item_id = f"METH:{mod}:{qual}"
        else:
            item_id = f"FN:{mod}:{qual}"
    kind = t.get("kind") or ("module" if is_class else ("method" if "." in qual else "fn"))
    risk = t.get("risk") or RISK_READ
    plan = t.get("plan")
    if not plan:
        if mod.startswith(_QA_PREFIX):
            plan = "L0"
        elif is_class:
            plan = "L1" if risk in (RISK_READ, RISK_LLM, RISK_WRITE) else "L2"
        else:
            plan = "L2" if risk in (RISK_DANGER, RISK_HANG, RISK_NET) else "L1"
    extra = {
        "qualname": qual,
        "is_async": bool(t.get("is_async")),
        "sig": str(t.get("sig") or ""),
        "private": bool(t.get("private")),
        "is_class": is_class,
        "file": str(t.get("file") or ""),
        "scan_risk": risk,
        #: inventory 的 decorators（如 ["property"]）必须带进来，否则 property 会被当成可调用对象
        "decorators": list(t.get("decorators") or []),
    }
    return types.Item(
        id=str(item_id),
        kind=str(kind),
        name=str(t.get("name") or qual or item_id),
        module=mod,
        line=int(t.get("line") or 0),
        risk=str(risk),
        plan=str(plan),
        status=types.NOTRUN,
        detail="",
        extra=extra,
    )


def register(reg: registry.Registry, targets: list[dict] | None = None) -> int:
    """把本轮要探的对象全部注册进 Registry（status=NOTRUN）。返回新增条数。

    `targets=None` 时自己 ast 扫 `app/**`（不 import，所以不会触发任何副作用）。
    """
    tg = targets if targets is not None else scan()
    added = 0
    for t in tg:
        before = len(reg.items)
        reg.add(_target_to_item(t))
        if len(reg.items) > before:
            added += 1
    return added


def _is_nested(it: types.Item) -> bool:
    """定义在函数体内的对象（`Class.method.<locals>.inner`）：模块级 getattr 永远取不到。"""
    blob = f"{it.id}|{it.extra.get('qualname') or ''}|{it.name or ''}"
    return "<locals>" in blob


def _is_descriptor_item(it: types.Item) -> bool:
    """`@property` / `@x.setter` / `@cached_property`：descriptor，不是可调用对象。"""
    decos = it.extra.get("decorators") or []
    if isinstance(decos, str):
        decos = [decos]
    for d in decos:
        text = str(d)
        if text in ("property", "cached_property", "functools.cached_property"):
            return True
        if text.endswith((".setter", ".deleter", ".getter")):
            return True
    qual = str(it.extra.get("qualname") or "")
    return f"{it.module}.{qual}" in _PROPERTY_QUALNAMES


#: 兜底名单：源码/decorators 元数据都缺失时，按 qualname 直接认作 property
#: （2026-10-31 实测被误报成 RAISED 的 6 条，先钉死在这里）
_PROPERTY_QUALNAMES: frozenset[str] = frozenset({
    "app.core.config.ProviderCfg.is_mock",
    "app.core.config.ProviderCfg.small",
    "app.core.config.ContextCfg.usable",
    "app.core.config.Config.db_file",
    "app.core.config.Config.persona_file",
    "app.core.config.Config.panel_dir",
})


def _exclusion(it: types.Item, *, include_private: bool) -> tuple[str, str] | None:
    """返回 (skip_code, reason) 表示"本 phase 不跑它"；返回 None 表示要跑。

    **口径（2026-10-31 Lead 定）**：L0 只有两类算缺陷 ——
    `模块 import 失败` 与 `模块级可解析对象 getattr 失败`。
    嵌套对象 / property / 私有实现 / plan=L0 的项一律 SKIP，不许污染覆盖率分母，也不许染红报告。
    """
    if it.id.startswith("CLS:"):
        return SKIP_STATIC, "类由 static 阶段负责（getattr/isinstance 已经验过）"
    if _is_nested(it):
        return SKIP_NESTED, "定义在函数内（qualname 含 <locals>），模块级不可达"
    if _is_descriptor_item(it):
        return SKIP_PROPERTY, "@property/setter/cached_property 描述符，不是可调用对象"
    if it.extra.get("private") and not include_private:
        return SKIP_PRIVATE, "私有实现，按契约 plan=L0（inventory 口径），本 phase 不跑"
    if it.plan not in ("L1", "L2"):
        return SKIP_TIER, f"plan={it.plan or '-'}：不在 L1/L2，静态阶段负责"
    return None


def _all_probe_items(reg: registry.Registry) -> list[types.Item]:
    out: list[types.Item] = []
    for it in reg.items.values():
        if it.id.startswith(("FN:", "METH:", "CLS:")) or it.kind in _FUNC_KINDS:
            out.append(it)
    return sorted(out, key=lambda x: (x.module, x.line, x.id))


#: 测试底座自身不参与"产品函数探测"（否则会把自己设计的异常记成产品缺陷：
#: sandbox.prepare_env 故意在重复调用时抛错、report.write 会撞同名文件）
_HARNESS_MODULES = ("app.qa",)


def _is_harness(it: types.Item) -> bool:
    mod = str(it.module or "")
    return any(mod == m or mod.startswith(m + ".") for m in _HARNESS_MODULES)


def _probe_items(reg: registry.Registry, *, include_private: bool = False) -> list[types.Item]:
    """本 phase **真正要执行**的 item：只认 `FN:` / `METH:`，且排除嵌套 / property / 私有。"""
    out: list[types.Item] = []
    for it in _all_probe_items(reg):
        if _is_harness(it):
            continue
        if _exclusion(it, include_private=include_private) is None:
            out.append(it)
    return out


def skip_excluded(reg: registry.Registry, *, include_private: bool = False) -> dict[str, int]:
    """把"本 phase 不负责"的 item 统一标 SKIP（必须先标，否则它们停在 NOTRUN 污染分母）。"""
    stat: dict[str, int] = {}
    for it in _all_probe_items(reg):
        if _is_harness(it):
            if it.status == types.NOTRUN:
                reg.mark(it.id, types.SKIP, "测试底座自身（app.qa），不参与产品函数探测")
                stat["harness:"] = stat.get("harness:", 0) + 1
            continue
        hit = _exclusion(it, include_private=include_private)
        if hit is None:
            continue
        code, why = hit
        if it.status != types.NOTRUN:
            continue  # 别的 phase 已经处理过（static/CLS）就不覆盖
        reg.mark(it.id, types.SKIP, f"{code} {why}", stage="L0", skip_code=code)
        stat[code] = stat.get(code, 0) + 1
    return stat


# ============================================================== 注入对象 ==
class _Ctx:
    """惰性持有的注入对象：只有真的需要时才初始化 runtime（会建临时库）。"""

    def __init__(self) -> None:
        self._rt: Any = None
        self._tmp: pathlib.Path | None = None

    def tmp_dir(self) -> pathlib.Path:
        if self._tmp is None:
            root = pathlib.Path(os.environ.get("SYMEM_DATA") or ".")
            self._tmp = root / "probe" / "tmp"
            self._tmp.mkdir(parents=True, exist_ok=True)
        return self._tmp

    def rt(self) -> Any:
        if self._rt is None:
            from app.core import runtime as runtime_mod  # noqa: PLC0415 - 契约要求惰性

            self._rt = runtime_mod.get()
        return self._rt

    def persona(self) -> Any:
        return self.rt().persona

    def cfg(self) -> Any:
        return self.rt().cfg

    def conn(self) -> Any:
        """临时库连接：必须 `check_same_thread=False`。

        L1 的 sync 调用跑在 worker 线程里，而 `app.core.db.connect()` 建的是默认线程亲和的连接
        —— 传进去就会炸 "SQLite objects created in a thread can only be used in that same thread"。
        """
        import sqlite3  # noqa: PLC0415

        path = os.environ.get("SYMEM_DB") or ""
        con = sqlite3.connect(path, timeout=15, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
        return con

    def cfg_for(self, module: str) -> Any:
        """按模块挑配置段：onebot / qq 的函数要的是子配置，不是根 Config。"""
        cfg = self.cfg()
        if "onebot" in module:
            return getattr(cfg, "onebot", cfg)
        if module.startswith("app.adapters.qq") or ".qq" in module:
            return getattr(cfg, "qq", cfg)
        return cfg

    def store(self) -> Any:
        from app.core import memengine  # noqa: PLC0415

        return memengine.store()


_CTX = _Ctx()


# ========================================================== 参数合成 ==
def _ann_text(ann: Any) -> str:
    if ann is inspect.Parameter.empty or ann is None:
        return ""
    if isinstance(ann, str):
        return ann.strip()
    try:
        return inspect.formatannotation(ann).strip()
    except Exception:  # noqa: BLE001
        return str(ann).strip()


#: 无注解参数的兜底值：按参数名猜（扫出来的 top 名字是 rt / persona / cfg / text / user_id）
_NAME_VALUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(rt|runtime|self_rt)$"), "rt"),
    (re.compile(r"^persona$"), "persona"),
    (re.compile(r"^(cfg|config)$"), "cfg"),
    (re.compile(r"^(conn|connection)$"), "conn"),
    (re.compile(r"^store$"), "store"),
    (re.compile(r"^(sql|statement|sql_text|query_sql|raw_sql)$"), "sql"),
    (re.compile(r"^(path|p|root|dir|folder|base|file|filepath|filename)$"), "path"),
    (re.compile(r"^(user_id|uid|mid|id|n|seq|count|limit|index|idx|turn|hour|k|v_int)$"), "1"),
    (re.compile(r"^(flag|force|enabled|ok|on|dry|dry_run|commit|verbose)$"), "false"),
    (re.compile(r"^(text|msg|message|content|line|reason|key|value|name|label|nick|q|kw|"
                r"query|title|topic|word|phrase|term|prompt|system|body|src|dst|openid|group_id)$"), "小鲸"),
)


#: 注解是 `str` 但语义特殊的参数：给真值，别拿"小鲸"去当 SQL / 表名（否则报的是无意义的语法错）
_STR_SPECIAL: dict[str, str] = {
    "sql": "SELECT 1 AS x",
    "statement": "SELECT 1 AS x",
    "sql_text": "SELECT 1 AS x",
    "table": "mem_item",
    "table_name": "mem_item",
    "tbl": "mem_item",
    "day_type": "workday",
    "interrupt_policy": "allow",
    "base_url": "http://127.0.0.1:9/v1",
    "api_base": "http://127.0.0.1:9",
}


def _by_name(name: str, module: str = "") -> tuple[bool, Any]:
    for rx, kind in _NAME_VALUES:
        if not rx.search(name):
            continue
        if kind == "rt":
            return True, _CTX.rt()
        if kind == "persona":
            return True, _CTX.persona()
        if kind == "cfg":
            return True, _CTX.cfg_for(module)
        if kind == "conn":
            return True, _CTX.conn()
        if kind == "store":
            return True, _CTX.store()
        if kind == "sql":
            return True, "SELECT 1 AS x"
        if kind == "path":
            return True, _CTX.tmp_dir() / f"{name}.txt"
        if kind == "1":
            return True, 1
        if kind == "false":
            return True, False
        return True, "小鲸"
    return False, None


def _first_enum_member(cls: Any) -> tuple[bool, Any]:
    """枚举类 → 第一个成员。注意别写成 `(True, x) if cond else False, None`（优先级会炸成嵌套元组）。"""
    try:
        members = list(cls)  # Enum / IntEnum
    except TypeError:
        return False, None
    if not members:
        return False, None
    return True, members[0]


def _value_for(ann: Any, name: str, mod_globals: dict[str, Any], module: str = "") -> tuple[bool, Any, str]:
    """按注解造一个值。返回 (ok, value, note)。note 只在失败时有意义。"""
    text = _ann_text(ann)
    if not text:
        ok, val = _by_name(name, module)
        return (True, val, "") if ok else (False, None, f"{name}: 无注解且名字不认识")

    # Optional / Union / `X | None`：一律给 None（契约规定的口径）
    norm = text.replace(" ", "")
    if norm.startswith("Optional[") or "|None" in norm or norm in ("None", "NoneType"):
        return True, None, ""
    if norm.startswith(("Union[", "typing.Union[")):
        return True, None, ""
    # Literal[...]：取第一个字面量
    m = re.match(r"^(?:typing\.)?Literal\[(.*)\]$", norm)
    if m:
        first = m.group(1).split(",")[0].strip().strip("'\"")
        if first in ("True", "False"):
            return True, first == "True", ""
        if re.fullmatch(r"-?\d+", first):
            return True, int(first), ""
        return True, first, ""
    if norm in ("str", "builtins.str"):
        return True, _STR_SPECIAL.get(name, "小鲸"), ""
    if norm in ("int", "builtins.int"):
        return True, 1, ""
    if norm in ("float", "builtins.float"):
        return True, 1.0, ""
    if norm in ("bool", "builtins.bool"):
        return True, False, ""
    if norm in ("bytes", "bytearray"):
        return True, b"", ""
    if norm in ("Any", "typing.Any"):
        return True, None, ""
    if norm.startswith(("dict[", "Dict[", "typing.Dict[", "Mapping[", "MutableMapping[")) or norm in ("dict", "Mapping", "MutableMapping"):
        return True, {}, ""
    if norm.startswith(("list[", "List[", "typing.List[", "Sequence[", "Iterable[")) or norm in ("list", "Sequence", "Iterable"):
        return True, [], ""
    if norm.startswith(("tuple[", "Tuple[", "typing.Tuple[")) or norm in ("tuple", "Tuple"):
        return True, (), ""
    if norm.startswith(("set[", "Set[", "typing.Set[", "frozenset[")) or norm in ("set", "Set", "frozenset"):
        return True, set(), ""
    if norm.startswith(("Callable[", "typing.Callable[")):
        return True, (lambda *a, **k: None), ""
    if norm in ("Path", "pathlib.Path", "PurePath", "pathlib.PurePath", "PosixPath"):
        return True, _CTX.tmp_dir() / "probe.txt", ""
    if norm in ("Persona", "persona.Persona"):
        return True, _CTX.persona(), ""
    if norm in ("Runtime", "runtime.Runtime"):
        return True, _CTX.rt(), ""
    if norm in ("Config", "config.Config"):
        return True, _CTX.cfg(), ""
    if norm in ("sqlite3.Connection", "Connection"):
        return True, _CTX.conn(), ""
    if norm in ("datetime", "datetime.datetime"):
        import datetime as _dt  # noqa: PLC0415

        return True, _dt.datetime.now().astimezone(), ""
    if norm in ("date", "datetime.date"):
        import datetime as _dt  # noqa: PLC0415

        return True, _dt.date.today(), ""
    if norm in ("Namespace", "argparse.Namespace"):
        return False, None, f"{name}: 需要 argparse.Namespace（CLI 上下文，属性炸不出来）"
    if norm in ("Response", "HTMLResponse", "JSONResponse", "starlette.responses.Response", "Request", "fastapi.Request"):
        return False, None, f"{name}: 需要 ASGI Request/Response，合成不安全（归 routes phase）"
    if norm in ("UploadFile", "fastapi.UploadFile"):
        return False, None, f"{name}: 需要上传文件对象（归 routes phase）"

    # 名字里有"|"的联合类型（`int | str`、`datetime | None` 已在上方处理）：取第一个
    if "|" in norm:
        head = norm.split("|")[0].strip()
        if head:
            return _value_for(head, name, mod_globals, module)
    # `list[dict[str, Any]]` 之类带参数泛型：按最外层容器处理
    base = norm.split("[", 1)[0]
    if base in ("dict", "Dict", "list", "List", "tuple", "Tuple", "set", "Set", "Sequence", "Mapping"):
        return _value_for(base, name, mod_globals, module)

    # 尝试在模块命名空间里解析成真类型
    cls = mod_globals.get(base)
    if isinstance(base, str) and "." in base:
        head = base.split(".")[-1]
        cls = mod_globals.get(head, cls)
    if isinstance(cls, type):
        ok, val = _first_enum_member(cls)
        if ok:
            return True, val, ""
        if getattr(cls, "__dataclass_fields__", None):
            try:
                return True, cls(), ""
            except Exception:  # noqa: BLE001 - 构造不出来就走下面的兜底
                pass
        try:
            return True, cls(), ""
        except Exception:  # noqa: BLE001
            return False, None, f"{name}: 未知类型 {base}，且无法无参构造"
    ok, val = _by_name(name, module)
    if ok:
        return True, val, ""
    return False, None, f"{name}: 未知类型 {base}"


def synthesize(fn: Any, mod_globals: dict[str, Any], module: str = "") -> tuple[list[Any], dict[str, Any], list[str]]:
    """按签名合成一次调用的参数。返回 (args, kwargs, problems)。

    problems 非空 = 缺参，调用方必须判 `SKIP`（**不是缺陷**）。
    """
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    problems: list[str] = []
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        return args, kwargs, [f"signature: {exc}"]
    for p in sig.parameters.values():
        if p.name in ("self", "cls"):
            continue
        if p.kind is inspect.Parameter.VAR_POSITIONAL or p.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if p.default is not inspect.Parameter.empty:
            continue  # 有默认值就不传，让函数自己用它
        ok, val, note = _value_for(p.annotation, p.name, mod_globals, module)
        if not ok:
            problems.append(note or f"{p.name}: 合成失败")
            continue
        if p.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(val)
        else:
            kwargs[p.name] = val
    return args, kwargs, problems


# ============================================================ 对象解析 ==
class _Resolved:
    __slots__ = ("module", "mod", "obj", "err", "err_kind")

    def __init__(self) -> None:
        self.module = ""
        self.mod: Any = None
        self.obj: Any = None
        self.err = ""
        self.err_kind = ""


_IMPORT_CACHE: dict[str, tuple[Any, str]] = {}


def resolve(item: types.Item) -> _Resolved:
    """import 模块 + 逐级 getattr。失败时 err_kind 区分 import / attr。"""
    out = _Resolved()
    mod_name = item.module
    out.module = mod_name
    if mod_name in _IMPORT_CACHE:
        mod, err = _IMPORT_CACHE[mod_name]
    else:
        mod, err = None, ""
        try:
            mod = importlib.import_module(mod_name)
        except BaseException as exc:  # noqa: BLE001 - 导入期什么都可能抛
            err = f"{type(exc).__name__}: {exc}"
        _IMPORT_CACHE[mod_name] = (mod, err)
    if mod is None:
        out.err, out.err_kind = err, "import"
        return out
    out.mod = mod
    qual = str(item.extra.get("qualname") or item.name or "")
    obj: Any = mod
    try:
        for part in qual.split("."):
            obj = getattr(obj, part)
    except AttributeError as exc:
        out.err, out.err_kind = f"{type(exc).__name__}: {exc}", "attr"
        return out
    out.obj = obj
    return out


def _mod_globals(r: _Resolved) -> dict[str, Any]:
    mod = r.mod
    return getattr(mod, "__dict__", {}) or {}


# ============================================================== L0 ==
def l0(reg: registry.Registry, items: list[types.Item], *, limit: int | None = None) -> dict[str, int]:
    """L0 静态反射：能导入、能取到对象、有签名、async 标记与 AST 一致。

    **只有两种失败算缺陷（RAISED）**：模块 import 失败、模块级可解析对象 getattr 失败。
    取不到签名 / async 标记不一致 → SKIP（留痕，不染红）。
    """
    stat: dict[str, int] = {"OK": 0, types.RAISED: 0}
    for idx, it in enumerate(items):
        if limit is not None and idx >= limit:
            reg.mark(it.id, types.NOTRUN, f"L0 截断（limit={limit}）", stage="L0")
            stat[types.NOTRUN] = stat.get(types.NOTRUN, 0) + 1
            continue
        t0 = time.perf_counter()
        ms = lambda: round((time.perf_counter() - t0) * 1000, 2)  # noqa: E731
        # 防御：万一有嵌套/property 漏进来，也别报成缺陷
        if _is_nested(it):
            reg.mark(it.id, types.SKIP, f"{SKIP_NESTED} 定义在函数内，模块级不可达", stage="L0",
                     skip_code=SKIP_NESTED)
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        if _is_descriptor_item(it):
            reg.mark(it.id, types.SKIP, f"{SKIP_PROPERTY} 描述符，不是可调用对象", stage="L0",
                     skip_code=SKIP_PROPERTY)
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        r = resolve(it)
        if r.err:
            where = "导入失败" if r.err_kind == "import" else "取不到对象"
            reg.mark(it.id, types.RAISED, f"L0 {where}: {sandbox.mask(r.err, limit=600)}",
                     stage="L0", err_kind=r.err_kind, ms=ms())
            stat[types.RAISED] += 1
            continue
        if isinstance(r.obj, property):
            # metadata 没带 decorators 时的兜底：descriptor 不是可调用对象
            reg.mark(it.id, types.SKIP, f"{SKIP_PROPERTY} property 描述符，不是可调用对象",
                     stage="L0", skip_code=SKIP_PROPERTY)
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        try:
            sig = inspect.signature(r.obj)
        except (TypeError, ValueError) as exc:
            if isinstance(r.obj, type):
                # builtin 基类（RuntimeError/Exception…）的子类取不到签名是 CPython 的限制，
                # 不是仓库缺陷：确认"它是类且可调用"即判 OK，只在 detail 里留痕。
                reg.mark(it.id, types.OK,
                         f"L0 可调用（类）；签名由 builtin 基类继承，不可 introspect: {type(exc).__name__}",
                         stage="L0", sig="(builtin)", has_doc=bool(inspect.getdoc(r.obj)),
                         sig_unknown=True, ms=ms())
                stat["OK"] += 1
                continue
            reg.mark(it.id, types.SKIP,
                     f"{SKIP_SIG} L0 取不到签名（C 扩展/内置）：{type(exc).__name__}: {exc}",
                     stage="L0", skip_code=SKIP_SIG, sig_unknown=True, ms=ms())
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        try:
            is_async = inspect.iscoroutinefunction(r.obj) or inspect.isasyncgenfunction(r.obj)
        except Exception:  # noqa: BLE001
            is_async = False
        ast_async = bool(it.extra.get("is_async"))
        if is_async != ast_async:
            reg.mark(it.id, types.SKIP,
                     f"{SKIP_ASYNC} async 标记不一致: inspect={is_async} ast={ast_async}",
                     stage="L0", skip_code=SKIP_ASYNC, async_mismatch=True, ms=ms())
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        doc = inspect.getdoc(r.obj) or ""
        reg.mark(
            it.id, types.OK,
            f"L0 签名 {str(sig)[:180]}" + ("" if doc else " | 无 docstring"),
            stage="L0", sig=str(sig)[:400], has_doc=bool(doc),
            doc=(doc.splitlines()[0][:120] if doc else ""),
            ms=ms(),
        )
        stat["OK"] += 1
    return stat


# ====================================================== 调用与判据 ==
def _fmt_args(args: list[Any], kwargs: dict[str, Any], *, limit: int = 220) -> str:
    def one(v: Any) -> str:
        try:
            if callable(v) and not isinstance(v, type):
                return f"<callable {getattr(v, '__name__', '?')}>"
            txt = repr(v)
        except Exception:  # noqa: BLE001
            txt = f"<{type(v).__name__}>"
        return txt if len(txt) <= 60 else txt[:57] + "…"

    parts = [one(a) for a in args] + [f"{k}={one(v)}" for k, v in kwargs.items()]
    out = ", ".join(parts)
    return sandbox.mask(out[:limit])


def _raise_origin(exc: BaseException) -> tuple[bool, str]:
    """判断异常是在"调用边界"（参数被 Python 拒）还是在函数体里。

    返回 (boundary, 首帧描述)。boundary=True 说明参数没进去 → 判 SKIP。
    """
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return True, "no-traceback"
    last = tb[-1]
    fname = pathlib.Path(str(last.filename)).name
    boundary = fname == "probe.py"
    desc = f"{fname}:{last.lineno} in {last.name}"
    return boundary, desc


def _looks_abstract(exc: BaseException) -> bool:
    msg = str(exc)
    return isinstance(exc, TypeError) and ("abstract" in msg or "abstractmethod" in msg)


@contextlib.contextmanager
def _quiet():
    """隔离被探函数的副作用：吃掉 stdout/stderr，并把 `sys.argv` 换成探针自己的。

    为什么必须做：`app.cli.*` 这类入口会 `print()` 一大段、还会读 `sys.argv[1:]`
    ——实测它把探针的参数 `--levels L0,L1` 当成了自己的子命令，直接 argpase 报错退出。
    """
    old_argv = sys.argv
    out, err = io.StringIO(), io.StringIO()
    sys.argv = ["probe"]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield out, err
    finally:
        sys.argv = old_argv


async def invoke(obj: Any, args: list[Any], kwargs: dict[str, Any], *, timeout: float) -> Any:
    """真调一次：async 直接 await，sync 丢线程（20s 上限）。"""
    if inspect.iscoroutinefunction(obj) or inspect.isasyncgenfunction(obj):
        res = obj(*args, **kwargs)
        if inspect.isawaitable(res):
            return await asyncio.wait_for(res, timeout)
        return res
    call = lambda: obj(*args, **kwargs)  # noqa: E731
    res = await asyncio.wait_for(asyncio.to_thread(call), timeout)
    if inspect.iscoroutine(res):
        res.close()  # 工厂函数返回的协程没人 await：显式关掉，别留 "never awaited" 警告
    return res


def _owner_class(item: types.Item, r: _Resolved) -> Any:
    """`Class.method` 的 owner 类；取不到返回 None。"""
    qual = str(item.extra.get("qualname") or "")
    if "." not in qual:
        return None
    obj: Any = r.mod
    try:
        for part in qual.rsplit(".", 1)[0].split("."):
            obj = getattr(obj, part)
    except AttributeError:
        return None
    return obj if isinstance(obj, type) else None


async def _instance_for(cls: Any, *, timeout: float) -> tuple[Any, str]:
    """给未绑定方法造一个 self：按构造签名合成，失败返回 (None, reason)。"""
    import sys as _sys  # noqa: PLC0415

    mod = _sys.modules.get(getattr(cls, "__module__", ""), None)
    globals_ = getattr(mod, "__dict__", {}) or {}
    args, kwargs, problems = synthesize(cls, globals_, module=str(getattr(cls, "__module__", "")))
    if problems:
        return None, "构造参数合不出来（" + "; ".join(problems)[:160] + "）"
    try:
        inst = await asyncio.wait_for(asyncio.to_thread(lambda: cls(*args, **kwargs)), timeout)
        return inst, ""
    except asyncio.TimeoutError:
        return None, f"{getattr(cls, '__name__', '?')}() 超时 {timeout}s"
    except KeyboardInterrupt:
        raise
    except SystemExit as exc:
        return None, f"{getattr(cls, '__name__', '?')}() 调了 sys.exit({exc.code})"
    except BaseException as exc:  # noqa: BLE001
        return None, f"{getattr(cls, '__name__', '?')}() 抛 {type(exc).__name__}: {exc}"


def _is_route_handler(obj: Any) -> bool:
    """签名里有 FastAPI 依赖注入默认值 → 这是路由处理函数，不能脱离 ASGI 直调。"""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.default is inspect.Parameter.empty:
            continue
        kind = type(p.default)
        if kind.__name__ in _FASTAPI_DEFAULTS or kind.__module__.startswith("fastapi.params"):
            return True
    return False


def _is_http_exc(exc: BaseException) -> bool:
    kind = type(exc)
    return kind.__name__.endswith("HTTPException") and kind.__module__.split(".")[0] in ("fastapi", "starlette")


async def _probe_one(it: types.Item, *, timeout: float) -> tuple[str, str, dict[str, Any]]:
    """单条 L1 调用，返回 (status, detail, extra)。"""
    r = resolve(it)
    if r.err:
        return types.RAISED, f"L1 解析失败: {sandbox.mask(r.err, limit=400)}", {"stage": "L1", "err_kind": r.err_kind}
    obj = r.obj
    extra: dict[str, Any] = {"stage": "L1"}
    if isinstance(obj, property):
        return (types.SKIP, f"{SKIP_PROPERTY} property 描述符，不是可调用对象",
                {**extra, "skip_code": SKIP_PROPERTY})
    # 通过类拿到的实例方法：先造 self，再调 bound method（否则 bind 必然缺 self）
    if inspect.isfunction(obj):
        try:
            params = list(inspect.signature(obj).parameters.values())
        except (TypeError, ValueError):
            params = []
        first = params[0].name if params else ""
        if first in ("self", "cls"):
            owner = _owner_class(it, r)
            if owner is None:
                return (types.SKIP, f"{SKIP_SYNTH} 未绑定方法，找不到 owner 类（{first}）",
                        {**extra, "skip_code": SKIP_SYNTH})
            inst, why = await _instance_for(owner, timeout=timeout)
            if inst is None:
                return (types.SKIP, f"{SKIP_SYNTH} self: {why}"[:600],
                        {**extra, "skip_code": SKIP_SYNTH, "self_class": getattr(owner, "__name__", "")})
            try:
                obj = getattr(inst, str(it.extra.get("qualname") or "").rsplit(".", 1)[-1])
            except AttributeError as exc:
                return (types.SKIP, f"{SKIP_SYNTH} self: {getattr(owner, '__name__', '?')} 上取不到方法: {exc}",
                        {**extra, "skip_code": SKIP_SYNTH})
            extra["self_class"] = getattr(owner, "__name__", "")
    if _is_route_handler(obj):
        return (types.SKIP, f"{SKIP_ROUTE} 参数默认值是 FastAPI 依赖对象（Query/Path/Body/Depends），"
                            f"脱离 ASGI 直调没有意义（归 routes phase）",
                {**extra, "skip_code": SKIP_ROUTE})
    args, kwargs, problems = synthesize(obj, _mod_globals(r), module=it.module)
    if problems:
        return (types.SKIP, f"{SKIP_SYNTH} {'; '.join(problems)[:600]}",
                {**extra, "args": "", "skip_code": SKIP_SYNTH})
    try:
        inspect.signature(obj).bind(*args, **kwargs)
    except TypeError as exc:
        return (types.SKIP, f"{SKIP_BIND} {exc}",
                {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_BIND})
    t0 = time.perf_counter()
    _qname = str(it.extra.get("qualname") or it.name or "").rsplit(".", 1)[-1]
    if _qname in _BY_DESIGN_LOOPS:
        # 常驻循环（群主动搭话 / botpy 长连接）本来就不返回，进程内调必然超时
        return (types.SKIP, f"按设计常驻循环（{_qname}），不参与进程内调用",
                {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_PRECOND})
    try:
        with _quiet() as (buf_out, buf_err):
            await invoke(obj, args, kwargs, timeout=timeout)
    except asyncio.TimeoutError:
        return (types.HANG, f"L1 超时 {timeout}s（参数 {_fmt_args(args, kwargs)}）",
                {**extra, "args": _fmt_args(args, kwargs), "timeout": timeout})
    except SystemExit as exc:
        # 脚本/CLI 入口被调起来就会 sys.exit —— 这是它的设计，不是缺陷
        return (types.SKIP, f"{SKIP_SYSEXIT} SystemExit({exc.code})：入口函数按设计退出进程",
                {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_SYSEXIT})
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 - 探针必须吞住一切，但不吞 Ctrl-C
        boundary, first = _raise_origin(exc)
        msg = sandbox.mask(f"{type(exc).__name__}: {exc}", limit=500)
        if _is_http_exc(exc):
            # 直调 route handler 拿到 404/403：临时库是空的，这是语义上的正常结果
            return (types.SKIP, f"{SKIP_HTTP} {msg}（直调 handler，归 routes phase 复验）",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_HTTP})
        if type(exc).__name__ == "NetworkBlocked" or "network blocked" in str(exc).lower():
            # 沙箱断网守卫命中：这是测试环境的预期行为，不是产品缺陷
            return (types.SKIP, f"{SKIP_NET} 外网被沙箱拦截（预期）：{msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_NET})
        if _is_precondition(msg):
            # 前置条件不满足（线程要求/运行期未初始化/探针乱序把 store 关了）：
            # 这是"探针环境问题"，不是产品缺陷——但要留原文备查
            return (types.SKIP, f"{SKIP_PRECOND} 前置条件不满足（探针环境）：{msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_PRECOND})
        if _looks_abstract(exc) and boundary:
            return (types.SKIP, f"{SKIP_ABSTRACT} {msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_ABSTRACT})
        if isinstance(exc, NotImplementedError):
            # 抽象基类方法被直接调用：这是"该抛且抛了"（子类负责实现）
            return (types.EXPECTED, f"抽象基类按设计抛 NotImplementedError: {msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "abstract_call": True,
                     "exc_type": type(exc).__name__})
        if isinstance(exc, (ValueError, TypeError)) and (
                any(k in str(exc) for k in _VALIDATION_HINTS) or _VALIDATION_RE.search(str(exc))):
            # 校验函数按设计拒绝我们编的参数（"日型只能是 workday/…"）：该抛且抛了 → EXPECTED
            return (types.EXPECTED, f"校验按设计拒绝合成参数: {msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "validate": True,
                     "exc_type": type(exc).__name__})
        if isinstance(exc, (ValueError, RuntimeError, KeyError)) and any(
                k in str(exc) for k in _PRECOND_HINTS):
            # 业务前置条件（"好感还没到"）：临时库凑不出那个状态，不是缺陷
            return (types.SKIP, f"{SKIP_PRECOND} 业务前置条件不满足: {msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_PRECOND})
        if boundary and isinstance(exc, (TypeError, ValueError)) and "signature" not in msg:
            return (types.SKIP, f"{SKIP_CALLTYPE} {msg}",
                    {**extra, "args": _fmt_args(args, kwargs), "skip_code": SKIP_CALLTYPE,
                     "param_mismatch": True, "origin": first})
        return (types.RAISED, f"L1 {msg} @ {first} | 参数 {_fmt_args(args, kwargs)}",
                {**extra, "args": _fmt_args(args, kwargs), "origin": first,
                 "exc_type": type(exc).__name__})
    else:
        ms = round((time.perf_counter() - t0) * 1000, 2)
        noise = sandbox.mask(buf_out.getvalue().strip()[:200], limit=200)
        if noise:
            extra["stdout"] = noise
        return (types.OK, f"L1 调用成功 {ms}ms | 参数 {_fmt_args(args, kwargs)}"
                + (f" | stdout={noise[:80]}" if noise else ""),
                {**extra, "args": _fmt_args(args, kwargs), "ms": ms})


async def l1(reg: registry.Registry, items: list[types.Item], *, timeout: float = CALL_TIMEOUT,
             limit: int | None = None) -> dict[str, int]:
    """进程内真调。同模块前一项 HANG 会 poison 后面几项（线程收不回来）。"""
    stat: dict[str, int] = {}
    poisoned: set[str] = set()
    done = 0
    for it in items:
        if limit is not None and done >= limit:
            reg.mark(it.id, types.NOTRUN, f"L1 截断（limit={limit}）", stage="L1")
            stat[types.NOTRUN] = stat.get(types.NOTRUN, 0) + 1
            continue
        if it.module in poisoned:
            reg.mark(it.id, types.SKIP, f"{SKIP_POISON} 同模块前一项 HANG，已转 L2 子进程", stage="L1",
                     skip_code=SKIP_POISON)
            stat[types.SKIP] = stat.get(types.SKIP, 0) + 1
            continue
        done += 1
        status, detail, extra = await _probe_one(it, timeout=timeout)
        if status == types.HANG:
            poisoned.add(it.module)
        if status in (types.OK, types.RAISED):
            # 保留 L0 的 metadata（sig/doc），只覆盖执行结论
            extra.setdefault("l0_stage", it.extra.get("stage", ""))
        reg.mark(it.id, status, detail, **extra)
        stat[status] = stat.get(status, 0) + 1
    return stat


# ============================================================== L2 ==
def _log_dir() -> pathlib.Path:
    run_dir = os.environ.get("SYMEM_ADV_RUN")
    base = pathlib.Path(run_dir) if run_dir else (ROOT / "logs")
    d = base / "probe-subprocess"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_name(module: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{registry.slug(module, maxlen=60)}-{stamp}.log"


def l2(reg: registry.Registry, items: list[types.Item], *, timeout: float = SUBPROCESS_TIMEOUT) -> dict[str, int]:
    """逐模块起子进程批跑（60s 硬超时），用 --batch 入口的 JSON 回报落 item。"""
    stat: dict[str, int] = {}
    by_module: dict[str, list[types.Item]] = {}
    for it in items:
        by_module.setdefault(it.module, []).append(it)
    log_dir = _log_dir()
    for module, group in sorted(by_module.items()):
        log_path = log_dir / _log_name(module)
        #: 子进程回报也用 `.log` 后缀：`.gitignore` 里的 `*.log` 会一起忽略掉，
        #: 不往 `git status` 里丢未跟踪文件（`*.json` 没有这条豁免）。
        out_json = log_path.with_name(log_path.name.replace(".log", ".report.log"))
        cmd = [
            sys.executable, "-m", "app.qa.probe",
            "--module", module, "--batch",
            "--out", str(out_json),
            "--tag", "probe_sub",
        ]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(ROOT)
        env["SYMEM_PROBE_TIMEOUT"] = str(min(BATCH_CALL_TIMEOUT, timeout))
        t0 = time.perf_counter()
        report: dict[str, Any] | None = None
        err = ""
        try:
            proc = _run_subprocess(cmd, timeout=timeout, env=env)
            log_path.write_text(
                f"$ {' '.join(cmd)}\n--- exit {proc.returncode} / {round(time.perf_counter() - t0, 2)}s ---\n"
                + (proc.stdout or "") + ("\n[stderr]\n" + (proc.stderr or "") if proc.stderr else ""),
                encoding="utf-8",
            )
            if out_json.exists():
                report = json.loads(out_json.read_text(encoding="utf-8"))
            if proc.returncode != 0:
                err = f"exit={proc.returncode}: {(proc.stderr or proc.stdout or '')[-300:]}"
        except _SubprocessTimeout as exc:
            log_path.write_text(f"$ {' '.join(cmd)}\n--- TIMEOUT {timeout}s ---\n{exc}", encoding="utf-8")
            err = f"子进程 {timeout}s 超时"
        except OSError as exc:
            log_path.write_text(f"$ {' '.join(cmd)}\n--- SPAWN FAILED ---\n{exc}", encoding="utf-8")
            err = f"子进程起不来: {exc}"
        ms = round((time.perf_counter() - t0) * 1000, 1)
        got = {str(x.get("id")): x for x in (report or {}).get("items", [])} if report else {}
        for it in group:
            row = got.get(it.id)
            if row is not None:
                status = str(row.get("status") or types.RAISED)
                detail = str(row.get("detail") or "")[:600]
                payload = dict(row.get("extra") or {})
                payload.pop("stage", None)  # 子进程报的是 L1，父进程统一盖成 L2
                reg.mark(it.id, status, f"L2 {sandbox.mask(detail, limit=600)}", stage="L2",
                         sub_ms=ms, log=str(log_path), **payload)
                stat[status] = stat.get(status, 0) + 1
                continue
            if "超时" in err:
                reg.mark(it.id, types.HANG, f"L2 {err}", stage="L2", sub_ms=ms, log=str(log_path))
                stat[types.HANG] = stat.get(types.HANG, 0) + 1
            else:
                reg.mark(it.id, types.RAISED, f"L2 子进程未回报: {sandbox.mask(err, limit=400)}",
                         stage="L2", sub_ms=ms, log=str(log_path))
                stat[types.RAISED] = stat.get(types.RAISED, 0) + 1
    return stat


class _SubprocessTimeout(RuntimeError):
    pass


def _run_subprocess(cmd: list[str], *, timeout: float, env: dict[str, str]):
    """同步跑子进程（l2 是同步函数，在 run() 里用 to_thread 调）。"""
    import subprocess  # noqa: PLC0415

    try:
        return subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _SubprocessTimeout(
            f"{exc}\nstdout: {(exc.stdout or '')[-500:]}" if exc.stdout else str(exc)
        ) from exc


# ============================================================== 汇总 ==
def _severity(item: types.Item) -> str:
    """RAISED/HANG 的严重度（启发式，不是法律）。

    - 导入 / 取不到对象 / 取不到签名 → **P0**
    - async 标记不一致 → **P1**
    - 异常最深帧落在 `app/core/db.py`（DB 层真拒了）→ **P1**：这类基本是真缺陷
      （例：`users.purge_self` 对只读视图 `memories` 执行 DELETE）
    - TypeError/KeyError/AttributeError 等"参数形状可疑" → **P2**
    - 其余 → P1
    """
    if item.status == types.HANG:
        return types.P1
    kind = str(item.extra.get("err_kind") or "")
    if kind in ("import", "attr", "signature"):
        return types.P0
    if kind == "async_mismatch":
        return types.P1
    exc_type = str(item.extra.get("exc_type") or "")
    origin = str(item.extra.get("origin") or "")
    if exc_type in ("ModuleNotFoundError", "ImportError"):
        return types.P0
    if origin.startswith(("db.py:", "memv2.py:")) and exc_type in (
            "OperationalError", "IntegrityError", "DatabaseError", "ProgrammingError"):
        return types.P1
    if exc_type in ("TypeError", "ValueError", "KeyError", "IndexError", "IntegrityError",
                    "AttributeError", "AssertionError", "RuntimeError", "OSError",
                    "OperationalError", "DatabaseError"):
        return types.P2
    return types.P1


def _findings(items: list[types.Item]) -> list[types.Finding]:
    out: list[types.Finding] = []
    n = 0
    for it in items:
        if it.status not in (types.RAISED, types.HANG):
            continue
        n += 1
        stage = str(it.extra.get("stage") or "L0")
        detail = it.detail
        out.append(types.Finding(
            id=f"F-PROBE-{n:04d}",
            severity=_severity(it),
            area=it.module or "app",
            title=f"{'超时' if it.status == types.HANG else '调用异常'}: {it.name}",
            detail=detail,
            evidence={
                "item": it.id,
                "stage": stage,
                "risk": it.risk,
                "args": it.extra.get("args", ""),
                "origin": it.extra.get("origin", ""),
                "exc_type": it.extra.get("exc_type", ""),
                "log": it.extra.get("log", ""),
            },
            repro=f".venv\\Scripts\\python.exe -m app.qa.probe --module {it.module} --levels L0,L1",
            item_id=it.id,
        ))
        if len(out) >= 400:  # 报告别被单一 phase 撑爆
            break
    return out


# ============================================================== 入口 ==
async def run(reg: registry.Registry, *, levels=("L0", "L1", "L2"), limit: int | None = None,
              timeout: float = CALL_TIMEOUT, subprocess_timeout: float = SUBPROCESS_TIMEOUT,
              reset_db: bool = False, network_guard: bool = True,
              include_private: bool = False) -> types.PhaseResult:
    """按层执行已注册的探针 item，返回 PhaseResult（phase="functions"）。

    先把"不归本 phase 跑"的 item（CLS / 嵌套 / property / private / plan=L0）标 SKIP，
    再执行 FN:`METH:` 里真正该跑的那批 —— 覆盖率分母才不会被这些项污染。
    """
    res = types.PhaseResult(phase="functions")
    skipped = skip_excluded(reg, include_private=include_private)
    items = _probe_items(reg, include_private=include_private)
    if not items:
        res.notes = "没有可执行的 FN/METH item：先调 register()（或全部被 SKIP 规则的过滤掉了）"
        res.finish()
        return res
    stats: dict[str, dict[str, int]] = {}
    if skipped:
        stats["SKIP_EXCLUDED"] = skipped
    order = [str(x).upper() for x in levels]

    ctx = sandbox.network_guard() if network_guard else None
    blocked: list[str] = []
    t_start = time.perf_counter()
    if ctx is not None:
        blocked = ctx.__enter__()
    try:
        # 先把运行期建起来：db.init() 会跑引擎迁移并建 `memories` 视图；
        # 否则 session.archive / terminate 这类读视图的函数会在空库上 no such table
        if "L1" in order or "L2" in order:
            try:
                _CTX.rt()
            except Exception:  # noqa: BLE001 - 起不来就让后面的 SKIP 兜住
                pass
        if "L0" in order:
            stats["L0"] = l0(reg, items, limit=limit)
        if "L1" in order:
            pool = [i for i in items if i.plan == "L1"]
            if reset_db and pool:
                _resetlib()
            stats["L1"] = await l1(reg, pool, timeout=timeout, limit=limit)
        if "L2" in order:
            pool = [i for i in items if i.plan == "L2"]
            if pool:
                stats["L2"] = await asyncio.to_thread(l2, reg, pool, timeout=subprocess_timeout)
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    res.items = _all_probe_items(reg)  # 含 SKIP 的那批：报告里"列了没跑"要显性可见
    res.findings = _findings(res.items)
    counts = reg.counts()
    skip_codes: dict[str, int] = {}
    for it in items:
        code = str(it.extra.get("skip_code") or "")
        if code:
            skip_codes[code.rstrip(":")] = skip_codes.get(code.rstrip(":"), 0) + 1
    parts = [
        f"levels={','.join(order)}",
        f"items={len(res.items)}（本次真正执行 {len(items)} 条）",
        f"registry_counts={json.dumps(counts, ensure_ascii=False)}",
        f"tier={json.dumps(stats, ensure_ascii=False)}",
    ]
    if limit is not None:
        parts.append(f"limit={limit}（超出者记 NOTRUN）")
    if skip_codes:
        parts.append(f"skip_codes={json.dumps(skip_codes, ensure_ascii=False)}")
    if blocked:
        parts.append(f"network_blocked={len(blocked)} 次: {sandbox.mask(json.dumps(blocked[:8], ensure_ascii=False))}")
    if reset_db:
        parts.append("reset_db=True（每轮 L1 前重建临时库）")
    parts.append(f"wall={round(time.perf_counter() - t_start, 2)}s")
    res.notes = " | ".join(parts)
    return res.finish()


def _resetlib() -> None:
    """软重置临时库：关掉 memengine 单例 → 删库文件 → 重新 init。"""
    try:
        from app.core import db, memengine  # noqa: PLC0415

        memengine.reset()
        sandbox.reset_db()
        db.init(pathlib.Path(os.environ["SYMEM_DB"]))
    except Exception:  # noqa: BLE001 - 重置失败不该打断整轮（下一项自己会报错）
        pass


# --------------------------------------------------------------- batch --
def _batch(argv: list[str]) -> int:
    """子进程入口：只为指定模块跑 L0+L1（本进程内，不再往下起子进程）。"""
    sandbox.prepare_env("probe_sub")
    ap = argparse.ArgumentParser(prog="app.qa.probe --batch")
    ap.add_argument("--module", action="append", default=[])
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="probe_sub")
    args = ap.parse_args([a for a in argv if a != "--batch"])
    modules = [m for m in args.module if m]
    timeout = float(os.environ.get("SYMEM_PROBE_TIMEOUT") or BATCH_CALL_TIMEOUT)
    reg = registry.Registry()
    n = register(reg, scan(modules=modules))
    skip_excluded(reg)
    items = _probe_items(reg)
    l0(reg, items)
    # 子进程也要先把运行期建起来（db.init + 引擎迁移 + memories 视图），
    # 否则 detect_dnd / session.archive 这类函数会在没初始化的库上炸
    try:
        _CTX.rt()
    except Exception:  # noqa: BLE001
        pass
    blocked: list[str] = []
    with sandbox.network_guard() as bl:
        asyncio.run(l1(reg, items, timeout=timeout))
        blocked = bl
    payload = {
        "modules": modules,
        "registered": n,
        "counts": reg.counts(),
        "network_blocked": [sandbox.mask(str(x)) for x in blocked[:20]],
        "timeout": timeout,
        "items": [
            {"id": it.id, "status": it.status, "detail": sandbox.mask(it.detail, limit=600),
             "extra": {k: v for k, v in it.extra.items() if k in
                       ("stage", "args", "origin", "exc_type", "ms", "skip_code", "param_mismatch")}}
            for it in items
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        path = pathlib.Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0


def _main(argv: list[str]) -> int:
    if "--batch" in argv:
        return _batch(argv)
    sandbox.prepare_env("probe")
    ap = argparse.ArgumentParser(prog="app.qa.probe", description="逐函数动态探针")
    ap.add_argument("--levels", default="L0,L1", help="L0,L1,L2（默认 L0,L1）")
    ap.add_argument("--module", action="append", default=[], help="只探这些模块（可重复/逗号分隔）")
    ap.add_argument("--limit", type=int, default=None, help="每层最多执行多少条")
    ap.add_argument("--timeout", type=float, default=CALL_TIMEOUT)
    ap.add_argument("--subprocess-timeout", type=float, default=SUBPROCESS_TIMEOUT)
    ap.add_argument("--reset-db", action="store_true")
    ap.add_argument("--include-private", action="store_true",
                    help="连私有函数一起调（默认按契约跳过 private）")
    ap.add_argument("--out", default="", help="把 PhaseResult 写成 JSON")
    #: 兼容 `scripts/adv_full.py --phase functions` 的调用习惯；本模块只做 functions
    ap.add_argument("--phase", default="", help="兼容参数：只接受 functions（其余忽略）")
    args = ap.parse_args(argv)
    if args.phase and args.phase not in ("functions", "probe", "all"):
        print(f"[warn] probe 只负责 functions 阶段，忽略 --phase {args.phase!r}")
    mods: list[str] = []
    for chunk in args.module:
        mods.extend([x.strip() for x in chunk.split(",") if x.strip()])
    reg = registry.Registry()
    n = register(reg, scan(modules=mods or None))
    print(f"registered={n} modules={len({i.module for i in reg.items.values()})}")
    res = asyncio.run(run(
        reg,
        levels=tuple(x.strip().upper() for x in args.levels.split(",")),
        limit=args.limit, timeout=args.timeout, subprocess_timeout=args.subprocess_timeout,
        reset_db=args.reset_db, include_private=args.include_private,
    ))
    print(f"phase=functions secs={res.secs} counts={json.dumps(res.counts(), ensure_ascii=False)}")
    print(f"notes: {res.notes}")
    bad = [i for i in res.items if i.status in (types.RAISED, types.HANG)]
    bad.sort(key=lambda x: (x.module, x.line))
    for it in bad[:15]:
        exc_type = str(it.extra.get("exc_type") or it.extra.get("err_kind") or "-")
        first = it.detail.split(" @ ")[0].split(" | ")[0][:160]
        print(f"  [{it.status}] {it.module}.{it.extra.get('qualname') or it.name}: {exc_type}: {first}")
    if len(bad) > 15:
        print(f"  ... 另有 {len(bad) - 15} 条（见 --out JSON）")
    if args.out:
        pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.out).write_text(
            json.dumps(res.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
