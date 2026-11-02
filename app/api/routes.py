from __future__ import annotations

import pathlib
import time
from datetime import datetime, timedelta
from typing import Any

import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import __version__
from app.core.runtime import get as get_runtime
from app.life import clock, diary, human_life, proactive
from app.memory import ingest as ingest_mod
from app.core import channel
from app.core import config as config_mod
from app.core import continuity
from app.core import db
from app.core.paths import DATA_ROOT
from app.memory import cold, compress, reference
from app.persona import transfer
from app.mind import models as model_registry
from app.memory import store, h2
from app.mind import loop
from app.relation import engine
from app.relation import finale
from app.relation import session as session_mod

router = APIRouter()


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    source: str = "panel"
    # QQ 群多用户：归属的 user_id（0 = 单用户/共用）﻿​﻿﻿​​‍﻿‍​​‌‍‍‌​﻿​﻿﻿﻿​﻿‌‍﻿‍﻿‍‍‌‌﻿﻿
    user_id: int = 0


class IngestIn(BaseModel):
    path: str
    mode: str = "auto"


class OpenAIIn(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    stream: bool = False


# ---------------------------------------------------------------- 基础

@router.get("/v1/health")
def health() -> dict[str, Any]:
    rt = get_runtime()
    try:
        from app.adapters import qq as qq_adapter

        qq_status = qq_adapter.status()
    except Exception:  # noqa: BLE001
        qq_status = {}
    return {
        "ok": True,
        "environment": str(getattr(rt.cfg, "environment", "prod") or "prod"),
        "provider": "mock" if rt.cfg.provider.is_mock else "openai",
        "model": rt.cfg.provider.model,
        "active_model": model_registry.pick("chat_default").name,
        "force_model": str(getattr(rt.cfg.provider, "force_model", "") or ""),
        "context_window": int(rt.cfg.context.window),
        "reserve_output": int(rt.cfg.context.reserve_output),
        "max_tokens": int(rt.cfg.provider.max_tokens),
        "db": str(rt.cfg.db_file),
        "persona": rt.persona.name,
        "version": __version__,
        # 启动预检（base_url 与模型名是否同源）与运行期降级记录：出问题时先看这两段
        "preflight": getattr(rt, "preflight", {}) or {},
        # 通道能力位：官方 bot 没有表情/语音/群主动，OneBot 全量（客户端按这个渲染）
        "channel": channel.as_dict(),
        "qq": qq_status,
        "offline": {
            "count": int(db.kv_get("llm_offline_count", "0") or 0),
            "last": db.kv_get("llm_offline_last", ""),
            "reason": db.kv_get("llm_offline_reason", ""),
        },
    }


@router.get("/v1/board")
def board() -> dict[str, Any]:
    rt = get_runtime()
    persona = rt.persona
    relation = engine.get_relation()
    life = clock.get_state(persona)
    proactive.tick(rt, persona)
    stage = engine.stage_payload(persona, relation["affinity"])
    stats = store.memory_stats()

    return {
        "persona": {
            "name": session_mod.her_name(persona.name),
            "species": persona.species,
            "age_label": persona.age_label,
            "tagline": persona.tagline,
            "avatar": None,
        },
        "relation": {
            **relation,
            "aversion": session_mod.get_aversion(),
            "aversion_phase": session_mod.aversion_phase(session_mod.get_aversion()),
            "stage": stage["stage"],
            "stage_label": stage["stage_label"],
            "call_user": stage["call_user"],
            "next_stage": stage["next_stage"],
        },
        "life": life,
        # 紧凑状态卡（和 /v1/human_life/now 同一份结构）：面板/桌宠可直接拿来显示
        "human_life": human_life.snapshot(),
        "traits": engine.get_traits(),
        "memories": {
            **stats,
            "temperature": h2.stats(),
            "recent": store.recent_memories(5),
        },
        "history": [
            {"role": e["role"], "text": e["text"], "ts": e["ts"], "meta": e.get("meta")}
            for e in store.recent_events(20)
        ],
        "stage_table": stage["stage_table"],
        "reference": reference.status(),
        "session": session_mod.session_payload(persona),
        "context": {
            **compress.status(),
            "mode": rt.cfg.context.mode,
            "budget": rt.cfg.context.budget,
            "trigger": rt.cfg.context.trigger,
            "used": int(db.kv_get("last_used_tokens", "0") or 0),
        },
        "settings": model_registry.current_settings(),
        "stats": {
            **model_registry.usage_summary(
                int(session_mod.get(persona.name).get("seq") or 1), "hour", 24
            )["totals"],
            "session_seq": int(session_mod.get(persona.name).get("seq") or 1),
        },
        "proactive": proactive.pending_messages(0)[-6:],
        "farewell": finale.status(),
    }


@router.post("/v1/chat")
async def chat(inp: ChatIn) -> dict[str, Any]:
    channel.set_channel("panel")
    rt = get_runtime()
    # 面板是"不分场合"的主对话（chat_type 空）：保留摘要与群聊记忆（audit N2/N3）。
    # 但语气层需要知道是不是恋人，所以单独补 is_lover；panel uid=0 单人模式默认按恋人。
    from app.intimacy import gate as _igate

    uid = int(inp.user_id or 0)
    is_lover = _igate.is_lover_of(uid) if uid > 0 else True
    return await loop.chat_turn(
        rt, inp.text, source=inp.source, user_id=inp.user_id, is_lover=is_lover,
    )


@router.get("/v1/inspect")
def inspect() -> dict[str, Any]:
    rt = get_runtime()
    if not rt.last_prompt:
        raise HTTPException(status_code=404, detail="还没有发生过对话")
    return rt.last_prompt


@router.get("/v1/relation/log")
def relation_log(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"items": engine.recent_log(limit)}


# ---------------------------------------------------------------- 记忆

@router.get("/v1/memories")
def memories(
    q: str = "",
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    user_id: int | None = Query(None, description="只看某个人的记忆；不传 = 全部（面板会按归属分组）"),
) -> dict[str, Any]:
    items = store.search_memories(q=q, kind=kind, limit=limit)
    if user_id is not None:
        items = [it for it in items if int(it.get("user_id") or 0) == int(user_id)]
    # 面板要按"谁的记忆"分组，所以这里把归属名字一起带上：
    # 记忆本身只有 user_id，昵称在 qq_user；uid=0 是面板那条线（她自己）。
    owners: list[dict[str, Any]] = []
    seen: dict[int, str] = {0: "面板（她自己）"}
    try:
        for row in db.rows("SELECT id, nickname FROM qq_user"):
            seen[int(row["id"])] = str(row["nickname"] or f"uid {row['id']}")
    except Exception:  # noqa: BLE001 - 昵称表读不出来不该挡住列表
        pass
    counts: dict[int, int] = {}
    for it in items:
        uid = int(it.get("user_id") or 0)
        it["owner"] = seen.get(uid) or f"uid {uid}"
        counts[uid] = counts.get(uid, 0) + 1
    for uid, nick in seen.items():
        if counts.get(uid):
            owners.append({"user_id": uid, "nickname": nick, "count": counts[uid]})
    return {"items": items, "total": len(items), "owners": owners}


class ProviderIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class SettingsIn(BaseModel):
    mix_mode: bool | None = None
    deep_think: bool | None = None
    show_delta: bool | None = None
    llm_appraisal: bool | None = None
    style: str | None = None
    # 工具环节 / 声音环节的开关（决定 token 与算力消耗）
    tools_enabled: bool | None = None
    group_tools_enabled: bool | None = None
    web_enabled: bool | None = None
    vision_enabled: bool | None = None
    vision_model: str | None = None
    vision_base_url: str | None = None
    tts_enabled: bool | None = None
    tts_send_in_chat: bool | None = None
    tts_cache_ttl_days: int | None = None
    tts_cache_enabled: bool | None = None
    tts_base_url: str | None = None
    tts_voice: str | None = None
    tts_style: str | None = None
    # 连接与账号：启动器（只写 config.local.yaml）与家人名单（双写 kv）
    launcher_qq: str | None = None
    napcat_dir: str | None = None
    webui_port: int | None = None
    family_roles: list[dict] | None = None
    announce_godfather: bool | None = None


class DevStateIn(BaseModel):
    affinity: float | None = None
    trust: float | None = None
    attraction: float | None = None
    aversion: float | None = None
    energy: float | None = None
    mood: float | None = None


class DevLoginIn(BaseModel):
    password: str


class RelationIn(BaseModel):
    affinity: float | None = None
    trust: float | None = None
    attraction: float | None = None
    aversion: float | None = None


class PinIn(BaseModel):
    pinned: bool = True


@router.post("/v1/memories/{mid}/pin")
def pin_memory(mid: int, inp: PinIn) -> dict[str, Any]:
    """钉住 = 强制热记忆，永不衰减。这是"承诺"，不是"权重"。"""
    if not h2.pin(mid, inp.pinned):
        raise HTTPException(status_code=404, detail="没有这条记忆")
    return {"ok": True, "id": mid, "pinned": inp.pinned}


@router.get("/v1/memory/recall")
def memory_recall(q: str = Query(..., min_length=1), user_id: int = Query(-1),
                  chat_type: str = Query("")) -> dict[str, Any]:
    """调试口：看一句话会想起什么、想不起来什么。`user_id<0` = 全库检索。"""
    return h2.retrieve(q, user_id=user_id, chat_type=chat_type).as_dict()


@router.delete("/v1/memories/{mid}")
def forget(mid: int) -> dict[str, Any]:
    if not store.delete_memory(mid):
        raise HTTPException(status_code=404, detail="没有这条记忆")
    return {"ok": True}


@router.post("/v1/ingest")
def ingest_txt(inp: IngestIn) -> dict[str, Any]:
    rt = get_runtime()
    p = pathlib.Path(inp.path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"文件不存在：{inp.path}")
    try:
        return ingest_mod.ingest(p, rt.persona)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/v1/reference/ingest")
def reference_ingest(inp: IngestIn) -> dict[str, Any]:
    """参考语料：只写 ref_turns / ref_quotes，不碰 memories 与 relation。"""
    rt = get_runtime()
    p = pathlib.Path(inp.path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在：{inp.path}")
    files = sorted(p.glob("part*.txt")) if p.is_dir() else [p]
    if not files:
        raise HTTPException(status_code=400, detail="没有找到可导入的分卷")
    results = []
    for f in files:
        try:
            results.append(reference.ingest_reference(f, rt.persona.name))
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "path": str(f), "error": str(e)})
    return {
        "ok": True,
        "files": len(files),
        "results": results,
        "status": reference.status(),
    }


@router.get("/v1/reference/status")
def reference_status() -> dict[str, Any]:
    return {**reference.status(), "quotes_sample": reference.sample_quotes(8)}


def _dev_ok(password: str) -> bool:
    rt = get_runtime()
    key = str(rt.cfg.developer.key or "")
    # 口令留空 = 开发者接口整体关闭。空口令绝不能匹配空输入，否则等于无密码。
    return bool(rt.cfg.developer.enabled) and bool(key) and password == key


@router.post("/v1/dev/login")
def dev_login(inp: DevLoginIn) -> dict[str, Any]:
    rt = get_runtime()
    if not rt.cfg.developer.enabled:
        raise HTTPException(status_code=403, detail="开发者开关未开启")
    key = str(rt.cfg.developer.key or "")
    return {"ok": bool(key) and inp.password == key, "taps_required": rt.cfg.developer.logo_taps}


class ContextIn(BaseModel):
    mode: str = "budget"


@router.get("/v1/dev/context")
def dev_context(password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rt = get_runtime()
    return {
        "mode": rt.cfg.context.mode,
        "budget": rt.cfg.context.budget,
        "trigger": rt.cfg.context.trigger,
        "hard_cap": rt.cfg.context.hard_cap,
        "keep_recent": rt.cfg.context.keep_recent,
        "status": compress.status(),
    }


@router.post("/v1/dev/context")
def dev_context_set(inp: ContextIn, password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    if inp.mode not in ("budget", "ratio"):
        raise HTTPException(status_code=400, detail="mode 只能是 budget 或 ratio")
    rt = get_runtime()
    rt.cfg.context.mode = inp.mode
    db.kv_set("context_mode", inp.mode)
    return {"ok": True, "mode": inp.mode}


@router.post("/v1/dev/session/clear")
def dev_clear_session(password: str = Query("")) -> dict[str, Any]:
    """清空当前会话：对话、记忆、摘要、命名状态、关系。参考语料与锚点不动。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    with db.cursor() as cur:
        for table in ("events", "relation_log", "traits_log", "summaries"):
            cur.execute(f"DELETE FROM {table}")
        cur.execute("UPDATE relation SET affinity = 0, trust = 0, attraction = 0, aversion = 0 WHERE id = 1")
        cur.execute("UPDATE session SET name_locked = 0, name_pending = NULL, her_name = ? WHERE id = 1",
                    (get_runtime().default_name,))
    forgotten = h2.forget_all()
    continuity.record(
        "session_reset", "会话被清空：对话、记忆、关系一起归零", "dev",
        {"forgotten": int(forgotten or 0)},
    )
    for key in ("naming_step", "naming_tries", "memory_degraded", "last_used_tokens"):
        db.kv_set(key, "")
    db.kv_set("naming_step", "0")
    return {"ok": True}


@router.get("/v1/dev/transfer/export")
def dev_export_persona(password: str = Query("")) -> dict[str, Any]:
    """导出人设 + 记忆 + 对话到一个 json 文件，路径返回给前端。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rt = get_runtime()
    path = transfer.export_file(rt)
    return {"ok": True, "path": path}


@router.post("/v1/dev/transfer/import")
async def dev_import_persona(request: Request, password: str = Query(""),
                             keep_events: int = Query(1)) -> dict[str, Any]:
    """导入人设档案。先备份当前库，再覆盖。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"不是合法 JSON：{exc}") from exc
    rt = get_runtime()
    try:
        result = transfer.import_payload(rt, payload, keep_events=bool(keep_events))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rt.reload_persona()
    return result


@router.post("/v1/dev/state/set")
def dev_set_state(inp: DevStateIn, password: str = Query("")) -> dict[str, Any]:
    """开发者直接改属性：关系三轴、厌恶、精力、心情。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rel = engine.get_relation()
    engine.set_relation(
        affinity=inp.affinity if inp.affinity is not None else float(rel["affinity"]),
        trust=inp.trust if inp.trust is not None else float(rel["trust"]),
        attraction=inp.attraction if inp.attraction is not None else float(rel["attraction"]),
        reason="开发者修改",
    )
    if inp.aversion is not None:
        session_mod.set_aversion(inp.aversion)
    sets, args = [], []
    if inp.energy is not None:
        sets.append("energy = ?")
        args.append(float(inp.energy))
    if inp.mood is not None:
        sets.append("mood = ?")
        args.append(float(inp.mood))
    if sets:
        db.execute("UPDATE life_state SET " + ", ".join(sets) + " WHERE id = 1", tuple(args))
    return {"ok": True, "relation": engine.get_relation(),
            "life": {"energy": db.one("SELECT energy FROM life_state WHERE id=1")["energy"]}}


@router.post("/v1/dev/awake")
def dev_set_awake(on: int = Query(1), password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    db.kv_set("dev_awake", "1" if on else "0")
    return {"ok": True, "awake": bool(on)}


@router.post("/v1/dev/memory/clear")
def dev_clear_memories(password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    gone = h2.forget_all()
    db.execute("UPDATE relation SET aversion = 0 WHERE id = 1")
    continuity.record(
        "memory_forgotten", "她被清空了记忆（开发者操作）", "dev",
        {"count": int(gone or 0)},
    )
    return {"ok": True}


@router.post("/v1/dev/relation/reset")
def dev_reset_relation(password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    engine.set_relation(affinity=0, trust=0, attraction=0, reason="开发者重置")
    session_mod.set_aversion(0)
    continuity.record("relation_reset", "她对你的感觉被外部归零了", "dev")
    return {"ok": True}


@router.get("/v1/dev/anchors")
def dev_anchors(password: str = Query(""), limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rows = db.rows("SELECT text, scene, stage_min, freq FROM anchors WHERE enabled = 1 ORDER BY freq DESC LIMIT ?", (limit,))
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/v1/dev/companion/ruminate")
def dev_companion_ruminate(password: str = Query(""), user_id: int = Query(0),
                           limit: int = Query(2, ge=1, le=5),
                           dry: int = Query(0)) -> dict[str, Any]:
    """陪伴层独处：dry=1 只挑料（不写库），否则真跑一轮念头写回。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    from app.memory import companion

    if dry:
        return {"ok": True, "dry_run": True, "seeds": companion.idle(user_id, limit=limit)}
    return {"ok": True, **companion.ruminate(user_id, limit=limit)}


@router.post("/v1/dev/memory/compact")
async def dev_compact_cold(password: str = Query(""), dry: int = Query(0)) -> dict[str, Any]:
    """冷记忆压缩。先 dry=1 预览，确认后再真跑。"""
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rt = get_runtime()
    return await cold.compact_cold(rt, rt.persona, dry_run=bool(dry))


@router.get("/v1/dev/memory/cold")
def dev_cold_status(password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    return cold.stats()


@router.post("/v1/dev/memory/restore/{merge_id}")
def dev_restore_merge(merge_id: int, password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    return cold.restore(merge_id)


@router.get("/v1/dev/archives")
def dev_archives(password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    from app.relation.finale import GIRL_DIR

    items = []
    if GIRL_DIR.exists():
        for d in sorted(GIRL_DIR.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            md = d / "离开记录.md"
            items.append({
                "name": d.name,
                "has_record": md.exists(),
                "updated": md.stat().st_mtime if md.exists() else 0,
            })
    return {"items": items, "root": str(GIRL_DIR)}


@router.get("/v1/dev/archives/{name}")
def dev_archive(name: str, password: str = Query("")) -> dict[str, Any]:
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="名字不合法")
    from app.relation.finale import GIRL_DIR

    d = GIRL_DIR / name
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="没有这份档案")
    md = d / "离开记录.md"
    return {
        "name": name,
        "record": md.read_text(encoding="utf-8") if md.exists() else "",
        "files": [p.name for p in sorted(d.iterdir())],
    }


@router.get("/v1/dev/last")
def dev_last(password: str = Query("")) -> dict[str, Any]:
    """单步调试：上一轮的完整 prompt、预算分区，以及每一步的中间结果。

    只读，不改任何状态。没有聊过时 has_prompt=false。
    """
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rt = get_runtime()
    prompt = getattr(rt, "last_prompt", None)
    meta = getattr(rt, "last_meta", None)
    return {"ok": True, "has_prompt": bool(prompt), "prompt": prompt, "meta": meta}


@router.get("/v1/dev/logs/export")
def dev_export_logs(password: str = Query(""), limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """导出调试日志：事件、记忆、关系流水、上下文状态、上一轮 prompt/meta。

    写到 `exports/debug-<时间戳>.log.json`，返回路径。只读库，不改状态。
    """
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    from app.core.paths import DATA_ROOT

    rt = get_runtime()
    events = [dict(r) for r in db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
    events.reverse()
    memories = [dict(r) for r in db.rows("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))]
    payload = {
        "format": "ai-life/debug-log",
        "version": 1,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "health": health(),
        "settings": model_registry.current_settings(),
        "relation": engine.get_relation(),
        "session": session_mod.session_payload(rt.persona),
        "context": {
            **compress.status(),
            "mode": rt.cfg.context.mode,
            "budget": rt.cfg.context.budget,
            "used": int(db.kv_get("last_used_tokens", "0") or 0),
        },
        "events": events,
        "memories": memories,
        "relation_log": engine.recent_log(limit),
        "prompt": getattr(rt, "last_prompt", None),
        "meta": getattr(rt, "last_meta", None),
        "proactive": proactive.pending_messages(0)[-6:],
        "farewell": finale.status(),
    }
    out_dir = DATA_ROOT / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"debug-{stamp}.log.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "events": len(events),
        "memories": len(memories),
    }


@router.get("/v1/farewell")
async def farewell() -> dict[str, Any]:
    """她留下的那封信。生成中时 status=pending；pending 超过一轮就当场补生成。"""
    rt = get_runtime()
    st = finale.status()
    if st.get("status") == "pending":
        path = db.kv_get("letter_archive", "")
        if path and pathlib.Path(path).exists():
            try:
                await finale.compose(rt, pathlib.Path(path))
            except Exception:  # noqa: BLE001
                pass
    return finale.status()


@router.post("/v1/farewell/read")
def mark_farewell_read() -> dict[str, Any]:
    finale.clear_status()
    return {"ok": True}


def _mask(key: str) -> str:
    if not key:
        return ""
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "…"


def _mask_qq(qq: str) -> str:
    """QQ 号只回显打码值（例：391****758）；完整号永远不出接口。"""
    text = str(qq or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    if len(text) <= 8:
        return text[:1] + "****" + text[-2:]
    return text[:3] + "****" + text[-3:]


def _launcher_payload(rt) -> dict[str, Any]:
    """启动器段（面板「连接与账号」）：小号给打码 + 是否已配，目录与端口原样给。"""
    cfg = rt.cfg.launcher
    qq = str(getattr(cfg, "qq", "") or "").strip()
    try:
        port = int(getattr(cfg, "webui_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    return {
        "launcher": {
            "qq": _mask_qq(qq),
            "qq_set": bool(qq),
            "napcat_dir": str(getattr(cfg, "napcat_dir", "") or ""),
            "webui_port": port,
        }
    }


def _family_payload(rt) -> dict[str, Any]:
    """家人段：返回**实际生效**的名单（kv 优先，与 love.family_role 一致）。"""
    family = rt.cfg.family
    roles: list[dict[str, Any]] = []
    try:
        raw_kv = db.kv_get("family_roles", "") or ""
    except Exception:  # noqa: BLE001 - 列表接口读不到 kv 就退回配置，别 500
        raw_kv = ""
    if raw_kv.strip():
        try:
            parsed = json.loads(raw_kv)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            roles = config_mod.normalize_family_roles(parsed)
    if not roles:
        roles = config_mod.normalize_family_roles(family.roles)
    announce_godfather = bool(getattr(family, "announce_godfather", False))
    try:
        kv_flag = db.kv_get("announce_to_godfather", "")
    except Exception:  # noqa: BLE001
        kv_flag = ""
    if kv_flag != "":
        announce_godfather = kv_flag.strip() == "1"
    return {
        "family": {
            "roles": [
                {
                    "openid": str(item.get("openid") or ""),
                    "role": str(item.get("role") or ""),
                    "aliases": [str(alias) for alias in (item.get("aliases") or [])],
                }
                for item in roles
            ],
            "father_roles": [str(role) for role in (getattr(family, "father_roles", None) or [])],
            "announce_godfather": announce_godfather,
        }
    }


@router.get("/v1/settings/provider")
def get_provider() -> dict[str, Any]:
    rt = get_runtime()
    return {
        "base_url": rt.cfg.provider.base_url,
        "model": rt.cfg.provider.model,
        "has_key": bool(rt.cfg.provider.api_key),
        "key_masked": _mask(rt.cfg.provider.api_key),
        "is_mock": rt.cfg.provider.is_mock,
    }


@router.post("/v1/settings/provider")
def set_provider(inp: ProviderIn) -> dict[str, Any]:
    rt = get_runtime()
    before_model = str(rt.cfg.provider.model or "")
    before_base = str(rt.cfg.provider.base_url or "")
    key = inp.api_key.strip() or rt.cfg.provider.api_key
    config_mod.save_provider(inp.base_url, key, inp.model)
    rt.apply_provider(inp.base_url, key, inp.model)
    after_model = str(rt.cfg.provider.model or "")
    after_base = str(rt.cfg.provider.base_url or "")
    if after_model != before_model or after_base != before_base:
        continuity.record(
            "model_changed", f"{before_model or '(空)'} → {after_model or '(空)'}", "settings",
            {"before_base": before_base, "after_base": after_base},
        )
    return {**get_provider(), "saved": True}


def _tools_payload(rt) -> dict[str, Any]:
    """工具 / 声音两个环节的开关快照（面板设置页用）。"""
    tools = rt.cfg.tools
    voice = rt.cfg.voice
    return {
        "tools": {
            "enabled": bool(tools.enabled),
            "group_enabled": bool(tools.group_enabled),
            "web_enabled": bool(tools.web.enabled),
            "vision_enabled": bool(tools.vision.enabled),
            "vision_model": str(tools.vision.model or ""),
            "vision_base_url": str(tools.vision.base_url or ""),
            "vision_has_key": bool(tools.vision.api_key),
            "mocked": bool(rt.cfg.provider.is_mock),
        },
        "voice": {
            "enabled": bool(voice.enabled),
            "send_in_chat": bool(voice.send_in_chat),
            "base_url": str(voice.base_url or ""),
            "voice": str(voice.voice or ""),
            "style": str(voice.style or ""),
            "cache_ttl_days": int(voice.cache_ttl_days or 0),
            "cache_enabled": bool(voice.cache_enabled),
        },
    }


@router.get("/v1/settings")
def get_settings() -> dict[str, Any]:
    rt = get_runtime()
    return {**model_registry.current_settings(), "styles": model_registry.styles(),
            **_tools_payload(rt), **_launcher_payload(rt), **_family_payload(rt)}


@router.post("/v1/settings")
def set_settings(inp: SettingsIn) -> dict[str, Any]:
    rt = get_runtime()
    # 工具环节：先改内存（立刻生效），再落 config.local.yaml（重启还在）
    tools_fields = {
        "enabled": inp.tools_enabled,
        "group_enabled": inp.group_tools_enabled,
    }
    tools = rt.cfg.tools
    if inp.tools_enabled is not None:
        tools.enabled = bool(inp.tools_enabled)
    if inp.group_tools_enabled is not None:
        tools.group_enabled = bool(inp.group_tools_enabled)
    if inp.web_enabled is not None:
        tools.web.enabled = bool(inp.web_enabled)
        tools_fields["web"] = {"enabled": bool(inp.web_enabled)}
    vision_fields: dict[str, Any] = {}
    if inp.vision_enabled is not None:
        tools.vision.enabled = bool(inp.vision_enabled)
        vision_fields["enabled"] = bool(inp.vision_enabled)
    if inp.vision_model is not None:
        tools.vision.model = str(inp.vision_model)
        vision_fields["model"] = str(inp.vision_model)
    if inp.vision_base_url is not None:
        tools.vision.base_url = str(inp.vision_base_url)
        vision_fields["base_url"] = str(inp.vision_base_url)
    if vision_fields:
        tools_fields["vision"] = vision_fields
    if any(v is not None for v in tools_fields.values()):
        try:
            config_mod.save_section("tools", tools_fields)
        except OSError:
            pass

    # 声音环节
    voice_fields: dict[str, Any] = {}
    if inp.tts_enabled is not None:
        rt.cfg.voice.enabled = bool(inp.tts_enabled)
        voice_fields["enabled"] = bool(inp.tts_enabled)
    if inp.tts_send_in_chat is not None:
        rt.cfg.voice.send_in_chat = bool(inp.tts_send_in_chat)
        voice_fields["send_in_chat"] = bool(inp.tts_send_in_chat)
    if inp.tts_cache_enabled is not None:
        rt.cfg.voice.cache_enabled = bool(inp.tts_cache_enabled)
        voice_fields["cache_enabled"] = bool(inp.tts_cache_enabled)
    if inp.tts_base_url is not None:
        rt.cfg.voice.base_url = str(inp.tts_base_url).strip()
        voice_fields["base_url"] = str(inp.tts_base_url).strip()
    if inp.tts_voice is not None:
        rt.cfg.voice.voice = str(inp.tts_voice).strip() or "feiyu"
        voice_fields["voice"] = rt.cfg.voice.voice
    if inp.tts_style is not None:
        rt.cfg.voice.style = str(inp.tts_style).strip() or "cute"
        voice_fields["style"] = rt.cfg.voice.style
    if inp.tts_cache_ttl_days is not None:
        ttl = max(0, int(inp.tts_cache_ttl_days))
        rt.cfg.voice.cache_ttl_days = ttl
        voice_fields["cache_ttl_days"] = ttl
    if voice_fields:
        try:
            config_mod.save_section("voice", voice_fields)
        except OSError:
            pass

    # 启动器（小号 / NapCat 目录 / WebUI 端口）：私人信息，只写 config.local.yaml。
    # 小号留空 = "这次不改"（前端也只在填了新号时才发这个字段）。
    launcher_fields: dict[str, Any] = {}
    if inp.launcher_qq is not None:
        qq = str(inp.launcher_qq).strip()
        rt.cfg.launcher.qq = qq
        launcher_fields["qq"] = qq
    if inp.napcat_dir is not None:
        folder = str(inp.napcat_dir).strip()
        rt.cfg.launcher.napcat_dir = folder
        launcher_fields["napcat_dir"] = folder
    if inp.webui_port is not None:
        port = max(0, min(65535, int(inp.webui_port)))
        rt.cfg.launcher.webui_port = port
        launcher_fields["webui_port"] = port
    if launcher_fields:
        try:
            config_mod.save_section("launcher", launcher_fields)
        except OSError:
            pass

    # 家人名单：config.local.yaml 与 db kv 双写。family_role 热路径优先读 kv，
    # 所以改完立刻生效；配置那份保证重启后仍然在。
    family_fields: dict[str, Any] = {}
    if inp.family_roles is not None:
        roles = config_mod.normalize_family_roles(inp.family_roles)
        rt.cfg.family.roles = roles
        family_fields["roles"] = roles
        try:
            db.kv_set("family_roles", json.dumps(roles, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - kv 写失败还有配置文件兜底
            pass
    if inp.announce_godfather is not None:
        flag = bool(inp.announce_godfather)
        rt.cfg.family.announce_godfather = flag
        family_fields["announce_godfather"] = flag
        try:
            db.kv_set("announce_to_godfather", "1" if flag else "0")
        except Exception:  # noqa: BLE001 - 同上
            pass
    if family_fields:
        try:
            config_mod.save_section("family", family_fields)
        except OSError:
            pass
    if launcher_fields or family_fields:
        # 只刷新模块缓存，不替换 rt.cfg（runtime 里还有 context.mode 之类的运行时值）。
        # 刷新后 love.family_role() / announce.fathers() 马上能看到新名单。
        try:
            config_mod.refresh()
        except Exception:  # noqa: BLE001 - 刷新失败不影响已经改好的内存值
            pass

    return {**model_registry.save_settings(inp.mix_mode, inp.style, inp.deep_think,
                                  inp.show_delta, inp.llm_appraisal), "styles": model_registry.styles(),
            **_tools_payload(rt), **_launcher_payload(rt), **_family_payload(rt)}


@router.get("/v1/stats")
def get_stats(
    bucket: str = Query("hour"),
    limit: int = Query(24, ge=1, le=400),
    scope: str = Query("session"),
) -> dict[str, Any]:
    """用量统计。scope=session 只看当前会话，scope=all 含已离开的人。"""
    rt = get_runtime()
    seq = int(session_mod.get(rt.default_name).get("seq") or 1)
    data = model_registry.usage_summary(None if scope == "all" else seq, bucket, limit)
    data["scope"] = scope
    data["session_seq"] = seq
    data["settings"] = model_registry.current_settings()
    data["history"] = model_registry.history()
    return data


@router.get("/v1/session")
def session_state() -> dict[str, Any]:
    rt = get_runtime()
    return session_mod.session_payload(rt.persona)


@router.post("/v1/relation")
def set_relation_dev(inp: RelationIn) -> dict[str, Any]:
    """手动设定好感/厌恶。只有开发者开关打开时才允许。"""
    rt = get_runtime()
    if not rt.cfg.developer.enabled:
        raise HTTPException(status_code=403, detail="开发者开关未开启")
    if inp.aversion is not None:
        session_mod.set_aversion(inp.aversion)
    rel = engine.set_relation(
        affinity=inp.affinity, trust=inp.trust, attraction=inp.attraction, reason="开发者手动设定"
    )
    return {
        "ok": True,
        "relation": rel,
        "aversion": session_mod.get_aversion(),
        "stage": engine.stage_payload(rt.persona, rel["affinity"]),
    }


@router.post("/v1/session/new")
def force_new_session() -> dict[str, Any]:
    """开发者强制结束当前关系并换一个人（会先归档）。"""
    rt = get_runtime()
    if not rt.cfg.developer.enabled:
        raise HTTPException(status_code=403, detail="开发者开关未开启")
    info = session_mod.terminate(rt.persona, reason="manual")
    return {"ok": True, **info, "session": session_mod.session_payload(rt.persona)}


@router.get("/v1/soul/letter-template")
def letter_template() -> dict[str, Any]:
    """记忆交接信的空白模板。用户复制出去改，然后走 /v1/ingest 回来。"""
    rt = get_runtime()
    text = rt.persona.letter_template()
    if not text:
        raise HTTPException(status_code=404, detail="这个人物包没有配置交接信模板")
    return {"version": int(rt.persona.memory_letter.get("version", 1)), "text": text}


# ---------------------------------------------------------------- M5 设置面板

def _soul_snapshot_dir() -> pathlib.Path:
    d = DATA_ROOT / "data" / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TraitIn(BaseModel):
    dim: str
    value: float


class ScheduleProactiveIn(BaseModel):
    enabled: bool | None = None
    cooldown_min: int | None = None
    daily_max: int | None = None
    intimate: bool | None = None
    # 三道闸门：夜间静默区间 / 每天最多带几条念头 / 手动免打扰
    quiet_start: int | None = None
    quiet_end: int | None = None
    thought_daily_max: int | None = None
    dnd_minutes: int | None = None
    clear_dnd: bool | None = None


class ScheduleIn(BaseModel):
    proactive: ScheduleProactiveIn | None = None
    # 「她的一天」总开关：None = 不动；True/False = 开/关人类语料接管
    human_life: bool | None = None
    # 日型覆盖（桌宠/演示用）："" = 自动按星期判；也可 workday/restday/holiday/travel/special
    human_life_day_type: str | None = None


class ModelSettingsIn(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = None
    small_model: str | None = None


class SnapshotRestoreIn(BaseModel):
    name: str


@router.get("/v1/persona")
def get_persona_full() -> dict[str, Any]:
    """人物页：设定、人格向量、原话、反向锚点、阶段表、作息表。只读。"""
    rt = get_runtime()
    p = rt.persona
    affinity = float(engine.get_relation()["affinity"])
    return {
        "key": p.key,
        "name": p.name,
        "tagline": p.tagline,
        "age_label": p.age_label,
        "species": p.species,
        "self_ref": p.self_ref,
        "traits": engine.get_traits(),
        "quotes": p.all_quotes(),
        "negatives": p.negatives(),
        "style": p.style(),
        "stage_table": [
            {
                "key": s.get("key"),
                "label": s.get("label"),
                "at": s.get("at"),
                "call_user": s.get("call_user"),
            }
            for s in p.stages()
        ],
        "schedule": p.schedule(),
        "current_stage": engine.stage_payload(p, affinity),
    }


@router.post("/v1/persona/traits")
def set_persona_trait(inp: TraitIn) -> dict[str, Any]:
    """调人格向量。只允许落在人设给的 min/max 之间，每次留一条 traits_log。"""
    row = db.one("SELECT * FROM traits WHERE dim = ?", (inp.dim,))
    if row is None:
        raise HTTPException(status_code=404, detail="没有这个维度")
    before = float(row["value"])
    value = max(float(row["min_value"]), min(float(row["max_value"]), float(inp.value)))
    db.execute("UPDATE traits SET value = ? WHERE dim = ?", (value, inp.dim))
    db.execute(
        "INSERT INTO traits_log (ts, dim, before, after, cause) VALUES (?, ?, ?, ?, ?)",
        (
            datetime.now().astimezone().isoformat(timespec="seconds"),
            inp.dim,
            before,
            value,
            "设置面板手动校准",
        ),
    )
    return {"ok": True, "traits": engine.get_traits()}


@router.get("/v1/schedule")
def get_schedule() -> dict[str, Any]:
    """日程页：作息表 + 此刻状态 + 主动消息设置 + 人类语料接入情况。"""
    rt = get_runtime()
    return {
        "timezone": rt.cfg.life.timezone,
        "now": clock.get_state(rt.persona),
        "table": rt.persona.schedule(),
        "proactive": proactive.settings(),
        "human_life": human_life.status(),
    }


@router.post("/v1/schedule")
def set_schedule(inp: ScheduleIn) -> dict[str, Any]:
    if inp.proactive is None and inp.human_life is None and inp.human_life_day_type is None:
        raise HTTPException(status_code=400, detail="没有要改的内容")
    # 日型覆盖先校验再落笔：非法值直接 400，别写一半
    day_type_text: str | None = None
    if inp.human_life_day_type is not None:
        day_type_text = str(inp.human_life_day_type).strip()
        if day_type_text and day_type_text not in human_life.DAY_TYPES:
            raise HTTPException(
                status_code=400,
                detail="日型只能是 " + "/".join(human_life.DAY_TYPES) + "，或空串表示自动",
            )
    p = inp.proactive
    if p is not None:
        if p.enabled is not None:
            db.kv_set("proactive_enabled", "1" if p.enabled else "0")
        if p.cooldown_min is not None:
            db.kv_set("proactive_cooldown_min", str(max(1, min(240, int(p.cooldown_min)))))
        if p.daily_max is not None:
            db.kv_set("proactive_daily_max", str(max(0, min(50, int(p.daily_max)))))
        if p.intimate is not None:
            proactive.set_proactive_intimate(bool(p.intimate))
        if p.quiet_start is not None:
            db.kv_set("proactive_quiet_start", str(int(p.quiet_start) % 24))
        if p.quiet_end is not None:
            db.kv_set("proactive_quiet_end", str(int(p.quiet_end) % 24))
        if p.thought_daily_max is not None:
            db.kv_set("proactive_thought_max", str(max(0, min(20, int(p.thought_daily_max)))))
        if p.clear_dnd:
            proactive.clear_dnd(0)
        if p.dnd_minutes is not None and int(p.dnd_minutes) > 0:
            # 面板上这一下是"全局免打扰"（uid=0）：QQ 私聊那条线也会看它
            proactive.set_dnd(0, max(1, min(720, int(p.dnd_minutes))), "面板手动免打扰")
    if inp.human_life is not None:
        human_life.set_enabled(bool(inp.human_life))
    if day_type_text is not None:
        human_life.set_force_day_type(day_type_text)
    return {"ok": True, "schedule": get_schedule()}


@router.get("/v1/human_life/now")
def get_human_life_now() -> dict[str, Any]:
    """她此刻在干嘛（紧凑状态卡）：给桌宠 / QQ 机器人轮询用。

    没开开关 / 没语料也返回 200：enabled=false、ok=false、字段给 null，不报错。
    """
    return human_life.snapshot()


class DiaryRunIn(BaseModel):
    day: str = ""


@router.get("/v1/diary")
def get_diary() -> dict[str, Any]:
    """她的一天：最近几天的日记、今天是否已被提起。"""
    return diary.status()


@router.post("/v1/diary/run")
async def diary_run(inp: DiaryRunIn) -> dict[str, Any]:
    """手动补写某一天的日记（默认昨天）。设置页/调试用；同一天已写过则返回旧的。"""
    rt = get_runtime()
    day = inp.day.strip() or (clock.now().date() - timedelta(days=1)).isoformat()
    try:
        date = datetime.fromisoformat(day).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
    if date > clock.now().date():
        raise HTTPException(status_code=400, detail="不能写未来的日记")
    entry = await diary.compose_for(rt, rt.persona, date.isoformat())
    return {"ok": True, "entry": entry}


@router.get("/v1/settings/models")
def get_model_settings() -> dict[str, Any]:
    rt = get_runtime()
    provider = {
        **get_provider(),
        "small_model": rt.cfg.provider.small_model,
        "temperature": rt.cfg.provider.temperature,
        "max_tokens": rt.cfg.provider.max_tokens,
        "timeout_s": rt.cfg.provider.timeout_s,
    }
    return {
        "provider": provider,
        "styles": model_registry.styles(),
        "tasks": dict(model_registry.TASK_ROUTE),
        "task_labels": dict(model_registry.TASK_LABEL),
        "settings": model_registry.current_settings(),
    }


@router.post("/v1/settings/models")
def set_model_settings(inp: ModelSettingsIn) -> dict[str, Any]:
    rt = get_runtime()
    fields: dict[str, Any] = {}
    if inp.temperature is not None:
        value = max(0.0, min(2.0, float(inp.temperature)))
        rt.cfg.provider.temperature = value
        fields["temperature"] = value
    if inp.max_tokens is not None:
        tokens = max(64, min(8000, int(inp.max_tokens)))
        rt.cfg.provider.max_tokens = tokens
        fields["max_tokens"] = tokens
    if inp.small_model is not None:
        rt.cfg.provider.small_model = inp.small_model.strip()
        fields["small_model"] = rt.cfg.provider.small_model
    if fields:
        config_mod.save_provider_fields(fields)
    return {"ok": True, "provider": get_model_settings()["provider"]}


@router.post("/v1/soul/export")
def soul_export() -> dict[str, Any]:
    """导出一份完整灵魂档案（人设 + 记忆 + 对话），返回文件路径。"""
    rt = get_runtime()
    path = transfer.export_file(rt)
    p = pathlib.Path(path)
    return {"ok": True, "path": path, "bytes": p.stat().st_size}


@router.get("/v1/soul/snapshots")
def soul_snapshots() -> dict[str, Any]:
    d = _soul_snapshot_dir()
    items = []
    for path in sorted(d.glob("*.life.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "bytes": stat.st_size,
            }
        )
    return {"dir": str(d), "items": items}


@router.post("/v1/soul/snapshots")
def soul_snapshot_create() -> dict[str, Any]:
    rt = get_runtime()
    d = _soul_snapshot_dir()
    name = f"snap-{datetime.now().strftime('%Y%m%d-%H%M%S')}.life.json"
    path = d / name
    path.write_text(
        json.dumps(transfer.export_payload(rt), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "name": name, "path": str(path), "bytes": path.stat().st_size}


@router.post("/v1/soul/snapshots/restore")
def soul_snapshot_restore(inp: SnapshotRestoreIn) -> dict[str, Any]:
    rt = get_runtime()
    name = inp.name
    if not name or any(bad in name for bad in ("..", "/", "\\")):
        raise HTTPException(status_code=400, detail="快照名不合法")
    path = _soul_snapshot_dir() / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="没有这个快照")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"快照不是合法 JSON：{exc}") from exc
    try:
        result = transfer.import_payload(rt, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rt.reload_persona()
    return {"ok": True, "name": name, **result}


@router.post("/v1/persona/reload")
def reload_persona_hot(path: str = Query(""), password: str = Query("")) -> dict[str, Any]:
    """热加载人设：不用重启内核。path 为空就重读当前人设文件。

    用于改完 persona/*.yaml 后立即生效（QQ 那边下一句话就是新设定）。
    """
    if not _dev_ok(password):
        raise HTTPException(status_code=403, detail="密码不对")
    rt = get_runtime()
    if path.strip():
        target = pathlib.Path(path.strip())
        if not target.is_absolute():
            target = DATA_ROOT / target if (DATA_ROOT / target).exists() else pathlib.Path.cwd() / target
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"找不到人设文件：{path}")
        rt.cfg.persona_override = target
    before = {"name": rt.persona.name, "key": rt.persona.key}
    rt.reload_persona()
    after = {"name": rt.persona.name, "key": rt.persona.key}
    if after != before:
        continuity.record(
            "persona_changed", f"{before['name']} → {after['name']}", "dev",
            {"before": before, "after": after, "file": str(rt.cfg.persona_file)},
        )
    return {
        "ok": True,
        "before": before,
        "after": after,
        "rules": len(rt.persona.rules()),
        "anchors": len(rt.persona.all_quotes()),
        "file": str(rt.cfg.persona_file),
    }


@router.post("/v1/soul/import")
async def soul_import(request: Request) -> dict[str, Any]:
    """导入灵魂档案（body 为 .life.json 原文）。先备份当前库由 transfer 负责提示，不做静默回滚。"""
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"不是合法 JSON：{exc}") from exc
    rt = get_runtime()
    try:
        result = transfer.import_payload(rt, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rt.reload_persona()
    return {"ok": True, **result}


# ---------------------------------------------------------------- OpenAI 兼容

@router.get("/v1/models")
def models() -> dict[str, Any]:
    rt = get_runtime()
    return {
        "object": "list",
        "data": [
            {
                "id": f"ai-life/{rt.persona.key}",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ai-life",
            }
        ],
    }


@router.post("/v1/chat/completions")
async def openai_compat(inp: OpenAIIn) -> dict[str, Any]:
    rt = get_runtime()
    user_text = ""
    for m in reversed(inp.messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                user_text = " ".join(
                    str(c.get("text", "")) for c in content if isinstance(c, dict)
                )
            else:
                user_text = str(content or "")
            break

    # OpenAI 兼容口是单用户本机 API：按"不分场合 + 恋人"处理，语气层生效
    result = await loop.chat_turn(
        rt, user_text, source="openai_compat", is_lover=True,
    )
    content = "\n".join(result.get("reply") or [])
    meta = result.get("meta") or {}

    return {
        "id": f"chatcmpl-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": inp.model or rt.cfg.provider.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": int(meta.get("prompt_tokens_est", 0)),
            "completion_tokens": max(1, len(content) // 2),
            "total_tokens": int(meta.get("prompt_tokens_est", 0)) + max(1, len(content) // 2),
        },
    }
