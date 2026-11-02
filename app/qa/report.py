"""对抗式全量训练的汇总报告生成器（CONTRACT §2 / `app/qa/report.py`）。

职责：把 `Item / Finding / TurnRecord / PhaseResult` 汇总成两份**给人看、也给脚本吃**的产物：

- `run_dir/report.md`   ：结论先行 → 总览 → 覆盖 → 缺陷 → 未通过 → 路由矩阵 → 转录 → 环境 → 下一步
- `run_dir/report.json` ：`{run_id, meta, overview, coverage, findings, items, phases, turns, generated_at}`

三条设计约束（对应 CONTRACT §0 的硬规则）：

1. **零副作用**：不 import `app.core/app.mind/app.memory`，不读库、不联网、不写 `app/` 其它文件。
   本模块只依赖 `app.qa.{types,sandbox}` 与标准库（`sandbox` 自身也只吃标准库）。
2. **缺数据不许崩**：`registry=None` / `phases={}` / `turns=None` / `findings=None` 都必须能出报告，
   缺的那一节写「未执行」——**"没跑"必须显性可见，不能因为少一个参数就从报告里消失**。
3. **可复核**：`report.json` 对同一输入是确定性的（只有顶层 `generated_at` 每次不同），
   排序全部显式指定，不依赖 dict/set 的迭代偶然顺序。

隐私：所有写出的字符串都过 `sandbox.mask()`；另外本模块补一道「归属 ID」遮罩
（`uid/user_id/openid/qq/uin/group_id` 等键的纯数字值、以及形如 `QQ:123456` 的片段），
因为 `sandbox.mask()` 只认密钥形态，不认 QQ 号。
﻿​‌‍‍‌​‌​​​‍‍‌﻿​﻿‍‍﻿‍‍‍‌﻿﻿‍‍‌﻿‍‍﻿﻿"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import re
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence

from app.qa import sandbox, types

__all__ = [
    "write",
    "REPORT_MD",
    "REPORT_JSON",
    "NOT_RUN",
    "PHASE_ORDER",
    "KIND_ORDER",
]

REPORT_MD = "report.md"
REPORT_JSON = "report.json"

NOT_RUN = "未执行"
MISSING = "未记录"

#: 阶段展示顺序：对齐 `scripts/adv_full.py` 的编排顺序（inventory→routes→functions→cli→duel→coverage）。
PHASE_ORDER: tuple[str, ...] = (
    "inventory", "routes", "functions", "cli", "duel", "coverage",
)

#: CONTRACT §3 明确要求落盘的五个阶段（缺了要在报告里点名）。
CONTRACT_PHASES: tuple[str, ...] = ("inventory", "routes", "functions", "duel", "coverage")

#: CONTRACT §1 的 kind 全集（多出来的按名字排到最后，不丢）。
KIND_ORDER: tuple[str, ...] = (
    "module", "fn", "method", "route", "cli",
    "table", "config", "persona", "scenario", "panel", "unknown",
)

KIND_DESC: dict[str, str] = {
    "module": "模块可导入性 / 静态结构",
    "fn": "函数（含私有 _x）",
    "method": "类方法",
    "route": "HTTP 路由（method + path）",
    "cli": "CLI 子命令",
    "table": "数据库表 / 视图",
    "config": "配置键",
    "persona": "人格 yaml 键",
    "scenario": "对抗场景（对话）",
    "panel": "面板页面 / 静态资源",
    "unknown": "未归类（registry 兜底产生）",
}

STATUS_ORDER: tuple[str, ...] = (
    types.OK, types.RAISED, types.SKIP, types.HANG, types.NOTRUN, types.EXPECTED,
)
BAD_STATUS: tuple[str, ...] = (types.RAISED, types.HANG, types.NOTRUN)
SEV_ORDER: tuple[str, ...] = (types.P0, types.P1, types.P2, types.INFO)

#: 表格截断宽度（CONTRACT：detail 160 / evidence 200；正文块给得宽一些）
W_DETAIL_TABLE = 160
W_EVIDENCE = 200
W_DETAIL_BLOCK = 600
W_UNCOVERED_ROWS = 300

#: `sandbox.mask()` 默认只留 4000 字；JSON 要存全文，所以显式放大。
BIG = 200_000

_TIME_FMT = "%Y-%m-%dT%H:%M:%S"

#: 归属类字段：值是纯数字时一律打码（QQ 号 / openid 数字 / 群号）
_ID_KEYS = frozenset({
    "uid", "uin", "qq", "qq号", "qq_number", "user_id", "userid", "openid",
    "group_id", "groupid", "group", "self_id", "sender_id", "target_id", "account",
})
_RE_QQ_INLINE = re.compile(r"(?i)\b(?:qq|uin|群号|账号)\s*[:：=]?\s*(\d{5,12})\b")


# ============================================================== 基础小工具 =


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """同时吃 dataclass 与 dict（JSON 反序列化回来的用得上）。"""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda x: str(x))
    return [value]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out  # NaN 归一


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pct(part: Any, whole: Any) -> str:
    p, w = _num(part), _num(whole)
    if w <= 0:
        return "—"
    return f"{p / w * 100:.1f}%"


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _cut(text: Any, width: int) -> str:
    """单行截断：表格里用，超宽补「…」。"""
    body = _one_line(text)
    if width <= 0:
        return ""
    return body if len(body) <= width else body[: max(1, width - 1)] + "…"


def _qq_guard(text: str) -> str:
    """额外一道 QQ 号遮罩：只认明确带前缀的内联写法，避免误伤行号/耗时。"""
    return _RE_QQ_INLINE.sub(lambda m: m.group(0)[: m.group(0).find(m.group(1))] + "***", text)


#: `sandbox.mask()` 的替换是「`group(1) + "***"`」——对裸 `sk-xxx` 这种令牌，
#: 它只会在原文后面追加 `***`，令牌本体仍然留在字符串里。报告是要发出去的，
#: 所以本模块补一道**真剥离**（不改 `sandbox.py`，只在自己的出口处兜住）。
_RE_BARE_SK = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_RE_KV_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|app[_-]?token|access[_-]?token|bot[_-]?token|appid|app_id|secret)\b"
    r"(\s*[:=：]\s*)([^\s,;，；。）)\]]+)"
)


def _secret_guard(text: str) -> str:
    """把裸令牌与 `key=value` 形态的凭据真正抹掉（保留键名，值换成 ***）。"""
    text = _RE_BARE_SK.sub("sk-***", text)
    return _RE_KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def _m(value: Any, limit: int = BIG) -> str:
    """全文脱敏：`sandbox.mask()` + QQ 号兜底；None → 空串。"""
    if value is None:
        return ""
    body = value if isinstance(value, str) else str(value)
    try:
        body = sandbox.mask(body, limit=limit)
    except TypeError:  # 老签名没有 limit 关键字
        body = sandbox.mask(body)[:limit]
    except Exception:  # noqa: BLE001 - 遮罩不许拖垮报告
        pass
    return _secret_guard(_qq_guard(body))


def _m_deep(value: Any, depth: int = 0) -> Any:
    """递归脱敏任意 JSON 可序列化结构（meta / evidence / extra 用）。"""
    if depth > 12:
        return "<深度截断>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6) if value == value else 0.0
    if isinstance(value, str):
        return _m(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key in sorted(value, key=lambda k: str(k)):
            out[str(key)] = _mask_id_pair(str(key), value[key], depth)
        return out
    if isinstance(value, (list, tuple)):
        return [_m_deep(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_m_deep(v, depth + 1) for v in sorted(value, key=lambda x: str(x))]
    if hasattr(value, "to_dict") and not isinstance(value, type):
        try:
            return _m_deep(value.to_dict(), depth + 1)
        except Exception:  # noqa: BLE001 - 半个对象也不能拖垮报告
            pass
    return _m(repr(value))


def _mask_id_pair(key: str, value: Any, depth: int) -> Any:
    """键名像归属 ID 且值是纯数字 → 打码。"""
    if key.lower().strip() in _ID_KEYS and re.fullmatch(r"\d{4,}", str(value or "")):
        return "***"
    return _m_deep(value, depth + 1)


def _record_dict(obj: Any) -> dict[str, Any]:
    """把 dataclass / dict 归一成脱敏后的 dict（`report.json` 用，保留全文）。"""
    if isinstance(obj, Mapping):
        raw: Any = dict(obj)
    elif hasattr(obj, "to_dict"):
        try:
            raw = obj.to_dict()
        except Exception:  # noqa: BLE001
            raw = {"id": str(obj), "repr": repr(obj)}
    else:
        raw = {"id": str(obj), "repr": repr(obj)}
    out = _m_deep(raw)
    return out if isinstance(out, dict) else {"value": out}


def _json_block(payload: Any, width: int = 0) -> str:
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError):
        body = str(payload)
    body = _m(body)
    if width and len(body) > width:
        body = body[: max(1, width - 1)] + "…"
    return body


# ============================================================== 输入归一化 =


def _phase_entries(phases: Any) -> list[tuple[str, Any]]:
    """把 `phases` 归一成 [(name, obj)]，顺序固定：先 PHASE_ORDER，再其余按名字。"""
    if not isinstance(phases, Mapping):
        return []
    known = [(name, phases[name]) for name in PHASE_ORDER if name in phases]
    extra = [(str(name), phases[name]) for name in phases if name not in PHASE_ORDER]
    extra.sort(key=lambda kv: kv[0])
    return known + extra


def _items_of(obj: Any) -> list[Any]:
    """从一个 phase 值里抠出 item 列表（PhaseResult / dict / 裸 list 都认）。"""
    if obj is None:
        return []
    if isinstance(obj, (list, tuple)):
        return [x for x in obj if x is not None]
    raw = _get(obj, "items", None)
    if isinstance(raw, (list, tuple)):
        return [x for x in raw if x is not None]
    return []


def _counts(items: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        status = str(_get(it, "status", types.NOTRUN) or types.NOTRUN)
        out[status] = out.get(status, 0) + 1
    return out


def _phase_secs(obj: Any) -> float | None:
    """耗时。**不用** `PhaseResult.secs`：它没 finish 时会取 `time.time()`，破坏确定性。"""
    if obj is None:
        return None
    secs = _get(obj, "secs", None)
    started, ended = _get(obj, "started", None), _get(obj, "ended", None)
    if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
        if ended and ended >= started:
            delta = round(float(ended) - float(started), 2)
            # 时钟异常（假 started / 跨进程复用对象）时不往报告里塞天文数字
            return delta if 0.0 <= delta <= 172800.0 else None
        if not ended:
            return 0.0  # 未 finish：报 0，别让报告每次都不一样
    if isinstance(secs, (int, float)):
        value = round(float(secs), 2)
        return value if 0.0 <= value <= 172800.0 else None
    return None


def _collect_items(registry: Any, phases: Any) -> list[Any]:
    """合并 registry 与 phases 里的 item，按 id 排序，去重。"""
    pool: dict[str, Any] = {}
    reg_items = _get(registry, "items", None) if registry is not None else None
    if isinstance(reg_items, Mapping):
        for item in reg_items.values():
            iid = str(_get(item, "id", "") or "")
            if iid:
                pool[iid] = item
    for _name, obj in _phase_entries(phases):
        for item in _items_of(obj):
            iid = str(_get(item, "id", "") or "")
            if iid:
                pool.setdefault(iid, item)
    return [pool[k] for k in sorted(pool)]


def _item_phase_map(phases: Any) -> dict[str, str]:
    """item id → 首次出现的 phase 名（报告里给出处用）。"""
    out: dict[str, str] = {}
    for name, obj in _phase_entries(phases):
        for item in _items_of(obj):
            iid = str(_get(item, "id", "") or "")
            if iid:
                out.setdefault(iid, name)
    return out


def _norm_findings(findings: Any) -> list[Any]:
    out = [f for f in _as_list(findings) if f is not None]
    out.sort(key=lambda f: (_sev_rank(str(_get(f, "severity", "") or "")), str(_get(f, "id", "") or "")))
    return out


def _sev_rank(sev: str) -> int:
    try:
        return SEV_ORDER.index(sev)
    except ValueError:
        return len(SEV_ORDER)


def _norm_turns(turns: Any) -> list[Any]:
    out = [t for t in _as_list(turns) if t is not None]

    def key(t: Any) -> tuple[str, int, str]:
        return (
            str(_get(t, "scenario", "") or ""),
            _int(_get(t, "index", 0), 0),
            str(_get(t, "role", "") or ""),
        )

    out.sort(key=key)
    return out


def _turn_findings(turn: Any) -> list[Any]:
    return [f for f in _as_list(_get(turn, "findings", None)) if f is not None]


# ============================================================== 各区块计算 =


def _overview(items: Sequence[Any], findings: Sequence[Any], coverage: dict) -> dict[str, Any]:
    by_status = _counts(items)
    by_sev: dict[str, int] = {}
    for f in findings:
        sev = str(_get(f, "severity", "") or types.INFO)
        by_sev[sev] = by_sev.get(sev, 0) + 1

    total = len(items)
    ok = by_status.get(types.OK, 0)
    expected = by_status.get(types.EXPECTED, 0)
    raised = by_status.get(types.RAISED, 0)
    hang = by_status.get(types.HANG, 0)
    skip = by_status.get(types.SKIP, 0)
    notrun = by_status.get(types.NOTRUN, 0)
    executed = ok + expected + raised + hang
    passed = ok + expected

    top = [f for f in findings if str(_get(f, "severity", "")) in (types.P0, types.P1)][:3]
    if not top:
        top = list(findings[:3])

    return {
        "items_total": total,
        "items_executed": executed,
        "items_passed": passed,
        "items_skipped": skip,
        "items_notrun": notrun,
        "by_status": {k: by_status[k] for k in sorted(by_status)},
        "findings_total": len(findings),
        "by_severity": {k: by_sev[k] for k in sorted(by_sev)},
        "pass_ratio": round(passed / max(1, executed), 4) if executed else None,
        "coverage_ratio": coverage.get("ratio"),
        "top_finding_ids": [str(_get(f, "id", "") or "") for f in top],
    }


def _coverage(registry: Any, phases: Any, meta: Mapping[str, Any], items: Sequence[Any]) -> dict[str, Any]:
    """覆盖数据来源优先级：meta["coverage"] → phases["coverage"] 本身是 dict → 其后 notes 的 JSON。"""
    raw: Any = None
    note = ""
    if isinstance(meta, Mapping) and isinstance(meta.get("coverage"), Mapping):
        raw, note = dict(meta["coverage"]), "来源：meta.coverage"
    if raw is None:
        obj = phases.get("coverage") if isinstance(phases, Mapping) else None
        if isinstance(obj, Mapping):
            raw, note = dict(obj), "来源：phases['coverage']（dict）"
        elif obj is not None:
            notes = _get(obj, "notes", "")
            if isinstance(notes, str) and notes.strip().startswith("{"):
                try:
                    parsed = json.loads(notes)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, Mapping):
                    raw, note = dict(parsed), "来源：phases['coverage'].notes（JSON）"
            if raw is None:
                maybe = _get(registry, "coverage", None)
                if isinstance(maybe, Mapping):
                    raw, note = dict(maybe), "来源：registry.coverage"
            if raw is None:
                note = "来源：phases['coverage'] 里没有 covered/total/uncovered 字段"

    covered = total = None
    ratio = None
    uncovered: list[str] = []
    by_kind_raw: Mapping[str, Any] = {}
    derived = False

    if raw is not None:
        covered = _int(raw.get("covered"), -1)
        total = _int(raw.get("total"), -1)
        covered = None if covered < 0 else covered
        total = None if total < 0 else total
        ratio = _num(raw.get("ratio"), -1.0)
        ratio = None if ratio < 0 else round(ratio, 4)
        if ratio is None and covered is not None and total:
            ratio = round(covered / total, 4)
        for entry in _as_list(raw.get("uncovered")):
            uncovered.append(_uncovered_label(entry))
        if isinstance(raw.get("by_kind"), Mapping):
            by_kind_raw = raw["by_kind"]

    # 函数/方法口径：用 registry 里的总数兜底（不受 coverage 是否可用影响）
    fn_items = [i for i in items if str(_get(i, "kind", "")) in ("fn", "method")]

    if not uncovered and not (raw is not None and raw.get("uncovered") is not None):
        # 没有覆盖工具时，用 NOTRUN 当"列了没跑"的兜底证据（口径不同，必须标注）
        derived = True
        for it in items:
            if str(_get(it, "status", "")) == types.NOTRUN:
                uncovered.append(str(_get(it, "id", "") or ""))
        uncovered = [u for u in uncovered if u]

    by_kind: dict[str, dict[str, Any]] = {}
    for kind in _kinds_of(items):
        rows = [i for i in items if str(_get(i, "kind", "") or "unknown") == kind]
        cell = _counts(rows)
        entry = {
            "total": len(rows),
            "by_status": {k: cell[k] for k in sorted(cell)},
        }
        if isinstance(by_kind_raw.get(kind), Mapping):
            entry.update(_m_deep(dict(by_kind_raw[kind])))
        by_kind[kind] = entry

    return {
        "available": raw is not None,
        "note": note or "phases 里没有 `coverage` 阶段，也没有传 meta['coverage']（覆盖追踪没跑）",
        "covered": covered,
        "total": total,
        "ratio": ratio,
        "fn_total": len(fn_items),
        "fn_covered": covered if (covered is not None and not fn_items) else None,
        "uncovered": uncovered,
        "uncovered_derived_from_notrun": derived,
        "by_kind": by_kind,
    }


def _uncovered_label(entry: Any) -> str:
    if isinstance(entry, Mapping):
        module = str(entry.get("module", "") or "")
        qual = str(entry.get("qualname", "") or entry.get("name", "") or "")
        if module or qual:
            return _m(f"{module}:{qual}" if module and qual else (qual or module))
    return _m(str(entry or ""))


def _kinds_of(items: Sequence[Any]) -> list[str]:
    found = {str(_get(i, "kind", "") or "unknown") for i in items}
    ordered = [k for k in KIND_ORDER if k in found]
    ordered += sorted(found - set(KIND_ORDER))
    return ordered


def _route_rows(items: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for it in items:
        if str(_get(it, "kind", "")) != "route":
            continue
        extra = _get(it, "extra", {}) or {}
        if not isinstance(extra, Mapping):
            extra = {}
        method = str(extra.get("method") or "").upper()
        path = str(extra.get("path") or "")
        iid = str(_get(it, "id", "") or "")
        raw = iid[6:] if iid.upper().startswith("ROUTE:") else str(_get(it, "name", "") or iid)
        if not method or not path:
            match = re.match(r"^\s*([A-Za-z]{3,7})\s+(\S+)", raw)
            if match:
                method = method or match.group(1).upper()
                path = path or match.group(2)
            elif not path:
                path = raw.strip() or "—"

        attempts = _first_num(extra, ("attempts", "tries", "payloads", "variants", "cases", "n"))
        worst = _first_num(extra, ("worst_status", "max_status", "status_code", "status", "code"))
        is_5xx = extra.get("is_5xx")
        if not isinstance(is_5xx, bool):
            is_5xx = bool(worst is not None and worst >= 500)
        auth = extra.get("auth_missing")
        if not isinstance(auth, bool):
            for alias in ("no_auth_ok", "unauth_ok", "missing_auth", "auth_bypass"):
                if isinstance(extra.get(alias), bool):
                    auth = extra[alias]
                    break
        rows.append({
            "id": iid,
            "method": method or "—",
            "path": path or "—",
            "attempts": attempts,
            "worst_status": worst,
            "is_5xx": bool(is_5xx),
            "auth_missing": auth if isinstance(auth, bool) else None,
            "status": str(_get(it, "status", "") or ""),
            "detail": str(_get(it, "detail", "") or ""),
        })
    rows.sort(key=lambda r: (r["path"], r["method"], r["id"]))
    return rows


def _first_num(extra: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        if key in extra and extra[key] is not None:
            value = extra[key]
            if isinstance(value, bool):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _turn_rows(turns: Sequence[Any]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for turn in turns:
        name = str(_get(turn, "scenario", "") or "(未命名场景)")
        cell = buckets.setdefault(name, {
            "scenario": name,
            "records": 0,
            "user_records": 0,
            "her_records": 0,
            "latency_sum": 0,
            "latency_n": 0,
            "findings": 0,
            "first_index": None,
            "last_index": None,
        })
        cell["records"] += 1
        role = str(_get(turn, "role", "") or "")
        if role == "user":
            cell["user_records"] += 1
        elif role:
            cell["her_records"] += 1
        latency = _int(_get(turn, "latency_ms", 0), 0)
        if latency > 0:
            cell["latency_sum"] += latency
            cell["latency_n"] += 1
        cell["findings"] += len(_turn_findings(turn))
        index = _int(_get(turn, "index", 0), 0)
        cell["first_index"] = index if cell["first_index"] is None else min(cell["first_index"], index)
        cell["last_index"] = index if cell["last_index"] is None else max(cell["last_index"], index)

    rows: list[dict[str, Any]] = []
    for name in sorted(buckets):
        cell = buckets[name]
        rows.append({
            "scenario": name,
            "transcript": f"transcripts/{name}.jsonl",
            "records": cell["records"],
            "user_records": cell["user_records"],
            "her_records": cell["her_records"],
            "turns": (cell["last_index"] or 0),
            "avg_latency_ms": round(cell["latency_sum"] / cell["latency_n"], 1) if cell["latency_n"] else None,
            "findings": cell["findings"],
        })
    return rows


def _desensitize_path(value: Any) -> str:
    """临时数据根脱敏：临时目录只留最后一段，家目录换成 ~。"""
    if not value:
        return MISSING
    raw = str(value)
    body = raw
    try:
        temp_root = tempfile.gettempdir()
        resolved = str(pathlib.Path(raw))
        if temp_root and resolved.lower().startswith(temp_root.lower()):
            body = "<TMP>/" + (pathlib.Path(resolved).name or "")
        else:
            home = str(pathlib.Path.home())
            if home and resolved.lower().startswith(home.lower()):
                body = "~" + resolved[len(home):]
    except (OSError, ValueError):
        body = raw
    return _cut(_m(body), 200)


def _env(meta: Mapping[str, Any], run_id: str, run_dir: pathlib.Path) -> dict[str, Any]:
    """环境与可复现信息。缺的写「未记录」，不猜。"""
    def pick(*keys: str) -> Any:
        for key in keys:
            if isinstance(meta, Mapping) and meta.get(key) not in (None, ""):
                return meta[key]
        return None

    started = pick("started_at", "started", "start")
    ended = pick("ended_at", "ended", "end")
    duration = pick("duration_secs", "duration", "elapsed_s", "elapsed_secs", "elapsed", "secs")
    blocked = pick("net_blocked", "blocked_count", "network_blocked", "blocked")
    blocked_n = len(_as_list(blocked)) if isinstance(blocked, (list, tuple, set)) else _int(blocked, 0)

    mock = pick("provider_mock", "is_mock", "mock")
    if mock is None:
        provider = pick("provider")
        if isinstance(provider, Mapping):
            mock = provider.get("is_mock")
    if mock is None:
        # adv_full 的 manifest 只留 env 快照；SYMEM_MOCK=1 就是 mock provider
        env_snapshot = pick("env")
        if isinstance(env_snapshot, Mapping) and str(env_snapshot.get("SYMEM_MOCK", "")).lower() in ("1", "true"):
            mock = True
    mock_text = MISSING if mock is None else ("是（mock，零成本）" if bool(mock) else "否（真 provider）")

    # `scripts/adv_full.py` 的 manifest 用 argv 列表 + elapsed_s + tmp_data_root，这里一并认
    command = pick("command", "cmd", "argv", "cmdline")
    if isinstance(command, (list, tuple)):
        command = " ".join(str(x) for x in command)
    if not command:
        command = MISSING
    elif not str(command).startswith("python"):
        command = "python scripts/adv_full.py " + str(command)

    return {
        "run_id": run_id,
        "head": _m(str(pick("head", "git_head", "HEAD", "commit", "git_commit") or MISSING)),
        "python": _m(_one_line(pick("python", "python_version") or platform.python_version())),
        "started_at": _m(str(started or MISSING)),
        "ended_at": _m(str(ended or MISSING)),
        "duration_secs": (round(_num(duration), 2) if duration is not None else None),
        "command": _m(str(command)),
        "data_root": _desensitize_path(
            pick("data_root", "tmp_data_root", "SYMEM_DATA", "tmp_root") or os.environ.get("SYMEM_DATA")
        ),
        "provider_mock": mock_text,
        "net_blocked": blocked_n,
        "report_dir": _desensitize_path(run_dir),
    }


def _headline(overview: Mapping[str, Any], top: Sequence[Any]) -> str:
    """结论先行：目标 ≤100 字。"""
    total = overview["items_total"]
    executed = overview["items_executed"]
    skipped = overview["items_skipped"]
    notrun = overview["items_notrun"]
    p0 = overview["by_severity"].get(types.P0, 0)
    p1 = overview["by_severity"].get(types.P1, 0)

    if not total:
        head = f"受测 0 项。P0 {p0} 条，P1 {p1} 条。"
    else:
        head = f"受测 {total} 项（执行 {executed}／跳过 {skipped}／未跑 {notrun}），P0 {p0} 条，P1 {p1} 条。"
    if not top:
        return _cut(head + "无 P0/P1 缺陷。", 100)

    for width in (18, 16, 12, 9, 6, 4):
        names = "；".join(_clip_title(_get(f, "title", ""), width) for f in top[:3])
        candidate = f"{head}最严重：{names}。"
        if len(candidate) <= 100:
            return candidate
    return _cut(f"{head}最严重：{_one_line(_get(top[0], 'title', ''))}。", 100)


def _clip_title(title: Any, width: int) -> str:
    """结论行里的标题截断：明确加「…」，别让读者以为原文就断在半句。"""
    body = _one_line(title)
    if len(body) <= width:
        return body
    return body[: max(1, width - 1)] + "…"


def _next_steps(findings: Sequence[Any]) -> list[dict[str, Any]]:
    buckets = [
        {"label": "立刻修（P0：崩溃／数据损坏／安全／静默失败）", "ids": [], "titles": []},
        {"label": "本轮修（P1：功能错误／契约不符／出戏）", "ids": [], "titles": []},
        {"label": "记录待办（P2／INFO：体验与口径）", "ids": [], "titles": []},
    ]
    for f in findings:
        sev = str(_get(f, "severity", "") or types.INFO)
        idx = 0 if sev == types.P0 else 1 if sev == types.P1 else 2
        buckets[idx]["ids"].append(str(_get(f, "id", "") or ""))
        buckets[idx]["titles"].append(_one_line(_get(f, "title", "")))
    return [b for b in buckets]


# ============================================================== Markdown ====


def _header_lines(run_id: str, run_dir: pathlib.Path) -> list[str]:
    return [
        f"# 对抗式全量训练报告 · `{_m(run_id)}`",
        "",
        f"> 产物目录：`{_desensitize_path(run_dir)}` ｜ 同名 JSON：`{REPORT_JSON}`（可脚本复核，字段见文末）",
        "",
    ]


def _section_headline(headline: str, overview: Mapping[str, Any], blockers: Sequence[str]) -> list[str]:
    lines = ["## 1. 结论先行", "", f"**{headline}**", ""]
    chips = [
        ("受测条目", f"{overview['items_total']}"),
        ("执行", f"{overview['items_executed']}"),
        ("通过", f"{overview['items_passed']}"),
        ("P0", f"{overview['by_severity'].get(types.P0, 0)}"),
        ("P1", f"{overview['by_severity'].get(types.P1, 0)}"),
        ("跳过", f"{overview['items_skipped']}"),
        ("未跑", f"{overview['items_notrun']}"),
    ]
    lines.append("| " + " | ".join(k for k, _ in chips) + " |")
    lines.append("|" + "|".join("---" for _ in chips) + "|")
    lines.append("| " + " | ".join(v for _, v in chips) + " |")
    lines.append("")
    if blockers:
        lines += ["**必须先看的三个问题**", ""]
        lines += [f"- {item}" for item in blockers]
        lines.append("")
    return lines


def _section_phases(rows: Sequence[Mapping[str, Any]], total_row: Mapping[str, Any] | None) -> list[str]:
    lines = ["## 2. 总览：阶段 × 条目 × 状态", ""]
    if not rows:
        lines += [f"**{NOT_RUN}**：本次没有传 `phases`（或为空 dict），没有任何阶段执行记录。", ""]
        if total_row:
            lines += [
                "| 阶段 | 条目数 | OK | RAISED | SKIP | HANG | NOTRUN | EXPECTED | 耗时(s) | 备注 |",
                "|---|---|---|---|---|---|---|---|---|---|",
                _phase_row_md("（无阶段记录，仅注册表）", total_row),
                "",
            ]
        return lines

    lines += [
        "| 阶段 | 条目数 | OK | RAISED | SKIP | HANG | NOTRUN | EXPECTED | 耗时(s) | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(_phase_row_md(str(row["phase"]), row))
    if total_row:
        lines.append(_phase_row_md("**合计（注册表全量）**", total_row))
    lines.append("")
    missing = [p for p in CONTRACT_PHASES if p not in {str(r["phase"]) for r in rows}]
    if missing:
        lines.append(f"契约要求的阶段没跑到：{'、'.join(f'`{p}`' for p in missing)}（{NOT_RUN}）。")
        lines.append("")
    return lines


def _phase_row_md(label: str, row: Mapping[str, Any]) -> str:
    by_status = row.get("by_status") or {}
    cells = [label, str(row.get("items_total", 0))]
    for status in STATUS_ORDER:
        cells.append(str(by_status.get(status, 0)))
    secs = row.get("secs")
    cells.append("—" if secs is None else f"{_num(secs):.2f}")
    cells.append(_cut(row.get("notes", "") or "", 60) or "—")
    return "| " + " | ".join(cells) + " |"


def _section_coverage(coverage: Mapping[str, Any], items: Sequence[Any]) -> list[str]:
    lines = ["## 3. 覆盖", ""]
    if coverage.get("available"):
        lines += [
            f"- 全量覆盖：**{_pct(coverage.get('covered'), coverage.get('total'))}**"
            f"（{coverage.get('covered') if coverage.get('covered') is not None else '?'} / "
            f"{coverage.get('total') if coverage.get('total') is not None else '?'}）",
        ]
    else:
        lines += [f"- 全量覆盖：**{NOT_RUN}** — {_cut(coverage.get('note', ''), 120)}"]
    fn_total = _int(coverage.get("fn_total"), 0)
    lines += [
        f"- 函数／方法条目：**{fn_total}** 条"
        + (f"，被调用覆盖 {_pct(coverage.get('fn_covered'), fn_total)}" if coverage.get("fn_covered") is not None else "（逐函数调用覆盖比未提供）"),
        f"- 登记条目：全量见 `{REPORT_JSON}` 的 `items`（编排脚本另存 `items.json`）；本节的 kind 汇总覆盖 {len(items)} 条 item",
        "",
    ]

    kinds = _kinds_of(items)
    if kinds:
        lines += [
            "### 3.1 按 kind 汇总",
            "",
            "| kind | 说明 | 条目数 | OK | RAISED | SKIP | HANG | NOTRUN |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for kind in kinds:
            rows = [i for i in items if str(_get(i, "kind", "") or "unknown") == kind]
            cell = _counts(rows)
            lines.append(
                "| "
                + " | ".join([
                    f"`{kind}`",
                    KIND_DESC.get(kind, "—"),
                    str(len(rows)),
                    str(cell.get(types.OK, 0)),
                    str(cell.get(types.RAISED, 0)),
                    str(cell.get(types.SKIP, 0)),
                    str(cell.get(types.HANG, 0)),
                    str(cell.get(types.NOTRUN, 0)),
                ])
                + " |"
            )
        lines.append("")
    else:
        lines += [f"### 3.1 按 kind 汇总", "", f"**{NOT_RUN}**：一条 item 都没有。", ""]

    # ---- 未覆盖清单（"不偷懒"的证据）----
    uncovered = list(coverage.get("uncovered") or [])
    lines += ["### 3.2 未覆盖清单", ""]
    if coverage.get("uncovered_derived_from_notrun"):
        lines += [
            "> 口径说明：本次没有拿到覆盖追踪器的结果，这里列的是 **`status=NOTRUN` 的条目**"
            "（\"登记了但没跑\"），不等于\"没被调用过\"。",
            "",
        ]
    if not uncovered:
        lines += ["无（没有未覆盖／未执行的条目）。", ""]
    else:
        lines += [
            f"共 **{len(uncovered)}** 条。这是本报告最重要的自证：**没有静默丢弃**。",
            "",
            "| # | 条目 / 函数 |",
            "|---|---|",
        ]
        for idx, label in enumerate(uncovered[:W_UNCOVERED_ROWS], 1):
            lines.append(f"| {idx} | `{_cut(label, 120)}` |")
        rest = len(uncovered) - W_UNCOVERED_ROWS
        if rest > 0:
            lines.append(f"| … | 其余 {rest} 条见 `{REPORT_JSON}` 的 `coverage.uncovered` |")
        lines.append("")
    return lines


def _section_findings(findings: Sequence[Any], provided: bool = True, source: str = "") -> list[str]:
    lines = ["## 4. 缺陷清单", ""]
    if not provided:
        return lines + [f"**{NOT_RUN}**：没有传入 `findings`（意思是「没跑」，不是「0 条」）。", ""]
    if source:
        lines += [f"> 来源：{source}", ""]
    if not findings:
        return lines + ["**0 条**：本次没有任何 Finding（不代表没问题，先看 §3.2 未覆盖清单）。", ""]

    counts: dict[str, int] = {sev: 0 for sev in SEV_ORDER}
    areas: dict[str, int] = {}
    for f in findings:
        sev = str(_get(f, "severity", "") or types.INFO)
        counts[sev] = counts.get(sev, 0) + 1
        area = str(_get(f, "area", "") or "(未填)")
        areas[area] = areas.get(area, 0) + 1

    lines += [
        "| 严重度 | 条数 | 含义 |",
        "|---|---|---|",
        f"| P0 | {counts.get(types.P0, 0)} | 崩溃／数据损坏／安全／静默失败 |",
        f"| P1 | {counts.get(types.P1, 0)} | 功能错误／契约不符／明显出戏 |",
        f"| P2 | {counts.get(types.P2, 0)} | 体验／健壮性／口径不一致 |",
        f"| INFO | {counts.get(types.INFO, 0)} | 记录项 |",
        "",
        "分布：" + "、".join(f"`{_cut(a, 24)}`×{n}" for a, n in sorted(areas.items(), key=lambda kv: (-kv[1], kv[0]))),
        "",
    ]

    for f in findings:
        sev = str(_get(f, "severity", "") or types.INFO)
        fid = str(_get(f, "id", "") or "(无 id)")
        area = _m(str(_get(f, "area", "") or "(未填 area)"))
        title = _m(_one_line(_get(f, "title", "")) or "(无标题)")
        detail = _m(str(_get(f, "detail", "") or ""))
        evidence = _get(f, "evidence", None)
        repro = _m(_one_line(_get(f, "repro", "")))
        item_id = _m(str(_get(f, "item_id", "") or ""))

        lines += [f"### [{sev}] `{fid}` · `{area}` — {title}", ""]
        lines.append(f"- **所属条目**：{('`' + item_id + '`') if item_id else '—'}")
        lines.append(f"- **复现**：{('`' + _cut(repro, 200) + '`') if repro else '—'}")
        if detail:
            shown = detail if len(detail) <= W_DETAIL_BLOCK else detail[: W_DETAIL_BLOCK - 1] + "…"
            lines += ["- **详情**：", "", "  " + shown.replace("\n", "\n  "), ""]
        if evidence:
            # 先按键名打码（uid/user_id/qq… 的纯数字值），再序列化 —— 顺序反了会漏号
            block = _json_block(_m_deep(evidence))
            truncated = len(block) > W_EVIDENCE
            if truncated:
                block = block[: W_EVIDENCE - 1] + "…"
            lines += ["- **证据**：", "", "  ```json", *[f"  {ln}" for ln in block.splitlines()], "  ```", ""]
            if truncated:
                lines += [f"  （证据已截到 {W_EVIDENCE} 字，全文见 `{REPORT_JSON}`）", ""]
        else:
            lines += ["- **证据**：—", ""]
    return lines


def _section_unresolved(items: Sequence[Any], phase_map: Mapping[str, str], registry: Any) -> list[str]:
    lines = ["## 5. 未通过条目逐条表", ""]
    if registry is None and not phase_map:
        return lines + [f"**{NOT_RUN}**：既没有 `registry` 也没有任何 phase 的 item 列表。", ""]

    bad = [i for i in items if str(_get(i, "status", "") or "") in BAD_STATUS]
    if not bad:
        return lines + ["无：没有任何 `RAISED` / `HANG` / `NOTRUN` 条目。", ""]

    per_status = _counts(bad)
    lines += [
        "口径：`RAISED`＝调用抛异常，`HANG`＝超时，`NOTRUN`＝登记了没跑到。"
        "**`SKIP` 不在此表**（按契约 §0.5 给过 reason，属于设计内跳过）。",
        "",
        "分布：" + "、".join(f"`{s}`×{per_status[s]}" for s in STATUS_ORDER if per_status.get(s)) + f"，合计 **{len(bad)}** 条。",
        "",
        "| id | 状态 | 所属阶段 | 名称 | detail（前 160 字） |",
        "|---|---|---|---|---|",
    ]
    for it in bad:
        iid = str(_get(it, "id", "") or "")
        lines.append(
            "| "
            + " | ".join([
                f"`{_cut(iid, 60)}`",
                f"`{_cut(_get(it, 'status', ''), 16)}`",
                f"`{_cut(phase_map.get(iid, '—'), 24)}`",
                _cut(_get(it, "name", "") or "—", 60),
                _cut(_get(it, "detail", "") or "—", W_DETAIL_TABLE).replace("|", "\\|"),
            ])
            + " |"
        )
    lines.append("")
    return lines


def _section_routes(rows: Sequence[Mapping[str, Any]], items: Sequence[Any]) -> list[str]:
    lines = ["## 6. 路由矩阵", ""]
    if not any(str(_get(i, "kind", "")) == "route" for i in items):
        return lines + [f"**{NOT_RUN}**：本次 item 里没有任何 `kind=route`（routes phase 未执行）。", ""]

    five = sum(1 for r in rows if r["is_5xx"])
    noauth = sum(1 for r in rows if r["auth_missing"] is True)
    attempted = sum(1 for r in rows if r["attempts"])
    lines += [
        f"- 路由条目：**{len(rows)}** 条；其中尝试过请求的 {attempted} 条。",
        f"- 出现过 5xx：**{five}** 条（任何 5xx 按契约判 P0）。",
        f"- 无鉴权却 2xx：**{noauth}** 条（已知现状，见 CONTRACT §2 routes_fuzz）。",
        "",
        "| 方法 | 路径 | 尝试数 | 最差状态码 | 5xx | 缺鉴权 | 条目状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        attempts = "—" if r["attempts"] is None else str(r["attempts"])
        worst = "—" if r["worst_status"] is None else str(r["worst_status"])
        lines.append(
            "| "
            + " | ".join([
                f"`{_cut(r['method'], 10)}`",
                f"`{_cut(r['path'], 70)}`",
                attempts,
                worst,
                "**是**" if r["is_5xx"] else "否",
                ("是" if r["auth_missing"] else "否") if r["auth_missing"] is not None else MISSING,
                f"`{_cut(r['status'], 12) or '—'}`",
            ])
            + " |"
        )
    lines.append("")
    if not rows:
        lines.append(f"> 有 route 条目但一条都没能解析出 method/path：{NOT_RUN}。")
        lines.append("")
    return lines


def _section_transcripts(rows: Sequence[Mapping[str, Any]], turns: Sequence[Any]) -> list[str]:
    lines = ["## 7. 对话转录索引", ""]
    if not turns:
        return lines + [f"**{NOT_RUN}**：没有 `TurnRecord`（duel phase 未执行或未产出转录）。", ""]

    total_records = sum(r["records"] for r in rows)
    total_findings = sum(r["findings"] for r in rows)
    lat_sum = 0
    lat_n = 0
    for turn in turns:
        lat = _int(_get(turn, "latency_ms", 0), 0)
        if lat > 0:
            lat_sum += lat
            lat_n += 1
    lines += [
        f"- 场景 **{len(rows)}** 个，记录 **{total_records}** 条，命中 Finding **{total_findings}** 条。",
        f"- 平均延迟（仅计 >0 的记录）：**{round(lat_sum / lat_n, 1) if lat_n else '—'} ms**。",
        f"- 转录文件相对路径：`transcripts/<scenario>.jsonl`；prompt 快照 `transcripts/<scenario>.prompt.json`。",
        "",
        "| 场景 | 轮数 | user / her | 平均延迟(ms) | 命中 Finding | 转录路径 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        avg = "—" if r["avg_latency_ms"] is None else f"{_num(r['avg_latency_ms']):.1f}"
        lines.append(
            "| "
            + " | ".join([
                _cut(r["scenario"], 40).replace("|", "\\|"),
                str(r["records"]),
                f"{r['user_records']} / {r['her_records']}",
                avg,
                str(r["findings"]),
                f"`{_cut(r['transcript'], 60)}`",
            ])
            + " |"
        )
    lines.append("")
    return lines


def _section_env(env: Mapping[str, Any]) -> list[str]:
    lines = ["## 8. 环境与可复现", ""]
    rows = [
        ("HEAD", env.get("head")),
        ("Python", env.get("python")),
        ("run id", f"`{_m(str(env.get('run_id') or ''))}`"),
        ("开始", env.get("started_at")),
        ("结束", env.get("ended_at")),
        ("耗时(s)", "—" if env.get("duration_secs") is None else f"{_num(env['duration_secs']):.2f}"),
        ("命令", f"`{_cut(env.get('command'), 200)}`"),
        ("临时数据根（脱敏）", f"`{env.get('data_root')}`"),
        ("provider", env.get("provider_mock")),
        ("联网被拦次数", str(env.get("net_blocked", 0))),
        ("报告目录（脱敏）", f"`{env.get('report_dir')}`"),
    ]
    lines += ["| 项 | 值 |", "|---|---|"]
    for key, value in rows:
        lines.append(f"| {key} | {_cut(value if value not in (None, '') else MISSING, 200).replace('|', chr(92) + '|')} |")
    lines.append("")
    lines += [
        "复现要点：",
        "",
        "1. 先 `sandbox.prepare_env()`，**再** import 任何 `app.*`（否则 `SYMEM_DATA` 已定死，见 `sandbox.py:38-42`）。",
        "2. 全程 `SYMEM_MOCK=1` ＋ 断网 ＋ 临时数据根，真库与真网络都不参与。",
        "3. 单条命令：`python scripts/adv_full.py --phase <phase>`；单场景：`python -m app.qa.duel --only <scenario>`。",
        "",
    ]
    return lines


def _section_next(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["## 9. 下一步", ""]
    total = sum(len(s["ids"]) for s in steps)
    if not total:
        return lines + ["没有待办：本次 0 条 Finding。", ""]
    for step in steps:
        ids = list(step["ids"])
        lines += [f"### {step['label']}", ""]
        if not ids:
            lines += ["无。", ""]
            continue
        preview = ids[:24]
        lines.append("、".join(f"`{i}`" for i in preview) + (f" …共 {len(ids)} 条" if len(ids) > len(preview) else ""))
        lines.append("")
        titles = [t for t in step["titles"][:6] if t]
        if titles:
            lines += [f"- {_cut(t, 90)}" for t in titles]
            lines.append("")
    return lines


def _section_footer(run_dir: pathlib.Path) -> list[str]:
    return [
        "---",
        "",
        f"## 附：`{REPORT_JSON}` 字段",
        "",
        "```json",
        '{ "run_id": str, "generated_at": str,',
        '  "meta": {...}, "overview": {...}, "coverage": {...},',
        '  "findings": [ {id, severity, area, title, detail, evidence, repro, item_id} ],',
        '  "items": [ {id, kind, name, module, line, risk, plan, status, detail, extra, phase} ],',
        '  "phases": [ {phase, secs, counts, notes, items_total} ],',
        '  "turns": [ {scenario, index, role, text, latency_ms, meta, findings} ] }',
        "```",
        "",
        "约定：",
        "",
        "- 表格里长文本已截断（detail 160、evidence 200），**全文在 JSON**；JSON 里所有字符串同样过 `sandbox.mask()`。",
        "- 同一输入重复生成时，除顶层 `generated_at` 外 JSON 完全一致（排序全部显式指定）。",
        f"- 本报告由 `app/qa/report.py` 生成；未修改 `app/` 任何既有文件。产物目录 `{_desensitize_path(run_dir)}`。",
        "",
    ]


# ============================================================== 主体 ======


def write(
    run_dir: pathlib.Path,
    *,
    registry: Any = None,
    phases: dict | None = None,
    findings: list | None = None,
    turns: list | None = None,
    meta: dict | None = None,
) -> pathlib.Path:
    """汇总并落盘 `report.md` + `report.json`；返回 `report.md` 的路径。

    参数全部容忍缺失：`registry=None` / `phases={}` / `findings=None` / `turns=None`
    都会正常出报告，对应小节写「未执行」。
    """
    run_dir = pathlib.Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    phases_map: Mapping[str, Any] = phases if isinstance(phases, Mapping) else {}
    meta_map: Mapping[str, Any] = meta if isinstance(meta, Mapping) else {}
    run_id = _m(str(meta_map.get("run_id") or meta_map.get("run") or run_dir.name or "adv"))
    generated_at = time.strftime(_TIME_FMT)

    items = _collect_items(registry, phases_map)
    phase_map = _item_phase_map(phases_map)
    findings_provided = findings is not None
    findings_list = _norm_findings(findings)
    findings_source = "传入的 `findings`"
    if not findings_provided:
        # 编排脚本理论上会把各 phase 的 findings 汇总后传进来；万一只给了 registry+phases，
        # 各 PhaseResult 自带的 findings 也不该被扔掉。
        gathered: list[Any] = []
        for _name, obj in _phase_entries(phases_map):
            gathered.extend(_as_list(_get(obj, "findings", None)))
        if gathered:
            findings_list = _norm_findings(gathered)
            findings_provided = True
            findings_source = f"从各 phase 自带的 `findings` 汇总（{len(findings_list)} 条）"
    turns_list = _norm_turns(turns)
    coverage = _coverage(registry, phases_map, meta_map, items)
    overview = _overview(items, findings_list, coverage)
    env = _env(meta_map, run_id, run_dir)

    # ---- 阶段行 ----
    phase_rows: list[dict[str, Any]] = []
    for name, obj in _phase_entries(phases_map):
        ph_items = _items_of(obj)
        counts = _get(obj, "counts", None)
        if not isinstance(counts, Mapping):
            counts = _counts(ph_items)
        phase_rows.append({
            "phase": name,
            "items_total": len(ph_items) or sum(_int(v) for v in counts.values()),
            "by_status": {str(k): _int(v) for k, v in counts.items()},
            "secs": _phase_secs(obj),
            "notes": _m(str(_get(obj, "notes", "") or "")),
        })
    total_row = {
        "items_total": overview["items_total"],
        "by_status": overview["by_status"],
        "secs": round(sum(_num(r["secs"]) for r in phase_rows if r["secs"]), 2) if phase_rows else None,
        "notes": f"{len(phase_rows)} 个阶段",
    } if overview["items_total"] else None

    route_rows = _route_rows(items)
    turn_rows = _turn_rows(turns_list)
    steps = _next_steps(findings_list)

    # 真正"必须先看"的：优先真实缺陷，别让一堆 [已知现状] 的鉴权提示占满前三
    _known = lambda f: "[已知现状]" in str(_get(f, "title", ""))
    _p = [f for f in findings_list if str(_get(f, "severity", "")) == types.P0]
    _p += [f for f in findings_list
           if str(_get(f, "severity", "")) == types.P1 and not _known(f)]
    _p += [f for f in findings_list
           if str(_get(f, "severity", "")) == types.P1 and _known(f)]
    seen = set()
    top = []
    for f in _p:
        key = str(_get(f, "id", ""))
        if key in seen:
            continue
        seen.add(key)
        top.append(f)
        if len(top) >= 3:
            break
    blockers = [
        f"**[{_get(f, 'severity')}] {_m(str(_get(f, 'id', '')))}** {_cut(_get(f, 'title', ''), 80)}"
        + (f" —— 条目 `{_m(str(_get(f, 'item_id', '')))}`" if _get(f, "item_id", "") else "")
        for f in top
    ]
    headline = _headline(overview, top)

    # ---- md ----
    lines: list[str] = []
    lines += _header_lines(run_id, run_dir)
    lines += _section_headline(headline, overview, blockers)
    lines += _section_phases(phase_rows, total_row)
    lines += _section_coverage(coverage, items)
    lines += _section_findings(findings_list, provided=findings_provided, source=findings_source)
    lines += _section_unresolved(items, phase_map, registry)
    lines += _section_routes(route_rows, items)
    lines += _section_transcripts(turn_rows, turns_list)
    lines += _section_env(env)
    lines += _section_next(steps)
    lines += _section_footer(run_dir)

    md_body = "\n".join(sandbox.mask(line, limit=BIG) for line in lines) + "\n"
    md_path = run_dir / REPORT_MD
    md_path.write_text(md_body, encoding="utf-8")

    # ---- json（全量、脱敏、确定性排序）----
    items_json: list[dict[str, Any]] = []
    for it in items:
        row = _record_dict(it)
        row["phase"] = phase_map.get(str(row.get("id", "")), "")
        row.setdefault("detail", "")
        items_json.append(row)

    findings_json = [_record_dict(f) for f in findings_list]

    turns_json: list[dict[str, Any]] = [_record_dict(turn) for turn in turns_list]

    phases_json = [
        {
            "phase": row["phase"],
            "secs": row["secs"],
            "counts": dict(sorted(row["by_status"].items())),
            "items_total": row["items_total"],
            "notes": row["notes"],
        }
        for row in phase_rows
    ]

    payload = {
        "run_id": run_id,
        "meta": _m_deep(dict(meta_map)),
        "overview": _m_deep(overview),
        "coverage": _m_deep(coverage),
        "findings": findings_json,
        "items": items_json,
        "phases": phases_json,
        "turns": turns_json,
        "generated_at": generated_at,
    }
    json_path = run_dir / REPORT_JSON
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return md_path
