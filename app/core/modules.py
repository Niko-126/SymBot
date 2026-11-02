"""外置模块热插拔：把 GitHub 上下的模块丢进 `modules/<id>/` 就生效。

设计口径（细节见 docs/32-模块热插拔.md）：

1. **零改码**：只认 `modules/*/manifest.json`，不往 `app/` 里加任何模块名。
2. **放错不崩**：manifest 校验、import、`register(api)` 全部逐个隔离，
   一个坏模块只把自己标成 `failed`，内核照常启动。
3. **热生效**：`reload()` 会重新扫目录 + 重新 import（连同子模块一起从 sys.modules
   摘掉），工具/路由/钩子当场换新，不用重启内核。
4. **开关走 kv**：`module_enabled:<id>`，空 = 默认启用；停用不删文件。
5. **模块代码 = 进程权限**：因此只做校验，不替用户装依赖、不自动联网。

main.py 只需要两行（放在 include_router 那几行之后、面板 Mount 之前）：

    from app.core import modules as modules_mod
    modules_mod.attach(app)

本文件也可以单独跑：`python -m app.core.modules --init` 建目录 + 写 README。
﻿﻿﻿‌‍﻿‌‍‍​﻿‍﻿‍​‍‌﻿﻿‍﻿﻿‍‌​‌﻿﻿‍‍‌‍﻿﻿"""

from __future__ import annotations

import importlib.util as _importlib_util
import json
import logging
import pathlib
import re
import sys
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core import db
from app.core.paths import RESOURCE_ROOT

log = logging.getLogger("symemory.modules")

# 模块根目录：跟着代码走（打包态在 RESOURCE_ROOT，开发态就是仓库根）
MODULES_DIR: pathlib.Path = RESOURCE_ROOT / "modules"

# 动态 import 用的命名空间包；每个模块是它的子模块 `symbot_modules.<id>`
_PKG = "symbot_modules"

_KINDS = ("tool", "route", "hook", "mixed")
_REQUIRED = ("id", "name", "version", "entry", "kind", "requires")

# kebab-case：小写字母/数字，用单个连字符连接
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# pip 包名（允许 extras：[fast]），只挡明显的垃圾，不做完整 PEP 508 解析
_PIP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,\s-]+\])?$")

README_TEXT = """# 外置模块（modules/）

> 从 GitHub 下载 → 解压到 `modules/<id>/` → 面板点「重新加载」或重启内核。**不用改任何 Python 代码。**

内核不认识你的模块：看到 `modules/<id>/manifest.json` 就按声明加载。
放错了也不会崩，面板「模块」页会把哪里不对写清楚。

## 1. 三步接入

1. **下载**：拿到模块压缩包（GitHub 的 Code → Download ZIP，或 `git clone`）。
2. **解压**：放到仓库的 `modules/<id>/`，`<id>` 必须和 `manifest.json` 里的 `id` 一模一样
   （例：`modules/hello/`）。多套一层文件夹是最常见的放错。
3. **生效**：面板 →「模块」→ 点「重新加载」；没有面板就重启内核（`python -m app.cli serve`）。

目录长这样：

```
modules/
├── README.md          ← 本文件（内核第一次启动会自动生成）
├── hello/             ← 示例模块，可以照着抄
│   ├── manifest.json
│   └── hello.py
└── 你的模块/
    ├── manifest.json
    ├── main.py
    └── 其它文件（模板 / 数据 / 子脚本都放这一层）
```

## 2. manifest.json 字段

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | ✅ | string | 模块 id，kebab-case（小写字母、数字、连字符），**必须等于目录名** |
| `name` | ✅ | string | 显示名，面板上给人看的 |
| `version` | ✅ | string | 版本号，建议 `1.0.0` |
| `entry` | ✅ | string | 入口 `.py` 的相对路径（相对模块目录，不许用 `..` 跳出去） |
| `kind` | ✅ | string | `tool` / `route` / `hook` / `mixed` |
| `requires` | ✅ | string[] | pip 包名列表；**只校验、不自动装**，没有依赖就写 `[]` |

额外字段（`author`、`description`、`homepage` …）随便写，内核不管，但别人看得懂。

## 3. 写一个模块：register(api)

入口文件必须暴露 `register(api)`，其它什么都不用管：

```python
def hello_ping() -> dict:
    return {"ok": True, "pong": 1700000000.0}


def register(api):
    api.register_tool("hello_ping", hello_ping, desc="示例工具：回一个 pong")
```

`api` 就三个方法：

| 方法 | 作用 |
|---|---|
| `api.register_tool(name, fn, desc="")` | 注册工具；`fn(**args)` 返回 dict，名字全局唯一（先到先得） |
| `api.register_router(router)` | 挂一个 `fastapi.APIRouter`，reload 时自动挂到内核上 |
| `api.register_hook(event, fn)` | 注册钩子；`fn(payload: dict)`，事件名自定，`registry().emit(event, payload)` 触发 |

模块里还能用 `api.dir`（自己目录的 `pathlib.Path`）和 `api.log`（logger）。
需要的依赖写进 `requires`，自己 `pip install`；缺包时模块不加载，面板会提示缺哪个。

## 4. 安全边界（装之前先读）

- 模块代码跑在**内核进程内**，权限和内核一样：能读文件、发网络请求、写数据库。
- 只装你看得懂、信得过的模块。**来路不明的压缩包等于把整台机器交出去。**
- 模块只能通过 `register(api)` 挂东西，改不了内核；停用后它的工具 / 路由 / 钩子立刻下架。
- 内核自动生成的只有本文件和 `modules/` 目录本身；卸载 = 停用 + 删目录。

## 5. 出问题看哪里

- 面板「模块」页：每个模块都带 `error`（人话）、`loaded`、`tools`、`routes`。
- 命令行：`python -m app.core.modules --list` 打印完整清单（JSON）。
- 设计与排查：`docs/32-模块热插拔.md`。

---

本文件在内核第一次启动、或跑 `python -m app.core.modules --init` 时自动生成。
"""


# ---------------------------------------------------------------- 数据结构


@dataclass
class ModuleInfo:
    """一个模块的发现/加载结果。`to_dict()` 就是面板看到的那份 JSON。"""

    id: str
    path: pathlib.Path = field(default_factory=lambda: pathlib.Path("."))
    name: str = ""
    version: str = ""
    entry: str = ""
    kind: str = ""
    requires: list[str] = field(default_factory=list)
    source: str = ""
    enabled: bool = True
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    loaded: bool = False
    loaded_at: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    hook_counts: dict[str, int] = field(default_factory=dict)
    # 路由对象不进 JSON，挂载时用
    routers: list[Any] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "version": self.version,
            "entry": self.entry,
            "kind": self.kind,
            "requires": list(self.requires),
            "enabled": bool(self.enabled),
            "source": self.source,
            "error": self.error,
            "warnings": list(self.warnings),
            "loaded": bool(self.loaded),
            "loaded_at": self.loaded_at,
            "tools": [dict(t) for t in self.tools],
            "routes": _routes_of(self),
            "hooks": dict(self.hook_counts),
        }


@dataclass
class ToolEntry:
    name: str
    fn: Callable[..., Any]
    desc: str
    module_id: str


# ---------------------------------------------------------------- 目录准备


def ensure_ready() -> pathlib.Path:
    """建 `modules/` 和 README（幂等）。失败只记日志，不抛。"""
    try:
        MODULES_DIR.mkdir(parents=True, exist_ok=True)
        readme = MODULES_DIR / "README.md"
        if not readme.exists():
            readme.write_text(README_TEXT, encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - 目录建不出来不该拖垮启动
        log.warning("模块目录准备失败：%s（%s）", exc, MODULES_DIR)
    return MODULES_DIR


def _rel(p: pathlib.Path) -> str:
    """给人看的路径：仓库内用相对路径（modules/hello），仓库外用绝对路径。"""
    try:
        return p.resolve().relative_to(RESOURCE_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 发现 / 校验


def discover() -> list[ModuleInfo]:
    """扫 `modules/*/manifest.json`，返回全部模块（含坏掉的，坏的那条带 error）。

    只列**目录里有 manifest.json** 的目录——`__pycache__`、半成品空目录不进列表。
    永不抛：单个模块读失败只影响它自己。
    """
    ensure_ready()
    out: list[ModuleInfo] = []
    try:
        folders = sorted((p for p in MODULES_DIR.iterdir() if p.is_dir()),
                         key=lambda p: p.name)
    except OSError as exc:
        log.warning("模块目录读不了：%s（%s）", exc, MODULES_DIR)
        return []
    for folder in folders:
        if folder.name.startswith((".", "_")):
            continue  # .git / __pycache__ / _templates 之类
        if not (folder / "manifest.json").is_file():
            continue
        try:
            out.append(_read_manifest(folder))
        except Exception as exc:  # noqa: BLE001 - 单个模块读崩了不许带走别人
            log.warning("模块 %s 读取异常：%s", folder.name, exc)
            broken = ModuleInfo(id=folder.name, path=folder, source=_rel(folder))
            broken.enabled = is_enabled(folder.name)
            broken.error = _human_error(exc)
            out.append(broken)
    return out


def _read_manifest(folder: pathlib.Path) -> ModuleInfo:
    info = ModuleInfo(id=folder.name, path=folder, source=_rel(folder))
    info.enabled = is_enabled(folder.name)
    mf = folder / "manifest.json"

    try:
        raw = json.loads(mf.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        info.error = f"manifest.json 读不了：{exc}"
        return info
    except json.JSONDecodeError as exc:
        info.error = f"manifest.json 第 {exc.lineno} 行不是合法 JSON：{exc.msg}"
        return info

    if not isinstance(raw, dict):
        info.error = "manifest.json 顶层必须是对象（{ ... }）"
        return info

    errors: list[str] = []
    for key in _REQUIRED:
        if key not in raw:
            errors.append(f"缺字段：{key}")

    # ---- id：必填 + kebab-case + 与目录名一致
    mid = raw.get("id")
    if "id" in raw:
        if not isinstance(mid, str) or not mid.strip():
            errors.append("id 必须是非空字符串")
        else:
            mid = mid.strip()
            if not _ID_RE.match(mid):
                errors.append(f"id 不合法：`{mid}`（只能小写字母、数字和连字符，例：hello-world）")
            elif mid != folder.name:
                errors.append(f"id `{mid}` 与目录名 `{folder.name}` 不一致（目录名必须是模块 id）")

    # ---- name / version：必填非空字符串
    name = raw.get("name")
    if "name" in raw:
        if not isinstance(name, str) or not name.strip():
            errors.append("name 必须是非空字符串")
        else:
            info.name = name.strip()

    version = raw.get("version")
    if "version" in raw:
        if not isinstance(version, str) or not version.strip():
            errors.append("version 必须是非空字符串（例：1.0.0）")
        else:
            info.version = version.strip()

    # ---- kind
    kind = raw.get("kind")
    if "kind" in raw:
        if not isinstance(kind, str) or kind.strip() not in _KINDS:
            errors.append(f"kind 只能是 {' / '.join(_KINDS)}，现在是 `{kind}`")
        else:
            info.kind = kind.strip()

    # ---- entry：相对路径 + 不许穿越 + 必须是存在的 .py
    entry = raw.get("entry")
    if "entry" in raw:
        if not isinstance(entry, str) or not entry.strip():
            errors.append("entry 必须是字符串（入口 .py 的相对路径）")
        else:
            entry = entry.strip().replace("\\", "/")
            info.entry = entry
            errors.extend(_check_entry(folder, entry))

    # ---- requires：字符串列表 + 名字合法 + 依赖是否装了（只校验，不装）
    requires = raw.get("requires")
    if "requires" in raw:
        if not isinstance(requires, list):
            errors.append("requires 必须是列表（没有依赖就写 []）")
        else:
            cleaned: list[str] = []
            for idx, item in enumerate(requires, start=1):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"requires 第 {idx} 项必须是包名字符串")
                    continue
                item = item.strip()
                if not _PIP_NAME_RE.match(item):
                    errors.append(f"requires 里的 `{item}` 不是合法包名")
                    continue
                cleaned.append(item)
            info.requires = cleaned
            missing = [pkg for pkg in cleaned if not _has_pkg(pkg)]
            if missing:
                errors.append(
                    "缺依赖：" + "、".join(missing)
                    + "（pip install " + " ".join(missing) + "；内核不自动装）"
                )

    if errors:
        info.error = "；".join(errors[:4]) + ("；…" if len(errors) > 4 else "")
    return info


def _check_entry(folder: pathlib.Path, entry: str) -> list[str]:
    """entry 的三道关：相对路径、不出目录、是存在的 .py 文件。"""
    p = pathlib.PurePosixPath(entry)
    if p.is_absolute() or re.match(r"^[A-Za-z]:", entry):
        return [f"entry 必须是相对路径：`{entry}`"]
    if ".." in p.parts:
        # 明确报穿越，比"解析后不在目录内"更好懂
        return [f"entry 不许往上跳：`{entry}`（只能指向模块目录里的文件）"]
    target = (folder / entry).resolve()
    try:
        target.relative_to(folder.resolve())
    except ValueError:
        return [f"entry 跑到模块目录外面了：`{entry}`"]
    if target.suffix.lower() != ".py":
        return [f"entry 必须是 .py 文件：`{entry}`"]
    if not target.is_file():
        return [f"entry 文件不存在：`{entry}`"]
    return []


def _has_pkg(name: str) -> bool:
    """装没装。先按发行名查 metadata，再按 import 名查 find_spec（PyYAML ↔ yaml 这种坑）。"""
    base = name.split("[", 1)[0].strip()
    try:
        import importlib.metadata as md

        md.distribution(base)
        return True
    except Exception:  # noqa: BLE001 - PackageNotFoundError 等
        pass
    for cand in {base, base.replace("-", "_"), base.replace(".", "_")}:
        try:
            if _importlib_util.find_spec(cand) is not None:
                return True
        except (ImportError, AttributeError, ValueError, ModuleNotFoundError):
            continue
    return False


def _human_error(exc: BaseException) -> str:
    """把异常翻成人话，面板直接显示。"""
    if isinstance(exc, ModuleNotFoundError):
        missing = str(getattr(exc, "name", "") or "").split(".")[0]
        if missing:
            return f"缺 Python 包：{missing}（pip install {missing}）"
        return "模块 import 失败：缺依赖"
    if isinstance(exc, SyntaxError):
        where = f"{exc.filename}:{exc.lineno}" if exc.filename else ""
        return f"语法错误 {where}：{exc.msg}"
    if isinstance(exc, ValueError):
        return str(exc)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}"[:300] if text else type(exc).__name__


# ---------------------------------------------------------------- 启用状态（kv）


def is_enabled(module_id: str) -> bool:
    """空 = 默认启用；"0"/false/off/no = 停用。库没就绪时按启用处理。"""
    mid = str(module_id or "").strip()
    if not mid:
        return False
    try:
        raw = db.kv_get(f"module_enabled:{mid}", "")
    except Exception:  # noqa: BLE001 - db 还没 init
        return True
    return str(raw).strip().lower() not in ("0", "false", "off", "no")


def set_enabled(module_id: str, enabled: bool) -> bool:
    """写 kv；空值才是"默认启用"，所以这里永远显式写 "1"/"0"。"""
    mid = str(module_id or "").strip()
    if not mid:
        raise ValueError("模块 id 不能为空")
    db.kv_set(f"module_enabled:{mid}", "1" if enabled else "0")
    return bool(enabled)


# ---------------------------------------------------------------- 注册表


class Registry:
    """已加载模块的工具 / 路由 / 钩子。全局只有一份（见 `registry()`）。"""

    def __init__(self) -> None:
        self.modules: dict[str, ModuleInfo] = {}
        self.tools: dict[str, ToolEntry] = {}
        self.hooks: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
        self.generation = 0
        self.loaded_at = ""
        self._mounted: list[Any] = []

    # ---- 注册（模块通过 ModuleAPI 调，不直接调这里）
    def add_tool(self, module_id: str, name: str, fn: Callable[..., Any], desc: str) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("register_tool: 工具名不能为空")
        if not callable(fn):
            raise ValueError(f"register_tool({key!r}): fn 必须可调用")
        if key in self.tools:
            prev = self.tools[key]
            raise ValueError(
                f"工具 `{key}` 已被模块 `{prev.module_id}` 注册（先到先得），本模块加载中止"
            )
        self.tools[key] = ToolEntry(name=key, fn=fn, desc=str(desc or ""), module_id=module_id)

    def add_hook(self, module_id: str, event: str, fn: Callable[..., Any]) -> int:
        key = str(event).strip()
        if not key:
            raise ValueError("register_hook: 事件名不能为空")
        if not callable(fn):
            raise ValueError(f"register_hook({key!r}): fn 必须可调用")
        self.hooks.setdefault(key, []).append((module_id, fn))
        return len(self.hooks[key])

    # ---- 查
    def tool_names(self) -> list[str]:
        return sorted(self.tools)

    def modules_payload(self) -> list[dict[str, Any]]:
        return [info.to_dict() for info in sorted(self.modules.values(), key=lambda i: i.id)]

    def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """跑一个模块工具。永不抛：失败 `{"ok": False, "reason": …}`，和 app.tools 同口径。"""
        entry = self.tools.get(str(name or ""))
        if entry is None:
            return {"ok": False, "name": str(name), "reason": "unknown-tool"}
        try:
            result = entry.fn(**(args or {}))
        except Exception as exc:  # noqa: BLE001 - 模块工具失败不许拖垮主链
            return {
                "ok": False,
                "name": entry.name,
                "module": entry.module_id,
                "reason": _human_error(exc),
            }
        if isinstance(result, dict):
            return result
        return {"ok": True, "name": entry.name, "module": entry.module_id, "result": result}

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """触发钩子。单个钩子炸了只记结果，不影响别的钩子和调用方。"""
        out: list[dict[str, Any]] = []
        for module_id, fn in list(self.hooks.get(str(event or ""), [])):
            try:
                out.append({"module": module_id, "ok": True, "result": fn(payload or {})})
            except Exception as exc:  # noqa: BLE001
                out.append({"module": module_id, "ok": False, "error": _human_error(exc)})
        return out

    # ---- 路由挂载 / 摘除
    def mount(self, app: Any) -> dict[str, Any]:
        """把模块路由挂到 app 上（幂等：先摘上一轮，再挂这一轮）。

        面板是 `app.mount("/", StaticFiles(...))`，会吞掉它后面的一切路由，
        所以新路由必须插到第一个 Mount 之前。
        """
        if app is None:
            return {"ok": False, "reason": "没有 app，跳过路由挂载"}
        routes = getattr(getattr(app, "router", None), "routes", None)
        if not isinstance(routes, list):
            return {"ok": False, "reason": "app.router.routes 不可用，跳过路由挂载"}

        for route in self._mounted:
            try:
                routes.remove(route)
            except ValueError:
                pass
        self._mounted.clear()

        mounted: list[dict[str, Any]] = []
        for info in sorted(self.modules.values(), key=lambda i: i.id):
            if not (info.loaded and info.enabled and not info.error):
                continue
            for router_obj in list(info.routers):
                before = len(routes)
                try:
                    app.include_router(router_obj)
                except Exception as exc:  # noqa: BLE001 - 路由挂不上只影响这个模块
                    info.warnings.append(f"路由挂载失败：{_human_error(exc)}")
                    continue
                fresh = routes[before:]
                if not fresh:
                    continue
                del routes[before:]
                idx = _mount_index(routes)
                routes[idx:idx] = fresh
                self._mounted.extend(fresh)
                for route in _routes_of(info):
                    if route not in mounted:
                        mounted.append(route)
        return {"ok": True, "mounted": mounted}

    def reset(self) -> None:
        """清空工具/路由/钩子。**不清 `_mounted`**：reload 后要先把上一轮挂上去的
        路由从 app 上摘掉，否则旧 handler 会一直活着（停用/改代码都不生效）。"""
        self.modules = {}
        self.tools = {}
        self.hooks = {}


_REGISTRY: Registry | None = None


def registry() -> Registry:
    """全局单例：已加载模块的工具 / 路由 / 钩子都从这里拿。"""
    global _REGISTRY
    if _REGISTRY is None:
        ensure_ready()
        _REGISTRY = Registry()
    return _REGISTRY


def _mount_index(routes: list[Any]) -> int:
    """第一个 Mount 的位置；没有 Mount 就放末尾。"""
    for i, route in enumerate(routes):
        if route.__class__.__name__ == "Mount":
            return i
    return len(routes)


def _routes_of(info: ModuleInfo) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for router_obj in info.routers:
        for route in getattr(router_obj, "routes", []) or []:
            methods = sorted(
                m for m in (getattr(route, "methods", None) or {"GET"})
                if m not in ("HEAD", "OPTIONS")
            )
            out.append({"path": str(getattr(route, "path", "")), "methods": methods})
    return out


# ---------------------------------------------------------------- 模块拿到手的 api


class ModuleAPI:
    """`register(api)` 里的那个 api。只有三个注册方法 + dir/log。"""

    def __init__(self, info: ModuleInfo, reg: Registry) -> None:
        self.module_id = info.id
        self.dir = info.path
        self.log = logging.getLogger(f"symemory.modules.{info.id}")
        self._info = info
        self._reg = reg

    def register_tool(self, name: str, fn: Callable[..., Any], desc: str = "") -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("register_tool: 工具名必须是非空字符串")
        if not callable(fn):
            raise ValueError(f"register_tool({name!r}): fn 必须是可调用对象")
        self._reg.add_tool(self.module_id, name, fn, desc)
        self._info.tools.append({"name": name.strip(), "desc": str(desc or "")})

    def register_router(self, router: Any) -> None:
        if router is None or not hasattr(router, "routes"):
            raise ValueError("register_router: 需要 fastapi.APIRouter 实例")
        self._info.routers.append(router)

    def register_hook(self, event: str, fn: Callable[..., Any]) -> None:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("register_hook: 事件名必须是非空字符串")
        if not callable(fn):
            raise ValueError(f"register_hook({event!r}): fn 必须是可调用对象")
        key = event.strip()
        self._reg.add_hook(self.module_id, key, fn)
        self._info.hook_counts[key] = self._info.hook_counts.get(key, 0) + 1


# ---------------------------------------------------------------- 加载


def _ensure_parent_package() -> None:
    pkg = sys.modules.get(_PKG)
    if pkg is None:
        pkg = types.ModuleType(_PKG)
        pkg.__doc__ = "外置模块命名空间（app/core/modules.py 生成）"
        sys.modules[_PKG] = pkg
    pkg.__path__ = [str(MODULES_DIR)]


def _forget(module_id: str) -> None:
    """从 sys.modules 摘掉某个模块（连同子模块），reload 才能拿到新代码。"""
    full = f"{_PKG}.{module_id}"
    for key in [k for k in list(sys.modules) if k == full or k.startswith(full + ".")]:
        sys.modules.pop(key, None)


def _unregister(reg: Registry, module_id: str) -> None:
    """模块注册到一半炸了：把它已经挂上去的工具/钩子也撤掉，不留半截。"""
    for name in [n for n, entry in reg.tools.items() if entry.module_id == module_id]:
        reg.tools.pop(name, None)
    for event in list(reg.hooks):
        reg.hooks[event] = [(mid, fn) for mid, fn in reg.hooks[event] if mid != module_id]
        if not reg.hooks[event]:
            del reg.hooks[event]


def _entry_target(info: ModuleInfo) -> pathlib.Path:
    """加载前再核一遍路径（防 registry 被塞了手工构造的 info）。"""
    target = (info.path / info.entry).resolve()
    try:
        target.relative_to(info.path.resolve())
    except ValueError as exc:
        raise ValueError(f"entry 跑到模块目录外面了：`{info.entry}`") from exc
    if not target.is_file():
        raise ValueError(f"entry 文件不存在：`{info.entry}`")
    return target


def _load_one(info: ModuleInfo, reg: Registry) -> None:
    target = _entry_target(info)
    full = f"{_PKG}.{info.id}"
    _ensure_parent_package()
    _forget(info.id)

    spec = _importlib_util.spec_from_file_location(
        full,
        str(target),
        submodule_search_locations=[str(info.path)],
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"entry 加载不了：`{info.entry}`（不是可导入的 Python 文件）")

    module = _importlib_util.module_from_spec(spec)
    module.__symbot_module__ = info.id  # type: ignore[attr-defined]
    sys.modules[full] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # 模块代码抛 SystemExit / KeyboardInterrupt 也不能带走内核
        sys.modules.pop(full, None)
        _forget(info.id)
        raise
    finally:
        import importlib

        importlib.invalidate_caches()

    register = getattr(module, "register", None)
    if not callable(register):
        sys.modules.pop(full, None)
        _forget(info.id)
        raise ValueError("entry 里没有 `def register(api)`，模块不会自己挂东西")

    api = ModuleAPI(info, reg)
    register(api)
    info.loaded = True
    info.loaded_at = _now()


def load_all() -> dict[str, Any]:
    """扫目录 + 逐个加载。永不抛：坏模块只进 `failed`，内核照常跑。"""
    ensure_ready()
    reg = registry()
    reg.reset()
    infos = discover()
    loaded: list[str] = []
    failed: list[dict[str, str]] = []
    disabled: list[str] = []

    for info in infos:
        if not info.enabled:
            disabled.append(info.id)
            continue
        if info.error:
            failed.append({"id": info.id, "error": info.error})
            continue
        try:
            _load_one(info, reg)
            loaded.append(info.id)
        except BaseException as exc:  # noqa: BLE001 - 模块炸了不许拖垮启动
            _forget(info.id)
            _unregister(reg, info.id)
            info.loaded = False
            info.error = _human_error(exc)
            failed.append({"id": info.id, "error": info.error})
            log.warning("模块 %s 加载失败：%s", info.id, info.error)

    reg.modules = {info.id: info for info in infos}
    reg.generation += 1
    reg.loaded_at = _now()
    result = {
        "ok": not failed,
        "total": len(infos),
        "loaded": loaded,
        "failed": failed,
        "disabled": disabled,
        "dir": str(MODULES_DIR),
        "notes": _notes(infos),
        "items": [info.to_dict() for info in sorted(infos, key=lambda i: i.id)],
    }
    if loaded:
        reg.emit("modules.loaded", {"ids": loaded})
    return result


def reload(app: Any = None) -> dict[str, Any]:
    """热重载：重新 discover + import，可选把新路由挂到 app 上。永不抛。"""
    result = load_all()
    try:
        result["mounted"] = registry().mount(app)
    except Exception as exc:  # noqa: BLE001 - 挂路由失败不改加载结果
        result["mounted"] = {"ok": False, "reason": _human_error(exc)}
    return result


def ensure_loaded(app: Any = None) -> dict[str, Any]:
    """还没加载过就加载一次（面板第一次打开时的兜底）。"""
    if registry().generation:
        return {"ok": True, "skipped": True, "generation": registry().generation}
    return reload(app=app)


def attach(app: Any) -> dict[str, Any]:
    """装配入口（main.py 两行里那第二行）：加载全部模块 + 挂路由。永不抛。"""
    return reload(app=app)


def snapshot() -> dict[str, Any]:
    """面板 GET /v1/console/modules 的形状。"""
    ensure_ready()
    reg = registry()
    items = reg.modules_payload()
    return {
        "items": items,
        "total": len(items),
        "dir": str(MODULES_DIR),
        "notes": _notes(list(reg.modules.values())),
        "generation": reg.generation,
        "loaded_at": reg.loaded_at,
        "tools": reg.tool_names(),
    }


def _notes(infos: list[ModuleInfo]) -> list[str]:
    notes = [
        f"模块目录：{MODULES_DIR}（不存在会自动建）",
        "放好模块后点「重新加载」或重启内核；停用状态记在 kv 的 module_enabled:<id>",
        "模块代码运行在内核进程里，拥有完整权限，只装信得过的",
    ]
    bad = [i for i in infos if i.error]
    if bad:
        notes.append(f"{len(bad)} 个模块有问题，看 items[].error："
                     + "、".join(i.id for i in bad[:6]))
    missing = sorted({pkg for i in infos for pkg in i.requires if not _has_pkg(pkg)})
    if missing:
        notes.append("缺依赖：" + "、".join(missing) + "（pip install " + " ".join(missing) + "）")
    return notes


# ---------------------------------------------------------------- 单独跑


def _main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--init" in args:
        ensure_ready()
        print(json.dumps(
            {"ok": True, "dir": str(MODULES_DIR), "readme": str(MODULES_DIR / "README.md")},
            ensure_ascii=False, indent=1,
        ))
        return 0
    if "--list" in args:
        ensure_loaded()
        print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
        return 0
    print("用法：python -m app.core.modules --init   # 建 modules/ 并写 README.md\n"
          "      python -m app.core.modules --list   # 打印模块清单")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "MODULES_DIR",
    "README_TEXT",
    "ModuleAPI",
    "ModuleInfo",
    "Registry",
    "attach",
    "discover",
    "ensure_loaded",
    "ensure_ready",
    "is_enabled",
    "load_all",
    "registry",
    "reload",
    "set_enabled",
    "snapshot",
]
