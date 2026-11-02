"""inventory —— 全量静态清单：把仓库里每个可调用面变成一条有 ID 的 Item。

## 为什么全程用 ast 而不 import

见 `app/qa/CONTRACT.md` §0.1。`import app.main` 会建库、读 persona、起后台协程；
`import app.core.paths` 会把 `SYMEM_DATA` 读死。本模块**只读文件文本**，副作用为零，
所以可以在任何 phase 之前安全执行，也可以被 93+ 个文件的扫描重复调用。

## 全量口径（不抽样）

    kind=module    app/**/*.py 每个文件一条（`MOD:app.memory.h2`）
    kind=fn        模块级函数 + 函数内嵌套函数（`extra.nested=True`），含私有 `_x`
    kind=method    类体内的函数（含 property / staticmethod / classmethod）
    kind=class     每个 class（含嵌套类）
    kind=route     `@router.get/post/...`、`@app.get/...`（含 create_app() 内条件注册的）
    kind=cli       `app/cli.py` 里 `sub.add_parser("name", ...)`
    kind=table     `app/core/db.py` 的 `_SCHEMA` + `data/life.db` 的 sqlite_master（**只读
                   mode=ro**；`sqlite_%` 内部表如 sqlite_sequence 不算业务表，不列）
    kind=config    `app/core/config.py` 的 dataclass 叶子字段（逐层展开到 `provider.api_key`）
    kind=persona   `persona/whale-chan.yaml` 顶层键

## risk 静态判定依据（按顺序取第一个命中，命中即停）

1. `danger` —— **被调名**（callee）的标识符 token 命中 `subprocess / rmtree / unlink /
   uninstall / release / pyinstaller`。会删文件 / 起进程 / 打包，进程内调用后果不可控。
2. `hang`(强) —— 函数体里有 `while True`（Constant 真值）。循环没有出口，这类"永不返回"
   的函数只能放子进程 + 超时（plan=L2）。
3. `net`    —— callee token 命中 `httpx / requests / socket / bili / harvest / fetch /
   urlopen`。测试在断网沙箱里跑，进程内调用必被打回（NetworkBlocked），单列以便统计。
4. `llm`    —— 点分名里出现 `llm / judge` 段、`models.pick`，或**以 chat 开头的多词函数名**
   （`chat_turn` / `chat_completions`）。mock 下可调，但要单独标出"多少函数依赖模型"。
   注意：不按 token 里出现 `chat` 就判 LLM——`h2.add` 里的 `_chat(chat_type)` 只是本地
   规范化小工具，按 token 判会把整个记忆写入误伤成 llm。
5. `hang`(弱) —— callee token 含 `sleep`。重试/打字延迟会睡一下，单看不足以判死；
   所以放在 llm 之后：既调模型又 sleep 的（`chat_turn`）按 llm 走 mock，纯 sleep 的才 hang。
6. `write`  —— callee token 命中 `create / write / add / set / forget / delete / terminate /
   compact / restore / ingest / send / push / save / insert / update`（外加语义等价、确实落库
   或改状态的 `commit / clear / reset / remove / append / upsert / migrate / apply / ensure`），
   **或**任一字符串常量里出现大写 SQL 关键字 `INSERT/UPDATE/DELETE`（SQL 只认大写，避免
   把中文注释/日志里的 "update" 当成写库）。
7. `read`   —— 以上都没命中。

上面前 5 条的顺序就是代码里的判定顺序；`while True` 的强 hang 排在第 2 位（循环没出口，
必挂），`sleep` 的弱 hang 排在第 5 位（见上）。

token 化规则：先按 `.` 切（取 callee 的点分名），再按 `_` 与 camelCase 切，最后小写。
这样 `settings` 不会被当成 `set`、`reset` 不会被当成 `set`、`offset` 不会被当成 `set`，
而 `h2.add` / `db.update` / `store.save` 能命中。**只扫 callee 名 + 字符串常量 + `while
True`**，不扫注释、docstring、变量名——因为"函数体出现 add"这种字符串匹配会把
`add_argument`、`annotation` 全判成写库，分类就废了。嵌套函数/类不并入外层风险（各算各的）。

优先级理由：danger 绝不能进程内跑；hang 会拖死整轮；net 在断网沙箱必失败；llm 走 mock；
write 在临时数据根里安全；剩下才是只读。

## plan 口径

    L4  route（HTTP 层）
    L2  cli / risk in (danger, hang)（子进程隔离 + 超时）
    L0  module / class / table / config / persona / 私有函数 / property（静态或只反射）
    L1  其余 fn / method（进程内调用）

## 容错与性能

- 单文件读不出 / SyntaxError：该 module 仍产出一条 `kind=module` 的 Item，
  `extra.parse_error` 记原因，写进 `manifest()["scan"]["parse_errors"]`，不整轮崩。
- 每个文件只读一次、只 parse 一次；结果按 (路径, mtime_ns, size) 指纹缓存，
  `build()` / `manifest()` / `probe_targets()` 重复调用不重复读盘。
- 97 个文件实测 ≈0.4s（含 ast.parse 与 db 只读打开），随仓库增长到 105 个文件约 0.7s，
  仍远低于 3s 预算。
﻿﻿‍‍​﻿‌‌﻿​‌‌‍‍‌‌‌﻿‌‌​‍‌﻿‌‌﻿‍‍‍‍​﻿﻿"""

from __future__ import annotations

import ast
import copy
import pathlib
import re
import sqlite3
import time
from typing import Any

from app.qa import sandbox, types

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
DB_FILE = ROOT / "data" / "life.db"
PERSONA_FILE = ROOT / "persona" / "whale-chan.yaml"
CONFIG_FILE = APP_DIR / "core" / "config.py"
DB_MODULE_FILE = APP_DIR / "core" / "db.py"

#: FastAPI 的 HTTP 方法装饰器名（含 websocket / api_route）
HTTP_VERBS = {
    "get", "post", "put", "patch", "delete", "head", "options",
    "websocket", "api_route",
}
#: router/app 之外的对象名不认，避免把 `@cache.get(...)` 之类误判成路由
ROUTER_BASES = ("router", "app")

_DANGER_TOKENS = frozenset({"subprocess", "rmtree", "unlink", "uninstall", "release", "pyinstaller"})
_NET_TOKENS = frozenset({"httpx", "requests", "socket", "bili", "harvest", "fetch", "urlopen"})
_WRITE_TOKENS = frozenset({
    "create", "write", "add", "set", "forget", "delete", "terminate",
    "compact", "restore", "ingest", "send", "push", "save", "insert", "update",
    # 语义等价、确实改库/改状态的补充动词（在 docstring 里声明过）
    "commit", "clear", "reset", "remove", "append", "upsert", "migrate", "apply", "ensure",
})
_SQL_WRITE_RE = re.compile(r"\b(INSERT|UPDATE|DELETE)\b")
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_PATH_PARAM_RE = re.compile(r"\{([^{}]+)\}")

#: CLI 子命令的显式风险（按语义写死，比从 handler 名字猜稳）
_CLI_RISK = {
    "serve": ("hang", "阻塞到 Ctrl-C 的 uvicorn.run；只能子进程 + 超时"),
    "ingest": ("write", "导入聊天记录，写 events/memories"),
    "init": ("write", "建库 + 初始化人物"),
    "reset": ("danger", "删 life.db / -wal / -shm"),
    "anchors": ("write", "重建锚点池，写 anchors"),
    "cold": ("write", "合并冷记忆，写 summaries/memory_merges"),
    "compress": ("write", "把旧对话折成线索，写 summaries"),
    "letter": ("read", "只打印记忆交接信模板"),
    "reference": ("write", "导入参考语料，写 ref_turns/ref_quotes"),
    "arc": ("read", "只统计情绪分期，不写库"),
}

_CACHE: dict[str, Any] = {"fp": None, "specs": None, "meta": None}


# --------------------------------------------------------------------------- 路径


def _rel(path: pathlib.Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:  # pragma: no cover - 文件不在仓库下
        return path.as_posix()


def _module_name(path: pathlib.Path) -> str:
    """`app/memory/h2.py` → `app.memory.h2`；`app/__init__.py` → `app`。"""
    try:
        rel = path.relative_to(APP_DIR)
    except ValueError:  # pragma: no cover
        rel = pathlib.Path(path.name)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(["app", *parts]) if parts else "app"


def _fingerprint() -> tuple[Any, ...]:
    parts: list[tuple[str, int, int]] = []
    for p in sorted(APP_DIR.rglob("*.py")):
        try:
            st = p.stat()
            parts.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((str(p), -1, -1))
    for p in (PERSONA_FILE, DB_FILE):
        try:
            st = p.stat()
            parts.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((str(p), -1, -1))
    return tuple(parts)


# --------------------------------------------------------------------- AST 工具


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)[:120]
    except Exception:  # noqa: BLE001 - 极端 AST 形态（如已孤立的 match 模式）
        return "?"


def _dotted(node: ast.AST) -> str:
    """把 `rt.llm.chat` 这种 callee 还原成点分字符串；还原不出就返回 ""。"""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, (ast.Attribute, ast.Subscript)):
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return ""
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _split_token(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _CAMEL_BOUNDARY.sub("_", str(text)).split("_"):
        if chunk:
            out.append(chunk.lower())
    return out


def _deco_tail(node: ast.AST) -> str:
    """装饰器尾巴：`@property` → property；`@x.setter` → setter。"""
    if isinstance(node, ast.Call):
        return _deco_tail(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _annotation_base(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _arg_text(arg: ast.arg, default: ast.AST | None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += ": " + _safe_unparse(arg.annotation)
    if default is not None:
        text += " = " + _safe_unparse(default)
    return text


def _format_sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """从 ast.arguments 拼出参数列表文本（带括号，含 / 与 * 分隔符）。"""
    try:
        a = fn.args
        parts: list[str] = []
        pos = list(a.posonlyargs) + list(a.args)
        n_def = len(a.defaults)
        first_def = len(pos) - n_def
        for idx, arg in enumerate(pos):
            default = a.defaults[idx - first_def] if idx >= first_def else None
            parts.append(_arg_text(arg, default))
            if a.posonlyargs and idx == len(a.posonlyargs) - 1:
                parts.append("/")
        if a.vararg is not None:
            parts.append("*" + _arg_text(a.vararg, None))
        elif a.kwonlyargs:
            parts.append("*")
        for kwarg, kdefault in zip(a.kwonlyargs, a.kw_defaults):
            parts.append(_arg_text(kwarg, kdefault))
        if a.kwarg is not None:
            parts.append("**" + _arg_text(a.kwarg, None))
        return ("(" + ", ".join(parts) + ")")[:300]
    except Exception:  # noqa: BLE001
        return "(?)"


# ------------------------------------------------------------------ 风险分析


class _BodyScan(ast.NodeVisitor):
    """只扫"会被执行"的语句：callee 名、字符串常量、while True。

    嵌套 def / class / lambda 是独立作用域，不并入外层（它们各自有自己的 Item）。
    """

    def __init__(self) -> None:
        self.callees: set[str] = set()
        self.strings: list[str] = []
        self.while_true = 0

    # --- 作用域边界：不下钻
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    # --- 采集
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _dotted(node.func)
        if name:
            self.callees.add(name)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        test = node.test
        if isinstance(test, ast.Constant) and test.value is True:
            self.while_true += 1
        elif isinstance(test, ast.Constant) and isinstance(test.value, int) and test.value == 1:
            self.while_true += 1
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            self.strings.append(node.value)


def _scan_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> _BodyScan:
    scan = _BodyScan()
    body = list(fn.body)
    # 跳过 docstring：文档里写 "DELETE 操作" 不该把函数判成写库
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    for stmt in body:
        scan.visit(stmt)
    return scan


def _llm_evidence(callees: set[str]) -> list[str]:
    """命中 LLM 的证据：`llm` / `judge` / `models.pick` / 真正的 `chat` 调用。

    这里不用"token 里出现 chat 就算"——`h2.add` 里的 `_chat(chat_type)` 只是
    "规范化 chat_type" 的本地小工具，token 同为 `chat`，会把整个记忆写入函数误判成
    依赖模型。只认三种形态：点分名里出现 `llm/judge` 段、`models.pick`、或者
    **以 chat 开头的多词函数名**（`chat` / `chat_turn` / `chat_completions`）。
    """
    strong: list[str] = []
    weak: list[str] = []
    for dotted in sorted(callees):
        segs = [s for s in dotted.split(".") if s]
        if not segs:
            continue
        toks = [t for seg in segs for t in _split_token(seg)]
        if "llm" in toks or "judge" in toks:
            strong.append(f"call:{dotted}")
        elif "models" in toks and "pick" in toks:
            strong.append(f"call:{dotted}")
        else:
            last_toks = _split_token(segs[-1])
            if segs[-1].lower() == "chat" or (len(last_toks) >= 2 and last_toks[0] == "chat"):
                weak.append(f"call:{dotted}")
    evidence = strong[:3] or weak[:3]
    return evidence


def _analyze(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, list[str]]:
    """返回 (risk, 命中依据)。规则与优先级见模块 docstring。"""
    scan = _scan_body(fn)
    toks: set[str] = set()
    for name in scan.callees:
        for part in name.split("."):
            toks.update(_split_token(part))
    toks.discard("")

    hit = sorted(toks & _DANGER_TOKENS)
    if hit:
        return "danger", [f"call:{t}" for t in hit[:3]]
    # while True 是强 hang（循环没有出口）；sleep 单独出现只是弱信号，放到 llm 之后再判，
    # 否则 `chat_turn`（内部有打字延迟的 sleep）会被误判成"会挂死"。
    if scan.while_true:
        return "hang", ["while True"]
    hit = sorted(toks & _NET_TOKENS)
    if hit:
        return "net", [f"call:{t}" for t in hit[:3]]
    llm_why = _llm_evidence(scan.callees)
    if llm_why:
        return "llm", llm_why
    if "sleep" in toks:
        return "hang", ["call:sleep"]
    hit = sorted(toks & _WRITE_TOKENS)
    sql = ""
    for text in scan.strings:
        match = _SQL_WRITE_RE.search(text)
        if match:
            sql = match.group(0)
            break
    if hit or sql:
        why = [f"call:{t}" for t in hit[:3]]
        if sql:
            why.append(f"sql:{sql}")
        return "write", why
    return "read", []


def _plan_for(kind: str, risk: str, private: bool, is_property: bool) -> str:
    if is_property:
        return "L0"          # property 单独调用没意义，只能反射
    if private:
        return "L0"          # 契约：私有函数默认静态
    if risk in ("danger", "hang"):
        return "L2"          # 子进程隔离 + 超时
    if risk == "net":
        return "L1"          # 在 network_guard 下进程内跑，靠异常判定
    return "L1"


# ---------------------------------------------------------------------- 扫描器


def _module_spec(module: str, rel: str, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"MOD:{module}",
        "kind": "module",
        "name": module,
        "module": module,
        "line": 1,
        "risk": "read",
        "plan": "L0",
        "extra": {"file": rel, **extra},
    }


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare):
            left = test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return True
    return False


def _walk_scope(
    stmts: list[ast.stmt],
    *,
    module: str,
    rel: str,
    prefix: str,
    in_class: bool,
    out: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    """递归列出当前作用域里的 def/class；if/try/for 里的定义也算（保持全量）。"""
    if depth > 12:  # pragma: no cover - 防病态输入
        return
    for node in stmts:
        if isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}"
            out.append({
                "id": f"CLS:{module}:{qual}",
                "kind": "class",
                "name": qual,
                "module": module,
                "line": node.lineno,
                "risk": "read",
                "plan": "L0",
                "extra": {
                    "file": rel,
                    "nested": prefix.endswith(".<locals>."),
                    "bases": [_safe_unparse(b) for b in node.bases][:6],
                    "decorators": [_safe_unparse(d) for d in node.decorator_list][:6],
                    "n_methods": sum(
                        1 for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ),
                    "has_doc": ast.get_docstring(node) is not None,
                },
            })
            _walk_scope(
                node.body, module=module, rel=rel, prefix=qual + ".",
                in_class=True, out=out, depth=depth + 1,
            )
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{node.name}"
            nested = prefix.endswith(".<locals>.")
            decorators = [_safe_unparse(d) for d in node.decorator_list][:8]
            tails = {_deco_tail(d) for d in node.decorator_list}
            is_property = bool(tails & {"property", "cached_property"})
            is_setter = bool(tails & {"setter", "getter", "deleter"})
            is_static = "staticmethod" in tails
            is_classmethod = "classmethod" in tails
            private = node.name.startswith("_")
            kind = "method" if in_class else "fn"
            risk, reasons = _analyze(node)
            out.append({
                "id": ("METH:" if kind == "method" else "FN:") + f"{module}:{qual}",
                "kind": kind,
                "name": qual,
                "module": module,
                "line": node.lineno,
                "risk": risk,
                "plan": _plan_for(kind, risk, private, is_property),
                "extra": {
                    "file": rel,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "private": private,
                    "decorators": decorators,
                    "sig": _format_sig(node),
                    "nested": nested,
                    "is_property": is_property,
                    "is_setter": is_setter,
                    "is_static": is_static,
                    "is_classmethod": is_classmethod,
                    "dunder": node.name.startswith("__") and node.name.endswith("__"),
                    "has_doc": ast.get_docstring(node) is not None,
                    "risk_reasons": reasons,
                },
            })
            _walk_scope(
                node.body, module=module, rel=rel, prefix=qual + ".<locals>.",
                in_class=False, out=out, depth=depth + 1,
            )
            continue

        if isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.AsyncFor, ast.AsyncWith)) \
                or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(node, field, None)
                if sub:
                    _walk_scope(
                        sub, module=module, rel=rel, prefix=prefix,
                        in_class=in_class, out=out, depth=depth + 1,
                    )
            for handler in getattr(node, "handlers", []) or []:
                _walk_scope(
                    handler.body, module=module, rel=rel, prefix=prefix,
                    in_class=in_class, out=out, depth=depth + 1,
                )


def _router_prefixes(tree: ast.Module) -> dict[str, tuple[str, bool]]:
    """`router = APIRouter(prefix="/v1/console", dependencies=[...])` → {router: (prefix, auth)}。"""
    out: dict[str, tuple[str, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = _dotted(node.value.func)
        if func.rpartition(".")[2] != "APIRouter":
            continue
        prefix = ""
        auth = False
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
            elif kw.arg == "dependencies":
                auth = True
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = (prefix, auth)
    return out


def _join_path(prefix: str, path: str) -> str:
    full = f"{prefix or ''}{path or ''}"
    if not full.startswith("/"):
        full = "/" + full
    while "//" in full:
        full = full.replace("//", "/")
    return full or "/"


def _route_methods(deco: ast.Call, verb: str) -> list[str]:
    if verb != "api_route":
        return ["WS" if verb == "websocket" else verb.upper()]
    for kw in deco.keywords:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            methods = [
                str(e.value).upper()
                for e in kw.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if methods:
                return methods
    return ["GET"]


def _route_specs(
    tree: ast.Module,
    *,
    module: str,
    rel: str,
    out: list[dict[str, Any]],
    notes: list[str],
) -> None:
    prefixes = _router_prefixes(tree)
    top_level = {id(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            dotted = _dotted(deco.func)
            owner, _, verb = dotted.rpartition(".")
            verb = verb.lower()
            if verb not in HTTP_VERBS or not owner:
                continue
            if owner not in prefixes and owner not in ROUTER_BASES:
                continue
            if not deco.args or not isinstance(deco.args[0], ast.Constant) \
                    or not isinstance(deco.args[0].value, str):
                notes.append(f"{rel}:{node.lineno} 路由 {dotted} 的 path 不是字符串常量，已跳过")
                continue
            prefix, auth = prefixes.get(owner, ("", False))
            full = _join_path(prefix, deco.args[0].value)
            handler_risk, reasons = _analyze(node)
            for method in _route_methods(deco, verb):
                risk = handler_risk
                if risk == "read" and method in ("POST", "PUT", "PATCH", "DELETE"):
                    risk = "write"      # 写方法至少是写，避免把 POST 全标成只读
                out.append({
                    "id": f"ROUTE:{method} {full}",
                    "kind": "route",
                    "name": f"{method} {full}",
                    "module": module,
                    "line": deco.lineno,
                    "risk": risk,
                    "plan": "L4",
                    "extra": {
                        "func": node.name,
                        "file": rel,
                        "method": method,
                        "path": full,
                        "prefix": prefix,
                        "router": owner,
                        "path_params": _PATH_PARAM_RE.findall(full),
                        "auth": auth,
                        "conditional": id(node) not in top_level,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "handler_risk": handler_risk,
                        "risk_reasons": reasons,
                        "decorators": [_safe_unparse(d) for d in node.decorator_list][:6],
                    },
                })


def _cli_specs(
    tree: ast.Module,
    *,
    module: str,
    rel: str,
    out: list[dict[str, Any]],
    notes: list[str],
) -> None:
    parsers: dict[str, dict[str, Any]] = {}
    handlers: dict[str, str] = {}
    options: dict[str, list[str]] = {}
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr == "add_parser" and call.args \
                    and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                name = call.args[0].value
                help_text = ""
                for kw in call.keywords:
                    if kw.arg == "help" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        help_text = kw.value.value
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        parsers[target.id] = {"name": name, "line": call.lineno, "help": help_text}
            elif call.func.attr == "add_argument" and call.args \
                    and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        options.setdefault(target.id, []).append(call.args[0].value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "set_defaults" and isinstance(node.func.value, ast.Name):
            for kw in node.keywords:
                if kw.arg == "func" and isinstance(kw.value, ast.Name):
                    handlers[node.func.value.id] = kw.value.id

    for var, meta in parsers.items():
        name = meta["name"]
        handler = handlers.get(var, "")
        mapping = _CLI_RISK.get(name)
        if mapping is not None:
            risk, why = mapping
            reasons = [f"cli:{why}"]
        else:
            fn_node = funcs.get(handler)
            risk, reasons = _analyze(fn_node) if fn_node is not None else ("read", [])
            notes.append(f"{rel}:{meta['line']} CLI {name} 没有显式风险映射，按 handler 推断 {risk}")
        out.append({
            "id": f"CLI:{name}",
            "kind": "cli",
            "name": name,
            "module": module,
            "line": meta["line"],
            "risk": risk,
            "plan": "L2",
            "extra": {
                "handler": handler,
                "file": rel,
                "help": meta["help"],
                "options": sorted(set(options.get(var, []))),
                "risk_reasons": reasons,
            },
        })


# ------------------------------------------------------------------------ 表


def _schema_tables() -> dict[str, dict[str, Any]]:
    """解析 `app/core/db.py` 的 `_SCHEMA` 里 CREATE TABLE 语句（带行号）。"""
    if not DB_MODULE_FILE.exists():
        return {}
    text = DB_MODULE_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(DB_MODULE_FILE))
    except SyntaxError:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_SCHEMA" for t in node.targets):
            continue
        value = node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        segment = ast.get_source_segment(text, value) or value.value
        for idx, line in enumerate(segment.splitlines()):
            match = _CREATE_TABLE_RE.search(line)
            if match:
                found.setdefault(match.group(1), {"line": value.lineno + idx, "source": "db._SCHEMA"})
    return found


def _live_tables(notes: list[str], errors: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """只读打开 data/life.db，读 sqlite_master + 列名/行数；文件不存在就跳过并记 notes。"""
    if not DB_FILE.exists():
        notes.append(f"{_rel(DB_FILE)} 不存在，表清单只含 db._SCHEMA（跳过活库校验）")
        return {}
    found: dict[str, dict[str, Any]] = {}
    try:
        conn = sqlite3.connect(f"{DB_FILE.as_uri()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        err = f"{type(exc).__name__}: {exc}"
        notes.append(f"以 mode=ro 打开 {_rel(DB_FILE)} 失败，跳过活库表：{err}")
        errors.append({"file": _rel(DB_FILE), "error": err})
        return {}
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in rows:
            quoted = '"' + str(name).replace('"', '""') + '"'
            try:
                columns = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({quoted})")]
                count = int(conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            except sqlite3.Error as exc:  # 视图/损坏表：列信息拿不到不算致命
                columns, count = [], None
                notes.append(f"读取表 {name} 的列/行数失败：{type(exc).__name__}: {exc}")
            found[str(name)] = {"columns": columns, "rows": count, "source": "data/life.db"}
    except sqlite3.Error as exc:
        err = f"{type(exc).__name__}: {exc}"
        notes.append(f"读 {_rel(DB_FILE)} 的 sqlite_master 失败：{err}")
        errors.append({"file": _rel(DB_FILE), "error": err})
    finally:
        try:
            conn.close()
        except sqlite3.Error:  # pragma: no cover
            pass
    return found


def _table_specs(notes: list[str], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    schema = _schema_tables()
    live = _live_tables(notes, errors)
    out: list[dict[str, Any]] = []
    for name in sorted(set(schema) | set(live)):
        in_schema = name in schema
        in_db = name in live
        sources = ([schema[name]["source"]] if in_schema else []) + ([live[name]["source"]] if in_db else [])
        extra = {
            "sources": sources,
            "in_schema": in_schema,
            "in_db": in_db,
            "columns": live.get(name, {}).get("columns", [])[:40],
            "n_columns": len(live.get(name, {}).get("columns", [])),
            "rows": live.get(name, {}).get("rows"),
        }
        out.append({
            "id": f"TBL:{name}",
            "kind": "table",
            "name": name,
            "module": "app.core.db" if in_schema else "data/life.db",
            "line": int(schema.get(name, {}).get("line", 0) or 0),
            "risk": "write",
            "plan": "L0",
            "extra": extra,
        })
    if not out:
        notes.append("没有解析到任何表（db.py/_SCHEMA 与 data/life.db 都为空）")
    return out


# --------------------------------------------------------------------- 配置键


def _config_specs(notes: list[str]) -> list[dict[str, Any]]:
    """静态展开 `app/core/config.py` 的 dataclass 字段树（不 import，不读 env）。"""
    if not CONFIG_FILE.exists():
        notes.append(f"{_rel(CONFIG_FILE)} 不存在，跳过配置键")
        return []
    text = CONFIG_FILE.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(CONFIG_FILE))
    except SyntaxError as exc:
        notes.append(f"{_rel(CONFIG_FILE)} 解析失败，跳过配置键：{exc.msg}")
        return []

    classes: dict[str, ast.ClassDef] = {}
    fields: dict[str, list[tuple[str, ast.AST | None, ast.AST | None, int]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_deco_tail(d) == "dataclass" for d in node.decorator_list):
            continue
        classes[node.name] = node
        collected: list[tuple[str, ast.AST | None, ast.AST | None, int]] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                if name.startswith("_"):
                    continue
                collected.append((name, stmt.annotation, stmt.value, stmt.lineno))
        fields[node.name] = collected

    if not classes:
        notes.append(f"{_rel(CONFIG_FILE)} 里没找到 @dataclass（配置键清单为空）")
        return []

    referenced: set[str] = set()
    for owner in fields:
        for _name, ann, _val, _line in fields[owner]:
            base = _annotation_base(ann)
            if base in classes:
                referenced.add(base)
    if "Config" in classes:
        roots = ["Config"]
    else:
        roots = [name for name in classes if name not in referenced]
        notes.append(f"{_rel(CONFIG_FILE)} 没有 Config 根类，改用未被引用的 dataclass 作根：{roots}")

    out: list[dict[str, Any]] = []

    def walk(cls_name: str, prefix: str, stack: frozenset[str]) -> None:
        if cls_name in stack:  # 循环引用防护
            notes.append(f"配置 dataclass 循环引用：{cls_name}")
            return
        for name, ann, val, line in fields.get(cls_name, []):
            dotted = f"{prefix}{name}"
            base = _annotation_base(ann)
            if base in classes:
                walk(base, dotted + ".", stack | {cls_name})
                continue
            out.append({
                "id": f"CFG:{dotted}",
                "kind": "config",
                "name": dotted,
                "module": "app.core.config",
                "line": line,
                "risk": "read",
                "plan": "L0",
                "extra": {
                    "class": cls_name,
                    "type": _safe_unparse(ann),
                    "default": _safe_unparse(val) if val is not None else "",
                    "root": roots[0] if roots else "",
                },
            })

    for root in roots:
        walk(root, "", frozenset())
    if not out:
        notes.append("配置键展开结果为空（dataclass 字段全是子配置？）")
    return out


# ----------------------------------------------------------------------- 人格


def _persona_specs(notes: list[str]) -> list[dict[str, Any]]:
    if not PERSONA_FILE.exists():
        notes.append(f"{_rel(PERSONA_FILE)} 不存在，跳过人格键")
        return []
    try:
        import yaml  # 惰性 import：只用 safe_load，不执行任何构造器
    except Exception as exc:  # noqa: BLE001
        notes.append(f"yaml 不可用，跳过人格键：{type(exc).__name__}: {exc}")
        return []
    try:
        data = yaml.safe_load(PERSONA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"{_rel(PERSONA_FILE)} 解析失败，跳过人格键：{type(exc).__name__}: {exc}")
        return []
    if not isinstance(data, dict):
        notes.append(f"{_rel(PERSONA_FILE)} 顶层不是 mapping（是 {type(data).__name__}），跳过人格键")
        return []

    out: list[dict[str, Any]] = []
    for key, value in data.items():
        extra: dict[str, Any] = {"type": type(value).__name__}
        if isinstance(value, dict):
            extra["size"] = len(value)
            extra["subkeys"] = [str(k) for k in list(value)[:12]]
        elif isinstance(value, list):
            extra["size"] = len(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            # 写进 items.json 的字符串一律过 mask（CONTRACT §0.4）
            extra["value"] = sandbox.mask(repr(value), limit=80)
        out.append({
            "id": f"PERSONA:{key}",
            "kind": "persona",
            "name": str(key),
            "module": _rel(PERSONA_FILE),
            "line": 0,
            "risk": "read",
            "plan": "L0",
            "extra": extra,
        })
    return out


# ------------------------------------------------------------------------ 主流程


def _scan() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    specs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    notes: list[str] = []
    files = sorted(APP_DIR.rglob("*.py"))

    for path in files:
        rel = _rel(path)
        module = _module_name(path)
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            err = f"{type(exc).__name__}: {exc}"
            specs.append(_module_spec(module, rel, {"parse_error": err}))
            errors.append({"file": rel, "error": err})
            continue
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError as exc:
            err = f"SyntaxError L{exc.lineno}: {exc.msg}"
            specs.append(_module_spec(module, rel, {"parse_error": err}))
            errors.append({"file": rel, "error": err})
            continue

        n_defs = sum(
            1 for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        specs.append(_module_spec(module, rel, {
            "lines": src.count("\n") + 1,
            "has_main_guard": _has_main_guard(tree),
            "top_level_defs": n_defs,
        }))
        _walk_scope(tree.body, module=module, rel=rel, prefix="", in_class=False, out=specs)
        _route_specs(tree, module=module, rel=rel, out=specs, notes=notes)
        if module == "app.cli":
            _cli_specs(tree, module=module, rel=rel, out=specs, notes=notes)

    specs.extend(_table_specs(notes, errors))
    specs.extend(_config_specs(notes))
    specs.extend(_persona_specs(notes))

    # ID 去重：同一 id 只留第一条，其余记 duplicate_from（例如重定义 / 两处同路由）
    uniq: dict[str, dict[str, Any]] = {}
    for spec in specs:
        key = spec["id"]
        if key in uniq:
            source = spec["extra"].get("file") or spec.get("module") or "-"
            uniq[key]["extra"].setdefault("duplicate_from", []).append(source)
            continue
        uniq[key] = spec
    specs = list(uniq.values())

    if not errors:
        notes.append("全部文件 ast 解析成功，无 parse_error")
    else:
        bad = ", ".join(e["file"] for e in errors[:8])
        notes.append(f"{len(errors)} 个文件解析失败（{bad}），已按 module 级 parse_error 记录，未中断本轮")

    meta: dict[str, Any] = {
        "files": len(files),
        "seconds": round(time.perf_counter() - t0, 3),
        "parse_errors": errors,
        "notes": notes,
        "root": str(ROOT),
        "db_file": _rel(DB_FILE),
        "db_exists": DB_FILE.exists(),
        "persona_file": _rel(PERSONA_FILE),
    }
    return specs, meta


def _load() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """带指纹缓存的扫描结果（specs 是 dict，build() 每次materialize 新 Item）。"""
    fp = _fingerprint()
    if _CACHE.get("fp") == fp and _CACHE.get("specs") is not None:
        return _CACHE["specs"], _CACHE["meta"]
    specs, meta = _scan()
    _CACHE.update({"fp": fp, "specs": specs, "meta": meta})
    return specs, meta


def _materialize(spec: dict[str, Any]) -> types.Item:
    return types.Item(
        id=spec["id"],
        kind=spec["kind"],
        name=spec["name"],
        module=spec.get("module") or "",
        line=int(spec.get("line") or 0),
        risk=spec.get("risk") or "read",
        plan=spec.get("plan") or "L0",
        detail="",
        # extra 深拷贝：build() 可以被调用多次，不能让调用方改到缓存里的 extra
        extra=copy.deepcopy(spec.get("extra") or {}),
    )


def build() -> list[types.Item]:
    """全量清单。每个模块一条 module Item + 其下所有可调用对象/路由/CLI/表/配置/人格键。"""
    specs, _meta = _load()
    return [_materialize(spec) for spec in specs]


def invalidate() -> None:
    """丢掉缓存（测试里改文件后用；正常流程靠 mtime 指纹自动失效）。"""
    _CACHE.update({"fp": None, "specs": None, "meta": None})


def probe_targets() -> list[dict[str, Any]]:
    """要动态调用的对象清单：模块级函数 + 类方法（含私有，标 private=True）。

    不包含：
    - 嵌套函数（`outer.<locals>.inner`）：无法按 qualname 取到对象，只做静态条目；
    - property / setter：`Class.prop` 拿到的不是函数，单独调用只会误导参数合成；
    - class / route / cli / table / config / persona：各自有专门的 phase。
    字段（契约 §2 + 便于 probe 直接落库的补充）：
        module / qualname / is_async / sig / risk / line / private
        id（与 inventory 的 Item id 完全一致，probe 注册时直接用）
        kind / decorators
    """
    specs, _meta = _load()
    out: list[dict[str, Any]] = []
    for spec in specs:
        if spec["kind"] not in ("fn", "method"):
            continue
        extra = spec.get("extra") or {}
        if extra.get("nested") or extra.get("is_property") or extra.get("is_setter"):
            continue
        out.append({
            "id": spec["id"],
            "kind": spec["kind"],
            "module": spec["module"],
            "qualname": spec["name"],
            "is_async": bool(extra.get("is_async")),
            "sig": str(extra.get("sig") or ""),
            "risk": spec.get("risk") or "read",
            "line": int(spec.get("line") or 0),
            "private": bool(extra.get("private")),
            "decorators": list(extra.get("decorators") or []),
        })
    return out


def manifest() -> dict[str, Any]:
    """统计：总数、按 kind、按 module、按 risk、路由数、表数、配置键数、扫描耗时。"""
    specs, meta = _load()
    by_kind: dict[str, int] = {}
    by_module: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for spec in specs:
        by_kind[spec["kind"]] = by_kind.get(spec["kind"], 0) + 1
        module = spec.get("module") or "-"
        by_module[module] = by_module.get(module, 0) + 1
        risk = spec.get("risk") or "read"
        by_risk[risk] = by_risk.get(risk, 0) + 1

    probe_count = sum(
        1 for s in specs
        if s["kind"] in ("fn", "method")
        and not any((s.get("extra") or {}).get(key) for key in ("nested", "is_property", "is_setter"))
    )
    property_count = sum(1 for s in specs if (s.get("extra") or {}).get("is_property"))
    nested_count = sum(1 for s in specs if (s.get("extra") or {}).get("nested"))

    counts = {"total": len(specs), **{k: by_kind.get(k, 0) for k in sorted(by_kind)}}
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": counts,
        "by_kind": dict(sorted(by_kind.items())),
        "by_module": dict(sorted(by_module.items())),
        "by_risk": dict(sorted(by_risk.items())),
        "totals": {
            "items": len(specs),
            "routes": by_kind.get("route", 0),
            "tables": by_kind.get("table", 0),
            "cli": by_kind.get("cli", 0),
            "config": by_kind.get("config", 0),
            "persona": by_kind.get("persona", 0),
            "fn": by_kind.get("fn", 0),
            "method": by_kind.get("method", 0),
            "class": by_kind.get("class", 0),
            "module": by_kind.get("module", 0),
            "probe_targets": probe_count,
            "properties": property_count,
            "nested": nested_count,
        },
        "scan": meta,
    }


if __name__ == "__main__":  # pragma: no cover - 手工排查用
    import json

    items = build()
    print(f"items={len(items)}")
    print(json.dumps(manifest(), ensure_ascii=False, indent=2))
