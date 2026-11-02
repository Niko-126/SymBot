"""声音环节：文字转语音（TTS）。

目前只做一件事——把她的话送进本地「声音服务」合成 WAV。
服务端是第三方项目（肥鱼声音），本项目只实现客户端，**不随仓库分发它的代码**。

开关在 `config.yaml` 的 `voice:` 段；默认关（关着就不出网、不花算力）。
﻿﻿‍‌‍‍﻿​​﻿​﻿﻿﻿﻿‌‌‍​‍‌‌﻿​​‌‌‌​​﻿﻿‌﻿"""

from __future__ import annotations

from app.voice import tts

__all__ = ["tts"]
