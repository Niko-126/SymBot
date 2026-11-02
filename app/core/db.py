from __future__ import annotations

import pathlib
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',
  text TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'panel',
  meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_seq INTEGER NOT NULL DEFAULT 1,
  level INTEGER NOT NULL,
  period_start TEXT,
  period_end TEXT,
  text TEXT NOT NULL,
  covered_to INTEGER,
  tokens_est INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- 记忆本体在引擎表 mem_item / mem_cue（vendor/symvolve_memory）。
-- 旧的名字 `memories` 现在是只读视图，由 app/memory/memv2.py 建，见 docs/49。

CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL DEFAULT 1,
  her_name TEXT,
  name_locked INTEGER NOT NULL DEFAULT 0,
  name_pending TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  started_at TEXT,
  ended_at TEXT,
  end_reason TEXT
);

CREATE TABLE IF NOT EXISTS relation (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  affinity REAL NOT NULL DEFAULT 0,
  trust REAL NOT NULL DEFAULT 0,
  attraction REAL NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS relation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user_id INTEGER NOT NULL DEFAULT 0,
  d_affinity REAL NOT NULL DEFAULT 0,
  d_trust REAL NOT NULL DEFAULT 0,
  d_attraction REAL NOT NULL DEFAULT 0,
  trigger TEXT,
  reason TEXT
);

-- ---- QQ 群：每个群成员一份独立关系（qq-group 分支，见 docs/20） ----
CREATE TABLE IF NOT EXISTS qq_user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  openid TEXT NOT NULL UNIQUE,
  nickname TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  blocked INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_relation (
  user_id INTEGER PRIMARY KEY REFERENCES qq_user(id),
  affinity REAL NOT NULL DEFAULT 0,
  trust REAL NOT NULL DEFAULT 0,
  attraction REAL NOT NULL DEFAULT 0,
  aversion REAL NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT 'observer',
  hit_100_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lover (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_seq INTEGER NOT NULL DEFAULT 1,
  user_id INTEGER NOT NULL REFERENCES qq_user(id),
  status TEXT NOT NULL DEFAULT 'active',
  bound_at TEXT NOT NULL,
  ended_at TEXT,
  end_reason TEXT,
  cooldown_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_lover_active ON lover(session_seq, status);
CREATE INDEX IF NOT EXISTS idx_lover_user ON lover(user_id);

-- ---- 群聊进化：人类观察 / 每人看法 / 周期报告（docs/22） ----
CREATE TABLE IF NOT EXISTS human_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  topic TEXT NOT NULL DEFAULT '习惯',
  content TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.6,
  user_id INTEGER,
  session_seq INTEGER NOT NULL DEFAULT 1,
  chat_type TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_human_notes_day ON human_notes(day);
CREATE INDEX IF NOT EXISTS idx_human_notes_topic ON human_notes(topic);

CREATE TABLE IF NOT EXISTS member_impression (
  user_id INTEGER PRIMARY KEY REFERENCES qq_user(id),
  summary TEXT NOT NULL DEFAULT '',
  traits_seen TEXT NOT NULL DEFAULT '[]',
  habits TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reflect_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  content TEXT NOT NULL,
  metrics TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflect_kind ON reflect_report(kind, created_at);

-- ---- 外部网络内容：热梗库 / 见过的视频（与她的记忆物理隔离，docs/25） ----
CREATE TABLE IF NOT EXISTS gags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  source_title TEXT NOT NULL DEFAULT '',
  bvid TEXT NOT NULL DEFAULT '',
  likes INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'comment',
  enabled INTEGER NOT NULL DEFAULT 1,
  harvested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gags_likes ON gags(enabled, likes);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gags_text ON gags(text);

CREATE TABLE IF NOT EXISTS web_videos (
  bvid TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  pic TEXT NOT NULL DEFAULT '',
  play INTEGER NOT NULL DEFAULT 0,
  url TEXT NOT NULL DEFAULT '',
  first_seen TEXT NOT NULL
);

-- 联网查词缓存（独立表，不进她的记忆，docs/27）
CREATE TABLE IF NOT EXISTS web_lookup (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  results TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_lookup_kw ON web_lookup(keyword, created_at);

-- 自动学到的新词（带可信度，可在控制台复核/删除）
CREATE TABLE IF NOT EXISTS learned_gag (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL UNIQUE,
  meaning TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'web',
  raw TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learned_conf ON learned_gag(confidence DESC);

-- 亲密模式上下文（独立表，仅用户明确进入该模式后记录）
CREATE TABLE IF NOT EXISTS intimate_context (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  mode TEXT NOT NULL DEFAULT 'suggestive',
  is_private INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  events TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_intimate_user ON intimate_context(user_id, ended_at);

-- 亲密偏好档案：从私密记录里提炼"他喜欢什么/讨厌什么/边界在哪"（docs/29）
CREATE TABLE IF NOT EXISTS intimate_profile (
  user_id INTEGER PRIMARY KEY,
  likes TEXT NOT NULL DEFAULT '[]',
  dislikes TEXT NOT NULL DEFAULT '[]',
  boundaries TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '',
  sessions_seen INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

-- 没说完的话题：她说“明天再讲/回头再说”，到点提醒她主动接上
CREATE TABLE IF NOT EXISTS open_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
  chat_type TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  due_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  surfaced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_due ON open_threads(user_id, status, due_at);

CREATE TABLE IF NOT EXISTS traits (
  dim TEXT PRIMARY KEY,
  value REAL NOT NULL,
  baseline REAL NOT NULL,
  min_value REAL NOT NULL,
  max_value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS traits_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  dim TEXT NOT NULL,
  before REAL NOT NULL,
  after REAL NOT NULL,
  cause TEXT
);

CREATE TABLE IF NOT EXISTS life_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  energy REAL NOT NULL DEFAULT 60,
  mood REAL NOT NULL DEFAULT 0,
  focus REAL NOT NULL DEFAULT 50,
  social_battery REAL NOT NULL DEFAULT 60,
  activity TEXT,
  last_tick TEXT
);

CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 连续性账本：任何"改写她"的动作（换人设/换模型/清记忆/重开会话/导入灵魂）
-- 都在这里留一条痕迹。她可以被改变，但改变不能被抹掉（见 docs/51）。
CREATE TABLE IF NOT EXISTS continuity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_continuity_ts ON continuity_log(ts DESC);

-- 念头浮现台账：她夜里想的事，第二天最多"顺口提一次"；提过就记这里（只增）
CREATE TABLE IF NOT EXISTS thought_surface (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thought_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL DEFAULT 0,
  chat_type TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_thought_surface_once ON thought_surface(thought_id);
CREATE INDEX IF NOT EXISTS idx_thought_surface_time ON thought_surface(ts DESC);

-- 参考语料：只用来学"人怎么相处"，不参与用户记忆与关系
CREATE TABLE IF NOT EXISTS ref_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  period TEXT NOT NULL DEFAULT 'unknown',
  source TEXT
);
CREATE INDEX IF NOT EXISTS idx_ref_period ON ref_turns(period);

CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_seq INTEGER NOT NULL DEFAULT 1,
  model TEXT NOT NULL,
  task TEXT NOT NULL DEFAULT 'chat_default',
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_seq ON usage(session_seq, ts);

CREATE TABLE IF NOT EXISTS memory_merges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_seq INTEGER NOT NULL DEFAULT 1,
  summary_id INTEGER NOT NULL,
  source_ids TEXT NOT NULL,
  sources TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anchors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL UNIQUE,
  scene TEXT NOT NULL DEFAULT '日常',
  stage_min INTEGER NOT NULL DEFAULT 0,
  explicit INTEGER NOT NULL DEFAULT 0,
  profane INTEGER NOT NULL DEFAULT 0,
  freq INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ref_quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL UNIQUE,
  freq INTEGER NOT NULL DEFAULT 1,
  period TEXT
);

-- 加好友申请：OneBot 只给"申请事件"，真正的通过要我们回 set_friend_add_request
CREATE TABLE IF NOT EXISTS friend_request (
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
);
CREATE INDEX IF NOT EXISTS idx_friend_request_status ON friend_request(status, id);
"""

_DB_PATH: pathlib.Path | None = None


def init(db_path: pathlib.Path) -> None:
    global _DB_PATH
    _DB_PATH = pathlib.Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO relation (id, affinity, trust, attraction, updated_at)"
            " VALUES (1, 0, 0, 0, datetime('now'))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO life_state (id, energy, mood, focus, social_battery, activity, last_tick)"
            " VALUES (1, 60, 0, 50, 60, '发呆', datetime('now'))"
        )
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    # 衡忆 2.0：建引擎表 → 旧 memories 表归档 → 建只读视图（幂等，见 docs/49）﻿​‌﻿​‍​‌‍﻿﻿​‌​‍‍​﻿‍​﻿‍​‍﻿‍​﻿﻿﻿﻿​‌﻿
    from app.memory import memv2

    memv2.ensure_ready()


def _table_kind(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return str(row[0]) if row else ""


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列。`memories` 只在升级前的库里还是表；引擎接管后它是只读视图，不再碰。"""
    if _table_kind(conn, "memories") == "table":
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        adds = (
            ("heat", "ALTER TABLE memories ADD COLUMN heat REAL NOT NULL DEFAULT 0.9"),
            ("pinned", "ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
            ("cues", "ALTER TABLE memories ADD COLUMN cues TEXT NOT NULL DEFAULT '[]'"),
            ("last_touch", "ALTER TABLE memories ADD COLUMN last_touch TEXT"),
            # 群聊多用户：记忆归属；0 = 共用（旧数据）
            ("user_id", "ALTER TABLE memories ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"),
        )
        for name, ddl in adds:
            if name not in cols:
                conn.execute(ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_heat ON memories(pinned, heat)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)")
    # 群聊：事件归属（0 = 旧的单用户数据）
    event_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "user_id" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id)")
    # 私聊 / 群聊分流：老数据留空，空 = 不区分（两边都能读到）
    if "chat_type" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN chat_type TEXT NOT NULL DEFAULT ''")
    if "group_id" not in event_cols:
        conn.execute("ALTER TABLE events ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_chat ON events(user_id, chat_type)"
    )
    if _table_kind(conn, "memories") == "table":
        mem_cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        if "chat_type" not in mem_cols:
            conn.execute("ALTER TABLE memories ADD COLUMN chat_type TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_chat ON memories(user_id, chat_type)"
        )
    if "aversion" not in {row[1] for row in conn.execute("PRAGMA table_info(relation)")}:
        conn.execute("ALTER TABLE relation ADD COLUMN aversion REAL NOT NULL DEFAULT 0")
    # 观察来源：这条"人的习惯"是从私聊还是群聊里看到的（空 = 老数据/不区分）
    note_cols = {row[1] for row in conn.execute("PRAGMA table_info(human_notes)")}
    if "chat_type" not in note_cols:
        conn.execute("ALTER TABLE human_notes ADD COLUMN chat_type TEXT NOT NULL DEFAULT ''")
    # 拉黑：某个人厌恶拉满只影响他自己，不终结整个会话
    qq_cols = {row[1] for row in conn.execute("PRAGMA table_info(qq_user)")}
    if "blocked" not in qq_cols:
        conn.execute("ALTER TABLE qq_user ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    if "blocked_reason" not in qq_cols:
        conn.execute("ALTER TABLE qq_user ADD COLUMN blocked_reason TEXT NOT NULL DEFAULT ''")
    # 关系流水归属：多用户时必须能分清"这是谁的账"，
    # 否则分手信会把别人的辱骂一起算进来（docs/23 §3.2 第 7 条）。
    log_cols = {row[1] for row in conn.execute("PRAGMA table_info(relation_log)")}
    if "user_id" not in log_cols:
        conn.execute("ALTER TABLE relation_log ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_relation_log_user ON relation_log(user_id)")
    if "session_seq" not in {row[1] for row in conn.execute("PRAGMA table_info(summaries)")}:
        conn.execute("ALTER TABLE summaries ADD COLUMN session_seq INTEGER NOT NULL DEFAULT 1")
    # R 级插件（app/intimacy/r18/）用的两列：事后照护偏好、上次停在 L 几。
    # 插件没装时这俩列永远为空，对基础链路零影响。
    prof_cols = {row[1] for row in conn.execute("PRAGMA table_info(intimate_profile)")}
    if "aftercare" not in prof_cols:
        conn.execute("ALTER TABLE intimate_profile ADD COLUMN aftercare TEXT NOT NULL DEFAULT '[]'")
    if "last_stage" not in prof_cols:
        conn.execute("ALTER TABLE intimate_profile ADD COLUMN last_stage TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " seq INTEGER NOT NULL DEFAULT 1,"
        " her_name TEXT,"
        " name_locked INTEGER NOT NULL DEFAULT 0,"
        " name_pending TEXT,"
        " status TEXT NOT NULL DEFAULT 'active',"
        " started_at TEXT,"
        " ended_at TEXT,"
        " end_reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL,"
        " session_seq INTEGER NOT NULL DEFAULT 1,"
        " model TEXT NOT NULL,"
        " task TEXT NOT NULL DEFAULT 'chat_default',"
        " tokens_in INTEGER NOT NULL DEFAULT 0,"
        " tokens_out INTEGER NOT NULL DEFAULT 0,"
        " cost REAL NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_seq ON usage(session_seq, ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_merges ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL,"
        " session_seq INTEGER NOT NULL DEFAULT 1,"
        " summary_id INTEGER NOT NULL,"
        " source_ids TEXT NOT NULL,"
        " sources TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS anchors ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " text TEXT NOT NULL UNIQUE,"
        " scene TEXT NOT NULL DEFAULT '日常',"
        " stage_min INTEGER NOT NULL DEFAULT 0,"
        " explicit INTEGER NOT NULL DEFAULT 0,"
        " profane INTEGER NOT NULL DEFAULT 0,"
        " freq INTEGER NOT NULL DEFAULT 1,"
        " enabled INTEGER NOT NULL DEFAULT 1)"
    )
    # 连续性账本（docs/51）：老库补表，不重建
    conn.execute(
        "CREATE TABLE IF NOT EXISTS continuity_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL,"
        " kind TEXT NOT NULL,"
        " detail TEXT NOT NULL DEFAULT '',"
        " source TEXT NOT NULL DEFAULT '',"
        " meta TEXT NOT NULL DEFAULT '{}')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_continuity_ts ON continuity_log(ts DESC)"
    )
    # 念头浮现台账（老库补表）
    conn.execute(
        "CREATE TABLE IF NOT EXISTS thought_surface ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " thought_id INTEGER NOT NULL,"
        " user_id INTEGER NOT NULL DEFAULT 0,"
        " chat_type TEXT NOT NULL DEFAULT '',"
        " ts TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_thought_surface_once"
        " ON thought_surface(thought_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thought_surface_time ON thought_surface(ts DESC)"
    )


def connect() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("db.init() 未被调用")
    conn = sqlite3.connect(str(_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def cursor(commit: bool = True) -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with cursor(commit=False) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def one(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql: str, args: tuple = ()) -> int:
    # 后台抽取/评估会和主对话同时写；遇到短暂锁就重试，别把整轮聊天搞挂
    for attempt in range(3):
        try:
            with cursor() as cur:
                cur.execute(sql, args)
                return cur.lastrowid or 0
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.15 * (attempt + 1))
                continue
            raise
    return 0


def executemany(sql: str, seq: list[tuple]) -> int:
    """批量写，一个事务一个连接。导入十万级记录时必须走这条。"""
    if not seq:
        return 0
    for attempt in range(3):
        conn = connect()
        try:
            cur = conn.executemany(sql, seq)
            conn.commit()
            return cur.rowcount
        except sqlite3.OperationalError as e:
            conn.rollback()
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.15 * (attempt + 1))
                continue
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return 0


def kv_get(key: str, default: str = "") -> str:
    r = one("SELECT value FROM kv WHERE key = ?", (key,))
    return r["value"] if r else default


def kv_key(key: str, scope: Any = None) -> str:
    """带作用域的 kv 键：`scope` 非空 → `key:scope`，否则原样用全局键。

    为什么要它：群聊/多用户场景里，单槽全局键会让 A 的状态污染 B——
    「一个群让她闭嘴」变成「所有群都闭嘴」。作用域键 + 全局键回落，
    老数据（没有 `:scope` 后缀）仍然读得到。
    """
    s = "" if scope is None else str(scope).strip()
    return f"{key}:{s}" if s else key


def kv_set(key: str, value: str) -> None:
    execute(
        "INSERT INTO kv (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
