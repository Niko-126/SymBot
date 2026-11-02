"""通道能力位：同一套内核，不同通道能干什么不一样。

- `qq_official`（官方开放平台 botpy）：只能被动回文本；没有表情包/语音/群主动消息；
  单条消息有长度与频率限制。
- `onebot`（NapCat/LLOneBot）：全量能力（表情包、语音、群主动、好友申请）。

用法：适配器启动时 `set_channel("qq_official")`；内核/面板只读 `caps()`。
﻿‌﻿‍‍‍﻿﻿‌​‍‌‍‍﻿‍‌‌‍﻿‌‍﻿﻿‌‌﻿‌​‌​‌​﻿"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Caps:
    name: str = "panel"
    stickers: bool = False       # 能否发表情包
    voice: bool = False          # 能否发语音（record 段）
    images: bool = False         # 能否发图
    group_proactive: bool = False  # 群里能否主动开口
    private_proactive: bool = False  # 私聊能否主动开口
    max_reply_chars: int = 4000  # 单条消息上限（超出会切段）
    can_split: bool = True       # 是否允许多条气泡


PANEL = Caps(name="panel", stickers=True, voice=False, images=True,
             group_proactive=True, private_proactive=True, max_reply_chars=4000)
ONEBOT = Caps(name="onebot", stickers=True, voice=True, images=True,
              group_proactive=True, private_proactive=True, max_reply_chars=1500)
QQ_OFFICIAL = Caps(name="qq_official", stickers=False, voice=False, images=True,
                   group_proactive=False, private_proactive=True, max_reply_chars=1500,
                   can_split=False)  # 官方 API 一次只能回一条，不能拆气泡

_PRESETS = {"panel": PANEL, "onebot": ONEBOT, "qq_official": QQ_OFFICIAL}
_current: Caps = PANEL


def set_channel(name: str) -> Caps:
    global _current
    _current = _PRESETS.get(str(name or ""), PANEL)
    return _current


def caps() -> Caps:
    return _current


def as_dict() -> dict[str, Any]:
    return asdict(_current)
