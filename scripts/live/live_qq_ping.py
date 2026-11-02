"""真机主动消息探针：用官方 bot 主动给「阿星」私聊 + 群 555666777 各发一条。

用途：让真实链路先动起来（用户收到消息后回一句，我们就能采到真话术）。
不写仓库、只跑一次；凭据从 config.local.yaml 读，不打印。

用法：.venv\\Scripts\\python.exe .ai_analysis\\live_qq_ping.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import botpy  # noqa: E402

from app.core import config as config_mod  # noqa: E402

CFG = config_mod.get()
OPENID = "49BEBF2DFA8982E91C750153D89231DE"          # qq_user id=19（阿星，真机 C2C 留下）
GROUP_OPENID = "C113FE1C1E07D63C8338542B6E6CF6FF"    # 真机群 @ 留下的 group_openid

C2C_TEXT = "我在。你回我一句，我要把你说的原话记下来。"
GROUP_TEXT = "我在群里了。@我一下，我确认能不能听见。"


class PingClient(botpy.Client):
    async def on_ready(self):
        print("[ready]", getattr(self, "robot", None), flush=True)
        try:
            msg = await self.api.post_c2c_message(openid=OPENID, msg_type=0, content=C2C_TEXT)
            print("[c2c ok]", getattr(msg, "id", "?"), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[c2c FAIL]", type(exc).__name__, exc, flush=True)
        try:
            msg = await self.api.post_group_message(
                group_openid=GROUP_OPENID, msg_type=0, content=GROUP_TEXT)
            print("[group ok]", getattr(msg, "id", "?"), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[group FAIL]", type(exc).__name__, exc, flush=True)
        await asyncio.sleep(0.5)
        await self.close()


async def main() -> int:
    print("appid:", str(CFG.qq.appid)[:6] + "***", "| sandbox:", CFG.qq.sandbox,
          "| enabled:", CFG.qq.enabled, flush=True)
    if not (CFG.qq.enabled and CFG.qq.appid and CFG.qq.token):
        print("[abort] qq 凭据不全")
        return 2
    client = PingClient(intents=botpy.Intents(public_messages=True),
                        is_sandbox=bool(CFG.qq.sandbox))
    try:
        await asyncio.wait_for(client.start(appid=CFG.qq.appid, secret=CFG.qq.token), timeout=75)
    except asyncio.TimeoutError:
        print("[timeout] 75s 内没跑完（可能连不上网关）", flush=True)
        return 3
    except Exception as exc:  # noqa: BLE001
        print("[FAIL]", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
