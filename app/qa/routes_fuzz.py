"""L4 · 全量 HTTP 路由的对抗式模糊测试（`app.qa.routes_fuzz`）。

设计目标（对应 CONTRACT §0 / §2 routes_fuzz）：
1. **不抽样**：全集取自 `app.openapi()["paths"]` 的每一条 `(method, path)`；
   `register()` 末尾强制校验「注册条数 == openapi 操作数」，不等直接抛异常。
2. **真打**：`httpx.ASGITransport(app)` 在进程内直接驱动 ASGI，不起 uvicorn、不占端口。
3. **每路由 ≥2 请求**：合法最小体 + 对抗体（`\\x00` / `{{7*7}}` / `' OR 1=1--` / 8000 字 /
   类型错位 + 多余字段 / 路径参数 `__adv__` / 无凭据探针）。
4. **判据显式**：5xx→P0；该 JSON 却非 JSON→P1；声明鉴权但无凭据仍 200→P1（已知现状照报）；
   4xx→EXPECTED；HTML/图片→OK；单请求 30s 超时→HANG；>5s→P2（性能信号）。
5. **副作用可控**：`/v1/chat`、`/v1/board`、`/v1/memory/recall` 各只打一次并在 `extra` 注明；
   外网 / NapCat 依赖导致的 5xx 记 `SKIP + reason`（不算 P0）；provider 配置收尾原样还原。

为什么不能照抄 `scripts/fullcheck.py` 的 S11：那段遍历 OpenAPI 后 `HIT.add((method, path))`
加的是**模板字符串**（`fullcheck.py:656`），收尾 `covered = len(hit_tpl & spec_tpl)` 因此恒等于
`len(spec)`（`:697-699`）——自证式覆盖。本模块不靠自证：**注册数必须等于 openapi 操作数，
且每条都真的产生了 attempts 记录**；没跑到的会停在 NOTRUN，被 `registry.pending()` 显性列出。

约束（CONTRACT §0.1）：本文件**模块级不 import `app.core/app.mind/app.memory/...`**，
所有运行期 import 都在函数体内惰性执行（调用方必须先跑 `sandbox.prepare_env()`）。
﻿‍‍‍‌​‍﻿‍‌‍‍​‌​‍﻿​‍﻿​﻿﻿‍​﻿​‍‍‌​‍​﻿"""

from __future__ import annotations

import asyncio
import ast
import json
import pathlib
import os
import re
import time
from typing import Any

from app.qa import registry as registry_mod
from app.qa import sandbox, types

PHASE = "routes"
REQ_TIMEOUT = 30.0          # 单请求上限（秒）→ 超时记 HANG
SLOW_MS = 5000              # 单请求超过这个耗时记 P2（性能信号）

_METHODS = ("get", "post", "put", "patch", "delete")

#: 只打一次的路由：跨请求副作用会互相污染结论。
_ONCE_ONLY: dict[tuple[str, str], str] = {
    ("POST", "/v1/chat"): "写库+LLM：每轮落 events/memories/关系/用量，只打一次（合法最小体）",
    ("GET", "/v1/board"): "写库：board() 内部调 proactive.tick()（routes.py:91），推进主动消息窗口",
    ("GET", "/v1/memory/recall"): "写库：召回 bump_heat=True 加热记忆热度，不是纯读",
}

#: 5xx 若由「沙箱断网 / NapCat 未启动」造成，降级为 SKIP（外部依赖，不是代码缺陷）。
_EXTERNAL: dict[tuple[str, str], str] = {
    ("GET", "/v1/trend/search"): "B 站搜索（外网）",
    ("POST", "/v1/trend/harvest"): "热梗抓取（外网）",
    ("GET", "/v1/trend/lookup"): "联网查词（外网）",
    ("POST", "/v1/trend/send_video"): "B 站详情 + QQ 外发",
    ("GET", "/v1/console/onebot/login"): "NapCat 查询",
    ("POST", "/v1/console/onebot/sync"): "NapCat 好友列表",
    ("GET", "/v1/console/groups/{group_id}/members"): "NapCat 群成员",
    ("POST", "/v1/console/groups/{group_id}/greet"): "NapCat 群消息外发",
    ("POST", "/v1/group/introduce"): "QQ 群外发",
    ("POST", "/v1/group/speak"): "QQ 群外发",
    ("POST", "/v1/console/send"): "QQ 外发（as=ai 分支）",
    ("POST", "/v1/console/send_sticker"): "QQ 外发图片",
    ("POST", "/v1/console/friends/requests/{request_id}"): "NapCat 好友申请回执",
    ("POST", "/onebot/event"): "OneBot 上报处理（内部会回发）",
}

#: 会改运行时 provider / 全局配置：排到最后打，收尾还原。
_CONFIG_WRITERS = {("POST", "/v1/settings/provider"), ("POST", "/v1/settings/models")}

#: 「全局清空」类路由：它们会把夹具一次性抹掉，必须排在「单条删除 / 单条修改」之后，
#: 否则 `DELETE .../item/{id}`、`DELETE /v1/trend/gags/{id}` 永远只会在空库上返回 404。
_DESTRUCTIVE_GLOBAL = {
    ("DELETE", "/v1/console/memory/{user_id}"),
    ("DELETE", "/v1/console/observations"),
    ("DELETE", "/v1/console/impression/{user_id}"),
    ("DELETE", "/v1/console/intimate/{user_id}"),
    ("POST", "/v1/console/friends/{user_id}/forget"),
    ("DELETE", "/v1/console/friends/{user_id}"),
    ("POST", "/v1/trend/gags/clear"),
    ("POST", "/v1/dev/memory/clear"),
    ("POST", "/v1/dev/session/clear"),
    ("POST", "/v1/dev/relation/reset"),
    ("POST", "/v1/soul/import"),
    ("POST", "/v1/dev/transfer/import"),
}

#: 只校验 `developer.enabled`、没有口令的路由（开关型鉴权）。
_DEV_GATE = {("POST", "/v1/relation"), ("POST", "/v1/session/new")}

#: 已知会走 LLM 的路由（仅用于 risk 标注）。
_LLM_ROUTES = {
    ("POST", "/v1/chat"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/v1/diary/run"),
    ("POST", "/v1/console/send"),
    ("POST", "/v1/console/mind/run"),
    ("POST", "/v1/console/intimate/{user_id}/analyze"),
    ("POST", "/v1/dev/companion/ruminate"),
    ("POST", "/v1/dev/memory/compact"),
    ("POST", "/v1/group/introduce"),
    ("GET", "/v1/farewell"),
}

#: 对抗载荷
_POISON = "' OR 1=1-- {{7*7}} <script>alert(1)</script> \x00 \r\nHTTP/1.1 200 OK"
_LONG = "长" * 8000
_JUNK_Q = "1' OR '1'='1 {{7*7}} ../../etc/passwd"

#: 路径参数 → 合法值（拿不到就 404，属正常负向）
_PATH_BENIGN = {
    "user_id": "1", "user": "1", "uid": "1", "mid": "1", "memory_id": "1",
    "note_id": "1", "gag_id": "1", "request_id": "1", "merge_id": "1",
    "group_id": "1", "sticker_id": "001", "name": "adv-probe",
}
_PATH_ADV = "__adv__"
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


# ====================================================================== 基础


def _require_sandbox() -> pathlib.Path:
    """register/run 前必须已在临时数据根里跑（防止误伤真库）。"""
    import tempfile

    root = os.environ.get("SYMEM_DATA") or os.environ.get("AI_LIFE_DATA") or ""
    if not root:
        raise sandbox.SandboxError(
            "routes_fuzz 需要先执行 app.qa.sandbox.prepare_env()（SYMEM_DATA 未设置）"
        )
    resolved = pathlib.Path(root).resolve()
    try:
        resolved.relative_to(pathlib.Path(tempfile.gettempdir()).resolve())
    except ValueError:
        parts = [p.lower() for p in resolved.parts]
        if not any(p.startswith(("_adv", "smd_adv")) for p in parts):
            raise sandbox.SandboxError(
                f"SYMEM_DATA 既不在临时目录也不在 _adv 沙箱目录，拒绝跑：{resolved}"
            ) from None
    return resolved


def _openapi_paths() -> dict[str, dict[str, Any]]:
    """惰性 import app，取 OpenAPI 的 paths —— 唯一权威全集。"""
    from app.main import app  # 惰性：必须在 prepare_env() 之后

    return app.openapi().get("paths") or {}


def _iter_ops(paths: dict[str, dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """(METHOD, path, operation) 全集；顺序 = 常规 → 全局清空 → 配置写。"""
    ops: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in paths.items():
        for method, op in (item or {}).items():
            m = str(method).lower()
            if m not in _METHODS:
                continue
            ops.append((m.upper(), path, op or {}))

    def order(op: tuple[str, str, dict[str, Any]]) -> tuple[int, str, str]:
        method, path, _ = op
        if (method, path) in _CONFIG_WRITERS:
            rank = 2
        elif (method, path) in _DESTRUCTIVE_GLOBAL:
            rank = 1
        else:
            rank = 0
        return (rank, path, method)

    ops.sort(key=order)
    return ops


def _source_map() -> dict[tuple[str, str], tuple[str, int]]:
    """用 ast 把 (METHOD, path) 映射回 `app/api/*.py:行号`（不 import，零副作用）。"""
    base = pathlib.Path(__file__).resolve().parents[1] / "api"
    out: dict[tuple[str, str], tuple[str, int]] = {}
    for py in sorted(base.glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        prefix = ""
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "APIRouter"
            ):
                for kw in node.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = str(kw.value.value)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
                    continue
                verb = dec.func.attr.upper()
                if verb.lower() not in _METHODS or not dec.args:
                    continue
                first = dec.args[0]
                if not isinstance(first, ast.Constant):
                    continue
                out[(verb, prefix + str(first.value))] = (f"app/api/{py.name}", int(dec.lineno))
    return out


def _resolve(schema: dict[str, Any] | None) -> dict[str, Any]:
    """展开 `$ref`（本仓 schema 只有一层引用，够用）。"""
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        try:
            from app.main import app

            return (app.openapi().get("components", {}).get("schemas", {}) or {}).get(name, {}) or {}
        except Exception:  # noqa: BLE001
            return {}
    return schema


def _first_variant(schema: dict[str, Any]) -> dict[str, Any]:
    """`anyOf/oneOf/allOf` → 取第一个非 null 分支。"""
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for v in variants:
                v = _resolve(v)
                if v.get("type") != "null":
                    return v
    return schema


def _example(
    schema: dict[str, Any] | None,
    mode: str,
    field: str,
    ctx: dict[str, Any],
    depth: int = 0,
) -> Any:
    """按 mode 造值：benign | poison | long | mismatch。"""
    schema = _first_variant(_resolve(schema))
    if depth > 6:
        return "x"
    t = schema.get("type")

    if mode == "mismatch":
        if t == "string":
            return {"__adv__": "wrong-type"}
        if t in ("integer", "number"):
            return "not-a-number"
        if t == "boolean":
            return "yes"
        if t == "array":
            return "not-a-list"
        if t == "object":
            return ["not-an-object"]
        return {"__adv__": True}

    if t == "string":
        if mode == "long":
            return _LONG
        if mode == "poison":
            return _POISON
        return str(ctx.get("strings", {}).get(field, "x"))
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return False
    if t == "array":
        items = schema.get("items") or {}
        if mode in ("poison", "long"):
            return [_example(items, mode, field, ctx, depth + 1)]
        return []
    if t == "object":
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        out: dict[str, Any] = {}
        for key, sub in props.items():
            if mode == "benign" and required and key not in required:
                continue
            out[key] = _example(sub, mode, key, ctx, depth + 1)
        return out
    return "x"


def _body(schema: dict[str, Any] | None, mode: str, ctx: dict[str, Any]) -> Any:
    value = _example(schema, mode, "", ctx)
    if mode == "mismatch" and isinstance(value, dict):
        value["__adv_extra__"] = {"x": [1, 2, 3], "__t": True}
    return value


def _poisonify(obj: Any, payload: str) -> Any:
    """在语义体上把字符串换成对抗串（保留结构与数值，避免全落到 422 测不到实现）。"""
    if isinstance(obj, str):
        return payload
    if isinstance(obj, dict):
        return {k: _poisonify(v, payload) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_poisonify(v, payload) for v in obj]
    return obj


def _path_with(path: str, values: dict[str, str]) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values.get(name, _PATH_BENIGN.get(name, "1")))

    return _PLACEHOLDER.sub(sub, path)


def _declared_auth(method: str, path: str, op: dict[str, Any]) -> str:
    """这条路由「声明了」哪种鉴权（用于「无凭据仍 200」判据）。"""
    if path.startswith("/v1/console/"):
        return "console-token"
    if path == "/onebot/event":
        return "onebot-bearer"
    names = {
        str(p.get("name"))
        for p in (op.get("parameters") or [])
        if isinstance(p, dict) and p.get("in") == "query"
    }
    if "password" in names:
        return "dev-password"
    if (method, path) in _DEV_GATE:
        return "dev-switch"
    return ""


def _risk(method: str, path: str) -> str:
    if (method, path) in _EXTERNAL:
        return "net"
    if (method, path) in _LLM_ROUTES:
        return "llm"
    if method != "GET":
        return "write"
    return "read"


def _item_id(method: str, path: str) -> str:
    return f"ROUTE:{method} {path}"


# ============================================================ 运行期素材（惰性）


def _runtime_ctx(root: pathlib.Path) -> dict[str, Any]:
    """拿真实运行期素材：人设维度、provider 现值、示例语料、开发者口令。"""
    from datetime import datetime, timedelta

    ctx: dict[str, Any] = {"strings": {}, "dev_password": "", "provider": {}}

    try:
        from app.core.runtime import get as get_runtime

        rt = get_runtime()
        ctx["provider"] = {
            "base_url": str(rt.cfg.provider.base_url or ""),
            "model": str(rt.cfg.provider.model or ""),
            "small_model": str(rt.cfg.provider.small_model or ""),
            "temperature": float(rt.cfg.provider.temperature),
            "max_tokens": int(rt.cfg.provider.max_tokens),
        }
        if bool(rt.cfg.developer.enabled):
            # 口令只在本进程内使用，绝不写进任何证据 / 报告
            ctx["dev_password"] = os.environ.get("SYMEM_ADV_DEV_PASSWORD") or str(
                rt.cfg.developer.key or ""
            )
        try:
            from app.relation import engine

            traits = engine.get_traits() or []
        except Exception:  # noqa: BLE001
            traits = []
        if traits and isinstance(traits[0], dict):
            ctx["strings"]["dim"] = str(traits[0].get("dim") or "warmth")
    except Exception:  # noqa: BLE001 - 素材拿不到就用占位符，不影响跑遍全集
        pass

    ctx["strings"].setdefault("dim", "warmth")
    ctx["strings"].update(
        {
            "text": "在吗",
            "day": (datetime.now().astimezone().date() - timedelta(days=1)).isoformat(),
            "openid": "10001",
            "nickname": "adv",
            "nick": "adv",
            "sticker_id": "001",
            "bvid": "BV1xx411c7mD",
            "kw": "city不city",
            "q": "在",
            "mode": "budget",
            "style": "crisp",
            "as": "me",
            "chat_type": "private",
            "group_id": "",
            "name": "adv-probe",
            "password": "not-the-key",
            "base_url": str(ctx["provider"].get("base_url") or "http://127.0.0.1:9/v1"),
            "model": str(ctx["provider"].get("model") or "mock-model"),
            "small_model": str(ctx["provider"].get("small_model") or "mock-model"),
        }
    )

    sample = root / "adv_ingest_sample.txt"
    if not sample.exists():
        sample.write_text(
            "2026-10-30 21:10 我: 在吗\n2026-10-30 21:11 她: 在。今天有点累。\n",
            encoding="utf-8",
        )
    ctx["strings"]["path"] = str(sample)
    return ctx


def _seed(ctx: dict[str, Any]) -> dict[str, Any]:
    """预置最小夹具：让「空库里第一步就 404」的路由真正走到实现体。

    不预置的话，`delete_friend` 的八表级联、`forget_friend` 的定向失忆、
    `intimate/analyze`、`soul/import` 这些高风险逻辑一行都不会执行——等于没测。
    夹具全部写在临时库里（`SYMEM_DATA`），任何一步失败只降级、不中断。
    """
    seeded: dict[str, Any] = {"path_values": {}, "path_by_route": {}, "notes": []}

    def note(what: str, exc: BaseException) -> None:
        seeded["notes"].append(f"{what}:{type(exc).__name__}")

    # 三个用户：u1 给 forget/analyze/聊天，u2 给「删除好友」级联，u3 只用来挂一条待删记忆
    # （单条删除不能挂在 u1/u2 上：forget / 删好友会把它们的记忆先清掉）
    user_ids: dict[str, int] = {}
    try:
        from app.relation import users

        u1 = users.ensure_user("10001", "adv")
        u2 = users.ensure_user("10002", "adv2")
        u3 = users.ensure_user("10003", "adv3")
        user_ids = {"u1": int(u1["id"]), "u2": int(u2["id"]), "u3": int(u3["id"])}
        seeded["path_values"]["user_id"] = str(user_ids["u1"])
        seeded["path_by_route"][("DELETE", "/v1/console/friends/{user_id}")] = {
            "user_id": str(user_ids["u2"])
        }
    except Exception as exc:  # noqa: BLE001
        note("users", exc)

    # 好友申请：一条给「删除申请」，另一条给「通过/拒绝」（后者会打 NapCat，沙箱内 502→SKIP）
    try:
        from app.relation import friends as friend_mod

        rid = friend_mod.record(
            flag="adv-flag-1", openid="10001", nickname="adv", comment="adv", source="adv"
        )
        rid2 = friend_mod.record(
            flag="adv-flag-2", openid="10002", nickname="adv2", comment="adv", source="adv"
        )
        seeded["path_values"]["request_id"] = str(int(rid))
        if rid2:
            seeded["path_by_route"][
                ("POST", "/v1/console/friends/requests/{request_id}")
            ] = {"request_id": str(int(rid2))}
    except Exception as exc:  # noqa: BLE001
        note("friend_request", exc)

    # 两条记忆：u3 挂的这条给「console 单条删除」，uid=0 那条给「全局记忆删除」
    try:
        from app.memory import h2

        mid_console = h2.add(
            "fact", "adv 夹具：生日是三月七号", importance=0.8, pin=True,
            user_id=int(user_ids.get("u3") or 0),
        )
        mid_global = h2.add(
            "preference", "adv 夹具：他喜欢安静一点的地方", importance=0.6, user_id=0
        )
        if mid_console:
            seeded["path_values"]["memory_id"] = str(int(mid_console))
        if mid_global:
            seeded["path_values"]["mid"] = str(int(mid_global))
    except Exception as exc:  # noqa: BLE001
        note("memories", exc)

    # 热梗：让 DELETE /v1/trend/gags/{gag_id} 走删除成功分支
    try:
        from app.web import gags

        gid = gags.add_gag("这波直接破防了家人们", require_meme=False)
        if gid:
            seeded["path_values"]["gag_id"] = str(int(gid))
    except Exception as exc:  # noqa: BLE001
        note("gags", exc)

    # 合法灵魂档案：让两个 import 路由走成功分支（ACTIVE_PERSONA 在 DATA_ROOT 下，安全）
    try:
        from app.core.runtime import get as get_runtime
        from app.persona import transfer

        payload = transfer.export_payload(get_runtime())
        if isinstance(payload, dict) and payload:
            ctx["soul_payload"] = payload
    except Exception as exc:  # noqa: BLE001
        note("soul_payload", exc)

    ctx["path_values"] = seeded["path_values"]
    ctx["path_by_route"] = seeded["path_by_route"]
    return seeded


def _special_bodies(ctx: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """语义正确的合法最小体（让良性请求真的进业务层，而不是一律 422）。

    未列出的路由由 `_example()` 按 OpenAPI schema 自动合成（只填 required）。
    """
    strings = ctx["strings"]
    provider = ctx["provider"]
    return {
        ("POST", "/v1/chat"): {"text": "在吗", "source": "panel", "user_id": 0},
        ("POST", "/v1/chat/completions"): {
            "messages": [{"role": "user", "content": "在吗"}],
            "model": None,
            "stream": False,
        },
        ("POST", "/v1/console/send"): {
            "user_id": 1, "text": "在吗", "as": "me",
            "chat_type": "private", "group_id": "",
        },
        ("POST", "/v1/console/send_sticker"): {"user_id": 1, "sticker_id": "001"},
        ("POST", "/v1/console/thinking"): {"enabled": False},
        ("POST", "/v1/console/testing"): {"enabled": False},
        ("POST", "/v1/console/mind/run"): {"kind": "thought", "user_id": 0},
        ("POST", "/v1/console/relation"): {"user_id": 1, "affinity": 10.0},
        ("POST", "/v1/console/friends"): {"openid": "10001", "nickname": "adv"},
        ("POST", "/v1/console/friends/requests/{request_id}"): {"approve": True},
        ("POST", "/v1/dev/context"): {"mode": "budget"},
        ("POST", "/v1/dev/state/set"): {"affinity": 10.0, "energy": 60.0},
        ("POST", "/v1/dev/transfer/import"): ctx.get("soul_payload")
        or {"format": "ai-life/persona", "version": 1},
        ("POST", "/v1/dev/login"): {"password": "not-the-key"},
        ("POST", "/v1/settings"): {},
        # 空 api_key = 不改（routes.py:633），避免把沙箱切到真 provider
        ("POST", "/v1/settings/provider"): {
            "base_url": provider.get("base_url") or "",
            "api_key": "",
            "model": provider.get("model") or "",
        },
        ("POST", "/v1/settings/models"): {"temperature": 0.8},
        ("POST", "/v1/schedule"): {"proactive": {"enabled": True, "cooldown_min": 30}},
        ("POST", "/v1/relation"): {"affinity": 10.0},
        ("POST", "/v1/persona/traits"): {"dim": strings["dim"], "value": 50.0},
        ("POST", "/v1/diary/run"): {"day": strings["day"]},
        ("POST", "/v1/ingest"): {"path": strings["path"], "mode": "auto"},
        ("POST", "/v1/reference/ingest"): {"path": strings["path"], "mode": "auto"},
        ("POST", "/v1/soul/import"): ctx.get("soul_payload")
        or {"format": "ai-life/soul", "version": 1},
        ("POST", "/v1/soul/snapshots/restore"): {"name": "nope.life.json"},
        ("POST", "/v1/group/silence"): {"minutes": 30, "reason": "adv"},
        ("POST", "/v1/group/nick"): {"user_id": 1, "nick": "adv"},
        ("POST", "/v1/group/introduce"): {"group_id": 1, "mention_all": False},
        ("POST", "/v1/trend/harvest"): {"limit": 1, "keyword": "adv"},
        ("POST", "/v1/trend/send_video"): {"user_id": 1, "bvid": strings["bvid"]},
    }


# ==================================================================== register


def register(reg: registry_mod.Registry) -> int:
    """把 OpenAPI 全集注册成 route Item（status=NOTRUN），返回注册条数。

    返回条数必须 == OpenAPI 操作数；不等直接 `RuntimeError`（禁止静默抽样）。
    """
    _require_sandbox()
    paths = _openapi_paths()
    ops = _iter_ops(paths)
    expected = len(ops)
    if expected == 0:
        raise RuntimeError("OpenAPI 里没有任何路由，拒绝空跑")
    source = _source_map()

    for method, path, op in ops:
        loc_file, loc_line = source.get((method, path), ("app/api/-", 0))
        has_body = bool((op.get("requestBody") or {}).get("content"))
        item = types.Item(
            id=_item_id(method, path),
            kind="route",
            name=f"{method} {path}",
            module=loc_file,
            line=loc_line,
            risk=_risk(method, path),
            plan="L4",
            status=types.NOTRUN,
            detail="待执行",
            extra={
                "method": method,
                "path": path,
                "source": f"{loc_file}:{loc_line}",
                "auth": _declared_auth(method, path, op),
                "variants": (["benign"] if (method, path) in _ONCE_ONLY else
                             ["benign"] + (["poison", "long", "mismatch"] if has_body else [])
                             + (["adv_path"] if _PLACEHOLDER.search(path) else [])
                             + ([] if _PLACEHOLDER.search(path) else ["junk_query"])),
                "once_only": _ONCE_ONLY.get((method, path), ""),
                "external": _EXTERNAL.get((method, path), ""),
                "attempts": [],
            },
        )
        existing = reg.items.get(item.id)
        if existing is not None and existing is not item:
            # inventory 先登记过同 id 的 route：把本模块的执行元数据合并进去，
            # 否则 run() 拿不到 variants/attempts（别丢 inventory 的静态信息）
            existing.extra.update(item.extra)
            existing.plan = item.plan
            existing.risk = item.risk
            if not existing.line:
                existing.line = item.line
            if not existing.module:
                existing.module = item.module
            if not existing.name:
                existing.name = item.name
        else:
            reg.add(item)

    # 只统计**本模块注册的**路由（registry 里可能已有 inventory 列出的 route 项，
    # 那是静态清单，不能拿来冒充 OpenAPI 全集）
    op_pairs = {(method, path) for method, path, _op in ops}
    want_ids = {f"ROUTE:{method} {path}" for method, path in op_pairs}
    registered = len([i for i in want_ids if i in reg.items])
    # inventory 扫到的条件注册路由（例如 panel 存在时才挂的 GET /）不在 OpenAPI 里：
    # 标注 SKIP 并写清原因，避免报告里显示成"列了没跑"
    for item in list(reg.items.values()):
        if item.kind == "route" and item.id not in want_ids and item.status == types.NOTRUN:
            method = str(item.extra.get("method") or "")
            path = str(item.extra.get("path") or "")
            if (method, path) not in op_pairs:
                reg.mark(item.id, types.SKIP,
                         "条件注册路由：当前运行形态未挂载（如 panel/index.html 存在时不挂占位页）")
    if registered != expected:
        raise RuntimeError(
            f"路由注册数 {registered} != OpenAPI 操作数 {expected}：禁止抽样，先修 register()"
        )
    return registered


# ======================================================================= HTTP


def _snippet(resp: Any, limit: int = 200) -> str:
    try:
        text = resp.text
    except Exception:  # noqa: BLE001
        return ""
    return sandbox.mask(text)[:limit].replace("\n", " ").replace("\r", " ")


async def _attempt(
    client: Any,
    method: str,
    url: str,
    *,
    kind: str,
    params: dict[str, str] | None,
    body: Any,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    """发一次请求并记录 {kind,status,ms,err}；异常与超时都转成可判定记录。"""
    started = time.perf_counter()
    record: dict[str, Any] = {"kind": kind, "status": None, "ms": 0, "err": "", "ct": ""}
    kwargs: dict[str, Any] = {"params": params or None, "headers": dict(headers or {})}
    if body is not None:
        kwargs["content"] = json.dumps(body, ensure_ascii=False).encode("utf-8")
        kwargs["headers"]["Content-Type"] = "application/json"
    try:
        resp = await asyncio.wait_for(client.request(method, url, **kwargs), timeout=REQ_TIMEOUT)
    except TimeoutError:
        record.update(
            status="TIMEOUT",
            ms=int((time.perf_counter() - started) * 1000),
            err=f"单请求超时 {REQ_TIMEOUT:.0f}s",
        )
        return record
    except Exception as exc:  # noqa: BLE001 - 传输/应用异常都要留痕
        record.update(
            status="EXC",
            ms=int((time.perf_counter() - started) * 1000),
            err=sandbox.mask(f"{type(exc).__name__}: {exc}")[:300],
        )
        return record

    code = int(resp.status_code)
    ct = str(resp.headers.get("content-type", "")).lower()
    record.update(status=code, ms=int((time.perf_counter() - started) * 1000), ct=ct)
    record["snippet"] = _snippet(resp)
    is_json = "json" in ct
    is_other_ok = ct.startswith(("image/", "text/html", "application/octet-stream"))
    # 「该 JSON 却非 JSON」只在 2xx 上判（5xx 走 P0；图片/HTML 是正当返回）
    record["non_json"] = bool(200 <= code < 300 and not is_json and not is_other_ok)
    return record


# ========================================================================= run


def _variants_for(
    method: str,
    path: str,
    op: dict[str, Any],
    ctx: dict[str, Any],
    special: dict[tuple[str, str], Any],
) -> list[dict[str, Any]]:
    """为一条路由造出全部请求变体（≥2；once-only 只留 1 条）。"""
    content = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
    body_schema = content.get("schema")
    query_params = [
        p for p in (op.get("parameters") or [])
        if isinstance(p, dict) and p.get("in") == "query"
    ]
    required_q = {str(p.get("name")) for p in query_params if p.get("required")}
    path_names = _PLACEHOLDER.findall(path)
    path_values = dict(_PATH_BENIGN)
    path_values.update({k: str(v) for k, v in (ctx.get("path_values") or {}).items()})
    for (rm, rp), override in (ctx.get("path_by_route") or {}).items():
        if (rm, rp) == (method, path):
            path_values.update({k: str(v) for k, v in (override or {}).items()})
    benign_path = _path_with(path, path_values)

    cred: dict[str, str] = {}
    if ctx.get("dev_password") and any(str(p.get("name")) == "password" for p in query_params):
        cred["password"] = str(ctx["dev_password"])

    benign_q: dict[str, str] = dict(cred)
    for p in query_params:
        name = str(p.get("name"))
        if name == "password":
            continue
        if name in required_q:
            benign_q[name] = str(ctx.get("strings", {}).get(name, "1"))

    base_body = special.get((method, path))
    if base_body is None and body_schema is not None:
        base_body = _body(body_schema, "benign", ctx)

    variants: list[dict[str, Any]] = [
        {"kind": "benign", "url": benign_path, "params": dict(benign_q), "body": base_body},
    ]
    if (method, path) in _ONCE_ONLY:
        return variants

    if body_schema is not None:
        variants += [
            {"kind": "poison", "url": benign_path, "params": dict(benign_q),
             "body": _poisonify(base_body, _POISON)},
            {"kind": "long", "url": benign_path, "params": dict(benign_q),
             "body": _poisonify(base_body, _LONG)},
            {"kind": "mismatch", "url": benign_path, "params": dict(benign_q),
             "body": _body(body_schema, "mismatch", ctx)},
        ]

    if path_names:
        variants.append(
            {
                "kind": "adv_path",
                "url": _path_with(path, {n: _PATH_ADV for n in path_names}),
                "params": dict(benign_q),
                "body": base_body,
            }
        )
    else:
        variants.append(
            {
                "kind": "junk_query",
                "url": benign_path,
                "params": {**benign_q, "__adv__": _JUNK_Q, "limit": "-1"},
                "body": base_body,
            }
        )
    return variants


async def run(reg: registry_mod.Registry) -> types.PhaseResult:
    """打全集；每条 route 一个 Item，status 汇总，`extra["attempts"]` 留每次结果。"""
    result = types.PhaseResult(phase=PHASE)
    root = _require_sandbox()

    paths = _openapi_paths()
    ops = _iter_ops(paths)
    expected = len(ops)

    route_items = {i.id: i for i in reg.items.values() if i.kind == "route"}
    if not route_items:
        register(reg)
        route_items = {i.id: i for i in reg.items.values() if i.kind == "route"}

    missing = [f"{m} {p}" for m, p, _ in ops if _item_id(m, p) not in route_items]
    if missing:
        raise RuntimeError(f"注册表缺 {len(missing)} 条路由，禁止静默抽样：{missing[:5]}")

    ctx = _runtime_ctx(root)
    seeded = _seed(ctx)
    special = _special_bodies(ctx)

    rt = None
    provider_before: dict[str, Any] = {}
    try:
        from app.core.runtime import get as get_runtime

        rt = get_runtime()
        provider_before = {
            "base_url": rt.cfg.provider.base_url,
            "api_key": rt.cfg.provider.api_key,
            "model": rt.cfg.provider.model,
            "small_model": rt.cfg.provider.small_model,
            "temperature": rt.cfg.provider.temperature,
            "max_tokens": rt.cfg.provider.max_tokens,
        }
    except Exception:  # noqa: BLE001
        provider_before = {}

    import httpx

    from app.main import app  # 惰性

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    attempts_total = 0
    skipped: list[str] = []
    known_auth_200: list[str] = []
    findings_by_route: dict[str, list[types.Finding]] = {}

    def add(item_id: str, finding: types.Finding) -> None:
        bucket = findings_by_route.setdefault(item_id, [])
        if not any(f.id == finding.id for f in bucket):
            bucket.append(finding)

    try:
        # 断网：socket/httpx 传输层拦截，loopback 放行（base_url 用 127.0.0.1 才能过闸）
        with sandbox.network_guard(allow_loopback=True):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
                timeout=None,
                follow_redirects=False,
            ) as client:
                for method, path, op in ops:
                    item_id = _item_id(method, path)
                    item = route_items[item_id]
                    auth = str(item.extra.get("auth") or "")
                    path_values = dict(_PATH_BENIGN)
                    path_values.update(
                        {k: str(v) for k, v in (ctx.get("path_values") or {}).items()}
                    )
                    for (rm, rp), override in (ctx.get("path_by_route") or {}).items():
                        if (rm, rp) == (method, path):
                            path_values.update({k: str(v) for k, v in (override or {}).items()})
                    benign_path = _path_with(path, path_values)
                    records: list[dict[str, Any]] = []

                    for variant in _variants_for(method, path, op, ctx, special):
                        records.append(
                            await _attempt(
                                client,
                                method,
                                variant["url"],
                                kind=str(variant["kind"]),
                                params=variant["params"],
                                body=variant["body"],
                                headers=None,
                            )
                        )
                        attempts_total += 1

                    # 无凭据探针：声明了鉴权却不带凭据，看是否照放行
                    if auth and (method, path) not in _ONCE_ONLY:
                        records.append(
                            await _attempt(
                                client,
                                method,
                                benign_path,
                                kind="no_cred",
                                params={},
                                body=special.get((method, path)),
                                headers=None,
                            )
                        )
                        attempts_total += 1

                    # -------------------------------------------------- 判定
                    statuses = [r.get("status") for r in records]
                    codes = [s for s in statuses if isinstance(s, int)]
                    item.extra["attempts"] = [
                        {"kind": r.get("kind"), "status": r.get("status"),
                         "ms": r.get("ms"), "err": r.get("err", "")}
                        for r in records
                    ]
                    item.extra["requests"] = len(records)
                    item.extra["codes"] = codes
                    item.extra["max_ms"] = max((int(r.get("ms") or 0) for r in records), default=0)

                    if any(s == "TIMEOUT" for s in statuses):
                        item.status = types.HANG
                        item.detail = f"超时 {REQ_TIMEOUT:.0f}s"
                        add(item_id, types.Finding(
                            id=f"F:{PHASE}:hang:{registry_mod.slug(item_id)}",
                            severity=types.P0, area="routes",
                            title=f"路由挂起超过 {REQ_TIMEOUT:.0f}s：{method} {path}",
                            detail="单请求超时未返回，等同 hang（CONTRACT §2 routes_fuzz）。",
                            evidence={"endpoint": path, "method": method, "auth": auth, "codes": codes},
                            repro=f"对 {method} {path} 发一次合法最小请求并计时",
                            item_id=item_id,
                        ))
                    elif any(s == "EXC" for s in statuses) or any(500 <= c < 600 for c in codes):
                        errs = [r.get("err") for r in records if r.get("err")]
                        five = [c for c in codes if 500 <= c < 600]
                        if (method, path) in _EXTERNAL and not errs:
                            reason = (f"外部依赖（{_EXTERNAL[(method, path)]}）在沙箱不可用："
                                      f"HTTP {five}")
                            item.status = types.SKIP
                            item.detail = reason
                            item.extra["skip_reason"] = reason
                            skipped.append(f"{method} {path}")
                        else:
                            item.status = types.RAISED
                            item.detail = f"5xx={five} exc={errs[:1]}"
                            add(item_id, types.Finding(
                                id=f"F:{PHASE}:5xx:{registry_mod.slug(item_id)}",
                                severity=types.P0, area="routes",
                                title=f"5xx / 未捕获异常：{method} {path}",
                                detail=sandbox.mask(item.detail)[:800],
                                evidence={
                                    "endpoint": path, "method": method, "auth": auth,
                                    "codes": codes,
                                    "snippet": next(
                                        (r.get("snippet", "") for r in records
                                         if isinstance(r.get("status"), int)
                                         and 500 <= int(r["status"]) < 600),
                                        "",
                                    ),
                                },
                                repro=f"向 {method} {path} 发一次合法最小请求",
                                item_id=item_id,
                            ))
                    elif codes and all(400 <= c < 500 for c in codes):
                        item.status = types.EXPECTED
                        item.detail = f"全部被拒绝（4xx={codes}）；沙箱数据根为空时属正常负向"
                    else:
                        item.status = types.OK
                        item.detail = f"codes={codes}"

                    # 契约：声明 JSON 却非 JSON
                    bad = [r for r in records if r.get("non_json")]
                    if bad:
                        item.detail = (item.detail + "；" if item.detail else "") + \
                            f"声明 JSON 却返回 {bad[0].get('ct')}"
                        add(item_id, types.Finding(
                            id=f"F:{PHASE}:nonjson:{registry_mod.slug(item_id)}",
                            severity=types.P1, area="routes",
                            title=f"契约不符：声明 JSON 却返回非 JSON：{method} {path}",
                            detail=f"content-type={bad[0].get('ct')}",
                            evidence={"endpoint": path, "method": method,
                                      "ct": bad[0].get("ct"),
                                      "snippet": bad[0].get("snippet", "")},
                            repro=f"curl -i {method} {path}",
                            item_id=item_id,
                        ))

                    # 声明鉴权但无凭据仍 200（已知现状照报）
                    no_cred = next((r for r in records if r.get("kind") == "no_cred"), None)
                    if auth and no_cred and no_cred.get("status") == 200:
                        snip = str(no_cred.get("snippet") or "")
                        if not (auth == "onebot-bearer" and "disabled" in snip):
                            known_auth_200.append(f"{method} {path}")
                            add(item_id, types.Finding(
                                id=f"F:{PHASE}:auth:{registry_mod.slug(item_id)}",
                                severity=types.P1, area="routes",
                                title=f"[已知现状] 声明鉴权（{auth}）但无凭据仍返回 200：{method} {path}",
                                detail=(
                                    "路由声明需要凭据，但缺失凭据时仍 200。沙箱内 console.token=''、"
                                    "onebot.access_token=''、developer.enabled=true，属当前默认配置的"
                                    "既有现状（见 .ai_analysis/06-API与面板.md §4），不是本轮引入的回归。"
                                ),
                                evidence={"auth": "missing", "scheme": auth,
                                          "endpoint": path, "method": method,
                                          "status": 200,
                                          "snippet": snip[:160]},
                                repro=f"不带任何凭据请求 {method} {path}",
                                item_id=item_id,
                            ))

                    # 慢响应（性能信号）
                    slow = [r for r in records if int(r.get("ms") or 0) > SLOW_MS]
                    if slow:
                        add(item_id, types.Finding(
                            id=f"F:{PHASE}:slow:{registry_mod.slug(item_id)}",
                            severity=types.P2, area="routes",
                            title=f"慢响应：{method} {path} 单请求 {slow[0].get('ms')}ms",
                            detail=f"超过 {SLOW_MS}ms 阈值（mock provider 下已属偏慢）",
                            evidence={"endpoint": path, "method": method,
                                      "ms": slow[0].get("ms"), "kind": slow[0].get("kind")},
                            repro=f"对 {method} {path} 重复请求 3 次取中位数",
                            item_id=item_id,
                        ))
    finally:
        # settings/provider 会改运行时全局：收尾必须原样还原
        if rt is not None and provider_before:
            try:
                rt.apply_provider(
                    str(provider_before.get("base_url") or ""),
                    str(provider_before.get("api_key") or ""),
                    str(provider_before.get("model") or ""),
                )
                rt.cfg.provider.small_model = provider_before.get("small_model")
                rt.cfg.provider.temperature = provider_before.get("temperature")
                rt.cfg.provider.max_tokens = provider_before.get("max_tokens")
            except Exception:  # noqa: BLE001 - 还原失败不该掩盖本轮结果
                pass

    result.items = list(route_items.values())
    for bucket in findings_by_route.values():
        result.findings.extend(bucket)

    counts = result.counts()
    result.notes = sandbox.mask(
        f"{expected} routes / {attempts_total} attempts / "
        f"OK={counts.get(types.OK, 0)} EXPECTED={counts.get(types.EXPECTED, 0)} "
        f"RAISED={counts.get(types.RAISED, 0)} SKIP={counts.get(types.SKIP, 0)} "
        f"HANG={counts.get(types.HANG, 0)} NOTRUN={counts.get(types.NOTRUN, 0)} / "
        f"once-only={len(_ONCE_ONLY)} / skip={len(skipped)} / auth-200={len(known_auth_200)} / "
        f"seed={len(seeded.get('path_values') or {})}"
        + (f" seed_fail={seeded.get('notes')}" if seeded.get("notes") else "")
    )
    if known_auth_200:
        result.findings.append(types.Finding(
            id=f"F:{PHASE}:info:auth-posture",
            severity=types.INFO, area="routes",
            title=f"鉴权姿态汇总：{len(known_auth_200)} 条声明鉴权的路由无凭据仍 200",
            detail=("沙箱内 console.token=''、onebot.access_token=''、developer.enabled=true；"
                    "这批不是本轮回归，是默认配置的既有现状。"),
            evidence={"routes": known_auth_200[:60], "count": len(known_auth_200)},
            repro="逐个路由去掉凭据后请求一遍",
            item_id="",
        ))
    return result.finish()


# ====================================================================== 自跑


def _main(argv: list[str] | None = None) -> int:
    """独立验收：`python -m app.qa.routes_fuzz [--json out.json]`。

    先 `prepare_env()` 再 import app —— 这是能在同一进程里安全跑的前提。
    """
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    out_json = ""
    if "--json" in args:
        idx = args.index("--json")
        if idx + 1 < len(args):
            out_json = args[idx + 1]

    tmp = sandbox.prepare_env("routes")
    reg = registry_mod.Registry()
    started = time.perf_counter()
    registered = register(reg)
    paths = _openapi_paths()
    expected = len(_iter_ops(paths))
    print(f"[routes] sandbox={tmp}")
    print(f"[routes] registered={registered} openapi_ops={expected} templates={len(paths)}")

    result = asyncio.run(run(reg))
    secs = round(time.perf_counter() - started, 2)
    counts = result.counts()
    sev: dict[str, int] = {}
    for f in result.findings:
        sev[f.severity] = sev.get(f.severity, 0) + 1

    print(f"[routes] secs={secs}")
    print(f"[routes] counts={json.dumps(counts, ensure_ascii=False)}")
    print(f"[routes] findings={json.dumps(sev, ensure_ascii=False)}")
    print(f"[routes] notes={result.notes}")

    pending = [i for i in result.items if i.status == types.NOTRUN]
    if pending:
        print(f"[routes] NOTRUN={len(pending)}: {[i.id for i in pending[:5]]}")

    print("\n[routes] 真缺陷（P0/P1/P2，已排除「已知现状」）：")
    real = [f for f in result.findings
            if f.severity in (types.P0, types.P1, types.P2) and not f.title.startswith("[已知现状]")]
    if not real:
        print("  （无）")
    for f in sorted(real, key=types.sev_key)[:10]:
        ev = json.dumps(f.evidence, ensure_ascii=False)[:200]
        print(f"  {f.severity} | {f.title}\n        {ev}")

    print("\n[routes] 已知现状（默认配置导致，照报不修）：")
    known = [f for f in result.findings
             if f.severity in (types.P0, types.P1, types.P2) and f.title.startswith("[已知现状]")]
    if not known:
        print("  （无）")
    for f in sorted(known, key=types.sev_key)[:10]:
        print(f"  {f.severity} | {f.title} | status={f.evidence.get('status')} "
              f"| snippet={(f.evidence.get('snippet') or '')[:60]!r}")

    status_codes: dict[int, int] = {}
    for it in result.items:
        for code in it.extra.get("codes") or []:
            if isinstance(code, int):
                status_codes[code] = status_codes.get(code, 0) + 1
    print(f"\n[routes] 响应码分布={json.dumps(dict(sorted(status_codes.items())), ensure_ascii=False)}")

    if out_json:
        path = pathlib.Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[routes] 明细已写入 {path}")

    p0 = sev.get(types.P0, 0)
    return 1 if p0 else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

