"""语音服务的 API key 解析。

声音服务（肥鱼 / `astrbot_plugin_feiyu_voice`）第一次启动会自己生成一把 key：

    <服务根>\\.runtime\\voice-service\\api_key.txt

`/v1` 下的所有接口（capabilities / speech / jobs / voice-conversion）都要 `Authorization: Bearer <key>`，
只有 `/health` 不需要。所以内核只要拿不到这把 key 就会：

- 面板「服务 › 语音」显示 `在线`、`模型 —`、`CUDA：否`（capabilities 被 401 挡掉，字段全空）
- 真去合成时 `POST /v1/speech` 返回 401 → `{"ok": false, "reason": "http-401"}`

用户不用知道这些，它就该自己找到。解析顺序：

1. `config.yml / config.local.yml` 的 `voice.api_key`（唯一需要手工填的地方）
2. 环境变量 `SYMEM_VOICE_KEY` / `FEIYU_VOICE_KEY`
3. 服务自己生成的 `api_key.txt`：先看 `_voice_dir()` 命中的服务根，再扫 `candidate_roots()`
4. 都没有就返回空串（调用方按「不带钥匙」处理，不抛）
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

#: key 文件相对「语音服务根」的位置
KEY_REL = pathlib.Path(".runtime") / "voice-service" / "api_key.txt"
#: key 文件相对「任意安装根」的位置（= .third_party/feiyu_voice/server 下）
KEY_REL_FROM_ROOT = (pathlib.Path(".third_party") / "feiyu_voice" / "server" / KEY_REL)
#: 允许用环境变量直接指定（排障 / 指向别人的服务）
KEY_ENV = ("SYMEM_VOICE_KEY", "FEIYU_VOICE_KEY")

#: 读文件失败时记一下，免得每次都重读磁盘（只是缓存空结果，文件一出现就会被读到）
_HEALED_FROM: str = ""


def _read_key(path: pathlib.Path) -> str:
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                return text.split()[0]
    except OSError:
        pass
    return ""


def candidate_files() -> list[pathlib.Path]:
    """按可信度列一遍 key 文件候选。"""
    out: list[pathlib.Path] = []

    def add(p: pathlib.Path) -> None:
        p = pathlib.Path(p)
        if p not in out:
            out.append(p)

    # 1) 内核自己解析出的服务根（安装态 = 内核 exe 旁边；开发态 = 仓库根）
    try:
        from app.core import services  # 懒导入，避免 import 环

        add(services._voice_dir() / KEY_REL)  # noqa: SLF001 - 同包内部约定
    except Exception:  # noqa: BLE001 - 拿不到就算了，后面还有兜底
        pass

    # 2) 显式指定服务目录
    env_dir = (os.environ.get("SYMEM_VOICE_DIR") or "").strip()
    if env_dir:
        add(pathlib.Path(env_dir) / KEY_REL)

    # 3) 各个候选根下的标准位置
    try:
        from app.core.paths import candidate_roots

        for base in candidate_roots():
            add(pathlib.Path(base) / KEY_REL_FROM_ROOT)
    except Exception:  # noqa: BLE001
        pass
    return out


def api_key_file() -> pathlib.Path | None:
    """第一个真存在且非空的 key 文件。"""
    for cand in candidate_files():
        if _read_key(cand):
            return cand
    return None


def resolve_api_key(box: Any = None) -> str:
    """拿这把 key：显式配置 → 环境变量 → 服务生成的 api_key.txt。"""
    # 1) 配置里手填的优先（用户明确指定就听他的）
    if box is not None:
        key = str(getattr(box, "api_key", "") or "").strip()
        if key:
            return key

    # 2) 环境变量
    for name in KEY_ENV:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value

    # 3) 服务自己生成的
    for cand in candidate_files():
        key = _read_key(cand)
        if key:
            return key
    return ""
