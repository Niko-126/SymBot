"""小鲸 Web 控制台的后端接口（panel/console.html 用）。

挂在 /v1/console/* 下。默认不校验口令（服务只监听 127.0.0.1）；
`console.token` 配了就要求带 `X-Console-Token`。
﻿‍‍​‌​﻿﻿​​‍​​‍‍﻿​​‍​​​‍‍﻿‌‍﻿‌‍‌‌​﻿"""

from __future__ import annotations

import hmac
import json
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core import db
from app.core.paths import DATA_ROOT
from app.core.runtime import get as get_runtime
from app.life import diary as diary_mod
from app.memory import evolve, h2, stickers
from app.mind import loop
from app.relation import friends as friend_mod
from app.relation import love, users


def _console_auth(request: Request) -> None:
    """配了口令就校验；没配就只靠回环地址。"""
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


router = APIRouter(prefix="/v1/console", dependencies=[Depends(_console_auth)])


def _send_api() -> tuple[str, str]:
    cfg = get_runtime().cfg.onebot
    return str(cfg.api_base).rstrip("/"), (cfg.access_token or "")


async def _qq_send(payload: dict[str, Any], api: str = "send_private_msg") -> bool:
    base, token = _send_api()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{base}/{api}", json=payload, headers=headers)
            if not r.is_success:
                return False
            data = r.json()
            return str(data.get("status")) == "ok"
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 记忆 / 印象 / 关系管理


@router.get("/memory/{user_id}/stats")
def memory_stats(user_id: int) -> dict[str, Any]:
    stats = h2.counts_for_user(user_id)
    return {"user_id": user_id, "count": int(stats["total"]), "by_kind": stats["by_kind"]}


@router.get("/memory/{user_id}")
def memory_list(user_id: int, limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    rows = h2.list_for_user(user_id, limit=int(limit))
    items = [
        {
            "id": r["id"], "kind": r["kind"], "content": r["content"],
            "importance": r["importance"], "pinned": 1 if r["pinned"] else 0,
            "first_seen": r["first_seen"],
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@router.delete("/memory/{user_id}")
def memory_clear(user_id: int) -> dict[str, Any]:
    n = h2.forget_user(user_id)
    if n:
        from app.core import continuity

        continuity.record(
            "memory_forgotten", f"她忘了这个人的 {n} 条记忆", "console",
            {"user_id": int(user_id), "count": int(n)},
        )
    return {"ok": True, "removed": int(n)}


@router.delete("/memory/item/{memory_id}")
def memory_delete_item(memory_id: int) -> dict[str, Any]:
    if not h2.delete_memory(int(memory_id)):
        raise HTTPException(status_code=404, detail="没有这条记忆")
    return {"ok": True}


@router.get("/impression/{user_id}")
def impression_get(user_id: int) -> dict[str, Any]:
    row = db.one("SELECT * FROM member_impression WHERE user_id = ?", (user_id,))
    if row is None:
        return {
            "user_id": user_id, "summary": "", "traits_seen": [], "habits": [],
            "version": 0, "updated_at": "",
        }
    d = dict(row)
    try:
        d["traits_seen"] = json.loads(d.get("traits_seen") or "[]")
        d["habits"] = json.loads(d.get("habits") or "[]")
    except json.JSONDecodeError:
        d["traits_seen"], d["habits"] = [], []
    d["user_id"] = user_id
    return d


@router.delete("/impression/{user_id}")
def impression_clear(user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("DELETE FROM member_impression WHERE user_id = ?", (user_id,))
    return {"ok": True}


@router.get("/relation/{user_id}")
def relation_get(user_id: int) -> dict[str, Any]:
    rel = users.relation_of(user_id)
    lover = users.lover_of(user_id)
    return {
        "user_id": user_id,
        "affinity": round(float(rel["affinity"]), 1),
        "trust": round(float(rel["trust"]), 1),
        "attraction": round(float(rel["attraction"]), 1),
        "aversion": round(float(rel["aversion"]), 1),
        "stage": rel["stage"],
        "hit_100_at": rel["hit_100_at"] or "",
        "lover": lover is not None,
    }


class RelationIn(BaseModel):
    user_id: int
    affinity: float | None = None
    trust: float | None = None
    attraction: float | None = None
    aversion: float | None = None


class RelationCapIn(BaseModel):
    """关系日上限的三个动作：看状态 / 清除当日已用 / 对某个用户禁用上限。

    为什么要手动口子（owner 2026-11-02）：加了硬日上限之后，旧数据把当天额度
    冲爆过（pos=214 vs 上限 12），结果一整天只跌不涨。控制台得能清、能单独放开。
    """

    action: str = "status"      # status | reset | bypass
    user_id: int = 0            # 0 = 面板那条线；reset 时传 0 表示"所有人一起清"
    enabled: bool = True        # bypass 用：True=该用户不受上限约束


@router.post("/relation_cap")
def relation_cap(inp: RelationCapIn) -> dict[str, Any]:
    from datetime import date

    from app.relation import engine

    today = date.today().isoformat()
    prefix = f"relation_cap:{today}"

    def _key(uid: int) -> str:
        return prefix if int(uid) == 0 else f"{prefix}:{int(uid)}"

    def _read(uid: int) -> dict[str, float]:
        raw = db.kv_get(_key(uid), "") or ""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return {"pos": float(data.get("pos") or 0.0), "neg": float(data.get("neg") or 0.0)}

    def _bypass(uid: int) -> bool:
        return db.kv_get(f"relation_cap_off:{int(uid)}", "0") == "1"

    if inp.action == "reset":
        targets = [inp.user_id] if int(inp.user_id or 0) > 0 else [0] + [
            int(r["id"]) for r in db.rows("SELECT id FROM qq_user")
        ]
        for uid in targets:
            db.kv_set(_key(uid), json.dumps({"pos": 0.0, "neg": 0.0}))
        return {"ok": True, "action": "reset", "cleared": targets, "today": today}

    if inp.action == "bypass":
        db.kv_set(f"relation_cap_off:{int(inp.user_id)}", "1" if inp.enabled else "0")
        return {"ok": True, "action": "bypass", "user_id": int(inp.user_id),
                "enabled": bool(inp.enabled)}

    pos_cap, neg_cap, caps_on = engine.caps_limits()
    uids = [0] + [int(r["id"]) for r in db.rows("SELECT id FROM qq_user ORDER BY id")]
    rows = []
    for uid in uids:
        cap = _read(uid)
        rows.append({
            "user_id": uid,
            "pos": round(cap["pos"], 2),
            "neg": round(cap["neg"], 2),
            "bypass": _bypass(uid),
        })
    return {
        "ok": True,
        "action": "status",
        "today": today,
        "hard_limit": float(engine.HARD_DAILY_POS),
        "caps_on": bool(caps_on),
        "pos_cap": round(float(pos_cap), 2),
        "neg_cap": round(float(neg_cap), 2),
        "rows": rows,
    }


@router.post("/relation")
def relation_set(inp: RelationIn) -> dict[str, Any]:
    users.relation_of(inp.user_id)  # 确保存在
    users.update_relation(
        inp.user_id,
        affinity=inp.affinity,
        trust=inp.trust,
        attraction=inp.attraction,
        aversion=inp.aversion,
    )
    return {"ok": True, **relation_get(inp.user_id)}


# ---------------------------------------------------------------- 深度思考开关


class ThinkIn(BaseModel):
    enabled: bool


@router.get("/thinking")
def thinking_get() -> dict[str, Any]:
    """深度思考开关状态（默认开）。"""
    from app.mind import models as model_registry

    st = model_registry.current_settings()
    return {"enabled": bool(st.get("deep_think")), "settings": st}


@router.post("/thinking")
def thinking_set(inp: ThinkIn) -> dict[str, Any]:
    """开关深度思考。开了会想更多，但每条回复更慢。"""
    from app.mind import models as model_registry

    model_registry.save_settings(deep_think=inp.enabled)
    return {"ok": True, **thinking_get()}


# ---------------------------------------------------------------- 测试期应答


@router.get("/testing")
def testing_get() -> dict[str, Any]:
    """测试期应答开关：打开后她只回固定三句，不走模型（联调用）。"""
    from app.mind.loop import TESTING_REPLIES

    return {"enabled": db.kv_get("testing_reply", "0") == "1",
            "replies": list(TESTING_REPLIES)}


class TestingIn(BaseModel):
    enabled: bool


@router.post("/testing")
def testing_set(inp: TestingIn) -> dict[str, Any]:
    db.kv_set("testing_reply", "1" if inp.enabled else "0")
    return {"ok": True, **testing_get()}


# ---------------------------------------------------------------- 观察管理


@router.get("/observations")
def observations_list(
    limit: int = Query(50, ge=1, le=200), topic: str = Query("")
) -> dict[str, Any]:
    """她的人类观察（human_notes）。带上是观察的谁、在哪个场合。"""
    cols = "h.id, h.day, h.topic, h.content, h.confidence, h.user_id, h.chat_type"
    if topic.strip():
        rows = db.rows(
            f"SELECT {cols} FROM human_notes h WHERE h.topic = ? ORDER BY h.id DESC LIMIT ?",
            (topic.strip(), int(limit)),
        )
    else:
        rows = db.rows(
            f"SELECT {cols} FROM human_notes h ORDER BY h.id DESC LIMIT ?",
            (int(limit),),
        )
    name_map = {int(r["id"]): str(r["nickname"] or r["openid"] or "")
                for r in db.rows("SELECT id, openid, nickname FROM qq_user")}
    items = []
    for r in rows:
        d = dict(r)
        d["nickname"] = name_map.get(int(d.get("user_id") or 0), "")
        items.append(d)
    total = db.one("SELECT COUNT(*) c FROM human_notes")["c"]
    by_topic = {
        str(r["topic"]): int(r["c"])
        for r in db.rows("SELECT topic, COUNT(*) c FROM human_notes GROUP BY topic")
    }
    return {"items": items, "total": int(total), "by_topic": by_topic}


@router.delete("/observations/{note_id}")
def observation_delete(note_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("DELETE FROM human_notes WHERE id = ?", (int(note_id),))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="没有这条观察")
    return {"ok": True}


@router.delete("/observations")
def observations_clear(topic: str = Query("")) -> dict[str, Any]:
    """清空观察。带 topic 只清该类（习惯/说话方式/情绪/仪式/相处）。"""
    with db.cursor() as cur:
        if topic.strip():
            cur.execute("DELETE FROM human_notes WHERE topic = ?", (topic.strip(),))
        else:
            cur.execute("DELETE FROM human_notes")
        return {"ok": True, "removed": int(cur.rowcount)}


# ---------------------------------------------------------------- 好友


@router.get("/friends")
def list_friends() -> dict[str, Any]:
    rows = db.rows("SELECT * FROM qq_user ORDER BY last_seen DESC")
    out = []
    for r in rows:
        uid = int(r["id"])
        rel = users.relation_of(uid)
        lover = users.lover_of(uid)
        cnt = db.one(
            "SELECT COUNT(*) c FROM events WHERE user_id = ? AND role = 'user'", (uid,)
        )["c"]
        out.append(
            {
                "user_id": uid,
                "openid": r["openid"],
                "nickname": r["nickname"] or r["openid"],
                "affinity": round(float(rel["affinity"]), 1),
                "trust": round(float(rel["trust"]), 1),
                "attraction": round(float(rel["attraction"]), 1),
                "stage": rel["stage"],
                "last_seen": r["last_seen"],
                "lover": lover is not None,
                "msg_count": int(cnt),
            }
        )
    return {"friends": out, "total": len(out)}


class FriendIn(BaseModel):
    openid: str = Field(min_length=3)
    nickname: str = ""


@router.post("/friends")
def add_friend(inp: FriendIn) -> dict[str, Any]:
    """本地登记一个已知用户（建关系、开记忆位）。

    **不会**在 QQ 里真的加好友：OneBot 没有主动加好友的接口。
    真要加成好友，得对方先发申请，走 `/friends/requests` 通过。
    """
    user = users.ensure_user(inp.openid.strip(), inp.nickname.strip())
    users.relation_of(int(user["id"]))
    return {
        "ok": True,
        "mode": "registered",
        "user_id": int(user["id"]),
        "note": "已在本地登记。QQ 好友关系要对方发申请，再到「好友申请」里通过。",
    }


@router.get("/friends/requests")
def friend_requests(status: str = Query(""), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """OneBot 推过来的好友申请。pending = 还没处理。"""
    items = friend_mod.listing(status=status.strip(), limit=int(limit))
    return {
        "items": items,
        "pending": friend_mod.pending_count(),
        "auto_accept": bool(getattr(get_runtime().cfg.onebot, "auto_accept_friend_request", True)),
    }


class FriendRequestIn(BaseModel):
    approve: bool = True


@router.post("/friends/requests/{request_id}")
async def friend_request_decide(request_id: int, inp: FriendRequestIn) -> dict[str, Any]:
    """通过 / 拒绝。真正跟 QQ 说一句 set_friend_add_request。"""
    from app.api.onebot import accept_request

    row = friend_mod.get(int(request_id))
    if row is None:
        raise HTTPException(status_code=404, detail="没有这条好友申请")
    if str(row.get("status") or "") != friend_mod.PENDING:
        raise HTTPException(status_code=409, detail=f"这条申请已经处理过了（{row.get('status')}）")
    if not str(row.get("flag") or ""):
        raise HTTPException(status_code=400, detail="这条申请没有 flag，OneBot 不认，没法通过")
    ok = await accept_request(get_runtime().cfg.onebot, int(request_id), approve=inp.approve)
    if not ok:
        raise HTTPException(status_code=502, detail="NapCat 没确认（检查是否在线、token 是否对）")
    return {"ok": True, "request_id": int(request_id), "approve": bool(inp.approve)}


@router.delete("/friends/requests/{request_id}")
def friend_request_delete(request_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("DELETE FROM friend_request WHERE id = ?", (int(request_id),))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="没有这条好友申请")
    return {"ok": True}


@router.post("/friends/{user_id}/forget")
def forget_friend(user_id: int) -> dict[str, Any]:
    """针对性失忆：清关系/记忆/印象/观察/私密记录/聊天原文，解除拉黑，从陌生人重来。

    她"忘了他"，不是"她死了"——所以不碰全局会话和其他用户。
    """
    row = users.get_user(int(user_id))
    if row is None:
        raise HTTPException(status_code=404, detail="没有这个用户")
    counts = users.forget_user(int(user_id))
    return {"ok": True, "user_id": int(user_id), "nickname": row.get("nickname") or "", **counts}


@router.delete("/friends/{user_id}")
def delete_friend(user_id: int) -> dict[str, Any]:
    row = db.one("SELECT id, openid FROM qq_user WHERE id = ?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="没有这个好友")
    openid = str(row["openid"] or "")
    db.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
    gone = h2.forget_user(user_id)
    from app.core import continuity

    continuity.record(
        "person_forgotten", f"她把一个人从记忆里抹掉了（{gone} 条）", "console",
        {"user_id": int(user_id), "openid": openid, "count": int(gone)},
    )
    db.execute("DELETE FROM user_relation WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM lover WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM member_impression WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM human_notes WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM intimate_context WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM intimate_profile WHERE user_id = ?", (user_id,))
    if openid:
        db.execute("DELETE FROM friend_request WHERE openid = ?", (openid,))
        for key in ("greeted",):
            db.kv_set(f"{key}:{openid}", "")
    for key in ("intimate_mode", "intimate_mode_ts", "love_pending", "proactive_last_ts"):
        db.kv_set(f"{key}:{int(user_id)}", "")
    db.execute("DELETE FROM qq_user WHERE id = ?", (user_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 聊天


@router.get("/chat/{user_id}")
def get_chat(
    user_id: int,
    limit: int = Query(50, ge=1, le=200),
    chat_type: str = Query(""),
    group_id: str = Query(""),
) -> dict[str, Any]:
    """聊天记录。chat_type=private|group 只取该场合（老数据留空，两边都算）。"""
    kind = str(chat_type or "").strip().lower()
    where = ["user_id = ?"]
    args: list[Any] = [int(user_id)]
    if kind in ("private", "group"):
        where.append("(chat_type = ? OR chat_type = '')")
        args.append(kind)
    if group_id.strip():
        where.append("group_id = ?")
        args.append(group_id.strip())
    args.append(int(limit))
    rows = db.rows(
        "SELECT id, role, text, ts, source, chat_type, group_id FROM events"
        " WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?",
        tuple(args),
    )
    msgs = [
        {
            "id": int(r["id"]),
            "role": r["role"],
            "text": r["text"] or "",
            "ts": r["ts"],
            "source": r["source"],
            "chat_type": r["chat_type"] or "",
            "group_id": r["group_id"] or "",
        }
        for r in reversed(rows)
    ]
    counts = {
        str(r["chat_type"] or "unknown"): int(r["c"])
        for r in db.rows(
            "SELECT chat_type, COUNT(*) c FROM events WHERE user_id = ?"
            " AND role = 'user' GROUP BY chat_type",
            (int(user_id),),
        )
    }
    return {"messages": msgs, "total": len(msgs), "counts": counts}


class SendIn(BaseModel):
    user_id: int
    text: str = Field(min_length=1)
    # 前端传 as（Python 关键字），用 alias 兼容
    as_role: str = Field(default="ai", alias="as")
    # private | group（空 = 不分场合）
    chat_type: str = ""
    group_id: str = ""

    model_config = {"populate_by_name": True}


@router.post("/send")
async def console_send(inp: SendIn) -> dict[str, Any]:
    rt = get_runtime()
    chat_type = str(inp.chat_type or "").strip().lower()
    chat_type = chat_type if chat_type in ("private", "group") else ""
    if inp.as_role == "me":
        # 模拟用户发来一句话：走完整对话（含亲密门控），但回复不推回 QQ
        from app.intimacy import gate as igate

        mode, note, is_lover, is_private = igate.prepare(inp.user_id, inp.text, chat_type)
        result = await loop.chat_turn(
            rt, inp.text, source="qq_onebot", user_id=inp.user_id,
            chat_type=chat_type, group_id=inp.group_id,
            intimate_mode=mode.value, intimate_note=note, is_lover=is_lover,
        )
        igate.finish(inp.user_id, mode, result.get("reply") or [], is_private)
        return {
            "ok": True, "mode": "me", "reply": result.get("reply") or [],
            "intimate_mode": mode.value, "intimate_note": note,
        }
    # AI 主动说一句：直接经 QQ 发出去，也记进事件
    from app.memory import store

    if chat_type == "group":
        gid = str(inp.group_id or "").strip()
        if not gid:
            raise HTTPException(status_code=400, detail="群聊要带 group_id")
        try:
            gid_value: Any = int(gid)
        except ValueError:
            gid_value = gid
        ok = await _qq_send({"group_id": gid_value, "message": inp.text}, api="send_group_msg")
    else:
        ok = await _qq_send({"user_id": _openid_of(inp.user_id), "message": inp.text})
    store.add_event("her", inp.text, source="console", user_id=inp.user_id,
                    chat_type=chat_type, group_id=inp.group_id)
    return {"ok": True, "mode": "ai", "sent": ok}


def _openid_of(user_id: int) -> int:
    row = db.one("SELECT openid FROM qq_user WHERE id = ?", (user_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="没有这个好友")
    try:
        return int(str(row["openid"]))
    except ValueError:
        raise HTTPException(status_code=400, detail="这个好友的 QQ 号不是纯数字") from None


# ---------------------------------------------------------------- 表情


@router.get("/stickers")
def list_stickers(
    limit: int = Query(60, ge=1, le=300), q: str = Query(""), safe_only: bool = Query(True)
) -> dict[str, Any]:
    items = stickers.load_index()
    if safe_only:
        items = [x for x in items if x.get("safe")]
    if q.strip():
        kw = q.strip().lower()
        items = [
            x for x in items
            if kw in str(x.get("title", "")).lower() or kw in str(x.get("quote", "")).lower()
        ]
    total = len(items)
    page = items[: int(limit)]
    return {
        "items": [
            {
                "id": str(x.get("id")),
                "title": str(x.get("title") or "")[:40],
                "quote": str(x.get("quote") or "")[:30],
                "scene": x.get("scene"),
                "safe": bool(x.get("safe")),
                "url": f"/v1/console/sticker/{x.get('id')}.webp",
            }
            for x in page
        ],
        "total": total,
    }


@router.get("/sticker/{sticker_id}.webp")
def sticker_file(sticker_id: str) -> FileResponse:
    entry = next((x for x in stickers.load_index() if str(x.get("id")) == sticker_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="没有这张表情")
    path = stickers.file_path(entry)
    if path is None:
        raise HTTPException(status_code=404, detail="图片文件缺失")
    return FileResponse(str(path), media_type="image/webp")


class StickerIn(BaseModel):
    user_id: int
    sticker_id: str


@router.post("/send_sticker")
async def send_sticker(inp: StickerIn) -> dict[str, Any]:
    entry = next((x for x in stickers.load_index() if str(x.get("id")) == inp.sticker_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="没有这张表情")
    path = stickers.file_path(entry)
    if path is None:
        raise HTTPException(status_code=404, detail="图片文件缺失")
    from app.memory import store

    ok = await _qq_send(
        {
            "user_id": _openid_of(inp.user_id),
            "message": [{"type": "image", "data": {"file": path.resolve().as_uri()}}],
        }
    )
    store.add_event("her", f"[表情] {entry.get('quote') or entry.get('title')}", source="console", user_id=inp.user_id)
    return {"ok": True, "sent": ok, "sticker": inp.sticker_id}


# ---------------------------------------------------------------- AI 想法


@router.get("/companion/{user_id}")
def companion_board(user_id: int) -> dict[str, Any]:
    """陪伴层状态：触发策略、念头额度、最近一轮"要不要主动提"的决定。

    另外带上"她主动开口"的三道闸门现状（夜间静默/免打扰/今日配额），
    面板不用新开路由就能看出她为什么没主动（docs/55）。
    """
    from app.life import proactive
    from app.memory import companion

    data = companion.status(user_id)
    try:
        data["proactive"] = proactive.settings_for(user_id)
    except Exception:  # noqa: BLE001 - 观测字段失败不影响主状态
        data["proactive"] = {}
    return data


@router.get("/companion/{user_id}/thoughts")
def companion_thoughts(user_id: int, limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    """她最近自己想的事（只读）。"""
    from app.life import thoughts

    items = thoughts.recent(user_id, limit=int(limit))
    return {"user_id": int(user_id), "items": items, "total": len(items)}


@router.get("/continuity")
def continuity_board(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """连续性账本：她被改写过的所有痕迹（换人设/换模型/清记忆/重开会话/导入灵魂）。"""
    from app.core import continuity

    return {
        "count": continuity.count(),
        "major": continuity.summary_text(4),
        "items": continuity.recent(int(limit)),
    }


@router.get("/mind")
def mind_board() -> dict[str, Any]:
    name_map = {int(r["id"]): str(r["nickname"] or r["openid"] or "")
                for r in db.rows("SELECT id, openid, nickname FROM qq_user")}
    impressions = []
    for r in db.rows("SELECT * FROM member_impression ORDER BY updated_at DESC LIMIT 20"):
        d = dict(r)
        try:
            traits = json.loads(d.get("traits_seen") or "[]")
            habits = json.loads(d.get("habits") or "[]")
        except json.JSONDecodeError:
            traits, habits = [], []
        impressions.append(
            {
                "user_id": int(d["user_id"]),
                "nickname": name_map.get(int(d["user_id"]), ""),
                "summary": d.get("summary") or "",
                "traits_seen": traits,
                "habits": habits,
                "updated_at": d.get("updated_at"),
            }
        )
    notes = []
    for r in db.rows(
        "SELECT id, day, topic, content, user_id, chat_type FROM human_notes"
        " ORDER BY id DESC LIMIT 30"
    ):
        notes.append(
            {
                "id": int(r["id"]),
                "day": r["day"],
                "topic": r["topic"],
                "content": r["content"],
                "user_id": int(r["user_id"] or 0),
                "chat_type": str(r["chat_type"] or ""),
                "nickname": name_map.get(int(r["user_id"] or 0), ""),
            }
        )
    thoughts = []
    for r in db.rows("SELECT * FROM reflect_report ORDER BY id DESC LIMIT 12"):
        d = dict(r)
        thoughts.append(
            {
                "kind": d["kind"],
                "period_start": d["period_start"],
                "period_end": d["period_end"],
                "content": d["content"],
                "created_at": d["created_at"],
            }
        )
    diary_items = []
    for r in db.rows("SELECT key, value FROM kv WHERE key LIKE 'diary:%' ORDER BY key DESC LIMIT 14"):
        try:
            payload = json.loads(str(r["value"]))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("day"):
            diary_items.append({"day": payload["day"], "text": payload.get("text", "")})
    return {
        "impressions": impressions,
        "notes": notes,
        "thoughts": thoughts,
        "diary": diary_items,
    }


class MindRunIn(BaseModel):
    kind: str = "thought"
    user_id: int = 0


@router.post("/mind/run")
async def mind_run(inp: MindRunIn) -> dict[str, Any]:
    rt = get_runtime()
    persona = rt.persona
    if inp.kind == "thought":
        await evolve.write_human_notes(rt, persona)
        content = await evolve.write_think_report(rt, persona)
        return {"ok": True, "content": content}
    if inp.kind == "impression":
        if inp.user_id <= 0:
            raise HTTPException(status_code=400, detail="要指定 user_id")
        n = await evolve.update_impressions(rt, persona, limit_users=50, min_lines=1)
        return {"ok": True, "updated": n}
    if inp.kind == "diary":
        day = db.kv_get("diary_last_date", "") or ""
        entry = await diary_mod.compose_for(rt, persona, _yesterday())
        return {"ok": True, "entry": entry}
    if inp.kind == "eval":
        content = await evolve.write_memory_eval(rt, persona)
        return {"ok": True, "content": content}
    raise HTTPException(status_code=400, detail="未知的 kind")


def _yesterday() -> str:
    from datetime import datetime, timedelta

    return (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()


@router.get("/status")
def console_status() -> dict[str, Any]:
    """控制台顶部状态：内核 + QQ + 素材库。"""
    rt = get_runtime()
    stats = stickers.stats()
    ob = rt.cfg.onebot
    return {
        "persona": rt.persona.name,
        "model": rt.cfg.provider.model,
        "provider": "mock" if rt.cfg.provider.is_mock else "openai",
        "stickers": stats,
        "friends": db.one("SELECT COUNT(*) c FROM qq_user")["c"],
        "memories": h2.memory_stats()["total"],
        "friend_requests": friend_mod.pending_count(),
        "console_auth": bool(str(getattr(rt.cfg.console, "token", "") or "")),
        "onebot": {
            "enabled": bool(getattr(ob, "enabled", False)),
            "api_base": str(getattr(ob, "api_base", "") or ""),
            "self_id": str(getattr(ob, "self_id", "") or ""),
            "auto_accept_friend_request": bool(
                getattr(ob, "auto_accept_friend_request", True)
            ),
        },
    }


# ---------------------------------------------------------------- NapCat 登录 / 好友同步


@router.get("/groups")
def groups_list(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """已知群号（从聊天记录里来）——群聊发送时给前端做下拉，不用手打。"""
    rows = db.rows(
        "SELECT group_id, COUNT(*) c, MAX(ts) m FROM events WHERE group_id <> ''"
        " GROUP BY group_id ORDER BY m DESC LIMIT ?",
        (int(limit),),
    )
    return {
        "items": [
            {"group_id": str(r["group_id"]), "count": int(r["c"] or 0), "last_ts": str(r["m"] or "")}
            for r in rows
        ]
    }


@router.get("/groups/{group_id}/members")
async def group_members(group_id: int) -> dict[str, Any]:
    """拉这个群的成员，分「已认识 / 还没认识」。"""
    cfg = get_runtime().cfg.onebot
    if not bool(getattr(cfg, "enabled", False)):
        raise HTTPException(status_code=400, detail="onebot 没启用")
    from app.api.onebot import get_group_members

    items = await get_group_members(cfg, group_id)
    if items is None:
        raise HTTPException(status_code=502, detail="拉不到群成员：NapCat 不通或没权限")
    self_id = str(getattr(cfg, "self_id", "") or "")
    known = {str(r["openid"]) for r in db.rows("SELECT openid FROM qq_user")}
    unknown = [
        i for i in items
        if i["openid"] not in known and (not self_id or i["openid"] != self_id)
    ]
    return {"total": len(items), "known": len(items) - len(unknown), "unknown": unknown}


@router.post("/groups/{group_id}/greet")
async def group_greet(group_id: int, limit: int = Query(3, ge=1, le=10)) -> dict[str, Any]:
    """把群里还没认识的人拉进来，主动 @ 招呼（一次最多 limit 个，每人只招呼一次）。"""
    cfg = get_runtime().cfg.onebot
    if not bool(getattr(cfg, "enabled", False)):
        raise HTTPException(status_code=400, detail="onebot 没启用")
    from app.api.onebot import _post_api, get_group_members
    from app.memory import store
    from app.relation import greet as greet_mod

    items = await get_group_members(cfg, group_id)
    if items is None:
        raise HTTPException(status_code=502, detail="拉不到群成员：NapCat 不通或没权限")
    self_id = str(getattr(cfg, "self_id", "") or "")
    known = {str(r["openid"]) for r in db.rows("SELECT openid FROM qq_user")}
    unknown = [
        i for i in items
        if i["openid"] not in known and (not self_id or i["openid"] != self_id)
    ]
    greeted: list[dict[str, Any]] = []
    for item in items:
        if len(greeted) >= int(limit):
            break
        oid = str(item["openid"])
        if self_id and oid == self_id:
            continue
        # 已经认识的人不招呼；只招呼真正的生面孔
        if oid in known:
            continue
        if greet_mod.already_greeted(oid) or greet_mod.family_skip(oid):
            continue
        user = users.ensure_user(oid, str(item.get("nickname") or ""))
        if users.is_blocked(int(user["id"])):
            continue
        gl = greet_mod.line()
        body = {
            "group_id": int(group_id),
            "message": [
                {"type": "at", "data": {"qq": oid}},
                {"type": "text", "data": {"text": " " + gl}},
            ],
        }
        sent = await _post_api(cfg, "send_group_msg", body)
        if sent is None:
            raise HTTPException(status_code=502, detail="招呼发送失败：NapCat 没确认")
        greet_mod.mark_greeted(oid)
        store.add_event("her", gl, source="console", user_id=int(user["id"]),
                        meta={"greet": True}, chat_type="group", group_id=str(group_id))
        greeted.append({"openid": oid, "nickname": str(item.get("nickname") or ""), "line": gl})
    return {"ok": True, "group_id": int(group_id), "greeted": greeted,
            "total": len(items), "unknown": len(unknown),
            "remaining": max(0, len(unknown) - len(greeted))}


@router.get("/onebot/login")
async def onebot_login() -> dict[str, Any]:
    """NapCat 当前登录的是哪个小号。登录本身在 NapCat 里做，这里只查询，不碰账号密码。"""
    cfg = get_runtime().cfg.onebot
    if not bool(getattr(cfg, "enabled", False)):
        return {"ok": True, "enabled": False, "online": False,
                "reason": "onebot 没启用（config.yaml: onebot.enabled）"}
    from app.api.onebot import get_login_info

    info = await get_login_info(cfg)
    return {"ok": True, "enabled": True,
            "auto_accept": bool(getattr(cfg, "auto_accept_friend_request", True)), **info}


@router.post("/onebot/sync")
async def onebot_sync() -> dict[str, Any]:
    """把 QQ 里真实的好友拉进来（OneBot get_friend_list）——控制台「加好友」终于有实质内容。"""
    rt = get_runtime()
    cfg = rt.cfg.onebot
    if not bool(getattr(cfg, "enabled", False)):
        raise HTTPException(status_code=400, detail="onebot 没启用：先在 config.yaml 打开 onebot.enabled")
    from app.api.onebot import get_friend_list

    items = await get_friend_list(cfg)
    if items is None:
        raise HTTPException(status_code=502, detail="拉不到好友列表：NapCat 不通或小号没登录")
    added = 0
    for item in items:
        before = users.get_user_by_openid(item["openid"])
        users.ensure_user(item["openid"], item.get("nickname") or "")
        if before is None:
            added += 1
    return {"ok": True, "total": len(items), "added": added}


# ---------------------------------------------------------------- 亲密记录（可查看 / 可清）


@router.get("/intimate/exemplars")
def intimate_exemplars() -> dict[str, Any]:
    from app.intimacy import exemplars as iex

    iex.ensure_seeded()
    return {"items": iex.all_items(), "stats": iex.stats()}


@router.get("/intimate/{user_id}")
def intimate_view(user_id: int, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """查看某个人的亲密会话记录（进入该模式后才有；普通聊天不写这里）。"""
    from app.intimacy import analyze as ianalyze
    from app.intimacy import exemplars as iex
    from app.intimacy import memory as imem
    from app.intimacy import mode as imode

    return {
        "user_id": int(user_id),
        "mode": imode.expire(int(user_id)).value,
        "lover": users.lover_of(int(user_id)) is not None,
        "sessions": imem.recent(int(user_id), limit),
        "profile": ianalyze.profile_of(int(user_id)),
        "exemplars": iex.stats(),
    }


@router.post("/intimate/{user_id}/analyze")
async def intimate_analyze(user_id: int) -> dict[str, Any]:
    """手动触发偏好分析（把私密记录折成"他喜欢/讨厌/边界"）。"""
    from app.intimacy import analyze as ianalyze

    if users.get_user(int(user_id)) is None:
        raise HTTPException(status_code=404, detail="没有这个用户")
    profile = await ianalyze.analyze_user(get_runtime(), int(user_id))
    return {"ok": True, "user_id": int(user_id), "profile": profile}


@router.delete("/intimate/{user_id}")
def intimate_clear(user_id: int) -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("DELETE FROM intimate_context WHERE user_id = ?", (int(user_id),))
        deleted = int(cur.rowcount or 0)
    return {"ok": True, "deleted": deleted}


# ---------------------------------------------------------------- 声音（TTS）独立 API
#
# 为什么单开一条 API：合成是**慢请求 + 会排队**的事，不能挤在聊天主链上。
# 这里给面板/外部脚本一个独立入口，走 app/voice 的专属线程池（voice.workers），
# 与聊天模型、与其它 to_thread 调用互不抢资源；同一句话还会命中缓存。


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str = ""
    style: str = ""


@router.get("/tts")
def tts_status() -> dict[str, Any]:
    """声音服务状态：开没开、缓存几个、这一分钟用了几次额度。"""
    from app.voice import tts as tts_mod

    return {"ok": True, **tts_mod.status(get_runtime().cfg)}


@router.post("/tts")
async def tts_say(inp: TTSIn) -> dict[str, Any]:
    """合成一段语音（不走聊天主链）。返回 WAV 的绝对路径。"""
    from app.voice import tts as tts_mod

    cfg = get_runtime().cfg
    res = await tts_mod.synth(cfg, inp.text, voice=inp.voice, style=inp.style)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason", "failed")}
    return {
        "ok": True,
        "path": res.get("path"),
        "bytes": res.get("bytes"),
        "cached": bool(res.get("cached")),
        "voice": res.get("voice"),
        "style": res.get("style"),
        "url": "/v1/console/tts/file?path=" + str(res.get("path") or ""),
    }


@router.get("/tts/file")
def tts_file(path: str = Query("", description="POST /tts 返回的绝对路径")) -> Any:
    """把合成好的 WAV 发回去给面板播放。只允许 data/voices 下的文件。"""
    import pathlib

    from app.core.paths import DATA_ROOT

    if not path:
        raise HTTPException(status_code=400, detail="缺少 path")
    root = (pathlib.Path(DATA_ROOT) / "data" / "voices").resolve()
    target = pathlib.Path(path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="只能取 data/voices 下的文件")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="语音文件不存在")
    return FileResponse(str(target), media_type="audio/wav")


# ---------------------------------------------------------------- 服务控制（内核 / NapCat / 语音）
#
# owner 2026-11-02 明确要的：面板上能看和管三个常驻服务，不用去摸终端。
# 状态一律现探（见 app/core/services.py）；这里只做参数校验与转发，
# 唯一有状态的是"重启内核"——它必须**先回包再自杀**，所以是排程不是同步执行。


class ServiceActionIn(BaseModel):
    target: str = Field(description="kernel | napcat | voice")
    action: str = Field(default="restart", description="start | stop | restart | login")
    qq: str = ""


def _kernel_port(request: Request) -> int:
    """这次请求实际落在哪个端口上，就是内核端口（别信配置，可能被 --port 改过）。"""
    try:
        return int(request.url.port or 8787)
    except (ValueError, TypeError):
        return 8787


@router.get("/service/status")
def service_status(request: Request) -> dict[str, Any]:
    """三个服务的实时状态：内核 pid/端口/运行时长、NapCat 登录态、语音模型/CUDA/日志。"""
    from app.core import services

    return services.status(_kernel_port(request))


@router.post("/service/action")
def service_action(inp: ServiceActionIn, request: Request) -> dict[str, Any]:
    """启动 / 停止 / 重启。失败一律带人话 `reason`，不静默。"""
    from app.core import services

    target = str(inp.target or "").strip().lower()
    if target not in ("kernel", "napcat", "voice"):
        raise HTTPException(status_code=400, detail="target 只能是 kernel / napcat / voice")
    result = services.action(
        target,
        str(inp.action or "restart"),
        qq=str(inp.qq or ""),
        port=_kernel_port(request),
    )
    if not result.get("ok"):
        # 不是 500：这是"动作没成"而不是"服务坏了"，面板要能读 reason
        return {"ok": False, "target": target, "action": str(inp.action or ""),
                "reason": str(result.get("reason") or "没成功"), **result}
    return result


@router.get("/service/voice/log")
def service_voice_log(tail: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """语音服务的日志尾（默认 200 行）。TTS 不弹终端，全靠这个窗口。"""
    from app.core import services

    return services.voice_log(int(tail))


@router.post("/service/qr")
def service_qr() -> dict[str, Any]:
    """登录二维码。先看是不是已经登录，再问 NapCat WebUI 要图；拿不到就说清为什么。

    真机事实：OneBot v11 那侧没有扫码接口，二维码在 NapCat WebUI（6099）。
    """
    from app.core import napcat_webui, services

    if not bool(getattr(get_runtime().cfg.onebot, "enabled", False)):
        return {"ok": False, "reason": "onebot 没启用（config.yaml: onebot.enabled）",
                "already_login": False}
    st = napcat_webui.login_status()
    if st.get("is_login"):
        info = services.napcat_status()
        return {
            "ok": True,
            "already_login": True,
            "image": "",
            "uin": info.get("uin") or "",
            "nickname": info.get("nickname") or "",
            "reason": "小号已经登录了，不需要扫码",
        }
    res = napcat_webui.qrcode()
    if res.get("ok"):
        return {"ok": True, "image": res.get("image"), "kind": res.get("kind"),
                "already_login": False,
                "webui": f"http://127.0.0.1:{napcat_webui.webui_port()}/webui"}
    return {
        "ok": False,
        "image": "",
        "already_login": bool(res.get("already_login")),
        "reason": str(res.get("reason") or "拿不到二维码"),
        "webui": f"http://127.0.0.1:{napcat_webui.webui_port()}/webui",
        "hint": "也可以去 NapCat 的 WebUI 页面直接扫码；扫完面板会自动变绿。",
    }


class QuickLoginIn(BaseModel):
    uin: str = ""


@router.post("/service/qq/quick_login")
def service_quick_login(inp: QuickLoginIn) -> dict[str, Any]:
    """NapCat 一键快速登录（复用本机已登录过的 QQ 凭据，不用扫码）。"""
    from app.core import napcat_webui, services

    uin = str(inp.uin or "").strip() or services._default_qq()  # noqa: SLF001 - 同包内部默认值
    if not uin:
        return {"ok": False, "reason": "没给 QQ 号，也没在设置里配小号（设置 → 连接与账号）"}
    res = napcat_webui.quick_login(uin)
    if res.get("ok"):
        return {"ok": True, "uin": uin, "note": "已让小号快速登录；等 5–15 秒面板会变绿"}
    return res


@router.get("/service/napcat/quick_list")
def service_quick_list() -> dict[str, Any]:
    """这台机器上 NapCat 存过的快速登录账号（不含任何口令）。"""
    from app.core import napcat_webui

    return {"items": napcat_webui.quick_login_list()}
