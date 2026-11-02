# 12 · HTTP 接口全量（app/api）

> 覆盖：`app/api/*.py`（6 个文件 / 2795 行）｜基于 2026-11-01 的代码状态｜上游：docs/04（`loop.chat_turn`）、docs/06（`h2` 记忆引擎）、docs/08（关系 `engine`）｜下游：docs/13（`main.py` 挂载 router）、docs/14（`panel/` 页面调用）、docs/20（面板后端接口）

## 1. 这一层干什么

这一层是 FastAPI 的路由层：把 HTTP 请求翻译成一次内核调用或一次库读写，再把结果拼成 dict 返回。它解决的是"五个不同来源的调用方怎么用同一套内核"——本地面板、桌面窗口、OneBot 上报、官方 bot 旁路、外部 OpenAI 兼容客户端，全部走 `app/api` 下的五个 `APIRouter`。

它自己**不做**业务判断：不拼 prompt（那是 `app/mind/loop.py` 的活）、不写记忆（`app/memory/h2.py: add()`）、不结算关系（`app/relation/engine.py: apply_appraisal()`）、不改人设文件的解析。路由函数里能看到的全是"取 runtime → 调某个模块 → 组响应"，唯一的例外是 `console.py` 与 `onebot.py` 的发送函数，它们直接对 NapCat 的 HTTP API 发请求，因为"发一条 QQ 消息"这件事没有更下层的模块。

调用方向是单向的：HTTP 层 → 内核层（`loop` / `h2` / `engine` / `life` / `web`）→ SQLite。反向只有一种情况：`onebot.py` 被 NapCat 主动 POST 进来，此时它是整个系统的入口而不是出口。

全仓共 **127 个操作、112 条路径模板**，分布在 5 个 router 上：`routes.py` 59、`console.py` 42、`trend.py` 12、`group.py` 10、`onebot.py` 1。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/api/__init__.py` | 0 | 空包标记（0 字节），没有任何符号 |
| `app/api/routes.py` | 1113 | 内核主 API：健康检查、对话、记忆、开发者台、人设/灵魂、设置、OpenAI 兼容 |
| `app/api/console.py` | 929 | 小鲸 Web 控制台后端，挂在 `/v1/console/*`，带可选口令依赖 |
| `app/api/trend.py` | 154 | 热梗库与 B 站搜索，挂在 `/v1/trend/*` |
| `app/api/group.py` | 100 | 群聊控制：自我介绍、静默、主动开口、昵称，挂在 `/v1/group/*` |
| `app/api/onebot.py` | 499 | OneBot v11 入站上报 `/onebot/event` + 出站 `api_base` 调用 + outbox 兜底 |

## 3. 模块逐一详解

### 3.1 `app/api/__init__.py`

**职责**：空文件，0 字节。它的存在只为了让 `app.api` 成为包，从而 `from app.api import trend as trend_mod` 这类语句成立（`onebot.py` 第 31 行、`intake.py` 第 52 行都这么导）。

**坑**：没有 `__all__`、没有 `router`，所以任何"从 `app.api` 直接拿 router"的写法都会失败，必须逐个模块导入。

### 3.2 `app/api/routes.py`（1113 行 / 16 个类 / 62 个函数 / 59 个操作）

**职责**：内核的"官方" HTTP 面。无前缀（`APIRouter()` 空参构造，第 31 行），所有路径自带 `/v1/` 前缀。它同时承担四件事：面板首屏数据、对话入口、开发者/调试台（全部要求 `?password=` 对得上 `developer.key`）、OpenAI 兼容转发。

**路由总表（59）**：

| 方法 | 路径 | 处理函数 | 请求体 / 参数关键字段 | 作用 |
|---|---|---|---|---|
| GET | `/v1/health` | `health` | 无 | 15 字段自检（见 3.2.3） |
| GET | `/v1/board` | `board` | 无 | 首屏聚合：人设/关系/作息/记忆/历史/阶段表/上下文/用量/主动消息/告别信 |
| POST | `/v1/chat` | `chat` | `ChatIn{text: 1–8000 字, source="panel", user_id=0}` | 面板主对话（见 3.2.2） |
| GET | `/v1/inspect` | `inspect` | 无 | 上一轮完整 prompt，没聊过返回 404 |
| GET | `/v1/relation/log` | `relation_log` | `?limit=20`（1–200） | `relation_log` 表倒序流水 |
| GET | `/v1/memories` | `memories` | `?q=&kind=&limit=50`（1–500） | `store.search_memories` 检索 |
| POST | `/v1/memories/{mid}/pin` | `pin_memory` | `PinIn{pinned=True}` | 钉住＝强制热记忆，`h2.pin` |
| GET | `/v1/memory/recall` | `memory_recall` | `?q`（必填）、`user_id=-1`、`chat_type=""` | 召回调试口，`h2.retrieve(...).as_dict()` |
| DELETE | `/v1/memories/{mid}` | `forget` | 路径 `mid` | `store.delete_memory`，不存在 404 |
| POST | `/v1/ingest` | `ingest_txt` | `IngestIn{path, mode="auto"}` | 导聊天记录 TXT 进记忆 |
| POST | `/v1/reference/ingest` | `reference_ingest` | `IngestIn{path, mode}` | 参考语料，只写 `ref_turns`/`ref_quotes` |
| GET | `/v1/reference/status` | `reference_status` | 无 | `turns`/`quotes`/`periods` + `quotes_sample`(8) |
| POST | `/v1/dev/login` | `dev_login` | `DevLoginIn{password}` | 试口令，返回 `taps_required` |
| GET | `/v1/dev/context` | `dev_context` | `?password` | 上下文 5 个配置 + `compress.status()` |
| POST | `/v1/dev/context` | `dev_context_set` | `ContextIn{mode="budget"}` + `?password` | 只许 `budget`/`ratio`，落 kv `context_mode` |
| POST | `/v1/dev/session/clear` | `dev_clear_session` | `?password` | 清 `events`/`relation_log`/`traits_log`/`summaries` + `h2.forget_all()` |
| GET | `/v1/dev/transfer/export` | `dev_export_persona` | `?password` | 导 `.life.json` 到 `exports/` |
| POST | `/v1/dev/transfer/import` | `dev_import_persona` | body＝JSON 原文、`?password`、`keep_events=1` | 导入档案并 `reload_persona()` |
| POST | `/v1/dev/state/set` | `dev_set_state` | `DevStateIn{affinity,trust,attraction,aversion,energy,mood}` + `?password` | 直接改三轴/厌恶/精力/心情 |
| POST | `/v1/dev/awake` | `dev_set_awake` | `?on=1&password=` | 写 kv `dev_awake` |
| POST | `/v1/dev/memory/clear` | `dev_clear_memories` | `?password` | 清全部记忆 + 厌恶归零 + 留痕 |
| POST | `/v1/dev/relation/reset` | `dev_reset_relation` | `?password` | 三轴归零 + 厌恶归零 + 留痕 |
| GET | `/v1/dev/anchors` | `dev_anchors` | `?password&limit=30` | `anchors` 表按 `freq` 倒序 |
| POST | `/v1/dev/companion/ruminate` | `dev_companion_ruminate` | `?password&user_id=0&limit=2&dry=0` | 陪伴层独处；`dry=1` 只挑料 |
| POST | `/v1/dev/memory/compact` | `dev_compact_cold` | `?password&dry=0` | `cold.compact_cold(dry_run=...)` |
| GET | `/v1/dev/memory/cold` | `dev_cold_status` | `?password` | `cold.stats()` |
| POST | `/v1/dev/memory/restore/{merge_id}` | `dev_restore_merge` | 路径 `merge_id` + `?password` | `cold.restore` 还原一次冷合并 |
| GET | `/v1/dev/archives` | `dev_archives` | `?password` | 扫 `Girl-memory/` 下的档案目录 |
| GET | `/v1/dev/archives/{name}` | `dev_archive` | 路径 `name` + `?password` | 单份档案；含 `..`、`/`、`\` 直接 400 |
| GET | `/v1/dev/last` | `dev_last` | `?password` | 上一轮 `last_prompt` + `last_meta`，只读 |
| GET | `/v1/dev/logs/export` | `dev_export_logs` | `?password&limit=200` | 写 `exports/debug-<时间戳>.log.json` |
| GET | `/v1/farewell` | `farewell` | 无 | 告别信；`status=pending` 且归档在则当场 `compose` |
| POST | `/v1/farewell/read` | `mark_farewell_read` | 无 | `finale.clear_status()` |
| GET | `/v1/settings/provider` | `get_provider` | 无 | `base_url`/`model`/`has_key`/`key_masked`/`is_mock` |
| POST | `/v1/settings/provider` | `set_provider` | `ProviderIn{base_url,api_key,model}` | 落盘 + `rt.apply_provider` + 变化才留痕 |
| GET | `/v1/settings` | `get_settings` | 无 | 设置项 + `styles` |
| POST | `/v1/settings` | `set_settings` | `SettingsIn{mix_mode,deep_think,show_delta,llm_appraisal,style}` | `save_settings` |
| GET | `/v1/stats` | `get_stats` | `?bucket=hour&limit=24&scope=session` | 用量；`scope=all` 含已离开的人 |
| GET | `/v1/session` | `session_state` | 无 | `session_payload`（seq/名字锁/厌恶相位） |
| POST | `/v1/relation` | `set_relation_dev` | `RelationIn{affinity,trust,attraction,aversion}` | 要求 `developer.enabled` |
| POST | `/v1/session/new` | `force_new_session` | 无 | 强制结束当前关系（先归档） |
| GET | `/v1/soul/letter-template` | `letter_template` | 无 | 记忆交接信模板，没配 404 |
| GET | `/v1/persona` | `get_persona_full` | 无 | 人物页只读：设定/向量/原话/反向锚点/阶段表/作息 |
| POST | `/v1/persona/traits` | `set_persona_trait` | `TraitIn{dim,value}` | 夹到 `min_value`/`max_value` + 写 `traits_log` |
| GET | `/v1/schedule` | `get_schedule` | 无 | 作息表 + 此刻 + 主动设置 + 人类语料状态 |
| POST | `/v1/schedule` | `set_schedule` | `ScheduleIn{proactive{...},human_life,human_life_day_type}` | 写一批 kv（见 3.2.5） |
| GET | `/v1/human_life/now` | `get_human_life_now` | 无 | 紧凑状态卡，永远 200 |
| GET | `/v1/diary` | `get_diary` | 无 | `diary.status()` |
| POST | `/v1/diary/run` | `diary_run` | `DiaryRunIn{day=""}` | 补写某天日记（默认昨天），拒绝未来日期 |
| GET | `/v1/settings/models` | `get_model_settings` | 无 | provider + styles + `TASK_ROUTE` + `TASK_LABEL` |
| POST | `/v1/settings/models` | `set_model_settings` | `ModelSettingsIn{temperature,max_tokens,small_model}` | 温度夹 0–2、tokens 夹 64–8000，写本地配置 |
| POST | `/v1/soul/export` | `soul_export` | 无 | 导出完整灵魂档案 + 字节数 |
| GET | `/v1/soul/snapshots` | `soul_snapshots` | 无 | 列 `data/snapshots/*.life.json` |
| POST | `/v1/soul/snapshots` | `soul_snapshot_create` | 无 | 建 `snap-YYYYmmdd-HHMMSS.life.json` |
| POST | `/v1/soul/snapshots/restore` | `soul_snapshot_restore` | `SnapshotRestoreIn{name}` | 校验名字 → 导入 → `reload_persona()` |
| POST | `/v1/persona/reload` | `reload_persona_hot` | `?path=&password=` | 热加载人设（见 3.2.4） |
| POST | `/v1/soul/import` | `soul_import` | body＝`.life.json` 原文 | `transfer.import_payload` + reload |
| GET | `/v1/models` | `models` | 无 | OpenAI 兼容：`ai-life/<persona.key>` |
| POST | `/v1/chat/completions` | `openai_compat` | `OpenAIIn{messages,model,stream=False}` | OpenAI 兼容对话（见 3.2.6） |

**请求/响应模型（16 个 Pydantic 类，字段照抄）**：

| 类 | 字段 | 默认值 |
|---|---|---|
| `ChatIn` | `text: str = Field(min_length=1, max_length=8000)`、`source: str`、`user_id: int` | `"panel"`、`0` |
| `IngestIn` | `path: str`、`mode: str` | `"auto"` |
| `OpenAIIn` | `messages: list[dict[str, Any]]`、`model: str \| None`、`stream: bool` | `None`、`False` |
| `ProviderIn` | `base_url: str`、`api_key: str`、`model: str` | 全是 `""` |
| `SettingsIn` | `mix_mode`、`deep_think`、`show_delta`、`llm_appraisal: bool \| None`、`style: str \| None` | 全 `None` |
| `DevStateIn` | `affinity`、`trust`、`attraction`、`aversion`、`energy`、`mood: float \| None` | 全 `None` |
| `DevLoginIn` | `password: str` | 必填 |
| `RelationIn` | `affinity`、`trust`、`attraction`、`aversion: float \| None` | 全 `None` |
| `PinIn` | `pinned: bool` | `True` |
| `ContextIn` | `mode: str` | `"budget"` |
| `TraitIn` | `dim: str`、`value: float` | 必填 |
| `ScheduleProactiveIn` | `enabled: bool \| None`、`cooldown_min: int \| None`、`daily_max: int \| None`、`intimate: bool \| None`、`quiet_start: int \| None`、`quiet_end: int \| None`、`thought_daily_max: int \| None`、`dnd_minutes: int \| None`、`clear_dnd: bool \| None` | 全 `None` |
| `ScheduleIn` | `proactive: ScheduleProactiveIn \| None`、`human_life: bool \| None`、`human_life_day_type: str \| None` | 全 `None` |
| `ModelSettingsIn` | `temperature: float \| None`、`max_tokens: int \| None`、`small_model: str \| None` | 全 `None` |
| `SnapshotRestoreIn` | `name: str` | 必填 |
| `DiaryRunIn` | `day: str` | `""` |

**关键实现 1：`/v1/chat` 的 uid=0 特例**（第 150–162 行）

`chat()` 的流程只有四步，但每一步都有原因：

1. 调 `channel.set_channel("panel")`——把进程级能力位切回面板（表情包/多气泡可用）。这是**全局可变状态**，谁最后调谁说了算。
2. 取 `uid = int(inp.user_id or 0)`。
3. 算 `is_lover`：`uid > 0` 时查 `_igate.is_lover_of(uid)`（这个人是不是唯一恋人）；`uid == 0` 时**直接给 `True`**。原因写在注释里：面板是"不分场合"的主对话，单人模式下没有 user 概念，但语气层需要知道"是不是在跟恋人说话"。
4. `await loop.chat_turn(rt, text, source, user_id, is_lover=...)`——注意**不传 `chat_type`**，所以走"不分场合"分支：摘要与群聊记忆都保留，只有语气按恋人走。

**关键实现 2：`/v1/health` 的 15 个字段**（第 54–85 行）

| 字段 | 来源 | 说明 |
|---|---|---|
| `ok` | 常量 `True` | 能响应就是 ok，不反映模型可用性 |
| `provider` | `"mock" if rt.cfg.provider.is_mock else "openai"` | `is_mock` 的判据是 **api_key 为空**（`config.py` 的 property） |
| `model` | `rt.cfg.provider.model` | 主模型名 |
| `active_model` | `model_registry.pick("chat_default").name` | 实际会被路由到的模型名 |
| `force_model` | `rt.cfg.provider.force_model`（空则 `""`） | 非空时全局强制所有任务走它 |
| `context_window` | `rt.cfg.context.window` | 默认 64000 |
| `reserve_output` | `rt.cfg.context.reserve_output` | 默认 1024 |
| `max_tokens` | `rt.cfg.provider.max_tokens` | 默认 400 |
| `db` | `str(rt.cfg.db_file)` | `data/life.db` 绝对路径 |
| `persona` | `rt.persona.name` | 当前人设名 |
| `version` | `app.__version__` | `0.1.0` |
| `preflight` | `rt.preflight` | `{ok, base_url, model, force_model, warnings, checked_at}`，启动时算好，零网络 |
| `channel` | `channel.as_dict()` | 当前能力位：`name/stickers/voice/images/group_proactive/private_proactive/max_reply_chars/can_split` |
| `qq` | `qq_adapter.status()`，导入失败则 `{}` | `enabled/appid/sandbox/started/connected/events/last_event_at/last_reply_at/last_error` |
| `offline` | kv 三个键 | `{count: llm_offline_count, last: llm_offline_last, reason: llm_offline_reason}` |

**关键实现 3：`/v1/persona/reload`**（第 1005–1036 行）

1. 先过 `_dev_ok(password)`——要求 `cfg.developer.enabled` 为真且口令等于 `cfg.developer.key`。
2. `path` 非空时解析目标文件：绝对路径直接用；相对路径**先试 `DATA_ROOT / path`**，不存在再退到 `Path.cwd() / path`；都不存在 404。命中后写 `rt.cfg.persona_override`。
3. 记下 `before = {name, key}`，调 `rt.reload_persona()`（内部重读 YAML + `engine.init_traits` 同步人格向量基线）。
4. `after != before` 才写 `continuity.record("persona_changed", ...)`，并带上 `file`。
5. 返回 `before/after/rules 条数/anchors 条数/file`——`rules` 与 `anchors` 是"新设定生效了吗"的直观证据。

**关键实现 4：记忆与关系接口的分工**

| 想做的事 | 走哪个口 | 底层 |
|---|---|---|
| 批量导入历史 | `POST /v1/ingest` | `app/memory/ingest.py` |
| 只学风格不占记忆位 | `POST /v1/reference/ingest` | `ref_turns`/`ref_quotes` |
| 检索记忆 | `GET /v1/memories` | `store.search_memories(q, kind, limit)` |
| 看一句话会想起什么 | `GET /v1/memory/recall` | `h2.retrieve(...).as_dict()` |
| 钉住/取消钉住 | `POST /v1/memories/{mid}/pin` | `h2.pin`，钉住=永不衰减 |
| 删单条 | `DELETE /v1/memories/{mid}` | `store.delete_memory` |
| 看关系流水 | `GET /v1/relation/log` | `engine.recent_log` |
| 手改三轴 | `POST /v1/relation` | `engine.set_relation` + `session.set_aversion` |
| 看阶段/称呼/下一阶段 | `GET /v1/board` 的 `relation` 段 | `engine.stage_payload` |

**数据结构 / 存储**：

- 主要读：`mem_item`（经 `h2`/`memengine`）、`relation`、`relation_log`、`traits`、`traits_log`、`events`、`summaries`、`anchors`、`ref_turns`、`ref_quotes`、`gags`、`web_videos`。
- 主要写：`GET /v1/board` 里 `engine.set_relation` 不会写，但 `proactive.tick` 会写主动消息与 kv；`POST /v1/dev/session/clear` 直接 `DELETE` 四张表 + `UPDATE relation` + `UPDATE session`。
- 读写的 kv 键：`context_mode`、`dev_awake`、`llm_offline_count`、`llm_offline_last`、`llm_offline_reason`、`naming_step`、`naming_tries`、`memory_degraded`、`last_used_tokens`、`human_life_enabled`、`proactive_*`（一批）、`letter_status`/`letter_text`/`letter_name`/`letter_path`/`letter_desktop`、`diary:*`。
- 文件：`DATA_ROOT/exports/`（灵魂档案 `*.life.json` 与 `debug-*.log.json`）、`DATA_ROOT/data/snapshots/`、`DATA_ROOT/Girl-memory/<name>/离开记录.md`。

**坑**：

- `GET /v1/board` 是 GET 但有副作用：内部调 `proactive.tick(rt, persona)`，会在到点/到配额时真的产出主动消息。轮询面板等于轮询心情。
- `POST /v1/settings/provider` 的 `api_key` 留空表示"不改"：`key = inp.api_key.strip() or rt.cfg.provider.api_key`。想真清空 key 只能改 `config.local.yaml`。
- `GET /v1/inspect` 与 `/v1/dev/last` 返回的是同一份 `rt.last_prompt`，但前者 404、后者返回 `has_prompt=false`，前端要分别处理。
- `_mask` 对长度 ≤12 的 key 只回 `…`，也就是说短 key 永远看不到首尾。
- `GET /v1/farewell` 是唯一"生成中会当场重试"的 GET：`status == "pending"` 时若 kv `letter_archive` 指向的文件存在，就 `await finale.compose(...)` 补一次，失败被 `except` 吞掉。
- `/v1/persona/reload` 的 `password` 走 query 而不是 body，和同一 router 里别的 POST 不一致（登录口令出现在 URL 里，会被访问日志记下）。
- `POST /v1/schedule` 里的 `dnd_minutes` 用的是 **uid=0 全局免打扰**（`proactive.set_dnd(0, ...)`），注释明说"QQ 私聊那条线也会看它"——即面板设的免打扰会影响所有人。

**耦合**：被 `panel/console.html`、`panel/index.html`、`desktop.py` 起的窗口、外部 OpenAI 客户端调用；内部调 `loop`、`engine`、`h2`、`store`、`reference`、`cold`、`compress`、`transfer`、`model_registry`、`clock`/`diary`/`human_life`/`proactive`、`finale`、`session`、`continuity`、`db`，以及 `trend.py` 的 `_dev_ok`（`trend.py` 反向导入）。

### 3.3 `app/api/console.py`（929 行 / 8 个类 / 47 个函数 / 42 个操作）

**职责**：小鲸 Web 控制台的后端。router 带前缀 `/v1/console`，并且**在 router 级别**挂了口令依赖：

```python
router = APIRouter(prefix="/v1/console", dependencies=[Depends(_console_auth)])
```

`_console_auth` 的三段逻辑：`cfg.console.token` 为空 → 直接放行（注释写明"没配就只靠回环地址"）；非空 → 从 `X-Console-Token` 头或 `?token=` 查询串取；`hmac.compare_digest` 不等就 401。

**按功能分组的路由总表（42）**：

记忆（4）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/memory/{user_id}/stats` | `memory_stats` | 路径 `user_id` | `count` + `by_kind` |
| GET | `/v1/console/memory/{user_id}` | `memory_list` | `?limit=50`（1–500） | 列某人的记忆（id/kind/content/importance/pinned/first_seen） |
| DELETE | `/v1/console/memory/{user_id}` | `memory_clear` | 路径 `user_id` | `h2.forget_user`，删了才留痕 |
| DELETE | `/v1/console/memory/item/{memory_id}` | `memory_delete_item` | 路径 `memory_id` | 删单条，不存在 404 |

印象（2）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/impression/{user_id}` | `impression_get` | 路径 `user_id` | `member_impression` 行；解析 `traits_seen`/`habits` 两个 JSON 列，坏 JSON 退成 `[]` |
| DELETE | `/v1/console/impression/{user_id}` | `impression_clear` | 路径 `user_id` | 直接 `DELETE`，无留痕 |

关系（2）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/relation/{user_id}` | `relation_get` | 路径 `user_id` | 四轴四舍五入到 1 位、`stage`、`hit_100_at`、`lover` |
| POST | `/v1/console/relation` | `relation_set` | `RelationIn{user_id, affinity, trust, attraction, aversion}` | 先 `relation_of` 确保行存在，再 `users.update_relation` |

开关（4）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/thinking` | `thinking_get` | 无 | 深度思考开关（读 `current_settings()["deep_think"]`） |
| POST | `/v1/console/thinking` | `thinking_set` | `ThinkIn{enabled}` | `model_registry.save_settings(deep_think=...)` |
| GET | `/v1/console/testing` | `testing_get` | 无 | 测试期三句应答开关 + `TESTING_REPLIES` 原文 |
| POST | `/v1/console/testing` | `testing_set` | `TestingIn{enabled}` | 写 kv `testing_reply`（`"1"`/`"0"`） |

观察（3）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/observations` | `observations_list` | `?limit=50`（1–200）、`?topic=` | `human_notes` + `nickname` 映射 + `by_topic` 计数 |
| DELETE | `/v1/console/observations/{note_id}` | `observation_delete` | 路径 `note_id` | 删一条，`rowcount==0` 404 |
| DELETE | `/v1/console/observations` | `observations_clear` | `?topic=` | 带 topic 只清该类，否则全清 |

好友与代发（8）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/friends` | `list_friends` | 无 | `qq_user` 全量 + 每人四轴/阶段/`msg_count`/`lover` |
| POST | `/v1/console/friends` | `add_friend` | `FriendIn{openid: min_length=3, nickname=""}` | **只在本地登记**，不真加 QQ 好友 |
| GET | `/v1/console/friends/requests` | `friend_requests` | `?status=&limit=50` | 好友申请列表 + `pending` + `auto_accept` |
| POST | `/v1/console/friends/requests/{request_id}` | `friend_request_decide` | `FriendRequestIn{approve=True}` | 调 `onebot.accept_request` → `set_friend_add_request` |
| DELETE | `/v1/console/friends/requests/{request_id}` | `friend_request_delete` | 路径 `request_id` | 删申请记录（不通知 QQ） |
| POST | `/v1/console/friends/{user_id}/forget` | `forget_friend` | 路径 `user_id` | 针对性失忆（关系/记忆/印象/观察/私密/原文） |
| DELETE | `/v1/console/friends/{user_id}` | `delete_friend` | 路径 `user_id` | 硬删：9 张表 + kv 前缀键 + 留痕 |
| POST | `/v1/console/send` | `console_send` | `SendIn{user_id, text, as="ai", chat_type, group_id}` | 见下"模拟用户/AI 主动说" |

聊天与表情（4）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/chat/{user_id}` | `get_chat` | `?limit=50`、`?chat_type=private\|group`、`?group_id=` | `events` 倒序取再翻正；老数据 `chat_type=''` 两边都算；附 `counts` |
| GET | `/v1/console/stickers` | `list_stickers` | `?limit=60`（1–300）、`?q=`、`?safe_only=True` | 表情索引（`safe` 过滤 + title/quote 关键词） |
| GET | `/v1/console/sticker/{sticker_id}.webp` | `sticker_file` | 路径 `sticker_id` | 返回 `FileResponse(media_type="image/webp")` |
| POST | `/v1/console/send_sticker` | `send_sticker` | `StickerIn{user_id, sticker_id}` | 以 `file://` URI 发 image 段 |

想法与连续性（5）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/companion/{user_id}` | `companion_board` | 路径 `user_id` | `companion.status` + `proactive.settings_for`（失败只让子字段为空） |
| GET | `/v1/console/companion/{user_id}/thoughts` | `companion_thoughts` | `?limit=10`（1–100） | `thoughts.recent` |
| GET | `/v1/console/continuity` | `continuity_board` | `?limit=50`（1–500） | 连续性账本：`count` + `major`（近 4 条摘要）+ 明细 |
| GET | `/v1/console/mind` | `mind_board` | 无 | 印象 20 条 + 观察 30 条 + 反思 12 条 + 日记 14 篇 |
| POST | `/v1/console/mind/run` | `mind_run` | `MindRunIn{kind="thought", user_id=0}` | `kind` ∈ `thought`/`impression`/`diary`/`eval`，其它 400 |

状态（1）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/status` | `console_status` | 无 | 顶部状态：人设/模型/provider/表情计数/好友数/记忆数/待处理申请/`console_auth`/`onebot` 四项 |

群（3）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/groups` | `groups_list` | `?limit=20`（1–100） | 从 `events.group_id` 里推已知群号 |
| GET | `/v1/console/groups/{group_id}/members` | `group_members` | 路径 `group_id` | `get_group_member_list`，分"已认识/没认识" |
| POST | `/v1/console/groups/{group_id}/greet` | `group_greet` | `?limit=3`（1–10） | 逐个 @ 招呼生面孔，每人只一次 |

OneBot（2）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/onebot/login` | `onebot_login` | 无 | `get_login_info`+`get_status`；未启用只回 `enabled=false` 不报错 |
| POST | `/v1/console/onebot/sync` | `onebot_sync` | 无 | `get_friend_list` 拉真实好友进 `qq_user`，返回 `added` |

亲密（4）：

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/console/intimate/exemplars` | `intimate_exemplars` | 无 | 先 `ensure_seeded()` 再返回范本库 + 统计 |
| GET | `/v1/console/intimate/{user_id}` | `intimate_view` | `?limit=20`（1–100） | `mode`（已过期判定）/`lover`/会话记录/偏好档/范本统计 |
| POST | `/v1/console/intimate/{user_id}/analyze` | `intimate_analyze` | 路径 `user_id` | 手动跑 `analyze_user`，把私密记录折成偏好档 |
| DELETE | `/v1/console/intimate/{user_id}` | `intimate_clear` | 路径 `user_id` | 只删 `intimate_context`，返回删除条数 |

**关键实现：`POST /v1/console/send` 的两个方向**（第 469–505 行）

1. 归一化 `chat_type`：只接受 `private`/`group`，其它一律变 `""`（不分场合）。
2. `as == "me"`：**模拟用户发来一句话**。先 `igate.prepare(user_id, text, chat_type)` 拿 `(mode, note, is_lover, is_private)`，再 `loop.chat_turn(source="qq_onebot", ...)`，最后 `igate.finish(...)`。回复只回给控制台，**不推回 QQ**（注释原话）。
3. `as == "ai"`：AI 主动说一句。群聊必须带 `group_id`（否则 400），调 `_qq_send(api="send_group_msg")`；私聊调 `_qq_send` 默认 `send_private_msg`，`user_id` 用 `_openid_of(user_id)` 把本地 id 换成 QQ 号（非纯数字 400，查不到 404）。无论发没发出去都 `store.add_event("her", ...)`——这是"她说过了"的事实记录，`sent` 字段才是"送达了吗"。

**坑**：

- 三条"发送"路径各自实现：`_qq_send`（console，`httpx.AsyncClient(timeout=30)`、只认 `data["status"]=="ok"`）、`onebot._post_api`（返回 `dict|None`）、`adapters/qq.py`（走 botpy SDK）。三处的失败语义不同。
- `/intimate/exemplars` 必须注册在 `/intimate/{user_id}` 之前（第 887 行 vs 895 行）：后者把路径段声明成 `int`，顺序反了 `exemplars` 会被 422 拦下。
- `delete_friend` 是硬删：9 条 `DELETE`（`events`/`user_relation`/`lover`/`member_impression`/`human_notes`/`intimate_context`/`intimate_profile`/`friend_request`/`qq_user`）+ `h2.forget_user` + 一串 kv 清标记（`greeted:<openid>`、`intimate_mode*`、`love_pending`、`proactive_last_ts`）+ 一条 `continuity.record`。
- `console_status` 里的 `"memories": h2.memory_stats()["total"]` 是全库计数，不是当前会话。
- `group_greet` 里 `unknown` 变量只用于最终统计，实际循环遍历的是 `items`——两者在"已知成员"上等价，但读数时容易误以为循环的是 `unknown`。

**耦合**：前端 `panel/console.html`；内部调 `h2`、`stickers`、`evolve`、`loop`、`friend_mod`、`love`、`users`、`diary_mod`，并反向导入 `app.api.onebot` 的 `accept_request`/`_post_api`/`get_group_members`/`get_login_info`/`get_friend_list`——**`console.py` 依赖 `onebot.py`，反向没有**。

### 3.4 `app/api/trend.py`（154 行 / 2 个类 / 13 个函数 / 12 个操作）

**职责**：热梗库与 B 站搜索，前缀 `/v1/trend`。明确的设计约束写在模块 docstring：热梗存 `gags` 表，**不写 `memories`**，对话里只是偶尔给一段可选提示，不参与召回、不影响好感。

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| GET | `/v1/trend/search` | `trend_search` | `?q`（必填）、`?limit=12`（1–30） | B 站搜索，命中结果顺手 `gags.save_video`；异常转 502 |
| POST | `/v1/trend/send_video` | `send_video` | `SendVideoIn{user_id, bvid}` | 查 `bili.video_info` → 发"标题\n链接" → 落 `events` |
| GET | `/v1/trend/stats` | `trend_stats` | 无 | `gags.stats()` + `harvest.status(rt)` |
| GET | `/v1/trend/gags` | `list_gags` | `?limit=100`（1–500）、`?q=` | 热梗列表 |
| DELETE | `/v1/trend/gags/{gag_id}` | `remove_gag` | 路径 `gag_id` | 删一条，不存在 404 |
| POST | `/v1/trend/gags/clear` | `clear_gags` | `?password` | **要开发者口令**（借 `routes._dev_ok`），清空 `gags` |
| POST | `/v1/trend/harvest` | `harvest_now` | `HarvestIn{limit=20, keyword=""}` | 手动抓一轮（同步、会阻塞网络） |
| GET | `/v1/trend/hint` | `hint` | 无 | 会注入对话的那段提示 + 5 条样本 |
| GET | `/v1/trend/lookup` | `web_lookup` | `?kw`（必填）、`?context=` | 查词：黑话表→缓存→百度→Bing |
| GET | `/v1/trend/glossary` | `glossary` | 无 | 内置 `AI_NICKNAMES` |
| GET | `/v1/trend/learned` | `learned_list` | `?limit=50`（1–200） | 自动学到的新词（按可信度） |
| DELETE | `/v1/trend/learned/{gag_id}` | `learned_delete` | 路径 `gag_id` | 删一条，不存在 404 |

**唯一的非路由函数 `match_search_intent(text)`**：命中 11 个动词之一（`搜一下`/`搜个`/`搜一搜`/`找一下`/`找个视频`/`发个视频`/`来个视频`/`b站搜`/`B站搜`/`帮我搜`/`搜搜`），取动词右侧、去掉 `" 一下个的，。！？"` 后作为关键词；关键词为空返回 `None`。被 `onebot.py` 第 315 行和 `adapters/intake.py` 第 55 行同时调用。

**坑**：`send_video` 的注释说"先发卡片链接，再发文字标题"，但代码只发了一条纯文本 `f"{title}\n{url}"`（第 49–50 行），没有卡片段。

### 3.5 `app/api/group.py`（100 行 / 3 个类 / 10 个函数 / 10 个操作）

**职责**：群聊控制面，前缀 `/v1/group`。全是薄封装，逻辑在 `app/group/*`。

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| POST | `/v1/group/introduce` | `introduce` | `IntroIn{group_id=None, mention_all=True}` | 让 `gintro.send_intro` 在群里自我介绍；`group_id` 为 `""`/`0` 时视为 `None`（自动挑群） |
| GET | `/v1/group/status` | `group_status` | 无 | `gctx.standby_state()`（待机/焦点状态） |
| POST | `/v1/group/silence` | `set_silence` | `SilenceIn{minutes=gpro.STOP_MINUTES, reason="手动"}` | 静默，返回 `until` |
| POST | `/v1/group/unsilence` | `clear_silence` | 无 | 解除静默 |
| POST | `/v1/group/speak` | `speak_now` | 无 | 立刻主动说一句；静默中直接返回 `ok=false` |
| POST | `/v1/group/start` | `start_active` | `?interval=600` | 起后台主动循环，返回 `started`（已跑则 `false`） |
| POST | `/v1/group/stop` | `stop_active` | 无 | 停主动循环 |
| GET | `/v1/group/nick/{user_id}` | `get_nick` | 路径 `user_id` | 取"她怎么叫他" |
| POST | `/v1/group/nick` | `set_nick` | `NickIn{user_id, nick}` | `by="user"` 落库；空昵称 400 |
| POST | `/v1/group/nick/{user_id}/suggest` | `suggest_nick` | 路径 `user_id` | 生成建议昵称（不落库） |

**坑**：`start_active`/`speak_now` 声明成 `async` 是有原因的，源码注释写了：同步端点跑在线程池里，没有运行中的事件循环，`gactive.start()` 里的 `create_task` 会炸。`app/group/active.py: start()` 还额外做了退化处理——没有事件循环就改起 `daemon` 线程自己 `asyncio.run`。

### 3.6 `app/api/onebot.py`（499 行 / 0 个类 / 9 个函数 / 1 个操作）

**职责**：OneBot v11 接入（NapCat / LLOneBot / Lagrange）。只有一个路由，但它是整个系统最长的单函数（`onebot_event` 第 205–499 行）。

| 方法 | 路径 | 处理函数 | 参数 | 作用 |
|---|---|---|---|---|
| POST | `/onebot/event` | `onebot_event` | OneBot v11 上报 JSON（`post_type`/`message_type`/`message`/`sender`/...） | 入站总入口，见下方分流 |

**入站分流顺序（`onebot_event`）**：

1. `channel.set_channel("onebot")`——NapCat 全量能力，先切再判。
2. `cfg.enabled` 为假 → `{"ok": False, "ignored": True, "reason": "onebot disabled"}`。
3. 配了 `access_token` → 校验 `Authorization`，既接受 `Bearer <token>` 也接受裸 token，不等即忽略。
4. 解析 JSON 失败 → `{"ok": False, "error": "bad json"}`。
5. `post_type == "request"` → `_handle_friend_request`：只处理 `request_type == "friend"`；记进 `friend_request`；`auto_accept_friend_request`（默认 `True`）就直接 `accept_request`，否则留 pending 等控制台点。
6. `post_type != "message"` → 忽略。
7. `self_id` 与自己配置不符 → 忽略（"other bot"）；`user_id` 为空或等于自己 → 忽略（"self message"）；命中 `ignore_ids` → 忽略（注释：群里另一个机器人，否则两个 bot 互相抬杠）。
8. `_segments_text` 把数组段或 CQ 字符串折成 `(纯文本, at 列表)`；空文本忽略。
9. `users_mod.ensure_user(user_qq, card or nickname)` 建/取本地用户，记下 `_known_before`。
10. **群聊增强**（仅 `message_type=="group"`）：`gactive.remember_group` 先登记群号 → `gpro.detect_stop(text)` 命中就静默并回 `silenced`（不再说话）→ 静默期内没 @ 也没叫名字 → `ignored`；被 @ 则 `break_silence` → `gpro.should_be_quiet()`（她连发 2~3 条没人接）且没 @ → `standby` → `gctx.handle_nick_change`（改名指令）→ `need_name`/`nick_set` 两种短路 → 没 @ 没名字时用 `gctx.who_is_talking_to_her` 判语境，判否就 `not talking to her`；被 @ 时写 kv `group_focus_user`/`group_focus_at`。
11. **搜视频意图**：`trend_mod.match_search_intent(text)` 命中 → `bili.search_videos(kw, 3)`；有结果就直接回链接（群里带 at 段），失败落 outbox，返回 `mode="search"` 且**不进内核**；搜不到才继续往下当普通聊天。
12. **不懂就查**：`lookup_mod.needs_lookup(text)` 且没被拉黑 → 先发一句 `"我想一想。"`（群/私聊不同 body），写 `events`（`meta={"lookup_ack": True}`），再 `lookup_mod.prompt_hint(...)` 作为 `prefetched_hint`，并把 `slow=True`。整段 `try/except` 包裹，失败就当普通聊天。
13. **亲密门控**：`igate.prepare(user_id, text, chat_type)` 拿 `mode/note/is_lover/is_private`。
14. **内核**：`loop.chat_turn(source="qq_onebot", user_id, chat_type, group_id, intimate_mode, intimate_note, is_lover, prefetched_hint, slow)`。之后 `igate.finish(...)`。
15. **出站拼装**：`replies` 空则用 `["……"]`；`caps = channel.caps()`，`split = caps.can_split and len(replies) > 1`。群聊首段是 `at` + 文本，私聊只有文本。
16. **表情包**：`caps.stickers` 为真且 `random.random() < cfg.sticker_chance`（默认 0.45）→ `stickers.pick(anchor_scene)`，命中就把 `image` 段（`file://` URI）追加到末尾。
17. **语音**：`caps.voice` 为真时，`voices.wants_sing(text)`（唱歌/来一首/情书）**必发**；否则按 `cfg.voice_chance`（默认 0.15）概率发。命中就把 `record` 段追加，并把首条文本替换成 `"那就听一小段。"`。两段都各自 `try/except`，失败不影响文字。
18. **发送**：群 `send_group_msg{group_id, message}`，私聊 `send_private_msg{user_id, message}`。返回 `None` 就 `_append_outbox(...)`。
19. **多气泡**：发送成功且 `split` 且 `len(replies) > 1` 时，把 `replies[1:4]` 逐条补发，每条间隔 `await asyncio.sleep(0.45)`。
20. **新人招呼**：群聊且 `not _known_before`、`greeting_enabled()`、非家人跳过、没被拉黑、没招呼过 → 发 `at` + `greeting` 一行，`mark_greeted`，写 `events(meta={"greet": True})`。
21. 返回 `{ok, replied, user_id, message_type, sent, sticker, voice, greeted}`。

**出站调用 `_post_api(cfg, api, payload)`**（第 64–77 行）：`url = api_base.rstrip("/") + "/" + api`；有 `access_token` 就加 `Authorization: Bearer`；`httpx.AsyncClient(timeout=cfg.timeout_s or 10)`；非 2xx 返回 `None`；JSON 解析失败当作 `{"ok": True}`；任何异常返回 `None`——**永不向上抛**，因为失败要落 outbox 而不是打断 QQ 回包。

**outbox**：`OUTBOX = DATA_ROOT / "data" / "onebot_outbox.jsonl"`。`_append_outbox` 逐行写 JSON（`ensure_ascii=False`），`OSError` 静默吞掉。本模块内调用点 3 处：搜视频发送失败（第 335 行）、主回复发送失败（第 451 行）、新人招呼发送失败（第 481 行）；多气泡补发失败**不写** outbox。第 4 个调用点在 `main.py` 的主动外发任务里（第 86 行，记录带 `"source": "proactive"`）。

**辅助函数（被 console.py 复用）**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `_segments_text` | `(message: Any) -> tuple[str, list[str]]` | 数组/CQ 串 → `(纯文本, at 的 QQ 列表)` |
| `_append_outbox` | `(record: dict[str, Any]) -> None` | 追加一行 outbox |
| `_post_api` | `async (cfg, api: str, payload: dict[str, Any]) -> dict[str, Any] \| None` | 调 NapCat HTTP API |
| `get_login_info` | `async (cfg) -> dict[str, Any]` | 问 `get_login_info`+`get_status`，返回 `online/user_id/nickname/good/api_base` |
| `get_friend_list` | `async (cfg) -> list[dict[str, Any]] \| None` | `get_friend_list` → `[{openid, nickname}]` |
| `get_group_members` | `async (cfg, group_id: int \| str) -> list[dict[str, Any]] \| None` | `get_group_member_list` → `[{openid, nickname, role}]` |
| `_handle_friend_request` | `async (cfg, payload: dict[str, Any]) -> dict[str, Any]` | 好友申请入库/自动通过 |
| `accept_request` | `async (cfg, request_id: int, approve: bool = True) -> bool` | 回 `set_friend_add_request`，成功后 `ensure_user` |

**坑**：

- 第 310–312 行是一段死代码：`if message_type == "group" and cfg.group_at_only and not ats and not str(cfg.self_id) in ats: pass`——分支体只有 `pass`，`group_at_only` 这个配置在这里不起任何作用（真正的过滤在步骤 10 的语境判断里）。
- 第 268/281/294/310 行混用了 `str(cfg.self_id) in ats` 与 `not str(cfg.self_id) in ats` 两种写法，语义一样但可读性差。
- `split` 判断依赖 `channel.caps()` 的**进程级全局值**：`/v1/chat` 或官方适配器随时会把它改掉，两个请求交错时先取到的那一轮会用到另一个通道的能力位。
- 私聊分支不判 `cfg.group_at_only`、也不判静默——静默只作用于群。
- 拉黑用户在步骤 12 被 `not users_mod.is_blocked(...)` 挡住"不懂就查"，但**不会**挡住 `chat_turn`（内核自己有一层拉黑短路）。

### 3.7 鉴权全景（哪些要口令，哪些裸奔）

| 组 | 路径 | 需要什么 | 依据 |
|---|---|---|---|
| 面板主 API | `routes.py` 里除 `/v1/dev/*`、`/v1/persona/reload`、`POST /v1/relation`、`POST /v1/session/new` 之外的 37 个端点（含 `/v1/health`、`/v1/board`、`/v1/chat`、`/v1/memories`、`/v1/stats`） | **什么都不需要** | 无 `Depends`，服务只监听 `127.0.0.1` |
| 开发者台 | 19 个 `/v1/dev/*` 中的 18 个（`POST /v1/dev/login` 例外：它本身是"试口令"，不吃 `?password=`，归上面一行）、`POST /v1/persona/reload` | `?password=` 必须等于 `developer.key` 且 `developer.enabled` | `_dev_ok()`，否则 403 |
| 开发者开关门 | `POST /v1/relation`、`POST /v1/session/new` | 只要 `developer.enabled` 为真，**不校验口令** | 直接读 `rt.cfg.developer.enabled` |
| 控制台 | `/v1/console/*`（42 个全覆盖） | `cfg.console.token` 非空时要求 `X-Console-Token` 或 `?token=` | router 级 `Depends(_console_auth)` |
| 热梗清空 | `POST /v1/trend/gags/clear` | `?password=`（借 `_dev_ok`） | 单个端点自带 |
| 热梗其它 | `/v1/trend/*` 其余 11 个 | 无 | — |
| 群控制 | `/v1/group/*` | 无 | — |
| OneBot 入站 | `/onebot/event` | 配了 `onebot.access_token` 才校验 `Authorization` | 函数体内手写比对 |
| OpenAI 兼容 | `/v1/models`、`/v1/chat/completions` | 无 | — |

`/v1/chat` 的现状要单独说清楚：**没有鉴权、且 CORS 全开**。`main.py` 里 `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`，注释写明"只监听 127.0.0.1，放开 CORS 是为了让面板在 `file://` 直开时也能联调；若以后改成 0.0.0.0 对外提供，必须收窄 allow_origins"。也就是说：任何本机进程（含浏览器里任意网页的脚本）都能 `POST /v1/chat` 跟她说话，也能读 `/v1/board` 里的全部状态。

## 4. 数据流

**一次面板对话**：

```text
panel/console.html
  → POST /v1/chat  {text, source:"panel", user_id:0}
    → routes.chat: channel.set_channel("panel")
    → uid=0 → is_lover=True
    → loop.chat_turn(chat_type="")
      → 回忆(h2.retrieve) → 关系读数(engine) → 组 prompt → LLM
      → 写 events / 写记忆(h2.add) / 结算关系(apply_appraisal)
    → {reply:[...], state:{...}, meta:{...}}
  → 前端另拉 GET /v1/board 刷新关系/作息/记忆
```

**一次 QQ 消息（OneBot 通道）**：

```text
NapCat --HTTP上报--> POST /onebot/event
  → channel.set_channel("onebot")
  → 开关/token/self_id/ignore_ids 过滤
  → 群聊前置：静默 → 待机 → 改名 → 语境
  → 搜视频意图短路（直接回链接，落 outbox 兜底）
  → 不懂就查：先发"我想一想。"，取 prompt_hint
  → igate.prepare → loop.chat_turn → igate.finish
  → 段拼装（at/text/image/record）→ _post_api(api_base)
  → 失败：data/onebot_outbox.jsonl
  → 多气泡补发（can_split=True 时）
  → 新人招呼
```

**一次官方 bot 消息（旁路，不经过本层路由）**：`botpy` WS 事件 → `adapters/qq.py` → `intake.precheck` → `loop.chat_turn` → `message.reply`。它借用本层符号只有一处：`adapters/intake.py` 里 `from app.api import trend` 后调 `trend.match_search_intent`；发送、鉴权、状态全在适配器自己手里。

## 5. 改这一层要注意

1. **加路由必须同时想清楚前缀归属**：`routes.py`/`onebot.py` 是无前缀（路径里自写 `/v1/...`），`console.py`/`trend.py`/`group.py` 有前缀。写错一处就是双前缀 `/v1/console/v1/console/...`。
2. **console 的 42 个端点不写 `Depends`**：口令只在 router 构造时挂一次。新加 router 想复用必须自己 `dependencies=[Depends(_console_auth)]`，`_console_auth` 不是 `app` 级中间件。
3. **路径顺序即匹配顺序**：`/intimate/exemplars` 必须在 `/intimate/{user_id}` 前注册（否则 `exemplars` 被当成 `int` 解析，422）。`/friends/requests` 与 `/friends/{user_id}`、`/v1/dev/archives` 与 `/v1/dev/archives/{name}` 目前靠"段数不同"天然不冲突，但新增"具体路径 + 参数路径"共存时必须把具体的写前面。
4. **加字段不要只改响应**：`/v1/health` 的 `channel` 段是 `channel.as_dict()` 直出，改 `channel.Caps` 就会改这个接口；`/v1/board` 的 `relation` 段是 `engine.stage_payload` 的展开，改阶段表结构会同时打到面板、桌宠、`/v1/persona`。
5. **开发者端点都要 `_dev_ok(password)`**：漏一个就是"无口令可清库"。已有例外（`POST /v1/relation`、`POST /v1/session/new`）只查开关，别再扩大。
6. **`channel.set_channel()` 的位置不能乱挪**：它决定同一个进程里"当前话是谁说的"。`/v1/chat` 开头、`/onebot/event` 开头、`qq.start_background` 开头各一次，其余地方不许写。
7. **新增入站前置逻辑要进 `adapters/intake.py`**，不要只写在 `onebot.py` 里——否则官方通道行为会不一致（这是已经踩过的坑，见 docs/13）。

## 6. 交叉引用

- 相关：docs/13（`main.py` 如何 `include_router`、startup 起了哪几个后台任务）
- 相关：docs/04（`loop.chat_turn` 的入参与 `meta` 字段）、docs/06（`h2` 的 `retrieve`/`pin`/`forget_all`）、docs/08（`engine` 的三轴与阶段）
- 相关：docs/14（面板页面怎么调这 42 个 console 端点）、docs/11（群机制的静默/待机/主动）

## 附：2026-11-01 新增的三个操作（声音独立 API）

| 方法 | 路径 | 干什么 |
|---|---|---|
| `GET` | `/v1/console/tts` | 声音服务状态：`available` / `reason` / 缓存文件数 / 本分钟已用额度 / worker 数 |
| `POST` | `/v1/console/tts` | 合成一段语音（不走聊天主链），body `{text, voice?, style?}`，返回 `{ok, path, bytes, cached, url}` |
| `GET` | `/v1/console/tts/file?path=…` | 取 WAV 给面板播放；**只允许 `data/voices/` 下的文件**（路径逃逸返回 403） |

为什么要单开：合成是慢请求 + 会排队，不能挤在聊天主链上。这三个操作走 `app/voice` 的**专属线程池**
（`voice.workers`，默认 2），与聊天模型、与其它 `to_thread` 调用互不抢资源；同一句话命中缓存直接复用。
