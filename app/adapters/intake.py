"""两个通道共用的入站前置层。

背景：这些机制原本只写在 OneBot 适配器里（`app/api/onebot.py`），
官方 bot（`app/adapters/qq.py`）直接走内核，导致同一句话在两个通道行为不一致
（真机实测：搜视频不触发、"别说了"不静默、"以后叫我X"不落库）。

这里只放"前置判定"，不碰内核：
1. 静默指令（别说了 / 闭嘴）→ 记下静默 + 回一句确认
2. 静默期内没被 @ → 不回
3. 改昵称（以后叫我XX）→ 落库 + 回确认
4. 搜视频（搜一下XX）→ 调 B 站搜索，有结果直接回链接

返回：
- None：继续正常对话
- {"reply": str}：直接回这句，不进内核
- {"skip": True}：什么都不回
﻿﻿‌‍​‍​﻿​‍​﻿‍‌‌‌‍​‍‍﻿​​‍​‌‍​‍﻿‍‌‍﻿"""

from __future__ import annotations

from typing import Any


def precheck(
    rt,
    text: str,
    *,
    user_id: int,
    chat_type: str,
    group_id: str = "",
    at_me: bool = True,
) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None

    # ---- 改昵称（群/私聊都可以）
    try:
        from app.group import context as gctx

        change = gctx.handle_nick_change(text, int(user_id))
        if change is not None:
            if change.get("need_name"):
                return {"reply": "你想让我怎么叫你？说个名字。"}
            nick = str(change.get("nick") or "")
            return {"reply": f"好，以后就叫你{nick}。" if nick else "行，记住了。"}
    except Exception:  # noqa: BLE001 - 前置层失败不许拖垮聊天
        pass

    # ---- 搜视频
    try:
        from app.api import trend as trend_mod
        from app.web import bili

        kw = trend_mod.match_search_intent(text)
        if kw:
            try:
                videos = bili.search_videos(kw, 3)
            except Exception:  # noqa: BLE001
                videos = []
            if videos:
                lines = [f"{i + 1}. {v['title']}\n{v['url']}" for i, v in enumerate(videos)]
                return {"reply": "搜到几个：\n" + "\n".join(lines)}
    except Exception:  # noqa: BLE001
        pass

    # ---- 群机制：静默 / 待机
    if chat_type == "group":
        try:
            from app.group import proactive as gpro, active as gactive

            gactive.remember_group(group_id)
            if gpro.detect_stop(text):
                gpro.silence(gpro.STOP_MINUTES, gid=group_id)
                return {"reply": "行，不说了。"}
            silent, _why = gpro.is_silenced(group_id)
            if silent:
                if at_me:
                    gpro.break_silence(by="@", gid=group_id)
                else:
                    return {"skip": True}
        except Exception:  # noqa: BLE001
            pass

    return None
