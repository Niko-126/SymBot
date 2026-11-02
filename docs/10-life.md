# 10 · 生活与作息（app/life）

> 覆盖：`app/life/*.py`（7 个文件 / 2738 行）｜基于 2026-11-01 的代码状态｜上游：docs/05（对话主链 `loop.chat_turn`）、docs/06（记忆 `h2`）、docs/08（关系数值）｜下游：docs/11（群聊）、docs/15（人类语料管线）、docs/12（HTTP 接口）

## 1. 这一层干什么

`app/life` 是"她此刻是个什么状态"的唯一来源，同时管四件互相咬合的事。

第一，把时钟和作息表算成一份状态：几点、在干嘛、精力多少、睡没睡，落 `life_state` 表（`clock.get_state()`）。
第二，把状态翻译成"怎么回"：延迟多少毫秒、句子多长、给面板什么提示、给模型什么状态描述（`behavior.decide()`）。
第三，让"她的一天"有连续性：每天一段日记（`diary.py`）、用人类作息语料接管她的作息（`human_life.py`）、夜里/白天自己冒念头（`thoughts.py`）。
第四，主动外发：一段时间没说话她会来找你，被冷落会生气，带出昨晚想的念头（`proactive.py`）。

它不做什么：不写记忆正文（写记忆只走 `app/memory/h2.py: add()`）、不结算关系（只调 `relation.session.adjust_aversion()`）、不直接发消息（`proactive` 只算"要说什么"，真发在 `app/main.py` 的后台 worker）。它调用 `persona`（作息表/阶段表）、`memengine`（引擎存储）、`h2`（记忆写入口）、`companion`（陪伴层台账）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/life/__init__.py` | 0 | 空文件，只是包标记，没有任何 re-export |
| `app/life/clock.py` | 95 | 时区时间、作息槽位、`life_state` 读写 |
| `app/life/behavior.py` | 194 | 清醒度模型 + 回复策略（延迟/提示/是否回） |
| `app/life/diary.py` | 189 | 每天一段第一人称日记，第二天提一次 |
| `app/life/human_life.py` | 554 | 把 `data/human_life/_derived` 语料读进运行时（只读接管） |
| `app/life/proactive.py` | 863 | 按人主动外发：闸门、免打扰、配额、两段式 prepare/commit |
| `app/life/thoughts.py` | 843 | 独处念头：夜里一整件、白天碎片、第二天浮现 |

## 3. 模块逐一详解

### 3.1 `app/life/__init__.py`

**职责**：包标记。文件为空（0 行），没有 `__all__`、没有符号、没有 `import`。所有使用方都写 `from app.life import clock` 这种子模块导入，不走包级导出。

**坑**：空文件意味着没有任何初始化顺序保证；`app/life/*` 之间的循环导入靠函数内 `from app.life import xxx` 局部导入规避（例如 `proactive.py` 里六处 `from app.life import thoughts as thought_mod` 全在函数体内部）。

### 3.2 `app/life/clock.py`（95 行）

**职责**：把"现在几点 + 作息表"变成一份状态并写回 `life_state`。它是全仓唯一写 `life_state` 的地方（另一处是 `app/api/routes.py:409` 的开发者直接改数值）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `_TZ` | `ZoneInfo("Asia/Shanghai")` | 模块级固定时区，全层展示都按 UTC+08 |
| `now` | `now() -> datetime` | 带时区的当前时刻 |
| `human_time` | `human_time(dt: datetime) -> str` | 中文时间短语，如「晚上 21 点 5 分」 |
| `get_state` | `get_state(persona: Persona) -> dict` | 算此刻状态并写回 `life_state` |
| `set_mood` | `set_mood(mood: float \| None = None, energy: float \| None = None) -> None` | 只改心情/精力，其余不动 |

**关键实现**：`get_state` 的执行顺序。

1. `dt = now()`；`slot = persona.activity_at(dt.hour, dt.minute)`。`activity_at` 遍历 `persona.schedule()` 里 `start` ≤ 当前分钟数的最后一条，取不到返回 `{"activity": "发呆", "energy": 50}`（`app/persona/load.py:115-125`）。
2. 从槽位取三个值：`activity`（默认 `"发呆"`）、`base_energy`（默认 `50.0`）、`sleeping`（默认 `False`）。
3. 读 kv `dev_awake`，等于 `"1"` 时强制 `sleeping = False`、`activity = "清醒着（开发者）"`。这个键由 `POST /v1/dev/awake` 写入（`app/api/routes.py:418`）。
4. 读 `life_state WHERE id = 1`，取 `mood`（默认 0.0）、`focus`（默认 50.0）、`social_battery`（默认 60.0）。
5. 精力融合：`energy = base_energy if row is None else round(float(row["energy"]) * 0.6 + base_energy * 0.4, 1)`。0.6 的权重给上一次的精力，避免整点跳变。
6. human_life 只读接管：`not dev_awake and human_life.enabled()` 时调 `human_life.resolve(dt)`，异常吞掉当 `None`；命中就覆盖 `activity` / `energy` / `sleeping` 三项。
7. `UPDATE life_state SET energy, mood, focus, social_battery, activity, last_tick WHERE id = 1`，`last_tick` 用 `dt.isoformat(timespec="seconds")`。
8. 返回 10 个键：`virtual_time`、`virtual_time_human`、`activity`、`energy`、`mood`、`focus`、`social_battery`、`sleeping`、`hour`、`human_life`（原始命中 dict 或 `None`）。

`human_time` 的分段是硬编码的：`h < 5` 凌晨、`< 8` 早上、`< 11` 上午、`< 13` 中午、`< 18` 下午、其余晚上，格式 `f"{prefix} {h} 点 {m} 分"`。

**数据结构 / 存储**：

- 表 `life_state`：`id`（CHECK id = 1，单行）、`energy REAL DEFAULT 60`、`mood REAL DEFAULT 0`、`focus REAL DEFAULT 50`、`social_battery REAL DEFAULT 60`、`activity TEXT`、`last_tick TEXT`（`app/core/db.py:235-243`）。
- kv：`dev_awake`（`"1"`/`"0"`，开发者让她醒着）。
- 入参 `persona` 提供：`schedule()`（作息表）、`activity_at(hour, minute)`。

**耦合**：被 `app/mind/loop.py:310`（每轮对话取此刻）、`app/api/routes.py:93`（面板首屏）、`app/api/routes.py:826`（设置页回显）、`app/qa/scenarios.py` 调用；`get_state` 内部调 `human_life.enabled()` / `resolve()`。

**坑**：

1. `get_state` 有写副作用：每调一次就 `UPDATE` 一次 `life_state`，高频请求下是热点写，但它同时是"读"接口。
2. `activity_at` 只按 `start` 从前到后覆盖、不看 `end`，所以 00:00–03:00 会一直停在"还在改方案"这一档（persona 表就是这么配的），跨零点的"睡着了"只在 03:00 之后生效。
3. human_life 命中时 `energy` 是**直接覆盖**，不参与 0.6/0.4 融合，所以开关一开精力会在语料槽位边界阶跃。
4. `mood` / `focus` / `social_battery` 在这一层只读不写：没有任何逻辑根据对话修改它们，全靠开发者接口或别处写入。
5. `set_mood` 在 `life_state` 没有 id=1 的行时直接 return，不建行。

### 3.3 `app/life/behavior.py`（194 行）——清醒度模型

**职责**：把 `clock.get_state()` 的状态翻译成回复策略。它不改业务，只产出 5 个字段：`mode` / `will_reply` / `delay_ms` / `hint` / `prompt_hint`。

**为什么不是"睡着就不回"**：旧行为是睡着时直接不回；2026-11-01 起换成清醒度滑杆——睡着时一句话叫不醒，但**越聊越清醒**，停一会儿会自己睡回去。`mode` 仍然保留了 `asleep` 这一档，但 asleep 分支返回 `will_reply=True`（只回两三个字）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `Reaction` | `@dataclass Reaction`：`mode: str = "awake"`、`will_reply: bool = True`、`delay_ms: int = 0`、`hint: str = ""`、`prompt_hint: str = ""` | 策略载体 |
| `awake_level` | `awake_level(now: float \| None = None) -> float` | 当前清醒度（含时间衰减） |
| `bump_awake` | `bump_awake(chars: int = 0, recent_user_turns: int = 0, now: float \| None = None) -> float` | 聊了一句，清醒度上升并写回 kv |
| `decide` | `decide(life: dict[str, Any], recent_user_turns: int = 0, persona=None, *, text: str = "", now: float \| None = None) -> Reaction` | 主入口 |
| `apply_delay` | `apply_delay(segments: list[str], reaction: Reaction) -> list[int]` | 把延迟摊到每个分段气泡 |

**常量真值**：

| 常量 | 值 | 含义 |
|---|---|---|
| `AWAKE_KEY` | `"awake_level"` | 清醒度（0–1 的字符串，写回保留 4 位小数） |
| `AWAKE_TS_KEY` | `"awake_ts"` | 上次变动的 unix 秒（保留 3 位小数） |
| `AWAKE_HALF_LIFE_MIN` | `6.0` | 停聊半衰期（分钟） |
| `AWAKE_STEP_BASE` | `0.14` | 单句基础增量 |
| `AWAKE_STEP_MAX_EXTRA` | `0.10` | 长句额外增量上限 |
| `AWAKE_KEYS` | `(0.25, 0.55, 0.80)` | 四档分界 |
| `_SLEEPY_ACTIVITY` | `("睡",)` | 活动名里含"睡"就算该睡状态 |
| `_BUSY_ACTIVITY` | `("洗澡", "吃饭", "上班", "归档", "转录", "开会", "下班路上")` | 忙档关键字 |

**读取衰减**（`awake_level`）：

1. 读 kv `awake_level`、`awake_ts`，`float()` 失败分别退回 0.0；先 clamp 到 `[0, 1]`。
2. `ts > 0 and now > ts` 时算 `idle_min = (now - ts) / 60`，然后 `level *= 0.5 ** (idle_min / 6.0)`。
3. 再 clamp 一次返回。半衰期 6 分钟意味着 0.84 停 6 分钟掉到 0.42（drowsy），再 6 分钟到 0.21（asleep）——约 20 分钟睡回去。

**每句话加成**（`bump_awake`）：

```
level = min(1.0, level + AWAKE_STEP_BASE + min(0.10, chars / 240.0) + 0.02 * min(3, recent_user_turns))
```

- 基础 `+0.14`：从 0 开始第一句只到 0.14（asleep），一句话叫不醒。
- 长句额外：`chars / 240`，打满 240 字给 `+0.10`。
- 连续轮数：`0.02 × min(3, 轮数)`，最多 `+0.06`。
- 单句理论上限 `+0.30`，所以从 0 到 0.80（awake）至少要 3 句像样的话。
- 写回：`kv awake_level = f"{level:.4f}"`、`kv awake_ts = f"{now:.3f}"`。

**四档与策略**：

| level | mode | 面板 `hint` | `delay_ms` | `prompt_hint` 摘要 |
|---|---|---|---|---|
| `< 0.25` | `asleep` | 她好像刚睡着，被你叫醒了。再聊两句她就清醒了。 | 1500 | 刚被你叫醒，脑子是糊的，只回一句、两三个字，别解释别提问 |
| `0.25–0.55` | `drowsy` | 她被你聊得清醒了一点。 | 2200 | 半梦半醒，一到两句、短、带困意；不要说自己「在忙」 |
| `0.55–0.80` | `sleepy` | `""`（空串） | 1600 | 困但能正常接话，两句以内，语气软，可以粘一点；不要编「在忙」 |
| `≥ 0.80` | `awake` | 聊着聊着，她清醒过来了。 | 900 | 你本来快睡了，被他一句一句聊醒了，语气正常，可以带一句「被你说醒了」 |

**`decide()` 的完整返回值结构**：返回一个 `Reaction`，字段语义与每个分支的取值如下。

| 分支（判定顺序） | mode | will_reply | delay_ms | hint | prompt_hint |
|---|---|---|---|---|---|
| 睡状态 且 上一轮已 ≥ 0.80（已聊醒） | `awake` | True | 600 | `""` | `""`（不再每轮塞"被叫醒"） |
| 睡状态，本句后 level < 0.25 | `asleep` | True | 1500 | 见上表 | `【现在的状态】<asleep 提示>`，`activity` 非空时再拼 `（她今晚的作息：<activity>）` |
| 睡状态，本句后 0.25–0.55 | `drowsy` | True | 2200 | 她被你聊得清醒了一点。 | `【现在的状态】<drowsy 提示>` |
| 睡状态，本句后 0.55–0.80 | `sleepy` | True | 1600 | `""` | `【现在的状态】<sleepy 提示>` |
| 睡状态，本句后 ≥ 0.80 | `awake` | True | 900 | 聊着聊着，她清醒过来了。 | 固定一句"你本来快睡了，被他一句一句聊醒了…" |
| 白天，activity 命中 `_BUSY_ACTIVITY` | `busy` | True | 4200 | `""` | `你正在{activity}，只能抽空回一两句。句子短，不展开。` |
| 白天，`energy <= 25` | `sleepy` | True | 2600 | `""` | 精力很低，快撑不住了但还想说话，短、软、粘，别赶人 |
| 白天，`0 <= hour <= 5` | `awake` | True | 1200 | `""` | 深夜还有点困但醒着，话比白天少，别只回一个「嗯」 |
| 其余（默认落地） | `awake` | True | 0 | `""` | `""` |

判定细节：`sleepish = sleeping or any(k in activity for k in ("睡",))`；睡状态下每个分支之前都会先调 `bump_awake()`；白天分支也调一次 `bump_awake()` 但丢弃返回值（作用是维持"聊着聊着更精神"的连续性，白天不做衰减判断）。

**`apply_delay`**：`[max(200, base + 420 + len(s) * 28 + random.randint(-80, 220)) for s in segments]`，`base = max(0, int(reaction.delay_ms))`。所以最终延迟 = 策略延迟 + 420ms 固定 + 每字 28ms + `-80~+220` 抖动。`loop.py:347` 另有一条外部规则：首次见面且 `delay_ms == 0` 时改成 1800。

**数据结构 / 存储**：

- kv：`awake_level`、`awake_ts`（两个键都是字符串）。
- 没有任何表写入；`Reaction` 是纯 dataclass。

**耦合**：被 `app/mind/loop.py:346` 每轮调用（`recent_user = _recent_user_turns(10, uid, chat_type)`）；`reaction.prompt_hint` 进 `prompt.py` 的【此刻的状态】段（`prompt.py:380`），`reaction.delay_ms` 进 `apply_delay` 后作为 `delays_ms` 返回给前端/通道。

**坑**：

1. `will_reply` 恒为 `True`：源码里已经没有任何返回 `False` 的分支，`loop.py:349` 的 `if not reaction.will_reply` 是不可达分支（保留兼容）。
2. `decide()` 的 `persona` 形参**从未被使用**，是死参数；阶段/作息信息全靠 `life` dict。
3. `_SLEEP_HINTS` 里每档带的延迟数值（1500/2200/1600/900）与分支里硬编码的 `delay_ms` 是两套重复数据，只改一处会漂移。
4. kv 读写全吞异常（`_kv_get` / `_kv_set` 都是 try/except），数据库不可用时清醒度恒读 0 → 永远落在 asleep 档，这是有意的降级而不是 bug。
5. `bump_awake` 写回的 `level` 是**写回那一刻**的值，之后读时才衰减；所以连续两次 `awake_level()` 之间不写库就不会过期。
6. 白天也会抬清醒度，所以 `awake_level` 实际语义是"最近有多活跃"，不只是"睡醒程度"。

### 3.4 `app/life/diary.py`（189 行）

**职责**：每天生成一段只给她自己看的第一人称日记，第二天第一次聊天时提一次。它不是用户记忆，是她的生活痕迹：正文同时落 `memories(kind='diary')` 与 kv `diary:<day>`，读给模型的是 kv，这样"第二天提起"有确定的时间锚点。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `DIARY_KIND` | `"diary"` | 记忆 kind |
| `get` | `get(day: str) -> dict[str, Any] \| None` | 读某天条目（JSON 坏值当没有） |
| `recent` | `recent(limit: int = 7) -> list[dict[str, Any]]` | 按 key 倒序取最近几天的条目 |
| `note_event` | `note_event(content: str, *, topic: str = "相处", user_id: int \| None = None) -> int` | 追加一条"小事记"（写 `human_notes` + 记忆） |
| `ensure` | `async ensure(rt, persona) -> dict[str, Any] \| None` | 新的一天第一次聊天时补写昨天 |
| `compose_for` | `async compose_for(rt, persona, day: str) -> dict[str, Any] \| None` | 手动给某天补写（已存在直接返回旧的） |
| `prompt_line` | `prompt_line() -> str` | 第二天给模型的一句提示（引用过就返回空串） |
| `mark_referenced` | `mark_referenced() -> None` | 记下"今天已经提过日记" |
| `status` | `status() -> dict[str, Any]` | 面板快照（6 键：today / last_written / referenced_on / has_yesterday / count / items） |

四个内部辅助（共 12 个 def）：`_day_key(day: str) -> str`（拼 `diary:<day>`）、`_week_events(day: str, limit: int = 40) -> list[dict[str, Any]]`（取某天的事件）、`_save(day: str, text: str, source_ids: list[int]) -> dict[str, Any]`（写 kv + 写记忆）、`_compose(rt, persona, events: list[dict[str, Any]]) -> str`（异步，调模型生成正文）。

**什么时候生成**：`ensure()` 在 `app/mind/loop.py:334` 每轮对话开头被调，内部靠 kv 短路，实际一天只判一次。

1. `today = clock.now().date().isoformat()`；若 `kv diary_last_date == today` 直接返回 `None`。
2. **先** 写 `kv diary_last_date = today`（先记账，避免每次聊天都探一次）。
3. 取昨天的事件：`SELECT id, ts, role, text FROM events WHERE ts LIKE '<yesterday>%' AND role IN ('user','her') AND kind = 'text' ORDER BY id LIMIT 40`，过滤空文本。
4. 没有事件 → 返回 `None`（**不编造生活**）。
5. `_compose()`：取最后 20 条拼成 `他：…` / `我：…`（每条截 60 字），把 `persona.schedule()` 里所有活动名用 `、` 连起来作为"你平常的一天大概是："；system 要求第一人称、2–4 句、口语短句、不复述原话、不提 AI/模型/聊天记录；`models.pick("reflect")`；`max_tokens=220`、`temperature=0.9`；用 `appraise.strip_actions()` 清掉动作旁白。
6. 模型抛异常或返回空 → 兜底取她昨天最后一句；连最后一句都没有就用 `"今天也就这样，没什么可写的。"`；最后 `text[:300]`、换行换空格。
7. `_save()`：写 kv `diary:<day>` = `{"day", "text", "written_at"}`（`ensure_ascii=False`），同时 `h2.add("diary", f"[{day}] {text}", importance=0.8, source_ids=[事件 id…])`。

**写在哪**：kv `diary:YYYY-MM-DD` 是正文真身；`memories` 视图里 `kind='diary'` 的行用于检索（`memories` 是只读视图，写入走 `h2.add`）。

**第二天怎么被提起**：

1. `prompt_line()`：若 `kv diary_ref_date == today` 返回 `""`；否则读昨天的 `diary:<yesterday>`，没有条目返回 `""`；有则返回三行：`（<日期> 你写下的日记）` + 正文 + `可以顺口提一句，不要逐字念，也不要说「我日记里写了」这种话。`
2. `loop.py:441` 拿到 `diary_hint`，作为 `diary=` 参数传给 `prompt.build()`，进 system 的【你昨天的日记】段（`app/mind/prompt.py:374`）。
3. 这一轮回复成功返回后，`loop.py:910` 调 `mark_referenced()` 把 `diary_ref_date` 写成今天——所以一天只提一次，之后靠正常记忆检索。

**kv 三个键**：`diary_last_date`（写到哪天，防重复尝试）、`diary_ref_date`（哪天提过，防重复提）、`diary:<day>`（正文 JSON，`recent(30)` 用它算条数）。

**`note_event` 做什么**：群昵称变更时会调（`app/group/context.py:53` 传入"我开始叫他「X」…"）。它写两处：`observe.add_note(topic, content, confidence=0.9, user_id=…)` 进 `human_notes` 的 `topic` 列（该列只认 `observe.NOTE_TOPICS = ("习惯", "说话方式", "情绪", "仪式", "相处")`，不在表里的一律落 `习惯`，正文截 400 字），以及 `h2.add("diary", f"[{today}] {content}", importance=0.7, user_id=…)`。`observe.add_note` 抛异常不影响记忆写入。

**耦合**：被 `loop.py:334`（生成）、`loop.py:441/910`（提起）调用；`compose_for` 由 `POST /v1/diary/run` 调（`app/api/routes.py:904`），`status` 由 `GET /v1/diary` 调；`note_event` 被 `app/group/context.py` 调；内部调 `models.pick("reflect")`、`h2.add`、`appraise.strip_actions`、`clock.now`。

**坑**：

1. `_week_events` 只过滤 `role IN ('user','her')` 与 `kind='text'`，**不过滤 `chat_type`**：群里别人说的话也会进"昨天发生的事"，进而写进她的日记。
2. `ensure()` 先写 `diary_last_date` 再去 compose：如果 `_compose` 之后抛了非捕获异常（例如 `_save` 里 kv 写失败），今天不会重试。
3. `recent()` 直接 `SELECT value FROM kv WHERE key LIKE 'diary:%' ORDER BY key DESC`，所以它同时会扫到名字以 `diary:` 开头的其它键（当前只有按天条目）。
4. 日记字数上限是 300 字（`text[:300]`），比 prompt 里要求的 2–4 句宽松，模型写长会被硬截。
5. `compose_for` 不去重 `source_ids`，同一天重写会再 `h2.add` 一次（同一 `(kind, content, user, chat)` 重复写只会加温，不新增行）。

### 3.5 `app/life/human_life.py`（554 行）

**职责**：把 `data/human_life/_derived` 里的作息语料读进运行时，用"人类语料"接管她的活动/精力/睡没睡。**只读接管**：不写库、不动 persona 作息表，异常一律降级成 `ok=False`，让上层继续跑老逻辑。

**语料从哪来**：`CORPUS_DIR = DATA_ROOT / "data" / "human_life" / "_derived"`；`corpus_dir(dir=None)` 的优先级是「入参 > 环境变量 `AI_LIFE_HUMAN_LIFE_DIR` > CORPUS_DIR」。仓库里实际存在的文件：`rhythm.workday.jsonl`、`rhythm.restday.jsonl`、`rhythm.special.jsonl`、`rhythm.user_ad_workday.jsonl`、`rhythm.user_sick_day.jsonl`、`activity.jsonl`、`activity.user_ad.jsonl`。读取用的 glob 是 `rhythm.*.jsonl` + `activity*.jsonl`。

**语料形状**（真实第一行）：`{"id": "user_ad_day_a", "kind": "rhythm", "day_type": "workday", "segments": [{"start": "00:00", "end": "08:20", "label": "sleep"}, …]}`。一行一份作息；`day_type` 记在记录里，`_day_type_of()` 在没有该字段时才从文件名 `rhythm.<day_type>.jsonl` 抠（抠不到默认 `workday`）。

**活动枚举与三档映射**：`ACTIVITY_MAP` 共 13 项，值是 `(中文短语, 精力 0–100, 睡没睡)`。

| label | 短语 | energy | sleeping |
|---|---|---|---|
| `sleep` | 睡着了 | 10 | True |
| `meal` | 在吃饭 | 45 | False |
| `work` | 在上班 | 55 | False |
| `commute` | 在路上 | 35 | False |
| `chores` | 在收拾屋子 | 40 | False |
| `study` | 在看书学东西 | 45 | False |
| `leisure` | 在放松 | 55 | False |
| `social` | 在跟人说话 | 50 | False |
| `screen` | 在刷手机 | 40 | False |
| `selfcare` | 在洗漱收拾 | 45 | False |
| `errand` | 在外面办事 | 35 | False |
| `exercise` | 在运动 | 30 | False |
| `other` | 在发呆 | 50 | False |

不认识的 label 一律按 `other` 落（`_FALLBACK`），避免一行脏数据让整个时刻变 `None`。再上一层 `ACTIVITY_PHRASES` 给同一 label 提供 2–5 种口语说法，"同一段 + 同一天给同一句"靠 `hash(f"{label}:{日期}:{start_min}") % len(options)` 选定，所以状态卡不会每秒换词。

**日型与选档**：

| 常量 | 值 |
|---|---|
| `KV_ENABLED` | `"human_life_enabled"`（默认 `"0"`，关着） |
| `KV_FORCE_DAY_TYPE` | `"human_life_force_day_type"` |
| `DAY_TYPES` | `("workday", "restday", "holiday", "travel", "special")` |
| `PREFERRED_IDS` | `{"workday": ("user_ad_day_a", "user_ad_day_b", "user_ad_day_c"), "special": ("user_sick_day",)}` |
| `CACHE_TTL` | `60.0` 秒 |
| `DEFAULT_SUBDIR` | `("data", "human_life", "_derived")` |

选档顺序（`resolve`）：kv 日型覆盖 > 显式 `day_type` 参数 > `day_type_for(moment)`（周六周日 = `restday`，其余 `workday`）。`_pick_rhythm()` 先在目标日型里找 `PREFERRED_IDS` 的候选 id，多份自述时按 `moment.date().toordinal() % len(preferred)` 轮换；没有优先档就取文件顺序第一条（`stat_*_average` 排最前）；整个日型没货就退回 `workday`。

**怎么读进运行时**：三个消费点，全部只读、全部吞异常。

1. `clock.get_state()`：`if not dev_awake and human_life.enabled(): hl = human_life.resolve(dt)`，命中覆盖 `activity` / `energy` / `sleeping`（`clock.py:57-66`）。
2. `loop.py:312-318`：`human_life.today_line()` 得到一段【你今天】注入（`prompt.py:369`）。
3. `proactive.py:_day_state()`：拿 `resolve()` 的短语当主动开口的第二优先话题。

**读不到时的降级**：

| 情况 | 表现 |
|---|---|
| kv 开关是 `"0"`（默认） | `enabled()` 返回 False，`clock` 行为一个字都不变 |
| 目录不存在 | `_read` 返回 `ok=False`、`errors=1`，`resolve` 返回 `None` |
| 行坏了 / 段全坏 | 坏行计数进 `errors`，好记录照用；整份语料无有效作息时 `ok=False` |
| kv 读不到（库没初始化） | `enabled()` 返回 False，`force_day_type()` 返回 `""` |
| 任何异常 | `resolve` / `today_line` 外层 try/except，返回 `None` / `""` |

`snapshot()` 永远能返回：开关关着、没语料、时刻落在段缝里，都只是 `enabled`/`ok` 为 `false`、相关字段 `null`，调用方可以无条件 200（`GET /v1/human_life/now`）。

**函数清单**（`grep '^\s*def '` 计 21 个，全列）：

| 符号 | 签名 | 一句话 |
|---|---|---|
| `_phrase_for` | `_phrase_for(label: str, moment: datetime, start_min: int) -> str` | 按 label/日期/段起点哈希选一句口语说法 |
| `corpus_dir` | `corpus_dir(dir: Any = None) -> pathlib.Path` | 入参 > 环境变量 > `DATA_ROOT` 下默认目录 |
| `enabled` | `enabled() -> bool` | 读 kv 开关，读失败当关着 |
| `set_enabled` | `set_enabled(flag: bool) -> None` | 写开关，写失败不抛 |
| `reset_cache` | `reset_cache() -> None` | 清内存缓存（测试换目录用） |
| `force_day_type` | `force_day_type() -> str` | 读日型覆盖，非法/空当自动 |
| `set_force_day_type` | `set_force_day_type(value: str) -> None` | 写日型覆盖，非法值抛 `ValueError` |
| `_signature` | `_signature(d: pathlib.Path) -> tuple` | 文件指纹（名字 + mtime_ns + 大小） |
| `_read_jsonl` | `_read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]` | 逐行读 jsonl，返回（记录, 坏行数） |
| `_to_minute` | `_to_minute(text: Any) -> int \| None` | `"HH:MM"` → 当天第几分钟，`"24:00"`=1440 |
| `_clean_segments` | `_clean_segments(raw: Any) -> list[dict]` | 洗成 `[{start, end, label}]`，丢坏段 |
| `_day_type_of` | `_day_type_of(record: dict, filename: str) -> str` | 记录自带优先，否则从文件名抠 |
| `_read` | `_read(d: pathlib.Path) -> dict` | 真读盘，目录不在/全坏给 `ok=False` |
| `load` | `load(dir: Any = None) -> dict` | 带 60 秒 + 指纹缓存地读，永不抛 |
| `status` | `status() -> dict` | 面板摘要（9 键：ok/dir/rhythms/activities/by_day_type/enabled/loaded_at/errors/force_day_type） |
| `day_type_for` | `day_type_for(moment: datetime) -> str` | 周六周日 restday，其余 workday |
| `_pick_rhythm` | `_pick_rhythm(data: dict, day_type: str, moment: datetime \| None = None) -> dict \| None` | 同日型内优先选"她自己的一天"，否则第一条 |
| `resolve` | `resolve(moment: datetime, day_type: str \| None = None) -> dict \| None` | 时刻 → 她在干嘛（11 键），拿不到 `None` |
| `now_local` | `now_local() -> datetime` | UTC+08 当前时间 |
| `snapshot` | `snapshot(moment: datetime \| None = None) -> dict` | 紧凑状态卡（含 `seconds_left`、`corpus` 健康度） |
| `today_line` | `today_line(moment: datetime \| None = None) -> str` | 给 prompt 的一句【你今天】，拿不到返回 `""` |

`resolve()` 返回值 11 个键：`activity`、`phrase`、`energy`、`sleeping`、`label`、`source`（作息 id）、`day_type`、`segment`（`["HH:MM", "HH:MM"]` 两元素）。

`today_line()` 的文案：`今天是{day_word}，你现在的状态：{phrase}。这就是你今天的日常，不是他跟你讲过的事；聊到工作时可以说具体在忙什么，但不要编客户名、方案名、同事名。` 后面按情况追加尾巴——睡着加`；这个点你本来该睡着，被抓到会有点不好意思`，`energy <= 25` 加`；你没什么力气，说话会短一点`，`energy >= 60` 加`；你精神还行`。`_DAY_TYPE_WORDS` 5 项：workday 工作日 / restday 休息日 / holiday 节假日 / travel 出差·旅行 / special 特殊的一天（不太舒服/熬夜）。

**缓存**：`_CACHE` 只存一份 `{"dir", "sig", "at", "data"}`；命中条件是目录字符串相同、指纹相同、`time.monotonic() - at < 60.0`。指纹由参与解析的每个文件的 `(name, st_mtime_ns, st_size)` 组成，语料是静态文件，这样避免每次 `get_state` 都读盘。

**坑**：

1. `errors` 把三种情况混在一个计数里：坏 JSON 行、段全坏的记录、目录不存在（记 1）。
2. 精力是离散台阶（sleep 10 / exercise 30 / work 55），不是连续量，开关一开就能在槽位边界看到精力跳变。
3. `set_force_day_type(None)` 走的是 `"" if value is None else …`，能正常清空，但类型注解写的是 `str`。
4. `_to_minute` 允许 `"24:00"`（1440），所以 segment 的 `end` 可以等于 1440，`snapshot` 里 `seconds_left` 用 `min(86400, end*60) - 当天秒数` 封顶。
5. `activity*.jsonl` 只统计条数（`activities`）与留档（`activity_records`），`resolve` 完全不参与——语料里的活动条目当前不产生任何行为。
6. `snapshot()` 在函数末尾 `try` 里只 `return card`，异常路径返回的是**已经被 corpus/day_type 填过的那版 card**，字段停在半填状态（`enabled` 已写、`activity` 仍为 `None`）。

### 3.6 `app/life/proactive.py`（863 行 / 53 个函数）

**职责**：按"人"主动外发（只私聊），四件事：等久了主动找你、发过消息不回就问、一直被晾着进生气支线、主动开口时把昨晚的念头带出去。三道闸门 + 一道身体闸门焊在 `gate()` 里。

**优先级**：有据念头 > 她今天的生活 > 恋人暗示 > 锚点寒暄。

**先给全函数表（53 个）**：

| # | 符号 | 签名 | 一句话 |
|---|---|---|---|
| 1 | `_now` | `_now() -> datetime` | 本地时区当前时刻（带 tzinfo） |
| 2 | `_ts_of` | `_ts_of(value: str) -> datetime \| None` | ISO 串转带时区时间，坏值 `None` |
| 3 | `_last_user_ts` | `_last_user_ts() -> datetime \| None` | 全库最后一条 user 事件时刻 |
| 4 | `_last_any_ts` | `_last_any_ts() -> datetime \| None` | 全库最后一条事件时刻 |
| 5 | `_append` | `_append(lines: list[str], source: str = "proactive") -> list[dict[str, Any]]` | 面板线落库：先写全局 `proactive_last_ts` |
| 6 | `_decay_aversion` | `_decay_aversion(now: datetime) -> None` | 闲置≥3 天每天 -2.5 厌恶，单次封顶 -10，每天一次 |
| 7 | `_cfg_int` | `_cfg_int(key: str, default: int) -> int` | 读整数 kv，坏值退回默认 |
| 8 | `enabled` | `enabled() -> bool` | kv `proactive_enabled` 默认 `"1"` |
| 9 | `cooldown_min` | `cooldown_min() -> int` | 冷却分钟，默认 20，最小 1 |
| 10 | `daily_max` | `daily_max() -> int` | 每人每天上限，默认 6，0 = 不限 |
| 11 | `thought_daily_max` | `thought_daily_max() -> int` | 每天最多带几条念头，默认 2，0 = 不许带 |
| 12 | `thought_carry_enabled` | `thought_carry_enabled() -> bool` | 念头带出总开关（环境变量 `AI_LIFE_PROACTIVE_THOUGHT` 优先） |
| 13 | `today_count` | `today_count() -> int` | 今天全库 `source='proactive'` 的条数 |
| 14 | `quiet_hours` | `quiet_hours() -> tuple[int, int]` | 夜间静默区间（起, 止） |
| 15 | `in_quiet_hours` | `in_quiet_hours(now: datetime \| None = None) -> bool` | 现在是否静默时段，起=止视为关闭 |
| 16 | `_minutes_until_morning` | `_minutes_until_morning(now: datetime) -> int` | 到静默结束点还有多少分钟（≥1） |
| 17 | `detect_dnd` | `detect_dnd(text: str, now: datetime \| None = None) -> tuple[int, str] \| None` | 判"他要安静"：(分钟, 理由)，0 = 解除 |
| 18 | `set_dnd` | `set_dnd(user_id: int, minutes: int, reason: str = "", *, now: datetime \| None = None) -> dict[str, Any]` | 写 `proactive_dnd_until/reason:<uid>` |
| 19 | `clear_dnd` | `clear_dnd(user_id: int) -> None` | 两个 dnd kv 写成空串 |
| 20 | `dnd_status` | `dnd_status(user_id: int, *, now: datetime \| None = None) -> dict[str, Any]` | 读免打扰状态，过期就地清 |
| 21 | `apply_dnd` | `apply_dnd(user_id: int, detected: tuple[int, str] \| None, *, now: datetime \| None = None) -> dict[str, Any]` | 把 `detect_dnd` 结果落盘并回最新状态 |
| 22 | `_sleeping_at` | `_sleeping_at(persona, moment: datetime) -> bool` | 按作息判某一刻睡没睡（`dev_awake` 优先） |
| 23 | `_ukey` | `_ukey(kind: str, user_id: int) -> str` | 拼 `proactive_<kind>:<uid>` |
| 24 | `_thought_key` | `_thought_key(user_id: int, now: datetime) -> str` | 拼 `proactive_thought_count:<uid>:<date>` |
| 25 | `_user_today_thoughts` | `_user_today_thoughts(user_id: int, *, now: datetime \| None = None) -> int` | 这人今天已带出几条念头 |
| 26 | `_bump_thought_count` | `_bump_thought_count(user_id: int, *, now: datetime \| None = None) -> int` | 念头计数 +1 并返回新值 |
| 27 | `_user_last_event` | `_user_last_event(user_id: int, chat_type: str = "private") -> tuple[datetime \| None, int]` | 这人最后一次说话的时刻与事件 id |
| 28 | `_user_last_ts` | `_user_last_ts(user_id: int, chat_type: str = "private") -> datetime \| None` | 上一项只取时刻 |
| 29 | `_last_user_text` | `_last_user_text(user_id: int, chat_type: str = "private") -> str` | 这人最后一句话原文 |
| 30 | `_day_state` | `_day_state(moment: datetime) -> dict[str, Any] \| None` | 她此刻在干嘛（human_life 接管，睡着给 `None`） |
| 31 | `_user_today_count` | `_user_today_count(user_id: int, *, now: datetime \| None = None) -> int` | 这人今天她的主动条数（两种 source 都数） |
| 32 | `_unanswered_proactive` | `_unanswered_proactive(user_id: int) -> int` | 连着几条主动没人接 |
| 33 | `_sleeping_gate_message` | `_sleeping_gate_message() -> str` | 返回固定串「她这个点睡着了」 |
| 34 | `gate` | `gate(rt, persona, user_id: int, chat_type: str = "private", *, now: datetime \| None = None) -> tuple[bool, str]` | 全部闸门，返回 (能不能, 原因) |
| 35 | `_mark_window` | `_mark_window(user_id: int, last_id: int) -> None` | 写 `proactive_window:<uid>` = `"<uid>:<last_id>"` |
| 36 | `_commit_thought` | `_commit_thought(user_id: int, item: dict[str, Any], *, now: datetime \| None = None) -> bool` | 念头说出口：落浮现台账 + 扣当天额度 |
| 37 | `decide` | `decide(rt, persona, user_id: int, chat_type: str = "private", *, now: datetime \| None = None) -> dict[str, Any]` | 这次说什么：`{ok, reason, kind, item, user_id, stage}` |
| 38 | `_day_line` | `_day_line(item: dict[str, Any] \| None) -> str` | 把"她今天在干嘛"套成一句模板（≤80 字） |
| 39 | `_fallback_lines` | `_fallback_lines(user_id: int, dec: dict[str, Any]) -> list[str]` | 今天生活 → 恋人暗示 → 锚点寒暄 |
| 40 | `_append_for_user` | `_append_for_user(lines: list[str], user_id: int, chat_type: str = "private", source: str = "proactive", *, now: datetime \| None = None) -> list[str]` | 按人落库并写 `proactive_last_ts:<uid>` |
| 41 | `tick_user` | `tick_user(rt, persona, user_id: int, chat_type: str = "private", *, now: datetime \| None = None) -> list[str]` | 一步到位（模板，不调模型） |
| 42 | `_clean_speak` | `_clean_speak(text: str) -> str` | 洗模型输出：去引号、含元信息词即废、截 60 字 |
| 43 | `_speak_thought` | `async _speak_thought(rt, persona, item: dict[str, Any]) -> str` | 用模型把念头说成一条消息，失败 `""` |
| 44 | `_speak_day` | `async _speak_day(rt, persona, item: dict[str, Any]) -> str` | 用模型说"今天在干嘛"，失败 `""` |
| 45 | `prepare_tick` | `async prepare_tick(rt, persona, user_id: int, chat_type: str = "private", *, now: datetime \| None = None) -> dict[str, Any]` | 只算不落库，供真实外发 |
| 46 | `commit_tick` | `commit_tick(prepared: dict[str, Any]) -> list[str]` | 真发成功才记账：台账/窗口/事件/冷却 |
| 47 | `tick_user_async` | `async tick_user_async(rt, persona, user_id: int, chat_type: str = "private", *, now: datetime \| None = None) -> list[str]` | `prepare` + `commit`（面板/测试） |
| 48 | `proactive_intimate` | `proactive_intimate() -> bool` | kv `proactive_intimate` 是否 `"1"` |
| 49 | `set_proactive_intimate` | `set_proactive_intimate(on: bool) -> None` | 写这个 kv |
| 50 | `settings` | `settings() -> dict[str, Any]` | 全局设置快照（13 键） |
| 51 | `settings_for` | `settings_for(user_id: int, *, now: datetime \| None = None) -> dict[str, Any]` | 这个人身上的主动状态（12 键） |
| 52 | `tick` | `tick(rt, persona) -> list[dict[str, Any]]` | 面板单用户线的推进（含 nag/anger） |
| 53 | `pending_messages` | `pending_messages(after_id: int = 0) -> list[dict[str, Any]]` | 拉主动消息（`id`/`text`/`ts`） |

**gate() 的判定顺序**（顺序即优先级，命中即返回原因）：

| # | 条件 | 拒绝原因 |
|---|---|---|
| 1 | `uid <= 0` 或 `chat_type != "private"` | 只对私聊主动 |
| 2 | `users.is_blocked(uid)` | 他已经被拉黑 |
| 3 | `not enabled()` | 主动开关关着 |
| 4 | `in_quiet_hours(moment)` | 夜间静默（23:00–08:00） |
| 5 | 他本人的 DND 生效，或全局 `uid=0` 的 DND 生效 | 免打扰（<理由>，还剩 N 分钟） |
| 6 | `_sleeping_at(persona, moment)` | 她这个点睡着了 |
| 7 | 这人从没说过话 | 还没聊过 |
| 8 | `stage` 不在 `IDLE_MINUTES` 里 | 关系还没到会主动的阶段 |
| 9 | `idle_min < threshold` | 才安静了 N 分钟（这个阶段要 M 分钟） |
| 10 | 距 `proactive_last_ts:<uid>` < `cooldown_min()` 分钟 | 冷却中 |
| 11 | `proactive_window:<uid>` 已等于 `"<uid>:<last_id>"` | 这个静默窗口已经主动过一次 |
| 12 | `used >= daily_max()`（`daily_max()>0` 时） | 今天的主动次数用完了（x/y） |
| 13 | 连着 ≥2 条主动没人接 | 连着发了 N 条还没人接 |
| — | 全部通过 | 可以主动 |

**等多久才算"久"**：`IDLE_MINUTES = {5: 30, 4: 40, 3: 90, 2: 240, 1: 480, 0: 360}`，键是 `persona.stage_index(affinity)`。人设 6 档（observer 0 / swimmer 20 / echo 40 / samefreq 60 / lover 80 / bond 95）与这 6 个键一一对应，最黏的共生档 30 分钟就来找，观察者档反而 360 分钟（比"同游者"的 480 分钟还早，这是 `0` 这个键的特例值）。

**免打扰（DND）**：`detect_dnd()` 是唯一入口，`loop.py:287` 每收到他一句话调一次。六条正则与对应时长：

| 正则集 | 命中举例 | 分钟 | 理由 |
|---|---|---|---|
| `_DND_CLEAR` | 忙完了 / 我回来了 / 在吗 | 0（解除） | 他说他忙完了 |
| `_DND_SLEEP` | 先睡了 / 我困了 / 睁不开眼 | 夜里→到静默结束；白天 90 | 他说要睡了 |
| `_DND_NAP` | 眯一会儿 / 打个盹 | 60 | 他说要眯一会儿 |
| `_DND_QUIET` | 别吵 / 别打扰 / 安静点 | 240 | 他说别吵他 |
| `_DND_SHORT` | 不聊了 / 先这样 / 我下线 | 30 | 他说先不聊了 |
| `_DND_BUSY` | 在开会 / 加班 / 在开车 | 120 | 他说在忙 |

"夜里"的判定是 `in_quiet_hours(moment) or hour >= 21 or hour < 6`。`_DND_CLEAR` 里含"在吗 / 在不在 / 你人呢"，所以他随口一句"在吗"就会把免打扰清掉。落盘两个键：`proactive_dnd_until:<uid>`（带时区 ISO）、`proactive_dnd_reason:<uid>`。`dnd_status()` 读到过期时间会**顺手清掉**这两个键。`uid=0` 是面板的"全局免打扰"：面板上点一下，QQ 私聊也不许打扰。

**两段式 prepare → send → commit**：真实外发在 `app/main.py:52-98` 的 startup worker 里（`onebot.enabled` 且 `onebot.proactive_push` 都为真才起，间隔 `max(60, proactive_interval_min * 60)` 秒，默认 `proactive_interval_min = 5`）。

1. 遍历 `SELECT id, openid FROM qq_user WHERE blocked = 0`，对每个人 `await prepare_tick(rt, rt.persona, id, "private")`。
2. `prepare_tick` 内部：`decide()` 拿 `kind`/`item` → 按 kind 生成文案（`thought` 先 `_speak_thought` 再退 `thoughts.proactive_line`；`day` 先 `_speak_day` 再退 `_day_line`）→ 都没有就用 `_fallback_lines`。
3. **生成之后再校验一次窗口**：模型调用占几百毫秒，期间他若又说话（`_user_last_event` 的 id 变了），本次直接作废，返回 `{"ok": False, "reason": "他刚说话了，这次不主动"}`。
4. 返回 `{"ok", "lines", "item", "kind", "user_id", "last_id", "now"}`，**此时一个字都还没落库**。
5. 真的调用 `send_private_msg` 成功后，才 `commit_tick(prepared)`：`_commit_thought`（念头落台账 + 扣额度）→ `_mark_window` → `_append_for_user`（写事件 + 写 `proactive_last_ts:<uid>`）。
6. 发送失败（`_post_api` 返回 `None`）→ `ob_mod._append_outbox({...})` 追加到 `data/onebot_outbox.jsonl`，**不记账**：配额不扣、窗口不记，下一轮还有机会，绝不会出现"她说了但你没收到还扣了次数"。

**面板线（`tick`）与 QQ 线的差别**：`tick()` 只跑面板单用户（`session.status == "active"` 且 `name_locked`），用全局 kv `proactive_last_ts` / `proactive_window` / `proactive_nag` / `proactive_angry`；配额用 `today_count()`。两条线各有一套键，互不影响。`tick` 的三段推进：窗口没发过 → 发锚点寒暄；发过且闲置 ≥ `threshold*2` → `_NAG`（「你在干嘛。」「为什么不理我。」）+ 厌恶 +1.5；闲置 ≥ `threshold*4` → `_ANGER`（「你是一点都不想回我。」「算了，你忙。」）+ 厌恶 +4.0。每个窗口只各触发一次。

**文案与清洗**：`_fallback_lines` 的顺序是「`day` → `intimate` → `anchor`」；`intimate` 只在 `users.lover_of(uid) is not None and proactive_intimate()` 时出现，取 `intimacy.exemplars.by_kind("metaphor")[:1]`；`anchor` 走 `anchors_mod.pick_dynamic(scene, max(stage,0), 2)`，`scene = "撒娇" if stage >= 3 else "关心"`，取不到就用「在干嘛。」（stage ≥ 3）或「今天还好吗。」。`_clean_speak` 的规则：换行压成空格、去首尾 `「」“”"'` 和空格、**命中 `_SPEAK_BAN`（记忆/记录/系统/念头/数据库/提示词/模型/训练）任一就整句作废**、超过 60 字硬截、短于 3 字作废。

**kv 键总表**：

| 键 | 值域 | 用途 |
|---|---|---|
| `proactive_enabled` | `"1"`/`"0"`，默认 `"1"` | 总开关 |
| `proactive_cooldown_min` | 整数，默认 20 | 冷却分钟 |
| `proactive_daily_max` | 整数，默认 6 | 每人每天上限，0 = 不限 |
| `proactive_thought_max` | 整数，默认 2 | 每天带几条念头 |
| `proactive_thought_enabled` | `"0"` 关闭，默认开 | 念头带出开关（环境变量 `AI_LIFE_PROACTIVE_THOUGHT=0` 优先） |
| `proactive_intimate` | `"1"`/`"0"` | 恋人暗示是否放行 |
| `proactive_quiet_start` / `proactive_quiet_end` | 小时，默认 23 / 8 | 夜间静默区间 |
| `proactive_last_ts` | ISO 时间 | 面板线上次主动 |
| `proactive_last_ts:<uid>` | ISO 时间 | 这人上次主动（冷却用） |
| `proactive_window` | 用户消息 ISO 时间 | 面板线窗口 |
| `proactive_window:<uid>` | `"<uid>:<last_id>"` | 这人这个窗口已经主动过 |
| `proactive_nag` / `proactive_angry` | 窗口 ISO 时间 | 面板线催/生气的去重 |
| `proactive_dnd_until:<uid>` | 带时区 ISO | 免打扰到期时刻 |
| `proactive_dnd_reason:<uid>` | 文本 | 免打扰理由（展示给用户看） |
| `proactive_thought_count:<uid>:<date>` | 整数 | 这人今天带了几条念头 |
| `aversion_decay:<date>` | `"done"` | 厌恶衰减今天做过了 |

**settings() 返回 13 键**：`enabled`、`cooldown_min`、`daily_max`、`thought_daily_max`、`thought_carry`、`quiet_start`、`quiet_end`、`night_silent_now`、`dnd`（`uid=0`）、`idle_minutes`（键已转成字符串）、`last_ts`、`today_count`、`intimate`。**settings_for() 返回 12 键**：`user_id`、`daily_max`、`today_count`、`thought_daily_max`、`today_thoughts`、`quiet_start`、`quiet_end`、`night_silent_now`、`dnd`、`unanswered`、`cooldown_min`、`last_ts`。

**坑**：

1. `today_count()` 只数 `source='proactive'`，**不含 `proactive_thought`**；而 `_user_today_count()` 两种都数。所以面板「今日 N 条」会漏掉带念头发出的那种。
2. `_append()`（面板线）在循环外先写全局 `proactive_last_ts`，再逐条 `add_event`；`_append_for_user()` 用同一个 ISO 时间戳写给每个人。
3. `gate()` 第 1 条就把非私聊挡掉，所以群聊永远不走这条主动链路（群主动见 docs/11）。
4. `_user_last_event` 的 SQL 条件是 `(chat_type = ? OR chat_type = '')`，会把他早期在面板里说的话也算进这个人头上。
5. `_commit_thought` 依赖 `thoughts.mark_surface()` 返回 `True` 才扣念头额度，而 `mark_surface` 用的是 `INSERT OR IGNORE` 并且无条件 `return True`——撞唯一索引（已经提过）也算成功，额度照样扣。
6. `set_dnd` 内部 `minutes = max(1, int(minutes))`，所以"解除"只能走 `clear_dnd`；`apply_dnd` 里 `<= 0` 的分支是对的，别的地方传 0 会变成 1 分钟。
7. `_sleeping_at` 故意不调 `clock.get_state`（那个函数按真实时钟算还会写库），这样才能按传入时刻判（测试/补发）。
8. `tick_user` 与 `prepare_tick` 两条路都会生成文案，但只有 `prepare_tick` 有"生成后再校验窗口"，`tick_user` 没有——模型慢的时候，一步到位版会对着已经过期的窗口说话。
9. `_decay_aversion` 的 `aversion_decay:<date>` 只按自然日去重，跨零点那一刻不会重算闲置天数。

### 3.7 `app/life/thoughts.py`（843 行 / 41 个函数）

**职责**：让她一个人在的时候想点事，并把想的事写回记忆；第二天找合适时机提一次。引擎（`vendor/symvolve_memory`）只给"想什么"（`ThoughtSeed`）和"写回台账"，**不生成文字**，生成归宿主——这一层就是宿主那一半。

**ThoughtSeed 从哪来**：`companion.get(uid).idle(user_id=uid, limit=n)` 返回 `ThoughtSeed` 元组。它是 `@dataclass(frozen=True, slots=True)`，四个字段 `kind` / `item_ids` / `prompt` / `reason`；四种 `kind` 是 `unfinished`（未完成的约定）、`echo`（旧事回看）、`bridge`（线索桥接）、`drift`（久未提起的情绪），优先级 `_PRIORITY = {unfinished: 0, echo: 1, bridge: 2, drift: 3}`。签名 `thought_signature(seed) = f"{kind}:{'-'.join(item_ids)}"`。

**两个窗口、两套额度**：

| 项目 | 夜里（一整件事） | 白天（碎片） |
|---|---|---|
| 小时窗 | `_NIGHT_HOURS = {23, 0, 1, 2, 3, 4, 5}` | `_DAY_HOURS = range(8, 22)`（08–21 点） |
| 间隔 | `_INTERVAL_SECONDS = 3 * 3600` | 默认 4 小时（kv `idle_fragment_interval_hours`，最少 1 小时） |
| 每天上限 | 台账 `daily_limit = 3`（滚动 24 小时） | 默认 2 条（kv `idle_fragment_daily_max`，负数当 0） |
| 上次时间 | kv `idle_thought_last` | kv `idle_fragment_last` |
| 计数键 | 台账（`companion:ledger` 持久化） | kv `idle_fragment_count:<uid>:<date>` |
| 去重命名空间 | `<uid>` | `<uid>#fragment` |
| 总开关 | 环境变量 `AI_LIFE_IDLE`（`"0"` 关）+ kv `idle_thought_enabled` | kv `idle_fragment_enabled`（默认开） |
| 提示词 | `_system()`，≤40 字、像自言自语 | `_system_fragment()`，≤30 字、"闪一下" |
| 生成上限 | `max_tokens=100`，正文截 80 字 | `max_tokens=40`，正文截 40 字 |
| 记忆 meta | `grounded` | `grounded` + `fragment=True` + `day_part` |

白天碎片在念头台账里单独占 `<uid>#fragment` 命名空间，所以白天想多少都不吃夜里那 3 条/24h。

**天亮前一天最多几条念头**：夜里 3 条（滚动 24 小时）+ 白天 2 条（自然日），共 5 条。

**想什么 / 写回台账的分工**：

1. `should_try()` 是便宜判断（主链 `loop.py:325` 每轮问一次）：夜里看 `idle_thought_enabled` 与距 `idle_thought_last` 是否 ≥ 3 小时；白天看 `idle_fragment_enabled` 与 `_fragment_cooling() == 0`；都不在窗口返回 False。
2. 主链为真就 `asyncio.create_task(maybe_ruminate(...))` 丢后台，不拖慢这一轮回复。
3. `maybe_ruminate()` 再卡一道：窗口（`force=True` 可跳过）、碎片间隔、碎片额度或夜间种子冷却/夜间额度。
4. 挑种子 → `_evidence()` 捞证据（种子指向的记忆正文前 3 条、每条记忆的来源事件前 2 条、最近 8 条聊天，按 `(kind,id,text)` 去重，最多 6 条）。
5. `_generate()` 调模型（`models.pick("summary")`，`max_tokens` 40/100，碎片/夜里各一套 system）。
6. `_grounding_detail()` 定级，`mode == "none"` 时退化成复述（`_fallback_thought` / `_fallback_fragment`），`mode` 改记 `"fallback"`。
7. `h2.add("thought", text, importance=…, cues=["念头"], user_id=uid, chat_type="", extra_meta=extra_meta)` 写回记忆。
8. 记台账：夜间 `cm.thoughts.record(user_id=uid, …)` + `companion.save_ledgers()` + `kv idle_thought_last`；白天 `_bump_fragment_count` + 台账写 `<uid>#fragment` + `kv idle_fragment_last`。
9. 返回结算 dict（`ok`、`item_id`、`text`、`seed_kind`、`seed_item_ids`、`used_today`、`grounded`；白天多 `fragment`、`day_part`）。失败一律返回 `{"ok": False, "reason": …}` 或 `None`，从不硬编。

**"有据"是怎么算的**（`_grounding_detail`）：把正文和证据都转成字符 bigram 集合，`score = 共享片段数 / 正文片段数`；再抓正文里 ≥2 位的数字，记入 `new_digits`（证据里没出现过的）。

| mode | 条件 | 后续处理 |
|---|---|---|
| `clear` | `score >= 0.45`（`_CLEAR_MIN`） | 当正常回忆说 |
| `fuzzy` | `0.20 <= score < 0.45`（`_GROUND_MIN`） | 说得不确定，带"好像/记不清" |
| `dream` | `score > 0` 但 `< 0.20` | 梦/遗忘级，importance 降到 0.15，话术带"昨晚好像梦到" |
| `none` | 零个共享片段 | 判定"凭空编"→ 退化成照真实片段复述，`mode` 记 `fallback` |

`importance`：`dream` 0.15，其余 0.18。写进 `mem_item` 的 `extra_meta["grounded"]` 有 7 个键：`score`、`mode`、`shared`、`new_digits`、`fallback`、`evidence_ids`、`evidence_kinds`。

**第二天怎么被"浮现"**：核心是 `thought_surface` 表 + 五道门。

`thought_surface` 表（`app/core/db.py:263-271`）：`id` 自增、`thought_id INTEGER NOT NULL`、`user_id INTEGER DEFAULT 0`、`chat_type TEXT DEFAULT ''`、`ts TEXT`；唯一索引 `idx_thought_surface_once ON thought_surface(thought_id)` —— **一条念头一辈子只能被提一次**。

| 门 | `pick_surface`（回复时顺口提） | `pick_for_proactive`（主动开口带） |
|---|---|---|
| 时段 | 07:00–22:59（`_SURFACE_START_HOUR=7`、`_SURFACE_END_HOUR=23`） | **不看时段**（夜禁交给 proactive 的闸门） |
| 他的情绪 | `_user_is_struggling(text)` 为真则不提 | 同上 |
| 同一人冷却 | 6 小时（`_SURFACE_GAP_HOURS = 6`） | 同上 |
| 是否提过 | `thought_surface` 里没有这个 id | 同上 |
| 年龄窗 | 36 小时（`_SURFACE_MAX_AGE_HOURS`） | 参数可传，默认也是 36 小时 |
| 有据要求 | 必须有 `grounded` meta | 同上 |

`_last_surface_epoch` 的 SQL 是 `WHERE user_id = ? OR user_id = 0`，所以面板那条线（uid=0）提过念头，也会让带 uid 的人一起进冷却。

**提出来长什么样**：三条出口。

1. `surface_hint(item)`：回复时注入【夜里想的】段（`prompt.py:377`）。`clear` 说"你昨晚一个人时想过：…"；`fuzzy` 说"你记得不太清，只记得大概：…"；`dream` 说"你昨晚半梦半醒，只记得一个模糊的影子：…"。三档结尾都带"如果气氛合适可以提一句"，并声明"对方正在难受/说正事就先不提"。
2. `proactive_line(item)`：确定性模板，不调模型。`dream` → `昨晚好像梦到一点东西——<片段>`（不加句号）；`fuzzy` → `对了，好像<片段>`；`clear`/`fallback` → `对了，<片段>`，非 dream 结尾补句号；总长封顶 `_LINE_TOTAL_MAX = 90` 字。
3. `speak_prompt(item, *, persona_name="")`：交给模型说成一条她自己发的消息（`max_tokens=120`），再由 `proactive._clean_speak` 洗一遍。

`_line_fragment()` 负责洗正文：先去换行/压空格，最多循环 3 次剥掉开头的"又想起 / 忽然想起 / 刚刚 / 昨晚 / 记得"这类交代词（`_LINE_LEAD_RE`），再剃首尾标点（`_LINE_TRIM_CHARS`），超 60 字截断。

**全函数表（41 个）**：

| # | 符号 | 签名 | 一句话 |
|---|---|---|---|
| 1 | `_enabled` | `_enabled() -> bool` | 环境变量 `AI_LIFE_IDLE != "0"` |
| 2 | `_kv_int` | `_kv_int(key: str, default: int) -> int` | 读整数 kv，坏值退回默认 |
| 3 | `_fragment_enabled` | `_fragment_enabled() -> bool` | 白天碎片总开关（kv 默认开） |
| 4 | `_fragment_interval_seconds` | `_fragment_interval_seconds() -> int` | 碎片间隔秒，默认 4 小时，最少 1 小时 |
| 5 | `_fragment_daily_max` | `_fragment_daily_max() -> int` | 碎片每天上限，默认 2，负数当 0 |
| 6 | `_fragment_cooling` | `_fragment_cooling(moment: int) -> int` | 距下次可想的秒数，0 = 现在可以 |
| 7 | `_fragment_date` | `_fragment_date(moment: int) -> str` | 碎片额度的自然日字符串 |
| 8 | `_fragment_count` | `_fragment_count(uid: str, moment: int) -> int` | 这人今天已想几条碎片 |
| 9 | `_bump_fragment_count` | `_bump_fragment_count(uid: str, moment: int) -> int` | 碎片计数 +1 并返回 |
| 10 | `_fragment_namespace` | `_fragment_namespace(uid: str) -> str` | 台账命名空间 `"<uid>#fragment"` |
| 11 | `_day_part` | `_day_part(hour: int) -> str` | 白天三档 morning/noon/afternoon |
| 12 | `_hour` | `_hour(now_ts: int \| None = None) -> int` | 取小时数（传 ts 用本地时区换算） |
| 13 | `should_try` | `should_try(now_ts: int \| None = None) -> bool` | 便宜判断：现在值不值得想一轮 |
| 14 | `_system` | `_system(persona) -> str` | 夜里念头的 system 提示 |
| 15 | `_system_fragment` | `_system_fragment(persona) -> str` | 白天碎片的 system 提示 |
| 16 | `_evidence` | `_evidence(user_id: int, seed) -> list[dict[str, Any]]` | 捞证据（记忆 + 来源原话 + 最近聊天） |
| 17 | `_blob` | `_blob(evidence: list[dict[str, Any]]) -> str` | 证据拼成一段文本 |
| 18 | `_bigrams` | `_bigrams(text: str) -> set[str]` | 字符二元组集合 |
| 19 | `_grounding_detail` | `_grounding_detail(text: str, evidence: list[dict[str, Any]]) -> dict[str, Any]` | 算 score/shared/new_digits/mode |
| 20 | `_grounding_score` | `_grounding_score(text: str, evidence: list[dict[str, Any]]) -> float` | 只取 score（调试/测试用） |
| 21 | `_fallback_thought` | `_fallback_thought(evidence: list[dict[str, Any]]) -> str` | 夜里判编时的复述兜底 |
| 22 | `_fallback_fragment` | `_fallback_fragment(evidence: list[dict[str, Any]]) -> str` | 白天判编时的复述兜底 |
| 23 | `_generate` | `async _generate(rt, persona, seed, evidence: list[dict[str, Any]], *, fragment: bool = False) -> str \| None` | 调模型生成念头正文 |
| 24 | `_pick_fragment_seed` | `_pick_fragment_seed(cm, uid: str, moment: int)` | 挑今天还没想过的碎片种子 |
| 25 | `maybe_ruminate` | `async maybe_ruminate(rt, persona, user_id: int = 0, *, force: bool = False, now: int \| None = None) -> dict[str, Any] \| None` | 想一轮（夜里/白天两套额度） |
| 26 | `_epoch` | `_epoch(value: Any) -> int` | 任意时间表示 → unix 秒 |
| 27 | `_user_is_struggling` | `_user_is_struggling(text: str) -> bool` | 他是否正在难受（本地词表 + 引擎情绪） |
| 28 | `_surfaced_ids` | `_surfaced_ids(limit: int = 200) -> set[int]` | 已提过的念头 id 集合 |
| 29 | `_last_surface_epoch` | `_last_surface_epoch(user_id: int = 0) -> int` | 这人最近一次提念头的时间 |
| 30 | `_surface_cooling` | `_surface_cooling(moment: int, user_id: int = 0) -> bool` | 6 小时冷却是否还没过 |
| 31 | `_surface_candidates` | `_surface_candidates(user_id: int, moment: int, max_age_hours: int) -> dict[str, Any] \| None` | 未提过 + 年龄窗 + 必须有 grounded |
| 32 | `pick_surface` | `pick_surface(user_id: int = 0, *, now_ts: int \| None = None, text: str = "") -> dict[str, Any] \| None` | 回复时顺口提的那条 |
| 33 | `pick_for_proactive` | `pick_for_proactive(user_id: int = 0, *, now_ts: int \| None = None, max_age_hours: int = _SURFACE_MAX_AGE_HOURS, text: str = "") -> dict[str, Any] \| None` | 主动开口带出的那条（不看时段） |
| 34 | `surface_hint` | `surface_hint(item: dict[str, Any] \| None) -> str` | 念头 → 【夜里想的】提示语 |
| 35 | `_line_fragment` | `_line_fragment(text: Any) -> str` | 念头正文洗成可接模板的片段 |
| 36 | `proactive_line` | `proactive_line(item: dict[str, Any] \| None) -> str` | 确定性模板句（≤90 字） |
| 37 | `speak_prompt` | `speak_prompt(item: dict[str, Any] \| None, *, persona_name: str = "") -> tuple[str, str]` | 交给模型说成一条消息（system, user） |
| 38 | `mark_surface` | `mark_surface(thought_id: int, user_id: int = 0, chat_type: str = "", ts: str \| None = None) -> bool` | 登记"提过了"（`INSERT OR IGNORE`） |
| 39 | `surface_stats` | `surface_stats() -> dict[str, Any]` | 返回 `{"surfaced": N}` |
| 40 | `_iso` | `_iso(ts: int) -> str` | unix 秒 → 带时区 ISO |
| 41 | `recent` | `recent(user_id: int = 0, limit: int = 5) -> list[dict[str, Any]]` | 面板读最近念头（带 `surfaced`/`grounded`） |

**读写的表/键**：

- `thought_surface`：写（`mark_surface`）、读（`_surfaced_ids` / `_last_surface_epoch` / `recent` 的 `surfaced` 判定）。
- `memories` 视图：`SELECT id, content, first_seen FROM memories WHERE kind = 'thought' AND (user_id = ? OR user_id = 0) ORDER BY id DESC LIMIT 20`（`_surface_candidates`）；写入永远走 `h2.add`。
- kv：`idle_thought_last`、`idle_thought_enabled`、`idle_fragment_last`、`idle_fragment_enabled`、`idle_fragment_interval_hours`、`idle_fragment_daily_max`、`idle_fragment_count:<uid>:<date>`、`companion:ledger`（台账持久化，由 `companion.save_ledgers()` 写）。
- 环境变量：`AI_LIFE_IDLE`（`"0"` 全关）。

**坑**：

1. `_surface_candidates` 只在最后 20 条 `kind='thought'` 里找候选，加上 36 小时年龄窗，念头攒多了以后老念头永远轮不到（等于自然淘汰）。
2. `_last_surface_epoch` 把 `user_id = 0` 也算进同一人的冷却，多人共用一个 uid 时表现正常，但面板与 QQ 会互相影响 6 小时。
3. `mark_surface` 永远返回 `True`（除非抛异常），`INSERT OR IGNORE` 撞唯一索引也算成功——调用方无法区分"第一次提"和"重复提"。
4. `_epoch` 对没有时区的字符串调用 `dt.astimezone()`，按系统本地时区补齐，所以跨时区部署时 `thought_surface.ts` 的解释会变。
5. `_user_is_struggling` 的两层判定：先查 `_DISTRESS_WORDS`（24 个词，含"好累/太累/心累"这类引擎词表不认的口语），再调 `detect_emotion()` 要求 `negative` 且 `intensity >= 0.35`。
6. 白天窗口是 `range(8, 22)`，22 点整就不想了；夜里是 23 点起，所以 **22:00–22:59 是一个两套额度都不管的空档**。
7. `maybe_ruminate(force=True)` 只跳过窗口，间隔与额度照样守；同一天连着两次还是会被拦。
8. `_generate` 的 `models.pick("summary")` 和 `diary` 的 `models.pick("reflect")` 是两个不同的模型档位，改模型配置时两边会分开变。

## 4. 数据流

```text
他一句话进来（adapters/intake → mind/loop.chat_turn）
  ├─ 0.3 proactive.detect_dnd(text) → apply_dnd(uid)          [kv proactive_dnd_until/reason:<uid>]
  ├─ 1   clock.get_state(persona)
  │        ├─ persona.activity_at(h, m)                        [作息表槽位]
  │        ├─ kv dev_awake
  │        ├─ human_life.enabled()/resolve(dt)                  [只读语料接管]
  │        └─ UPDATE life_state（每次请求都写）
  ├─ 1.05 human_life.today_line() → 【你今天】                 [prompt.py:369]
  ├─ 1.15 thoughts.should_try() → 后台 maybe_ruminate()
  │        └─ ThoughtSeed → _evidence → _generate → grounded → h2.add(kind=thought)
  │           → 台账(companion:ledger) + kv idle_thought_last / idle_fragment_last
  ├─ 1.2  diary.ensure() → 昨天有聊天才写
  │        └─ kv diary:<yesterday> + h2.add(kind=diary, importance=0.8)
  ├─ 1.5  behavior.decide(life, recent_user, text=text)
  │        ├─ awake_level/awake_ts 读衰减、bump_awake 写回       [只在 sleepish 时判档]
  │        └─ Reaction{mode, will_reply, delay_ms, hint, prompt_hint}
  ├─ 2.2  thoughts.pick_surface(uid, text) → surface_hint → 【夜里想的】
  ├─ 4    diary.prompt_line() → 【你昨天的日记】
  ├─ 5    模型回复
  ├─ 6.x  surface_item 非空 → thoughts.mark_surface()           [thought_surface 只增]
  ├─ 7    segments + behavior.apply_delay(segments, reaction) → delays_ms
  └─ 7.x  diary_hint 非空 → diary.mark_referenced()             [kv diary_ref_date]

独立线（QQ 私聊主动外发，main.py startup worker，默认关）
  proactive.prepare_tick(rt, persona, uid, "private")
    ├─ gate()：私聊 → 拉黑 → 开关 → 夜间 → DND → 睡着 → 聊过 → 阶段 → 闲置 → 冷却 → 窗口 → 配额 → 连发
    ├─ decide()：thought > day > intimate > anchor
    ├─ 生成文案（模型 → 模板兜底）→ _clean_speak
    └─ 生成后再校验 last_id（他刚说话就作废）
  → OneBot send_private_msg
    ├─ 成功 → commit_tick()：_commit_thought + _mark_window + _append_for_user
    └─ 失败 → data/onebot_outbox.jsonl，不记账
```

## 5. 改这一层要注意

1. **加一个 kv 开关要同步四处**：读取处的默认值、`proactive.settings()` / `settings_for()`（面板要看得见）、`app/api/routes.py` 的 `ScheduleProactiveIn` 保存分支（要能改）、`docs/18-config.md` 的键表。
2. **改 `AWAKE_KEYS` 会同时改两个语义**：四档分界，以及 `decide()` 里"已经聊醒就不再塞提示"的出口（`prev_level >= AWAKE_KEYS[2]`）。至少要保住 0.25 < 0.55 < 0.80 的单调性。
3. **加 `day_type` 要改三处**：`human_life.DAY_TYPES`、`_DAY_TYPE_WORDS`（`today_line` 的措辞）、可选的 `PREFERRED_IDS`；`app/api/routes.py:841` 的日型校验直接读 `DAY_TYPES`，漏改会 400。
4. **加主动消息来源必须进 `PROACTIVE_SOURCES`**（`("proactive", "proactive_thought")`），否则 `_unanswered_proactive()` 连发计数和 `_user_today_count()` 配额都会漏算。
5. **加念头来源只能二选一**：夜间窗加进 `_NIGHT_HOURS`、白天窗加进 `_DAY_HOURS`，额度键不能复用（`_LAST_KEY` 与 `_FRAG_LAST_KEY` 是两套）。
6. **凡是要"真实外发"的新增路径，必须走 prepare → 发 → commit**：`tick_user` / `tick_user_async` 是给面板与测试的一步到位版，用它们做真机外发会在发送失败时白扣配额。
7. **`life_state` 只有 `id = 1` 一行**（`CHECK (id = 1)`），加字段要改 `app/core/db.py` 的建表语句与 `_migrate()` 补列逻辑，`clock.get_state` 的 `UPDATE` 字段清单也要同步。
8. **`app/life` 内部不调 `continuity.record()`**：改她状态的动作留痕在调用侧（`app/api/routes.py:428` 清记忆时才记 `memory_forgotten`；`/v1/dev/awake` 目前不留痕）。新增会改变她状态的能力时，按项目约定补到调用侧。
9. **`human_life` 的开关默认是关的**：任何依赖"她今天的日常"的功能（`proactive._day_state`、`today_line`）都要能在 `enabled()` 为假时正常降级，不要假设语料可用。

## 6. 交叉引用

- 记忆写入唯一入口 `h2.add()`、`memories` 只读视图：docs/06。
- `loop.chat_turn` 的完整流水线与 prompt 各段（【此刻的状态】【你今天】【你昨天的日记】【夜里想的】）：docs/05。
- `persona.schedule()` / `stage_index()` / `activity_at()` 的人格包结构：docs/19。
- 群昵称变更写日记（`diary.note_event`）与群侧上下文：docs/11。
- `data/human_life/_derived` 语料是怎么从公报/自述推出来的：docs/15。
- 主动外发的通道侧（`send_private_msg` / `onebot_outbox.jsonl` / `proactive_push` 开关）：docs/12、docs/13、docs/18。
- 陪伴层台账 `companion:ledger` 与情绪触发（`app/memory/companion.py` + vendor 的 `CompanionMemory`）：docs/06、docs/07。
- `thought_surface` / `life_state` / `kv` / `events` 表结构：docs/17。

## 变更记录（2026-11-01）

- **日记不吃群聊**：`diary._week_events()` 加了 `chat_type != 'group'` 过滤——群里的场面不是她的生活，不进她的日记。
- **念头浮现只算一次额度**：`thoughts.mark_surface()` 先查再插，撞 `thought_surface` 的唯一索引就返回 `False`（以前 `INSERT OR IGNORE` 之后无条件 `True`，额度白扣）。
