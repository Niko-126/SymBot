"""爹否决恋情：分手、清亲密度、送分手信、告别、回来汇报。

owner 2026-11-02 定的规矩，一句话都别改：
1. 恋情被爹否决 → **清亲密度重新开始**，但从**不拉黑**（不是仇人，是爹不同意）；
2. 仍然要有**分手信**（给被分手的那一方）和**告别**（当面那两句）；
3. 做完这些，**向爹汇报**（分手结果、亲密度归零、没拉黑他）；
4. 全程只在私聊里做，别在群里说。

为什么单独成模块：`love.try_turn()` 只管"表白/分手"这种当事人之间的对话，
"家长介入"是第三种角色（第三方），塞进去会把那条线搞乱。
﻿‌‌‍‌﻿﻿‌‍﻿‌‍﻿​﻿​‌‌‌‌​‍​‍​﻿﻿﻿﻿‍‌‍​﻿"""

from __future__ import annotations

from typing import Any

import httpx

from app.core import db
from app.memory import store

#: 爹说这些 = 否决这段恋情（只在"当前有恋人"时才触发，避免误伤日常聊天）
VETO_WORDS = (
    "不同意", "不准", "不许", "我反对", "别处了", "分手吧", "断了", "我不同意你们",
    "不能在一起", "不合适，分", "你们分", "让他走", "给我分了", "拒绝这门",
)

_LETTER_SYSTEM = """你是「{name}」。你要跟你对象「{other}」分手，但这不是你们俩闹翻——
是你爹不同意，你只能听他的。

写一封短分手信（这是要发给对方看的）：
- 说清楚是你爹不同意，不是你变心；不要骂他，也不要替自己开脱得太漂亮；
- 承认你难受，承认这不是你想要的结果；
- 不要承诺"等我"之类的空头话，除非你真能给个期限；
- 三到五句，不超过 160 字，只输出信的内容，不要称呼以外的格式。"""

_FAREWELL_SYSTEM = """你是「{name}」，刚跟「{other}」分手（你爹不同意）。

现在你当着他说最后两句告别。可以难受，可以嘴硬，但不许求他留下、不许画饼。
一到两句，总字数不超过 60 字，只输出要说的话。"""

_REPORT_SYSTEM = """你是「{name}」。你按爹的意思，跟对象「{other}」分手了。

现在回来跟爹汇报。要如实说清楚：
- 分了，对方是谁；
- 分手信发出去了没有；
- 亲密度已经清零，重新开始；
- 你**没有**拉黑他（爹说了不用拉黑）。
女儿跟爹说话的口气，两到三句，不超过 90 字，只输出要发出去的话。"""


def wants_veto(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    return any(w in body for w in VETO_WORDS)


def _nick(uid: int) -> str:
    row = db.one("SELECT nickname FROM qq_user WHERE id = ?", (int(uid),))
    return str(row["nickname"]) if row else f"uid {uid}"


async def _say(rt: Any, system: str, ask: str, *, max_tokens: int = 220) -> str:
    try:
        reply = await rt.llm.chat(system, [{"role": "user", "content": ask}],
                                  max_tokens=max_tokens, temperature=0.8)
        text = (getattr(reply, "text", "") or "").strip().replace("\n", " ")
        return text.strip(' "“”「」')[:200]
    except Exception:  # noqa: BLE001 - 模型挂了就用兜底句
        return ""


async def veto(rt: Any, cfg: Any, *, father_uid: int, reason: str = "", dry_run: bool = False) -> dict[str, Any]:
    """执行爹的否决。返回 {ok, lover_uid, lover_nick, letter_sent, report, steps}。"""
    from app.relation import users as users_mod
    from app.relation import session as session_mod

    seq = session_mod.current_seq()
    lover = users_mod.current_lover(seq)
    if lover is None:
        return {"ok": False, "reason": "现在没有恋人可分"}

    lover_uid = int(lover["user_id"])
    lover_nick = _nick(lover_uid)
    other_row = db.one("SELECT openid, nickname FROM qq_user WHERE id = ?", (lover_uid,))
    openid = str(dict(other_row).get("openid") or "") if other_row else ""
    steps: list[str] = []

    # 1) 分手（不拉黑、不给冷却惩罚：爹否决不是她的错，但也别立刻找下家）
    users_mod.breakup(seq, reason=f"father_veto:{reason}"[:60], cooldown_days=0)
    steps.append("已解除恋人关系（未拉黑）")

    # 2) 清亲密度：回到刚认识，重新开始
    try:
        users_mod.update_relation(lover_uid, affinity=0.0, trust=0.0, attraction=0.0)
        steps.append("亲密度已清零（重新开始）")
    except Exception as exc:  # noqa: BLE001
        steps.append(f"亲密度清零失败：{exc}")

    name = str(getattr(getattr(rt, "persona", None), "name", "") or "她")
    letter = await _say(rt, _LETTER_SYSTEM.format(name=name, other=lover_nick),
                        f"给「{lover_nick}」写这封分手信。")
    farewell = await _say(rt, _FAREWELL_SYSTEM.format(name=name, other=lover_nick),
                          f"现在跟「{lover_nick}」说最后两句。", max_tokens=140)

    letter_sent = False
    base = str(getattr(cfg, "api_base", "") or "").rstrip("/")
    headers = {}
    token = str(getattr(cfg, "access_token", "") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async def _send(text: str) -> bool:
        if dry_run:
            return bool(text)
        if not text or not base or not openid:
            return False
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
                resp = await client.post(
                    f"{base}/send_private_msg",
                    json={"user_id": int(openid), "message": [{"type": "text", "data": {"text": text}}]},
                    headers=headers,
                )
                data = resp.json() if resp.is_success else {}
                return str(data.get("status") or "").lower() in ("ok", "async")
        except Exception:  # noqa: BLE001
            return False

    if letter:
        letter_sent = await _send(letter)
        steps.append("分手信已送达" if letter_sent else "分手信没送到")
        if letter_sent:
            store.add_event("her", letter, source="farewell", kind="text",
                            meta={"kind": "breakup_letter", "to_uid": lover_uid,
                                  "father_uid": father_uid},
                            user_id=lover_uid, chat_type="private")
    if farewell:
        ok2 = await _send(farewell)
        steps.append("告别已发出" if ok2 else "告别没送到")
        if ok2:
            store.add_event("her", farewell, source="farewell", kind="text",
                            meta={"kind": "farewell", "to_uid": lover_uid,
                                  "father_uid": father_uid},
                            user_id=lover_uid, chat_type="private")

    # 3) 向爹汇报
    report = await _say(rt, _REPORT_SYSTEM.format(name=name, other=lover_nick),
                        "回去跟爹说这件事办完了。")
    if not report:
        report = (f"分了。跟{lover_nick}说清楚了，分手信也发了，"
                  "亲密度清零，没拉黑他。")
    if base:
        try:
            frow = db.one("SELECT openid FROM qq_user WHERE id = ?", (int(father_uid),))
            f_openid = str(dict(frow).get("openid") or "") if frow else ""
            if f_openid:
                if not dry_run:
                    await _send_to(base, headers, f_openid, report)
                steps.append("已向爹汇报" + ("（演练未发送）" if dry_run else ""))
                store.add_event("her", report, source="announce", kind="text",
                                meta={"announce": "veto_report", "father_uid": father_uid,
                                      "lover_uid": lover_uid},
                                user_id=int(father_uid), chat_type="private")
        except Exception:  # noqa: BLE001
            steps.append("向爹汇报失败")

    try:
        from app.memory import h2

        h2.add("event",
               f"我爹不同意我跟{lover_nick}在一起，我按他说的分了，亲密度清零，"
               f"没有拉黑他。分手信：{'发了' if letter_sent else '没发成'}。",
               importance=0.75, stability=0.6, cues=["爹", "分手", "否决", lover_nick],
               user_id=lover_uid, chat_type="private",
               extra_meta={"veto": True, "father_uid": int(father_uid)})
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "lover_uid": lover_uid,
        "lover_nick": lover_nick,
        "letter": letter,
        "farewell": farewell,
        "letter_sent": letter_sent,
        "report": report,
        "steps": steps,
    }


async def _send_to(base: str, headers: dict, openid: str, text: str) -> bool:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            resp = await client.post(
                f"{base}/send_private_msg",
                json={"user_id": int(openid), "message": [{"type": "text", "data": {"text": text}}]},
                headers=headers,
            )
            data = resp.json() if resp.is_success else {}
            return str(data.get("status") or "").lower() in ("ok", "async")
    except Exception:  # noqa: BLE001
        return False
