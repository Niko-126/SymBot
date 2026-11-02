"""人设卡编辑后端（panel/persona.html 用）。

挂在 `/v1/persona-card/*` 下：

- `GET  /v1/persona-card`          聚合当前生效人设（YAML 字段 + DB 人格向量 + 阶段表）
- `PUT  /v1/persona-card`          深合并改动 → 写 `DATA_ROOT/persona-active.yaml` → 热生效
- `GET  /v1/persona-card/export`   导出可直接下载的 JSON
- `POST /v1/persona-card/import`   导入导出的 JSON（同样的备份 + 热生效）
- `GET  /v1/persona-card/preview`  按好感度/家人角色算"她此刻怎么称呼你"

两条硬约束：

1. **源人设只读**：`persona/whale-girl.yaml` 永远不改，所有改动以"当前生效人设"为底
   深合并后写到 `DATA_ROOT/persona-active.yaml`；旧 active 先备份到
   `DATA_ROOT/persona-backup/`。
2. **人格向量的 min/max 只读**：只把 value 写进 DB 的 traits 表（clamp 到 min/max），
   和 `POST /v1/persona/traits` 走同一张表、同样留 `traits_log`。

鉴权照抄 `app/api/console.py` 的 `_console_auth`（这里不 import，避免循环依赖）：
没配 `console.token` 就只靠回环地址，配了就要求 `X-Console-Token`。
﻿‌﻿﻿‌‍‌‌‍﻿﻿‌‍﻿‌‍​﻿‌‍‍‌﻿‌​‍​‍‍‌﻿‍﻿﻿"""

from __future__ import annotations

import copy
import hmac
import math
import os
import pathlib
import shutil
import threading
from datetime import date, datetime
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core import config, db
from app.core.paths import DATA_ROOT
from app.core.runtime import get as get_runtime
from app.relation import engine

# ---------------------------------------------------------------- 路径与锁

ACTIVE_NAME = "persona-active.yaml"
BACKUP_DIRNAME = "persona-backup"
# 同一个进程里两个面板同时保存时，至少别把文件写成半截
_SAVE_LOCK = threading.Lock()

# 阶段表里这些 call_user 只是"说明词"，不是真称呼
_PLACEHOLDER_CALLS = {
    "",
    "你",
    "「你」",
    "他的名字",
    "小名",
    "你定的称呼",
    "只有你们两个人用的称呼",
}

# 阶段 → 挑哪一组锚点当示例
_SCENE_BY_STAGE = {
    "observer": "日常",
    "swimmer": "关心",
    "echo": "不高兴",
    "samefreq": "被撒娇",
    "lover": "深层",
    "bond": "深层",
}

_SCALAR_FIELDS = ("name", "tagline", "species", "age_label", "self_ref", "world", "appearance")
_BOTTOM_LINE_FIELDS = ("principle", "repair", "floor", "forbidden")


def _active_path() -> pathlib.Path:
    return DATA_ROOT / ACTIVE_NAME


def _backup_dir() -> pathlib.Path:
    return DATA_ROOT / BACKUP_DIRNAME


def _assert_under_data_root(path: pathlib.Path) -> pathlib.Path:
    """只允许碰数据目录下的文件（人设卡唯一能写的两处就在这里生成）。"""
    root = DATA_ROOT.resolve()
    target = path.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="人设卡只允许读写数据目录里的文件") from None
    return target


# ---------------------------------------------------------------- 鉴权


def _persona_auth(request: Request) -> None:
    """和 console 一样的口径：配了口令才校验，没配就只靠回环地址。"""
    token = str(getattr(get_runtime().cfg.console, "token", "") or "")
    if not token:
        return
    given = (
        request.headers.get("x-console-token")
        or request.query_params.get("token")
        or ""
    )
    if not hmac.compare_digest(str(given), token):
        raise HTTPException(status_code=401, detail="控制台口令不对")


router = APIRouter(prefix="/v1/persona-card", dependencies=[Depends(_persona_auth)])


# ---------------------------------------------------------------- 读当前人设


def _source_label() -> str:
    """来源标签：当前生效文件就是 persona-active.yaml 才叫"已编辑"。"""
    active = _active_path()
    try:
        current = get_runtime().cfg.persona_file
        return "已编辑" if active.exists() and current.resolve() == active.resolve() else "内置"
    except OSError:
        return "内置"


def _traits_payload(persona: Any) -> dict[str, dict[str, float]]:
    """DB 里的当前值 + 人设里的 min/max。没人格行时退回 YAML 基线。"""
    rows = {str(r["dim"]): r for r in engine.get_traits()}
    out: dict[str, dict[str, float]] = {}
    for dim, spec in persona.traits().items():
        row = rows.get(str(dim))
        if row is not None:
            out[str(dim)] = {
                "value": float(row["value"]),
                "min": float(row["min"]),
                "max": float(row["max"]),
            }
        else:
            base = float(spec.get("baseline", 50))
            out[str(dim)] = {
                "value": base,
                "min": float(spec.get("min", base)),
                "max": float(spec.get("max", base)),
            }
    # 人设里已经删掉、但 DB 还留着的维度也带上，别让面板凭空少一格
    for dim, row in rows.items():
        if dim not in out:
            out[dim] = {
                "value": float(row["value"]),
                "min": float(row["min"]),
                "max": float(row["max"]),
            }
    return out


def _bottom_line_payload(persona: Any) -> dict[str, Any]:
    raw = persona.raw.get("bottom_line")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "principle": str(raw.get("principle") or "").strip(),
        "repair": [str(x) for x in (raw.get("repair") or [])],
        "floor": str(raw.get("floor") or "").strip(),
        "forbidden": [str(x) for x in (raw.get("forbidden") or [])],
    }


def _current_family_role() -> str:
    """家人角色交给 love.family_role（它可能正被队友改成配置驱动，所以懒加载）。"""
    try:
        from app.relation import love

        return str(love.family_role() or "")
    except Exception:  # noqa: BLE001 - 关系模块出问题不该拖垮人设卡
        return ""


def _family_call(role: str) -> str:
    role = str(role or "").strip()
    if role in ("生父", "爹", "爸爸", "父亲"):
        return "爹"
    if role in ("干爹", "干爸爸"):
        return "干爹"
    return ""


def _family_payload() -> dict[str, Any]:
    role = _current_family_role()
    return {
        "role": role,
        "roles": ["生父", "干爹"],
        "call_user": _family_call(role),
    }


def _card_payload() -> dict[str, Any]:
    persona = get_runtime().persona
    return {
        "key": persona.key,
        "name": persona.name,
        "tagline": persona.tagline,
        "species": persona.species,
        "age_label": persona.age_label,
        "self_ref": persona.self_ref,
        "world": persona.world,
        "appearance": persona.appearance,
        "traits": _traits_payload(persona),
        "speech": {"style": persona.style(), "rules": persona.rules()},
        "stages": [
            {
                "key": str(s.get("key") or ""),
                "label": str(s.get("label") or ""),
                "at": float(s.get("at", 0)),
                "call_user": str(s.get("call_user") or ""),
            }
            for s in persona.stages()
        ],
        "bottom_line": _bottom_line_payload(persona),
        "family": _family_payload(),
        "source": _source_label(),
        "editable": True,
    }


# ---------------------------------------------------------------- 合并 / 写盘


def _read_base_raw(rt: Any) -> dict[str, Any]:
    """以当前生效人设为底。源文件只读；读坏了退回内存里的 raw。"""
    src = rt.cfg.persona_file
    data: Any = None
    if src.exists():
        try:
            data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"读不了当前人设文件 {src.name}（{exc.__class__.__name__}）",
            ) from None
    if not isinstance(data, dict) or not data:
        data = copy.deepcopy(rt.persona.raw or {})
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=500, detail="当前人设是空的，没有可编辑的底板")
    return data


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _str_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"{label}必须是一行一条的列表")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise HTTPException(status_code=400, detail=f"{label}里出现了非文字内容")
        text = item.strip()
        if text:
            out.append(text)
    return out


def _normalize_stages(items: Any) -> list[dict[str, Any]]:
    """校验并规范阶段表：key 不能重、at 必须是 0–100 的数字、按 at 排序。"""
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="阶段表至少要有一行")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"阶段表第 {idx} 行不是对象")
        key = str(item.get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail=f"阶段表第 {idx} 行缺 key")
        if key in seen:
            raise HTTPException(status_code=400, detail=f"阶段 key 重复了：{key}")
        seen.add(key)
        try:
            at = float(item.get("at", 0))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"阶段「{key}」的 at（好感门槛）不是数字"
            ) from None
        if not math.isfinite(at) or at < 0 or at > 100:
            raise HTTPException(status_code=400, detail=f"阶段「{key}」的 at 要在 0–100 之间")
        row: dict[str, Any] = {
            "key": key,
            "label": str(item.get("label") or key).strip() or key,
            "at": int(at) if at.is_integer() else at,
            "call_user": str(item.get("call_user") or "").strip(),
        }
        if "behavior" in item:
            row["behavior"] = str(item.get("behavior") or "")
        if "unlock" in item:
            unlock = item.get("unlock")
            row["unlock"] = [str(x) for x in unlock] if isinstance(unlock, list) else []
        out.append(row)
    out.sort(key=lambda s: float(s["at"]))
    return out


def _check_traits(values: Any) -> list[tuple[str, float, float]]:
    """先校验再落库：返回 (维度, clamp 后的值, 旧值)。"""
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="traits 应该是 {维度: 数值} 的对象")
    rows = {str(r["dim"]): r for r in engine.get_traits()}
    out: list[tuple[str, float, float]] = []
    for dim, value in values.items():
        key = str(dim)
        row = rows.get(key)
        if row is None:
            raise HTTPException(status_code=400, detail=f"没有这个人格维度：{key}")
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"人格维度「{key}」的值不是数字"
            ) from None
        if not math.isfinite(num):
            raise HTTPException(status_code=400, detail=f"人格维度「{key}」的值不是有限数字")
        clamped = max(float(row["min"]), min(float(row["max"]), num))
        out.append((key, clamped, float(row["value"])))
    return out


def _write_traits(checked: list[tuple[str, float, float]]) -> None:
    """和 POST /v1/persona/traits 一致：clamp 后落库 + 留一条 traits_log。"""
    if not checked:
        return
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for dim, value, before in checked:
        db.execute("UPDATE traits SET value = ? WHERE dim = ?", (value, dim))
        db.execute(
            "INSERT INTO traits_log (ts, dim, before, after, cause) VALUES (?, ?, ?, ?, ?)",
            (now, dim, before, value, "人设卡编辑"),
        )


def _backup_active() -> pathlib.Path | None:
    active = _active_path()
    if not active.exists():
        return None
    src = _assert_under_data_root(active)
    bdir = _backup_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    bdir = _assert_under_data_root(bdir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = bdir / f"persona-active-{stamp}.yaml"
    n = 1
    while dest.exists():
        dest = bdir / f"persona-active-{stamp}-{n}.yaml"
        n += 1
    shutil.copy2(src, dest)
    return dest


def _write_active(raw: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path | None]:
    """先备份旧 active，再原子替换。"""
    active = _active_path()
    _assert_under_data_root(active)
    backup = _backup_active()
    text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    tmp = active.with_name(active.name + ".tmp")
    _assert_under_data_root(tmp)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, active)
    return active, backup


def _apply_bottom_line(raw: dict[str, Any], incoming: Any) -> None:
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="bottom_line 应该是对象")
    patch = {k: incoming[k] for k in _BOTTOM_LINE_FIELDS if k in incoming}
    for key in ("repair", "forbidden"):
        if key in patch:
            patch[key] = _str_list(patch[key], "边界词")
    old = raw.get("bottom_line")
    raw["bottom_line"] = _deep_merge(old if isinstance(old, dict) else {}, patch)


def _save(
    rt: Any,
    *,
    scalars: dict[str, Any],
    speech_style: Any = None,
    speech_rules: Any = None,
    stages: Any = None,
    bottom_line: Any = None,
    traits: Any = None,
) -> tuple[pathlib.Path, pathlib.Path | None]:
    """深合并 → 备份 → 写 active → 写 DB traits → 热生效。"""
    # traits 先校验，别让文件写完了才发现维度名不对
    checked_traits = _check_traits(traits) if traits else []

    with _SAVE_LOCK:
        raw = _read_base_raw(rt)
        for field, value in scalars.items():
            if value is None:
                continue
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{field} 必须是文字")
            if field == "name":
                value = value.strip()
                if not value:
                    raise HTTPException(status_code=400, detail="人设名字不能为空")
            raw[field] = value

        if speech_style is not None or speech_rules is not None:
            speech = raw.get("speech")
            speech = dict(speech) if isinstance(speech, dict) else {}
            if speech_style is not None:
                speech["style"] = _str_list(speech_style, "说话风格")
            if speech_rules is not None:
                speech["rules"] = _str_list(speech_rules, "说话规则")
            raw["speech"] = speech

        if stages is not None:
            normalized = _normalize_stages(stages)
            # 面板只编辑四项；behavior / unlock 没提交就沿用旧的
            old = {
                str(s.get("key")): s
                for s in (raw.get("stages") or [])
                if isinstance(s, dict) and s.get("key")
            }
            for row in normalized:
                prev = old.get(row["key"]) or {}
                if "behavior" not in row and prev.get("behavior"):
                    row["behavior"] = prev["behavior"]
                if "unlock" not in row and prev.get("unlock"):
                    row["unlock"] = list(prev["unlock"])
            raw["stages"] = normalized

        if bottom_line is not None:
            _apply_bottom_line(raw, bottom_line)

        active, backup = _write_active(raw)
        _write_traits(checked_traits)

        # 落盘 + 内存两处都切到 active：只 reload 不改 cfg 的话还会读回源人设
        config.save_section("persona", {"path": str(active)})
        rt.cfg.persona.path = str(active)
        rt.cfg.persona_override = active
        rt.reload_persona()

    return active, backup


# ---------------------------------------------------------------- 请求体


class PersonaCardIn(BaseModel):
    name: str | None = None
    tagline: str | None = None
    species: str | None = None
    age_label: str | None = None
    self_ref: str | None = None
    world: str | None = None
    appearance: str | None = None
    traits: dict[str, float] | None = None
    speech_style: list[str] | None = None
    speech_rules: list[str] | None = None
    stages: list[dict[str, Any]] | None = None
    bottom_line: dict[str, Any] | None = None


class PersonaImportIn(BaseModel):
    # 故意不标 dict：传数组/字符串时也要回 400 人话，而不是 422
    payload: Any = None


def _traits_from_export(value: Any) -> dict[str, float]:
    """导出格式是 {dim: {value,min,max}}；也接受 {dim: 数字}。min/max 一律忽略。"""
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="导入失败：traits 应该是对象")
    out: dict[str, float] = {}
    for dim, item in value.items():
        if isinstance(item, dict):
            item = item.get("value")
        try:
            out[str(dim)] = float(item)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"导入失败：人格维度「{dim}」的值不是数字"
            ) from None
    return out


def _validate_import(payload: Any) -> None:
    """缺 name / stages 直接 400，给人话。"""
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="导入失败：内容必须是一个 JSON 对象（导出文件最外层那个）"
        )
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="导入失败：缺 name（人设名字）")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise HTTPException(status_code=400, detail="导入失败：缺 stages（阶段表），至少要有一行")


# ---------------------------------------------------------------- 接口


@router.get("")
def get_card() -> dict[str, Any]:
    """聚合当前生效人设：YAML 字段 + DB 人格向量 + 阶段表 + 家人称呼。"""
    return _card_payload()


@router.put("")
def update_card(inp: PersonaCardIn) -> dict[str, Any]:
    """保存人设卡。只改 active 副本，源人设永远不动。"""
    patch = inp.model_dump(exclude_none=True)
    scalars = {k: patch[k] for k in _SCALAR_FIELDS if k in patch}
    active, backup = _save(
        get_runtime(),
        scalars=scalars,
        speech_style=patch.get("speech_style"),
        speech_rules=patch.get("speech_rules"),
        stages=patch.get("stages"),
        bottom_line=patch.get("bottom_line"),
        traits=patch.get("traits"),
    )
    return {
        "ok": True,
        "saved_to": str(active),
        "backup": str(backup) if backup else "",
        "card": _card_payload(),
    }


@router.get("/export")
def export_card() -> JSONResponse:
    """导出成 JSON，浏览器直接下载。只含人设字段，不含来源/家人这些运行态信息。"""
    card = _card_payload()
    payload: dict[str, Any] = {
        "format": "smd-persona-card/1",
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "key": card["key"],
        "name": card["name"],
        "tagline": card["tagline"],
        "species": card["species"],
        "age_label": card["age_label"],
        "self_ref": card["self_ref"],
        "world": card["world"],
        "appearance": card["appearance"],
        "traits": {dim: {"value": spec["value"], "min": spec["min"], "max": spec["max"]}
                   for dim, spec in card["traits"].items()},
        "speech": card["speech"],
        "stages": card["stages"],
        "bottom_line": card["bottom_line"],
    }
    filename = f"persona-card-{card['key']}-{date.today().isoformat()}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
def import_card(inp: PersonaImportIn) -> dict[str, Any]:
    """导入导出的 JSON：校验 → 深合并 → 备份 → 写 active → 热生效。"""
    payload = inp.payload
    _validate_import(payload)
    _normalize_stages(payload.get("stages"))

    speech = payload.get("speech")
    speech = speech if isinstance(speech, dict) else {}
    style = payload.get("speech_style", speech.get("style"))
    rules = payload.get("speech_rules", speech.get("rules"))
    traits = payload.get("traits")
    active, backup = _save(
        get_runtime(),
        scalars={k: payload[k] for k in _SCALAR_FIELDS if isinstance(payload.get(k), str)},
        speech_style=style,
        speech_rules=rules,
        stages=payload.get("stages"),
        bottom_line=payload.get("bottom_line"),
        traits=_traits_from_export(traits) if traits else None,
    )
    return {
        "ok": True,
        "saved_to": str(active),
        "backup": str(backup) if backup else "",
        "ignored_trait_bounds": True,
        "card": _card_payload(),
    }


@router.get("/preview")
def preview(
    affinity: float = Query(60, ge=0, le=100),
    role: str = Query("", max_length=32),
) -> dict[str, Any]:
    """她此刻会怎么称呼你 + 当前阶段 + 一句示例（不调模型）。"""
    persona = get_runtime().persona
    stage = engine.stage_payload(persona, float(affinity))
    raw_call = str(stage.get("call_user") or "").strip()

    family = str(role or "").strip() or _current_family_role()
    family_call = _family_call(family)
    if family_call:
        call_user = family_call
    elif raw_call in _PLACEHOLDER_CALLS:
        call_user = "你"
    else:
        call_user = raw_call

    scene = "关心" if family_call else _SCENE_BY_STAGE.get(str(stage.get("stage")), "日常")
    anchors: list[str] = []
    for name in (scene, "日常", "关心"):
        anchors = persona.anchors([name], per_scene=4)
        if anchors:
            break
    if not anchors:
        anchors = persona.all_quotes() or persona.style()

    sample = anchors[0] if anchors else ""
    if call_user and call_user not in ("你", "") and sample:
        # 只在前面加称呼：直接替换句中的"你"会把语义弄拧
        sample = f"{call_user}，{sample}"

    return {
        "call_user": call_user,
        "call_user_raw": raw_call,
        "stage_key": str(stage.get("stage") or ""),
        "stage_label": str(stage.get("stage_label") or ""),
        "sample_line": sample,
        "affinity": float(affinity),
        "role": family,
        "source": _source_label(),
    }
