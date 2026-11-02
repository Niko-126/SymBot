# 17 · 数据模型全量（`data/life.db`）

> 覆盖：**37 张业务表 + 1 个只读视图 `memories`**｜其中 **7 张**由 `vendor/symvolve_memory` 的迁移脚本建（`mem_item` / `mem_cue` / `mem_event` / `mem_recall_log` / `mem_render_cache` / `mem_embedding` / `schema_migrations`），**30 张**由 `app/core/db.py` 建＋逐列探测补列。
>
> **口径说明**：`sqlite_master` 里 `type='table'` 共 38 条，其中 `sqlite_sequence` 是 SQLite 给 AUTOINCREMENT 记账的内部表，不属于业务模型；本章把它单列在「其他」，**不计入 37 张业务表**。
>
> 所有 DDL 与列信息直接取自 `sqlite_master`；行数是生成当刻对真库只读打开的快照（运行时会变，别拿它当断言）。

## 1. 总览

单个 SQLite 文件（WAL 模式），没有 ORM，没有统一 `schema_version`。分四组：

| 分组 | 表 |
|---|---|
| 会话/关系 | `friend_request` `lover` `member_impression` `qq_user` `relation` `relation_log` `session` `traits` `traits_log` `user_relation` |
| 记忆 | `mem_cue` `mem_embedding` `mem_event` `mem_item` `mem_recall_log` `mem_render_cache` `memories` |
| 生活/内容 | `anchors` `continuity_log` `events` `gags` `human_notes` `intimate_context` `intimate_profile` `kv` `learned_gag` `life_state` `memory_merges` `open_threads` `ref_quotes` `ref_turns` `reflect_report` `summaries` `thought_surface` `usage` `web_lookup` `web_videos` |
| 系统 | `schema_migrations` |
| 其他 | `sqlite_sequence` |

### 1.1 当前真库行数

| # | 表/视图 | 类型 | 分组 | 当前行数 |
|---|---|---|---|---|
| 1 | `anchors` | 表 | 生活/内容 | 0 |
| 2 | `continuity_log` | 表 | 生活/内容 | 3 |
| 3 | `events` | 表 | 生活/内容 | 4 |
| 4 | `friend_request` | 表 | 会话/关系 | 0 |
| 5 | `gags` | 表 | 生活/内容 | 503 |
| 6 | `human_notes` | 表 | 生活/内容 | 0 |
| 7 | `intimate_context` | 表 | 生活/内容 | 0 |
| 8 | `intimate_profile` | 表 | 生活/内容 | 0 |
| 9 | `kv` | 表 | 生活/内容 | 47 |
| 10 | `learned_gag` | 表 | 生活/内容 | 0 |
| 11 | `life_state` | 表 | 生活/内容 | 1 |
| 12 | `lover` | 表 | 会话/关系 | 0 |
| 13 | `mem_cue` | 表 | 记忆 | 297 |
| 14 | `mem_embedding` | 表 | 记忆 | 0 |
| 15 | `mem_event` | 表 | 记忆 | 33 |
| 16 | `mem_item` | 表 | 记忆 | 33 |
| 17 | `mem_recall_log` | 表 | 记忆 | 234 |
| 18 | `mem_render_cache` | 表 | 记忆 | 9 |
| 19 | `member_impression` | 表 | 会话/关系 | 0 |
| 20 | `memory_merges` | 表 | 生活/内容 | 0 |
| 21 | `open_threads` | 表 | 生活/内容 | 26 |
| 22 | `qq_user` | 表 | 会话/关系 | 0 |
| 23 | `ref_quotes` | 表 | 生活/内容 | 0 |
| 24 | `ref_turns` | 表 | 生活/内容 | 0 |
| 25 | `reflect_report` | 表 | 生活/内容 | 3 |
| 26 | `relation` | 表 | 会话/关系 | 1 |
| 27 | `relation_log` | 表 | 会话/关系 | 210 |
| 28 | `schema_migrations` | 表 | 系统 | 2 |
| 29 | `session` | 表 | 会话/关系 | 1 |
| 30 | `sqlite_sequence` | 表 | 其他 | 17 |
| 31 | `summaries` | 表 | 生活/内容 | 1 |
| 32 | `thought_surface` | 表 | 生活/内容 | 0 |
| 33 | `traits` | 表 | 会话/关系 | 8 |
| 34 | `traits_log` | 表 | 会话/关系 | 0 |
| 35 | `usage` | 表 | 生活/内容 | 461 |
| 36 | `user_relation` | 表 | 会话/关系 | 0 |
| 37 | `web_lookup` | 表 | 生活/内容 | 0 |
| 38 | `web_videos` | 表 | 生活/内容 | 50 |
| 39 | `memories` | 视图 | 记忆 | 1 |

## 2. 逐表 DDL 与列说明

### 2.1 `anchors`（表｜生活/内容｜0 行）

锚点池：从真实语料抽出的「她会说的话」，按场景分层。

```sql
CREATE TABLE anchors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL UNIQUE,
  scene TEXT NOT NULL DEFAULT '日常',
  stage_min INTEGER NOT NULL DEFAULT 0,
  explicit INTEGER NOT NULL DEFAULT 0,
  profane INTEGER NOT NULL DEFAULT 0,
  freq INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `text` | TEXT | 是 | — | 否 |
| 2 | `scene` | TEXT | 是 | '日常' | 否 |
| 3 | `stage_min` | INTEGER | 是 | 0 | 否 |
| 4 | `explicit` | INTEGER | 是 | 0 | 否 |
| 5 | `profane` | INTEGER | 是 | 0 | 否 |
| 6 | `freq` | INTEGER | 是 | 1 | 否 |
| 7 | `enabled` | INTEGER | 是 | 1 | 否 |

### 2.2 `continuity_log`（表｜生活/内容｜3 行）

**连续性账本**：她可以被改变，但每次改变必须留痕（谁、什么 source、什么 meta）。

```sql
CREATE TABLE continuity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}'
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `kind` | TEXT | 是 | — | 否 |
| 3 | `detail` | TEXT | 是 | '' | 否 |
| 4 | `source` | TEXT | 是 | '' | 否 |
| 5 | `meta` | TEXT | 是 | '{}' | 否 |

索引：

- `idx_continuity_ts`：`CREATE INDEX idx_continuity_ts ON continuity_log(ts DESC)`

### 2.3 `events`（表｜生活/内容｜4 行）

对话事件流：`role` user/her、`text`、`source`（panel/qq_c2c/qq_group/onebot）、`user_id`、`chat_type`。她的话也落这里，上下文靠它连续。

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'panel',
  meta TEXT NOT NULL DEFAULT '{}'
, user_id INTEGER NOT NULL DEFAULT 0, chat_type TEXT NOT NULL DEFAULT '', group_id TEXT NOT NULL DEFAULT '')
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `role` | TEXT | 是 | — | 否 |
| 3 | `kind` | TEXT | 是 | 'text' | 否 |
| 4 | `text` | TEXT | 是 | — | 否 |
| 5 | `source` | TEXT | 是 | 'panel' | 否 |
| 6 | `meta` | TEXT | 是 | '{}' | 否 |
| 7 | `user_id` | INTEGER | 是 | 0 | 否 |
| 8 | `chat_type` | TEXT | 是 | '' | 否 |
| 9 | `group_id` | TEXT | 是 | '' | 否 |

索引：

- `idx_events_chat`：`CREATE INDEX idx_events_chat ON events(user_id, chat_type)`
- `idx_events_user`：`CREATE INDEX idx_events_user ON events(user_id)`

### 2.4 `friend_request`（表｜会话/关系｜0 行）

好友申请记录：谁申请的、批没批、什么时候。

```sql
CREATE TABLE friend_request (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seq INTEGER NOT NULL DEFAULT 0,
  flag TEXT NOT NULL DEFAULT '',
  openid TEXT NOT NULL DEFAULT '',
  nickname TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `seq` | INTEGER | 是 | 0 | 否 |
| 2 | `flag` | TEXT | 是 | '' | 否 |
| 3 | `openid` | TEXT | 是 | '' | 否 |
| 4 | `nickname` | TEXT | 是 | '' | 否 |
| 5 | `comment` | TEXT | 是 | '' | 否 |
| 6 | `source` | TEXT | 是 | '' | 否 |
| 7 | `status` | TEXT | 是 | 'pending' | 否 |
| 8 | `note` | TEXT | 是 | '' | 否 |
| 9 | `created_at` | TEXT | 是 | — | 否 |
| 10 | `updated_at` | TEXT | 是 | — | 否 |

索引：

- `idx_friend_request_status`：`CREATE INDEX idx_friend_request_status ON friend_request(status, id)`

### 2.5 `gags`（表｜生活/内容｜503 行）

热梗库：从 B 站评论/标题学来的网络热语，**独立存放、绝不进她的记忆**。

```sql
CREATE TABLE gags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  source_title TEXT NOT NULL DEFAULT '',
  bvid TEXT NOT NULL DEFAULT '',
  likes INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'comment',
  enabled INTEGER NOT NULL DEFAULT 1,
  harvested_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `text` | TEXT | 是 | — | 否 |
| 2 | `source_title` | TEXT | 是 | '' | 否 |
| 3 | `bvid` | TEXT | 是 | '' | 否 |
| 4 | `likes` | INTEGER | 是 | 0 | 否 |
| 5 | `kind` | TEXT | 是 | 'comment' | 否 |
| 6 | `enabled` | INTEGER | 是 | 1 | 否 |
| 7 | `harvested_at` | TEXT | 是 | — | 否 |

索引：

- `idx_gags_likes`：`CREATE INDEX idx_gags_likes ON gags(enabled, likes)`
- `idx_gags_text`：`CREATE UNIQUE INDEX idx_gags_text ON gags(text)`

### 2.6 `human_notes`（表｜生活/内容｜0 行）

人类日常观察笔记。

```sql
CREATE TABLE human_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  topic TEXT NOT NULL DEFAULT '习惯',
  content TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.6,
  user_id INTEGER,
  session_seq INTEGER NOT NULL DEFAULT 1,
  chat_type TEXT NOT NULL DEFAULT ''
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `day` | TEXT | 是 | — | 否 |
| 3 | `topic` | TEXT | 是 | '习惯' | 否 |
| 4 | `content` | TEXT | 是 | — | 否 |
| 5 | `confidence` | REAL | 是 | 0.6 | 否 |
| 6 | `user_id` | INTEGER | 否 | — | 否 |
| 7 | `session_seq` | INTEGER | 是 | 1 | 否 |
| 8 | `chat_type` | TEXT | 是 | '' | 否 |

索引：

- `idx_human_notes_day`：`CREATE INDEX idx_human_notes_day ON human_notes(day)`
- `idx_human_notes_topic`：`CREATE INDEX idx_human_notes_topic ON human_notes(topic)`

### 2.7 `intimate_context`（表｜生活/内容｜0 行）

亲密模式的上下文记录（与普通记忆物理隔离）。

```sql
CREATE TABLE intimate_context (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  mode TEXT NOT NULL DEFAULT 'suggestive',
  is_private INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  events TEXT NOT NULL DEFAULT '[]'
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | INTEGER | 是 | — | 否 |
| 2 | `mode` | TEXT | 是 | 'suggestive' | 否 |
| 3 | `is_private` | INTEGER | 是 | 1 | 否 |
| 4 | `started_at` | TEXT | 是 | — | 否 |
| 5 | `ended_at` | TEXT | 否 | — | 否 |
| 6 | `events` | TEXT | 是 | '[]' | 否 |

索引：

- `idx_intimate_user`：`CREATE INDEX idx_intimate_user ON intimate_context(user_id, ended_at)`

### 2.8 `intimate_profile`（表｜生活/内容｜0 行）

亲密画像：likes / dislikes / boundaries / summary，由 `POST /v1/console/intimate/<uid>/analyze` 生成，**不自动触发**。

```sql
CREATE TABLE intimate_profile (
  user_id INTEGER PRIMARY KEY,
  likes TEXT NOT NULL DEFAULT '[]',
  dislikes TEXT NOT NULL DEFAULT '[]',
  boundaries TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  sessions_seen INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `user_id` | INTEGER | 否 | — | 是 |
| 1 | `likes` | TEXT | 是 | '[]' | 否 |
| 2 | `dislikes` | TEXT | 是 | '[]' | 否 |
| 3 | `boundaries` | TEXT | 是 | '[]' | 否 |
| 4 | `summary` | TEXT | 是 | '' | 否 |
| 5 | `sessions_seen` | INTEGER | 是 | 0 | 否 |
| 6 | `updated_at` | TEXT | 是 | — | 否 |

### 2.9 `kv`（表｜生活/内容｜47 行）

运行时开关仓库：`awake_level`、`naming_step`、`intimate_mode:<uid>`、`nick:<uid>`、`group_silent_until[:<gid>]`、`group_focus_user[:<gid>]`、`group_last_proactive[:<gid>]`、`group_proactive[:<gid>]:<date>`、`proactive_dnd_until:<uid>` 等。

> **作用域后缀是 2026-11-01 加的**：`db.kv_key(key, gid)` 生成 `key:<gid>`，不传 gid 时仍用老的无后缀全局键（旧数据照常读）。群相关的静默/焦点/配额都改成了按群存，回归 D14 钉住。

```sql
CREATE TABLE kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `key` | TEXT | 否 | — | 是 |
| 1 | `value` | TEXT | 是 | — | 否 |

### 2.10 `learned_gag`（表｜生活/内容｜0 行）

她学过的梗及其使用记录。

```sql
CREATE TABLE learned_gag (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL UNIQUE,
  meaning TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'web',
  raw TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `keyword` | TEXT | 是 | — | 否 |
| 2 | `meaning` | TEXT | 是 | '' | 否 |
| 3 | `confidence` | REAL | 是 | 0 | 否 |
| 4 | `source` | TEXT | 是 | 'web' | 否 |
| 5 | `raw` | TEXT | 是 | '' | 否 |
| 6 | `created_at` | TEXT | 是 | — | 否 |
| 7 | `updated_at` | TEXT | 是 | — | 否 |

索引：

- `idx_learned_conf`：`CREATE INDEX idx_learned_conf ON learned_gag(confidence DESC)`

### 2.11 `life_state`（表｜生活/内容｜1 行）

她的生活状态快照（在做的事、精力、地点感）。

```sql
CREATE TABLE life_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  energy REAL NOT NULL DEFAULT 60,
  mood REAL NOT NULL DEFAULT 0,
  focus REAL NOT NULL DEFAULT 50,
  social_battery REAL NOT NULL DEFAULT 60,
  activity TEXT,
  last_tick TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `energy` | REAL | 是 | 60 | 否 |
| 2 | `mood` | REAL | 是 | 0 | 否 |
| 3 | `focus` | REAL | 是 | 50 | 否 |
| 4 | `social_battery` | REAL | 是 | 60 | 否 |
| 5 | `activity` | TEXT | 否 | — | 否 |
| 6 | `last_tick` | TEXT | 否 | — | 否 |

### 2.12 `lover`（表｜会话/关系｜0 行）

恋人绑定：`status` active/ended、`bound_at`、`end_reason`、`cooldown_until`（分手 7 天冷却）。唯一恋人由代码保证，不由唯一索引保证。

```sql
CREATE TABLE lover (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_seq INTEGER NOT NULL DEFAULT 1,
  user_id INTEGER NOT NULL REFERENCES qq_user(id),
  status TEXT NOT NULL DEFAULT 'active',
  bound_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT,
  cooldown_until TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `session_seq` | INTEGER | 是 | 1 | 否 |
| 2 | `user_id` | INTEGER | 是 | — | 否 |
| 3 | `status` | TEXT | 是 | 'active' | 否 |
| 4 | `bound_at` | TEXT | 是 | — | 否 |
| 5 | `ended_at` | TEXT | 否 | — | 否 |
| 6 | `end_reason` | TEXT | 否 | — | 否 |
| 7 | `cooldown_until` | TEXT | 否 | — | 否 |

索引：

- `idx_lover_active`：`CREATE INDEX idx_lover_active ON lover(session_seq, status)`
- `idx_lover_user`：`CREATE INDEX idx_lover_user ON lover(user_id)`

### 2.13 `mem_cue`（表｜记忆｜297 行）

记忆线索（cue）：一条记忆挂多个可召回的关键词/实体线索。

```sql
CREATE TABLE mem_cue (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL REFERENCES mem_item(id) ON DELETE CASCADE,
    cue      TEXT    NOT NULL,
    kind     TEXT    NOT NULL DEFAULT 'content',
    weight   REAL    NOT NULL DEFAULT 1.0,
    UNIQUE (item_id, cue)
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `item_id` | INTEGER | 是 | — | 否 |
| 2 | `cue` | TEXT | 是 | — | 否 |
| 3 | `kind` | TEXT | 是 | 'content' | 否 |
| 4 | `weight` | REAL | 是 | 1.0 | 否 |

索引：

- `idx_cue_item`：`CREATE INDEX idx_cue_item ON mem_cue(item_id)`
- `idx_cue_value`：`CREATE INDEX idx_cue_value ON mem_cue(cue)`

### 2.14 `mem_embedding`（表｜记忆｜0 行）

向量缓存（可选路径）。

```sql
CREATE TABLE mem_embedding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES mem_item(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL CHECK (dim > 0),
    vector BLOB NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE (item_id, model)
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `item_id` | INTEGER | 是 | — | 否 |
| 2 | `model` | TEXT | 是 | — | 否 |
| 3 | `dim` | INTEGER | 是 | — | 否 |
| 4 | `vector` | BLOB | 是 | — | 否 |
| 5 | `created_at` | INTEGER | 是 | — | 否 |

索引：

- `idx_embedding_item`：`CREATE INDEX idx_embedding_item ON mem_embedding(item_id)`
- `idx_embedding_model`：`CREATE INDEX idx_embedding_model ON mem_embedding(model)`

### 2.15 `mem_event`（表｜记忆｜33 行）

记忆事件原文（raw_text **永不进 prompt**），用于审计与重放。

```sql
CREATE TABLE mem_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    chat_type   TEXT    NOT NULL DEFAULT 'private',
    scope       TEXT    NOT NULL DEFAULT 'private',
    role        TEXT    NOT NULL DEFAULT 'user',
    raw_text    TEXT    NOT NULL,
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  INTEGER NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | TEXT | 是 | — | 否 |
| 2 | `chat_type` | TEXT | 是 | 'private' | 否 |
| 3 | `scope` | TEXT | 是 | 'private' | 否 |
| 4 | `role` | TEXT | 是 | 'user' | 否 |
| 5 | `raw_text` | TEXT | 是 | — | 否 |
| 6 | `meta_json` | TEXT | 是 | '{}' | 否 |
| 7 | `created_at` | INTEGER | 是 | — | 否 |

索引：

- `idx_event_chat_time`：`CREATE INDEX idx_event_chat_time ON mem_event(user_id, chat_type, created_at)`
- `idx_event_scope_time`：`CREATE INDEX idx_event_scope_time ON mem_event(user_id, scope, created_at)`
- `idx_event_user_time`：`CREATE INDEX idx_event_user_time ON mem_event(user_id, created_at)`

### 2.16 `mem_item`（表｜记忆｜33 行）

**记忆本体**。一条记忆一行：kind、文本、importance、heat、pinned、forgotten、superseded_by、merged_into、last_seen 等。`memories` 视图就是它的投影。

```sql
CREATE TABLE mem_item (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    NOT NULL,
    chat_type        TEXT    NOT NULL DEFAULT 'private',
    scope            TEXT    NOT NULL DEFAULT 'private',
    kind             TEXT    NOT NULL DEFAULT 'fact',
    title            TEXT    NOT NULL DEFAULT '',
    text             TEXT    NOT NULL,
    bot_reply        TEXT,
    importance       INTEGER NOT NULL DEFAULT 8 CHECK (importance BETWEEN 1 AND 100),
    clarity          TEXT    NOT NULL DEFAULT 'clear' CHECK (clarity IN ('vivid', 'clear', 'fuzzy', 'trace')),
    heat             REAL    NOT NULL DEFAULT 0.0 CHECK (heat >= 0.0 AND heat <= 1.0),
    pinned           INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    source_event_ids TEXT    NOT NULL DEFAULT '[]',
    superseded_by    INTEGER REFERENCES mem_item(id),
    superseded_at    INTEGER,
    merged_into      INTEGER REFERENCES mem_item(id),
    forgotten        INTEGER NOT NULL DEFAULT 0 CHECK (forgotten IN (0, 1)),
    created_at       INTEGER NOT NULL,
    last_seen        INTEGER NOT NULL,
    last_recall      INTEGER,
    recall_count     INTEGER NOT NULL DEFAULT 0,
    meta_json        TEXT    NOT NULL DEFAULT '{}'
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | TEXT | 是 | — | 否 |
| 2 | `chat_type` | TEXT | 是 | 'private' | 否 |
| 3 | `scope` | TEXT | 是 | 'private' | 否 |
| 4 | `kind` | TEXT | 是 | 'fact' | 否 |
| 5 | `title` | TEXT | 是 | '' | 否 |
| 6 | `text` | TEXT | 是 | — | 否 |
| 7 | `bot_reply` | TEXT | 否 | — | 否 |
| 8 | `importance` | INTEGER | 是 | 8 | 否 |
| 9 | `clarity` | TEXT | 是 | 'clear' | 否 |
| 10 | `heat` | REAL | 是 | 0.0 | 否 |
| 11 | `pinned` | INTEGER | 是 | 0 | 否 |
| 12 | `source_event_ids` | TEXT | 是 | '[]' | 否 |
| 13 | `superseded_by` | INTEGER | 否 | — | 否 |
| 14 | `superseded_at` | INTEGER | 否 | — | 否 |
| 15 | `merged_into` | INTEGER | 否 | — | 否 |
| 16 | `forgotten` | INTEGER | 是 | 0 | 否 |
| 17 | `created_at` | INTEGER | 是 | — | 否 |
| 18 | `last_seen` | INTEGER | 是 | — | 否 |
| 19 | `last_recall` | INTEGER | 否 | — | 否 |
| 20 | `recall_count` | INTEGER | 是 | 0 | 否 |
| 21 | `meta_json` | TEXT | 是 | '{}' | 否 |

索引：

- `idx_item_clarity`：`CREATE INDEX idx_item_clarity ON mem_item(user_id, clarity)`
- `idx_item_importance`：`CREATE INDEX idx_item_importance ON mem_item(user_id, importance DESC)`
- `idx_item_kind`：`CREATE INDEX idx_item_kind ON mem_item(user_id, kind)`
- `idx_item_pinned_heat`：`CREATE INDEX idx_item_pinned_heat ON mem_item(user_id, pinned DESC, heat DESC)`
- `idx_item_user_chat`：`CREATE INDEX idx_item_user_chat ON mem_item(user_id, chat_type, created_at DESC)`
- `idx_item_user_time`：`CREATE INDEX idx_item_user_time ON mem_item(user_id, created_at DESC)`

### 2.17 `mem_recall_log`（表｜记忆｜234 行）

每次召回命中记录：谁召回了哪条、打分多少。召回会加热度靠它计数。

```sql
CREATE TABLE mem_recall_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    NOT NULL,
    chat_type  TEXT    NOT NULL DEFAULT 'private',
    query_text TEXT    NOT NULL DEFAULT '',
    cues_json  TEXT    NOT NULL DEFAULT '[]',
    status     TEXT    NOT NULL DEFAULT 'miss' CHECK (status IN ('hit', 'uncertain', 'miss')),
    confidence REAL    NOT NULL DEFAULT 0.0,
    item_ids   TEXT    NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | TEXT | 是 | — | 否 |
| 2 | `chat_type` | TEXT | 是 | 'private' | 否 |
| 3 | `query_text` | TEXT | 是 | '' | 否 |
| 4 | `cues_json` | TEXT | 是 | '[]' | 否 |
| 5 | `status` | TEXT | 是 | 'miss' | 否 |
| 6 | `confidence` | REAL | 是 | 0.0 | 否 |
| 7 | `item_ids` | TEXT | 是 | '[]' | 否 |
| 8 | `created_at` | INTEGER | 是 | — | 否 |

索引：

- `idx_recall_user_time`：`CREATE INDEX idx_recall_user_time ON mem_recall_log(user_id, created_at DESC)`

### 2.18 `mem_render_cache`（表｜记忆｜9 行）

渲染缓存：把记忆渲染成 prompt 片段的结果复用。

```sql
CREATE TABLE mem_render_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    NOT NULL,
    chat_type  TEXT    NOT NULL,
    scenario   TEXT    NOT NULL,
    query_hash TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    tokens     INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    UNIQUE (user_id, chat_type, scenario, query_hash)
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | TEXT | 是 | — | 否 |
| 2 | `chat_type` | TEXT | 是 | — | 否 |
| 3 | `scenario` | TEXT | 是 | — | 否 |
| 4 | `query_hash` | TEXT | 是 | — | 否 |
| 5 | `text` | TEXT | 是 | — | 否 |
| 6 | `tokens` | INTEGER | 是 | 0 | 否 |
| 7 | `created_at` | INTEGER | 是 | — | 否 |
| 8 | `expires_at` | INTEGER | 否 | — | 否 |

### 2.19 `member_impression`（表｜会话/关系｜0 行）

群里对每个人的印象（按群 + 人）。

```sql
CREATE TABLE member_impression (
  user_id INTEGER PRIMARY KEY REFERENCES qq_user(id),
  summary TEXT NOT NULL DEFAULT '',
  traits_seen TEXT NOT NULL DEFAULT '[]',
  habits TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `user_id` | INTEGER | 否 | — | 是 |
| 1 | `summary` | TEXT | 是 | '' | 否 |
| 2 | `traits_seen` | TEXT | 是 | '[]' | 否 |
| 3 | `habits` | TEXT | 是 | '[]' | 否 |
| 4 | `version` | INTEGER | 是 | 1 | 否 |
| 5 | `updated_at` | TEXT | 是 | — | 否 |

### 2.20 `memory_merges`（表｜生活/内容｜0 行）

冷记忆软合并的映射记录（`merged_into` 可还原）。

```sql
CREATE TABLE memory_merges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_seq INTEGER NOT NULL DEFAULT 1,
  summary_id INTEGER NOT NULL,
  source_ids TEXT NOT NULL,
  sources TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `session_seq` | INTEGER | 是 | 1 | 否 |
| 3 | `summary_id` | INTEGER | 是 | — | 否 |
| 4 | `source_ids` | TEXT | 是 | — | 否 |
| 5 | `sources` | TEXT | 是 | — | 否 |

### 2.21 `open_threads`（表｜生活/内容｜26 行）

「没说完的话题」：她承诺过「回头再说」的事，到点注入一次提示。

```sql
CREATE TABLE open_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  chat_type TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  due_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  surfaced_at TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `user_id` | INTEGER | 是 | 0 | 否 |
| 2 | `chat_type` | TEXT | 是 | '' | 否 |
| 3 | `text` | TEXT | 是 | — | 否 |
| 4 | `due_at` | TEXT | 是 | — | 否 |
| 5 | `status` | TEXT | 是 | 'open' | 否 |
| 6 | `created_at` | TEXT | 是 | — | 否 |
| 7 | `surfaced_at` | TEXT | 否 | — | 否 |

索引：

- `idx_threads_due`：`CREATE INDEX idx_threads_due ON open_threads(user_id, status, due_at)`

### 2.22 `qq_user`（表｜会话/关系｜0 行）

QQ 身份映射：`openid`（官方通道）/ QQ 号 → 内部 `user_id`，`blocked` 拉黑位。

```sql
CREATE TABLE qq_user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  openid TEXT NOT NULL UNIQUE,
  nickname TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  blocked INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT NOT NULL DEFAULT ''
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `openid` | TEXT | 是 | — | 否 |
| 2 | `nickname` | TEXT | 是 | '' | 否 |
| 3 | `first_seen` | TEXT | 是 | — | 否 |
| 4 | `last_seen` | TEXT | 是 | — | 否 |
| 5 | `blocked` | INTEGER | 是 | 0 | 否 |
| 6 | `blocked_reason` | TEXT | 是 | '' | 否 |

### 2.23 `ref_quotes`（表｜生活/内容｜0 行）

参考语料的单句引用池。

```sql
CREATE TABLE ref_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL UNIQUE,
  freq INTEGER NOT NULL DEFAULT 1,
  period TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `text` | TEXT | 是 | — | 否 |
| 2 | `freq` | INTEGER | 是 | 1 | 否 |
| 3 | `period` | TEXT | 否 | — | 否 |

### 2.24 `ref_turns`（表｜生活/内容｜0 行）

参考语料的对话轮（只学相处方式，不进用户记忆）。

```sql
CREATE TABLE ref_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  period TEXT NOT NULL DEFAULT 'unknown',
  source TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `role` | TEXT | 是 | — | 否 |
| 3 | `text` | TEXT | 是 | — | 否 |
| 4 | `period` | TEXT | 是 | 'unknown' | 否 |
| 5 | `source` | TEXT | 否 | — | 否 |

索引：

- `idx_ref_period`：`CREATE INDEX idx_ref_period ON ref_turns(period)`

### 2.25 `reflect_report`（表｜生活/内容｜3 行）

群聊进化 / 记忆自评的周期报告。

```sql
CREATE TABLE reflect_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  content TEXT NOT NULL,
  metrics TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `kind` | TEXT | 是 | — | 否 |
| 2 | `period_start` | TEXT | 是 | — | 否 |
| 3 | `period_end` | TEXT | 是 | — | 否 |
| 4 | `content` | TEXT | 是 | — | 否 |
| 5 | `metrics` | TEXT | 是 | '{}' | 否 |
| 6 | `created_at` | TEXT | 是 | — | 否 |

索引：

- `idx_reflect_kind`：`CREATE INDEX idx_reflect_kind ON reflect_report(kind, created_at)`

### 2.26 `relation`（表｜会话/关系｜1 行）

**面板单用户线**的关系四维（单行）。与 `user_relation` 是两套账。

```sql
CREATE TABLE relation (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  affinity REAL NOT NULL DEFAULT 0,
  trust REAL NOT NULL DEFAULT 0,
  attraction REAL NOT NULL DEFAULT 0,
  updated_at TEXT
, aversion REAL NOT NULL DEFAULT 0)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `affinity` | REAL | 是 | 0 | 否 |
| 2 | `trust` | REAL | 是 | 0 | 否 |
| 3 | `attraction` | REAL | 是 | 0 | 否 |
| 4 | `updated_at` | TEXT | 否 | — | 否 |
| 5 | `aversion` | REAL | 是 | 0 | 否 |

### 2.27 `relation_log`（表｜会话/关系｜210 行）

每次 `apply_appraisal()` 结算的流水：四维增量、触发源、模型给的理由、**归属谁的账**。只增不改。

```sql
CREATE TABLE relation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user_id INTEGER NOT NULL DEFAULT 0,
  d_affinity REAL NOT NULL DEFAULT 0,
  d_trust REAL NOT NULL DEFAULT 0,
  d_attraction REAL NOT NULL DEFAULT 0,
  trigger TEXT,
  reason TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `user_id` | INTEGER | 是 | 0 | 否 |
| 3 | `d_affinity` | REAL | 是 | 0 | 否 |
| 4 | `d_trust` | REAL | 是 | 0 | 否 |
| 5 | `d_attraction` | REAL | 是 | 0 | 否 |
| 6 | `trigger` | TEXT | 否 | — | 否 |
| 7 | `reason` | TEXT | 否 | — | 否 |

> `user_id` 是 2026-11-01 补的（`_migrate` 会 `ALTER TABLE` 补列，老库自动升级）：0 = 面板用户，
> 其它 = QQ 用户。分手信 `finale._conflict_summary(payload, uid)` 只汇收件人的账，
> 不会再把这个人的辱骂写进那个人的信里。

### 2.28 `schema_migrations`（表｜系统｜2 行）

vendor 引擎的迁移记账表（引擎自己建、自己写）。

```sql
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `version` | INTEGER | 否 | — | 是 |
| 1 | `name` | TEXT | 是 | — | 否 |
| 2 | `checksum` | TEXT | 是 | — | 否 |
| 3 | `applied_at` | INTEGER | 是 | — | 否 |

### 2.29 `session`（表｜会话/关系｜1 行）

她会话的生命周期：`seq` 轮次、`her_name` 当前名字、`name_locked` 是否锁定、`name_pending` 待确认名、`status` active/ended。一次「重置」= 结束旧会话 + 起新 seq。

```sql
CREATE TABLE session (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL DEFAULT 1,
  her_name TEXT,
  name_locked INTEGER NOT NULL DEFAULT 0,
  name_pending TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  started_at TEXT,
  ended_at TEXT,
  end_reason TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `seq` | INTEGER | 是 | 1 | 否 |
| 2 | `her_name` | TEXT | 否 | — | 否 |
| 3 | `name_locked` | INTEGER | 是 | 0 | 否 |
| 4 | `name_pending` | TEXT | 否 | — | 否 |
| 5 | `status` | TEXT | 是 | 'active' | 否 |
| 6 | `started_at` | TEXT | 否 | — | 否 |
| 7 | `ended_at` | TEXT | 否 | — | 否 |
| 8 | `end_reason` | TEXT | 否 | — | 否 |

索引：

- `idx_lover_active`：`CREATE INDEX idx_lover_active ON lover(session_seq, status)`
- `idx_usage_seq`：`CREATE INDEX idx_usage_seq ON usage(session_seq, ts)`

### 2.30 `sqlite_sequence`（表｜其他｜17 行）

```sql
CREATE TABLE sqlite_sequence(name,seq)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `name` | — | 否 | — | 否 |
| 1 | `seq` | — | 否 | — | 否 |

### 2.31 `summaries`（表｜生活/内容｜1 行）

工作记忆压缩产物：LLM 摘要 + `covered_to`（已覆盖到哪条事件），预算裁剪靠它切历史。

```sql
CREATE TABLE summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_seq INTEGER NOT NULL DEFAULT 1,
  level INTEGER NOT NULL,
  period_start TEXT,
  period_end TEXT,
  text TEXT NOT NULL,
  covered_to INTEGER,
  tokens_est INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `session_seq` | INTEGER | 是 | 1 | 否 |
| 2 | `level` | INTEGER | 是 | — | 否 |
| 3 | `period_start` | TEXT | 否 | — | 否 |
| 4 | `period_end` | TEXT | 否 | — | 否 |
| 5 | `text` | TEXT | 是 | — | 否 |
| 6 | `covered_to` | INTEGER | 否 | — | 否 |
| 7 | `tokens_est` | INTEGER | 是 | 0 | 否 |
| 8 | `created_at` | TEXT | 是 | — | 否 |

### 2.32 `thought_surface`（表｜生活/内容｜0 行）

夜里独处的念头「浮上水面」记录：写了什么、什么时候被谁看见。

```sql
CREATE TABLE thought_surface (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thought_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL DEFAULT 0,
  chat_type TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `thought_id` | INTEGER | 是 | — | 否 |
| 2 | `user_id` | INTEGER | 是 | 0 | 否 |
| 3 | `chat_type` | TEXT | 是 | '' | 否 |
| 4 | `ts` | TEXT | 是 | — | 否 |

索引：

- `idx_thought_surface_once`：`CREATE UNIQUE INDEX idx_thought_surface_once ON thought_surface(thought_id)`
- `idx_thought_surface_time`：`CREATE INDEX idx_thought_surface_time ON thought_surface(ts DESC)`

### 2.33 `traits`（表｜会话/关系｜8 行）

人格八维的当前值（含 min/max 夹取后）。

```sql
CREATE TABLE traits (
  dim TEXT PRIMARY KEY,
  value REAL NOT NULL,
  baseline REAL NOT NULL,
  min_value REAL NOT NULL,
  max_value REAL NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `dim` | TEXT | 否 | — | 是 |
| 1 | `value` | REAL | 是 | — | 否 |
| 2 | `baseline` | REAL | 是 | — | 否 |
| 3 | `min_value` | REAL | 是 | — | 否 |
| 4 | `max_value` | REAL | 是 | — | 否 |

### 2.34 `traits_log`（表｜会话/关系｜0 行）

人格八维的变更流水，改她必须留痕的另一半。

```sql
CREATE TABLE traits_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  dim TEXT NOT NULL,
  before REAL NOT NULL,
  after REAL NOT NULL,
  cause TEXT
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `dim` | TEXT | 是 | — | 否 |
| 3 | `before` | REAL | 是 | — | 否 |
| 4 | `after` | REAL | 是 | — | 否 |
| 5 | `cause` | TEXT | 否 | — | 否 |

### 2.35 `usage`（表｜生活/内容｜461 行）

模型用量：每次调用的 `model` / `task` / `tokens_in` / `tokens_out` / `cost`。

```sql
CREATE TABLE usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_seq INTEGER NOT NULL DEFAULT 1,
  model TEXT NOT NULL,
  task TEXT NOT NULL DEFAULT 'chat_default',
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `ts` | TEXT | 是 | — | 否 |
| 2 | `session_seq` | INTEGER | 是 | 1 | 否 |
| 3 | `model` | TEXT | 是 | — | 否 |
| 4 | `task` | TEXT | 是 | 'chat_default' | 否 |
| 5 | `tokens_in` | INTEGER | 是 | 0 | 否 |
| 6 | `tokens_out` | INTEGER | 是 | 0 | 否 |
| 7 | `cost` | REAL | 是 | 0 | 否 |

索引：

- `idx_usage_seq`：`CREATE INDEX idx_usage_seq ON usage(session_seq, ts)`

### 2.36 `user_relation`（表｜会话/关系｜0 行）

**QQ 多人线**：按 `user_id` 一行的四维 + `stage` 阶段 + `hit_100_at`（实际是「到 80 分的时间」）。

```sql
CREATE TABLE user_relation (
  user_id INTEGER PRIMARY KEY REFERENCES qq_user(id),
  affinity REAL NOT NULL DEFAULT 0,
  trust REAL NOT NULL DEFAULT 0,
  attraction REAL NOT NULL DEFAULT 0,
  aversion REAL NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT 'observer',
  hit_100_at TEXT,
  updated_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `user_id` | INTEGER | 否 | — | 是 |
| 1 | `affinity` | REAL | 是 | 0 | 否 |
| 2 | `trust` | REAL | 是 | 0 | 否 |
| 3 | `attraction` | REAL | 是 | 0 | 否 |
| 4 | `aversion` | REAL | 是 | 0 | 否 |
| 5 | `stage` | TEXT | 是 | 'observer' | 否 |
| 6 | `hit_100_at` | TEXT | 否 | — | 否 |
| 7 | `updated_at` | TEXT | 是 | — | 否 |

### 2.37 `web_lookup`（表｜生活/内容｜0 行）

联网查词的缓存与结果。

```sql
CREATE TABLE web_lookup (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  results TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 是 |
| 1 | `keyword` | TEXT | 是 | — | 否 |
| 2 | `results` | TEXT | 是 | '[]' | 否 |
| 3 | `summary` | TEXT | 是 | '' | 否 |
| 4 | `created_at` | TEXT | 是 | — | 否 |

索引：

- `idx_web_lookup_kw`：`CREATE INDEX idx_web_lookup_kw ON web_lookup(keyword, created_at)`

### 2.38 `web_videos`（表｜生活/内容｜50 行）

B 站视频元信息缓存。

```sql
CREATE TABLE web_videos (
  bvid TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  pic TEXT NOT NULL DEFAULT '',
  play INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL
)
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `bvid` | TEXT | 否 | — | 是 |
| 1 | `title` | TEXT | 是 | '' | 否 |
| 2 | `author` | TEXT | 是 | '' | 否 |
| 3 | `pic` | TEXT | 是 | '' | 否 |
| 4 | `play` | INTEGER | 是 | 0 | 否 |
| 5 | `url` | TEXT | 是 | '' | 否 |
| 6 | `first_seen` | TEXT | 是 | — | 否 |

### 2.39 `memories`（视图｜记忆｜1 行）

**视图**（不是表）：`mem_item` 的投影，过滤 forgotten / superseded_by / merged_into。**只读，禁止 DELETE**（D1 回归就是防这个）。

```sql
CREATE VIEW memories AS
SELECT
    m.id                                                  AS id,
    m.kind                                                AS kind,
    m.text                                                AS content,
    m.importance / 100.0                                  AS importance,
    0.5                                                   AS stability,
    m.source_event_ids                                    AS source_ids,
    strftime('%Y-%m-%dT%H:%M:%S', m.created_at, 'unixepoch', 'localtime') AS first_seen,
    strftime('%Y-%m-%dT%H:%M:%S', m.last_seen, 'unixepoch', 'localtime')  AS last_seen,
    m.recall_count                                        AS recall_count,
    CASE WHEN m.last_recall IS NULL THEN NULL
         ELSE strftime('%Y-%m-%dT%H:%M:%S', m.last_recall, 'unixepoch', 'localtime') END AS last_recall,
    m.heat                                                AS heat,
    m.pinned                                              AS pinned,
    COALESCE((SELECT json_group_array(c.cue) FROM mem_cue c WHERE c.item_id = m.id), '[]') AS cues,
    CASE WHEN m.last_recall IS NULL
         THEN strftime('%Y-%m-%dT%H:%M:%S', m.last_seen, 'unixepoch', 'localtime')
         ELSE strftime('%Y-%m-%dT%H:%M:%S', m.last_recall, 'unixepoch', 'localtime') END AS last_touch,
    CAST(m.user_id AS INTEGER)                            AS user_id,
    m.chat_type                                           AS chat_type,
    m.clarity                                             AS clarity
FROM mem_item m
WHERE m.forgotten = 0
  AND m.superseded_by IS NULL
  AND m.merged_into IS NULL
```

| # | 列 | 声明类型 | 非空 | 默认值 | 主键 |
|---|---|---|---|---|---|
| 0 | `id` | INTEGER | 否 | — | 否 |
| 1 | `kind` | TEXT | 否 | — | 否 |
| 2 | `content` | TEXT | 否 | — | 否 |
| 3 | `importance` | — | 否 | — | 否 |
| 4 | `stability` | — | 否 | — | 否 |
| 5 | `source_ids` | TEXT | 否 | — | 否 |
| 6 | `first_seen` | — | 否 | — | 否 |
| 7 | `last_seen` | — | 否 | — | 否 |
| 8 | `recall_count` | INTEGER | 否 | — | 否 |
| 9 | `last_recall` | — | 否 | — | 否 |
| 10 | `heat` | REAL | 否 | — | 否 |
| 11 | `pinned` | INTEGER | 否 | — | 否 |
| 12 | `cues` | — | 否 | — | 否 |
| 13 | `last_touch` | — | 否 | — | 否 |
| 14 | `user_id` | INT | 否 | — | 否 |
| 15 | `chat_type` | TEXT | 否 | — | 否 |
| 16 | `clarity` | TEXT | 否 | — | 否 |

## 3. 不变量与读写纪律

1. **`memories` 是视图不是表。** 写它一定失败；写记忆只走 `app/memory/h2.py: add()`。回归用例 D1 专门钉的就是「别对视图执行 DELETE」。
2. **视图行数 == 活跃 `mem_item` 行数**，是改记忆后的必验项：`select count(*) from memories` 必须等于 `select count(*) from mem_item where forgotten=0 and superseded_by is null and merged_into is null`。
3. **关系数值只走 `relation/engine.apply_appraisal()`**，两套表（`relation` 单行 / `user_relation` 按 uid）都要同步，流水落 `relation_log`。
4. **任何改变她状态的动作必须写 `continuity_log`**（`app/core/continuity.py: record()`）。
5. **`mem_event.raw_text` 永不进 prompt**，只用于审计与重放。
6. `kv` 是运行时开关的总仓库，键名带作用域后缀（`:1` / `:2` 是 `user_id`，`:2026-11-01` 是日期）。
7. `data/` 整个目录不入库（`.gitignore` 已忽略），所以表结构只能从代码与迁移脚本还原。
