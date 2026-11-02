# 07 · 记忆引擎内部（vendor/symvolve_memory）

> 覆盖：`vendor/symvolve_memory/`（35 个 `.py` / 12465 行 + `db/migrations/` 下 2 个 `.sql`，共 7 张表）｜基于 2026-11-01 的代码状态｜上游：docs/06（`app/memory/h2.py` 的写入与召回语义）、`app/core/memengine.py`｜下游：docs/08｜表结构另见 docs/17｜**§7 为 2026-11-02 新增：闭源分发（打包模式 / 加密容器 / 机器绑定 / 接口层）**

## 1. 这一层干什么

这是第三方记忆引擎 Symvolve Memory（包版 `__version__ = "0.3.0"`）的整目录副本，模型无关、多用户、纯 SQLite 存储。它把「一句话」变成一条可衰减、可召回、可解释、可审计的记忆行，并在下一次对话里把相关的行按分数捞回来、压成一段能直接塞进 prompt 的文本。

它对外只暴露四个门面：`Store`（存储层，`db/repo.py`，2428 行，全部 SQL 都在这个文件里）、`MemoryLifecycle`（生命周期：打分入库、惰性衰减、加温、软删、取代、冷合并）、`MemoryRecall`（线索召回与打分）、`MemoryRenderer`（温度分层渲染 + token 预算 + 渲染缓存）。上层 `app/core/memengine.py` 只做三件事：把 `vendor/` 插进 `sys.path`、按配置打开**同一个** `data/life.db`、缓存这四个门面的单例；`app/memory/h2.py` 是业务侧唯一写入口。

它解决的核心问题有三个，全都写在代码的模块 docstring 里并能在实现里逐条对上：一、`mem_event` 存原文且 append-only，永不进 prompt，prompt 只吃 `mem_item.text`；二、热度和清晰度用「惰性计算」实现，读的时候拿 `created_at` / `last_seen` / `meta['heat_anchor']` 与当前时钟算，算完写回，**没有定时任务**；三、每次召回都落 `mem_recall_log`，并给出逐条理由（`MatchRule`），事后能复盘「这条记忆凭什么被想起来」。

它明确不做的事：不生成 embedding（`mem_embedding` 只存调用方传进来的向量，`quant`/`render` 层都不调模型）、不调大模型、不做网络请求（`tools/http_server.py` 与 `tools/mcp_server.py` 是可选的独立入口，本项目不启用）、不做依赖管理之外的任何第三方依赖（全库只 import 标准库 + 自身）。

零依赖在代码里是可验证的：`db/repo.py` 只 import `hashlib / json / re / sqlite3 / struct / threading / pathlib / typing`；`recall.py` 只多一个 `math`；`render.py` 只多一个 `math`。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `vendor/symvolve_memory/__init__.py` | 163 | 包入口：声明 `__version__ = "0.3.0"`，一次性 re-export 全部公开符号并写进 `__all__`（66 个名字） |
| `vendor/symvolve_memory/capabilities.py` | 57 | 能力开关 `Capabilities`：`full()` / `lite()` 两套预设，门禁模型写入、多步检索、向量召回、渲染预算、召回条数上限 |
| `vendor/symvolve_memory/clock.py` | 42 | 可注入时钟：`Clock` 协议、`SystemClock`、`FixedClock`（衰减与召回因此可测，不用真等 30 天） |
| `vendor/symvolve_memory/config.py` | 66 | 进程级 `Settings`（9 个字段）+ `SYMM_*` 环境变量覆盖 + 默认库路径 |
| `vendor/symvolve_memory/errors.py` | 26 | 6 个异常类的层级：`MemoryError` → `ValidationError` / `NotFound` / `StoreError`(→`MigrationError`) / `CapabilityError` |
| `vendor/symvolve_memory/models.py` | 285 | 三个 dataclass（`MemoryEvent` / `Cue` / `MemoryItem`）+ 7 个 `StrEnum` + SQLite 行映射（`from_row` / `to_row`） |
| `vendor/symvolve_memory/decay.py` | 225 | 热度与清晰度的惰性衰减、遗忘判定、冷合并准入判定，`DecayPolicy` 集中全部阈值 |
| `vendor/symvolve_memory/scoring.py` | 248 | 纯规则重要度打分：`score_importance()` 加 8 条规则、情绪词表、`RelationStage` |
| `vendor/symvolve_memory/lifecycle.py` | 465 | 把存储原语拼成行为：打分入库 `draft_item`、惰性刷新 `refresh`、加温 `mark_seen`/`mark_recalled`、软删、取代、冷合并、`tick()` |
| `vendor/symvolve_memory/entities.py` | 143 | 标识符独立通道：NFKC + 大小写折叠 + 分隔符压平后的「紧键」精确匹配 |
| `vendor/symvolve_memory/recall.py` | 964 | 召回引擎：切词、候选预过滤、逐项打分、状态判定、向量兜底、审计落库、命中加温 |
| `vendor/symvolve_memory/render.py` | 397 | 温度分层（hot/warm/cold）渲染 + token 预算硬保证 + `mem_render_cache` 写穿缓存 |
| `vendor/symvolve_memory/timeline.py` | 807 | 只读时间线视图：按实体链还原一条结论的建立/取代/合并/遗忘全过程 |
| `vendor/symvolve_memory/stability.py` | 666 | 七段离线稳定性自检（归档往返/崩溃恢复/并发/幂等/时点一致/长跑/归档护栏），不进运行时热路径 |
| `vendor/symvolve_memory/db/__init__.py` | 29 | 存储层 re-export：`Store`、`connect`、`checkpoint`、迁移相关符号 |
| `vendor/symvolve_memory/db/connection.py` | 66 | `connect()`：建父目录、开 WAL、设 pragma、自动跑迁移；`checkpoint()` 收 WAL |
| `vendor/symvolve_memory/db/migrate.py` | 204 | 迁移发现/checksum 校验/执行/记账（`schema_migrations`） |
| `vendor/symvolve_memory/db/repo.py` | 2428 | `Store` 仓库：1 个类 + 21 个模块级函数 + 71 个方法（共 93 个 `def`/`class`），全库唯一写 SQL 的地方 |
| `vendor/symvolve_memory/db/migrations/0001_init.sql` | 92 | schema v1：`mem_event` / `mem_item` / `mem_cue` / `mem_recall_log` / `mem_render_cache` + 12 个索引 |
| `vendor/symvolve_memory/db/migrations/0002_embedding.sql` | 13 | schema v2：`mem_embedding`（float32 BLOB）+ 2 个索引 |
| `vendor/symvolve_memory/quant/__init__.py` | 125 | 量化域 re-export（名词表、标的、6 种记录、映射、预留端口） |
| `vendor/symvolve_memory/quant/vocabulary.py` | 253 | 量化名词表：`Venue`/`InstrumentType`/`Side`/`Action`/`Timeframe`/`Mode`/`OrderType`/`RiskScope`/`QuantRecordType` + 归一与校验 |
| `vendor/symvolve_memory/quant/instruments.py` | 167 | 标的规范形 `venue:symbol[:type]`，含 MT5 券商后缀归一到基代码 |
| `vendor/symvolve_memory/quant/records.py` | 684 | 六种记录结构（`Thesis`/`Signal`/`PositionSnapshot`/`OrderIntent`/`Fill`/`RiskLimit`）+ `meta['quant']` 信封 |
| `vendor/symvolve_memory/quant/mapping.py` | 339 | 记录 → `MemoryItem` 的唯一落库口径（`kind` / `meta` / `mem_cue` 三处挂载） |
| `vendor/symvolve_memory/quant/ports.py` | 150 | 三个预留 Protocol（行情/执行/账户），只定义不实现 |
| `vendor/symvolve_memory/companion/__init__.py` | 54 | 陪伴层 re-export |
| `vendor/symvolve_memory/companion/emotion.py` | 116 | 情绪读数 `detect_emotion()`：强度 0..1、极性 -1..1、标签；`resonance()` 共振值 |
| `vendor/symvolve_memory/companion/triggers.py` | 146 | 该不该主动提起旧事：`TriggerPolicy` + `decide_trigger()` → `speak`/`ask`/`silent` |
| `vendor/symvolve_memory/companion/bringup.py` | 139 | 主动提起的冷却台账 `BringUpLedger`（纯内存，不落库） |
| `vendor/symvolve_memory/companion/rumination.py` | 294 | 独处联想：挑「值得想一会儿」的组合成 `ThoughtSeed`，不生成文字 |
| `vendor/symvolve_memory/companion/service.py` | 425 | 陪伴门面 `CompanionMemory`：情绪 → 召回 → 共振重排 → 触发决定 → 渲染片段 |
| `vendor/symvolve_memory/tools/__init__.py` | 18 | 工具层 re-export |
| `vendor/symvolve_memory/tools/service.py` | 962 | `MemoryService` + 11 个 `ToolSpec` + 异常→错误码/HTTP 状态映射；HTTP/MCP/CLI 共用 |
| `vendor/symvolve_memory/tools/http_server.py` | 412 | 5 条 JSON 路由的 `ThreadingHTTPServer`，非回环绑定强制 token |
| `vendor/symvolve_memory/tools/mcp_server.py` | 214 | stdio JSON-RPC MCP server（一行一条） |
| `vendor/symvolve_memory/tools/cli.py` | 686 | `symm` 命令行入口，退出码 0/1/2 |

## 3. 模块逐一详解

### 3.1 `__init__.py`（163 行）

**职责**：这个文件是**契约**，不是逻辑。它把内部散在 12 个模块（`capabilities`、`clock`、`config`、`entities`、`decay`、`lifecycle`、`scoring`、`recall`、`render`、`timeline`、`errors`、`models`）里的东西摊平成一层命名空间，并用 `__all__` 显式声明哪些名字属于公开 API——`__all__` 一共 66 个名字。

**对外符号**（`__all__` 全量，按文件顺序）：`__version__`、`Capabilities`、`Clock`、`FixedClock`、`SystemClock`、`Settings`、`MemoryError`、`ValidationError`、`NotFound`、`StoreError`、`MigrationError`、`CapabilityError`、`ChatType`、`Clarity`、`Cue`、`CueKind`、`Kind`、`MemoryEvent`、`MemoryItem`、`RecallStatus`、`Role`、`Scope`、`Temperature`、`clamp_heat`、`clamp_importance`、`days_between`、`now_ts`、`DecayPolicy`、`clarity_for_age`、`decayed_clarity`、`decayed_heat`、`recalled_heat`、`should_forget`、`MemoryLifecycle`、`MergeReport`、`RefreshReport`、`compress_text`、`ImportanceScore`、`RelationStage`、`ScoreRule`、`score_importance`、`MatchRule`、`MemoryRecall`、`RecallHit`、`RecallResult`、`RecallWeights`、`extract_tokens`、`compact_entity`、`entity_keys`、`entity_overlap`、`is_entity`、`normalize_entity`、`DEFAULT_BUDGET_TOKENS`、`MemoryRenderer`、`RenderLimits`、`RenderResult`、`estimate_tokens`、`query_fingerprint`、`render_line`、`temperature_of`、`MemoryTimeline`、`TimelineChain`、`TimelineEntry`、`TimelineEvent`、`TimelineSegment`、`TimelineView`。

**关键事实**：`Store` **不在这 66 个名字里**。想用存储层必须写 `from symvolve_memory.db.repo import Store`（`app/core/memengine.py:34` 与 `app/memory/h2.py` 的调用方都是这么写的）。这不是遗漏，是分层：包根只暴露「模型 + 规则 + 四个门面里的三个」，`Store` 需要显式走 `db` 子包。

**坑**：`stability` 模块**没有被 re-export**（`__init__.py` 里没有它），要做稳定性自检必须写 `from symvolve_memory.stability import run_suite`。反过来说，`import symvolve_memory` 会连带加载 `recall`（964 行）/ `timeline`（807 行）/ `render`（397 行）三个重模块，冷启动成本固定，不会延迟到第一次召回。

### 3.2 `models.py`（285 行）

**职责**：数据模型 + SQLite 行映射。所有时间戳都是 UTC epoch 秒。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `now_ts` | `now_ts() -> int` | `int(time.time())` |
| `days_between` | `days_between(later: int, earlier: int) -> float` | 天数差，负数夹到 0（`SECONDS_PER_DAY = 86400`） |
| `clamp_importance` | `clamp_importance(value: int) -> int` | 夹到 1..100 |
| `clamp_heat` | `clamp_heat(value: float) -> float` | 夹到 0.0..1.0 |
| `MemoryEvent` | `@dataclass(kw_only=True, slots=True)` | append-only 原文行，字段：`user_id`、`raw_text`、`id`、`chat_type="private"`、`scope="private"`、`role="user"`、`created_at=0`、`meta={}` |
| `Cue` | 同上 | 一条线索：`item_id`、`cue`、`kind="content"`、`weight=1.0`、`id` |
| `MemoryItem` | 同上 | 当前记忆条目，22 个字段（列定义见 3.4 表 2） |

**8 个枚举**（全部 `StrEnum`，落库即字符串）：`ChatType`（private/group/channel/system）、`Scope`（private/group/shared/qq/bili/pet）、`Role`（user/bot/system）、`Kind`（fact/preference/event/promise/conflict/relation/identity/emotion/misc，共 9 个）、`Clarity`（vivid/clear/fuzzy/trace）、`RecallStatus`（hit/uncertain/miss）、`Temperature`（hot/warm/cold）、`CueKind`（content/time/sensory/action/entity）。`CLARITY_ORDER = (vivid, clear, fuzzy, trace)` 是清晰度比较的唯一次序来源。

**`MemoryItem` 的派生属性**：`is_active` = `not forgotten and superseded_by is None and merged_into is None`——这一个布尔量是全库「活跃」的唯一定义，`db/repo.py` 的 `_ACTIVE_ITEM_SQL` 常量与它逐项对应；`age_days(now)` 用 `created_at`，`since_seen_days(now)` 用 `last_seen or created_at`。

**关键实现**：`from_row` 逐个字段做容错——`importance` 为 `None` 时按 8，`last_seen` 为 0 时退回 `created_at`，`meta_json` 用 `_loads` 容错解析（坏 JSON 返回默认值，绝不抛）。`to_row` 反过来在写出前再夹一次 `clamp_importance` / `clamp_heat`，所以 DB 里的 CHECK 约束实际上永远不会被触发。

**坑**：`clamp_heat` 定义在**文件末尾**（第 282 行），而 `MemoryItem.to_row` 在第 264 行就调用它。运行时能跑（模块级函数在调用时才解析），但把 `to_row` 提到模块顶层执行就会 `NameError`；静态检查器也会报未定义。

### 3.3 `clock.py` / `errors.py` / `capabilities.py` / `config.py`

**`clock.py`（42 行）**：`Clock` 是 `runtime_checkable` 的 `Protocol`，只有一个 `now() -> int`。`FixedClock(start=1700000000, step=0)` 每次 `now()` 返回当前值后**自动前进一步**（`step=0` 时视为不动），另有 `advance(seconds)` 与 `set(epoch)` 手动拨。这个「读一次就前进」的语义是 `stability.py` 与 `lifecycle` 测试能复现的前提——同一个 `Store` 里连续两次 `insert_item` 会拿到不同的 `created_at`。

**`errors.py`（26 行）**：`MemoryError` 为根；`ValidationError`（调用方给了非法值）、`NotFound`、`StoreError`（数据库/归档/迁移失败）、`MigrationError(StoreError)`、`CapabilityError`。`tools/service.py` 把这张表原样映射成 JSON-RPC code 与 HTTP status（见 3.17）。

**`capabilities.py`（57 行）**：`full()` 的默认值就是字段默认值：`model_write=True`、`multi_step_search=True`、`vector_recall=False`、`render_budget=True`、`max_recall_results=3`；`lite()` 把 `model_write`/`multi_step_search`/`render_budget` 全关、`max_recall_results=2`。`from_dict` 先按 `name` 选预设再 `replace` 覆盖已知字段（未知键直接丢弃，不报错）。**注意 `vector_recall` 的两个预设都是 `False`**——向量召回必须由调用方显式打开。

**`config.py`（66 行）**：`Settings` 是 `frozen` dataclass，9 个字段：`db_path`、`archive_dir`、`http_host="127.0.0.1"`、`http_port=8765`、`http_token=None`、`default_budget_tokens=1200`、`busy_timeout_ms=5000`、`log_level="INFO"`、`capabilities=Capabilities.full()`。`from_env()` 认 `SYMM_` 前缀：`SYMM_CAPABILITIES`（`lite` 走 lite 预设，其余一律 full）、`SYMM_DB_PATH`、`SYMM_ARCHIVE_DIR`、`SYMM_HTTP_HOST`、`SYMM_HTTP_PORT`、`SYMM_HTTP_TOKEN`（空串归一成 `None`）、`SYMM_BUDGET_TOKENS`、`SYMM_BUSY_TIMEOUT_MS`、`SYMM_LOG_LEVEL`。默认库路径优先 `%LOCALAPPDATA%\SymvolveMemory\memory.db`，否则 `./var/memory.db`。**本项目完全不走这条路**：`app/core/memengine.py` 直接 `Store(config.get().db_file)`，`Settings` 只在 `tools/cli.py` 与 `tools/http_server.py` 里用。

### 3.4 `db/migrations/*.sql` + `db/migrate.py`（92 + 13 + 204 行）

这一节回答「引擎自己建哪些表、迁移怎么记账」。

**表 1 · `mem_event`（append-only 原文）**

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT | |
| `user_id` | TEXT | NOT NULL | 分区第一层 |
| `chat_type` | TEXT | NOT NULL DEFAULT 'private' | 分区第二层 |
| `scope` | TEXT | NOT NULL DEFAULT 'private' | 分区第三层 |
| `role` | TEXT | NOT NULL DEFAULT 'user' | user/bot/system |
| `raw_text` | TEXT | NOT NULL | 原文，**永不进 prompt** |
| `meta_json` | TEXT | NOT NULL DEFAULT '{}' | JSON 文本 |
| `created_at` | INTEGER | NOT NULL | epoch 秒 |

索引 3 个：`idx_event_user_time(user_id, created_at)`、`idx_event_chat_time(user_id, chat_type, created_at)`、`idx_event_scope_time(user_id, scope, created_at)`。**没有任何 UPDATE/DELETE 路径**——`Store` 只提供 `append_event`，这份表在代码里是只增不改的。

**表 2 · `mem_item`（记忆本体，22 列）**

| 列 | 类型 | 约束 |
|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT |
| `user_id` / `chat_type` / `scope` | TEXT | NOT NULL（后两者 DEFAULT 'private'） |
| `kind` | TEXT | NOT NULL DEFAULT 'fact' |
| `title` | TEXT | NOT NULL DEFAULT '' |
| `text` | TEXT | NOT NULL（prompt 唯一来源） |
| `bot_reply` | TEXT | 可空（她当时的回话） |
| `importance` | INTEGER | NOT NULL DEFAULT 8，`CHECK (importance BETWEEN 1 AND 100)` |
| `clarity` | TEXT | NOT NULL DEFAULT 'clear'，`CHECK (clarity IN ('vivid','clear','fuzzy','trace'))` |
| `heat` | REAL | NOT NULL DEFAULT 0.0，`CHECK (heat >= 0.0 AND heat <= 1.0)` |
| `pinned` | INTEGER | NOT NULL DEFAULT 0，`CHECK (pinned IN (0,1))` |
| `source_event_ids` | TEXT | NOT NULL DEFAULT '[]'（JSON 数字数组） |
| `superseded_by` / `superseded_at` | INTEGER | 外键 → `mem_item(id)` / epoch 秒 |
| `merged_into` | INTEGER | 外键 → `mem_item(id)` |
| `forgotten` | INTEGER | NOT NULL DEFAULT 0，`CHECK (forgotten IN (0,1))` |
| `created_at` / `last_seen` | INTEGER | NOT NULL |
| `last_recall` | INTEGER | 可空（从没被召回就是 NULL） |
| `recall_count` | INTEGER | NOT NULL DEFAULT 0 |
| `meta_json` | TEXT | NOT NULL DEFAULT '{}' |

索引 6 个：`idx_item_user_time(user_id, created_at DESC)`、`idx_item_user_chat(user_id, chat_type, created_at DESC)`、`idx_item_importance(user_id, importance DESC)`、`idx_item_pinned_heat(user_id, pinned DESC, heat DESC)`、`idx_item_kind(user_id, kind)`、`idx_item_clarity(user_id, clarity)`。

**表 3 · `mem_cue`（线索）**：`id` PK、`item_id` NOT NULL 外键 → `mem_item(id)` **ON DELETE CASCADE**、`cue` TEXT NOT NULL、`kind` TEXT DEFAULT 'content'、`weight` REAL DEFAULT 1.0，并有 `UNIQUE (item_id, cue)`。索引 2 个：`idx_cue_item(item_id)`、`idx_cue_value(cue)`。第二个索引是为召回的点查准备的——`recall_candidate_ids` 里有一句 `INDEXED BY idx_cue_value` 硬提示。

**表 4 · `mem_recall_log`（召回审计）**：`id` PK、`user_id`、`chat_type` DEFAULT 'private'、`query_text` DEFAULT ''、`cues_json` DEFAULT '[]'、`status` TEXT `CHECK (status IN ('hit','uncertain','miss'))` DEFAULT 'miss'、`confidence` REAL DEFAULT 0.0、`item_ids` DEFAULT '[]'、`created_at` NOT NULL。索引 1 个：`idx_recall_user_time(user_id, created_at DESC)`。

**表 5 · `mem_render_cache`（渲染缓存）**：`id` PK、`user_id`、`chat_type`、`scenario`、`query_hash`、`text`、`tokens` INTEGER DEFAULT 0、`created_at`、`expires_at` 可空，`UNIQUE (user_id, chat_type, scenario, query_hash)`。**没有额外索引**（唯一键本身就是索引），也没有过期清理的定时任务——过期只是「读时假装不存在」（见 3.11）。

**表 6 · `mem_embedding`（0002 引入）**：`id` PK、`item_id` NOT NULL 外键 ON DELETE CASCADE、`model` TEXT NOT NULL、`dim` INTEGER NOT NULL `CHECK (dim > 0)`、`vector` BLOB NOT NULL、`created_at`，`UNIQUE (item_id, model)`。索引 `idx_embedding_model(model)`、`idx_embedding_item(item_id)`。文件头注释写明取向：嵌入由调用方生成，本库不调模型，只按条目存一份。

**表 7 · `schema_migrations`（迁移记账）**：由 `db/migrate.py` 的 `_SCHEMA_DDL` 建，不在 `.sql` 文件里：`version INTEGER PRIMARY KEY`、`name TEXT NOT NULL`、`checksum TEXT NOT NULL`、`applied_at INTEGER NOT NULL`。

**迁移怎么跑**：

1. `available()` 扫 `db/migrations/`，用正则 `^(?P<version>\d{4})_(?P<name>[A-Za-z0-9_]+)\.sql$` 匹配文件名，重复版本号直接 `MigrationError`（`duplicate migration version`），最后按 `version` 升序返回 `Migration` 列表。
2. `_ensure_schema_table()` 建记账表（`IF NOT EXISTS` + commit）。
3. `applied()` 读出已记账的 `{version: {name, checksum, applied_at}}`；表不存在时返回空列表。
4. **一致性闸门**：先验「库里记的版本是否都还有对应文件」——有未知版本就 `MigrationError(database contains unknown migration versions: ...)`，直接拒绝启动（防止降级运行）。
5. `checksum()` 用 `path.read_bytes()` 的 sha256（读不到文件时退回 `sql.encode("utf-8")` 的 sha256）；已记账的版本 checksum 与当前文件不一致就 `MigrationError(checksum mismatch for migration ...)`。
6. `_run()` 用 `con.executescript(sql)` 执行，紧接着 `INSERT INTO schema_migrations`，再 `commit()`；任何 `sqlite3.Error` 先 `rollback()` 再抛 `MigrationError(migration {version} ({name}) failed: ...)`。
7. `apply(con, *, clock=None, target=None, migrations_dir=None) -> list[int]` 返回「本次真正应用了哪些版本」；`target` 是上限（`record.version > target` 就 break），负数报错。
8. `current_version(con) -> int` 取已记账的最大版本；`status(con)` 返回 `{current, applied, available, pending}` 四段快照。

**真实 checksum 值（本仓库当前字节）**：`0001_init.sql` 4151 字节，SHA256 = `b264a8757e610114c9f0e9dc39cd604d0f4a15276ec6055145808e8d993498a1`；`0002_embedding.sql` SHA256 = `112a34a4a8e2b5c68cb4b846972e20bb51bca88cc58b6caad0028161fa239103`。

**这条 checksum 闸门在本项目里真的触发过一次**，证据在 `app/core/memengine.py:43-55`：2026-10-31 上游把 `0001_init.sql` 的行尾从 CRLF 归一成 LF，字节数 4243 → 4151，SQL 内容逐字一致但 SHA256 变了，老库的 `schema_migrations` 记的是旧值，引擎启动就会 `MigrationError`。本项目因此写了一个只改账本、不碰表结构的 `heal_legacy_migration_checksums()`，对照表 `_KNOWN_CHECKSUM_REWRITES[1] = {"b5496e7e…ed113": "b264a875…498a1"}`，且只在「旧值 == 已知旧版 **且** 当前文件 hash == 已知新版」时才动手。

### 3.5 `db/connection.py`（66 行）

**职责**：唯一开库入口。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `connect` | `connect(path: str \| Path, *, clock: Clock \| None = None, busy_timeout_ms: int = 5000, apply_migrations: bool = True) -> sqlite3.Connection` | 开库并配置 pragma |
| `checkpoint` | `checkpoint(con: sqlite3.Connection, mode: str = "TRUNCATE") -> None` | WAL checkpoint |
| `MEMORY_PATH` | `":memory:"` | 内存库标记 |

**关键实现**：

1. 先判内存库（`":memory:"` 或以 `file::memory:` 开头）。
2. 文件库先 `db_path.parent.mkdir(parents=True, exist_ok=True)`，失败抛 `StoreError`。
3. `sqlite3.connect(timeout=busy_timeout_ms/1000.0, check_same_thread=False)`，`row_factory = sqlite3.Row`。
4. 三个 pragma：`busy_timeout`、`foreign_keys = ON`（外键级联删除靠它生效）；文件库再加 `journal_mode = WAL` 与 `synchronous = NORMAL`。
5. `apply_migrations=True` 时立刻 `migrate.apply(con, clock=clock)`。

`busy_timeout_ms` 负数抛 `ValidationError`；`checkpoint` 的 `mode` 只接受 `PASSIVE/FULL/RESTART/TRUNCATE` 四个关键词（大小写不敏感），其余抛 `ValidationError`。

**为什么 `check_same_thread=False`**：`Store` 用一个 `threading.RLock` 串行化所有读写，连接本身跨线程共享；SQLite 默认的同线程检查会直接炸掉，所以显式关掉，安全性由那把锁而不是连接来保证。

### 3.6 `db/repo.py`（2428 行，1 个类 + 21 个模块级函数 + 71 个方法）

**职责**：全库唯一写 SQL 的地方。`Store` 一个类，包住一个连接 + 一把 `RLock`。

**构造与生命周期**：

| 符号 | 签名 |
|---|---|
| `Store.__init__` | `Store(path: str \| Path, *, clock: Clock \| None = None, busy_timeout_ms: int = 5000, apply_migrations: bool = True) -> None` |
| 属性 | `path -> str`、`clock -> Clock`、`connection -> sqlite3.Connection` |
| 上下文 | `__enter__() -> "Store"`、`__exit__(...) -> bool`、`close() -> None` |

**内部写锁**：`_fetch` / `_scalar` / `_write` 三个私有方法都在 `with self._lock:` 里执行。`_write` 遇 `sqlite3.IntegrityError` 时 `rollback()` 并翻译成 `ValidationError("value rejected by schema constraints: ...")`——所以上层看到的「值被 schema 拒了」永远是 `ValidationError`，不是裸 `sqlite3` 异常。

**事件（原文）接口**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `append_event` | `append_event(self, event: MemoryEvent) -> int` | 写一行原文；`created_at` 为 0 时用注入时钟补齐；回填 `event.id` |
| `get_event` | `get_event(self, event_id: int) -> MemoryEvent \| None` | 按 id 取 |
| `list_events` | `list_events(self, *, user_id=None, chat_type=None, scope=None, since=None, until=None, limit=None, offset=0, ascending=True) -> list[MemoryEvent]` | 过滤 + 分页，`ascending` 决定 `ORDER BY created_at ASC, id ASC` 还是 DESC |
| `iter_events` | `iter_events(...) -> Iterator[MemoryEvent]` | 本质是 `iter(list_events(...))`——**不是游标**，会一次性全取 |
| `count_events` | `count_events(self, *, user_id=None, chat_type=None, scope=None, since=None, until=None) -> int` | 同套过滤条件计数 |

**条目接口**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `insert_item` | `insert_item(self, item: MemoryItem) -> int` | 插入；`created_at`/`last_seen` 为 0 时用当前时钟补；回填 id |
| `update_item` | `update_item(self, item: MemoryItem) -> None` | **全列重写** 21 列；`rowcount == 0` 抛 `NotFound` |
| `get_item` | `get_item(self, item_id: int) -> MemoryItem \| None` | |
| `list_items` | `list_items(self, *, user_id=None, chat_type=None, scope=None, kind=None, kinds=None, pinned=None, clarity=None, include_forgotten=False, include_superseded=False, include_merged=False, limit=None, offset=0, order_by="importance") -> list[MemoryItem]` | 默认只回活跃行 |
| `count_items` | `count_items(self, *, user_id=None, chat_type=None, scope=None, kind=None, kinds=None, pinned=None, clarity=None, include_forgotten=False, include_superseded=False, include_merged=False) -> int` | |
| `has_at_least_items` | `has_at_least_items(self, count: int, *, user_id=None, chat_type=None, scope=None, kinds=None) -> bool` | 用 `LIMIT 1 OFFSET count-1` 探测，不数全量 |
| `items_by_ids` | `items_by_ids(self, item_ids: Sequence[int], *, user_id=None, chat_type=None, scope=None, kinds=None, include_superseded=False, include_merged=False) -> list[MemoryItem]` | 批量按 id 取，排序固定 `importance DESC, id DESC` |

`order_by` 只认 5 个键（`ORDER_BY_COLUMNS`）：`importance`（默认）、`created_at`、`last_seen`、`heat`、`recall_count`，每个都自带 `, id DESC` 兜底；传别的值抛 `ValidationError`。`kind` 与 `kinds` 同时给也抛 `ValidationError`。

**线索接口**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `add_cue` | `add_cue(self, cue: Cue) -> int` | `ON CONFLICT (item_id, cue) DO NOTHING`；重复时回查已有 id（不抛错） |
| `add_cues` | `add_cues(self, item_id: int, cues: Iterable[Any]) -> int` | 逐条调 `add_cue`，返回**待写入条数**（含被去重的） |
| `list_cues` | `list_cues(self, item_id: int) -> list[Cue]` | 按 id 升序 |
| `cues_map` | `cues_map(self, item_ids: Sequence[int]) -> dict[int, list[Cue]]` | 一次 `IN` 查询回来，避免 N+1 |
| `replace_cues` | `replace_cues(self, item_id: int, cues: Iterable[Any]) -> int` | 先 `DELETE` 再逐条 `add_cue`（**两次独立事务**，见坑） |
| `list_cues_in` | `list_cues_in(self, *, user_id=None, chat_type=None, scope=None) -> list[Cue]` | 整个分区活跃条目的全部线索，召回旧路径靠它一次取回 |

`_cue_objects()` 是这三处共用的归一器：接受 `Cue` 实例、裸字符串、以及 1~3 元 tuple `(cue)` / `(cue, kind)` / `(cue, kind, weight)`；传 `str`/`bytes` 直接拒绝（`cues must be an iterable of cue values`）。

**召回审计与溯源**：`log_recall(self, *, user_id: str, chat_type: str = "private", query_text: str = "", cues: Iterable[Any] = (), status: str = "miss", confidence: float = 0.0, item_ids: Iterable[int] = (), created_at: int | None = None) -> int`——`status` 必须在 `{'hit','uncertain','miss'}` 里，`cues` 与 `item_ids` 都序列化成紧凑 JSON。另有 `list_recalls(*, user_id=None, chat_type=None, limit=50, offset=0)`、`recall_history(item_id, *, user_id=None, limit=20)`（**在 Python 侧做精确包含判断**，因为 `item_ids` 是 JSON 文本，用 SQL `LIKE` 会让 `1` 匹配到 `10`/`21`）、`explain(item_id, *, recall_limit=20)`（条目 + 来源事件 + 线索 + 召回记录四段证据链）、`deletion_proof(item_id, *, now=None)`（对规范化 JSON 算 sha256 的删除证明）。

**时点一致性**：`items_as_of(state_at, *, user_id=None, chat_type=None, scope=None, limit=None, offset=0)` 与 `item_ids_as_of(state_at, *, ...) -> set[int]`。三条口径：`created_at <= T`；`superseded_at` 有值且 `<= T` 才算当时已不成立；`merged_into` 有值且 `meta['merged_at'] <= T` 才算当时已被并走。**遗忘不看时点**——`forgotten` 一律按当前状态排除（软删不记时点，代码注释明写了这条边界）。

**保留与删除**：`purge_expired(*, retention_days: int, user_id=None, chat_type=None, scope=None, now=None, dry_run: bool = True) -> dict`。口径用 `last_seen < cutoff`（不是 `created_at`）——被反复用到的记忆不该因为「建立得早」被删；默认 `dry_run=True`，只报数不动手；返回 `{scanned, expired, forgotten, dry_run, retention_days, cutoff}`。

**召回候选（三个 SQL 通道）**：`recall_candidate_ids(*, user_id=None, chat_type=None, scope=None, kinds=None, needles: Sequence[str] = (), query_text: str = "", entity_keys: Sequence[str] = (), substring_chars: int = 8, include_inactive: bool = False) -> set[int]`，并集来自：

1. **线索包含**：`instr(c.cue, ?) > 0` 对每个 needle 一次；若还给了 `query_text`，再加一条 `(length(c.cue) > ? AND instr(?, c.cue) > 0)`，让长线索走整串包含。
2. **线索被查询包含**：把查询串切成 ≤ `substring_chars`（默认 8）的短子串，拿 `c.cue IN (...)` 走 `idx_cue_value` 等值点查（用 `INDEXED BY idx_cue_value` 硬钉执行计划）。
3. **实体通道**：`_entity_candidate_ids()` 两道筛——首字符必要条件（`_width_variants` 生成 ASCII/全角写法）先砍行，再对活下来的行做 `_compact_sql`（`lower()` + 逐个 `replace()` 压掉半角/全角分隔符）后要求每个字母/数字连续段都出现。

`expansion_candidate_ids(*, ..., cue_values: Sequence[str] = (), include_inactive: bool = False) -> set[int]` 是第二轮联想的候选：线索值精确落在 `cue_values` 里的活跃条目。注释写明**必须先走 `idx_cue_value` 再验分区**（用相关 `EXISTS` 而不是 `IN` 子查询），否则「先取十万级分区 id 再逐条探线索」实测慢 30 倍。

两个隔离片段 `_partition_in()`（`IN (SELECT id FROM mem_item ...)`）与 `_partition_exists()`（相关 `EXISTS`）就是上面这个取舍的实现，注释里分别写了为什么用哪个。

**渲染缓存**：`save_render(*, user_id, scenario, query_hash, text, chat_type="private", tokens=0, expires_at=None, created_at=None) -> int` 是 upsert，唯一键冲突时只更新 `text`/`tokens`/`created_at`/`expires_at` 四列；`get_render(*, user_id, scenario, query_hash, chat_type="private", now=None)` 在 `expires_at <= now` 时返回 `None`（**过期判定是闭区间**）；还有 `list_renders(...)`、`purge_renders(*, user_id=None, expired_only=True, now=None) -> int`、`count_renders(*, include_expired=True) -> int`。

**向量**：`put_embedding(item_id, model, vector, *, created_at=None) -> int`（先 `get_item` 确认条目存在，不存在抛 `NotFound`；`(item_id, model)` upsert）、`get_embedding(item_id, model=None)`（省略 model 取 `created_at` 最新的那份）、`embeddings_map(item_ids, model=None)`、`embeddings_in(*, user_id=None, chat_type=None, scope=None, model=None)`（JOIN `mem_item` 只取活跃行）、`delete_embedding(item_id, model=None)`、`count_embeddings()`、`embedding_stats(*, user_id=None)`。

**向量的字节格式**：`_VECTOR_FORMAT = "<{0}f"`，`_encode_vector()` 用 `struct.pack` 存 float32 小端 BLOB 并返回 `(bytes, dim)`；`_decode_vector()` 在 `len(raw) != dim * 4` 时**返回空列表**而不是抛错——脏向量按「没有」处理。注释给了容量依据：384 维 1536 字节，比 JSON 文本省一半。

**归档**：`dump() -> dict` 返回 `{format: "symvolve-memory-archive", version: 1, exported_at, schema_version, events, items, cues, recall_logs, embeddings}`——**不含 `mem_render_cache`**，渲染缓存是派生物，不进归档。`load(data, *, mode="merge")` 的 `mode` 只认 `merge`/`replace`；`replace` 先把 `superseded_by`/`merged_into` 置 NULL 再按 `mem_embedding → mem_cue → mem_item → mem_recall_log → mem_event` 顺序清空（顺序是为了让外键不炸）；归档的 `schema_version` **高于**当前库版本时直接拒收（`archive schema_version N is newer than this build`），宁可失败也不用旧列映射猜新字段。`export_file(path, *, indent=2) -> Path` 与 `import_file(path, *, mode="merge") -> dict` 是它的文件壳。

**统计**：`stats()` 回 12 个字段：`events`、`items`、`items_active`、`cues`、`recall_logs`、`render_cache`、`embeddings`、`distinct_users`、`earliest_event_at`、`latest_event_at`、`schema_version`、`db_bytes`（内存库恒 0）。`app/core/memengine.py:stats()` 只从中取 8 个（丢掉 `render_cache`/`embeddings`/`earliest_event_at`/`latest_event_at` 四个）。

**坑**：

- `connection` 属性直接把裸 `sqlite3.Connection` 交出去，docstring 自己写着 `write access is not guarded`——绕过 `Store` 的方法直接在这条连接上 `INSERT` 不受 `RLock` 保护。
- `replace_cues()` 是「先 DELETE 提交，再逐条 add」两次独立事务，中间崩了会留下一条没有任何线索的记忆。
- `add_cues()` 返回的是**待写入条数**而不是真实新增条数（重复线索被 `DO NOTHING` 吃掉也不减数）。
- `iter_events()` 名不副实：它把整个结果集取成 list 再 `iter()`，大库上等于全量反序列化。
- `update_item()` 是 21 列全写，读-改-写之间任何并发修改都会被覆盖（靠 `RLock` 保证同进程内不并发，跨进程无保护）。
- `items_as_of()` 在 `merged_into is not None` 且 `meta` 里**没有** `merged_at` 时也把条目排除，等价于「合并时间不可知就当已合并」。

### 3.7 `decay.py`（225 行）

**职责**：热度和清晰度的惰性衰减、遗忘与冷合并的准入判定。模块 docstring 列了四条不变量：清晰度只会往下走；`pinned` 全豁免；高关系阶段最低只到 `fuzzy`；heat 衰减以 anchor 为基准因而幂等。

**常量（真实值）**：

| 常量 | 值 | 含义 |
|---|---|---|
| `HEAT_RECALL_DELTA` | `0.28` | 每次召回命中的加温量 |
| `HEAT_DECAY_PER_DAY` | `0.04` | 每天线性回落的量；从 1.0 起算 25 天归零 |

**`DecayPolicy` 全部默认值**：`vivid_days=7.0`、`clear_days=30.0`、`fuzzy_days=60.0`、`forget_after_days=60.0`、`heat_recall_delta=0.28`、`heat_decay_per_day=0.04`、`forget_heat_max=0.10`、`forget_importance_max=40`、`high_relation_min_importance=60`、`merge_heat_max=0.15`、`merge_importance_max=45`、`merge_min_age_days=60.0`。`__post_init__` 强制 `vivid_days < clear_days < fuzzy_days`，否则 `ValueError`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `clarity_for_age` | `clarity_for_age(days: float, policy: DecayPolicy \| None = None) -> str` | `≤7` vivid、`≤30` clear、`≤60` fuzzy、其余 trace |
| `clarity_rank` | `clarity_rank(clarity: str) -> int` | 0 最清楚；非法值 `ValueError` |
| `is_sharper` | `is_sharper(left: str, right: str) -> bool` | `rank(left) < rank(right)` |
| `relation_stage_of` | `relation_stage_of(item: MemoryItem) -> RelationStage` | 读 `meta['relation_stage']`，缺省陌生 |
| `is_high_relation` | `is_high_relation(item: MemoryItem, policy=None) -> bool` | 阶段 `>= INTIMATE` 或 `importance >= 60` |
| `decayed_clarity` | `decayed_clarity(item, now: int, *, policy=None, relation_stage=None) -> str` | 只降不升 |
| `heat_anchor` | `heat_anchor(item: MemoryItem) -> int` | `meta['heat_anchor']`，否则 `max(last_seen, created_at, last_recall)` |
| `heat_after_days` | `heat_after_days(heat: float, days: float, *, policy=None) -> float` | `clamp_heat(heat - 0.04 * days)` |
| `decayed_heat` | `decayed_heat(item, now: int, *, policy=None) -> float` | pinned 或 heat≤0 直接返回原值；否则按 anchor 到 now 的天数结算 |
| `recalled_heat` | `recalled_heat(heat: float, *, policy=None) -> float` | `clamp_heat(heat + 0.28)` |
| `should_forget` | `should_forget(item, now: int, *, policy=None, clarity: str \| None = None) -> bool` | 遗忘判定 |
| `is_merge_candidate` | `is_merge_candidate(item, now: int, *, policy=None, clarity=None) -> bool` | 冷合并准入 |
| `age_in_days` | `age_in_days(now: int, stamp: int) -> float` | `days_between` 的薄壳 |

**`should_forget()` 的六道闸**（任一命中就 `False`）：`pinned` / 已 `forgotten` / 非 `is_active` → 不遗忘；`is_high_relation()` → 不遗忘；`age_days(now) < 60` → 不遗忘；有效清晰度不是 `trace` → 不遗忘；`heat > 0.10` → 不遗忘；最后只剩 `importance < 40` 这一条放行。

**`is_merge_candidate()` 的七道闸**：`pinned`/`forgotten`/非活跃 → 否；`meta['merged_summary']` 已存在（本身就是摘要）→ 否；高关系 → 否；`age < 60 天` → 否；有效清晰度不是 `trace` → 否；`heat > 0.15` → 否；最后 `importance <= 45` 才准入。注意遗忘用 `< 40`、合并用 `<= 45`——合并的门槛刻意更宽。

**坑**：`decayed_heat()` 对 `heat <= 0.0` 直接返回 `clamp_heat(item.heat)`，即 **0.0 永远不会变成负数也不会被 anchor 影响**；`heat_anchor()` 在 `meta['heat_anchor']` 不是正整数时静默退回 `max(...)`——JSON 里存成 float 的 anchor 会被忽略。

### 3.8 `scoring.py`（248 行）

**职责**：纯规则重要度打分。docstring 写死了取向：不打分批给 8 分、不用模型、不用向量，同样输入永远得到同样整数，明细全落进 `meta_json["importance_breakdown"]`。

**常量（真实值）**：`IMPORTANCE_BASE = 8`、`SELF_PRIVATE_BONUS = 42`、`SELF_GROUP_BONUS = 32`、`EMOTION_MAX = 24`、`FIRST_TIME_BONUS = 30`、`PROMISE_BONUS = 16`、`CONFLICT_BONUS = 14`、`ABOUT_SELF_BONUS = 5`、`TOO_SHORT_PENALTY = -12`、`MIN_EFFECTIVE_CHARS = 6`、`EMOTION_SATURATION = 2.4`、`HIGH_RELATION_MIN_IMPORTANCE = 60`；`RELATION_BONUS = {0: 0, 1: 10, 2: 20, 3: 30}`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `RelationStage` | `IntEnum`：`STRANGER=0` / `FAMILIAR=1` / `CLOSE=2` / `INTIMATE=3` | `from_value()` 接受枚举、整数、英文名、中文别名（陌生/熟悉/亲近/家人/恋人） |
| `ScoreRule` | `@dataclass(frozen=True, slots=True)`：`name`、`delta: int`、`reason` | 一条理由，`as_dict()` → `{rule, delta, reason}` |
| `ImportanceScore` | `total`、`raw_total`、`breakdown`、`pinned=False` | `.clamped` 判是否被夹过；`.as_meta()` 产出 meta 四段 |
| `effective_length` | `effective_length(text: str) -> int` | 去掉空白与标点（`_PUNCTUATION` 集合）后的字数 |
| `emotion_score` | `emotion_score(text: str) -> float` | 情绪词加权和，词表 `EMOTION_LEXICON` 共 41 条（中文 33 + 英文 8） |
| `emotion_bonus` | `emotion_bonus(text: str) -> int` | `min(1, sum/2.4) * 24` 四舍五入 |
| `importance_floor` | `importance_floor(relation_stage: RelationStage) -> int` | `INTIMATE` 及以上回 60，否则 1 |
| `score_importance` | `score_importance(text: str, *, chat_type: str = "private", speaker_is_owner: bool = True, relation_stage: RelationStage \| int \| str = RelationStage.STRANGER, kind: str = "fact", first_interaction: bool = False, min_chars: int = 6) -> ImportanceScore` | 主入口 |
| `merge_breakdown` | `merge_breakdown(existing, extra: ScoreRule) -> list[dict]` | 人工补分后仍保留明细 |

**9 条规则的触发条件**（照 `score_importance` 的执行顺序）：

1. `base`：永远 +8。
2. `self_private`：`speaker_is_owner` 且 `chat_type == "private"` → +42；`self_group`：`speaker_is_owner` 且 `chat_type in ("group","channel")` → +32。**只要 `speaker_is_owner=True`，私聊与群聊必中其一**，所以一条普通发言的起点实际是 50 或 40 分。
3. `relation_stage`：按 `RELATION_BONUS[stage]` 加 0/10/20/30。
4. `emotion`：`emotion_bonus() > 0` 就加（0~24）。
5. `first_time`：`first_interaction` 为真，或正文命中 `FIRST_TIME_PATTERN`（第一次|首次|头一回|头一次|初次|刚开始|生日|纪念日|周年|忌日|约定）→ +30。
6. `promise`：`kind == "promise"` 或命中 `PROMISE_PATTERN`（答应|保证|说好|约好|约定|承诺|一定|下次一起|改天一起|发誓|必去）→ +16。
7. `conflict`：`kind == "conflict"` 或命中 `CONFLICT_PATTERN`（对不起|抱歉|道歉|和好|原谅|误会|吵架|冷战|争执|闹别扭|翻脸|不理我）→ +14。
8. `about_self`：命中 `ABOUT_SELF_PATTERN`（`我(?:喜欢|讨厌|习惯|最近|觉得|想|要|在|是|不|会|刚|终于|以前|小时候|平时|一直)` 或 `我的[^\s，。！？,.!?]{1,6}`）→ +5。
9. `too_short`：`effective_length(body) < min_chars` → -12。

最后 `raw_total = sum(delta)`；`total = max(importance_floor(stage), clamp_importance(raw_total))` 再夹一次。`pinned` 字段就是 `first_interaction` 的布尔值——**首次交互自动钉住**，这是一个容易忽略的副作用。

**坑**：`EMOTION_LEXICON` 里同时有 `"心疼"` 与 `"心疼你"`，正文出现「心疼你」时两次 `count()` 都会命中（"心疼"出现一次、"心疼你"出现一次），加权和是 `0.9 + 0.9 = 1.8` 而不是 0.9——这是词表重叠带来的双计，没有去重逻辑。

### 3.9 `lifecycle.py`（465 行）

**职责**：把存储原语拼成「会老、会淡、会被合并、会被新事实推翻」的行为，不改 schema。所有写回都经 `Store.update_item()`，因此同样受 `RLock` 保护。

**常量**：`COMPRESS_MAX_CHARS = 160`、`TITLE_MAX_CHARS = 24`、`_SENTENCE_END = [。！？!?；;\n]`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `compress_text` | `compress_text(text: str, *, max_chars: int = 160) -> str` | 并空白、去重句、按「；，,」边界截断并补 `…` |
| `RefreshReport` | `scanned`、`clarity_changed`、`heat_changed`、`forgotten` | 一次惰性刷新的结果 |
| `MergeReport` | `groups`、`merged`、`summaries` | 一次冷合并的结果 |
| `MemoryLifecycle.__init__` | `MemoryLifecycle(store: "Store", *, policy: DecayPolicy \| None = None, relation_stage: RelationStage \| int \| str = RelationStage.STRANGER)` | |
| `score` | `score(self, text: str, *, chat_type="private", kind="fact", relation_stage=None, speaker_is_owner=True, first_interaction=False) -> ImportanceScore` | 转发给 `score_importance` |
| `draft_item` | `draft_item(self, *, user_id: str, text: str, title: str = "", chat_type="private", scope="private", kind="fact", relation_stage=None, speaker_is_owner=True, first_interaction=False, source_event_ids: Sequence[int] = (), bot_reply: str \| None = None) -> MemoryItem` | 算好重要度与 pinned，**不入库** |
| `create_item` | `create_item(self, **kwargs: Any) -> int` | `draft_item` + `insert_item` |
| `refresh` | `refresh(self, *, user_id=None, chat_type=None, scope=None, forget: bool = True) -> RefreshReport` | 扫一批活跃条目推进清晰度、结算 heat、按需软删 |
| `mark_seen` | `mark_seen(self, item_id: int, *, at: int \| None = None) -> MemoryItem` | 被再次提及：结算 heat、`last_seen=now`、清晰度回 `vivid`、anchor 前移 |
| `mark_recalled` | `mark_recalled(self, item_id: int, *, at: int \| None = None) -> MemoryItem` | 被召回命中：`heat + 0.28`、`recall_count + 1`、`last_recall=now`、anchor 前移 |
| `forget` | `forget(self, item_id: int, *, reason: str = "manual") -> MemoryItem` | 软删，写 `meta['forget_reason']` 与 `meta['forgotten_at']` |
| `supersede` | `supersede(self, old_id: int, new_id: int, *, at: int \| None = None) -> MemoryItem` | 置 `superseded_by`/`superseded_at`，跨用户抛 `ValidationError` |
| `clear_supersede` | `clear_supersede(self, old_id: int) -> MemoryItem` | 撤销取代 |
| `merge_cold` | `merge_cold(self, *, user_id=None, chat_type=None, scope=None, kind=None) -> MergeReport` | 冷记忆合成摘要 |
| `tick` | `tick(self, *, user_id=None, chat_type=None, scope=None, merge: bool = True) -> dict[str, Any]` | 一次完整惰性推进 |
| `refresh_all` / `clarity_window` / `compress_many` | 见源码 447/458/463 行 | 三个便捷壳 |

**`draft_item` 的派生字段**（这是理解「哪些字段是算出来的」的关键）：`title` 截到 `TITLE_MAX_CHARS=24`；`importance` 来自 `score_importance`；`clarity` 固定 `vivid`；`pinned` 来自 `result.pinned`（= `first_interaction`）；`meta` 写入三段：`relation_stage`（整数）、`importance_raw`（未夹的原始分）、`importance_breakdown`（逐条明细）。**注意它不写 `meta['heat_anchor']`**，所以新条目第一次被 `refresh` 结算时 anchor 会退回 `max(last_seen, created_at)`。

**`refresh()` 的四步**：对每条活跃条目 1) `decayed_clarity()` 与当前对比，变了就写；2) `decayed_heat()` 与当前差 `> 1e-9` 就写；3) 若 `not pinned and heat > 0` 就把 `meta['heat_anchor'] = now`（注释：heat 归零后 anchor 无意义，保留旧值少写一行）；4) `forget=True` 时 `should_forget()` 命中就置 `forgotten=True`。任何一步 dirty 就 `update_item()` 一次。

**`merge_cold()` 的分组键固定为 `(user_id, chat_type, scope, kind)`**，绝不跨用户/会话/场景合并；桶内少于 2 条跳过；摘要条目 `clarity='trace'`、`importance = max(被并条目)`、`meta` 里写 `merged_summary: True` 与 `merged_from: [id...]`、`heat_anchor`；线索用 `replace_cues` 继承（同一线索值取最大 weight）。

**`tick()` 的顺序**（注释里写明「顺序是有讲究的」）：先 `refresh(forget=False)` → 再 `merge_cold()`（若 `merge=True`）→ 最后 `refresh(forget=True)`。理由：先合并才轮到遗忘，否则冷条目在合并前就被软删除，`merged_into` 永远拿不到摘要。返回 7 个字段，`scanned` 取两次 refresh 的较大值。

### 3.10 `recall.py`（964 行）

**职责**：召回 = 按线索反查条目 + 可解释打分 + 落审计。模块 docstring 的四段说明（切词、`list_cues_in` 一次取回、加权打分、写审计 + 加温）与实现一一对应。

**常量**：`MIN_GRAM = 2`、`MAX_GRAM = 4`、`MAX_TOKENS = 64`、`CLARITY_POINTS = {vivid: 15.0, clear: 10.0, fuzzy: 4.0, trace: 0.0}`。

**`RecallWeights` 的全部权重与阈值（真实值）**：

| 字段 | 值 | 用途 |
|---|---|---|
| `cue_exact` | `34.0` | 线索精确命中 |
| `cue_partial` | `18.0` | 线索包含命中 |
| `cue_cap` | `60.0` | 线索分封顶 |
| `importance` | `30.0` | 重要度权重（`importance/100 * 30`） |
| `heat` | `20.0` | 热度权重（`heat * 20`） |
| `pinned` | `25.0` | 钉住加分 |
| `recency` | `12.0` | 新鲜度上限 |
| `recency_days` | `30.0` | 新鲜度衰减窗 |
| `co_cue` | `12.0` | 第二轮共线索，单条理由上限 |
| `vector` | `30.0` | 向量相似度权重 |
| `vector_min_similarity` | `0.35` | 向量兜底的最低相似度 |
| `entity` | `40.0` | 每个实体命中 |
| `hit_threshold` | `55.0` | 判 `hit` 的分数线 |
| `max_cue_weight` | `2.0` | 线索权重上限 |

`__post_init__` 会校验：`cue_exact/cue_partial > 0`、`0 < hit_threshold <= 100`、`recency_days > 0`、`max_cue_weight > 0`、`vector >= 0`、`0 <= vector_min_similarity <= 1`、`entity > 0`，任何一条不满足抛 `ValueError`。

**打分函数逐项拆解**——第一轮 `_score_item()`（返回 `None` 表示这条不进候选）：

- **精确命中**（`cue.cue in token_set`）：`value = cue_exact * weight`，`weight = min(2.0, max(0.0, cue.weight))`，即单条线索最多 `34 * 2 = 68` 分；置 `exact_hit = True`。
- **包含命中**：条件 `cue.cue in query_text or any(needle in cue.cue for needle in needles)`，`value = cue_partial * weight`，单条最多 36 分。
- **线索累加与封顶**：所有线索的 `best` 累加进 `cue_score`；`if cue_score > cue_cap + entity * 2`（即 > 140）时把超出部分扣掉并记一条 `cue_cap` 负分理由。
- **实体命中**：把线索值 + `title` + `text` 一起过 `entity_keys()`，与查询实体集求交，`delta = min(len(entity_hits), 2) * 40`（最多 80 分），并强制 `exact_hit = True`。
- **都不命中就淘汰**：`if not matched and not entity_hits: return None`。
- **属性分**（`_property_score()`）：`importance/100 * 30` + `clamp(heat,0,1) * 20` + `CLARITY_POINTS[clarity]`（vivid 15 / clear 10 / fuzzy 4 / trace 0）+ `pinned ? 25 : 0` + `max(0, 1 - age/30) * 12`，其中 `age = (now - (last_seen or created_at)) / 86400`。
- 最终 `score = round(cue_score + property_score, 3)`，`breakdown` 是「线索/实体理由 + 属性理由」的顺序元组，`matched_cues` 是命中的线索值元组。

**第二轮 `_expand()`**：`seeds = 分数 >= hit_threshold(55)` 的条目；对 seed 的每条线索记 `shared[cue] = max(ratio)`，`ratio = min(1, score/100)`；对尚未打分的条目，每命中一条共享线索 `extra += 12 * ratio`，累计后 `extra = min(extra, 24.0)`，分数 = 属性分 + extra。设计意图写在 docstring：第二轮进来的条目「天然低于第一轮，只做补充、不抢主位」。

**第四路向量 `_apply_vector()`**：只有 `caps.vector_recall == True` 且拿到查询向量（显式 `query_vector` > `embedder` 回调）才走。逐条算余弦 `_cosine()`；`similarity < 0.35` 且该条目**还不在 scored 里**就跳过；`delta = max(0, similarity) * 30`；已存在的条目加分并追加一条 `vector` 理由，新条目则以「属性分 + delta」进场。

**状态与置信度 `_judge()`**：`ranked` 为空 → `("miss", 0.0)`；否则 `confidence = round(min(1, max(0, top/100)), 4)`；`top >= 55` **或** `ranked[0].exact` → `hit`；`top > 0 且 tokens 非空` → `uncertain`；否则 `("miss", 0.0)`。

**`search()` 的完整流程**（`search(self, *, user_id: str, query: str = "", chat_type: str | None = "private", scope: str | None = None, limit: int | None = None, kinds: Sequence[str] | None = None, log: bool = True, bump_heat: bool = True, multi_step: bool | None = None, query_vector: Sequence[float] | None = None, as_of: int | None = None, now: int | None = None) -> RecallResult`）：

1. 校验 `user_id` 非空，定 `moment`（`now` 或注入时钟）。
2. `_QueryContext.build(query)`：切词 → `token_set` → 探测集 `needles` → `query_text = " ".join(tokens)` → `entities = entity_keys(query)`。
3. `wanted = resolve_limit(limit)`：`caps` 为 None 时用调用方的 `limit`（缺省 5）；有 `caps` 时取 `min(limit, caps.max_recall_results)`。
4. `as_of` 给了就先 `item_ids_as_of()` 拿允许集。
5. `_prefilter_active()` 三门槛（`threshold is None` 关闭；`len(tokens) > 48` 退回全扫；`len(needles) > 96` 退回全扫；`threshold <= 0` 总开；否则 `has_at_least_items(512)`）。
6. 预过滤开：`_collect_candidates()` 只取候选条目 + 线索（向量开着时把有向量的条目并进候选），与 `as_of` 集合求交；关：`list_items()` 全量 + `list_cues_in()` 整分区线索。
7. 逐条 `_score_item()`，命中者进 `scored`。
8. 有查询向量就 `_apply_vector()`。
9. `tokens 非空 且 _should_expand() 且 scored 非空` 时跑第二轮：预过滤路径用 `expansion_candidate_ids()` 只捞共线索条目，非预过滤路径直接复用全量 `items`；有新增就把 `steps` 记成 2。
10. 排序 `(-score, -importance, item_id)` 后截断到 `wanted`。
11. `_judge()` 出 `status`/`confidence`。
12. `log=True` 就写一条 `mem_recall_log`（`cues=tokens`、`item_ids=ranked` 的 id）。
13. `bump_heat=True` **且 `status == "hit"`** 才对每条 ranked 调 `mark_recalled()`——注意 `uncertain` 不加温。
14. 返回 `RecallResult`，`scanned` 是进入打分的条目数，`steps` 是 1 或 2。

**`PrefilterPolicy` 的真实默认值**：`threshold=512`、`max_tokens=48`、`scan_terms=96`、`substring_chars=8`。`extract_tokens()` 默认 `min_gram=2`、`max_gram=4`、`limit=64`。

**切词规则**：先把查询 `strip().lower()`；`_ASCII_WORD = [a-z0-9_]+` 按整词产出；`_CJK_RUN = [\u4e00-\u9fff]+` 先产出整串，再产出 2~4 字滑窗；去重保序，最多 64 个。

**`_containment_needles()` 的等价性论证**（注释原文大意）：中文 3~4 字 token 若整体是线索子串，它的 2-gram 也一定是线索子串，而 2-gram 本身又是 token，所以只需探测每个 token 的 2-gram；ASCII token 按整词探测；token 被截断（达到 `MAX_TOKENS`）时退回逐 token 探测，保证「探测集命中」与「逐 token 判断」严格等价。

**坑**：

- `_score_item` 里的包含命中条件写的是 `if tokens and (not best or self._weights.cue_partial > self._weights.cue_exact)`。因为 `18.0 > 34.0` 恒为假，所以「精确命中之后还能被包含命中顶掉分数」这条路**永远不会走到**；实际语义是「只有没精确命中时才算包含命中」。代码注释描述的是设计意图，实现比它窄。
- `_expansion_candidates()` 第一行 `del context`——参数是为了和其它候选方法签名一致而保留的死变量。
- `search()` 的 `chat_type` 默认是 `"private"`，而 h2 写进来的面板记忆 `chat_type` 是空串 `""`，两者严格相等过滤不会互相匹配。这正是 h2 要用「最多 4 个分区 + 去重合并」补旧语义的原因。
- `_judge()` 在 `tokens` 为空但实体命中时，若分数不到 55 会落到 `miss`——尽管 `exact=True` 时已被前一个分支拦下，实际不冲突，但两个条件写在不同分支里，读代码时容易以为 `exact` 优先。

### 3.11 `render.py`（397 行）

**职责**：把召回命中压成一段能进 prompt 的文本，预算硬保证，写穿缓存。三条铁律写在 docstring：只读条目不读原文、预算优先、缓存可丢。

**常量（真实值）**：`DEFAULT_BUDGET_TOKENS = 1200`、`DEFAULT_SCENARIO = "日常对话"`、`DEFAULT_HEADER = "【记忆】"`、`RENDER_VERSION = 1`（改渲染格式就 +1，旧缓存自然失效）。

**`RenderLimits` 默认值**：`budget_tokens=1200`、`max_lines=12`、`warm_chars=40`、`cold_chars=16`、`header="【记忆】"`。

**温度分层 `temperature_of(item, *, hot_heat=0.5, warm_heat=0.12)`**：

| 温度 | 判定 | 渲染 |
|---|---|---|
| `hot` | `pinned` 或 `heat >= 0.5` 或清晰度 `vivid` | `- 标题：完整正文` |
| `warm` | `heat >= 0.12` 或清晰度 `clear` | `- 标题：正文首段（≤40 字）` |
| `cold` | 其余（`fuzzy` / `trace`） | 只有 `- 标题`，无标题时取正文前 16 字 |

这套 0.5 / 0.12 两个阈值与 `app/memory/h2.py` 的 `HOT_THRESHOLD = 0.50`、`COLD_THRESHOLD = 0.12` 是**同一个数值的两份定义**（引擎一份、上层一份），改一边不会自动同步另一边。

**`estimate_tokens()`**：CJK（`0x4E00..0x9FFF`）一字一 token，其余四字符一 token（`ceil(other/4)`）。注释写明「不追求跟具体分词器对齐，只要求同一份文本稳定、可比较、不会低估太多」。

**`query_fingerprint()`**：把 `v{RENDER_VERSION}|chat_type|scope|scenario|query|budget|limit|as_of` 用 `|` 拼起来，取 sha256 前 **32 位**十六进制。`as_of` 参与指纹，所以时点渲染不会串缓存。

**`render()` 流程**：`resolve_budget()`（`caps.render_budget == False` 时返回 `None` 表示不裁剪；显式 `budget_tokens` ≤ 0 抛 `ValueError`）→ 算指纹 → `use_cache=True` 就 `get_render()`，命中直接返回 `RenderResult(cached=True, status="hit")` → 否则用传入的 `recall_result` 或自己调 `search()` → `_compose()` 拼行 → `estimate_tokens()` → `save_cache=True 且 text 非空` 就 `save_render()`（`expires_at = moment + ttl`，`cache_ttl_seconds` 为 `None` 时 `expires_at=None` 即永不过期）。

**`_compose()` 的预算硬保证**：逐条命中，先看 `len(lines) >= max_lines(12)` 就停手并标 `truncated`；预算为 `None` 直接加；否则**每一行都用整段重新估一次** `estimate_tokens(self._text_of(lines + [line]))`，放得下才收；放不下就二分找这一行能截多长（`probe = line[:middle].rstrip(" ，,、：:") + "…"`），截出来还有内容就收下并 `break`。注释解释了为什么每行重估：「token 估计带取整，单行分数相加会累积误差，所以每一行都用整段重新估一次，这样『返回的 tokens 不超过预算』是硬保证，不是近似」。

**`invalidate(*, user_id=None) -> int`**：`purge_renders(expired_only=False)`，默认整片清。

**坑**：

- 缓存命中时返回的 `RenderResult` 里 `item_ids=()`、`temperatures=()`、`confidence=0.0`，`status` 硬编码 `"hit"`——**命中缓存拿不到「这段文本用了哪几条记忆」**，只想要 id 的调用方必须绕过缓存。
- `render()` 里 `use_cache` 与 `save_cache` 是两个独立开关，可以「不读缓存但写缓存」或反之。
- `_first_segment()` 只在标点位置 `0 < cut < limit` 时按句子切，标点正好在第 0 位（正文以「。」开头）时走不到切分分支。

### 3.12 `entities.py`（143 行）

**职责**：标识符（代码/工单号/标的/版本号）的独立精确匹配通道。docstring 点明动机：「工业场景里最要命的召回错误不是漏，而是把 A 的结论算到 B 头上」，`AAPL` 与 `AAPL.US`、`JIRA-1234` 与 `JIRA1234`、全角 `ＡＢＣ` 与 `abc` 都该归一，而 `sql` 与 `sqlite` 是两回事。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `is_entity` | `is_entity(token: str, *, min_length: int = 2, max_word_length: int = 5) -> bool` | 像不像唯一标识 |
| `normalize_entity` | `normalize_entity(text: str) -> str` | NFKC + 小写 + 分隔符统一成单个 `-` |
| `compact_entity` | `compact_entity(text: str) -> str` | 压掉一切非字母数字，得「紧键」 |
| `entity_keys` | `entity_keys(text: str, *, min_length: int = 2, max_word_length: int = 5) -> set[str]` | 从文本切出紧键集合 |
| `entity_overlap` | `entity_overlap(query: str, candidates: Iterable[str], *, min_length: int = 2) -> set[str]` | 查询与一组候选的共有紧键 |

**`is_entity()` 的四条判据**（满足其一）：含数字；含连接符（`_SEPARATORS = "-_.:/# "`）；`raw.isupper()`；紧键长度 ≤ `max_word_length=5` 且不在 `_EN_STOPWORDS`（69 个英文功能词，从 `about` 到 `your`）里。

**`entity_keys()` 产出的三类键**：1) 每个被接受的标识符的紧键（`JIRA-1234 → jira1234`）；2) **相邻拼接**——两个标识符之间只隔 ≤2 个分隔符/空白时额外产出拼起来的紧键（`jira 4821 → jira4821`）；3) **后缀别名**——`AAPL.US` 同时产出 `aaplus` 与 `aapl`（后缀限定 2~4 位纯字母，`v2.3.1` 这种数字后缀不生成别名）。

`_ENTITY = [A-Za-z0-9][A-Za-z0-9\-_.:/#]*[A-Za-z0-9]|[A-Za-z0-9]`——**中文不进来**，注释说明「中文实体走线索匹配，硬当标识符只会把『牙齿复查』切成碎块」。

**耦合**：`recall.py` 用 `entity_keys()` 生成查询侧实体集并在打分里给 40 分/个；`db/repo.py::_entity_candidate_ids()` 用同一套语义在 SQL 里做候选超集（但只有 `lower()` + `replace()`，**没有 NFKC**，注释明确承认「不是全等，最终判定仍在 Python 侧」）；`timeline.py` 用 `compact_entity`/`entity_keys` 分组。

### 3.13 `timeline.py`（807 行）

**职责**：只读时间线视图，回答「某个实体在一段时间里，结论被建立、被更正、被合并、被遗忘的全过程」。docstring 的四条口径：纯只读（不写 `mem_recall_log`、不加温、不写渲染缓存、不碰 `mem_event`）；不新增 schema；与 `items_as_of` 对拍；不重算召回（实体归属只看线索与标识符切分）。

**常量**：`TIMELINE_DEFAULT_LIMIT = 500`、`TIMELINE_DEFAULT_TEXT_CHARS = 200`、`TIMELINE_MAX_SEGMENTS = 1000`；状态 `active`/`superseded`/`merged`/`forgotten`（判定顺序固定：遗忘 > 取代 > 合并 > 活跃）；事件 `created`/`superseded`/`merged`/`forgotten`（`_EVENT_ORDER` 0..3）。

**主入口**：`MemoryTimeline(store).view(*, user_id: str, chat_type: str | None = "private", scope: str | None = None, entities: Sequence[str] | None = None, since: int | None = None, until: int | None = None, as_of: int | None = None, bucket: int | None = None, kinds: Sequence[str] | None = None, include_forgotten: bool = False, limit: int | None = 500, text_chars: int | None = 200) -> TimelineView`。

**三种视图语义**（都写进了 docstring 的表）：都不给 → 分区内全部条目的完整演变；给 `since`/`until` → 只留生命周期与窗口**相交**的条目（窗口前建立、窗口内仍有效的老结论必须在）；给 `as_of` → 快照，`entries` 只保留该时刻有效的版本，`events` 给到该时刻为止的完整故事。

**参数校验**（真实存在）：`since > until` 抛 `ValidationError`；`bucket` 必须同时给 `since` 与 `until`，且 `(until-since)//bucket + 1 > 1000` 时报「use a coarser bucket」；`limit=0` 表示不限；`text_chars=0` 表示全文；`chat_type=""` 与 recall 口径一致视为「不限会话类型」。

**数据处理**：`list_items(include_forgotten=True, include_superseded=True, include_merged=True, order_by="created_at")` 全取 → `cues_map()` 一次取线索 → `_record()` 逐条转 `_Record` → 实体过滤 → 遗忘过滤 → `_overlaps()` 窗口过滤 → `as_of` 时再 `_valid_at()` 过滤 → 按 `(start, item_id)` 升序 → `limit` 截断 → `_chains()` 算版本号 → `_entry()` / `_events()` / `_segments()` 组装。窗口上界 `window_hi = min(until, as_of)`（两者都给时取更紧的）。

一条条目可以属于多条实体链（挂了两个 `kind='entity'` 线索时两条链里都出现），`entries` 里按 item 去重一次。`entity_key_set()`/`canonical_entity_key()` 保证一条 `AAPL.US` 不会同时冒出 `aaplus` 与 `aapl` 两条链。

### 3.14 `stability.py`（666 行）

**职责**：离线自检工具，**不进运行时热路径**。七段固定名（`SECTIONS`）：`archive_roundtrip`、`crash_recovery`、`concurrency`、`idempotency`、`time_travel`、`soak`、`archive_guard`。

**常量**：`DEFAULT_SEED = 20261030`、`BASE = 1700000000`、`DAY = 86400`；`SIZES[quick=True]` = `{archive_items: 120, crash_items: 100, threads: 4, per_thread: 20, travel_items: 80, travel_points: 60, soak_ops: 150}`；`SIZES[quick=False]` = `{1000, 500, 8, 100, 500, 200, 2000}`。

**对外符号**：`run_suite(*, quick: bool = True, seed: int = 20261030, only: Iterable[str] | None = None, raise_on_error: bool = False) -> StabilityReport`；七段各自的函数 `archive_roundtrip(*, seed, archive_items, workdir=None)`、`crash_recovery(*, seed, crash_items, workdir=None)`、`concurrency(...)`、`idempotency(*, seed, archive_items, workdir=None)`、`time_travel(...)`、`soak(*, seed, soak_ops, workdir=None)`、`archive_guard(*, seed, workdir=None)`；工具 `integrity_check(store: Store) -> str`、`SectionResult`、`StabilityReport`（含 `.failed` / `.as_dict()` / `.to_json(indent=2)` / `.summary()`）。

报告里带 `environment = {python, sqlite, platform, seed}`，固定种子保证失败可复现，所有库都在临时目录跑完即删。`crash_recovery` 靠子进程写入后 `os._exit()` 再由父进程重开验证 + `PRAGMA integrity_check`——这是唯一需要真起进程的一段。

### 3.15 `quant/`（6 个文件 / 1718 行）

**职责**：让记忆库记得住量化决策，但**只做名词、数据结构、预留接口，不做交易系统**（包 docstring 的「明确不做」清单：不连接交易所、不管理密钥、不下单、不做撮合与保证金、不做回测引擎）。

| 文件 | 关键符号 | 说明 |
|---|---|---|
| `quant/__init__.py` | 50 个 re-export | 量化域统一出口 |
| `quant/vocabulary.py` | `QUANT_META_VERSION = 1`、`Venue`（bybit/mt5/binance/okx/ctp/unknown）、`InstrumentType`（spot/perp/future/option/fx/cfd/equity/unknown）、`Side`（long/short/flat，带 `opposite`）、`Action`（open/add/reduce/close/hold/flatten/hedge）、`Timeframe`、`Mode`、`OrderType`、`RiskScope`、`QuantRecordType`、`coerce()`、`epoch_seconds()`、`finite_number()`、`fraction()`、`label()`、`tags()`、`timeframe_seconds()`、`is_timeframe_aligned()` | 只做词不做事，落库即字符串 |
| `quant/instruments.py` | `InstrumentRef`（`venue:symbol[:type]` 规范形）、`base_symbol()` | 构造即校验；`EURUSD.a`/`XAUUSD#` 归一到基代码 |
| `quant/records.py` | `Thesis`、`Signal`、`PositionSnapshot`、`OrderIntent`、`Fill`、`RiskLimit`、`Candle`、`QuantRecord`、`RECORD_CLASSES`、`record_to_meta()`、`record_from_meta()`、`record_type_of()` | 六种记录，全部 `kw_only + frozen + slots`，序列化走 `meta['quant']` 信封 `{"v": 1, "type": ...}` |
| `quant/mapping.py` | `KIND_BY_TYPE`、`IMPORTANCE_BY_TYPE`、`TYPE_LABELS`、`record_title()`、`record_text()`、`record_cues()`、`to_memory_item()`、`add_cues()`、`item_record()`、`quant_items()`、`is_quant_item()` | **唯一的落库口径** |
| `quant/ports.py` | `MarketDataPort`、`ExecutionPort`、`AccountPort`（三个 `runtime_checkable` Protocol）、`PORTS`、`VENUE_INTEGRATION_PLAN`、`describe_ports()`、`integration_plan()` | 只定义不实现，没有 HTTP 客户端/密钥/签名 |

**`mapping.py` 的三处挂载**（这是本章「写入路径」的第二个落库口径，值得单独记）：`mem_item.meta['quant']` 放记录本体；`mem_item.kind` 按 `KIND_BY_TYPE` 映射（`thesis`/`signal`/`order_intent`/`fill` → `event`，`position`/`risk_limit` → `fact`）；`mem_cue` 挂标的（`kind='entity'`，权重 1.0）+ 策略/方向/动作/类型/标签（`kind='content'`，权重 0.3~0.8）。

**`IMPORTANCE_BY_TYPE` 的真实值**：`risk_limit: 90`、`thesis: 78`、`position: 62`、`signal: 55`、`order_intent: 48`、`fill: 34`；`to_memory_item()` 的缺省值兜底是 50。**这条路径不经过 `score_importance()`**——重要度是查表给的，所以 `meta['quant']` 条目没有 `importance_breakdown`。

**本项目用法**：`app/memory/memv2.py` 不用 quant；`tools/service.py` 用 `KIND_BY_TYPE` 与 `record_from_meta`/`record_to_meta` 做 `symm_get_item` 的量化信封校验。也就是说 vendored 的 `quant` 在本项目里是**未被主链路使用**的一层。

### 3.16 `companion/`（6 个文件 / 1174 行）

**职责**：陪伴向的一层——「什么时候该主动想起、要不要说出口、独处时想什么」。docstring 明确分工：主库管「记得准、查得到、可审计」，这层管「主动想起」，且**不改核心评分**，所有情绪加权都在本层二次排序。

| 文件 | 关键符号 | 说明 |
|---|---|---|
| `companion/__init__.py` | 18 个 re-export | |
| `companion/emotion.py` | `COMPANION_SATURATION = 1.2`、`NEGATIVE_WORDS`、`POSITIVE_WORDS`、`EMOTION_WORDS`、`EmotionSignal`（`intensity` 0..1、`valence` -1..1、`labels`）、`detect_emotion()`、`resonance()` | 复用 `scoring.EMOTION_LEXICON` 的词表，饱和点取评分侧（2.4）的一半 |
| `companion/triggers.py` | `SPEAK="speak"`、`ASK="ask"`、`SILENT="silent"`、`TriggerPolicy`、`TriggerDecision`、`decide_trigger()` | `TriggerPolicy` 默认：`charge_threshold=0.35`、`min_score=30.0`、`strict_confidence=False`、`sensitive_kinds=("conflict",)`、`cooldown_seconds=86400.0`、`cooldown_turns=3` |
| `companion/bringup.py` | `format_remaining()`、`BringUpRecord`、`BringUpLedger`（`turn` 计数 + `entries` 字典） | 冷却台账**纯内存**，不写 `mem_item`；两个窗任一过期即放行（OR 不是 AND） |
| `companion/rumination.py` | `UNFINISHED/ECHO/BRIDGE/DRIFT`、`ThoughtSeed`、`THOUGHT_KIND="thought"`、`THOUGHT_CUE=("念头","thought",0.1)`、`THOUGHT_IMPORTANCE_CAP=20`、`thought_signature()`、`plan_idle_thoughts()`、`ThoughtLedger` | 只挑料不生成文字；同一颗种子 24 小时只写一次 |
| `companion/service.py` | `DEFAULT_RESONANCE_BOOST=20.0`、`EMOTION_CUE_WEIGHT=0.5`、`CompanionTurn`、`CompanionMemory` | 门面：情绪 → 召回 → 共振重排（第五路信号）→ 触发决定 → 渲染片段 |

**共振加分上限 20.0 的设计依据**写在常量注释里：「比线索分低一档，够把『情绪对的那条』顶上来，但压不过精确命中」（精确命中 34，包含 18，共振 20 落在中间）。

**本项目用法**：`app/memory/companion.py` 直接 import `BringUpLedger`、`CompanionMemory`、`ThoughtLedger`、`detect_emotion`、`triggers.ASK`；`app/life/thoughts.py` 用 `detect_emotion` 与 `thought_signature`。也就是说 companion 这一层是**本项目实际在用的**，而 quant 层不是。

### 3.17 `tools/`（5 个文件 / 2292 行）

**职责**：HTTP / MCP / CLI 三个入口共用一层 `MemoryService`，三个壳都只做「解析请求 → 调服务层 → 序列化回去」。本项目**不启用**这三个入口（`app/` 里没有 import `symvolve_memory.tools`）。

**`tools/service.py`（962 行）关键常量**：`MCP_PROTOCOL_VERSION = "2024-11-05"`、`SERVER_NAME = "symvolve_memory"`、`READ_TOOLS = ("symm_search","symm_render","symm_timeline","symm_get_item","symm_stats","symm_embedding_stats")`、`WRITE_TOOLS = ("symm_append","symm_pin","symm_forget","symm_supersede","symm_embed")`、`RESOURCE_URI_TEMPLATE = "symm://users/{user_id}/items/{item_id}"`、`RESOURCE_MIME_TYPE = "application/json"`。

**11 个 `ToolSpec`**（`TOOL_SPECS`，逐条都读过）：`symm_search`（必填 `user_id`）、`symm_append`（必填 `user_id`/`raw_text`，`requires_write=True`）、`symm_pin`（必填 `item_id`）、`symm_forget`（必填 `item_id`）、`symm_supersede`（必填 `old_item_id`/`new_item`）、`symm_render`（必填 `user_id`）、`symm_timeline`（必填 `user_id`）、`symm_get_item`（必填 `item_id`/`user_id`）、`symm_embed`（必填 `user_id`/`model`/`item_ids`/`vectors`）、`symm_embedding_stats`（无必填）、`symm_stats`（无必填）。5 个写工具全部标 `requires_write=True`——这就是 `Capabilities.model_write` 门禁的落点。

**错误映射三张表**：`ERROR_CODES`（`ValidationError` → -32602、`NotFound` → -32001、`CapabilityError` → -32002、`StoreError` → -32003、`MigrationError` → -32004，未知 -32603）、`HTTP_STATUS`（400/404/403/503/500）、`ERROR_NAMES`（`VALIDATION`/`NOT_FOUND`/`CAPABILITY`/`STORE`/`MIGRATION`）。

**`tools/http_server.py`（412 行）**：`ThreadingHTTPServer`，5 条路由 `GET /`、`GET /healthz`、`GET /tools`、`GET /stats`、`POST /tools/<name>`；`HEALTH_PATH = "/healthz"` 不参与凭据校验；非回环绑定必须配 `SYMM_HTTP_TOKEN`，否则拒绝启动，配了之后除 `GET|HEAD /healthz` 外每个请求都要 `Authorization: Bearer <token>`，比较走 `hmac.compare_digest`；码值 `INTERNAL_CODE=-32603`、`INVALID_REQUEST_CODE=-32600`、`NOT_FOUND_CODE=-32601`、`UNAUTHORIZED_CODE=-32005`；`LOG_REQUESTS = False` 默认关日志。

**`tools/cli.py`（686 行）**：`symm` 命令，退出码 0 成功 / 1 `MemoryError` 子类 / 2 argparse 错误；`serve` 与 `mcp` 两个长驻命令先于普通 handler 分发；JSON 模式下成功时 stdout 就是工具返回体本身。

**`tools/mcp_server.py`（214 行）**：stdio 一行一条 JSON-RPC，复用同一份 `ToolSpec` 与错误映射。

## 4. 数据流

### 4.1 写入：一条记忆从 `add()` 进来到落表

上层入口是 `app/memory/h2.py:add(kind, content, importance=0.6, stability=0.5, pin=False, cues=None, source_ids=None, user_id=0, chat_type="", extra_meta=None) -> int`。逐步拆：

1. **清洗**：`content.strip()`，空串直接 `return 0`（不写任何表）。
2. **取门面**：`store = memengine.store()`、`life = memengine.lifecycle()`。
3. **归一三个分区列**：`uid = str(int(user_id or 0))`（引擎 `user_id` 是字符串，旧系统 0 = 共用/她的）；`ct = str(chat_type or "")`（空串 = 不分场合，**引擎 schema 对 `chat_type` 没有 CHECK，可以原样落库**）；`sc = ct or "shared"`。
4. **查重**：`_find_duplicate()` = `store.list_items(user_id=uid, chat_type=ct, kinds=[kind])` 后逐条比 `item.text == content`。命中就走进「加温分支」：`importance = max(旧值, _importance_engine(importance))`、`heat = clamp_heat(max(旧 heat, 0.90) + 0.1)`、`pinned` 取或、`store.update_item()` 写回、`life.mark_seen(existing)`（这一步会再结算一次 heat 并把 `last_seen`/`clarity` 刷新）、有 `cues` 就 `store.add_cues()`，返回旧 id。**这条路径不产生新的 `mem_event` 行**。
5. **写原文**：`store.append_event(MemoryEvent(user_id=uid, chat_type=ct or "private", scope=sc, raw_text=content, meta=meta))` → 一条 `mem_event`；`created_at` 由 `Store` 用注入时钟补齐；`meta` 里放 `{"kind": kind}` + `extra_meta` + `ai_life_source_ids`（旧的 `source_ids` 指向 `events` 表，不能和 `source_event_ids` 混用）。
6. **组装条目**：`MemoryItem(user_id=uid, chat_type=ct, scope=sc, kind=kind, title=_title(content), text=content, importance=_importance_engine(importance), heat=1.0 if pin else 0.90, pinned=bool(pin), source_event_ids=[event_id], meta=dict(meta))`。
7. **落条目**：`store.insert_item(item)` → 一条 `mem_item`；`created_at` 与 `last_seen` 都等于引擎当刻（`last_seen` 由 `insert_item` 从 `created_at` 补）。
8. **挂线索**：`chosen = cues if cues is not None else extract_cues(content)`；`store.add_cues(mid, [(c, _cue_kind(c), 1.0) for c in chosen if c])` → 多行 `mem_cue`，每行 `weight=1.0`。
9. **返回** `mid`。

**这一步写了哪几张表**：`mem_event` 1 行（加温分支不写）、`mem_item` 1 行（加温分支是 UPDATE）、`mem_cue` 0~12 行（`extract_cues` 的 `limit=12`）。**不写** `mem_recall_log`（召回才写）、`mem_render_cache`（渲染才写）、`mem_embedding`（要调用方显式 `put_embedding`）。

**哪些字段是派生计算出来的**：

| 字段 | 谁算的 | 规则 |
|---|---|---|
| `title` | `h2._title()` | 按 `[。！？!?\n]` 切第一句，截 24 字 |
| `importance` | `h2._importance_engine()` | 旧口径 0~1 浮点 × 100 后 `max(1, min(100, round()))`；**这条路径不走 `score_importance()`** |
| `heat` | `h2.add()` | `pin` 时 `1.0`，否则 `INITIAL_HEAT = 0.90` |
| `clarity` | `models.MemoryItem` 默认 | `"clear"`（注意 `lifecycle.draft_item()` 走的是 `"vivid"`，两条入口不一致） |
| `created_at` / `last_seen` | `Store.insert_item()` | 用注入时钟补 0 值 |
| `source_event_ids` | `h2.add()` | `[event_id]`，JSON 序列化 |
| `mem_cue.kind` | `h2._cue_kind()` | 时间正则全匹配 → `time`；命中 `RECALL_WORDS` → `sensory`；含数字 → `entity`；其余 `content` |
| `mem_cue.weight` | `h2.add()` | 恒 `1.0`（引擎侧上限 `max_cue_weight=2.0`） |

另有一条**完全不同的写入口径**是 `MemoryLifecycle.draft_item()`（打分走 `score_importance()`、`clarity=vivid`、`meta` 带 `importance_breakdown`、`pinned=first_interaction`）以及 `MemoryLifecycle.create_item(**kwargs)` 的「打分 + 入库一步到位」。本项目主链路用的是 h2 那条，`draft_item` 只在 `tools/service.py` 的 `symm_append` 与 `quant` 路径里被用到。

### 4.2 召回：从 `retrieve()` 到拿回条目

上层入口 `app/memory/h2.py:retrieve(text, hot_limit=10, user_id=0, chat_type="") -> Recall`：

1. `maybe_tick()`：每小时最多一次 `lifecycle().tick(merge=False)`（`_TICK_KEY = "memory_engine_last_tick"`、`_TICK_INTERVAL_S = 3600`，时间戳存项目自己的 `kv` 表）——**衰减与遗忘就是靠这个惰性 tick 推进的**。
2. `_partitions()` 算出最多 4 个分区：这个人 + 共用（`user_id="0"`），每个再按「`chat_type` 精确」与「`chat_type=""`（不分场合）」展开。
3. **热层**：对每个分区 `store.list_items(user_id, chat_type)` 全取，滤掉 `kind in ("quote","thought")`，留 `pinned` 或 `_heat_of(item, now) >= 0.50` 的，按 `(-pinned, -heat, -importance, -id)` 排序取 `hot_limit` 条。`_heat_of()` 就是 `clamp_heat(_decay.decayed_heat(item, now))`——**热层用的是实时结算后的 heat，不是库里的原值**。
4. **线索层**：每个分区调一次 `recall.search(user_id=..., query=text, chat_type=part_ct, limit=5, log=False, bump_heat=True)`。四件事要注意：`limit=5` 是「多取两条」（后面要滤 quote）；`log=False` 让引擎不写审计（四次分区检索只该产生一条审计）；`bump_heat=True` 意味着**引擎在每次 `hit` 时已经加过温**；`status`/`confidence` 在四次结果间按 `hit > uncertain > miss` 归并。
5. 合并四个分区的 `hits`（同一个 `item_id` 取分数高的那份），滤掉 `quote`/`thought`，按 `(-score, item_id)` 排序。
6. 若最终状态是 `hit` 取前 **2** 条、`uncertain` 取前 **1** 条作为「刚想起来的」，且排除已在热层里的（避免 prompt 里同一句话出现两次）。
7. 最后**只写一条** `store.log_recall(...)`：`cues` 是引擎切出来的 token（截 12），`item_ids` 取「刚想起来的」的 id，为空时退到 `ranked[:3]`，`created_at=now`。

### 4.3 热度在一次对话里的六个变化时刻

| 时机 | 触发点 | 变化 |
|---|---|---|
| 写入 | `h2.add()` 新条目 | `heat = 1.0 if pin else 0.90`（引擎默认是 0.0，由上层显式给） |
| 重复写入 | `h2.add()` 命中查重 | `heat = clamp(max(heat, 0.90) + 0.10)`，随后 `mark_seen()` 再结算一次 |
| 再次提及 | `lifecycle.mark_seen()` | 先 `decayed_heat()` 结算旧账，再写 `last_seen=now`、`clarity=vivid`、`meta['heat_anchor']=now` |
| 召回命中 | `recall.search()` → `mark_recalled()` | `heat = clamp(decayed_heat(heat) + 0.28)`、`recall_count+1`、`last_recall=now`、anchor 前移 |
| 惰性推进 | `lifecycle.refresh()` | `heat = clamp(heat - 0.04 * 距 anchor 天数)`，并把 anchor 前移到 now |
| 读取时 | `h2._heat_of()` / `decayed_heat()` | 只算不写（`refresh` 才写回），所以面板显示的是实时结算值，与 `mem_item.heat` 列里存的「上次结算值」是两个数 |

**重复加温是一个真实存在的风险点**：`h2.retrieve()` 里引擎已经 `bump_heat=True` 加过一次，`h2.touch(ids)` 又会对同一批 id 调 `mark_recalled()`；如果上层在 `retrieve` 之后顺手 `touch` 一次，同一条记忆会被 `+0.56`。代码里没有任何幂等保护（`mark_recalled` 没有「同一秒只加一次」的判断）。

## 5. 边界：上层能碰什么、不能碰什么

### 5.1 对外 API 面 vs 内部实现

**稳定契约（会被 `__all__` 承诺的）**：`MemoryEvent` / `Cue` / `MemoryItem` 三个模型与 8 个枚举；`score_importance()` / `compress_text()` / `clamp_*` / `days_between` 这些纯函数；`DecayPolicy` / `RecallWeights` / `RenderLimits` / `PrefilterPolicy` 四张参数表；`MemoryLifecycle` / `MemoryRecall` / `MemoryRenderer` / `MemoryTimeline` 四个门面；`Store`（要显式从 `symvolve_memory.db.repo` 导入）。

**内部实现（签名不承诺稳定，上游可重排）**：`db/repo.py` 里所有以 `_` 开头的东西（`_item_filters`、`_partition_in`、`_compact_sql`、`_entity_candidate_ids`、`_encode_vector`…）；`recall.py` 的 `_score_item` / `_expand` / `_apply_vector` / `_judge` / `_QueryContext` / `_containment_needles`；`decay.py` 的 `clarity_rank` / `is_sharper`；`lifecycle._write_summary`；`timeline._Record`；`migrate._run` / `_ensure_schema_table`。**判据就是有没有进 `__all__` 或有没有 `_` 前缀**，两个信号在代码里是严格一致的。

**本项目实际用到的面**（grep 出来的全部调用点）：

| 上层文件 | 用到的符号 | 用途 |
|---|---|---|
| `app/core/memengine.py` | `MemoryLifecycle`、`MemoryRecall`、`MemoryRenderer`、`Store` | 开库、建四个单例 |
| `app/memory/h2.py` | `MemoryEvent`、`MemoryItem`、`decay` 模块、`models.clamp_heat` | 唯一写入口 + 召回 |
| `app/memory/memv2.py` | `decay`、`MemoryEvent`、`MemoryItem`、`clamp_heat`、`clamp_importance` | 老 `memories` 表迁进引擎 + 建只读视图 |
| `app/memory/companion.py` | `companion.BringUpLedger`、`CompanionMemory`、`ThoughtLedger`、`emotion.detect_emotion`、`triggers.ASK` | 陪伴层 |
| `app/life/thoughts.py` | `companion.emotion.detect_emotion`、`rumination.thought_signature` | 独处念头 |
| `scripts/smoke.py`、`scripts/humanity_check.py` | `MemoryEvent`、`MemoryItem` | 自检脚本造数据 |

**所以「上层唯一调用方是 h2 与 memengine」这个说法只对写入路径成立**——读路径和陪伴层还有 `memv2.py`、`companion.py`、`thoughts.py` 三个文件直接 import 引擎。

### 5.2 不能碰的东西

1. **不能绕过 `Store` 写 `mem_item` / `mem_cue` / `mem_event`**。`Store` 是唯一写入口，`_write()` 负责事务 + 异常翻译 + `RLock`；绕过去的直接后果是锁失效（`connection` 属性明确写着 `write access is not guarded`）。
2. **不能改 `mem_event` 的行**。schema 注释写明 append-only，`Store` 也没有 update/delete 方法——只有 `dump()`/`load(replace)` 会在整库替换时清空它。
3. **不能用 SQL 写 `memories` 视图**。它是 `app/memory/memv2.py` 建的只读投影（`CREATE VIEW memories AS ...`），底层就是 `mem_item` 的投影。
4. **不能手改 `schema_migrations` 的业务含义**。checksum 是文件字节的 sha256，改一个字节都会让下次启动 `MigrationError`。本仓库唯一允许的改法就是 `app/core/memengine.py::heal_legacy_migration_checksums()` 那种「已知旧值 → 已知新值」的定点修正。
5. **不能让 `mem_event.raw_text` 进 prompt**。`recall.py` 与 `render.py` 都不读 `mem_event`（recall 的 docstring 最后一行就是「不读 `mem_event` 原文：召回只碰 `mem_item` / `mem_cue` / `mem_embedding`」），`h2.py` 的铁律里也重复了一遍。
6. **不能期待引擎自动生成 embedding**。`mem_embedding` 只能由调用方 `put_embedding()` 写，`capabilities.vector_recall` 默认 `False`，本项目 `memengine._open()` 也没传 `Capabilities`，所以**向量召回在本项目里是关着的**。

### 5.3 为什么是整目录覆盖式升级

答案在代码结构里，不在文档里：

1. **迁移文件带 checksum 记账，改一个字节就要全链路协调**。`schema_migrations` 记 `(version, name, checksum, applied_at)`，`apply()` 对已记账版本逐一比对文件 sha256，不一致直接 `MigrationError`。补丁式改动会让「本仓库的文件」和「上游的 checksum」永久分叉，而账本里记的是历史上真正跑过的那个值。2026-10-31 的 CRLF→LF 归一就是活例子：内容逐字不变，照样拦下启动，最后只能在上层写一个定点修账本的函数。
2. **`__all__` 是明确的版本契约，内部实现可以自由重排**。上游只要保住那 66 个名字与 `Store` 的公开方法签名，`db/repo.py` 内部怎么换 SQL、`recall.py` 怎么换打分路径都不影响调用方。反过来说，一旦本仓库对 `_score_item` / `_partition_in` 做了局部修改，就等于把内部实现钉死，从此无法接受上游的任何重构。
3. **升级只需要一次整目录替换**。`app/core/memengine.py` 做的全部适配只有三件事：把 `vendor/` 插进 `sys.path`、`Store(want)` 打开同一个 `life.db`、修已知的 checksum 行尾差异。三件事都不碰引擎内部，所以上游发布新版本时替换目录 + 重跑一次迁移即可，没有合并冲突要解。
4. **表是引擎自己的，归属清晰**。7 张表（`mem_item` / `mem_cue` / `mem_event` / `mem_recall_log` / `mem_render_cache` / `mem_embedding` / `schema_migrations`）全部由引擎建、由引擎写；项目自己的 30 张业务表与 `memories` 视图由 `app/core/db.py` 与 `app/memory/memv2.py` 建（`data/life.db` 当前共 37 张表 + 1 个视图 = 引擎 7 + 业务 30）。这两组表共用一个 `life.db` 文件但**不共用 DDL 归属**，所以引擎升级不会碰到业务表。

### 5.4 改动只能落在哪些上层文件

这一层是只读副本，任何需求都只能在下列位置落地（都是代码里真实存在的适配点，不是建议）：

| 需求 | 唯一合法的落点 | 代码依据 |
|---|---|---|
| 换库路径 / 换单例 | `app/core/memengine.py::engine_path()` / `_open()` | 唯一的 `Store(...)` 构造点 |
| 修老库的迁移 checksum | `app/core/memengine.py::_KNOWN_CHECKSUM_REWRITES` + `heal_legacy_migration_checksums()` | 唯一写 `schema_migrations` 的地方 |
| 改写入语义（查重、初始热度、线索抽取） | `app/memory/h2.py::add()` / `_find_duplicate()` / `extract_cues()` | `INITIAL_HEAT=0.90`、`HOT_THRESHOLD=0.50`、`COLD_THRESHOLD=0.12` 都定义在这里 |
| 改「热层 + 线索层」的检索策略 | `app/memory/h2.py::retrieve()` / `_partitions()` / `_hot_from()` | 分区合并是上层补的，引擎只做严格相等过滤 |
| 改衰减节奏 | `app/memory/h2.py::maybe_tick()` 的 `_TICK_INTERVAL_S = 3600` | 引擎没有定时任务，推进只靠这个调用 |
| 改召回打分权重 | 构造 `MemoryRecall(store, weights=RecallWeights(...))`，落点在 `app/core/memengine.py::_open()` | `RecallWeights` 是构造参数，不是全局 |
| 改渲染预算 / 场景 | 构造 `MemoryRenderer(store, limits=RenderLimits(...), scenario=...)` | 同上 |
| 改陪伴层的触发门槛 | `app/memory/companion.py` 构造 `CompanionMemory(policy=TriggerPolicy(...))` | `TriggerPolicy` 也是构造参数 |
| 老 `memories` 表迁移 / 视图 | `app/memory/memv2.py` | 视图由它建，不属引擎 |

## 6. 交叉引用

- 表清单、`data/life.db` 全库表结构与 `memories` 视图：docs/17-database.md（`mem_item` / `mem_cue` / `mem_event` / `mem_recall_log` / `mem_embedding` / `mem_render_cache` / `schema_migrations` 七张表在其中都有独立小节）
- 引擎接线与 vendor 路径注入：`app/core/memengine.py`（181 行）
- 本章描述的所有写入与召回行为的上层封装：`app/memory/h2.py`（704 行）
- 老 `memories` 表迁移与只读视图：`app/memory/memv2.py`
- 陪伴层（唯一启用 companion 的地方）：`app/memory/companion.py`、`app/life/thoughts.py`
- 建库与业务表 DDL（与引擎表同库）：`app/core/db.py`，见 docs/17-database.md

## 7. 闭源分发：打包模式、加密容器与机器绑定

> §1–§6 讲的是「源码在盘上时引擎怎么工作」；本节讲「源码不在盘上时还能怎么工作」。
> 实测环境：Windows / CPython 3.14.8 / 本项目 venv（`cryptography`、`nuitka`、`pyarmor` 三个都没装）。

### 7.1 分发边界：源码不随仓库分发

上架后对外只留四样东西，**其余全部删除**：

| 留下 | 作用 |
|---|---|
| `app/memory/api.py` | 唯一公开接口（`remember` / `recall` / `stats` / `forget`），业务只认它 |
| `app/memory/_loader.py` | 解封器：容器 → 临时目录 → `sys.path`，退出即删 |
| `app/memory/_vault.bin` | 加密容器（源码的密文形态，`*.bin` 已 gitignore） |
| `docs/07-vendor-memory.md`（本节） | 格式、接口、模式选择、迁移、校验 |

删除对象：`vendor/symvolve_memory/` 整目录（35 个 `.py` / 12465 行 + 2 个 `.sql`）。
源码本体放私有仓，**不随本仓库、不随安装包、不随任何 tag 分发**。
本章是唯一一份"内部结构"描述文档，公开仓保留它是刻意的：接口与数据格式必须可审计，
被保护的是**实现**（算法与代码），不是行为契约。

验收口径两条，缺一不可：

1. 删掉 `vendor/symvolve_memory/` 后，`import app.memory.api` + 一次 `remember`/`recall` 往返仍能跑通（§7.6 演练实测）；
2. `strings app/memory/_vault.bin | findstr /i "recall remember"` 零命中（§7.6 实测零命中）。

### 7.2 公开接口清单（`app/memory/api.py`）

导入顺序：**先源码、后容器**。源码目录在盘上就按老路走（`vendor/` 追加进 `sys.path` 后
`import symvolve_memory`），不在盘上才让 `_loader` 解 `_vault.bin`。两条都不通 → 抛
`MemoryCoreUnavailable`（带 `cause` 字符串说明"是没打包还是指纹不对"），
**绝不静默返回空结果**——把"内核没装"退化成"她没记忆"是最坏的一种 bug。

| 函数 | 签名要点 | 返回 | 与 `app/memory/h2.py` 旧名对应 |
|---|---|---|---|
| `remember` | `(text, *, user_id=0, chat_type="", kind="fact", title="", importance=0.6, pinned=False, cues=None, bot_reply=None, meta=None, db_path=None)` | `int` 条目 id（空正文 0）；同人/场合/类/正文重复写 = 加温 + 抬重要度，不新增行 | `add(kind, content, ...)` |
| `recall` | `(query, *, user_id=0, chat_type="", limit=5, log=True, bump_heat=True, db_path=None)` | `{status, confidence, cues, items[], item_ids[], scanned, steps}`；`chat_type=""` = 不筛场合（传引擎 `None`） | `retrieve(text, ...)` |
| `stats` | `(*, db_path=None)` | 引擎计数 8 项 + `embeddings`/`render_cache`/`db_path`/`core` | `stats()` / `engine_stats()` |
| `forget` | `(item_id, *, reason="manual", db_path=None)` | `bool`；软删（`forgotten=1`），审计与回溯保留 | `delete_memory(mid)` |
| `forget_user` | `(user_id, *, db_path=None)` | `int` 条数（批量软删） | `forget_user(user_id)` |
| `ensure_core` | `(*, force_vault=None)` | 内核模块对象 | `app/core/memengine.py` 的隐式 import |
| `core_info` | `()` | `{ok, origin, version, file, temp_dir, forced_vault}` | 无（诊断新增） |
| `close` | `()` | `None`（关单例，换库/收尾用） | `memengine.reset()` |

环境变量（只影响本模块与解封器，不影响引擎）：

| 变量 | 作用 |
|---|---|
| `SYM_MEMORY_DB` | 压过 `app.core.config`，直接指定库文件（演练/测试用） |
| `SYM_MEMORY_FORCE_VAULT=1` | 跳过源码路径，强制走容器（`--verify` 与"删源码演练"用） |
| `SYM_MEMORY_VAULT_PASSPHRASE` | 口令模式容器的口令（见 §7.5 迁移） |

为什么接口层不直接 `from vendor.symvolve_memory import ...`：引擎内部全是绝对导入
`from symvolve_memory.xxx import ...`，而且 `app/core/memengine.py` 也是按顶层名加载的。
走 `vendor.` 前缀会加载出**第二份**模块对象，两份 `MemoryItem` 类互不认 `isinstance`。
所以接口层统一按顶层名 `symvolve_memory` 加载，`vendor/` 只是 `sys.path` 上的一段路。

### 7.3 三种打包模式怎么选

一条命令三种模式（`scripts/build_vendor_memory.py`），`--mode auto` 是默认：`nuitka → pyarmor → seal` 逐级降级，每级为什么降级都会打出来。

| 模式 | 前提 | 产物 | 强项 | 弱项 |
|---|---|---|---|---|
| `--mode nuitka` | 装 Nuitka，且 CPython ≤ 3.13 | `build/vendor-memory/nuitka/*.pyd` | 编成 C，源码彻底不在盘上，反编译成本最高 | 与解释器小版本 + 架构死绑；编译慢；跨平台要各编一份 |
| `--mode pyarmor` | 装 PyArmor，且 CPython ≤ 3.13 | `build/vendor-memory/pyarmor/` | 纯 Python 产物、跨平台、字节码混淆 + 运行时壳 | 免费版有文件数/体积上限；壳本身是入口，`pyarmor` 版本要跟运行时对齐 |
| `--mode seal` | **无**（纯标准库） | `app/memory/_vault.bin`（单文件） | 不引新依赖、一次封装到处能跑（同机）、源码零落盘 | 不是编译器级保护；机器绑定会带来迁移成本（§7.5） |

本机实测结论（写进收尾报告的那部分）：

- `pip list | findstr /i "nuitka pyarmor"` → 空；`importlib.util.find_spec` 对两者都是 `None`；
- 即使装了也不行：**CPython 3.14 超出 Nuitka / PyArmor 上游支持上限（3.13）**；
- 所以 `--mode nuitka` 与 `--mode pyarmor` 现在都**明确退出码 3**（`[失败] ... 不可用`），
  不假装成功、不产出假产物；`--mode seal` 是当前唯一能跑通的路径（退出码 0，§7.6 实测）。

选型口诀：有构建机 + 能锁 3.12/3.13 → `nuitka`；只有 PyPI 环境 → `pyarmor`；
两者都不满足（**本项目当前状态**）→ `seal`，并且用 `--verify` 把产物锁死在验收里。

### 7.4 容器数据格式（`_vault.bin`）

**库表结构**（`mem_event` / `mem_item` / `mem_cue` / `mem_recall_log` / `mem_render_cache` /
`mem_embedding` / `schema_migrations`）见 §3.4 与 docs/17，本节只讲"密文容器"这一层。

容器 = 59 字节头 + 密文 + 32 字节 tag，全部小端：

| 偏移 | 长度 | 字段 | 说明 |
|---|---|---|---|
| 0 | 9 | `magic` | `SYMVAULT\x01`，认错文件靠它 |
| 9 | 2 | `version` | 布局版本，当前 1；不认识的版本直接拒绝 |
| 11 | 4 | `iters` | PBKDF2 迭代数（默认 120000） |
| 15 | 1 | `mode` | 0 = 机器指纹｜1 = 口令 |
| 16 | 3 | `pad` | 对齐填充，恒 0 |
| 19 | 16 | `salt` | 随机盐（每次封装都换） |
| 35 | 16 | `nonce` | 密钥流起始随机数 |
| 51 | 8 | `ct_len` | 密文长度 |
| 59 | `ct_len` | `ct` | 密文 |
| 59+ct_len | 32 | `tag` | `HMAC-SHA256(mac_key, 头 ‖ 密文)` |

**密钥派生**：`master = PBKDF2-HMAC-SHA256(密钥材料, salt, iters, dklen=32)`，再岔成
`enc_key = HMAC(master, "symvault/enc")`、`mac_key = HMAC(master, "symvault/mac")`。
密钥材料 = 机器指纹（默认，见 §7.5）或口令（`SYM_MEMORY_VAULT_PASSPHRASE`）。

**加密**：PRF-CTR —— `keystream_i = HMAC-SHA256(enc_key, nonce ‖ counter64_be)`，
密文 = 明文 XOR 密钥流。**先加密后 MAC**，解封时先验 `tag` 再解密：
换机、改字节、改头字段，三种情况都只会得到 `VaultLocked`，一个字节明文都不会被产出。

**载荷**：确定性 ZIP（`ZIP_DEFLATED` level 9，成员时间戳恒 `1980-01-01 00:00:00`，
成员权限恒 `0o644`，成员顺序按路径排序），内含：

- `symvolve_memory/**/*.py`、`symvolve_memory/**/*.sql`（只收源码与迁移脚本，37 个文件）；
- `symvolve_memory/../_vault_manifest.json`（容器根）：每个文件的 `path`/`bytes`/`sha256`
  + `file_count` + `built_at`。**清单也封在密文里**——文件名单属于"谁在内核里"，
  不能以明文躺在容器头里（否则 `strings` 一眼就能看出 `recall.py`）。

**明文落地**：只落 `%TEMP%\symmem-<随机>`（`tempfile.mkdtemp`），
`_loader._guard_outside_repo()` 拒绝解到仓库目录内；`atexit` 整目录删除，
手动调 `_loader.cleanup()` 也可。仓库里从头到尾没有第二份明文。

**强度边界（说清楚，别自欺）**：这是"够用级"封装，不是 DRM 级保护。
它挡的是"拷走文件就能读源码"和"`strings` 一眼看穿结构"，挡不住
"在同一台机器上以同一用户运行并 dump 进程内存"。真要抗本地 dump 得上
`nuitka` + C 层外加壳，那是另一条路线（§7.3）。

### 7.5 机器绑定与迁移（换机怎么重新授权）

**指纹组成**（`_loader.machine_secret()`，只在本机内存里用，不写盘、不进本文档）：

```
system | machine | hostname(小写) | MAC48(十六进制)
```

`--fingerprint` 只打印它的 `sha256` 前 16 位（摘要，反推不出原文），
用于对账"这个包是给哪台机器封的"，本机摘要不在文档里记录。

**什么时候会失效**：换机器、改计算机名、换网卡/禁用网卡导致 MAC 变、
换了系统盘做系统重装（hostname 变）。表现统一是 `VaultLocked`，
`cause` 里带本机指纹摘要，一眼能看出"不是这台机器"。

**迁移两条路**：

1. **重封（推荐，安全性最高）**：在开发机/构建机上重新
   `python scripts/build_vendor_memory.py --mode seal`，把新的 `_vault.bin`
   随安装包发出去。需要源码在场，所以这是"授权 = 重新构建"。
2. **口令模式（要搬来搬去时）**：封装前设
   `SYM_MEMORY_VAULT_PASSPHRASE=<口令>`，容器 `mode=1`，密钥材料从机器指纹换成口令。
   之后把 `_vault.bin` 复制到任何机器、设同一个环境变量即可解封。
   代价很直白：口令就是密钥，别再叫它"机器绑定"，它就是一把共享钥匙。

**不失效的东西**：`seal` 产物是纯 Python 源码的密文，换 Python 小版本、换架构都不影响；
`nuitka`/`pyarmor` 产物反过来——换解释器就得重编。

### 7.6 产物校验方法

三条命令，从便宜到贵：

```powershell
# 1) 只读头 + 真解一遍载荷，数文件、验 ZIP CRC（不落盘）
.venv\Scripts\python.exe -c "from app.memory import _loader as L; import json; print(json.dumps(L.inspect_vault(), ensure_ascii=False, indent=2))"

# 2) 官方自检：容器 + 子进程导入（强制走容器，origin 必须是 vault）
.venv\Scripts\python.exe scripts\build_vendor_memory.py --verify

# 3) 文件级：容器哈希 + 关键字扫描
Get-FileHash app\memory\_vault.bin -Algorithm SHA256
strings app\memory\_vault.bin | findstr /i "recall remember"
```

本机实测（2026-11-02 最终构建，`--mode auto` 降级到 `seal`；盐每次随机，哈希只对这一次构建有效）：

| 项 | 值 |
|---|---|
| 容器字节 | 154103（头 59 + 密文 154012 + tag 32） |
| 容器 SHA256 | `26fc23c4f1ef331828efacb1b77f9f932b3ac4523ed914b0bca8e788b1c3f533` |
| 载荷 ZIP | 154012 字节，38 个成员（37 源文件 + 清单） |
| 封装耗时 | 91.7 ms（含 PBKDF2 120000 次） |
| 解密 + 落盘 + import | 246.8 ms（子进程整体 407.9 ms） |
| 关键字扫描 | `_vault.bin` 零命中；同口径扫明文载荷命中 84 处（证明检查有效） |

三种故障的表现（都已实测）：删掉 `_vault.bin` → `VaultMissing`（"先跑 --mode seal"）；
改一个字节 → `VaultLocked`（`hmac.compare_digest` 不过）；换机 → 同样 `VaultLocked`
（`cause` 里给出本机指纹摘要）。

### 7.7 接线：还差哪一行

`app/core/memengine.py:29` 仍然是裸的 `from symvolve_memory import (...)`。
源码目录还在时这行没问题；**删掉 `vendor/` 后它会先炸**，容器再好也轮不到解封。
唯一需要的改动是在那行之前插一行（`app/core/` 不归本轮改，留给 Lead）：

```python
from app.memory.api import ensure_core   # 源码不在盘上时先把容器解到临时目录
ensure_core()
from symvolve_memory import ...          # 原样保留
```

`app/memory/h2.py` 同理：它 `from symvolve_memory import ...`（29–34 行）依赖
`memengine` 先把路径插好，接线点只有 `memengine` 一处，h2 不用动。
业务调用方一行都不用改——`h2.add/retrieve/stats` 的契约没变。

### 7.8 坑

- **别用 `vendor.symvolve_memory` 这个包名**：会加载出第二份模块对象，`isinstance` 全盘失效（§7.2）。
- **`PYTHONPATH` 会盖掉 `sys.path[0]`**：演练脚本用 `PYTHONPATH=<演练根>` + 外部脚本跑时，`app` 从演练根解析是对的；但换成直接跑 `.tmp\drill-run.py` 就会因为脚本目录是 `.tmp` 而 `ModuleNotFoundError: app`。
- **`Rename-Item -NewName` 只接受裸文件名**：在 `app\memory\` 下改名备份 `_vault.bin`，备份会落在同一目录，不会落到当前目录。
- **`--verify` 必须在源码在盘上时也强制容器**：所以它设了 `SYM_MEMORY_FORCE_VAULT=1`，否则开发机会"验了个寂寞"（走的是源码路径）。
- **指纹含主机名**：改个计算机名就解不开，这不是 bug，是绑定（§7.5）。
- **口令模式别和机器模式混发**：`mode` 字节不同，混了只会得到 `VaultLocked`，看 `cause` 里的 `mode_name` 先分辨。

