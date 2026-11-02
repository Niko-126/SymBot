"""路径解析：开发态与打包态分开。

打包成 exe 后：
- 代码与静态资源（panel/persona/config）在 PyInstaller 的临时解包目录
- 数据库、Girl-memory、本地配置放在 exe 同目录，可写、可迁移
﻿​‍‍‌﻿﻿﻿﻿﻿﻿​‌​﻿﻿​‌​​‍‌​‍﻿​​‌‍‌​‍‍﻿"""

from __future__ import annotations

import os
import pathlib
import sys

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCE_ROOT = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    DATA_ROOT = pathlib.Path(sys.executable).resolve().parent
else:
    RESOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2]
    DATA_ROOT = RESOURCE_ROOT

# 数据根目录：新名 SYMEM_DATA 优先；AI_LIFE_DATA 是母体（AI-Life）兼容名，别删
_env_data = os.environ.get("SYMEM_DATA") or os.environ.get("AI_LIFE_DATA")
if _env_data:
    DATA_ROOT = pathlib.Path(_env_data)
