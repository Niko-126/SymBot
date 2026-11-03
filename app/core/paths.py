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

# 安装根：代码解包目录（_MEIPASS，%TEMP%\_MEIxxxxxx）和「东西放在哪」是两件事。
# 体积大的第三方资产（.third_party/feiyu_voice、models、tools/ffmpeg.exe…）随安装包
# 放在 **exe 旁边**，PyInstaller 不会、也不该把它们塞进 onefile 里：
#   安装态  D:\SymBot\resources\resources\kernel\SymBotKernel.exe
#           D:\SymBot\resources\resources\kernel\.third_party\feiyu_voice\server   ← 就在这一层
# 所以解析资产路径要从这里起步，而不是从 _MEIPASS。
# 开发态三者同根（仓库根），candidate_roots() 会去重。
BUNDLE_ROOT = DATA_ROOT

# 数据根目录：新名 SYMEM_DATA 优先；AI_LIFE_DATA 是母体（AI-Life）兼容名，别删
_env_data = os.environ.get("SYMEM_DATA") or os.environ.get("AI_LIFE_DATA")
if _env_data:
    DATA_ROOT = pathlib.Path(_env_data)

# 安装根只有 exe 自己知道；留个后门给非常规布局（便携版、手工摆盘）
_env_bundle = os.environ.get("SYMEM_BUNDLE")
if _env_bundle:
    BUNDLE_ROOT = pathlib.Path(_env_bundle)


def candidate_roots() -> tuple[pathlib.Path, ...]:
    """资产查找根，按可信度排序、去重保序。

    安装根（exe 旁边）一定排在最前：那是安装器真正放东西的地方。
    数据根第二（SYMEM_DATA 可能被面板指到安装根），解包目录最后兜底
    （开发态就是仓库根；安装态是 %TEMP%\\_MEIxxxxxx，那里只会有代码和静态资源）。
    """
    out: list[pathlib.Path] = []
    for root in (BUNDLE_ROOT, DATA_ROOT, RESOURCE_ROOT):
        try:
            item = pathlib.Path(root)
        except TypeError:  # 被赋了怪东西就跳过，不让它拖垮调用方
            continue
        if item not in out:
            out.append(item)
    return tuple(out)


def find_asset(rel: str | pathlib.Path, *, marker: str | None = None) -> pathlib.Path | None:
    """在候选根里找相对路径 `rel`，返回命中的绝对路径，都没有就 None。

    marker 用来确认「目录找对了」：给 `marker='.venv/Scripts/python.exe'` 时，
    只有 `根/rel/marker` 存在才算命中，避免撞上同名空目录。
    """
    rel = pathlib.Path(rel)
    for root in candidate_roots():
        cand = root / rel
        try:
            hit = cand.exists() if marker is None else (cand / marker).exists()
        except OSError:
            continue
        if hit:
            return cand
    return None
