"""真机测试专用内核启动器（不改用户配置文件）。

和 `python -m app.cli serve` 的区别，只为真机对抗服务：
1. 关掉 OneBot 通道（NapCat 现在登录的是用户本人 123456789，
   开 OneBot 会让她用用户的号说话/发主动消息）；
2. 每轮输出上限默认 2000，可用 `SYMEM_LIVE_MAX_TOKENS` 覆盖（如 8000）；
3. request timeout 120s；
4. 全量 logging 落文件，方便抓适配器里的 warning。
"""

import logging
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

os.environ["SYMEM_ONEBOT_ENABLED"] = "0"      # 关键：不碰 NapCat 那条线
os.environ["SYMEM_QQ_ENABLED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

log_path = REPO / "data" / "_adv" / "kernel-live.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)

from app.core import config as config_mod  # noqa: E402

CFG = config_mod.get()
CFG.provider.max_tokens = int(os.environ.get("SYMEM_LIVE_MAX_TOKENS", "2000"))
CFG.provider.temperature = 0.95
CFG.provider.timeout_s = 120.0
print(f"[live] provider={CFG.provider.model} max_tokens={CFG.provider.max_tokens} "
      f"onebot={CFG.onebot.enabled} qq={CFG.qq.enabled}", flush=True)

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=8787, log_level="info")
