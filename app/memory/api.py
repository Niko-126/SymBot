"""记忆内核的稳定公开接口（对外只认这里，内核实现随时可以换）。

分两条导入路径，先源码、后容器：

1. **源码路径**：`vendor/symvolve_memory/` 在盘上（开发仓、自测），直接
   `sys.path` 追加 `vendor/` 后 `import symvolve_memory`。
2. **容器路径**：源码不在盘上（分发态），`app/memory/_loader.py` 把
   `app/memory/_vault.bin` 解到临时目录并注入 `sys.path`，再 import。

两条都不通就抛 `MemoryCoreUnavailable`——**绝不静默返回空结果**，
否则调用方会把"内核没装"当成"这个人没有记忆"，那是最坏的一种 bug。

四个动作（与 `app/memory/h2.py` 的旧契约一一对应，字段别乱改）：

| 这里 | h2 旧名 | 语义 |
|---|---|---|
| `remember(text, ...)` | `add(kind, content, ...)` | 写一条记忆，同 (人, 场合, 类, 正文) 重复写 = 加温不新增 |
| `recall(query, ...)` | `retrieve(text, ...)` | 线索召回 + 可解释打分 + 落审计 |
| `stats()` | `stats()` / `engine_stats()` | 引擎计数（只读） |
| `forget(item_id)` | `delete_memory(mid)` | 软删一条（`forgotten=1`，不物理删） |

为什么正文与库路径都从 `app/core/config` 拿而不是写死：
库永远只有一个（`data/life.db`），换库只该改配置。但本模块**不能**硬依赖
`app.core`——演练态只留三个文件，`app.core` 不在场也得能跑，所以配置是
"能读就读，读不到退回仓库默认 `data/life.db`"。
﻿​‍​﻿﻿﻿​‌﻿​﻿‍​​﻿​​​​‍​﻿‌​‌‌‍​‍‍‍﻿﻿"""

from __future__ import annotations

import os
import pathlib
import re
import threading
from typing import Any, Iterable

__all__ = [
    "MemoryCoreUnavailable",
    "INITIAL_HEAT",
    "ensure_core",
    "core_info",
    "remember",
    "recall",
    "stats",
    "forget",
    "forget_user",
    "close",
]

#: 新条目的初始热度（与 h2.INITIAL_HEAT 同一个数：0.90 起，每天 -0.04）
INITIAL_HEAT = 0.90
#: 线索上限（与 h2.extract_cues 默认值对齐）
MAX_CUES = 12
#: 库路径环境变量（演练 / 测试用，压过配置文件）
DB_ENV = "SYM_MEMORY_DB"
#: 只走容器、跳过源码路径（`--verify` 与演练用）
FORCE_ENV = "SYM_MEMORY_FORCE_VAULT"

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TIME_CUE = re.compile(r"(?:\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*[日号])")

_LOCK = threading.RLock()
_CORE: Any | None = None
_CORE_ORIGIN = ""
_ENGINE: dict[str, Any] | None = None


class MemoryCoreUnavailable(RuntimeError):
    """内核两条路径都走不通（源码不在、容器解不开）。

    带上 `cause` 字符串，方便面板/日志直接显示"到底是没打包还是指纹不对"。
    """

    def __init__(self, message: str, *, cause: str = "") -> None:
        super().__init__(message)
        self.cause = cause


# ---------------------------------------------------------------- 导内核 --

def _flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _force_vault() -> bool:
    return _flag(os.environ.get(FORCE_ENV))


def _import_source() -> Any:
    """源码路径。注意用顶层名 `symvolve_memory`，不要用 `vendor.symvolve_memory`：

    引擎内部的绝对导入全写成 `from symvolve_memory.xxx import ...`，而且
    `app/core/memengine.py` 也是按顶层名加载的。走 `vendor.` 会加载出**第二份**
    模块对象，两份 `MemoryItem` 类互不认 `isinstance`，那是排查一周都查不出来的坑。
    """
    vendor = _ROOT / "vendor"
    if vendor.is_dir():
        text = str(vendor)
        if text not in sys_path():
            # 追加到末尾而不是 0：容器已经注入的临时目录要优先，
            # 否则 `SYM_MEMORY_FORCE_VAULT=1` 时又会从源码收一份进来。
            sys_path().append(text)
    try:
        import symvolve_memory  # noqa: PLC0415 - 必须按顺序懒加载
    except Exception as exc:  # noqa: BLE001 - 源码缺失/语法错都归到"路径不通"
        raise MemoryCoreUnavailable(
            f"vendor/symvolve_memory 导入失败：{type(exc).__name__}: {exc}"
        ) from exc
    return symvolve_memory


def sys_path() -> list[str]:
    """`sys.path` 的薄壳，只为让上面的注释读起来不绕。"""
    import sys  # noqa: PLC0415 - 极少数情况下 sys 已被替换（嵌入器），懒导入更稳

    return sys.path


def ensure_core(*, force_vault: bool | None = None) -> Any:
    """返回内核模块对象；两条路都不通时抛 `MemoryCoreUnavailable`。

    `force_vault=True` 或环境变量 `SYM_MEMORY_FORCE_VAULT=1` 时跳过源码路径，
    强制走 `_vault.bin`——产物验证与"删源码演练"必须走这条。
    """
    global _CORE, _CORE_ORIGIN
    with _LOCK:
        if _CORE is not None:
            return _CORE
        force = _force_vault() if force_vault is None else bool(force_vault)
        problems: list[str] = []

        if not force:
            try:
                _CORE = _import_source()
                _CORE_ORIGIN = "vendor"
                return _CORE
            except MemoryCoreUnavailable as exc:
                problems.append(f"源码路径：{exc}")

        try:
            from app.memory import _loader  # noqa: PLC0415 - 只在需要解封时导入
        except Exception as exc:  # noqa: BLE001
            raise MemoryCoreUnavailable(
                "内核不可用：源码路径不通，且解封器 _loader.py 导入失败。",
                cause=" | ".join(problems + [f"_loader：{type(exc).__name__}: {exc}"]),
            ) from exc

        try:
            root = _loader.ensure_core()
        except Exception as exc:  # noqa: BLE001 - 容器缺失/指纹不符/损坏 全在这
            raise MemoryCoreUnavailable(
                "内核不可用：源码路径不通，加密容器也解不开。",
                cause=" | ".join(problems + [f"容器路径：{type(exc).__name__}: {exc}"]),
            ) from exc

        try:
            _CORE = _import_source()
        except MemoryCoreUnavailable as exc:
            problems.append(str(exc))
            raise MemoryCoreUnavailable(
                "内核不可用：解封成功但 import symvolve_memory 仍失败。",
                cause=" | ".join(problems),
            ) from exc
        _CORE_ORIGIN = "vault" if root is not None else "preloaded"
        return _CORE


def core_info() -> dict[str, Any]:
    """内核状态快照（面板诊断用；不含机器指纹原文）。"""
    try:
        core = ensure_core()
    except MemoryCoreUnavailable as exc:
        return {"ok": False, "origin": "", "error": str(exc), "cause": exc.cause}
    file = str(getattr(core, "__file__", "") or "")
    return {
        "ok": True,
        "origin": _CORE_ORIGIN,
        "version": str(getattr(core, "__version__", "")),
        "file": file,
        "temp_dir": file.rsplit(os.sep + "symvolve_memory", 1)[0] if _CORE_ORIGIN == "vault" else "",
        "forced_vault": _force_vault(),
    }


# ---------------------------------------------------------------- 库 --

def _db_path(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    """库路径优先级：显式参数 > `SYM_MEMORY_DB` > 配置 > 仓库默认 `data/life.db`。"""
    if explicit:
        return pathlib.Path(explicit)
    env = os.environ.get(DB_ENV, "").strip()
    if env:
        return pathlib.Path(env)
    try:
        from app.core import config  # noqa: PLC0415 - 演练态没有 app.core

        return pathlib.Path(config.get().db_file)
    except Exception:  # noqa: BLE001 - 缺配置/缺包都不能挡住"用默认库"
        return _ROOT / "data" / "life.db"


def _engine(db_path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """按库路径缓存单例（Store + Lifecycle + Recall）。"""
    global _ENGINE
    core = ensure_core()
    path = _db_path(db_path).resolve()
    with _LOCK:
        if _ENGINE is not None and _ENGINE["path"] == path:
            return _ENGINE
        if _ENGINE is not None:
            try:
                _ENGINE["store"].close()
            except Exception:  # noqa: BLE001 - 关不上不能挡住重开
                pass
        from symvolve_memory.db.repo import Store  # noqa: PLC0415

        store = Store(path)
        life = core.MemoryLifecycle(store)
        rec = core.MemoryRecall(store, lifecycle=life)
        _ENGINE = {"path": path, "store": store, "lifecycle": life, "recall": rec}
        return _ENGINE


def close() -> None:
    """关掉引擎单例（换库、测试收尾、进程退出前都该调）。"""
    global _ENGINE
    with _LOCK:
        if _ENGINE is not None:
            try:
                _ENGINE["store"].close()
            except Exception:  # noqa: BLE001
                pass
        _ENGINE = None


# ---------------------------------------------------------------- 写 ----

def _uid(user_id: int | str) -> str:
    return str(int(user_id or 0))


def _chat(chat_type: str | None) -> str:
    return str(chat_type or "")


def _scope(chat_type: str | None) -> str:
    return _chat(chat_type) or "shared"


def _importance(value: float | int) -> int:
    """旧口径 0..1 → 引擎口径 1..100（与 h2._importance_engine 同一个映射）。"""
    return max(1, min(100, int(round(float(value or 0.5) * 100))))


def _title(text: str) -> str:
    head = re.split(r"[。！？!?\n]", text.strip(), maxsplit=1)[0]
    return head[:24] or text.strip()[:24]


def _cue_kind(cue: str) -> str:
    """线索分型只影响召回理由的展示，判断标准与 h2 保持一致。"""
    if _TIME_CUE.search(cue) or cue.isdigit():
        return "time"
    return "content"


def _find_duplicate(store: Any, uid: str, chat: str, kind: str, content: str) -> int:
    for item in store.list_items(user_id=uid, chat_type=chat, kinds=[kind]):
        if item.text == content:
            return int(item.id or 0)
    return 0


def remember(
    text: str,
    *,
    user_id: int | str = 0,
    chat_type: str = "",
    kind: str = "fact",
    title: str = "",
    importance: float = 0.6,
    pinned: bool = False,
    cues: Iterable[str] | None = None,
    bot_reply: str | None = None,
    meta: dict[str, Any] | None = None,
    db_path: str | pathlib.Path | None = None,
) -> int:
    """写一条记忆，返回条目 id（空正文返回 0，与 h2.add 一致）。

    重复写（同人、同场合、同类、同正文）= 加温 + 抬重要度 + 补线索，不新增行。
    """
    content = (text or "").strip()
    if not content:
        return 0
    core = ensure_core()
    eng = _engine(db_path)
    store, life = eng["store"], eng["lifecycle"]
    uid, ct, sc = _uid(user_id), _chat(chat_type), _scope(chat_type)

    existing = _find_duplicate(store, uid, ct, kind, content)
    if existing:
        item = store.get_item(existing)
        if item is not None:
            item.importance = max(int(item.importance), _importance(importance))
            item.heat = core.clamp_heat(max(float(item.heat), INITIAL_HEAT) + 0.1)
            item.pinned = bool(item.pinned) or bool(pinned)
            store.update_item(item)
            life.mark_seen(existing)
            extra = [str(c) for c in (cues or []) if str(c).strip()]
            if extra:
                store.add_cues(existing, [(c, _cue_kind(c), 1.0) for c in extra])
        return existing

    meta = dict(meta or {})
    meta["kind"] = kind
    event_id = store.append_event(
        core.MemoryEvent(
            user_id=uid,
            chat_type=ct or "private",
            scope=sc,
            raw_text=content,
            meta=dict(meta),
        )
    )
    item = core.MemoryItem(
        user_id=uid,
        chat_type=ct,
        scope=sc,
        kind=kind,
        title=title or _title(content),
        text=content,
        bot_reply=bot_reply,
        importance=_importance(importance),
        heat=1.0 if pinned else INITIAL_HEAT,
        pinned=bool(pinned),
        source_event_ids=[event_id],
        meta=dict(meta),
    )
    mid = store.insert_item(item)
    chosen = [str(c) for c in cues] if cues is not None else list(core.extract_tokens(content, limit=MAX_CUES))
    chosen = [c for c in chosen if c.strip()]
    if chosen:
        store.add_cues(mid, [(c, _cue_kind(c), 1.0) for c in chosen])
    return int(mid)


# ---------------------------------------------------------------- 读 ----

def recall(
    query: str,
    *,
    user_id: int | str = 0,
    chat_type: str = "",
    limit: int = 5,
    log: bool = True,
    bump_heat: bool = True,
    db_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """按线索召回，返回可解释结果（`status` / `confidence` / `items`）。

    `chat_type` 空 = 不筛场合（引擎语义是 ``None``，不是空串分区）。
    """
    eng = _engine(db_path)
    result = eng["recall"].search(
        user_id=_uid(user_id),
        query=str(query or ""),
        chat_type=_chat(chat_type) or None,
        limit=int(limit),
        log=log,
        bump_heat=bump_heat,
    )
    return {
        "user_id": result.user_id,
        "query": result.query,
        "status": result.status,
        "confidence": round(float(result.confidence), 4),
        "cues": list(result.cues),
        "items": [hit.as_dict() for hit in result.hits],
        "item_ids": list(result.item_ids),
        "scanned": int(result.scanned),
        "steps": int(result.steps),
    }


def stats(*, db_path: str | pathlib.Path | None = None) -> dict[str, Any]:
    """引擎计数 + 内核来源。字段名沿用 `app/core/memengine.py:stats()`。"""
    eng = _engine(db_path)
    raw = eng["store"].stats()
    keep = (
        "schema_version", "events", "items", "items_active", "cues",
        "recall_logs", "distinct_users", "db_bytes", "embeddings", "render_cache",
    )
    out = {k: raw.get(k) for k in keep}
    out["db_path"] = str(eng["path"])
    out["core"] = core_info()
    return out


# ---------------------------------------------------------------- 删 ----

def forget(
    item_id: int,
    *,
    reason: str = "manual",
    db_path: str | pathlib.Path | None = None,
) -> bool:
    """软删一条记忆（置 `forgotten=1`，审计与回溯都还在）。"""
    eng = _engine(db_path)
    store, life = eng["store"], eng["lifecycle"]
    item = store.get_item(int(item_id))
    if item is None:
        return False
    life.forget(int(item_id), reason=str(reason or "manual"))
    return True


def forget_user(user_id: int | str, *, db_path: str | pathlib.Path | None = None) -> int:
    """把这个人的活跃记忆全软删，返回条数。"""
    eng = _engine(db_path)
    store, life = eng["store"], eng["lifecycle"]
    uid = _uid(user_id)
    count = 0
    for item in store.list_items(user_id=uid, limit=None):
        if item.id is None:
            continue
        life.forget(int(item.id), reason="forget_user")
        count += 1
    return count
