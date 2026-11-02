from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router
from app.core import runtime

_PLACEHOLDER = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>衡忆</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0B0C0E;color:#F2F2EF;
font:14px/1.7 system-ui,"Microsoft YaHei",sans-serif}
.box{max-width:520px;padding:40px;border:1px solid #9AA0A6}
h1{font-size:16px;font-weight:500;margin:0 0 12px;letter-spacing:.02em}
p{margin:0 0 8px;color:#9AA0A6}
code{color:#F2F2EF}
</style></head><body><div class="box">
<h1>面板还没生成</h1>
<p>内核已经在跑，接口可用。</p>
<p>试一下 <code>GET /v1/board</code> 和 <code>POST /v1/chat</code>。</p>
<p>面板文件缺失：<code>panel/index.html</code></p>
</div></body></html>"""


def create_app() -> FastAPI:
    rt = runtime.init()
    app = FastAPI(
        title="衡忆数字生命 (SymBot)",
        version=__version__,
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        license_info={"name": "SymBot Personal Use License 1.1", "identifier": "LicenseRef-SymBot-1.1"},
    )
    # 只监听 127.0.0.1，放开 CORS 是为了让面板在 file:// 直开时也能联调。﻿‌‍‍​‌﻿‌‍‌﻿‌‍‍‌﻿‌‍‌﻿﻿‍﻿‍​‌﻿‌​﻿​‌‌﻿
    # 若以后改成 0.0.0.0 对外提供，必须收窄 allow_origins。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    from app.api.onebot import router as onebot_router
    from app.api.console import router as console_router
    from app.api.trend import router as trend_router
    from app.api.group import router as group_router

    app.include_router(onebot_router)
    app.include_router(console_router)
    app.include_router(trend_router)
    app.include_router(group_router)
    # 人设卡编辑页（P1-1）：必须在下面 static mount 之前挂，否则被静态挂载截胡成 404
    from app.api.persona_api import router as persona_card_router

    app.include_router(persona_card_router)
    # 外置模块热插拔（P2-3）：扫 modules/*/manifest.json，动态挂路由 + 注册工具
    from app.core import modules as modules_mod

    modules_mod.attach(app)
    # 模块的只读列表 / 开关 / 热重载（面板用）
    from app.api.modules_api import router as modules_router

    app.include_router(modules_router)

    @app.on_event("startup")
    async def _voice_cache_cleanup() -> None:
        """启动时按 voice.cache_ttl_days 清一次语音缓存（默认 15 天，0 = 不清）。

        放 startup 而不是合成热路径：清缓存是杂活，别拖慢回话。
        """
        try:
            from app.voice import tts as tts_mod

            result = tts_mod.cleanup_cache(rt.cfg)
            if result.get("removed"):
                import logging

                logging.getLogger(__name__).info(
                    "tts cache cleanup: removed=%s kept=%s ttl=%s",
                    result.get("removed"), result.get("kept"), result.get("ttl_days"),
                )
        except Exception:  # noqa: BLE001 - 清缓存失败不许拖垮启动
            pass

    @app.on_event("startup")
    async def _proactive_worker() -> None:
        """按用户主动外发（QQ 私聊）。默认关：onebot.proactive_push=false 就不起。"""
        cfg = rt.cfg.onebot
        if not (bool(getattr(cfg, "enabled", False)) and bool(getattr(cfg, "proactive_push", False))):
            return
        import asyncio

        from app.api import onebot as ob_mod
        from app.core import db
        from app.life import proactive as pro_mod

        async def _loop() -> None:
            while True:
                try:
                    for row in db.rows("SELECT id, openid FROM qq_user WHERE blocked = 0"):
                        # 别给"她自己"发主动消息：qq_user 里会有一条 openid = 小号本人
                        if str(cfg.self_id or "").strip() and str(row["openid"]) == str(cfg.self_id).strip():
                            continue
                        # 两段式外发：prepare 只算"要说什么"（三闸门 + 有据念头都在里面），
                        # 真发成功才 commit 记账；没发出去不扣配额、不记窗口，下一轮再试。
                        prepared = await pro_mod.prepare_tick(
                            rt, rt.persona, int(row["id"]), "private"
                        )
                        if not prepared.get("ok"):
                            continue
                        lines = [str(t) for t in (prepared.get("lines") or []) if str(t).strip()]
                        if not lines:
                            continue
                        user_qq = int(row["openid"])
                        msg = [{"type": "text", "data": {"text": t}} for t in lines]
                        sent = await ob_mod._post_api(
                            cfg, "send_private_msg",
                            {"user_id": user_qq, "message": msg},
                        )
                        if sent is None:
                            # 发失败：不记账（他没收到就不算她说过），留 outbox 方便排查
                            ob_mod._append_outbox({
                                "api": "send_private_msg",
                                "payload": {"user_id": user_qq, "message": msg},
                                "user_qq": user_qq,
                                "source": "proactive",
                            })
                            continue
                        pro_mod.commit_tick(prepared)
                except Exception:  # noqa: BLE001 - 主动任务不许拖垮服务
                    pass
                await asyncio.sleep(max(60, int(cfg.proactive_interval_min) * 60))

        asyncio.create_task(_loop())

    @app.on_event("startup")
    async def _harvest_worker() -> None:
        """热梗自动抓取（路线 A）：真实 provider 下后台每天抓一轮；mock 不起。

        - 抓取是慢操作（一轮 ~30s、上百次外网请求），放 asyncio.to_thread，不阻塞事件循环；
        - 失败 30 分钟后重试、单日最多 3 次；状态写 kv，`GET /v1/trend/stats` 可查。
        """
        trend_cfg = getattr(rt.cfg, "trend", None)
        if not bool(getattr(trend_cfg, "harvest_enabled", False)):
            return
        if bool(getattr(rt.cfg.provider, "is_mock", False)):
            return  # 测试/无 key：绝不联网
        import asyncio

        from app.web import harvest as harvest_mod

        async def _loop() -> None:
            while True:
                try:
                    await harvest_mod.tick(rt)
                except Exception:  # noqa: BLE001 - 后台任务不许拖垮服务
                    pass
                await asyncio.sleep(harvest_mod.TICK_SECONDS)

        asyncio.create_task(_loop())

    @app.on_event("startup")
    async def _qq_official_worker() -> None:
        """QQ 官方开放平台适配器：配了 appid+token 且 qq.enabled 才起（默认静默）。"""
        try:
            from app.adapters import qq as qq_adapter

            await qq_adapter.start_background()
        except Exception:  # noqa: BLE001 - 适配器不许拖垮服务
            pass

    @app.on_event("shutdown")
    async def _qq_official_stop() -> None:
        try:
            from app.adapters import qq as qq_adapter

            await qq_adapter.stop_background()
        except Exception:  # noqa: BLE001
            pass

    panel: Path = rt.cfg.panel_dir
    index = panel / "index.html"
    if index.exists():
        app.mount("/", StaticFiles(directory=str(panel), html=True), name="panel")
    else:

        @app.get("/")
        def _placeholder() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER)

        @app.get("/index.html")
        def _redirect() -> RedirectResponse:
            return RedirectResponse("/")

    return app


app = create_app()
