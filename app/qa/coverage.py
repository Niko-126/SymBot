"""coverage —— 运行期覆盖率：哪些 `app/` 下的函数真的被执行过。

## 为什么不用 trace / coverage.py

`sys.settrace` 会给每一行都回调一次，跑一轮全量 probe 会把耗时放大 3~10 倍；
`coverage.py` 是第三方依赖且会重写代码对象。Python 3.12+ 的内核级 `sys.monitoring`
只在函数**进入**时回调一次 `PY_START`，代价小、不打补丁，3.14 上一定可用。

安装顺序：
1. `sys.monitoring`：依次尝试 tool id `3,4,1,2,0,5`（`get_tool` 为空才占用），
   注册 `PY_START` 回调拿 code object。全局生效，**所有线程**都会计数。
2. 失败（tool id 被别的工具占满 / 老解释器）→ 退回 `sys.setprofile`：
   只对当前线程 + 之后 `threading.setprofile` 新建的线程生效，仍在 `start()` 里装好。
   若装之前已有别人的 profiler，会**链式**转调（不吞别人的回调）。

## 统计口径

- 只统计 `code.co_filename` 落在仓库 `app/` 下的 code object；`vendor/`、site-packages、
  临时脚本一律不算——否则标准库噪声会把比例冲成"100% 覆盖"。
- key = `module:qualname`：
  - module 由文件路径换算（`app/memory/h2.py` → `app.memory.h2`，`__init__.py` → 包名）；
  - qualname 用 `code.co_qualname`（方法自带类名，嵌套函数自带 `<locals>`）。
  这个 key 与 `inventory` 的 `FN:/METH:` Item id 去掉前缀后完全一致，
  所以 `report()` 可以直接用 `item.id` 反查，不需要另建映射表。
- `Tracer.hits` 只累加不重置；同一进程可多次 `with Tracer()`（实例内 enter/exit 可嵌套），
  `stop()` 幂等。计数走模块级锁，多线程安全。
- `report(reg)` 默认读模块级聚合 `_GLOBAL_HITS`（所有 Tracer 都会往里写，
  这样"跑完 probe 再 report"不用把 Tracer 对象传一路）；
  想只看某一个 Tracer 就 `report(reg, tracer=t)`。

## total 口径

来自 `registry` 里 `kind in ("fn", "method")` 且 `status != SKIP` 的 Item：
SKIP 是"按规则不该跑"（缺参/危险/外部依赖），不该拉低覆盖率；
NOTRUN / RAISED / HANG 算"没覆盖"——报告里"列了没跑"必须显性暴露。
﻿​​﻿﻿​‌﻿﻿‍‌‌‍﻿​﻿﻿‍﻿​﻿‍‌‌​﻿‌﻿‌﻿​‌﻿﻿"""

from __future__ import annotations

import os
import pathlib
import sys
import threading
from typing import Any

from app.qa import types

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APP_DIR = _ROOT / "app"
_TOOL_IDS = (3, 4, 1, 2, 0, 5)   # monitoring 的 6 个 id，先用没人占的 3/4
_MISS = object()

#: 所有 Tracer 共享的累计计数（report() 默认读它）
_GLOBAL_HITS: dict[str, int] = {}
_COUNT_LOCK = threading.Lock()
_INSTALL_LOCK = threading.Lock()


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except (OSError, ValueError):  # pragma: no cover
        return ""


class Tracer:
    """上下文管理器：`with Tracer() as t: ... ; t.hits`。"""

    def __init__(self, *, root: str | os.PathLike[str] | None = None) -> None:
        base = pathlib.Path(root) if root is not None else _ROOT
        self.root = base
        self.app_dir = base / "app"
        self.hits: dict[str, int] = {}
        self._app_norm = _norm(str(self.app_dir))
        self._file_cache: dict[str, str | None] = {}
        self._depth = 0
        self._mode = "idle"          # idle | monitoring | profile
        self._tid: int | None = None
        self._cb: Any = None
        self._prev_profile: Any = None
        self._prev_thread_profile: Any = None
        self._errors: list[str] = []

    # ------------------------------------------------------------- 上下文协议
    def __enter__(self) -> "Tracer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False    # 绝不吞异常

    # ------------------------------------------------------------------ 状态
    @property
    def mode(self) -> str:
        """`monitoring` / `profile`——最近一次实际装上的机制；`idle` 表示还没启动过。"""
        return self._mode

    @property
    def errors(self) -> list[str]:
        """安装/卸载过程中被吞掉的异常（用于报告"为什么退回了 setprofile"）。"""
        return list(self._errors)

    # ------------------------------------------------------------------ 启停
    def start(self) -> "Tracer":
        """幂等；实例内可嵌套（depth 计数，最外层才真正装）。"""
        with _INSTALL_LOCK:
            if self._depth:
                self._depth += 1
                return self
            self._depth = 1
            if not self._install_monitoring():
                self._install_profile()
            return self

    def stop(self) -> None:
        """幂等：不在运行时调用什么也不做。"""
        with _INSTALL_LOCK:
            if self._depth == 0:
                return
            self._depth -= 1
            if self._depth > 0:
                return
            if self._mode == "monitoring":
                self._detach_monitoring()
            elif self._mode == "profile":
                self._detach_profile()
            # 注意：不把 _mode 重置成 idle——报告要能说清"这轮实际用的是 monitoring 还是 profile"

    @property
    def running(self) -> bool:
        return self._depth > 0

    # ----------------------------------------------------------- monitoring
    def _install_monitoring(self) -> bool:
        mon = getattr(sys, "monitoring", None)
        if mon is None:
            return False
        events = getattr(mon, "events", None)
        if events is None or not hasattr(events, "PY_START"):
            return False
        for tid in _TOOL_IDS:
            try:
                if mon.get_tool(tid) is not None:
                    continue
                mon.use_tool_id(tid, "smd-qa-coverage")
            except (ValueError, PermissionError, AttributeError) as exc:
                self._errors.append(f"use_tool_id({tid}): {type(exc).__name__}: {exc}")
                continue
            try:
                self._cb = self._on_start
                mon.register_callback(tid, events.PY_START, self._cb)
                mon.set_events(tid, events.PY_START)
            except Exception as exc:  # noqa: BLE001 - 任何失败都要能退回 setprofile
                self._errors.append(f"register_callback({tid}): {type(exc).__name__}: {exc}")
                self._free_tool(mon, tid)
                continue
            self._tid = tid
            self._mode = "monitoring"
            return True
        return False

    def _free_tool(self, mon: Any, tid: int) -> None:
        try:
            mon.set_events(tid, 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            mon.register_callback(tid, mon.events.PY_START, None)
        except Exception:  # noqa: BLE001
            pass
        try:
            mon.free_tool_id(tid)
        except Exception:  # noqa: BLE001
            pass

    def _detach_monitoring(self) -> None:
        mon = getattr(sys, "monitoring", None)
        if mon is not None and self._tid is not None:
            self._free_tool(mon, self._tid)
        self._tid = None
        self._cb = None

    # -------------------------------------------------------------- profile
    def _install_profile(self) -> None:
        self._prev_profile = sys.getprofile()
        try:
            self._prev_thread_profile = threading.getprofile()
        except Exception:  # noqa: BLE001 - 3.9 之前的 py 没有该函数
            self._prev_thread_profile = None
        prev = self._prev_profile
        tracer = self

        def profiler(frame, event, arg):
            if event == "call":
                tracer._on_frame(frame)
            if prev is not None:
                try:
                    prev(frame, event, arg)
                except Exception:  # noqa: BLE001 - 别人装坏的回调不该拖死我们
                    pass

        try:
            sys.setprofile(profiler)
        except Exception as exc:  # noqa: BLE001
            self._errors.append(f"setprofile: {type(exc).__name__}: {exc}")
            self._mode = "idle"
            return
        try:
            threading.setprofile(profiler)   # 之后新建的线程也计数
        except Exception as exc:  # noqa: BLE001
            self._errors.append(f"threading.setprofile: {type(exc).__name__}: {exc}")
        self._profile_fn = profiler
        self._mode = "profile"

    def _detach_profile(self) -> None:
        fn = getattr(self, "_profile_fn", None)
        try:
            if sys.getprofile() is fn:
                sys.setprofile(self._prev_profile)
        except Exception:  # noqa: BLE001
            pass
        try:
            if threading.getprofile() is fn:
                threading.setprofile(self._prev_thread_profile)
        except Exception:  # noqa: BLE001
            pass
        self._profile_fn = None

    # -------------------------------------------------------------- 命中判定
    def _on_start(self, code, offset) -> None:  # noqa: ARG002 - 回调签名固定
        key = self._key_for(code)
        if key is not None:
            self._count(key)
        return None

    def _on_frame(self, frame) -> None:
        key = self._key_for(frame.f_code)
        if key is not None:
            self._count(key)

    def _count(self, key: str) -> None:
        with _COUNT_LOCK:
            self.hits[key] = self.hits.get(key, 0) + 1
            _GLOBAL_HITS[key] = _GLOBAL_HITS.get(key, 0) + 1

    def _key_for(self, code) -> str | None:
        filename = code.co_filename
        module = self._file_cache.get(filename, _MISS)
        if module is _MISS:
            module = self._module_of(filename)
            self._file_cache[filename] = module
        if not module:
            return None
        qualname = getattr(code, "co_qualname", None) or code.co_name
        return f"{module}:{qualname}"

    def _module_of(self, filename: str) -> str | None:
        norm = _norm(filename)
        if not norm or not self._app_norm:
            return None
        prefix = self._app_norm + os.sep
        if not norm.startswith(prefix):
            return None
        try:
            rel = os.path.relpath(os.path.abspath(filename), str(self.app_dir))
        except (OSError, ValueError):  # pragma: no cover - 跨盘符
            return None
        parts = rel.replace("\\", "/").split("/")
        if not parts or not parts[-1].endswith(".py"):
            return None
        stem = parts[-1][:-3]
        if stem == "__init__":
            mod_parts = ["app", *parts[:-1]]
        else:
            mod_parts = ["app", *parts[:-1], stem]
        if len(mod_parts) == 1:      # app/__init__.py
            return "app"
        return ".".join(mod_parts)


# ------------------------------------------------------------------------ 命中集


def snapshot() -> dict[str, int]:
    """当前进程累计命中的只读快照。"""
    with _COUNT_LOCK:
        return dict(_GLOBAL_HITS)


def clear() -> None:
    """清空模块级聚合（phase 之间要重新算覆盖时用；不动已存在的 Tracer.hits）。"""
    with _COUNT_LOCK:
        _GLOBAL_HITS.clear()


# ----------------------------------------------------------------------- 报告


def _registry_items(reg: Any) -> list[types.Item]:
    items = getattr(reg, "items", None)
    if isinstance(items, dict):
        return list(items.values())
    if items:
        try:
            return list(items)
        except TypeError:  # pragma: no cover
            pass
    if hasattr(reg, "by_kind"):  # 兼容只暴露查询方法的 Registry 实现
        return list(reg.by_kind("fn")) + list(reg.by_kind("method"))
    return []


def _item_key(item: types.Item) -> str:
    """`FN:app.memory.h2:add` → `app.memory.h2:add`；不一致时退回 module:name。"""
    item_id = str(item.id or "")
    if ":" in item_id:
        rest = item_id.split(":", 1)[1]
        if ":" in rest:
            return rest
    return f"{item.module}:{item.name}"


def report(reg: Any, tracer: Tracer | None = None) -> dict[str, Any]:
    """覆盖率报告：covered/total/ratio/uncovered + 按模块拆分（给 report.py 直接用）。

    额外字段（不属于硬契约，但报告需要）：
        covered_ids / by_module / hit_total / unregistered_hits / skipped / mode
    """
    hits = dict(tracer.hits) if tracer is not None else snapshot()
    mode = tracer.mode if tracer is not None else "global"

    all_items = [i for i in _registry_items(reg) if i.kind in ("fn", "method")]
    skipped = sum(1 for i in all_items if i.status == types.SKIP)
    items = [i for i in all_items if i.status != types.SKIP]

    covered = 0
    covered_ids: list[str] = []
    uncovered: list[str] = []
    by_module: dict[str, dict[str, int]] = {}
    registered: set[str] = set()

    for item in items:
        key = _item_key(item)
        registered.add(key)
        bucket = by_module.setdefault(item.module or "-", {"total": 0, "covered": 0})
        bucket["total"] += 1
        if hits.get(key, 0) > 0:
            covered += 1
            covered_ids.append(item.id)
            bucket["covered"] += 1
        else:
            uncovered.append(item.id)

    total = len(items)
    extra_hits = sorted(key for key in hits if key not in registered)
    return {
        "covered": covered,
        "total": total,
        "ratio": round(covered / total, 4) if total else 0.0,
        "uncovered": sorted(uncovered),
        "covered_ids": sorted(covered_ids),
        "by_module": {k: by_module[k] for k in sorted(by_module)},
        "skipped": skipped,
        "hits": len(hits),
        "hit_total": int(sum(hits.values())),
        "n_unregistered": len(extra_hits),
        "unregistered_hits": extra_hits[:200],
        "mode": mode,
    }


def selfcheck() -> dict[str, Any]:
    """最小自检：装一次、跑一段自身代码、卸载，确认 hits 非空且 key 形态正确。"""
    tracer = Tracer()
    with tracer:
        tracer._module_of(__file__)   # 触发一次本模块方法调用，确保命中 app.qa.coverage
    sample = [k for k in tracer.hits if k.startswith("app.qa.coverage:")]
    return {
        "mode": tracer.mode,
        "hits": len(tracer.hits),
        "hit_total": int(sum(tracer.hits.values())),
        "has_self": bool(sample),
        "sample": sample[:5],
        "errors": tracer.errors,
    }


if __name__ == "__main__":  # pragma: no cover - 手工排查用
    import json

    print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))
