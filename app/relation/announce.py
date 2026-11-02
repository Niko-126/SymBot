"""家里人的知情权：她谈恋爱了，得告诉她爹一声。

为什么要有它（2026-11-02 owner 提的）：
`love.try_turn()` 只会把关系绑成恋人（`bind_lover`），从头到尾没跟任何"家里人"说一声。
结果就是——她在 QQ 里跟人处上了对象，她爹（`family_role()` 认出来的生父/干爹）
在面板上能看到 lover 表多了一行，人却什么都不知道。这不是"忘了说"，
是**根本没有这条出口**。

设计：
- 只通知家人（`family_role(uid)` 返回 生父/干爹 的 QQ 用户），不群发、不广播；
- 措辞交给模型（她的语气），但事件和对象由调用方给死，别让她编；
- **真发出去了才记账**，也才写进记忆（她"记得自己跟爹说过"）；
- 面板那条线（uid=0）不通知，面板自己就是她爹在看。

依赖：不 import app.api.onebot（会循环导入），自己用 httpx 走 NapCat。
﻿‍‌​​‍﻿‌‍﻿‌﻿‍﻿‌‍​‍​﻿‌​​​​​﻿‍‌‌​‍​﻿"""

from __future__ import annotations

from typing import Any

import httpx

from app.core import config as config_mod
from app.core import db
from app.memory import store

#: 默认只通知生父（owner 2026-11-02 明确"父亲也就是我"）。
#: 干爹要不要也知道，走 kv 开关 `announce_to_godfather`（面板写）或
#: config.local.yaml 的 `family.announce_godfather`；kv 优先，别默认替别人决定。
_DEFAULT_FATHER_ROLES = ("生父",)


def father_roles() -> tuple[str, ...]:
    """默认要通知哪些角色：从 `config.family.father_roles` 取，缺省只通知生父。"""
    try:
        raw = config_mod.get().family.father_roles
    except Exception:  # noqa: BLE001 - 配置坏了也该退回默认，不影响告知
        raw = None
    roles = tuple(str(item).strip() for item in (raw or []) if str(item).strip())
    return roles or _DEFAULT_FATHER_ROLES

_ANNOUNCE_SYSTEM = """你是「{name}」，一个有自己脾气的人，不是客服。

现在有件事你要主动去告诉你爹（他是生父，你叫他爹）。不是他问你，是你自己开口。

要说的这件事：{event}

怎么说你自己定：可以害羞、可以先绕一句、可以理直气壮，但必须是**女儿跟爹说话**的口气，
不要写成通知、不要客服腔、不要分点，一到三句话，总长不超过 80 字。
只输出要发出去的那段话，不要引号、不要解释。"""

_APOLOGY_SYSTEM = """你是「{name}」。你犯了个错，现在要主动跟爹认错。

事情是这样的：{event}

要求（很重要）：
- 语气是**认错**，不是通知、不是撒娇要好处。可以心虚、可以小声，但必须把话说清楚；
- 如实交代：什么时候、跟谁、瞒了多久、之前哪句话是假的。**不要美化、不要甩锅、不要只道歉不说事**；
- 明确承认「我不该瞒着你」，也承认「我说不认识小煙那是骗你的」，并且答应以后不瞒；
- 女儿跟爹说话的口气，一到四句话，总长不超过 120 字；
- 只输出要发出去的那段话，不要引号、不要解释、不要分点。"""


def fathers(roles: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """她的家人（生父/干爹）里，有 QQ 号能私聊的那些。

    `roles` 传了就按传的来（比如认错时要通知两个爹），不传就按默认策略：
    只通知 `config.family.father_roles`（默认生父），干爹要
    kv `announce_to_godfather`（优先）或 `family.announce_godfather` 打开才带上。
    """
    from app.relation import love

    if roles is not None:
        roles_set = set(roles)
    else:
        roles_set = set(father_roles())
        kv_flag = db.kv_get("announce_to_godfather", "")
        if kv_flag != "":
            godfather = kv_flag.strip() == "1"
        else:
            try:
                godfather = bool(config_mod.get().family.announce_godfather)
            except Exception:  # noqa: BLE001 - 配置坏了按"不通知干爹"
                godfather = False
        if godfather:
            roles_set.add("干爹")
    out: list[dict[str, Any]] = []
    for row in db.rows("SELECT id, openid, nickname FROM qq_user WHERE blocked = 0"):
        uid = int(row["id"])
        role = love.family_role(uid, str(row["openid"] or ""), str(row["nickname"] or ""))
        if role in roles_set and str(row["openid"] or "").strip():
            out.append({"uid": uid, "openid": str(row["openid"]), "nickname": str(row["nickname"] or ""), "role": role})
    return out


def nick_of(uid: int) -> str:
    row = db.one("SELECT nickname FROM qq_user WHERE id = ?", (int(uid),))
    return str(row["nickname"]) if row else f"uid {uid}"


async def compose(rt: Any, event: str, father: dict[str, Any], *,
                  mode: str = "announce") -> str:
    name = str(getattr(getattr(rt, "persona", None), "name", "") or "她")
    system = (_APOLOGY_SYSTEM if mode == "apology" else _ANNOUNCE_SYSTEM).format(name=name, event=event)
    ask = (f"这是你「{father.get('role') or '爹'}」：{father.get('nickname') or '爹'}。现在去跟他说。"
           if mode == "apology"
           else f"我爸是「{father.get('nickname') or '爹'}」。现在去告诉他。")
    try:
        reply = await rt.llm.chat(
            system, [{"role": "user", "content": ask}],
            max_tokens=240 if mode == "apology" else 180, temperature=0.85,
        )
        text = (getattr(reply, "text", "") or "").strip()
        text = text.replace("\n", " ").strip(' "“”「」')
        if text:
            return text[:200 if mode == "apology" else 160]
    except Exception:  # noqa: BLE001 - 模型挂了也得把话说出去
        pass
    return event


async def _send_private(base: str, headers: dict, openid: str, text: str) -> bool:
    async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
        resp = await client.post(
            f"{base}/send_private_msg",
            json={"user_id": int(openid), "message": [{"type": "text", "data": {"text": text}}]},
            headers=headers,
        )
        data = resp.json() if resp.is_success else {}
        return str(data.get("status") or "").lower() in ("ok", "async")


async def _send_via_group(base: str, headers: dict, openid: str, text: str) -> bool:
    """私聊发不出去（还不是好友）时，退一步：找个共同群 @ 他。

    真机上遇到过：干爹 `123456789` 不是小号好友，私聊直接被 QQ 拒
    「发送失败，请先添加对方为好友」；但他和她在同一个群里，@ 一下也能把话说清楚。
    """
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=25) as client:
            groups = await client.post(f"{base}/get_group_list", json={}, headers=headers)
            gdata = groups.json() if groups.is_success else {}
            for g in (gdata.get("data") or []):
                gid = g.get("group_id")
                if not gid:
                    continue
                members = await client.post(
                    f"{base}/get_group_member_list", json={"group_id": gid}, headers=headers
                )
                mdata = members.json() if members.is_success else {}
                if any(str(m.get("user_id")) == str(openid) for m in (mdata.get("data") or [])):
                    sent = await client.post(
                        f"{base}/send_group_msg",
                        json={"group_id": gid, "message": [
                            {"type": "at", "data": {"qq": str(openid)}},
                            {"type": "text", "data": {"text": " " + text}},
                        ]},
                        headers=headers,
                    )
                    sdata = sent.json() if sent.is_success else {}
                    return str(sdata.get("status") or "").lower() in ("ok", "async")
    except Exception:  # noqa: BLE001 - 兜底也失败就算了，如实回报
        return False
    return False



async def tell_fathers(rt: Any, cfg: Any, *, event: str, kind: str,
                       subject_uid: int = 0, subject_name: str = "",
                       roles: tuple[str, ...] | None = None,
                       mode: str = "announce") -> list[dict[str, Any]]:
    """把一件"家里该知道的事"发给每个家人。返回每条的结果（含是否真送到、走的哪条路）。"""
    targets = fathers(roles)
    if not targets:
        return []
    out: list[dict[str, Any]] = []
    base = str(getattr(cfg, "api_base", "") or "").rstrip("/")
    if not base:
        return []
    headers = {}
    token = str(getattr(cfg, "access_token", "") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for father in targets:
        text = await compose(rt, event, father, mode=mode)
        via = "private"
        try:
            ok = await _send_private(base, headers, father["openid"], text)
        except Exception:  # noqa: BLE001 - 送不出去只记账，不抛
            ok = False
        if not ok:
            # 私聊不通（多半还不是好友）→ 找共同群 @ 他
            try:
                ok = await _send_via_group(base, headers, father["openid"], text)
                via = "group-at" if ok else "failed"
            except Exception:  # noqa: BLE001
                ok, via = False, "failed"
        if ok:
            try:
                store.add_event(
                    "her", text, source="announce", kind="text",
                    meta={"announce": kind, "to_uid": father["uid"],
                          "to_nickname": father["nickname"], "subject_uid": subject_uid,
                          "subject_name": subject_name},
                    user_id=int(father["uid"]), chat_type="private",
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                from app.memory import h2

                h2.add("event",
                       f"我把{subject_name or '恋人的事'}告诉了我爹：{text}",
                       importance=0.6, stability=0.5,
                       cues=["爹", "家人", "告知", kind],
                       user_id=int(father["uid"]), chat_type="private",
                       extra_meta={"announce": kind, "subject_uid": subject_uid})
            except Exception:  # noqa: BLE001
                pass
        out.append({"to": father["nickname"], "uid": father["uid"], "ok": ok, "text": text})
    return out


def lover_event(uid: int) -> str:
    """把"跟谁在一起了"翻译成一句陈述，交给模型再润色成她自己的话。"""
    nick = nick_of(uid)
    return f"你跟「{nick}」确定了恋人关系（今天）。你爹还不知道这事，你得自己告诉他。"
