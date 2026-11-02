"""OneBot v11 接入（小号路线）：NapCat / LLOneBot / Lagrange。

分工：
- **账号登录在 NapCat 里**（本仓库不碰 QQ 账号密码）
- NapCat 开「HTTP API」+「HTTP 上报」→ 上报到本服务 `/onebot/event`
- 我们处理消息 → 内核一轮对话 → 调 NapCat 的 `send_private_msg` / `send_group_msg` 回消息

安全/边界：
- `onebot.enabled` 关闭时所有事件忽略（不会误连）
- `access_token` 配了就校验 Authorization；账号侧的密钥只写 config.local.yaml
- 群聊默认只回 @她 的消息；发不出去时写 `data/onebot_outbox.jsonl` 兜底（便于排查）
- 与官方 `app/adapters/qq.py` 并存，以后切官方不用改这里
﻿​‌‌​‍​‌‌‍‍‍​​​﻿‌‌​‌​‍‍​​​‌‍​‍‍​﻿﻿"""

from __future__ import annotations

import asyncio
import json
import pathlib
import random
import re
import threading
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.core import channel
from app.core.paths import DATA_ROOT
from app.core.runtime import get as get_runtime
from app.mind import loop
from app.relation import users as users_mod
from app.web import bili
from app.api import trend as trend_mod

def _log(message: str) -> None:
    """轻量日志：onebot 这边没有 logger，print 会进内核的 last_serve.log。"""
    try:
        print(f"[onebot] {message}", flush=True)
    except Exception:  # noqa: BLE001 - 没 stdout 也不能炸
        pass


router = APIRouter()
OUTBOX = DATA_ROOT / "data" / "onebot_outbox.jsonl"

# ---------------------------------------------------------------- 点歌翻唱
#
# 玩法（2026-11-02）：他说"唱《雨爱》"或甩个 B 站链接 → 先回一句"我先练一下"
# 拖住等待，后台把歌下下来、分离人声、换成她的音色、混回伴奏，再发进聊天。
# 全链路两分钟左右，所以必须异步：不能卡住这条消息的回复。

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_URL_RE = re.compile(r"https?://[^\s，。！？]+")
_SING_HINTS = (
    "唱一首", "唱一个", "唱一下", "唱个", "唱首", "来一首", "来首", "点歌",
    "给我唱", "唱《", "唱\"", "唱“", "能不能唱", "会唱", "翻唱",
)
_TITLE_RE = re.compile(r"[《「『\"“]([^》」』\"”]{1,40})[》」』\"”]")


def _cover_target(text: str) -> str:
    """从一句话里抠出"要唱哪首"。

    优先级：B 站链接 / BV 号（他说"就这个"最准）→ 书名号里的歌名 → "唱XX" 后面那截。
    没有唱歌意图就返回空串。
    """
    body = (text or "").strip()
    if not body:
        return ""
    url = _URL_RE.search(body)
    if url and ("bilibili.com" in url.group(0) or "b23.tv" in url.group(0)):
        return url.group(0)
    bv = _BV_RE.search(body)
    if bv:
        return bv.group(1)
    if not (any(k in body for k in _SING_HINTS) or "唱" in body or "点歌" in body):
        return ""
    titled = _TITLE_RE.search(body)
    if titled:
        return titled.group(1).strip()
    # "点歌 孤勇者" / "来一首漠河舞厅" / "唱 起风了" 这类都要能抓
    marker = re.search(r"(?:点歌|来(?:一首|一个|一下|个|首)?|唱(?:一首|一个|一下|个|首)?)", body)
    cand = body[marker.end():] if marker else ""
    cand = re.sub(r"^(给我|帮我|你|来|吧|呗|呀|啊|的|这首歌|首歌|首|个|吧)+", "", cand.strip())
    cand = cand.strip(" 　，。！？~～…、").strip()
    if cand.startswith("歌") and len(cand) > 2:
        cand = cand[1:]
    # 没有真的歌名（"唱首歌吧""她唱歌不好听" 这种）就别去搜，当普通聊天
    if len(cand) < 2 or any(k in cand for k in ("不好听", "不会唱", "跑调", "好听", "歌")):
        return ""
    if cand in {"首", "歌曲", "一首", "点歌", "一个"}:
        return ""
    return cand[:40]


#: "等你确认是不是这首"的悬案：key = cover_pending:<uid>，内容是 {"url","title","at"}
#: 为什么要它：搜出来的歌跟关键词对不上时她会先问一句，用户回"是的"以后必须真的开唱
#: （2026-11-02 真机踩到：只问不唱，用户回"是的"就石沉大海了）。
_PEND_PREFIX = "cover_pending:"
_PEND_TTL_S = 300
_AFFIRM_WORDS = ("是的", "对", "嗯", "是", "好", "可以", "没错", "就这首", "唱吧", "来", "行")
_DENY_WORDS = ("不是", "不对", "不要", "不用", "算了", "换一首", "换一个", "别唱")


def _pend_key(uid: int) -> str:
    return f"{_PEND_PREFIX}{int(uid)}"


def _load_pending(uid: int) -> dict[str, Any]:
    try:
        from app.core import db

        raw = db.kv_get(_pend_key(uid), "") or ""
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_pending(uid: int, *, url: str, title: str) -> None:
    try:
        from app.core import db

        db.kv_set(_pend_key(uid), json.dumps(
            {"url": url, "title": title, "at": time.time()}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def _clear_pending(uid: int) -> None:
    try:
        from app.core import db

        db.kv_set(_pend_key(uid), "")
    except Exception:  # noqa: BLE001
        pass


def _affirm_deny(text: str) -> str:
    """判断这是不是一句"确认/否定"，短句才认（避免把长句子里的"是"当成确认）。"""
    body = (text or "").strip()
    if not body or len(body) > 10:
        return ""
    if any(k in body for k in _DENY_WORDS):
        return "deny"
    if any(k in body for k in _AFFIRM_WORDS):
        return "affirm"
    return ""


async def _send_cover_outcome(cfg: Any, *, user_qq: str, message_type: str,
                              group_id: str, res: dict[str, Any],
                              uid: int = 0) -> None:
    """把翻唱结果发回聊天：先一句话，再语音条（拿不到就发文件）。"""
    from app.memory import voices

    ok = bool(res.get("ok"))
    if message_type == "group":
        api = "send_group_msg"
        base = {"group_id": group_id}
    else:
        api = "send_private_msg"
        base = {"user_id": int(user_qq)}

    if not ok:
        reason = str(res.get("reason") or "")
        if reason == "confirm":
            # 搜出来的结果和关键词对不上：先问一句，别替人唱错歌。
            # 同时把"候选"记下来，等他回"是的"就直接开唱（不然就变成只问不唱）。
            if uid:
                _save_pending(uid, url=str(res.get("url") or ""),
                              title=str(res.get("title") or ""))
            line = f"我搜到的是《{res.get('title') or '一首别的歌'}》，你说的是这首吗？是的话我再练。"
        elif reason == "not-found":
            line = "这首歌我没搜到，换个名字或者直接甩我个链接？"
        else:
            line = "这首我练了半天没练下来，可能音太高了……下次换一首？"
        await _post_api(cfg, api, {**base, "message": [{"type": "text", "data": {"text": line}}]})
        return

    title = str(res.get("title") or "刚学的歌")
    # B 站标题往往一长串（"在百万豪装录音棚大声听 杨丞琳《雨爱》【Hi-res】"），
    # 能抠出书名号就用歌名，抠不出就截断，别把整条标题念给用户
    short = title
    _m = re.search(r"《([^》]{1,20})》", title)
    if _m:
        short = _m.group(1)
    elif len(title) > 20:
        short = title[:20]
    text = f"《{short}》我练好了，你听听？"
    await _post_api(cfg, api, {**base, "message": [{"type": "text", "data": {"text": text}}]})

    clip = res.get("clip_amr")
    mp3 = res.get("mp3")
    total = float(res.get("seconds") or 0)
    sent_clip = False
    if clip:
        try:
            uri = voices.sendable(pathlib.Path(str(clip))).resolve().as_uri()
            got = await _post_api(cfg, api, {**base, "message": [{"type": "record", "data": {"file": uri}}]})
            sent_clip = got is not None
        except Exception:  # noqa: BLE001 - 语音条失败就退化成发文件
            sent_clip = False
    # 语音条只有一段（<=60 秒），所以：
    #   超过 60 秒的歌 → 语音条 + 整首音频**两个都发**（语音条试听、文件听全）
    #   60 秒以内的短视频 → 语音条就够，不用再塞文件
    need_file = bool(mp3) and (total > 60 or not sent_clip)
    if need_file:
        # NapCat 的 upload_* 只认绝对路径 / file:// URI；cover 回来的是相对路径，
        # 直接传会得到 "识别URL失败, uri= data\covers\..."（真机踩到）
        mp3_abs = pathlib.Path(str(mp3)).resolve()
        try:
            if message_type == "group":
                await _post_api(cfg, "upload_group_file", {
                    "group_id": group_id, "file": str(mp3_abs),
                    "name": mp3_abs.name,
                }, timeout=180)
            else:
                await _post_api(cfg, "upload_private_file", {
                    "user_id": int(user_qq), "file": str(mp3_abs),
                    "name": mp3_abs.name,
                }, timeout=180)
        except Exception:  # noqa: BLE001
            pass


def _start_cover(cfg: Any, *, target: str, user_qq: str, message_type: str,
                 group_id: str, uid: int = 0) -> None:
    """后台线程跑整条翻唱管线（阻塞两分钟），完了回主事件循环发结果。"""
    loop_obj = asyncio.get_running_loop()

    def _worker() -> None:
        try:
            from app.voice import cover
        except Exception as exc:  # noqa: BLE001 - 管线没装就当没这个功能
            res = {"ok": False, "reason": "error", "detail": f"cover 模块不可用：{exc}"}
        else:
            res: dict[str, Any] = {"ok": False, "reason": "error"}
            try:
                # 先解析目标：搜出来的歌跟关键词对不上时先问一句，别替人唱错歌
                probe = cover.resolve(target)
                if not probe.get("ok"):
                    res = {"ok": False, "reason": probe.get("reason") or "not-found",
                           "detail": probe.get("detail")}
                elif probe.get("how") == "search" and probe.get("matched") is False:
                    res = {"ok": False, "reason": "confirm", "title": probe.get("title"),
                           "url": probe.get("url") or ""}
                else:
                    res = cover.run_cover(target)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "reason": "error", "detail": str(exc)}
        try:
            asyncio.run_coroutine_threadsafe(
                _send_cover_outcome(cfg, user_qq=user_qq, message_type=message_type,
                                    group_id=group_id, res=res, uid=uid),
                loop_obj,
            ).result(timeout=120)
        except Exception:  # noqa: BLE001 - 结果发不出去也别把线程炸了
            pass

    threading.Thread(target=_worker, name="qq-cover", daemon=True).start()



def _segments_text(message: Any) -> tuple[str, list[str]]:
    """OneBot message（数组/CQ 字符串）→ (纯文本, at 的 QQ 列表)。"""
    if isinstance(message, str):
        return message.strip(), []
    texts: list[str] = []
    ats: list[str] = []
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data") or {}
        if seg_type == "text":
            texts.append(str(data.get("text", "")))
        elif seg_type == "at":
            ats.append(str(data.get("qq", "")))
    return " ".join(t.strip() for t in texts if t.strip()).strip(), ats


def _segments_images(message: Any) -> list[dict[str, Any]]:
    """OneBot message → 图片附件列表（给工具环节"看"）。

    OneBot v11 的图片段形如 {"type":"image","data":{"url":"https://…","file":"xxx"}}。
    优先取 `url`；只有 `file` 时（NapCat 本地缓存名）拿不到就跳过——宁可不看，也别瞎猜。
    """
    if not isinstance(message, list):
        return []
    out: list[dict[str, Any]] = []
    for seg in message:
        if not isinstance(seg, dict) or seg.get("type") != "image":
            continue
        data = seg.get("data") or {}
        url = str(data.get("url") or "").strip()
        if not url:
            continue
        out.append({"type": "image", "url": url})
        if len(out) >= 4:  # 一轮最多收 4 张，真看几张由 tools.vision.max_images 决定
            break
    return out


def _append_outbox(record: dict[str, Any]) -> None:
    try:
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _send_ok(resp: dict[str, Any] | None) -> bool:
    """NapCat 的"发出去了吗"要看 status，不是只看 HTTP 200。

    真机上遇到过：HTTP 200 但 `{"status": "failed", "retcode": 200, "message": "..."}`,
    以前一律当成功 → **面板有她说的话、QQ 一条没有**（2026-11-02 owner 反馈）。
    """
    if not resp:
        return False
    return str(resp.get("status") or "ok").lower() in ("ok", "async")


async def _post_api(cfg, api: str, payload: dict[str, Any],
                    timeout: float | None = None) -> dict[str, Any] | None:
    url = f"{str(cfg.api_base).rstrip('/')}/{api}"
    headers = {"Authorization": f"Bearer {cfg.access_token}"} if cfg.access_token else {}
    try:
        # 传文件（upload_group_file / upload_private_file）比发消息慢得多，
        # 用默认的 10 秒会超时——超时被吞掉，表现就是"语音条发了、文件没发"。
        wait = float(timeout if timeout is not None else (cfg.timeout_s or 10))
        async with httpx.AsyncClient(timeout=wait) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if not resp.is_success:
                return None
            try:
                return resp.json()
            except ValueError:
                return {"ok": True}
    except Exception:  # noqa: BLE001 - 发送失败写 outbox，不抛给 QQ
        return None


async def get_login_info(cfg) -> dict[str, Any]:
    """问 NapCat 当前登录的是谁（登录在 NapCat 那边，控制台只查状态、不碰密码）。"""
    info = await _post_api(cfg, "get_login_info", {})
    status = await _post_api(cfg, "get_status", {})
    if info is None and status is None:
        return {"online": False, "reason": "NapCat 不通：检查 api_base、token、小号是否已登录"}
    data = (info or {}).get("data") if isinstance(info, dict) else None
    data = data if isinstance(data, dict) else {}
    st = (status or {}).get("data") if isinstance(status, dict) else None
    st = st if isinstance(st, dict) else {}
    uid = str(data.get("user_id") or "")
    return {
        "online": bool(uid or st.get("online") or st.get("good")),
        "user_id": uid,
        "nickname": str(data.get("nickname") or ""),
        "good": bool(st.get("good", True)),
        "api_base": str(getattr(cfg, "api_base", "") or ""),
    }


async def get_friend_list(cfg) -> list[dict[str, Any]] | None:
    """拉 QQ 真实好友列表（OneBot v11 get_friend_list）。拿不到返回 None。"""
    got = await _post_api(cfg, "get_friend_list", {})
    if not isinstance(got, dict):
        return None
    data = got.get("data")
    if not isinstance(data, list):
        return None
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("user_id") or "").strip()
        if not uid:
            continue
        out.append({
            "openid": uid,
            "nickname": str(item.get("remark") or item.get("nickname") or "").strip(),
        })
    return out


async def get_group_members(cfg, group_id: int | str) -> list[dict[str, Any]] | None:
    """拉群成员列表（OneBot v11 get_group_member_list）。拿不到返回 None。"""
    got = await _post_api(cfg, "get_group_member_list", {"group_id": int(group_id)})
    if not isinstance(got, dict):
        return None
    data = got.get("data")
    if not isinstance(data, list):
        return None
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("user_id") or "").strip()
        if not uid:
            continue
        out.append({
            "openid": uid,
            "nickname": str(item.get("card") or item.get("nickname") or "").strip(),
            "role": str(item.get("role") or ""),
        })
    return out


async def _handle_friend_request(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    """好友申请：先记下来；`onebot.auto_accept_friend_request`（默认开）就直接通过。

    关掉时申请留在控制台，等人点"通过"——那时才回 set_friend_add_request。
    """
    from app.relation import friends as friend_mod

    if str(payload.get("request_type") or "") != "friend":
        return {"ok": True, "ignored": True, "request_type": payload.get("request_type")}

    openid = str(payload.get("user_id") or "")
    if not openid or (cfg.self_id and openid == str(cfg.self_id)):
        return {"ok": True, "ignored": True, "reason": "self request"}

    flag = str(payload.get("flag") or "")
    comment = str(payload.get("comment") or "")
    rid = friend_mod.record(flag=flag, openid=openid, comment=comment, source="onebot")

    if not bool(getattr(cfg, "auto_accept_friend_request", True)):
        return {"ok": True, "mode": "friend_request_pending", "request_id": rid, "openid": openid}

    ok = await accept_request(cfg, rid)
    return {
        "ok": True,
        "mode": "friend_request_accepted" if ok else "friend_request_failed",
        "request_id": rid,
        "openid": openid,
    }


async def accept_request(cfg, request_id: int, approve: bool = True) -> bool:
    """回 set_friend_add_request。成功才算真通过，失败就把状态留着让人重试。"""
    from app.relation import friends as friend_mod
    from app.relation import users as users_mod

    row = friend_mod.get(request_id)
    if row is None or not str(row.get("flag") or ""):
        if row is not None:
            friend_mod.mark(request_id, friend_mod.FAILED, "没有 flag，OneBot 不认这条申请")
        return False

    sent = await _post_api(
        cfg,
        "set_friend_add_request",
        {"flag": str(row["flag"]), "approve": bool(approve)},
    )
    good = sent is not None and (sent.get("ok") is True or str(sent.get("status")) == "ok")
    if good:
        friend_mod.mark(
            request_id,
            friend_mod.ACCEPTED if approve else friend_mod.REJECTED,
            "" if approve else "手动拒绝",
        )
        if approve and str(row.get("openid") or ""):
            users_mod.ensure_user(str(row["openid"]), str(row.get("nickname") or ""))
    else:
        friend_mod.mark(request_id, friend_mod.FAILED, "OneBot 没确认（检查 NapCat 是否在线/有权限）")
    return bool(good)


@router.post("/onebot/event")
async def onebot_event(request: Request) -> dict[str, Any]:
    channel.set_channel("onebot")  # NapCat 全量能力
    rt = get_runtime()
    cfg = rt.cfg.onebot
    if not cfg.enabled:
        return {"ok": False, "ignored": True, "reason": "onebot disabled"}

    if cfg.access_token:
        auth = str(request.headers.get("authorization", ""))
        if auth not in (f"Bearer {cfg.access_token}", str(cfg.access_token)):
            return {"ok": False, "ignored": True, "reason": "bad token"}

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "bad json"}

    # 好友申请：OneBot 只给事件，真正的"通过"要我们回 set_friend_add_request
    if payload.get("post_type") == "request":
        return await _handle_friend_request(cfg, payload)

    if payload.get("post_type") != "message":
        return {"ok": True, "ignored": True, "post_type": payload.get("post_type")}

    self_id = str(payload.get("self_id") or "")
    if cfg.self_id and self_id and self_id != str(cfg.self_id):
        return {"ok": True, "ignored": True, "reason": "other bot"}

    user_qq = str(payload.get("user_id") or "")
    if not user_qq or (cfg.self_id and user_qq == str(cfg.self_id)):
        return {"ok": True, "ignored": True, "reason": "self message"}
    if user_qq in {str(x) for x in (getattr(cfg, "ignore_ids", None) or [])}:
        # 群里另一个机器人（官方 bot）发的消息：不当成人话，否则两个 bot 互相抬杠
        return {"ok": True, "ignored": True, "reason": "ignored id"}

    text, ats = _segments_text(payload.get("message") or payload.get("raw_message") or "")
    _images = _segments_images(payload.get("message"))
    if not text and _images:
        # 只发了一张图：给她一个占位文本，让工具环节去看图（不然直接被当空消息丢掉）
        text = "[图片]"
    if not text:
        return {"ok": True, "ignored": True, "reason": "empty"}

    message_type = str(payload.get("message_type") or "private")
    sender = payload.get("sender") or {}
    _known_before = users_mod.get_user_by_openid(user_qq) is not None
    user = users_mod.ensure_user(
        user_qq, str(sender.get("card") or sender.get("nickname") or "")
    )
    user_id = int(user["id"])

    # ---------- 群聊增强：静默指令最优先，命中就不再说话 ----------
    if message_type == "group":
        from app.group import context as gctx, proactive as gpro
        from app.group import active as gactive

        gactive.remember_group(payload.get("group_id"))
        # 群作用域键：静默/焦点/配额都按群各算各的，别让 A 群的状态污染 B 群
        _gid = str(payload.get("group_id") or "").strip()

        if gpro.detect_stop(text):
            until = gpro.silence(gpro.STOP_MINUTES, gid=_gid)
            return {"ok": True, "ignored": True, "mode": "silenced", "until": until,
                    "minutes": gpro.STOP_MINUTES}

        # 静默期：完全不输出。唯一例外是被 @（或叫她的名字）——那算对方主动找她
        silent, why_silent = gpro.is_silenced(_gid)
        if silent:
            mentioned_now = str(cfg.self_id or "") in ats
            if not mentioned_now:
                lowered_now = text.lower()
                names_now = [str(k) for k in (cfg.mention_keywords or [])] + [str(rt.persona.name)]
                mentioned_now = any(k and k.lower() in lowered_now for k in names_now)
            if not mentioned_now:
                # 普通消息：一句话都不回
                return {"ok": True, "ignored": True, "mode": "silenced", "reason": why_silent}
            # 被 @ → 打破静默，正常回应
            gpro.break_silence(by="@", gid=_gid)

        # 她连发 2~3 条都没人接 → 不再主动开口（等对方先说话）
        quiet, why_quiet = gpro.should_be_quiet(_gid)
        if quiet and not str(cfg.self_id or "") in ats:
            return {"ok": True, "ignored": True, "mode": "standby", "reason": why_quiet}

        # 昵称变更（别叫我 xx / 以后叫我 xx）
        nick_change = gctx.handle_nick_change(text, user_id)
        if nick_change is not None:
            if nick_change.get("need_name"):
                return {"ok": True, "ignored": True, "mode": "ask_nick",
                        "hint": "让他自己说一个想被怎么叫"}
            return {"ok": True, "ignored": True, "mode": "nick_set",
                    "nick": nick_change.get("nick")}

        # 没 @ 也没提名字：判断上下文是否在跟她说话
        mentioned = str(cfg.self_id or "") in ats
        if not mentioned:
            lowered = text.lower()
            names = [str(k) for k in (cfg.mention_keywords or [])] + [str(rt.persona.name)]
            mentioned = any(k and k.lower() in lowered for k in names)
        if not mentioned:
            talking, why = gctx.who_is_talking_to_her(text, user_id, False, _gid)
            if not talking:
                return {"ok": True, "ignored": True, "reason": "not talking to her", "why": why}
        else:
            # 她刚被 @：记下来，供"焦点转移→待机"判断（按群存，互不覆盖）
            from app.core import db as _db

            _db.kv_set(_db.kv_key("group_focus_user", _gid), str(user_id))
            _db.kv_set(_db.kv_key("group_focus_at", _gid),
                       gctx.now().isoformat(timespec="seconds"))

    if message_type == "group" and cfg.group_at_only and not mentioned:
        # group_at_only = 只回应"叫她"的消息：@、叫名字、或刚被她接过话的那个人。
        # 以前这里是空分支（`pass`），配置项其实完全没生效（docs/23 §3.1 第 1 条）。
        from app.core import db as _gdb

        _gate_gid = str(payload.get("group_id") or "").strip()
        _focus = (_gdb.kv_get(_gdb.kv_key("group_focus_user", _gate_gid), "") or "").strip()
        if _focus != str(user_id):
            return {"ok": True, "ignored": True, "mode": "at_only",
                    "reason": "group_at_only=1，且这条消息既没 @ 她、也没叫她名字"}

    # 搜视频意图："搜一下XX" → 直接搜 B 站并回复链接，不必走模型
    search_kw = trend_mod.match_search_intent(text)
    if search_kw:
        try:
            videos = bili.search_videos(search_kw, 3)
        except Exception:  # noqa: BLE001
            videos = []
        if videos:
            lines = [f"{i + 1}. {v['title']}\n{v['url']}" for i, v in enumerate(videos)]
            body = "搜到几个：\n" + "\n".join(lines)
            seg: list[dict[str, Any]] = []
            if message_type == "group":
                seg.append({"type": "at", "data": {"qq": user_qq}})
                seg.append({"type": "text", "data": {"text": " " + body}})
            else:
                seg.append({"type": "text", "data": {"text": body}})
            api = "send_group_msg" if message_type == "group" else "send_private_msg"
            key = "group_id" if message_type == "group" else "user_id"
            val = payload.get("group_id") if message_type == "group" else int(user_qq)
            sent = await _post_api(cfg, api, {key: val, "message": seg})
            if sent is None:
                _append_outbox({"api": api, "payload": {key: val, "message": seg}, "user_qq": user_qq})
            return {
                "ok": True, "replied": True, "mode": "search",
                "keyword": search_kw, "found": len(videos), "sent": sent is not None,
            }
        # 搜不到就当普通聊天往下走

    chat_type = "group" if message_type == "group" else "private"
    group_id = str(payload.get("group_id") or "") if message_type == "group" else ""

    # ---------- 不懂就查：先说"我想一想"，查完再回来结合关系/分数/情景答 ----------
    prefetched_hint = ""
    slow = False
    try:
        from app.memory import store
        from app.web import lookup as lookup_mod

        _kw = lookup_mod.needs_lookup(text)
        if _kw and not users_mod.is_blocked(int(user["id"])):
            _ack = "我想一想。"
            if message_type == "group":
                _api = "send_group_msg"
                _ack_body = {
                    "group_id": payload.get("group_id"),
                    "message": [
                        {"type": "at", "data": {"qq": user_qq}},
                        {"type": "text", "data": {"text": " " + _ack}},
                    ],
                }
            else:
                _api = "send_private_msg"
                _ack_body = {
                    "user_id": int(user_qq),
                    "message": [{"type": "text", "data": {"text": _ack}}],
                }
            await _post_api(cfg, _api, _ack_body)
            store.add_event("her", _ack, source="qq_onebot", user_id=int(user["id"]),
                            meta={"lookup_ack": True}, chat_type=chat_type, group_id=group_id)
            prefetched_hint = lookup_mod.prompt_hint(_kw, text)
            slow = bool(prefetched_hint)
    except Exception:  # noqa: BLE001 - 查资料失败就当普通聊天
        prefetched_hint = ""
        slow = False

    # ---------- 爹否决恋情：分手 + 清亲密度 + 分手信 + 告别 + 向他汇报 ----------
    # 放在最前面：这是"家长拍板"，不能被后面的点歌/传话分支抢走。
    try:
        from app.relation import love as _love_mod
        from app.relation import session as _session_mod
        from app.relation import users as _users_mod
        from app.relation import veto as _veto_mod

        _role = _love_mod.family_role(int(user["id"]))
        if _role and _veto_mod.wants_veto(text) and _users_mod.current_lover(_session_mod.current_seq()):
            _v = await _veto_mod.veto(rt, cfg, father_uid=int(user["id"]), reason=text[:60])
            if _v.get("ok"):
                _vreply = str(_v.get("report") or "分了。")
                _vapi = "send_group_msg" if message_type == "group" else "send_private_msg"
                _vbase = ({"group_id": payload.get("group_id")} if message_type == "group"
                          else {"user_id": int(user_qq)})
                await _post_api(cfg, _vapi, {**_vbase, "message": [
                    {"type": "text", "data": {"text": _vreply}},
                ]})
                _log("爹否决执行：" + "；".join(_v.get("steps") or []))
                return {"ok": True, "replied": True, "veto": True,
                        "lover": _v.get("lover_nick"), "steps": _v.get("steps")}
    except Exception as _exc:  # noqa: BLE001 - 否决流程失败就当普通聊天
        _log(f"爹否决流程失败：{_exc}")

    # ---------- 公开与否：上一轮她问过"要不要公开"，这一轮收答案 ----------
    try:
        from app.core import db as _dbm
        from app.memory import store as _store_pub

        _pk = f"lover_public_pending:{int(user['id'])}"
        if _dbm.kv_get(_pk, "") == "1":
            _body = (text or "").strip()
            _no = any(k in _body for k in ("不公开", "别说", "保密", "先别", "不要", "不"))
            _yes = any(k in _body for k in ("公开", "可以说", "告诉他们", "行", "好", "可以", "随你"))
            _ans = ""
            if _no:
                _ans = "不公开"
            elif _yes:
                _ans = "公开"
            if _ans:
                _dbm.kv_set(_pk, "")
                _dbm.kv_set(f"lover_public:{int(user['id'])}", _ans)
                try:
                    from app.memory import h2 as _h2p

                    _h2p.add("fact", f"关于我们恋情的公开范围，我们说定了：{_ans}。",
                             importance=0.6, stability=0.6, cues=["公开", "恋人", "约定"],
                             user_id=int(user["id"]), chat_type=chat_type,
                             extra_meta={"lover_public": _ans})
                except Exception:  # noqa: BLE001
                    pass
                _pack = ("好，那我就不瞒了。" if _ans == "公开" else "行，那就先我们俩知道。")
                _store_pub.add_event("her", _pack, source="qq_onebot", user_id=int(user["id"]),
                                     meta={"lover_public": _ans}, chat_type=chat_type,
                                     group_id=group_id)
                _papi = "send_group_msg" if message_type == "group" else "send_private_msg"
                _pbase = ({"group_id": payload.get("group_id")} if message_type == "group"
                          else {"user_id": int(user_qq)})
                await _post_api(cfg, _papi, {**_pbase, "message": [
                    {"type": "text", "data": {"text": _pack}},
                ]})
                return {"ok": True, "replied": True, "lover_public": _ans}
    except Exception as _exc:  # noqa: BLE001
        _log(f"公开问答处理失败：{_exc}")

    # ---------- 执行模块：他说"去跟某人说…"，她真的去发（不是嘴上说说） ----------
    try:
        from app.mind import contact as contact_mod

        if contact_mod.wants_contact(text):
            _parsed = contact_mod.parse_contact(text)
            if _parsed:
                _tgt = contact_mod.resolve_target(str(_parsed.get("target") or ""))
                _spoke = ""
                if _tgt is None:
                    _spoke = f"「{_parsed.get('target')}」我不认识啊，名字是不是打错了？"
                else:
                    _composed = await contact_mod.compose(
                        rt, _tgt, hint=str(_parsed.get("content") or ""),
                        tone_hint=str(_parsed.get("tone") or ""),
                    )
                    _spoken = str(_composed.get("text") or "").strip()
                    _ok = False
                    if _spoken:
                        _sent = await _post_api(
                            cfg, "send_private_msg",
                            {"user_id": int(_tgt["openid"]),
                             "message": [{"type": "text", "data": {"text": _spoken}}]},
                            timeout=30,
                        )
                        _ok = bool(_sent) and str(_sent.get("status") or "") == "ok"
                    # 真发出去了才记账，也才写进记忆（她知道"我问候过他"这件事）
                    contact_mod.log_event(int(user["id"]), [_spoken], target=_tgt,
                                          tone=str(_composed.get("tone") or ""), ok=_ok,
                                          reason=str(_composed.get("reason") or ""))
                    contact_mod.remember(_tgt, [_spoken], tone=str(_composed.get("tone") or ""),
                                         ok=_ok, reason=str(_composed.get("reason") or ""))
                    if _ok:
                        _spoke = f"说了。我这么跟{_tgt.get('nickname')}说的：「{_spoken}」"
                    else:
                        _spoke = f"我去找{_tgt.get('nickname')}了，但这句没送出去……等下再试。"
                if _spoke:
                    try:
                        from app.memory import store as _store3

                        _store3.add_event("her", _spoke, source="qq_onebot",
                                          user_id=int(user["id"]),
                                          meta={"contact_report": True},
                                          chat_type=chat_type, group_id=group_id)
                    except Exception:  # noqa: BLE001
                        pass
                    _r_api = "send_group_msg" if message_type == "group" else "send_private_msg"
                    _r_base = ({"group_id": payload.get("group_id")} if message_type == "group"
                               else {"user_id": int(user_qq)})
                    _r_segs: list[dict[str, Any]] = []
                    if message_type == "group":
                        _r_segs.append({"type": "at", "data": {"qq": user_qq}})
                        _r_segs.append({"type": "text", "data": {"text": " " + _spoke}})
                    else:
                        _r_segs.append({"type": "text", "data": {"text": _spoke}})
                    await _post_api(cfg, _r_api, {**_r_base, "message": _r_segs})
                    return {"ok": True, "replied": True, "contact": _parsed.get("target"),
                            "delivered": bool(_tgt)}
    except Exception:  # noqa: BLE001 - 执行失败就当普通聊天，别把消息吞了
        pass

    # ---------- 悬案优先：上一轮她问过"你说的是这首吗"，这一轮先看是不是在回答 ----------
    _typed_uid = int(user["id"])
    try:
        _pend = _load_pending(_typed_uid)
        if _pend:
            if time.time() - float(_pend.get("at") or 0) > _PEND_TTL_S:
                _clear_pending(_typed_uid)
            else:
                _ans = _affirm_deny(text)
                if _ans == "affirm" and _pend.get("url"):
                    _clear_pending(_typed_uid)
                    _go = str(getattr(rt.cfg.voice, "cover_ack", "") or "好，那我练了。")
                    _go = "好，那我练了。"
                    if message_type == "group":
                        _go_api = "send_group_msg"
                        _go_body = {"group_id": payload.get("group_id"), "message": [
                            {"type": "at", "data": {"qq": user_qq}},
                            {"type": "text", "data": {"text": " " + _go}},
                        ]}
                    else:
                        _go_api = "send_private_msg"
                        _go_body = {"user_id": int(user_qq),
                                    "message": [{"type": "text", "data": {"text": _go}}]}
                    await _post_api(cfg, _go_api, _go_body)
                    from app.memory import store as _store2

                    _store2.add_event("her", _go, source="qq_onebot", user_id=_typed_uid,
                                      meta={"cover_ack": True, "confirmed": True,
                                            "target": _pend.get("url")},
                                      chat_type=chat_type, group_id=group_id)
                    _start_cover(cfg, target=str(_pend["url"]), user_qq=user_qq,
                                 message_type=message_type, group_id=group_id, uid=_typed_uid)
                    return {"ok": True, "replied": True, "cover_confirmed": True,
                            "target": _pend.get("url")}
                if _ans == "deny":
                    _clear_pending(_typed_uid)
    except Exception:  # noqa: BLE001 - 悬案处理失败不该挡住正常聊天
        pass

    # ---------- 点歌：先回一句"我先练一下"，后台去下载/分离/换声 ----------
    _want_cover = ""
    try:
        if bool(getattr(rt.cfg.voice, "cover_enabled", True)):
            _want_cover = _cover_target(text)
    except Exception:  # noqa: BLE001 - 没配这个功能就当没有
        _want_cover = ""
    if _want_cover:
        try:
            from app.memory import store as _store

            _ack = str(getattr(rt.cfg.voice, "cover_ack", "") or "我先练一下。")
            if message_type == "group":
                _ack_api = "send_group_msg"
                _ack_body = {
                    "group_id": payload.get("group_id"),
                    "message": [
                        {"type": "at", "data": {"qq": user_qq}},
                        {"type": "text", "data": {"text": " " + _ack}},
                    ],
                }
            else:
                _ack_api = "send_private_msg"
                _ack_body = {"user_id": int(user_qq),
                             "message": [{"type": "text", "data": {"text": _ack}}]}
            await _post_api(cfg, _ack_api, _ack_body)
            _store.add_event("her", _ack, source="qq_onebot", user_id=int(user["id"]),
                             meta={"cover_ack": True, "target": _want_cover},
                             chat_type=chat_type, group_id=group_id)
            _start_cover(cfg, target=_want_cover, user_qq=user_qq,
                         message_type=message_type, group_id=group_id, uid=int(user["id"]))
            return {"ok": True, "replied": True, "cover": _want_cover, "ack": _ack}
        except Exception:  # noqa: BLE001 - 点歌链路挂了也继续正常聊天
            pass

    # ---------- 语音节奏：这一轮能不能发语音 / 要问要不要继续 / 该喊累了 ----------
    _vsess: dict[str, Any] = {"allow": True, "ask_now": False}
    _extra_hint = ""
    try:
        from app.voice import session as _vsession

        _vsess = _vsession.decide(rt, int(user["id"]), text)
        _extra_hint = "\n".join(
            x for x in (_vsess.get("ask_hint"), _vsess.get("tired_hint")) if x
        )
    except Exception:  # noqa: BLE001 - 节奏控制失败不该影响聊天
        _vsess, _extra_hint = {"allow": True, "ask_now": False}, ""

    # ---------- 亲密模式：走共享门控（和控制台同一条路） ----------
    from app.intimacy import gate as igate

    new_mode, note, is_lover, is_private = igate.prepare(int(user["id"]), text, chat_type)

    result = await loop.chat_turn(
        rt, text, source="qq_onebot", user_id=int(user["id"]),
        chat_type=chat_type, group_id=group_id,
        intimate_mode=new_mode.value, intimate_note=note, is_lover=is_lover,
        prefetched_hint=prefetched_hint, slow=slow,
        extra_hint=_extra_hint,
        attachments=_images or None,
    )

    # ---------- 家里人的知情权：她谈恋爱/分手了，得跟她爹说一声 ----------
    try:
        _love_action = str((result.get("meta") or {}).get("love") or "")
        if _love_action in ("bound", "breakup", "ended"):
            from app.relation import announce as _announce

            _subject = int((result.get("meta") or {}).get("user_id") or user["id"])
            _event = (_announce.lover_event(_subject) if _love_action == "bound"
                      else f"你跟「{_announce.nick_of(_subject)}」分开了，你爹还不知道。")
            _said = await _announce.tell_fathers(
                rt, cfg, event=_event, kind=_love_action,
                subject_uid=_subject, subject_name=_announce.nick_of(_subject),
            )
            if _said:
                _log_sent = "、".join(
                    f"{x['to']}({'已送达' if x['ok'] else '没送到'})" for x in _said
                )
                _log(f"家人知情通知：{_log_sent}")
            if _love_action == "bound" and bool(
                getattr(getattr(rt.cfg, "relationship", None), "lover", None)
                and getattr(rt.cfg.relationship.lover, "ask_public", True)
            ):
                # 绑上了要问一句"要不要公开"（答案在 handler 开头那段收）
                _ask = random.choice((
                    "……对了，我们这样，要不要公开？",
                    "话说，这事能跟大家说吗？",
                    "我们的事，你想公开还是先瞒着？",
                    "……要不要跟别人说我们在一起了。",
                ))
                try:
                    (result.get("reply") or []).append(_ask)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as _exc:  # noqa: BLE001 - 通知失败不许影响给她回话
        _log(f"家人知情通知失败：{_exc}")

    # 记录她的回复
    igate.finish(int(user["id"]), new_mode, result.get("reply") or [], is_private)
    replies = [str(x).strip() for x in (result.get("reply") or []) if str(x).strip()] or ["……"]
    # 节奏兜底：模型不一定每次都照着提示写，所以"喊累"和"问要不要继续"各留一句保险。
    # 问句从池子里随机取，不会每轮一模一样。
    if _vsess.get("tired"):
        _tired_lines = ("……嗓子有点哑了，先打字陪你。", "说了好多，让我歇会儿嘴。", "嘴巴干了，先打字啦。")
        if not any(k in replies[-1] for k in ("哑", "累", "歇", "打字", "嗓子")):
            replies.append(random.choice(_tired_lines))
    if _vsess.get("ask_now"):
        _ask_lines = ("还听不？", "要不要再来一段？", "还想听我说话吗？", "继续吗？")
        if not any(("？" in r or "?" in r) for r in replies[-2:] if r):
            replies.append(random.choice(_ask_lines))
    # 补出来的那几句也得进 events——她说出口的话必须留痕（docs/23 铁律）
    try:
        if len(replies) > len(result.get("reply") or []):
            from app.memory import store as _store

            for _extra in replies[len(result.get("reply") or []):]:
                _store.add_event("her", _extra, source="qq_onebot", user_id=int(user["id"]),
                                 meta={"voice_rhythm": True}, chat_type=chat_type, group_id=group_id)
    except Exception:  # noqa: BLE001 - 记账失败不影响回复
        pass
    reply = "\n".join(replies)
    # 多气泡兼容：支持切分的通道（OneBot/面板）逐条发；官方通道只能整段发
    caps = channel.caps()
    split = bool(getattr(caps, "can_split", False)) and len(replies) > 1
    first_text = replies[0] if split else reply

    segments: list[dict[str, Any]] = []
    if message_type == "group":
        segments.append({"type": "at", "data": {"qq": user_qq}})
        segments.append({"type": "text", "data": {"text": " " + first_text}})
    else:
        segments.append({"type": "text", "data": {"text": first_text}})

    sticker: dict[str, Any] | None = None
    try:
        chance = float(cfg.sticker_chance or 0) if getattr(caps, "stickers", False) else 0.0
        if chance > 0 and random.random() < chance:
            from app.memory import stickers

            entry = stickers.pick((result.get("meta") or {}).get("anchor_scene"))
            path = stickers.file_path(entry) if entry else None
            if path is not None:
                sticker = entry
                segments.append({"type": "image", "data": {"file": path.resolve().as_uri()}})
    except Exception:  # noqa: BLE001 - 表情包失败不影响文字回复
        sticker = None

    voice_path: pathlib.Path | None = None
    try:
        from app.memory import voices

        # "唱歌/来一首/情书" 这类明确请求：必发，不受概率限制；
        # 其余按概率；**都要过会话节奏这一关**（喊累了就只打字）。
        forced = bool(voices.wants_sing(text)) and bool(getattr(caps, "voice", False))
        _allow_voice = bool(_vsess.get("allow", True))
        # 关键词命中（文件名即触发词，如 晚安.wav）是**确定**要发的，不该再掷骰子；
        # 概率只用来给"没命中任何词"的日常对话加一点语音。
        _stem_hit = None
        try:
            for _p in voices.list_voices():
                if _p.stem and len(_p.stem) >= 2 and _p.stem in text:
                    _stem_hit = _p
                    break
        except Exception:  # noqa: BLE001
            _stem_hit = None
        _by_chance = (
            getattr(caps, "voice", False)
            and float(cfg.voice_chance or 0) > 0
            and random.random() < float(cfg.voice_chance)
        )
        if _allow_voice and (_stem_hit is not None or forced or _by_chance):
            voice_path = _stem_hit or voices.pick_for_text(
                text, (result.get("meta") or {}).get("anchor_scene")
            )
            if voice_path is not None:
                voice_path = voices.sendable(voice_path)
                segments.append({"type": "record", "data": {"file": voice_path.resolve().as_uri()}})
                if forced:
                    # 她要"唱"，就别只回一句"不唱"
                    segments[0 if message_type != "group" else 1] = {
                        "type": "text",
                        "data": {"text": (" " if message_type == "group" else "") + "那就听一小段。"},
                    }
    except Exception:  # noqa: BLE001 - 语音失败不影响文字/表情包
        voice_path = None

    # 记一笔节奏：发了语音 +1，只发文字就不动计数
    try:
        from app.voice import session as _vsession

        if voice_path is not None:
            _vsession.note_voice(rt, int(user["id"]), asked=bool(_vsess.get("ask_now")))
        else:
            _vsession.note_text(rt, int(user["id"]))
        if _vsess.get("reason") == "wants":
            # 他明确说"还想听"：必须落盘切成语音模式，不然下一轮又被当成打字
            _vsession.mark_resume(int(user["id"]))
        if _vsess.get("tired"):
            # 这一轮已经"喊过累了"：落盘切成打字模式，别下轮再喊一遍
            _vsession.mark_tired(int(user["id"]))
    except Exception:  # noqa: BLE001
        pass

    # 文字转语音（TTS）：没有现成录音可发时，按开关把这句话合成成语音。
    # 只在 OneBot 通道（caps.voice=True）+ voice.enabled + voice.send_in_chat 同时成立时做。
    if voice_path is None and getattr(caps, "voice", False):
        try:
            from app.core import config as _cfg_mod

            _vcfg = _cfg_mod.get()
            if bool(getattr(_vcfg.voice, "enabled", False)) and bool(
                getattr(_vcfg.voice, "send_in_chat", False)
            ):
                from app.voice import tts as _tts

                _tts_res = await _tts.synth(_vcfg, first_text)
                if _tts_res.get("ok"):
                    _p = pathlib.Path(str(_tts_res["path"]))
                    segments.append({"type": "record", "data": {"file": _p.resolve().as_uri()}})
        except Exception:  # noqa: BLE001 - TTS 失败就当没合成，文字照发
            pass

    if message_type == "group":
        api = "send_group_msg"
        body = {"group_id": payload.get("group_id"), "message": segments}
    else:
        api = "send_private_msg"
        body = {"user_id": int(user_qq), "message": segments}

    sent = await _post_api(cfg, api, body)
    if not _send_ok(sent):
        # NapCat 会说 status=failed / retcode 非 0 但 HTTP 200：以前当成功，消息就静默丢了
        await asyncio.sleep(0.8)
        retry = await _post_api(cfg, api, body)
        if _send_ok(retry):
            sent = retry
        else:
            _append_outbox({"api": api, "payload": body, "user_qq": user_qq,
                            "reason": "send-failed", "resp": sent})
    elif split and len(replies) > 1:
        # 多气泡：剩下的话各发一条，像人连着敲几句
        import asyncio as _asyncio

        for extra in replies[1:4]:
            await _asyncio.sleep(0.45)
            body2 = {**body, "message": [{"type": "text", "data": {"text": extra}}]}
            got2 = await _post_api(cfg, api, body2)
            if not _send_ok(got2):
                _append_outbox({"api": api, "payload": body2, "user_qq": user_qq,
                                "reason": "bubble-send-failed", "resp": got2})

    # 新人第一句：回完话再 @ 他问“你叫什么、是谁”（同一个人只招呼一次）
    greeted = False
    if message_type == "group" and not _known_before:
        try:
            from app.memory import store
            from app.relation import greet as greet_mod

            oid = str(user_qq)
            if (greet_mod.greeting_enabled()
                    and not greet_mod.family_skip(oid)
                    and not users_mod.is_blocked(user_id)
                    and not greet_mod.already_greeted(oid)):
                gl = greet_mod.line()
                gseg = [
                    {"type": "at", "data": {"qq": user_qq}},
                    {"type": "text", "data": {"text": " " + gl}},
                ]
                gbody = {"group_id": payload.get("group_id"), "message": gseg}
                gsent = await _post_api(cfg, "send_group_msg", gbody)
                if gsent is None:
                    _append_outbox({"api": "send_group_msg", "payload": gbody, "user_qq": user_qq})
                greet_mod.mark_greeted(oid)
                store.add_event("her", gl, source="qq_onebot", user_id=user_id,
                                meta={"greet": True}, chat_type="group",
                                group_id=str(payload.get("group_id") or ""))
                greeted = True
        except Exception:  # noqa: BLE001 - 招呼失败不影响正常回复
            greeted = False

    return {
        "ok": True,
        "replied": True,
        "user_id": int(user["id"]),
        "message_type": message_type,
        "sent": sent is not None,
        "sticker": str((sticker or {}).get("id") or ""),
        "voice": voice_path.stem if voice_path is not None else "",
        "greeted": greeted,
    }
