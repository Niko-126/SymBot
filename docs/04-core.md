# 04 · 内核基础层（app/core）

> 覆盖：`app/core/*.py`（8 个非空文件 / 1452 行；`app/core/__init__.py` 是 0 字节空文件）｜基于 2026-11-01 的代码状态｜上游：`app/main.py`、`app/api/*`（进程入口与适配器）｜下游：docs/05（对话主链），以及记忆/关系/生活各章

## 1. 这一层干什么

`app/core` 是内核的"地基"，只做六件事：把配置从三层来源合成一个 `Config`；算出开发态/打包态两套路径；开出唯一的 SQLite 连接面并建库、补列；在进程里捏一个唯一的 `Runtime` 对象图；把 vendored 记忆引擎接进同一个库；给"她可以被改变"这件事留一本账（`continuity_log`）。

它被三方调用：`app/main.py`（启动）、`app/api/*`（每个请求拿 `runtime.get()` 与 `db`）、以及几乎全部业务模块（读配置、读写库、写变更账本）。

它明确不做的事：不组装 prompt、不结算关系、不写记忆条目、不跑生活作息。这些分别属于 `app/mind`、`app/relation`、`app/memory`、`app/life`。`app/core` 里唯一有"业务判断"的是 `preflight.check()`（只按模型名判断是否同源）和 `channel.Caps`（只描述通道能力，不做发送）。

四条在全仓反复出现的约定，其实现点都在本层：记忆写入只走 `app/memory/h2.py: add()`；关系结算只走 `app/relation/engine.py: apply_appraisal()`；改变她的动作要 `app/core/continuity.py: record()` 留痕；`import app.main` 有副作用（建库/读人设/写 kv），所以 `Runtime.__init__` 的顺序本身是契约。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/core/__init__.py` | 0 | 空文件，仅用于把 `app/core` 变成包 |
| `app/core/config.py` | 356 | 三层配置合并、环境变量兼容、配置段 dataclass、本地配置回写 |
| `app/core/paths.py` | 26 | 开发态与 PyInstaller 打包态的 `RESOURCE_ROOT` / `DATA_ROOT` 解析 |
| `app/core/db.py` | 586 | SQLite 打开方式、30 张表与 17 个索引的建表脚本（迁移时再补 5 个索引）、逐列探测式迁移、连接/事务/重试封装 |
| `app/core/runtime.py` | 76 | 单进程单例 `Runtime`：配置 + 人设 + 库 + traits + 预检 + LLM 客户端 |
| `app/core/memengine.py` | 181 | 把 `vendor/symvolve_memory` 插进 `sys.path` 并对同一个 `life.db` 开单例 Store |
| `app/core/continuity.py` | 118 | 连续性账本：`record()` / `recent()` / `count()` / `summary_text()` / `status()` |
| `app/core/preflight.py` | 58 | 启动预检：`base_url` 与 `model`/`force_model` 命名规则是否同源（零网络） |
| `app/core/channel.py` | 51 | 三种通道的能力位 `Caps` 与全局 `set_channel()` |

## 3. 模块逐一详解

### 3.1 `app/core/config.py`

**职责**：定义全部配置段的 dataclass，从 `config.yaml` → `config.local.yaml` → `SYMEM_*`/`AI_LIFE_*` 环境变量三层合成一个进程内单例 `Config`，并负责把 provider 设置回写本地配置。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `env_var` | `env_var(name: str) -> str \| None` | 读一个配置环境变量，新名 `SYMEM_<NAME>` 优先，旧名 `AI_LIFE_<NAME>` 兜底 |
| `_apply_env` | `_apply_env(cfg: Config) -> Config` | 把 14 个环境变量写进 `cfg`，返回同一个对象 |
| `load` | `load() -> Config` | 执行三层合并，每次调用都重新读盘 |
| `get` | `get() -> Config` | 进程级缓存单例，只有 `_cached is None` 时才调 `load()` |
| `save_provider` | `save_provider(base_url: str, api_key: str, model: str) -> pathlib.Path` | 把三项写进 `DATA_ROOT/config.local.yaml`，返回路径 |
| `save_provider_fields` | `save_provider_fields(fields: dict) -> pathlib.Path` | 把 `provider.*` 的附加字段（`temperature`/`max_tokens`/`small_model`）写进同一个本地文件 |
| `Config.db_file` | `@property db_file -> pathlib.Path` | 相对路径按 `DATA_ROOT` 解析成绝对路径 |
| `Config.persona_file` | `@property persona_file -> pathlib.Path` | 人设文件三级优先：环境变量 > `config.yaml` 显式路径 > `DATA_ROOT/persona-active.yaml` |
| `Config.panel_dir` | `@property panel_dir -> pathlib.Path` | 固定为 `RESOURCE_ROOT / "panel"` |

**三层合并的实现路径**（`load()`，第 315–346 行）：

1. 第 1 层读 `CONFIG_PATH = RESOURCE_ROOT / "config.yaml"`，`yaml.safe_load` 成 `raw`；文件不存在则 `raw = {}`。
2. 第 2 层从 `LOCAL_CONFIG_PATHS = [DATA_ROOT / "config.local.yaml", RESOURCE_ROOT / "config.local.yaml"]` 里取**第一个存在的**文件（`next((p for p in LOCAL_CONFIG_PATHS if p.exists()), None)`），只取一个，不叠加。
3. 合并只做一层深合并：`for key, value in extra.items()`，若两边都是 dict 则 `raw[key] = {**raw[key], **value}`，否则整键替换。没有递归，所以 `relationship.lover` 这种两级结构是靠第 4 步单独处理的。
4. `relationship` 段先取出 `_rel_raw = raw.get("relationship") or {}`，再用 `LoverCfg(**_rel_raw.get("lover") or {})` 与 `CapsCfg(**_rel_raw.get("caps") or {})` 分别构造——这是唯一一个手写嵌套的段。
5. 其余 11 个段统一 `Cls(**(raw.get("<段名>") or {}))`。**关键字必须与 dataclass 字段同名**，写错一个键会在构造时抛 `TypeError`，没有任何兜底。
6. 最后 `return _apply_env(cfg)`：环境变量优先级最高。

`get()` 是缓存入口（第 349–356 行）：`_cached` 是模块级全局，一旦构造就不再重读；改完 `config.local.yaml` 想生效，要么重起进程，要么走 `Runtime.apply_provider()`（只覆盖 provider 三项）。

**`env_var()` 的双名兼容**：

```python
def env_var(name: str) -> str | None:
    return os.environ.get(f"SYMEM_{name}") or os.environ.get(f"AI_LIFE_{name}")
```

用 `or` 而不是 `if is not None`：`SYMEM_X=""`（空串）会继续落到 `AI_LIFE_X`。`_apply_env` 里 11 个 `if v := env_var(...)` 都是"空串等于没设"，`MOCK` 用 `== "1"` 判等，两个布尔开关（`QQ_ENABLED`/`ONEBOT_ENABLED`）用 `if (v := env_var(...)) is not None`——因此 `SYMEM_QQ_ENABLED=`（空串）会被当作"显式给了值"并解析成 `False`。

**14 个环境变量（`_apply_env`，第 241–273 行）**：

| 变量名（去掉前缀） | 写入字段 | 解析规则 |
|---|---|---|
| `MOCK` | `cfg.provider.api_key = ""` | 仅当值等于字符串 `"1"`；其它值无效果 |
| `PERSONA` | `cfg.persona.path`、`cfg.persona_override` | 相对路径按 `ROOT`（即 `RESOURCE_ROOT`）解析 |
| `QQ_ENABLED` | `cfg.qq.enabled` | `v.strip().lower() in ("1","true","yes","on")` |
| `ONEBOT_ENABLED` | `cfg.onebot.enabled` | 同上 |
| `QQ_APPID` | `cfg.qq.appid` | 原样赋值 |
| `QQ_TOKEN` | `cfg.qq.token` | 原样赋值 |
| `BASE_URL` | `cfg.provider.base_url` | 原样赋值 |
| `API_KEY` | `cfg.provider.api_key` | 原样赋值 |
| `MODEL` | `cfg.provider.model` | 原样赋值 |
| `SMALL_MODEL` | `cfg.provider.small_model` | 原样赋值 |
| `PORT` | `cfg.server.port` | `int(v)`，非数字会抛 `ValueError` |
| `CONTEXT_MODE` | `cfg.context.mode` | 原样赋值，不做取值校验 |
| `DB` | `cfg.store.db_path` | 原样赋值（相对路径由 `db_file` 补 `DATA_ROOT`） |
| `CONSOLE_TOKEN` | `cfg.console.token` | 原样赋值 |

**配置段与字段默认值（全列）**：

| 段 | 字段 | 默认值 |
|---|---|---|
| `server` | `host` | `"127.0.0.1"` |
| | `port` | `8787` |
| `provider` | `base_url` | `"https://api.deepseek.com/v1"` |
| | `api_key` | `""` |
| | `model` | `"deepseek-chat"` |
| | `small_model` | `""` |
| | `temperature` | `0.85` |
| | `max_tokens` | `400` |
| | `timeout_s` | `60.0` |
| | `force_model` | `""` |
| `context` | `window` | `64000` |
| | `reserve_output` | `1024` |
| | `warn_ratio` | `0.55` |
| | `force_ratio` | `0.75` |
| | `hard_ratio` | `0.90` |
| | `mode` | `"budget"`（另一取值 `"ratio"`） |
| | `budget` | `6000` |
| | `trigger` | `5000` |
| | `hard_cap` | `8000` |
| | `keep_recent` | `12` |
| | `verbatim_max` | `30` |
| | `degrade` | `False` |
| `store` | `db_path` | `"data/life.db"` |
| | `media_dir` | `"data/media"` |
| `persona` | `path` | `"persona/whale-girl.yaml"` |
| `life` | `timezone` | `"Asia/Shanghai"` |
| `developer` | `enabled` | `True` |
| | `key` | `""` |
| | `logo_taps` | `12` |
| `trend` | `harvest_enabled` | `False` |
| | `interval_hours` | `24` |
| | `limit` | `20` |
| `qq` | `enabled` | `False` |
| | `auto_start` | `True` |
| | `appid` | `""` |
| | `token` | `""` |
| | `sandbox` | `True` |
| | `group_at_only` | `True` |
| `onebot` | `enabled` | `False` |
| | `api_base` | `"http://127.0.0.1:3000"` |
| | `access_token` | `""` |
| | `self_id` | `""` |
| | `group_at_only` | `True` |
| | `timeout_s` | `10.0` |
| | `ignore_ids` | `[]` |
| | `mention_keywords` | `["小鲸鱼","大肥鱼","鲸鱼娘","小鲸","DeepSeek娘","超可爱大肥鱼","超级大肥鱼"]` |
| | `sticker_chance` | `0.45` |
| | `voice_chance` | `0.15` |
| | `auto_accept_friend_request` | `True` |
| | `proactive_push` | `False` |
| | `proactive_interval_min` | `5` |
| `console` | `token` | `""` |
| `relationship.lover` | `enabled` | `True` |
| | `threshold` | `80.0` |
| | `cooldown_days` | `7` |
| `relationship.caps` | `enabled` | `False` |
| | `daily_pos` | `5.0` |
| | `daily_neg` | `-8.0` |
| `Config`（根） | 12 个配置段 | 各自 `field(default_factory=...)` |
| | `persona_override` | `None`（`pathlib.Path \| None`，只由 `PERSONA` 环境变量写） |

两个派生属性：`ProviderCfg.is_mock` 为 `not self.api_key.strip()`（**只要 key 是空白就是 mock**）；`ProviderCfg.small` 为 `self.small_model or self.model`；`ContextCfg.usable` 为 `max(2000, self.window - self.reserve_output)`（默认 `64000-1024=62976`）。

**坑**（代码里真实存在的）：

- 仓库里的 `config.yaml` 有一个 `experience` 段（`notification`/`autostart`/`floating_window`），`Config` 里没有对应 dataclass，`load()` 只挑它认识的 12 个段，所以这段配置**完全不生效**，写它不会有任何行为变化。
- `config.yaml` 里 `persona.path` 是 `persona/whale-chan.yaml`，而 dataclass 默认值是 `persona/whale-girl.yaml`；文件缺失时 `persona_file` 会依次回落到 `persona-active.yaml` 再回落到这个不存在的路径。
- `persona_file` 的优先级注释写明"`config.yaml` 显式路径必须优先于 `persona-active.yaml`"，因为后者是导入残留；改优先级会让用户选的人设被旧版本顶回去。
- `save_provider` 读不出 YAML（`yaml.YAMLError`）时会把整个文件当空字典重写，等于丢掉 `config.local.yaml` 里其它段的配置（`load()` 同样没有对本地文件的 `YAMLError` 做保护）。

### 3.2 `app/core/paths.py`

**职责**：只导出两个模块级常量，决定"代码/静态资源在哪"和"可写数据在哪"。

| 常量 | 开发态（`sys.frozen` 不存在） | 打包态（PyInstaller） |
|---|---|---|
| `FROZEN` | `False` | `True` |
| `RESOURCE_ROOT` | `pathlib.Path(__file__).resolve().parents[2]` | `sys._MEIPASS`，缺失则 `sys.executable` 所在目录 |
| `DATA_ROOT` | 等于 `RESOURCE_ROOT` | `pathlib.Path(sys.executable).resolve().parent` |

**关键实现**：

1. 先按 `FROZEN` 定两个根目录。
2. 再用 `SYMEM_DATA`（旧名 `AI_LIFE_DATA`）覆盖 `DATA_ROOT`：`_env_data = os.environ.get("SYMEM_DATA") or os.environ.get("AI_LIFE_DATA")`，非空就整体替换。
3. 覆盖只影响 `DATA_ROOT`，`RESOURCE_ROOT` 永远是代码/资源那一侧。

**坑**：

- `config.py` 在**导入时**就算出 `LOCAL_CONFIG_PATHS`，`memengine.py` 在**导入时**就算出 `_VENDOR`。因此 `SYMEM_DATA` 必须在 `import app.core.config` 之前设好，运行中再改环境变量不会移动配置查找路径。
- 打包态把数据和 exe 放同一目录、资源放临时解包目录，这意味着"换一个 exe 位置 = 换一份数据库"，`DATA_ROOT` 不能靠相对当前工作目录推。

### 3.3 `app/core/db.py`

**职责**：把 `data/life.db` 打开、建表、补列、给业务层一套最薄的读写封装。全仓所有 SQL 都经过这里的 `cursor()`/`rows()`/`one()`/`execute()`/`executemany()`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `init` | `init(db_path: pathlib.Path) -> None` | 设全局库路径、建目录、跑 `_SCHEMA`、插两行单例初始数据、跑 `_migrate`、交给 `memv2.ensure_ready()` |
| `connect` | `connect() -> sqlite3.Connection` | 每次新建连接，开 WAL 与 `busy_timeout` |
| `cursor` | `cursor(commit: bool = True) -> Iterator[sqlite3.Cursor]` | `@contextmanager`，异常回滚并重抛，`finally` 关连接 |
| `rows` | `rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]` | 只读查询，`commit=False` |
| `one` | `one(sql: str, args: tuple = ()) -> sqlite3.Row \| None` | 取第一行 |
| `execute` | `execute(sql: str, args: tuple = ()) -> int` | 单条写，返回 `lastrowid or 0`；锁冲突重试 3 次 |
| `executemany` | `executemany(sql: str, seq: list[tuple]) -> int` | 批量写，一个事务一个连接，返回 `rowcount`；锁冲突重试 3 次 |
| `kv_get` | `kv_get(key: str, default: str = "") -> str` | 读 `kv` 表 |
| `kv_key` | `kv_key(key: str, scope: Any = None) -> str` | 作用域键：`scope` 非空 → `key:scope`，否则全局键。群聊/多用户状态一律走它，别手写单个全局键 |
| `kv_set` | `kv_set(key: str, value: str) -> None` | `INSERT ... ON CONFLICT(key) DO UPDATE SET value = excluded.value` |
| `_table_kind` | `_table_kind(conn: sqlite3.Connection, name: str) -> str` | 查 `sqlite_master.type`，返回 `"table"`/`"view"`/`""` |
| `_migrate` | `_migrate(conn: sqlite3.Connection) -> None` | 逐列探测式补列 + 补表 + 补索引 |

**连接与 WAL**（`connect()`，第 501–508 行）：

1. `_DB_PATH` 未设时直接 `raise RuntimeError("db.init() 未被调用")`，这是全仓最常见的启动错误来源。
2. `sqlite3.connect(str(_DB_PATH), timeout=15)`。
3. `conn.row_factory = sqlite3.Row`，业务层才能 `row["col"]` 取值。
4. `PRAGMA journal_mode=WAL`（写不阻塞读）。
5. `PRAGMA busy_timeout=15000`（毫秒）。

**`init()` 顺序**（第 343–365 行）：

1. `_DB_PATH = pathlib.Path(db_path)`，`parent.mkdir(parents=True, exist_ok=True)`。
2. `conn.executescript(_SCHEMA)`——30 张表 + 22 个索引，全部 `IF NOT EXISTS`，所以每次启动都会跑、且对已存在的库是空操作。
3. `INSERT OR IGNORE INTO relation (id, affinity, trust, attraction, updated_at) VALUES (1, 0, 0, 0, datetime('now'))`。
4. `INSERT OR IGNORE INTO life_state (id, energy, mood, focus, social_battery, activity, last_tick) VALUES (1, 60, 0, 50, 60, '发呆', datetime('now'))`。
5. `_migrate(conn)`，`conn.commit()`，`finally` 关连接。
6. 关连接之后才 `from app.memory import memv2; memv2.ensure_ready()`——`memories` 视图在这里建，不在 `db.py` 里建（见下）。

**`_SCHEMA` 创建的 30 张表（全列）**：

| 表 | 主键/唯一约束 | 用途 |
|---|---|---|
| `events` | `id` 自增 | 对话事件流水；`role`/`kind`/`text`/`source`/`meta`，迁移补 `user_id`/`chat_type`/`group_id` |
| `summaries` | `id` 自增 | 工作记忆摘要；`session_seq`/`level`/`period_start`/`period_end`/`text`/`covered_to`/`tokens_est`/`created_at` |
| `session` | `id` 且 `CHECK (id = 1)` | 单会话行：`seq`/`her_name`/`name_locked`/`name_pending`/`status`/`started_at`/`ended_at`/`end_reason` |
| `relation` | `id` 且 `CHECK (id = 1)` | 单用户关系：`affinity`/`trust`/`attraction`，迁移补 `aversion` |
| `relation_log` | `id` 自增 | 每次结算一条：`d_affinity`/`d_trust`/`d_attraction`/`trigger`/`reason` |
| `qq_user` | `id` 自增，`openid` 唯一 | 群成员：`nickname`/`first_seen`/`last_seen`，迁移补 `blocked`/`blocked_reason` |
| `user_relation` | `user_id` 主键并外键 `qq_user(id)` | 每人独立关系：`affinity`/`trust`/`attraction`/`aversion`/`stage`/`hit_100_at`/`updated_at` |
| `lover` | `id` 自增 | 恋人绑定：`session_seq`/`user_id`/`status`/`bound_at`/`ended_at`/`end_reason`/`cooldown_until` |
| `human_notes` | `id` 自增 | 人类观察：`day`/`topic`/`content`/`confidence`/`user_id`/`session_seq`/`chat_type` |
| `member_impression` | `user_id` 主键 | 每人印象：`summary`/`traits_seen`/`habits`/`version`/`updated_at` |
| `reflect_report` | `id` 自增 | 周期报告：`kind`/`period_start`/`period_end`/`content`/`metrics`/`created_at` |
| `gags` | `id` 自增，`text` 唯一 | 热梗库：`source_title`/`bvid`/`likes`/`kind`/`enabled`/`harvested_at` |
| `web_videos` | `bvid` 主键 | 见过的视频：`title`/`author`/`pic`/`play`/`url`/`first_seen` |
| `web_lookup` | `id` 自增 | 查词缓存：`keyword`/`results`/`summary`/`created_at` |
| `learned_gag` | `id` 自增，`keyword` 唯一 | 自动学到的新词：`meaning`/`confidence`/`source`/`raw`/`created_at`/`updated_at` |
| `intimate_context` | `id` 自增 | 亲密模式上下文：`user_id`/`mode`/`is_private`/`started_at`/`ended_at`/`events` |
| `intimate_profile` | `user_id` 主键 | 偏好档案：`likes`/`dislikes`/`boundaries`/`summary`/`sessions_seen`/`updated_at` |
| `open_threads` | `id` 自增 | 没说完的话题：`user_id`/`chat_type`/`text`/`due_at`/`status`/`created_at`/`surfaced_at` |
| `traits` | `dim` 主键 | 八维性格：`value`/`baseline`/`min_value`/`max_value` |
| `traits_log` | `id` 自增 | 性格变动：`dim`/`before`/`after`/`cause` |
| `life_state` | `id` 且 `CHECK (id = 1)` | 当下状态：`energy`/`mood`/`focus`/`social_battery`/`activity`/`last_tick` |
| `kv` | `key` 主键 | 全仓开关与小状态：`value` |
| `continuity_log` | `id` 自增 | 变更账本：`kind`/`detail`/`source`/`meta` |
| `thought_surface` | `id` 自增，`thought_id` 唯一 | 念头浮现台账：`user_id`/`chat_type`/`ts` |
| `ref_turns` | `id` 自增 | 参考语料：`role`/`text`/`period`/`source` |
| `usage` | `id` 自增 | 模型记账：`session_seq`/`model`/`task`/`tokens_in`/`tokens_out`/`cost` |
| `memory_merges` | `id` 自增 | 记忆合并：`summary_id`/`source_ids`/`sources` |
| `anchors` | `id` 自增，`text` 唯一 | 语气样本池：`scene`/`stage_min`/`explicit`/`profane`/`freq`/`enabled` |
| `ref_quotes` | `id` 自增，`text` 唯一 | 原话池：`freq`/`period` |
| `friend_request` | `id` 自增 | 好友申请：`seq`/`flag`/`openid`/`nickname`/`comment`/`source`/`status`/`note`/`created_at`/`updated_at` |

**索引（`_SCHEMA` 里 17 个，`_migrate` 里再补 5 个）**：

| 索引 | 表与列 |
|---|---|
| `idx_lover_active` | `lover(session_seq, status)` |
| `idx_lover_user` | `lover(user_id)` |
| `idx_human_notes_day` | `human_notes(day)` |
| `idx_human_notes_topic` | `human_notes(topic)` |
| `idx_reflect_kind` | `reflect_report(kind, created_at)` |
| `idx_gags_likes` | `gags(enabled, likes)` |
| `idx_gags_text`（唯一） | `gags(text)` |
| `idx_web_lookup_kw` | `web_lookup(keyword, created_at)` |
| `idx_learned_conf` | `learned_gag(confidence DESC)` |
| `idx_intimate_user` | `intimate_context(user_id, ended_at)` |
| `idx_threads_due` | `open_threads(user_id, status, due_at)` |
| `idx_continuity_ts` | `continuity_log(ts DESC)` |
| `idx_thought_surface_once`（唯一） | `thought_surface(thought_id)` |
| `idx_thought_surface_time` | `thought_surface(ts DESC)` |
| `idx_ref_period` | `ref_turns(period)` |
| `idx_usage_seq` | `usage(session_seq, ts)` |
| `idx_friend_request_status` | `friend_request(status, id)` |
| `idx_events_user` | `events(user_id)`（`_migrate`） |
| `idx_events_chat` | `events(user_id, chat_type)`（`_migrate`） |
| `idx_memories_heat` | `memories(pinned, heat)`（`_migrate`，仅当 `memories` 还是表） |
| `idx_memories_user` | `memories(user_id)`（`_migrate`，同上） |
| `idx_memories_chat` | `memories(user_id, chat_type)`（`_migrate`，同上） |

**逐列探测式迁移的实现方式**（`_migrate`，第 375–498 行）：

1. 用 `_table_kind(conn, "<表名>")` 判断目标是 `table`、`view` 还是不存在；`memories` 只有"还是表"（升级前的旧库）才继续处理，引擎接管后它是只读视图，一律跳过。
2. 用 `PRAGMA table_info(<表名>)` 把现有列名收成集合：`cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}`（`row[1]` 是列名，`row[0]` 是序号）。
3. 对每个候选列执行一次 `if "<列名>" not in cols: conn.execute("ALTER TABLE ... ADD COLUMN ...")`。SQLite 的 `ALTER TABLE ADD COLUMN` 不能带非恒定默认值，所以这里所有新列都给了字面量默认值（`0`/`''`/`'[]'`/`0.9`）。
4. 索引用 `CREATE INDEX IF NOT EXISTS`，无条件执行——重复跑不报错，所以不需要探测。
5. `_migrate` 末尾还有 6 段 `CREATE TABLE IF NOT EXISTS`（`session`/`usage`/`memory_merges`/`anchors`/`continuity_log`/`thought_surface`），给"表还没进过 `_SCHEMA`"的老库补表；新库走 `_SCHEMA` 就够，这里是冗余但幂等。

具体补的列：`memories` 的 `heat REAL NOT NULL DEFAULT 0.9`、`pinned INTEGER NOT NULL DEFAULT 0`、`cues TEXT NOT NULL DEFAULT '[]'`、`last_touch TEXT`、`user_id INTEGER NOT NULL DEFAULT 0`、`chat_type TEXT NOT NULL DEFAULT ''`；`events` 的 `user_id INTEGER NOT NULL DEFAULT 0`、`chat_type TEXT NOT NULL DEFAULT ''`、`group_id TEXT NOT NULL DEFAULT ''`；`relation` 的 `aversion REAL NOT NULL DEFAULT 0`；`human_notes` 的 `chat_type TEXT NOT NULL DEFAULT ''`；`qq_user` 的 `blocked INTEGER NOT NULL DEFAULT 0`、`blocked_reason TEXT NOT NULL DEFAULT ''`；`summaries` 的 `session_seq INTEGER NOT NULL DEFAULT 1`。

**`memories` 视图何时创建**：`db.init()` 最后一行 `memv2.ensure_ready()`。它先探测 `memories` 的类型与 `mem_item` 是否存在，若 `kind == "view" and engine_ready and not force` 就直接返回（`{"ok": True, "state": "ready"}`）；否则打开引擎 Store（引擎迁移在这里跑），如果旧 `memories` 还是表就把行搬进引擎、`ALTER TABLE memories RENAME TO memories_legacy_20261030`（重名时依次 `_2`、`_3`），最后 `CREATE VIEW memories AS SELECT ... FROM mem_item WHERE forgotten = 0 AND superseded_by IS NULL AND merged_into IS NULL`。所以：**只在首次启动或首次升级时建视图，之后每次启动都是探测即返回**。视图的列是 `id`/`kind`/`content`/`importance`/`stability`/`source_ids`/`first_seen`/`last_seen`/`recall_count`/`last_recall`/`heat`/`pinned`/`cues`/`last_touch`/`user_id`/`chat_type`/`clarity`。

**坑**：

- `rows()`/`execute()` 每次调用都新开一个连接（`cursor()` 里 `connect()` → 用完 `close()`），没有连接池。一轮对话里几十次查询就是几十次 `open/close` + 两次 PRAGMA，这是"单机够用、并发上不去"的根因。
- `execute()` 的锁重试只匹配 `sqlite3.OperationalError` 且消息里含 `locked`，重试间隔 `0.15 * (attempt + 1)` 秒，共 3 次（0.15/0.30）；第 3 次仍失败就抛出，由调用方决定是否吞掉。
- `_migrate` 里 `event_cols` 只在开头取了一次快照，之后补完 `user_id` 仍用旧集合判断 `chat_type`/`group_id`。因为老库这三个列同时缺失所以逻辑正确，但如果将来有人只补其中一个，判断会失真。
- `_DB_PATH` 是模块级全局：同一进程里第二次 `db.init(other_path)` 会**静默换库**，已打开的连接不受影响，之后所有新连接指向新库。
- `db.py` 在函数内部才 `from app.memory import memv2`，就是为了避开 `db → memv2 → memengine → config` 的导入环；把这个 import 提到文件头会直接循环导入。

### 3.4 `app/core/runtime.py`

**职责**：进程内唯一的对象图。所有入口（面板 `/v1/chat`、OneBot、QQ 官方 bot）都通过 `runtime.get()` 拿同一份 `rt`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `Runtime.__init__` | `__init__(self) -> None` | 按固定顺序构造全部运行期对象 |
| `Runtime.apply_provider` | `apply_provider(self, base_url: str, api_key: str, model: str) -> None` | 改 API 三项并重建 `self.llm`，不重启生效 |
| `Runtime.reload_persona` | `reload_persona(self) -> None` | 重读人设文件、刷新 `default_name`、重跑 `engine.init_traits()` |
| `init` | `init() -> Runtime` | 首次调用构造并缓存，之后返回同一个 |
| `get` | `get() -> Runtime` | `_runtime is None` 时转调 `init()` |

**`Runtime` 上挂的对象（全列）**：

| 属性 | 类型 | 谁写 | 谁读 |
|---|---|---|---|
| `cfg` | `Config` | `config_mod.get()` | 全仓（所有阈值都从这里取） |
| `default_name` | `str` | 先 `"小涵"`，再 `self.persona.name` | `session_mod.her_name()`、`session_payload()` |
| `persona` | `Persona` | `persona_load.load(cfg.persona_file)`；`reload_persona()` 可换 | `app/mind/loop.py`、`prompt.build()` |
| `preflight` | `dict` | `preflight.check(self.cfg)` | `/v1/health` |
| `llm` | `BaseLLM` | `llm_mod.build(cfg, persona)`；`apply_provider()` 可换 | `loop.chat_turn()`、`judge.judge()`、摘要/信件 |
| `last_prompt` | `dict` | 每轮主链末尾整体赋值（`loop.py:888`） | `GET /v1/inspect`、`/v1/debug/*` |
| `last_meta` | `dict` | 每轮主链末尾整体赋值（`loop.py:908`） | `/v1/*` 诊断、`app/qa/duel.py` |

`Runtime` 上**没有** `loop` 属性：主链是模块函数 `loop.chat_turn(rt, ...)`，`rt` 只作为参数被传进去。`rt` 上真正被主链读的只有 `cfg`、`persona`、`default_name`、`llm`，被主链写的只有 `last_prompt` 与 `last_meta`。

**`__init__` 的固定顺序（第 12–47 行）**：

1. `self.cfg = config_mod.get()`（三层合并在这里冻结）。
2. `self.persona = persona_load.load(self.cfg.persona_file)`；`default_name` 取 `persona.name`。
3. `db.init(self.cfg.db_file)`——建库、补列、建视图（`memv2.ensure_ready()`）。
4. `engine.init_traits(self.persona)`——把人物包的八维基线写进 `traits` 表。
5. `preflight.check()`；`ok` 为假时 `logging.getLogger("symemory").warning("provider 预检未通过：%s", ...)`。
6. 真实 provider 且 `kv["human_life_enabled"]` 为空时写 `"1"`（mock 下保持默认关，不动测试基线）；整段 `try/except` 吞异常。
7. `users_mod.purge_self(str(cfg.onebot.self_id))`——把她自己的小号从群成员表里清掉（否则她会把自己当群友）；同样吞异常。
8. `kv["context_mode"]` 是 `"budget"`/`"ratio"` 时覆盖 `cfg.context.mode`（面板改过的策略重启后仍生效）。
9. `self.llm = llm_mod.build(self.cfg, self.persona)`。
10. `self.last_prompt = {}`、`self.last_meta = {}`。

**为什么是单进程单例**：

- `db._DB_PATH`、`memengine._store`、`config._cached` 全是模块级全局，第二个实例会和第一个抢同一份库与同一个引擎单例，换库/换人设时互相踩。
- 一个进程 = 一份灵魂：`session` 表被 `CHECK (id = 1)` 锁成单行，`relation` 同理，`life_state` 同理；多实例没有语义。
- `last_prompt`/`last_meta` 是**单槽**设计，`app/qa/duel.py` 的注释写明了这个前提：主链只在走完时更新它，早退分支不更新，读的时候要靠"最后一条 user 消息是否对得上"判断快照是不是本轮。

**坑**：

- `reload_persona()` 只重建 `persona` 与 `traits`，**不重建 `rt.llm`**。`MockLLM` 在 `__init__` 时就把 `persona._speech["anchors"]` 拷成了 `self.pool`，所以 mock 模式下换人设后回复仍来自旧人物包的原话池；真实 provider 没有这个缓存，因为它们每轮现拼 prompt。
- `apply_provider()` 只写 `cfg.provider` 的三项，`force_model`/`temperature`/`max_tokens` 不会被这个入口改到。
- 第 6、7 步的异常被整体吞掉，所以"human_life 开关没自动打开"这类问题不会出现在启动日志里。

### 3.5 `app/core/memengine.py`

**职责**：`vendor/symvolve_memory` 是只读的 vendored 引擎，本模块是它在本项目的唯一入口：插 `sys.path`、按配置打开**同一个** `data/life.db`、缓存单例。

**对外符号（模块里 10 个 def：`__all__` 导出 7 个，另有 `heal_legacy_migration_checksums` 与两个私有函数）**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `engine_path` | `engine_path() -> pathlib.Path` | 返回 `config.get().db_file`，即项目主库 |
| `_migration_files` | `_migration_files() -> dict[int, pathlib.Path]` | 扫 `vendor/symvolve_memory/db/migrations/*.sql`，按文件名前缀数字建版本→路径表 |
| `heal_legacy_migration_checksums` | `heal_legacy_migration_checksums(path: pathlib.Path \| None = None) -> int` | 把老库 `schema_migrations` 里已知的旧 checksum 修正成当前文件值，返回修正行数 |
| `_open` | `_open() -> Store` | 路径变了就关旧库、跑 checksum 修复、`Store(path)` 跑引擎迁移、重建三个门面对象 |
| `store` | `store() -> Store` | 取引擎 `Store` 单例 |
| `lifecycle` | `lifecycle() -> MemoryLifecycle` | 取生命周期门面（加温/遗忘/合并） |
| `recall` | `recall() -> MemoryRecall` | 取召回门面 |
| `renderer` | `renderer() -> MemoryRenderer` | 取渲染门面 |
| `stats` | `stats() -> dict[str, Any]` | 从 `store().stats()` 里挑 8 个键返回 |
| `reset` | `reset() -> None` | 关掉单例，下次调用重新开库 |

**桥接方式**（第 25–34 行）：

```python
_VENDOR = RESOURCE_ROOT / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from symvolve_memory import MemoryLifecycle, MemoryRecall, MemoryRenderer
from symvolve_memory.db.repo import Store
```

`sys.path.insert(0, ...)` 放在模块顶层，所以**任何 `import app.core.memengine` 或 `import app.memory.h2` 的进程**在导入期就把 vendor 挂上了；`h2.py` 的注释也写明它依赖这个顺序（`memengine` 必须先于 `symvolve_memory` 导入）。

**全局单例**：`_LOCK = threading.RLock()`、`_store`、`_life`、`_recall`、`_renderer`、`_path` 六个模块级变量。`_open()` 在锁内判断 `_store is not None and _path == want`：命中直接返回，路径变了先 `_store.close()` 再重开。三个门面对象用同一个 `Store` 构造，`MemoryRecall(store, lifecycle=_life)`、`MemoryRenderer(store, recall=_recall, lifecycle=_life)`。

**checksum 修复**（第 43–55、74–113 行）：常量 `_KNOWN_CHECKSUM_REWRITES = {1: {"b5496e7e...ed113": "b264a875...498a1"}}`。2026-10-31 上游把 `0001_init.sql` 的行尾从 CRLF 归一成 LF，字节数 4243 → 4151，内容逐字相同但 SHA256 变了，引擎会按 checksum 拦下并抛 `MigrationError`。本函数在 `_open()` 里、`Store(want)` 之前调用，只做 `UPDATE schema_migrations SET checksum = ? WHERE version = ? AND checksum = ?`，且必须同时满足"库里存的是已知旧值"和"当前文件 sha256 等于已知新值"两个条件，否则 `continue` 不动手。

**`stats()` 保留的 8 个键**：`schema_version`、`events`、`items`、`items_active`、`cues`、`recall_logs`、`distinct_users`、`db_bytes`。

**坑**：

- `heal_legacy_migration_checksums` 不在 `__all__` 里，按 `from app.core.memengine import *` 拿不到，必须显式导入。
- `_open()` 的路径切换是"关旧开新"，但 `h2.py`/`companion.py` 等模块会缓存 `Store` 返回的 `MemoryItem` 之类的对象；换库后这些对象仍属于旧库，不能拿回新库写。
- `Store(want)` 会跑引擎迁移，所以"打开记忆引擎"这一步本身有 DDL 副作用；`db.init()` 在关掉自己的连接之后才走到这里，就是为了避免两个连接同时改 schema。

### 3.6 `app/core/continuity.py`

**职责**：连续性账本。她可以被改变，但改变必须留一条痕；这些行不是运维日志，是日后"她想起自己变过"的素材。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `record` | `record(kind: str, detail: str = "", source: str = "", meta: dict[str, Any] \| None = None) -> int` | 写一条，返回 `continuity_log.id`；失败返回 `0` 且不抛 |
| `recent` | `recent(limit: int = 20, kinds: tuple[str, ...] \| None = None) -> list[dict[str, Any]]` | 倒序取，`meta` 解析成 dict，补一个中文 `label` |
| `count` | `count() -> int` | 总行数 |
| `summary_text` | `summary_text(limit: int = 3) -> str` | 只取 `MAJOR_KINDS`，把 label 去重后用「；」拼成一句 |
| `status` | `status() -> dict[str, Any]` | `{"count", "recent"(10 条), "major"}` |

**`record()` 写进 `continuity_log` 的字段**：

| 列 | 来源 | 处理 |
|---|---|---|
| `ts` | `datetime.now().astimezone().isoformat(timespec="seconds")` | 带本地时区偏移，精确到秒 |
| `kind` | 第 1 个参数 | `str(kind or "unknown")[:40]`，截断 40 字符，不做白名单校验 |
| `detail` | 第 2 个参数 | `str(detail or "")[:500]` |
| `source` | 第 3 个参数 | `str(source or "")[:40]` |
| `meta` | 第 4 个参数 | `json.dumps(meta or {}, ensure_ascii=False)` |

整个 `db.execute` 包在 `try/except Exception` 里，失败只 `return 0`——记账是旁路，不许把主流程搞挂。

**种类与中文标签（7 种）**：`_KIND_LABEL` = `persona_changed`→"她的样子变了"、`model_changed`→"她说话的来路变了"、`memory_forgotten`→"她放下了一些记得的事"、`session_reset`→"她重新开始了一段"、`soul_imported`→"她被整个搬到了别处又搬回来"、`person_forgotten`→"她忘了某个人"、`relation_reset`→"她对你的感觉被重置了"。`MAJOR_KINDS` 只有 5 个：`persona_changed`、`model_changed`、`memory_forgotten`、`session_reset`、`soul_imported`（`person_forgotten` 与 `relation_reset` 不入"重大变化"摘要）。

**必须调用它的 8 个调用点（7 种 kind）**：

| 调用点 | kind | 触发条件 |
|---|---|---|
| `app/api/console.py:93` | `memory_forgotten` | 控制台按人清记忆 `h2.forget_user(user_id)` 且删除数 > 0 |
| `app/api/console.py:388` | `person_forgotten` | 控制台"忘掉一个人"：删他的 events/关系/恋人/印象/观察/亲密档案 |
| `app/api/routes.py:347` | `session_reset` | 开发者清空会话（events/relation_log/traits_log/summaries + `relation` 归零 + `h2.forget_all()`） |
| `app/api/routes.py:428` | `memory_forgotten` | `POST /v1/dev/memory/clear`（密码校验通过后清全部记忆） |
| `app/api/routes.py:441` | `relation_reset` | `POST /v1/dev/relation/reset`（关系与厌恶归零） |
| `app/api/routes.py:643` | `model_changed` | 设置页保存后 `model` 或 `base_url` 与保存前不同 |
| `app/api/routes.py:1025` | `persona_changed` | 切换人设后 `name` 或 `key` 与切换前不同 |
| `app/persona/transfer.py:136` | `soul_imported` | 导入灵魂档案成功后 |

**坑**：

- `kind` 不做白名单校验，拼错一个字符串不会被拦，只是 `summary_text()` 永远不会把它算进"重大变化"。
- 面板展示用的是 `count()` + `summary_text(4)` + `recent(limit)`（`app/api/console.py:624-626`），没有分页，`limit` 由调用方给。
- `summary_text` 的 `limit` 同时限制"条数"和"label 个数"，重复 label 会被去重，所以 3 条同 kind 的记录只出一句。

### 3.7 `app/core/preflight.py`

**职责**：启动时零网络、零 token 地判断 `base_url` 与 `model`/`force_model` 是否同源。存在的理由写在文件头：`base_url=https://api.deepseek.com/v1` 配 `force_model=deepseek-ai/DeepSeek-V4.1-Flash` 会让官方 API 直接 400，而 `loop.py` 的兜底会把异常吞掉改说"先去吃口饭"，40 条回复全是罐头，面板上看起来只是"她在忙"。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `check` | `check(cfg: Any) -> dict[str, Any]` | 返回 `{ok, base_url, model, force_model, warnings, checked_at}`，不抛 |

**常量**：`_DEEPSEEK_OFFICIAL = ("deepseek-flash", "deepseek-chat", "deepseek-reasoner")`。

**判定规则**（`_judge(name, where)`）：

1. `name` 为空直接返回，不产生警告。
2. `base` 转小写后含 `"api.deepseek.com"`：`name` 里出现 `"/"` 或不在 `_DEEPSEEK_OFFICIAL` 里 → 警告「`{where}={name}` 不是 DeepSeek 官方模型名（官方只认 …）」。
3. `base` 含 `"siliconflow"`：`name` 里没有 `"/"` → 警告「不像硅基流动的模型名（通常形如 `deepseek-ai/DeepSeek-V4.1-Flash`）」。
4. 其它 base（自建中转、OpenAI 兼容网关）不判，`ok` 保持真。
5. 先判 `model`，再判 `force_model`——只有 `force` 非空且 `force != model` 时才判它。
6. `ok = not warnings`，`checked_at` 取本地时区 ISO 秒。

**调用点**：`Runtime.__init__` 第 22 行存进 `rt.preflight`；`ok` 为假时写一条 `WARNING`；路由 `/v1/health` 把这份 dict 透出去。

**坑**：只认 DeepSeek 官方与硅基流动两家命名，其余 base 一律放行；`model` 与 `force_model` 相同时只判一次，所以"两个都错但错得一样"只会出一条警告。

### 3.8 `app/core/channel.py`

**职责**：通道能力位。同一套内核，面板 / OneBot / QQ 官方 bot 能做的事不一样；适配器启动时声明自己是谁，内核与面板只读能力。

**`Caps`（`@dataclass(frozen=True)`，8 个字段）**：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `name` | `"panel"` | 通道名 |
| `stickers` | `False` | 能否发表情包 |
| `voice` | `False` | 能否发语音（record 段） |
| `images` | `False` | 能否发图 |
| `group_proactive` | `False` | 群里能否主动开口 |
| `private_proactive` | `False` | 私聊能否主动开口 |
| `max_reply_chars` | `4000` | 单条消息上限（超出会切段） |
| `can_split` | `True` | 是否允许多条气泡 |

**三种预设（真实数值）**：

| 预设 | `name` | `stickers` | `voice` | `images` | `group_proactive` | `private_proactive` | `max_reply_chars` | `can_split` |
|---|---|---|---|---|---|---|---|---|
| `PANEL` | `"panel"` | `True` | `False` | `True` | `True` | `True` | `4000` | `True`（默认值） |
| `ONEBOT` | `"onebot"` | `True` | `True` | `True` | `True` | `True` | `1500` | `True`（默认值） |
| `QQ_OFFICIAL` | `"qq_official"` | `False` | `False` | `True` | `False` | `True` | `1500` | `False` |

`_PRESETS = {"panel": PANEL, "onebot": ONEBOT, "qq_official": QQ_OFFICIAL}`，`_current` 初值是 `PANEL`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `set_channel` | `set_channel(name: str) -> Caps` | `_current = _PRESETS.get(str(name or ""), PANEL)`，返回新值 |
| `caps` | `caps() -> Caps` | 返回当前能力位 |
| `as_dict` | `as_dict() -> dict[str, Any]` | `dataclasses.asdict(_current)` |

**`set_channel()` 的语义**：查表命中就换，**未知名与空串一律回落到 `PANEL`**（不是报错、不是保持原值）。因为 `_current` 是模块级全局，它描述的是"这个进程最近一次声明自己是谁"，不是"这条请求来自哪"。

**调用点**：`app/adapters/qq.py:250` 设 `"qq_official"`；`app/api/onebot.py:207` 设 `"onebot"`；`app/api/routes.py:152` 在 `POST /v1/chat` 开头设 `"panel"`；`app/api/onebot.py:396` 用 `channel.caps().can_split` 决定逐条发还是整段发；`app/api/routes.py:78` 把 `as_dict()` 塞进 board 响应。

**坑**：未知通道名回落 `PANEL` 意味着"能力只多不少"——真出现第四种通道而忘了加预设，它会拿到面板的全套能力（可发语音、可主动），而不是被降级拒绝。

## 4. 数据流

启动（`import app.main` → `create_app()`）：

```text
app.main
  └─ runtime.init()
       ├─ config.get() → load()
       │    ├─ RESOURCE_ROOT/config.yaml
       │    ├─ 第一个存在的 DATA_ROOT|RESOURCE_ROOT/config.local.yaml
       │    └─ _apply_env()  ← SYMEM_* / AI_LIFE_*
       ├─ persona.load(cfg.persona_file)          → Persona 对象
       ├─ db.init(cfg.db_file)
       │    ├─ connect()  (WAL + busy_timeout=15000)
       │    ├─ executescript(_SCHEMA)              30 表 + 17 索引
       │    ├─ INSERT OR IGNORE relation / life_state
       │    ├─ _migrate()                          逐列 PRAGMA 探测补列
       │    └─ memv2.ensure_ready()
       │         └─ memengine.store() → Store(life.db) 引擎迁移 → 建/探测 memories 视图
       ├─ engine.init_traits(persona)
       ├─ preflight.check(cfg)                    → rt.preflight
       ├─ kv: human_life_enabled（非 mock 且为空时写 "1"）
       ├─ users.purge_self(cfg.onebot.self_id)
       ├─ kv: context_mode → cfg.context.mode
       └─ llm.build(cfg, persona)                 → MockLLM | OpenAICompatLLM
```

一次请求进来时的通道声明：

```text
POST /v1/chat        → channel.set_channel("panel")     → loop.chat_turn(source="panel")
OneBot 上报          → channel.set_channel("onebot")    → loop.chat_turn(source="qq_onebot")
QQ 官方 bot          → channel.set_channel("qq_official") → loop.chat_turn(source=...)
```

## 5. 改这一层要注意

1. **加配置项**：`config.py` 里给对应 dataclass 加字段（带默认值）→ 需要写进 `config.yaml` 的就在文件里补 → 需要环境变量覆盖的就在 `_apply_env` 里补一行 → 读的地方一律 `cfg.<段>.<字段>`，不要自己读 YAML。漏了默认值会让所有没写这个键的 `config.yaml` 在构造时抛 `TypeError`。
2. **加表**：写进 `_SCHEMA`（新库直接建），同时在 `_migrate` 里补一段 `CREATE TABLE IF NOT EXISTS`（老库不会重跑 `_SCHEMA` 里已存在表的列，但补表能靠 `_SCHEMA` 每次启动的 `executescript` 生效；真正必须写进 `_migrate` 的是**给已存在的表加列**）。
3. **给已有表加列**：必须走 `_migrate` 的"`PRAGMA table_info` 收列名 → `if 列名 not in cols: ALTER TABLE ... ADD COLUMN`"模式，并且给字面量默认值；只改 `_SCHEMA` 对存量库无效。
4. **换库路径**：`store.db_path` 或 `SYMEM_DB` 改了之后，引擎也走同一个文件（`memengine.engine_path()` 就是 `config.get().db_file`），一份库包含聊天、关系、记忆、账本；换路径等于换一份灵魂档案。
5. **加一种通道**：在 `channel.py` 加 `Caps(...)` 常量并登记进 `_PRESETS`，适配器启动时 `set_channel("<名>")`；不加登记会静默拿到 `PANEL` 的能力。
6. **加一种变更**：`continuity.record("<kind>", ...)`，同时考虑要不要加进 `_KIND_LABEL` 与 `MAJOR_KINDS`；不加 label 的面板会显示原始英文 kind。
7. **改 `Runtime` 顺序**：`db.init()` 必须在 `engine.init_traits()` 之前（traits 表要先存在）；`llm.build()` 必须在 `persona` 之后（`MockLLM` 要用人物包的原话池）；`preflight` 要在 `llm.build()` 之前，否则预检失败也已经开始用了。

## 6. 交叉引用

- docs/05（对话主链）：`chat_turn()` 是本层所有设施的消费者——`rt.cfg` 的阈值、`rt.llm` 的调用、`db` 的读写、`continuity` 的旁路约定都在那一章展开。
- docs/49（记忆引擎）：`memories` 视图、引擎表 `mem_item`/`mem_cue`/`mem_recall_log`、`memories_legacy_20261030` 归档表的来龙去脉。
- docs/51（连续性）：`continuity_log` 为什么不是日志而是自传素材。
- docs/62（改名口径）：`SYMEM_*` 与 `AI_LIFE_*` 双名的对外说明。
- docs/20（QQ 群多用户）、docs/22（群聊进化）：`qq_user`/`user_relation`/`lover`/`human_notes`/`member_impression`/`reflect_report` 的写入方。
- docs/25（外部网络内容）、docs/27（联网查词）、docs/29（亲密模式）：`gags`/`web_videos`/`web_lookup`/`learned_gag`/`intimate_context`/`intimate_profile` 的写入方。
