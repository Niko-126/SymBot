# 13 · 应用装配与入口（main / cli / desktop / adapters）

> 覆盖：`app/main.py`、`app/cli.py`、`app/desktop.py`、`app/adapters/*.py`（6 个文件 / 1063 行）｜基于 2026-11-01 的代码状态｜上游：docs/12（HTTP 接口）、docs/04（内核 `loop`）｜下游：docs/14（面板）、docs/21（脚本与命令行）

## 1. 这一层干什么

这一层决定"进程怎么活起来、活成什么形状"。四个入口共用同一套内核，但装配方式不同：`main.py` 造 FastAPI 应用（HTTP 服务，含面板静态目录与三个后台任务）；`cli.py` 造一次性命令行作业（导语料、重建锚点、压冷记忆）；`desktop.py` 把 `main.py` 的 app 塞进 uvicorn 线程 + pywebview 原生窗口；`adapters/` 是外接通道（QQ 官方 bot 的 WS 长连，以及两个通道共用的入站前置层）。

它解决的核心问题是**副作用顺序**：`runtime.init()` 会建库、读人设、同步人格向量、跑 provider 预检、写 kv，这一步必须只发生一次、且必须早于任何路由被访问。`app/main.py` 用"模块级 `app = create_app()`"把这件事钉死在 import 时刻，代价是 `import app.main` 永远带副作用。

它明确不做的事：不实现对话（那是 `app/mind/loop.py`）、不实现记忆（`app/memory/*`）、不决定关系数值（`app/relation/engine.py`）。适配器尤其只有一条纪律，见 3.6。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/main.py` | 162 | FastAPI 装配：建 runtime、挂 5 个 router、起 3 个 startup 任务、挂面板静态目录 |
| `app/cli.py` | 371 | 10 个子命令的 `argparse` 分发与各自的一次性作业 |
| `app/desktop.py` | 143 | 桌面壳：uvicorn 线程 + 端口探测 + pywebview 无边框窗口 |
| `app/adapters/__init__.py` | 1 | 只有一行 docstring："外部接入适配器（QQ / 以后的飞书等）。" |
| `app/adapters/qq.py` | 301 | QQ 官方开放平台 botpy WS 适配器：群 @ 与 C2C 私聊 |
| `app/adapters/intake.py` | 85 | 两个通道共用的入站前置层：改名 / 搜视频 / 群静默 |

## 3. 模块逐一详解

### 3.1 `app/main.py`（162 行 / 0 类 / 9 函数）

**职责**：把内核包成一个 ASGI 应用。文件里只有一个真正的公共符号 `app`（第 162 行 `app = create_app()`），其余都是内部函数。

**装配顺序（`create_app()` 第 30–159 行，逐行可复现）**：

1. `rt = runtime.init()`——**先建内核，再造 app**。`Runtime.__init__` 内部依次做 8 件事：`config_mod.get()`（读 `config.yaml` + `config.local.yaml` 合并 + 环境变量覆盖）、`persona_load.load(cfg.persona_file)`、`db.init(cfg.db_file)`（建表 + 老库补列迁移 + `memv2.ensure_ready()` 把 `memories` 变成只读视图）、`engine.init_traits(persona)`、`preflight.check(cfg)`（零网络，只按 base_url 与模型名是否同源打分，失败打 WARNING）、真实 provider 下把 kv `human_life_enabled` 默认设 `"1"`、`users_mod.purge_self(onebot.self_id)` 清掉"她把自己当群成员"的遗留、读 kv `context_mode` 覆盖 `cfg.context.mode`，最后 `llm_mod.build(cfg, persona)`。
2. `FastAPI(title="衡忆数字生命 (SymBot)", version=__version__, docs_url="/v1/docs", openapi_url="/v1/openapi.json")`——注意文档路径被挪到 `/v1/` 下，因为 `/` 要留给面板。
3. `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`——注释写明只为 `file://` 直开面板联调，"若以后改成 0.0.0.0 对外提供，必须收窄 allow_origins"。
4. `app.include_router(router)`（`routes.py`，无前缀）——它是模块顶层导入的，所以排第一。
5. 在函数体内导入并依次挂载：`onebot_router`（无前缀）、`console_router`（`/v1/console`）、`trend_router`（`/v1/trend`）、`group_router`（`/v1/group`）。四个导入写在函数体内而不是文件头，因为 `console.py` 与 `trend.py` 会反向使用 `routes`/`onebot` 的私有符号（`_dev_ok`、`_post_api`、`accept_request`），延后导入最省事。
6. 注册 3 个 `@app.on_event("startup")`（见下表）。
7. 注册 1 个 `@app.on_event("shutdown")`。
8. `panel = rt.cfg.panel_dir`（= `RESOURCE_ROOT / "panel"`，打包态是 PyInstaller 解包目录）：`panel/index.html` 存在就 `app.mount("/", StaticFiles(directory=..., html=True), name="panel")`；不存在就注册 `GET /` 返回内置占位 HTML（深色页，提示"面板还没生成"，并给出 `GET /v1/board`、`POST /v1/chat` 两个可用接口）与 `GET /index.html` 的 `RedirectResponse("/")`（默认 307）。

**startup / shutdown 后台任务（这是本章最容易记住错的地方）**：

| 钩子 | 起的任务 | 触发门槛 | 循环干什么 | 周期 |
|---|---|---|---|---|
| `_proactive_worker` | OneBot 主动外发 | `onebot.enabled` **且** `onebot.proactive_push` | 遍历 `qq_user where blocked=0` → `proactive.prepare_tick(rt, persona, id, "private")` → 有 `lines` 才 `_post_api("send_private_msg")` → **发成功才** `commit_tick`；失败写 outbox 且不记账 | `max(60, proactive_interval_min*60)` 秒 |
| `_harvest_worker` | 热梗抓取 | `trend.harvest_enabled` **且** `provider.is_mock` 为假 | `await harvest_mod.tick(rt)`（内部自己判日配额/30 分钟重试/单日 3 次上限） | `harvest.TICK_SECONDS = 600` 秒 |
| `_qq_official_worker` | QQ 官方 bot WS | 由 `qq.start_background()` 自己判（`qq.enabled` + `appid` + `token` + `auto_start`） | 不循环：起一个 task 后立刻返回 | — |
| `_qq_official_stop`（shutdown） | 收尾 | 总是执行 | `await qq_adapter.stop_background()`：cancel task、`await` 吞掉异常、`_CLIENT=None`、`connected=False` | — |

三个 startup 按注册顺序执行，所以**进程启动完成后最后一次 `channel.set_channel()` 来自 `qq.start_background()`，即 `"qq_official"`**——哪怕 `qq.enabled=false`（见 3.4 的坑）。

**群主动循环不在 startup 里**：`app/group/active.py` 的循环只能由 `POST /v1/group/start` 拉起，进程启动后默认是停的。这是与"三个后台任务"并列的第四种后台活动，但它是请求触发的。

**`channel.set_channel()` 的全部调用点**（进程级全局状态，`_current` 初值 `PANEL`）：

| 时机 | 切到 | 位置 |
|---|---|---|
| 每次进程 startup（无论 QQ 开没开） | `qq_official` | `adapters/qq.py:250`，`start_background()` 第一行 |
| 收到 OneBot 上报 | `onebot` | `api/onebot.py:207` |
| 面板 `POST /v1/chat` | `panel` | `api/routes.py:152` |
| 其余任何请求 | 保持上一次的值 | — |

**坑**：

- `import app.main` 有副作用（建库、读人设、写 kv、跑预检），所以 `cli_probe.py`/`scenarios.py` 那类测试脚本要用子进程或 `app/qa/sandbox.py`。
- `_harvest_worker` 与 `_proactive_worker` 都是 `while True` + `asyncio.sleep`，没有 `stop`；进程退出时靠 uvicorn 收尾，只有 QQ 适配器有 `stop_background`。`GET /v1/trend/stats` 里能看到的抓取状态是 kv 记录的，不是任务状态。
- `_proactive_worker` 的 `int(row["openid"])` 没做异常保护（虽然在 `try` 内整体兜住）：openid 非纯数字会让这一轮所有用户跳过。
- 面板挂载在 `/` 且 `html=True`，所以任何拼错的路由会拿到面板的 404 页面而不是 JSON 错误——调试 API 时容易被误导。

**耦合**：被 `cli._cmd_serve`（字符串导入 `"app.main:app"`）、`desktop._serve`（对象导入 `app.main.app`）、`launcher.py`、打包 spec 使用；内部调 `runtime`、`api.routes`、`api.onebot/console/trend/group`、`adapters.qq`、`web.harvest`、`life.proactive`、`core.db`。

### 3.2 `app/cli.py`（371 行 / 0 类 / 12 函数）

**职责**：`python -m app.cli <cmd>` 的一次性作业。10 个子命令，全部以 `runtime.init()` 开始（除 `_cmd_reference` 的第一遍解析前也会 init），因此每一条都带"建库/读人设"的副作用。

| 子命令 | 参数 | 副作用 | 返回值/输出 |
|---|---|---|---|
| `serve` | `--port`（默认 8787） | 起 uvicorn，**host 取 `cfg.server.host` 而不是参数** | 三行启动信息 + 日志 |
| `ingest` | `path` | `ingest_mod.ingest(path, persona)` 写记忆 | `report` JSON；文件不存在打 stderr 并返回 1；`report["ok"]` 为假也返回 1 |
| `init` | 无 | 只建库/同步人设 | `{"ok": true, "db": ..., "persona": ...}` |
| `reset` | 无 | 删 `db`、`db-wal`、`db-shm` 三个文件 → 重新 `db.init` + `engine.init_traits` | 主库删不掉（被占用）返回 1 并提示先停服务；`-wal`/`-shm` 删不掉只 warn |
| `anchors` | `--per-scene`（24）、`--extra`（40） | 从 `ref_turns` 重建锚点池（写 `anchors` 表） | `stats` JSON + 固定声纹 12 条 + 7 个场景各 4 条样本（日常/关心/撒娇/生气/自嘲/亲密/深夜） |
| `cold` | `--dry` | `asyncio.run(cold.compact_cold(rt, persona, dry_run=...))` 合并冷记忆 | 合并明细 JSON + `cold.stats()` |
| `compress` | 无 | `asyncio.run(compress.compact(rt, persona, engine.get_relation()))` 把旧对话折成线索 | 结果 JSON + `compress.status()` |
| `letter` | 无 | 只读 `persona.letter_template()` | 打印模板；没配返回 1 |
| `reference` | `path`、`--force` | 见下方两遍流程；写 `ref_turns`/`ref_quotes`/候选名单/`data/import-report.md` | 汇总 JSON |
| `arc` | `path` | **不写库**，只 `classify_arc` 打表 | 月份表 + `{"counts": ...}` |

**`reference` 的两遍流程**（10 个子命令里唯一有状态的）：

1. 第一遍逐文件 `reference._parse_file(f)`，用 `scrub_mod.detect_names(ev, min_mid=1)` 收全局人名，用 `reference._month_stats(ev)` 聚合每月 `n/her/sweet/strain`，再 `reference.classify_stats(merged)` 得出全局情绪分期。
2. `--force` 时先 `DELETE FROM ref_turns` / `DELETE FROM ref_quotes`。
3. 第二遍逐文件 `reference.ingest_reference(f, persona.name, names=..., force=..., min_mid=1, write_candidates=False, arc=global_arc)`，累计 `turns_written`/`quotes_written`/去敏命中/被替换人名/疑似人名/分期计数；返回 `already_imported` 的卷直接标"跳过"。
4. 疑似人名过一遍阈值：**出现 ≥3 次且未被自动替换**才留下。
5. 黑名单自检：`load_denylist(Path("data/denylist.txt"))`，逐个 `SELECT COUNT(*) FROM ref_turns WHERE text LIKE '%词%'`，有残留打 `[警告]`，否则打 `[校验] 黑名单 N 个词，残留 0`。
6. `reference._write_candidates(...)` 写候选名单，`_write_import_report(...)` 生成 `data/import-report.md`（含分期表、去敏统计、已替换/待确认名单、"怎么处理"三步指引）。

**坑**：

- 用了 4 个私有符号（`reference._parse_file`、`reference._month_stats`、`reference._write_candidates`，都带 `# noqa: SLF001`），参考语料模块一改名 CLI 就断。
- `data/denylist.txt` 与 `data/import-report.md` 都是**相对当前工作目录**的路径（`pathlib.Path("data")`），而 API 层一律用 `DATA_ROOT`。打包成 exe 后从别的目录启动 CLI，报告会写到 exe 目录之外的 `./data`。
- `serve` 的 `--port` 只覆盖端口，`cfg.server.host` 改不了（`config.yaml` 或 `SYMEM_*` 环境变量才行）。
- `reset` 之所以能删库，是因为 `core/db.py` 的 `connect()` 每次 `sqlite3.connect` 后立刻 `close()`（`init()` 在 `finally` 里关连接），进程不长期持有文件句柄；但服务在跑时仍会返回 1。

### 3.3 `app/desktop.py`（143 行 / 0 类 / 7 函数）

**职责**：`python -m app.desktop` 起桌面版。三步：后台起内核（uvicorn）→ 等端口就绪 → 开原生窗口。

| 符号 | 签名 | 作用 |
|---|---|---|
| `_port_free` | `(host: str, port: int) -> bool` | `socket.bind` 试探，`OSError` 即占用 |
| `_wait_port` | `(host: str, port: int, timeout: float = 20.0) -> bool` | `settimeout(0.4)` + `connect_ex` 轮询，每次 `sleep(0.15)` |
| `_serve` | `(host: str, port: int) -> None` | `uvicorn.run(asgi_app, host, port, log_level="warning")`，**传对象不传字符串**（打包后不能按字符串导入） |
| `_redirect_logs` | `() -> None` | 只在 `sys.frozen` 时把 stdout/stderr 追加到 `DATA_ROOT/data/app.log`（`buffering=1` 行缓冲） |
| `main` | `(argv: list[str] \| None = None) -> int` | 全流程 |

**`main()` 的 10 步**：

1. `argparse("衡忆 desktop")`：`--port`（默认 8787）、`--no-gui`（只起服务，自测用）。
2. `_redirect_logs()`。
3. `rt = runtime.init()`；`host` 硬编码 `"127.0.0.1"`；`port = args.port`。
4. `_port_free` 为假 → stderr 提示"端口 {port} 被占用。可能已经有一个实例在运行。" → 返回 1。
5. `threading.Thread(target=_serve, args=(host, port), daemon=True).start()`。
6. `_wait_port` 失败（20 秒内连不上）→ stderr "内核没有起来，检查日志" → 返回 1。
7. 打印 `内核已启动：http://host:port/　模型：<mock 说明 或 模型名>`。
8. `--no-gui` → `while True: time.sleep(3600)`，`KeyboardInterrupt` 返回 0。
9. `import webview` 失败 → stderr 给安装命令并返回 1。
10. 定义 `Api`（`minimize()` / `close()`，内部调 `window.minimize()` / `window.destroy()`），`webview.create_window(...)` 后 `webview.start()`，返回 0。

**窗口参数（照抄）**：标题 `f"{rt.persona.name} · 衡忆"`、尺寸 `1180×800`、位置 `x=90, y=70`、`min_size=(960, 640)`、`frameless=True`、`easy_drag=True`、`text_select=True`、`js_api=Api()`。

**坑**：

- `Api` 的方法闭包里引用 `window`，而 `window` 是在方法定义**之后**才赋值的局部变量——`create_window` 返回后才赋值，页面在此之后才拿得到 `js_api` 对象，靠的是这个时序而不是作用域保证。
- `_redirect_logs` 里 `io`、`os` 两个导入完全没用（用 `_ = io, os` 消掉未使用告警），真实逻辑只有 `open` + 赋值 `sys.stdout/stderr`。
- `frameless=True` 意味着没有系统标题栏，最小化/关闭按钮必须由页面自己画并调 `js_api`；`panel` 里没有对应按钮时窗口只能靠任务栏关。
- `desktop.py` 不注册 `uvicorn` 的 shutdown 逻辑，`--no-gui` 被 Ctrl-C 打断时进程直接退出，后台任务不走 `stop_background`。

### 3.4 `app/adapters/qq.py`（301 行 / 0 类 / 16 函数）

**职责**：QQ 官方开放平台（`botpy`）适配器。与 OneBot 的本质区别写在 docstring：不需要公网 IP、不需要 NapCat，进程主动连腾讯网关；鉴权用 AppID + Secret；群里只收到 @ 她的消息（官方公域权限），私聊收 C2C。

**模块级状态 `STATUS`（`/v1/health.qq` 的唯一来源）**：

| 字段 | 初值 | 谁写 |
|---|---|---|
| `enabled` | `False` | `start_background()` 里 `STATUS.update(...)`，取 `cfg.qq.enabled` |
| `appid` | `""` | 同上，取 `cfg.qq.appid` |
| `sandbox` | `True` | 同上，取 `cfg.qq.sandbox` |
| `started` | `False` | `_run_forever()` 第一次循环里置 `True`（**进程起过 WS 就算**，不代表连上） |
| `connected` | `False` | `on_ready()` 置 `True`；`_run_forever()` 每轮开始/异常/`CancelledError`/`stop_background()` 都置 `False` |
| `events` | `0` | 每个群 @ 与 C2C 消息 `+= 1` |
| `last_event_at` | `""` | 每个入站消息，`_now()`（本地时区 ISO，秒精度） |
| `last_reply_at` | `""` | 前置层快速回复发出后、`_answer()` 返回后 |
| `last_error` | `""` | `on_ready` 清空；`chat_turn` / `precheck` / `reply` / 连接关闭 / 崩溃 写入，统一截断到 200 字符 |

**常量**：`_MAX_REPLY = 1500`（单条消息上限以内）、`_MENTION_RE = re.compile(r"<@!?[^>]{0,64}>")`。`clean_inbound(text)` 把官方消息里 `<@!openid>` 这类提及标记摘掉——注释说明不摘"她会把一串 openid 当成用户说的话"。

**事件处理（两个 handler，同一个骨架）**：

`on_group_at_message_create(message: GroupMessage)`：

1. `clean_inbound(message.content)`，空文本直接 return。
2. `openid = message.author.member_openid`；`nick = message.author.username`；`users_mod.ensure_user(openid, nick)`（好感/记忆按人隔离）。
3. `STATUS["events"] += 1`、`last_event_at = _now()`、`log.info`（openid 只打后 6 位）。
4. `group_openid = message.group_openid`。
5. `_precheck_text(text, user_id, chat_type="group", group_id=group_openid, at_me=True)` → `intake.precheck`；返回 `skip` 就 return，返回 `reply` 就 `message.reply(quick[:1500])` + `_store_quick(...)` + return。
6. `_answer(text, source="qq", user_id, chat_type="group", group_id=group_openid)`。
7. `message.reply(content=reply)`，异常只记 `last_error` 和 `log.warning`。

`on_c2c_message_create(message: C2CMessage)` 完全同构，差异只有 4 处：`openid` 取 `message.author.user_openid`、`chat_type="private"`、`_precheck_text` 不传 `group_id`、`source="qq_c2c"`。

**`_answer()` 的四步**：取 `runtime.get()` → `igate.prepare(user_id, text, chat_type)` 拿亲密门控四元组 → `loop.chat_turn(...)`（异常时把 `last_error` 记成 `chat_turn: ...` 并回一句 `"……等一下，我这边有点乱。"`）→ `igate.finish(...)` + `last_reply_at` → `_reply_text(result)`。

**`_reply_text()` 与官方通道"只能发一条"**：`"".join` 用 `"\n"` 把 `result["reply"]` 的多个气泡拼成一条；空则退到 `meta["hint"]`，再空则 `"……"`；最后 `text[:1500]`。这就是 `channel.Caps.can_split=False` 在代码里的落点——官方 API 一次只能回一条，所以不能像 OneBot 那样连发多个气泡。

**连接与重连 `_run_forever()`**：`backoff = 5`，每轮 `build_client()`（`botpy.Intents(public_messages=True)`，`is_sandbox=cfg.qq.sandbox`）→ `started=True`、`connected=False` → `await client.start(appid=cfg.qq.appid, secret=cfg.qq.token)`（阻塞到断开）→ 断开后置 `last_error="connection closed"` → `asyncio.sleep(backoff)` → `backoff = min(60, backoff * 2)`。退避序列 5→10→20→40→60→60…；`CancelledError` 直接向上抛，让 `stop_background()` 的 `await task` 结束。

**`start_background()` 的四道闸门（顺序不能错）**：

1. `channel.set_channel("qq_official")`——**无条件执行**，哪怕后面全部 return。
2. `STATUS.update(enabled=..., appid=..., sandbox=...)`——无条件，所以 `/v1/health.qq` 一定能看到配置值。
3. `enabled` 为真但 `appid`/`token` 缺 → 写 `last_error="missing appid/token"`，`log.warning`，返回 `False`。
4. `not (enabled and appid and token)` → `False`；`auto_start=False` → `False`；已有活 task → `True`；否则 `asyncio.create_task(_run_forever())` 并返回 `True`。

**`main()`（单文件调试入口）**：没配置就打印一段 `config.local.yaml` 模板（`enabled/auto_start/appid/token/sandbox`）和环境变量名，返回 1；配置齐了 `runtime.init()` + `build_client().run(appid=..., secret=...)`（同步阻塞）。

**坑**：

- `channel.set_channel("qq_official")` 在 `start_background()` 第一行且**不受 enabled 影响**：FastAPI startup 一定会调它，所以进程启动后全局能力位就是"官方通道"（无表情包/无语音/`can_split=False`）。真正的后果在 `/v1/health.channel` 和 `onebot.py` 的出站分支上，`/v1/chat` 会自己切回 `panel`，所以本地面板不受影响。
- `STATUS` 是**进程内单实例**，`/v1/health` 里 `except Exception: qq_status = {}` 兜底，所以没装 `botpy` 时这个字段直接变空 dict——前端要判空。
- 群里只处理 @：`on_group_at_message_create` 是 SDK 的事件名，非 @ 的群消息根本收不到（官方公域权限），所以这里没有"是否 @ 我"的判断。
- 前置层回复也会 `_store_quick` 落库，但**不走 `igate.finish`**——前置层的短回复不进亲密门的后续统计。
- `_answer()` 的异常分支返回的是硬编码中文兜底，不受人设影响。

### 3.5 `app/adapters/intake.py`（85 行 / 0 类 / 1 函数）

**职责**：两个通道共用的入站前置层。模块 docstring 直接写了动机：这些机制原本只写在 `app/api/onebot.py` 里，官方 bot 直接走内核，导致同一句话在两个通道行为不一致（"真机实测：搜视频不触发、'别说了'不静默、'以后叫我X'不落库"）。这里只放**前置判定**，不碰内核。

**唯一对外符号**：

```python
precheck(rt, text, *, user_id: int, chat_type: str,
         group_id: str = "", at_me: bool = True) -> dict[str, Any] | None
```

返回值三种语义（docstring 原文）：`None` 继续正常对话；`{"reply": str}` 直接回这句、不进内核；`{"skip": True}` 什么都不回。

**判定顺序（三段，各自 `try/except` 全包）**：

1. **改昵称**（群/私聊都走）：`gctx.handle_nick_change(text, user_id)`；`need_name` 为真回 `"你想让我怎么叫你？说个名字。"`，否则回 `"好，以后就叫你{nick}。"`（昵称为空回 `"行，记住了。"`）。
2. **搜视频**：`trend_mod.match_search_intent(text)` → `bili.search_videos(kw, 3)` → 有结果回 `"搜到几个：\n1. 标题\n链接…"`；搜不到继续往下。
3. **群机制**（`chat_type == "group"`）：`gactive.remember_group(group_id)` → `gpro.detect_stop(text)` 命中就 `gpro.silence(STOP_MINUTES)` 并回 `"行，不说了。"` → 静默期内 `at_me` 为真就 `break_silence(by="@")`，否则 `{"skip": True}`。

**为什么抽出来**：只有一处调用者（`qq.py:_precheck_text`），但抽出来的收益不是复用而是**单一事实来源**——群静默/改名/搜视频的判定口径只写在这一个文件里（未来接飞书等通道直接调它）。

**坑（代码里真实存在的）**：

- `precheck` 的 `try/except` 吞掉所有异常，返回 `None` 表示"照常聊天"。`qq.py` 外层还有一层 `_precheck_text` 的 `try/except`，双层兜底，代价是前置层彻底失效时**没有任何日志**（只有 `qq.py` 那层会写 `last_error`，而且只在 `precheck` 自己抛出时）。
- 顺序差异：`intake.precheck` 是"改名 → 搜视频 → 静默"，`onebot.py` 的内联实现是"静默 → 待机 → 改名 → 语境 → 搜视频"。同一句话走两个通道会拿到不同的判定结果，`intake.py` 并没有替换掉 OneBot 那份拷贝——这是当前代码的真实状态，不是设计意图。
- `precheck` 收 `rt` 参数但函数体里一次都没用（死参数），真实使用的是 `qq.py` 传进来的 `runtime.get()`。同理 `group_id` 只在群静默段用到。

### 3.6 适配器纪律（写新适配器前必读）

1. **只做三件事**：把平台事件翻译成 `(text, user_id, chat_type, group_id)` → 调 `loop.chat_turn(...)` → 把 `result["reply"]` 用平台 API 发回去。`qq.py:_answer()` 就是这 9 行的标准形态。
2. **不许拼 prompt**：prompt 由 `app/mind/prompt.py` 与 `loop.chat_turn` 负责。适配器能给的只有 `prefetched_hint`（`onebot.py` 的"不懂就查"）这类**明确的入参**，不能自己写 `system` 消息。
3. **不许写记忆**：唯一合法写法是 `store.add_event(...)` 记录"她说了这句话"（`onebot.py` 的 `_store_quick`、前置层短回复、招呼语都这么做）。记忆写入只走 `app/memory/h2.py: add()`，适配器碰不到。
4. **不许自己算关系**：好感结算唯一入口 `app/relation/engine.py: apply_appraisal()`，由内核调用；适配器只能读 `igate.prepare()` 的返回值。
5. **必须共用前置层**：新通道要先调 `intake.precheck(...)`，否则静默/改名/搜视频三条行为会与已有通道不一致。
6. **发送失败必须有兜底**：OneBot 落 `data/onebot_outbox.jsonl`（`_append_outbox`），官方通道落 `STATUS["last_error"]` + `log.warning`。不许把异常抛回平台事件循环。
7. **能力位要诚实**：`channel.Caps` 的 `stickers/voice/images/group_proactive/private_proactive/max_reply_chars/can_split` 必须按平台真实能力填。官方通道 `can_split=False`、`stickers=False`、`voice=False`、`group_proactive=False`，所以 `_reply_text()` 用 `"\n"` 拼接 + 截断 1500，**一次只能发一条**；OneBot 全开，`max_reply_chars=1500` 但可以连发多条气泡（`replies[1:4]`，每条间隔 0.45s）。
8. **状态要可观测**：至少给出 `connected/events/last_event_at/last_reply_at/last_error` 这五项，因为 `/v1/health` 直接透出（`qq.status()`）。
9. **凭据只进 `config.local.yaml` 或环境变量**，代码里不写账号密码；OneBot 那条线甚至不碰账号登录（登录在 NapCat 里）。

## 4. 数据流

**冷启动到第一句话**：

```text
python -m app.cli serve --port 8787
  → runtime.init()          建库 37 张业务表 + 视图 memories / 读人设 / 同步 traits / preflight / llm.build
  → uvicorn(app.main:app)   import 时执行 app = create_app()
      → FastAPI(...) + CORS(*) + include_router ×5
      → startup #1 _proactive_worker   （onebot.enabled 且 proactive_push 才起）
      → startup #2 _harvest_worker     （trend.harvest_enabled 且非 mock 才起）
      → startup #3 _qq_official_worker → qq.start_background() → channel="qq_official"
      → mount "/" panel 或占位页
  → 面板 GET /v1/board  →  assemble 首屏
  → 面板 POST /v1/chat  →  channel="panel" → loop.chat_turn → 写 events/记忆/关系
```

**桌面版**：

```text
python -m app.desktop [--port] [--no-gui]
  → _redirect_logs（frozen 时） → runtime.init()
  → 端口占用检查 → 线程里 uvicorn.run(app.main.app, log_level="warning")
  → _wait_port(127.0.0.1, port, 20s) 每 0.15s 试一次
  → webview.create_window(frameless, js_api=Api) → webview.start()
```

**官方 bot 一条消息**：`botpy WS` → `on_group_at_message_create` / `on_c2c_message_create` → `clean_inbound` → `ensure_user` → `intake.precheck`（短回复就结束）→ `_answer` → `igate.prepare` → `loop.chat_turn` → `_reply_text` → `message.reply`（一条，≤1500 字）。

## 5. 改这一层要注意

1. **加 router 就改 `create_app()`**：新 router 必须在第 47–50 行那段一起 `include_router`，并且自己带前缀或自己写全路径（`routes.py`/`onebot.py` 是无前缀的）。
2. **加 startup 任务要考虑顺序与收尾**：现有三个 startup 里只有 QQ 适配器有配对的 shutdown。新增长轮询任务必须自己处理 `CancelledError`，否则 `stop_background` 式的优雅退出会失效。
3. **`runtime.init()` 只能出现一次**：`runtime.init()` 是幂等的（`_runtime is None` 才构造），但 `Runtime.__init__` 里的 kv 写入（`human_life_enabled`）与 `purge_self` 是有副作用的引导逻辑，别在路由里再调。
4. **`channel.set_channel()` 的调用点只有三个**（`qq.start_background`、`onebot_event`、`routes.chat`）。新增通道照这个位置放，不要放进循环或每轮对话里。
5. **加 CLI 子命令要三处同时改**：`sub.add_parser(...)`、`set_defaults(func=...)`、实现 `_cmd_xxx(args) -> int`（返回码要真：0 成功 / 1 失败，脚本会看）。
6. **改 `Caps` 就改行为**：`can_split` 直接影响 `onebot.py` 的多气泡分支，`stickers`/`voice` 直接决定表情包与语音发不发，`max_reply_chars` 目前**没有**任何代码读取（只有 1500 这个字面量在 `qq._MAX_REPLY` 与 `channel.ONEBOT` 各写一次）——加限制时别以为改配置就够了。
7. **新通道必须走 `intake.precheck`**，并且不要顺手把 OneBot 那份内联逻辑也改一遍（会形成第三份拷贝）。

## 6. 交叉引用

- 相关：docs/12（这 5 个 router 的具体接口与鉴权）
- 相关：docs/04（`loop.chat_turn` 的入参与 `meta`）、docs/06（`h2`/`memengine` 与 `db.init` 建的表）、docs/08（`engine.init_traits` 与关系结算）
- 相关：docs/14（面板静态目录与占位页）、docs/21（`scripts/` 与 CLI 的分工）

## 变更记录（2026-11-01）

- **`group_at_only` 真正生效**：OneBot 入站里那个空分支（`pass`）改成了硬闸门——没 @、没叫名字、也不是"刚被她接过话的人"（`group_focus_user:<gid>`）就不回。
- **能力位切换时机**：`qq.start_background()` 只在官方通道真的起来了（enabled + appid + token + auto_start）之后才 `set_channel("qq_official")`，`qq.enabled=false` 时不再污染全局能力位。

- **拉黑期也留痕**：`chat_turn` 的 `blocked` 分支在返回前补写 `events`，被拉黑的人说的话库里仍然有记录（以前整段是空白）。
## 变更记录（2026-11-01）

- **OneBot 图片段进主链**：新增 `_segments_images()` 从 `message` 数组里取 `type=image` 的 `url`；
  只发图不写字时用 `[图片]` 占位（不再被 `empty` 丢掉），并以 `attachments=` 传给 `chat_turn`，
  交给工具环节的识图工具。拿不到 `url`（只有 NapCat 本地缓存名）就跳过——宁可不看，也不瞎猜。
