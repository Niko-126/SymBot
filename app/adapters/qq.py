"""QQ 官方开放平台适配器（WebSocket 长连接 · 群 @ + 私聊 C2C）。

与 OneBot 的区别：
- 官方协议不需要公网 IP，也不需要 NapCat；进程主动连腾讯网关（`botpy` 官方 SDK）；
- 鉴权用 **AppID + Secret（开放平台「开发设置」里的 Token）**，SDK 自己换 access_token、保活、重连；
- 群里只收 **@ 她的消息**（官方公域消息权限）；私聊收 C2C 消息；
- 每个 openid 独立映射到 `qq_user`，好感/记忆按人隔离（走 `users.ensure_user`）。

配置（`config.local.yaml`，不入库）：
    qq:
      enabled: true
      auto_start: true
      appid: "102xxxxxx"
      token: "开放平台的 Token/Secret"
      sandbox: true

环境变量：AI_LIFE_QQ_ENABLED=1 / AI_LIFE_QQ_APPID / AI_LIFE_QQ_TOKEN

单独调试：`.venv\\Scripts\\python.exe -m app.adapters.qq`
﻿‍​​​‍﻿‍​​‌‌﻿‌‍‍‍‌‌‍﻿​﻿‌‍﻿‌‍﻿‍‌‌‍﻿"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import re
from typing import Any

from app.adapters import intake
from app.core import channel
from app.core import config as config_mod
from app.core import runtime
from app.mind import loop
from app.relation import users as users_mod

log = logging.getLogger("ai-life.qq")

#: 适配器运行状态（给 /v1/health.qq 看；进程内单实例）
STATUS: dict[str, Any] = {
    "enabled": False,
    "appid": "",
    "sandbox": True,
    "started": False,
    "connected": False,
    "events": 0,
    "last_event_at": "",
    "last_reply_at": "",
    "last_error": "",
}

_TASK: asyncio.Task | None = None
_CLIENT: Any = None

_MAX_REPLY = 1500  # QQ 单条消息长度上限以内
#: 官方群 @ 消息的 content 里会带 `<@!openid>` 这类提及标记，进内核前必须摘掉，
#: 否则她会把一串 openid 当成用户说的话。
_MENTION_RE = re.compile(r"<@!?[^>]{0,64}>")


def clean_inbound(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def status() -> dict[str, Any]:
    return dict(STATUS)


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load_botpy():
    try:
        import botpy  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 qq-botpy。安装：.venv\\Scripts\\python.exe -m pip install -r requirements-qq.txt"
        ) from exc
    return botpy


def _reply_text(result: dict[str, Any]) -> str:
    parts = result.get("reply") or []
    text = "\n".join(str(p) for p in parts if str(p).strip()).strip()
    if not text:
        text = (result.get("meta") or {}).get("hint") or "……"
    return text[:_MAX_REPLY]


async def _answer(text: str, *, source: str, user_id: int, chat_type: str,
                  group_id: str = "") -> str:
    """一条 QQ 消息 → 内核一轮对话 → 回复文本（与 OneBot 同一条主链）。"""
    rt = runtime.get()
    from app.intimacy import gate as igate

    mode, note, is_lover, is_private = igate.prepare(int(user_id), text, chat_type)
    try:
        result = await loop.chat_turn(
            rt, text, source=source, user_id=int(user_id),
            chat_type=chat_type, group_id=group_id,
            intimate_mode=mode.value, intimate_note=note, is_lover=is_lover,
        )
    except Exception as exc:  # noqa: BLE001 - 一条消息失败不许拖垮连接
        log.exception("chat_turn failed")
        STATUS["last_error"] = f"chat_turn: {type(exc).__name__}: {exc}"[:200]
        return "……等一下，我这边有点乱。"
    igate.finish(int(user_id), mode, result.get("reply") or [], is_private)
    STATUS["last_reply_at"] = _now()
    return _reply_text(result)


def _precheck_text(text: str, *, user_id: int, chat_type: str,
                   group_id: str = "", at_me: bool = True) -> dict[str, Any] | None:
    """前置层失败只记录，不吞消息（真机踩过：异常导致消息既没回也没落库）。"""
    try:
        return intake.precheck(runtime.get(), text, user_id=user_id, chat_type=chat_type,
                               group_id=group_id, at_me=at_me)
    except Exception as exc:  # noqa: BLE001
        log.exception("intake precheck failed")
        STATUS["last_error"] = f"precheck: {type(exc).__name__}: {exc}"[:200]
        return None


def _store_quick(user_id: int, text: str, chat_type: str, group_id: str = "") -> None:
    """前置层的短回复也要落库，不然面板/记忆里看不到她说过这句。"""
    try:
        from app.memory import store

        store.add_event("her", text, source="qq", user_id=int(user_id),
                        chat_type=chat_type, group_id=group_id)
    except Exception:  # noqa: BLE001
        pass


def build_client():
    """建 botpy Client（只在 botpy 可导入时调用）。"""
    botpy = _load_botpy()
    from botpy.message import C2CMessage, GroupMessage

    cfg = config_mod.get()

    class AILifeClient(botpy.Client):  # noqa: N801
        async def on_ready(self):
            STATUS["connected"] = True
            STATUS["last_error"] = ""
            log.info("QQ official bot ready: %s (sandbox=%s)",
                     getattr(self, "robot", None), cfg.qq.sandbox)

        async def on_group_at_message_create(self, message: GroupMessage):
            text = clean_inbound(message.content or "")
            if not text:
                return
            openid = str(getattr(message.author, "member_openid", "") or "")
            nick = str(getattr(message.author, "username", "") or "")
            user = users_mod.ensure_user(openid, nick)
            STATUS["events"] += 1
            STATUS["last_event_at"] = _now()
            log.info("group@ %s(%s): %s", nick, openid[-6:], text[:60])
            group_openid = str(getattr(message, "group_openid", "") or "")
            pre = _precheck_text(text, user_id=int(user["id"]), chat_type="group",
                                 group_id=group_openid, at_me=True)
            if pre is not None:
                if pre.get("skip"):
                    return
                quick = str(pre.get("reply") or "").strip()
                if quick:
                    try:
                        await message.reply(content=quick[:_MAX_REPLY])
                        STATUS["last_reply_at"] = _now()
                        _store_quick(int(user["id"]), quick, "group", group_openid)
                    except Exception as exc:  # noqa: BLE001
                        STATUS["last_error"] = f"group precheck: {type(exc).__name__}: {exc}"[:200]
                    return
            reply = await _answer(
                text, source="qq", user_id=int(user["id"]), chat_type="group",
                group_id=group_openid,
            )
            try:
                await message.reply(content=reply)
            except Exception as exc:  # noqa: BLE001 - 发送失败只记录，不影响下一条
                STATUS["last_error"] = f"group reply: {type(exc).__name__}: {exc}"[:200]
                log.warning("group reply failed: %s", exc)

        async def on_c2c_message_create(self, message: C2CMessage):
            text = clean_inbound(message.content or "")
            if not text:
                return
            openid = str(getattr(message.author, "user_openid", "") or "")
            nick = str(getattr(message.author, "username", "") or "")
            user = users_mod.ensure_user(openid, nick)
            STATUS["events"] += 1
            STATUS["last_event_at"] = _now()
            log.info("c2c %s(%s): %s", nick, openid[-6:], text[:60])
            pre = _precheck_text(text, user_id=int(user["id"]), chat_type="private")
            if pre is not None:
                if pre.get("skip"):
                    return
                quick = str(pre.get("reply") or "").strip()
                if quick:
                    try:
                        await message.reply(content=quick[:_MAX_REPLY])
                        STATUS["last_reply_at"] = _now()
                        _store_quick(int(user["id"]), quick, "private", "")
                    except Exception as exc:  # noqa: BLE001
                        STATUS["last_error"] = f"c2c precheck: {type(exc).__name__}: {exc}"[:200]
                    return
            reply = await _answer(text, source="qq_c2c", user_id=int(user["id"]),
                                  chat_type="private")
            try:
                await message.reply(content=reply)
            except Exception as exc:  # noqa: BLE001
                STATUS["last_error"] = f"c2c reply: {type(exc).__name__}: {exc}"[:200]
                log.warning("c2c reply failed: %s", exc)

    intents = botpy.Intents(public_messages=True)
    return AILifeClient(intents=intents, is_sandbox=bool(cfg.qq.sandbox))


async def _run_forever() -> None:
    """连接 → 断开 → 退避重连（5s 起，最多 60s）。"""
    cfg = config_mod.get()
    backoff = 5
    while True:
        try:
            client = build_client()
            global _CLIENT
            _CLIENT = client
            STATUS["started"] = True
            STATUS["connected"] = False
            await client.start(appid=cfg.qq.appid, secret=cfg.qq.token)
            STATUS["connected"] = False
            STATUS["last_error"] = "connection closed"
            log.warning("QQ official bot connection closed")
        except asyncio.CancelledError:
            STATUS["connected"] = False
            raise
        except Exception as exc:  # noqa: BLE001 - 凭据错误/网络失败都走这里
            STATUS["connected"] = False
            STATUS["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("QQ official bot crashed")
        await asyncio.sleep(backoff)
        backoff = min(60, backoff * 2)


async def start_background() -> bool:
    """由 FastAPI 启动事件调用；没凭据/没启用就静默不启。"""
    cfg = config_mod.get()
    STATUS.update(enabled=bool(cfg.qq.enabled), appid=str(cfg.qq.appid or ""),
                  sandbox=bool(cfg.qq.sandbox))
    if cfg.qq.enabled:
        missing = [key for key in ("appid", "token") if not getattr(cfg.qq, key)]
        if missing:
            STATUS["last_error"] = "missing " + "/".join(missing)
            log.warning("QQ adapter enabled but %s empty; not starting", "/".join(missing))
            return False
    if not (cfg.qq.enabled and cfg.qq.appid and cfg.qq.token):
        return False
    if not bool(getattr(cfg.qq, "auto_start", True)):
        return False
    # 官方开放平台：只能被动回文本；没有表情包/语音/群主动（能力位给内核判断）。
    # 只在**真的起来了**之后才切换全局能力位——以前无条件写在函数第一行，
    # 于是 qq.enabled=false 时进程的能力位也是官方通道，面板/OneBot 会被按单条发送处理。
    channel.set_channel("qq_official")
    global _TASK
    if _TASK is not None and not _TASK.done():
        return True
    _TASK = asyncio.create_task(_run_forever())
    log.info("QQ official adapter starting (appid=%s sandbox=%s)",
             str(cfg.qq.appid)[:6], cfg.qq.sandbox)
    return True


async def stop_background() -> None:
    global _TASK, _CLIENT
    task, _TASK = _TASK, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _CLIENT = None
    STATUS["connected"] = False


def main() -> int:
    cfg = config_mod.get()
    if not cfg.qq.enabled or not cfg.qq.appid or not cfg.qq.token:
        print("QQ 官方适配器还没配置。在 config.local.yaml 里加：")
        print('qq:\n  enabled: true\n  auto_start: true\n  appid: "102xxxxxx"\n'
              '  token: "开放平台的 Token/Secret"\n  sandbox: true')
        print("或设环境变量 AI_LIFE_QQ_ENABLED=1 / AI_LIFE_QQ_APPID=... / AI_LIFE_QQ_TOKEN=...")
        return 1
    runtime.init()
    log.info("connecting QQ official bot appid=%s sandbox=%s", cfg.qq.appid, cfg.qq.sandbox)
    client = build_client()
    client.run(appid=cfg.qq.appid, secret=cfg.qq.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
