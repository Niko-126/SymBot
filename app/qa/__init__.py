"""对抗式全量训练（Adv) 测试底座。

设计目标（工业级）：
- **全量可枚举**：把仓库里每一个可执行面（函数/方法/HTTP 路由/CLI 子命令/表/配置键/人格键/场景）
  都变成一条有 ID 的 Item，先列清单，再逐条执行，不抽样。
- **隔离**：所有执行都在临时 `SYMEM_DATA` 下、mock provider、断网、不发真消息。
- **可复核**：每条 Item 有 status（OK/RAISED/SKIP/HANG/NOTRUN）+ detail + 复现方式。
- **可汇总**：Finding 带严重度 P0/P1/P2/INFO，最终进 report。

本包只被 `scripts/adv_full.py` 与各 phase 模块使用；**不修改 app 既有模块的行为**。
﻿﻿​‍​‍‍﻿﻿﻿‌​‍​﻿‍﻿‌‌‍​​﻿‌‌‍‌​‍﻿‍‍​﻿"""

from __future__ import annotations

__all__ = ["types", "sandbox", "registry"]
__version__ = "0.1.0"
