"""亲密门控：OneBot 和控制台共用同一条判定 / 记录链路。

之前 onebot.py 里手写了一遍「检测意图 → 转移模式 → 记私密上下文」，
控制台模拟对话完全没走这条路，于是"在控制台里怎么试都进不了亲密模式"。
抽到这里，两边都调同一个入口，行为不会再分叉。

## R 级插件（app/intimacy/r18/）分发

本模块是**两级结构**：

- `_prepare_plain` / `_finish_plain`：基础链路（意图 → 模式 → 记私密上下文）。
- `prepare` / `finish`：分发器。当 `app/intimacy/r18/` 存在且 `cfg.intimacy.enabled`
  为 True 时，整轮交给插件（插件里是三层判定：硬门控 → safeword → 状态机 → L0-L5 阶梯）；
  否则走基础链路。

插件是**可选目录**：把它整个删掉，`_r18()` 的 import 会失败、返回 None，
主链自动回退到基础行为，不留任何残骸。所以这里的探测**必须**包在 try/except 里。
﻿​‌﻿​​‌‌‍‍﻿‌‌​‍‍﻿﻿﻿​‍‌﻿​​​‌﻿‌﻿‌﻿‍﻿"""

from __future__ import annotations

from typing import Any

from app.intimacy import exemplars as exemplar_mod
from app.intimacy import memory as memory_mod
from app.intimacy import mode as mode_mod
from app.intimacy.mode import Mode


def is_private_of(chat_type: str) -> bool:
    return str(chat_type or "").strip().lower() == "private"


def is_lover_of(user_id: int) -> bool:
    try:
        from app.relation import users

        return users.lover_of(int(user_id)) is not None
    except Exception:  # noqa: BLE001 - 关系层异常不该让聊天挂掉
        return False


def _r18():
    """探测 R 级插件。

    未安装 / 未启用 / 包内任何异常 → 一律返回 None，绝不上抛给聊天主链。
    """
    try:
        from app.intimacy import r18

        return r18 if r18.enabled() else None
    except Exception:  # noqa: BLE001 - 插件坏了不该让主链挂掉
        return None


# ---------------------------------------------------------------- 基础链路


def _prepare_plain(user_id: int, text: str, chat_type: str) -> tuple[Mode, str, bool, bool]:
    """不带插件时的原始判定（保持与接入前逐字节等价）。"""
    uid = int(user_id or 0)
    is_private = is_private_of(chat_type)
    is_lover = is_lover_of(uid)
    if uid > 0:
        from app.relation import users

        if users.is_blocked(uid):
            return Mode.OFF, "blocked", False, is_private
    exemplar_mod.ensure_seeded()
    intent = mode_mod.detect_intent(text)
    mode, note = mode_mod.transition(
        uid, intent, is_private=is_private, is_lover=is_lover
    )
    if mode != Mode.OFF:
        memory_mod.record(uid, "user", text, mode.value, is_private)
    return mode, note, is_lover, is_private


def _finish_plain(user_id: int, mode: Mode, reply: Any, is_private: bool) -> None:
    if mode == Mode.OFF:
        return
    if isinstance(reply, (list, tuple)):
        text = "\n".join(str(x) for x in reply if str(x).strip())
    else:
        text = str(reply or "").strip()
    if text:
        memory_mod.record(int(user_id or 0), "her", text, mode.value, is_private)


# ---------------------------------------------------------------- 分发器


def prepare(user_id: int, text: str, chat_type: str) -> tuple[Mode, str, bool, bool]:
    """判定这一轮是否进入亲密模式，并把用户那句话记进私密上下文。

    返回 (模式, 说明, 是否恋人, 是否私聊)。

    说明（note）的取值范围取决于走哪条链路：
      - 基础链路：``""`` / ``blocked`` / ``stop_soft`` / ``group_limit`` / ``not_lover_deflect``
      - R 级插件：以上全部，外加 ``disabled`` / ``family_guard`` / ``minor_guard`` /
        ``red_light`` / ``yellow_light``（见 ``app/intimacy/r18/gate.py`` 的 docstring）
    """
    ext = _r18()
    if ext is not None:
        try:
            return ext.gate.prepare(user_id, text, chat_type)
        except Exception:  # noqa: BLE001 - 插件异常回退到基础链路，不吞用户这句话
            pass
    return _prepare_plain(user_id, text, chat_type)


def finish(user_id: int, mode: Mode, reply: Any, is_private: bool) -> None:
    """把她的回复也记进这次私密会话（只在模式未退出时）。"""
    ext = _r18()
    if ext is not None:
        try:
            return ext.gate.finish(user_id, mode, reply, is_private)
        except Exception:  # noqa: BLE001
            pass
    return _finish_plain(user_id, mode, reply, is_private)


def backend() -> str:
    """当前生效的链路，给 health / 控制台显示用：``"plain"`` 或 ``"r18"``。"""
    return "r18" if _r18() is not None else "plain"
