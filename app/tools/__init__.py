"""工具环节：给她装"手"。

- **联网工具**：`web_search`（查资料）、`web_fetch`（读链接）
- **识图工具**：`image_describe`（看用户发来的图）

入口只有两个：
- `run(name, args)`：执行一个工具（永不抛）
- `router.hint(rt, text, attachments)`：按话术/附件决定调什么，返回一段提示给 prompt

**mock 模式永不出网**（`registry.gate()` 第一道闸），所以离线测试零联网、零花费。
﻿‌​​﻿‌‍‌﻿﻿‍‌‌﻿‍﻿‌﻿‌‍​﻿‌‍‌‌﻿‍‌‍‍﻿﻿﻿"""

from __future__ import annotations

from app.tools import registry, router

__all__ = ["registry", "router", "run", "specs"]

run = registry.run
specs = registry.specs
