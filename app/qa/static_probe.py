"""静态项执行层：把 inventory 列出来的「非函数」条目真的验一遍。

分工：
- `module` / `class`：能不能 import / getattr 取到（导入即崩是最狠的一类缺陷）
- `config`：配置键在真实 `Config` 上是否存在、是否有值（密钥只记"有/无"，不落值）
- `persona`：人格包顶层键是否存在、是否为空
- `table`：`data/life.db` 里是否真的有这张表、多少行、多少列（`memories` 是视图，另算）

跑完这些，报告里就不会再出现"59 个配置键全是 NOTRUN"这种假欠账。
﻿‌﻿​﻿​‍‌‌‌‍‍﻿‍​‌﻿​​‍‍﻿﻿‍‌‍‌﻿​​‍‌‍﻿"""

from __future__ import annotations

import importlib
import pathlib
import sqlite3
import time
from typing import Any

from app.qa import sandbox, types

STATIC_KINDS = ("module", "class", "config", "persona", "table")


def register(reg: Any) -> int:
    """这些 Item 由 inventory 注册过；这里只统计待验数量。"""
    n = 0
    for kind in STATIC_KINDS:
        n += len([i for i in reg.items.values() if i.kind == kind])
    return n


def _masked_value(value: Any, key: str) -> str:
    secret_hint = any(k in key.lower() for k in ("key", "token", "secret", "password"))
    if secret_hint:
        return f"<{type(value).__name__} len={len(str(value or ''))}>"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _resolve_dotted(root: Any, dotted: str) -> tuple[bool, Any]:
    cur = root
    for part in dotted.split("."):
        if not hasattr(cur, part):
            return False, None
        cur = getattr(cur, part)
    return True, cur


def run(reg: Any) -> types.PhaseResult:
    started = time.time()
    items: list[types.Item] = []
    findings: list[types.Finding] = []
    notes: list[str] = []

    # ------------------------------------------------------------ 1) 模块/类
    from app.qa import inventory  # 惰性：确保 env 已定

    all_items = list(reg.items.values()) if hasattr(reg, "items") else []
    modules = [i for i in all_items if i.kind == "module"]
    classes = [i for i in all_items if i.kind == "class"]

    loaded: dict[str, Any] = {}
    for it in modules:
        mod_name = it.module or it.name
        try:
            loaded[mod_name] = importlib.import_module(mod_name)
            reg.mark(it.id, types.OK, "import ok")
        except Exception as exc:  # noqa: BLE001
            reg.mark(it.id, types.RAISED, f"{type(exc).__name__}: {exc}")
            findings.append(types.Finding(
                id=f"ST-MOD-{mod_name}", severity=types.P0, area=mod_name,
                title="模块无法 import（导入即崩）",
                detail=str(exc)[:400], item_id=it.id,
                repro=f"python -c \"import {mod_name}\"",
            ))
        items.append(it)
    notes.append(f"modules={len(modules)} ok={sum(1 for i in modules if i.status == types.OK)}")

    for it in classes:
        # id 形如 CLS:app.api.routes:ChatIn（模块里可能含点，类名在最后一个冒号之后）
        body = it.id.split(":", 1)[1] if ":" in it.id else it.id
        mod_name, _, cls = body.partition(":")
        mod_name = mod_name or it.module
        cls = cls or it.name
        if "<locals>" in cls:
            # 定义在函数体内的类，模块级取不到 —— 静态可达性判定，不算缺陷
            reg.mark(it.id, types.SKIP, "定义在函数内（<locals>），模块级不可达")
            items.append(it)
            continue
        try:
            mod = loaded.get(mod_name) or importlib.import_module(mod_name)
            obj = mod
            for part in cls.split("."):
                obj = getattr(obj, part)
            if not isinstance(obj, type):
                raise TypeError(f"{cls} 不是类")
            reg.mark(it.id, types.OK, f"class ok, methods={len(dir(obj))}")
        except Exception as exc:  # noqa: BLE001
            reg.mark(it.id, types.RAISED, f"{type(exc).__name__}: {exc}")
            findings.append(types.Finding(
                id=f"ST-CLS-{it.id}", severity=types.P1, area=mod_name,
                title="类取不到（导出名与声明不一致）", detail=str(exc)[:300], item_id=it.id,
            ))
        items.append(it)

    # -------------------------------------------------------------- 2) 配置键
    cfg_items = [i for i in all_items if i.kind == "config"]
    if cfg_items:
        try:
            from app.core import config as config_mod

            cfg = config_mod.get()
            for it in cfg_items:
                dotted = it.id.partition(":")[2]
                ok, value = _resolve_dotted(cfg, dotted)
                if not ok:
                    reg.mark(it.id, types.RAISED, "Config 上不存在该字段")
                    findings.append(types.Finding(
                        id=f"ST-CFG-{dotted}", severity=types.P1, area="config",
                        title="配置键在 dataclass 上不存在（文档/代码漂移）",
                        detail=f"{dotted} 无法从 Config 解析", item_id=it.id,
                        repro="python -c \"from app.core import config; print(config.get())\"",
                    ))
                else:
                    reg.mark(it.id, types.OK, _masked_value(value, dotted))
                items.append(it)
            notes.append(f"config={len(cfg_items)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"config 段失败：{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------- 3) 人格键
    per_items = [i for i in all_items if i.kind == "persona"]
    if per_items:
        try:
            from app.core import config as config_mod
            from app.persona import load as persona_load

            persona = persona_load.load(config_mod.get().persona_file)
            raw = getattr(persona, "raw", {}) or {}
            for it in per_items:
                key = it.id.partition(":")[2]
                if key not in raw:
                    reg.mark(it.id, types.RAISED, "人格包缺该键")
                    findings.append(types.Finding(
                        id=f"ST-PER-{key}", severity=types.P2, area="persona",
                        title="人格包缺键", detail=f"{config_mod.get().persona_file} 无 {key}",
                        item_id=it.id,
                    ))
                else:
                    val = raw.get(key)
                    empty = val in (None, "", [], {})
                    reg.mark(it.id, types.EXPECTED if empty else types.OK,
                             "空值" if empty else f"{type(val).__name__} size={len(val) if hasattr(val,'__len__') else '-'}")
                items.append(it)
            notes.append(f"persona={len(per_items)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"persona 段失败：{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------- 4) 表
    tbl_items = [i for i in all_items if i.kind == "table"]
    if tbl_items:
        try:
            from app.core import config as config_mod

            db_file = pathlib.Path(config_mod.get().db_file)
            con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            try:
                live = {r[0]: r[1] for r in con.execute(
                    "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
                for it in tbl_items:
                    name = it.id.partition(":")[2]
                    in_schema = bool(it.extra.get("in_schema", True))
                    real_rows = it.extra.get("rows")
                    if name not in live:
                        if in_schema:
                            # _SCHEMA 声明的表，临时库建不出来 = 建库逻辑真坏了
                            reg.mark(it.id, types.RAISED, "schema 声明的表在运行库里不存在")
                            findings.append(types.Finding(
                                id=f"ST-TBL-{name}", severity=types.P0, area="db",
                                title="schema 声明的表在运行库里不存在",
                                detail=f"{name} 不在 sqlite_master（{db_file.name}）", item_id=it.id,
                            ))
                        else:
                            # vendor 迁移表 / 真库独有：临时库不建属预期
                            reg.mark(it.id, types.SKIP, "vendor 迁移表，临时库未建（真库行数=%s）" % real_rows)
                        items.append(it)
                        continue
                    try:
                        rows = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                        cols = len(con.execute(f'PRAGMA table_info("{name}")').fetchall())
                    except Exception:  # noqa: BLE001
                        rows, cols = -1, -1
                    reg.mark(it.id, types.OK, f"{live[name]} rows={rows} cols={cols}")
                    it.extra.update({"rows": rows, "cols": cols, "dbtype": live[name]})
                    items.append(it)
                # 视图口径专项（记忆不变量）
                if "memories" in live and "mem_item" in live:
                    views = con.execute("SELECT count(*) FROM memories").fetchone()[0]
                    active = con.execute(
                        "SELECT count(*) FROM mem_item WHERE forgotten=0 "
                        "AND superseded_by IS NULL AND merged_into IS NULL").fetchone()[0]
                    if views != active:
                        findings.append(types.Finding(
                            id="ST-VIEW-memories", severity=types.P0, area="db",
                            title="memories 视图与活跃 mem_item 行数不一致",
                            detail=f"view={views} active={active}",
                            repro="SELECT count(*) FROM memories;  -- 对比 mem_item 过滤后行数",
                        ))
                    notes.append(f"view.memories={views} active_mem_item={active}")
            finally:
                con.close()
            notes.append(f"tables={len(tbl_items)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"table 段失败：{type(exc).__name__}: {exc}")

    return types.PhaseResult(
        phase="static", started=started, ended=time.time(),
        items=items, findings=findings, notes=" | ".join(notes),
    )
