# 11 · 群聊增强（app/group）

> 覆盖：`app/group/*.py`（5 个文件 / 709 行）｜基于 2026-11-01 的代码状态｜上游：docs/12（`app/api/onebot.py`）、docs/13（`app/adapters/intake.py`）、docs/10（生活与主动）｜下游：docs/06（观察与进化）、docs/17（数据模型）

## 1. 这一层干什么

`app/group` 只服务 QQ 群这一个场景，管四件事：这句子弹到底是不是在跟她说话（`context.py`）、什么时候该闭嘴什么时候能主动开口（`proactive.py`）、进群怎么做自我介绍（`intro.py`）、定期调度要不要自己找话说（`active.py`）。

它不做的事：不管私聊（私聊主动在 `app/life/proactive.py`）、不做记忆写入（写记忆走 `h2.add` / `store.add_event`）、不解析 OneBot 事件（那在 `app/api/onebot.py`）、不负责群成员印象的生成（那是 `app/memory/evolve.py` 的日更任务）。

调用它的地方有三处：`app/api/onebot.py:254-308`（群消息实时判定，主路径）、`app/adapters/intake.py:68-83`（两个通道共用的前置层）、`app/api/group.py`（10 个控制接口）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/group/__init__.py` | 5 | 纯 docstring，说明模块边界，无符号 |
| `app/group/context.py` | 181 | 语境判断（在跟谁说话/焦点是否转移）+ 昵称管理 + 待机状态 |
| `app/group/proactive.py` | 269 | 静默指令、连发待机、主动配额、话题素材 |
| `app/group/intro.py` | 105 | 群自我介绍生成与发送（可 @所有人） |
| `app/group/active.py` | 149 | 后台定期调度：条件链 + OneBot 群发 |

## 3. 模块逐一详解

### 3.1 `app/group/__init__.py`（5 行）

**职责**：只有模块 docstring 两行说明（`proactive` 管主动/静默/配额，`context` 管语境/昵称/待机），没有 `import`、没有符号、没有副作用。所有使用方写 `from app.group import context as gctx` / `from app.group import proactive as gpro`，并且**全都在函数体内局部导入**（`app/api/onebot.py:255`、`app/adapters/intake.py:70`、`app/api/group.py:11-14` 是例外，在模块顶部导入）。

### 3.2 `app/group/context.py`（181 行）

**职责**：回答三个问题——没 @ 她时这句话算不算跟她说话、她被 @ 之后焦点是不是丢了、她怎么称呼每个人。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `now` | `now() -> datetime` | 本地时区当前时间（带 tzinfo） |
| `nick_of` | `nick_of(user_id: int) -> str` | 读 kv `nick:<uid>`，没有返回 `""` |
| `set_nick` | `set_nick(user_id: int, nick: str, *, by: str = "her") -> str` | 写昵称 + 写日记，返回最终昵称 |
| `reset_nick` | `reset_nick(user_id: int) -> None` | 删 `nick:<uid>` 与 `nick_by:<uid>` 两行 |
| `suggest_nick` | `suggest_nick(user_id: int, user_name: str = "") -> str` | 她起一个昵称（已有就直接返回） |
| `handle_nick_change` | `handle_nick_change(text: str, user_id: int) -> dict[str, Any] \| None` | 解析"别叫我 X / 以后叫我 X" |
| `_recent` | `_recent(limit: int = 8, minutes: int = 10) -> list[dict[str, Any]]` | 取最近若干条 user/her 事件（正序） |
| `who_is_talking_to_her` | `who_is_talking_to_her(text: str, sender_id: int, mentioned: bool) -> tuple[bool, str]` | 这句话是不是在跟她说话 |
| `is_focus_lost` | `is_focus_lost(user_id: int, text: str) -> bool` | 焦点是否已经转给别人 |
| `standby_state` | `standby_state(gid: Any = "") -> dict[str, Any]` | 待机快照（4 键），按群 |

**问题一：没 @ 她时算不算跟她说话**（`who_is_talking_to_her(text, sender_id, mentioned, gid="")`）。`mentioned=True` 直接判真（理由"被 @ 了"）。否则取最近 `_recent(10, 15, gid)`——最近 15 分钟、最多 10 条 user/her 事件（**只在这个群内**），按 id 正序——然后按顺序判四条：

1. 最后一条是她说的，且发言 `user_id` 等于当前 sender → 真（"接着她的话说"）。
2. kv `group_focus_user:<gid>` 等于当前 sender，且最后一条是她说的 → 真（"刚才 @ 过她，接着聊"）。没传 `gid` 时用全局键 `group_focus_user`（单群/旧数据兼容）。
3. 最近一次她发言之后，说话的人集合恰好等于 `{sender_id}` → 真（"同一人连续对话"）。
4. 其余 → 假（"话题已转移"）；一条上下文都没有 → 假（"没有上下文"）。

`group_focus_user:<gid>` 由 `app/api/onebot.py` 在她被 @ / 被叫名字时写入（同处还写 `group_focus_at:<gid>`），**按群存**——A 群刚 @ 过她，不会让 B 群的陌生人被当成"接着聊"。

**问题二：她是不是已经不再是焦点**（`is_focus_lost(user_id, text, gid="")`）。取 `_recent(6, 15, gid)`：最后一条是别人说的（`role='user'` 且 uid ≠ 传入的 `user_id`），且最近 3 条里没有这个人与她的往来 → 返回 `True`。

**问题三：昵称**。三条正则/表定义在模块顶部：`NICK_ASK`（别叫我/不要叫/换个叫法/你叫我什么/怎么称呼/你想怎么叫我）、`NICK_GIVE`（`(?:^|[，。；\s])(?:以后叫我|就叫我|可以叫我|叫我)\s*["'「“]?([^\s"'」”，。]{1,8})`）、`NICK_POOL` 10 个备选（话痨、夜猫子、早起困难户、白饭续命人、深夜党、火锅常客、念念碎、小情绪、冷面热心、话少星人）。

`handle_nick_change` 的判定顺序是硬约束（源码注释写明"顺序很重要"）：先 `NICK_ASK` → `reset_nick` 并返回 `{"ok": True, "nick": "", "by": "ask", "need_name": True}`；再 `NICK_GIVE` → 去掉尾部语气词（`[吧啊呀哦呢嘛哈的了]+$`）后 `set_nick(..., by="user")`。顺序反了的话"别叫我话痨"会被当成设昵称。

`suggest_nick` 先看已有昵称；没有就从最近 8 条他的话里找特征词，命中表是 8 条：累→夜猫子、加班→加班人、困→起床困难户、饭→干饭人、吃→干饭人、睡→深夜党、哈哈→话痨、？→十万个为什么；都不命中就 `random.choice(NICK_POOL)`。

`set_nick` 除了写 kv，还会调 `app.life.diary.note_event(f"我开始叫他「{nick}」（{who}），以后别叫错了。", topic="相处", user_id=…)`——这就是"改名进日记"的落点，`note_event` 同时写 `human_notes` 与 `memories(kind='diary')`。

**`member_impression` 表**：这张表（`user_id` 主键、`summary`、`traits_seen` JSON、`habits` JSON、`version`、`updated_at`）**不由 `app/group` 写入**。它的唯一写入口是 `app/memory/observe.py:108-121` 的 `upsert_impression`，被 `app/memory/evolve.py:199` 的日更"思考"阶段调用；`app/api/console.py:635` 的 `/mind` 面板只读它。`context.py` 对它零引用——"她对每个群成员的看法"与"她怎么称呼这个人"是两套独立数据（表 vs kv）。

**存储**：kv `nick:<uid>`、`nick_by:<uid>`（值 `"her"` 或 `"user"`）、`group_focus_user` / `group_focus_at`（按群时带 `:<gid>` 后缀，`db.kv_key()` 生成）；读表 `events`（`role`, `text`, `user_id`, `ts`，按群时带 `group_id` 过滤）。

**坑**：

1. `is_focus_lost` 在全仓**没有任何调用点**（只有定义与 `app/qa/scenarios.py` 的注释提到"线上由 is_focus_lost 判"）。线上实际的焦点转移靠 `who_is_talking_to_her` 返回假 + `should_be_quiet` 待机来实现。
2. `who_is_talking_to_her` 的 `text` 形参**完全没用**，判定只靠最近事件。
3. `suggest_nick` 的 `user_name` 形参同样未使用，昵称只从对话内容推。
4. `_recent` 的时间窗是**全局事件**（不按 `chat_type`/`group_id` 过滤），同时开着多个群时，A 群的上下文会参与 B 群的判定。
5. `set_nick` 把昵称截到 8 个字符（`nick.strip()[:8]`），中文按字符算。
6. `standby_state()` 在函数内 `from app.group import proactive`，反向依赖：`context` 依赖 `proactive`，而 `proactive` 不依赖 `context`，没有环。

### 3.3 `app/group/proactive.py`（269 行 / 20 个函数）

**职责**：五条设计原则的落地——用户话语绝对优先（一句"别发了"立刻静默）、不粘人（冷却与频次上限）、只在有语境时开口、被 @ 之后会接着聊、昵称先用她自己的。

**常量真值**：

| 常量 | 值 | 说明 |
|---|---|---|
| `STOP_PATTERNS` | 正则，含 别发了/别说了/闭嘴/消停/安静/别吵/别刷/不要发/停止/停一下/先停/别找话题/别主动/别打扰/不想聊/别烦我/别烦了/别再@/别艾特/别回/别说话/禁言/安静点 | 命中即静默 |
| `STOP_MINUTES` | `30` | 静默时长（分钟） |
| `BREAK_ON_MENTION` | `True` | 声明"被 @ 可打破"，源码里没有读它的地方 |
| `NO_REPLY_LIMIT` | `3` | 声明"连发几条没人接就闭嘴"，实际用硬编码 2 |

**对外符号（20 个全表）**：

| 符号 | 签名 | 一句话 |
|---|---|---|
| `now` | `now() -> datetime` | 本地时区当前时间 |
| `_kv` | `_kv(key: str, default: str = "") -> str` | 读 kv，空值退默认 |
| `is_silenced` | `is_silenced(gid: Any = "") -> tuple[bool, str]` | 当前是否静默 + 剩余分钟说明（按群） |
| `silence` | `silence(minutes: int = STOP_MINUTES, reason: str = "用户要求安静") -> str` | 进静默，返回到期 ISO |
| `break_silence` | `break_silence(by: str = "@") -> bool` | 清静默，返回"原来是否在静默" |
| `unsilence` | `unsilence() -> None` | 无条件清静默 |
| `detect_stop` | `detect_stop(text: str) -> bool` | 这句话是不是在叫她闭嘴 |
| `unanswered_streak` | `unanswered_streak() -> int` | 她连着几条没人接 |
| `should_be_quiet` | `should_be_quiet(gid: Any = "") -> tuple[bool, str]` | 是否该待机（连发 ≥ 2，按群计数） |
| `focus_user_silent` | `focus_user_silent(minutes: int = 20) -> bool` | 跟她聊的人是否已晾她 ≥ 20 分钟 |
| `_today_key` | `_today_key(name: str) -> str` | 拼 `group_<name>:<date>` |
| `_bump` | `_bump(name: str) -> int` | 当天计数 +1 并返回 |
| `_count` | `_count(name: str) -> int` | 读当天计数 |
| `quota_ok` | `quota_ok() -> tuple[bool, str]` | 主动配额（时段/冷却/日上限） |
| `mark_proactive` | `mark_proactive() -> None` | 记上次主动时间 + 当天计数 +1 |
| `_recent_group_lines` | `_recent_group_lines(limit: int = 40, hours: int = 48, gid: Any = "") -> list[dict[str, Any]]` | 取最近群消息（正序，按群） |
| `_unfinished_topics` | `_unfinished_topics(limit: int = 8, gid: Any = "") -> list[str]` | 找"没聊完"的用户原话（按群） |
| `_hot_gag` | `_hot_gag() -> str \| None` | 从热梗库挑一句 |
| `_keyword_video` | `_keyword_video() -> dict[str, Any] \| None` | 拿见过的视频标题搜 B 站 |
| `build_topic` | `build_topic(gid: Any = "") -> dict[str, Any] \| None` | 准备主动话题 `{kind, text}`（按群取材） |

**静默（最高优先级）**：`detect_stop()` 命中 → `silence(30, gid=…)` 写 `group_silent_until[:gid]`（到期 ISO）与 `group_silent_reason[:gid]`。`is_silenced(gid)` 读到过期就**顺手清空**这两个键再返回 `False`。静默期内唯一出口是被 @ 或叫她的名字（`app/api/onebot.py` 与 `app/adapters/intake.py` 各实现一遍）：被 @ 就 `break_silence(by="@", gid=…)`，否则一句都不回。**静默按群生效**——让 A 群安静，B 群照常。

**主动开口的前置条件链**（`quota_ok(gid)` + 调用方）：

| 顺序 | 条件 | 拒绝文案 |
|---|---|---|
| 1 | `22 <= hour or hour < 8` | 夜间不主动 |
| 2 | 距 `group_last_proactive[:gid]` < 25 分钟 | 冷却中（N 分钟前刚发过） |
| 3 | 当天 `group_proactive[:gid]:<date>` ≥ 8 | 今天主动次数已满 |
| — | 通过 | `""` |

另外两道在调用侧：`is_silenced(gid)` 为真不谈；`should_be_quiet(gid)` 为真不谈（"已经连发 N 条没人接"，只数这个群的事件）。

**话题素材**（`build_topic`）：先取最近 3 天 `source='group_proactive'` 的原文，用"核心片段"（前 24 字）做去重。然后按 `random.random()` 落三段：

1. `roll < 0.45`：从 `_unfinished_topics()` 里挑一条没说过的新鲜句，输出 `对了，刚想起你之前说的「<原话>」，后来怎么样了？`。
2. `roll < 0.75`：`_hot_gag()` 拿热梗，输出 `刷到个说法：<梗>。是不是挺像我们群的。`。
3. 兜底：`_keyword_video()` 从 `web_videos` 随机取标题前 12 字去 `bili.search_videos(kw, 2)`，输出 `翻到个视频，<标题前 30 字>\n<链接>`。

一条都拿不到就返回 `None`——**没抓手就闭嘴**，不硬找话题。`build_topic` 内部还有一个嵌套函数 `_fresh(candidate: str) -> bool`（判断候选的前 24 字是否与最近说过的重复），所以这个模块的 `def` 计数是 20 个模块级 + 1 个嵌套。`_unfinished_topics` 的取材规则：72 小时、最多 80 条、只看 `role='user'`、长度 6–40 字、原文去重；命中条件是含 `？`/`?`，或含 明天/改天/下次/等我/回头/再说/打算/准备 之一。

**存储**：kv `group_silent_until`、`group_silent_reason`、`group_last_proactive`、`group_proactive:<date>`（以上四个按群时带 `:<gid>` 后缀，`db.kv_key()` 生成；不传 gid 用全局键）；读表 `events`（`role`/`text`/`user_id`/`ts`/`source`/`group_id`）、`web_videos`。

**坑**：

1. `quota_ok` 的 docstring 写"白天每小时最多 2 次"，代码里**没有按小时限流**，只有"25 分钟间隔 + 每天 8 条"。
2. ~~`unanswered_streak()` 的 SQL 不带 chat_type/group_id 过滤~~ **已修（2026-11-01）**：现在 `unanswered_streak(gid)` 在有 `gid` 时加 `group_id = ?`，私聊里连发不会把群里的主动待机掉（回归 D14 钉住了）。
3. `BREAK_ON_MENTION` 与 `NO_REPLY_LIMIT` 都是死常量；`should_be_quiet` 里写的是 `streak >= 2`。
4. `focus_user_silent()` 零调用点。
5. `detect_stop` 的 `STOP_PATTERNS` 含"安静"与"停止"，正常聊天里出现这两个词（"这歌听着挺安静的"）也会触发 30 分钟静默。
6. `break_silence` 的 `by` 形参没被使用（只用于签名语义）。
7. `is_silenced()` 里 `until` 解析失败直接当"没静默"返回，且不清键——坏值会一直留着但不再生效。

### 3.4 `app/group/intro.py`（105 行）

**职责**：让她在新群/重新开始时介绍自己，可选 @所有人。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `INTRO_SYSTEM` | 字符串 | 自我介绍 system 提示（6 条要求） |
| `INTRO_HINTS` | 字符串 | 群里谁是谁 + 她会什么 |
| `compose_intro` | `async compose_intro(rt, persona) -> str` | 让模型写一段（失败用兜底文案） |
| `send_intro` | `async send_intro(rt, persona, group_id: Any = None, mention_all: bool = True) -> dict[str, Any]` | 发到群里并落事件 |

**流程**（`send_intro`）：

1. `group_id` 为空 → 读 kv `onebot_groups`（逗号分隔），取第一个；一个都没有就返回 `{"ok": False, "reason": "还不知道群号，先在群里 @ 她一次"}`。
2. `compose_intro`：`models.pick("reflect")`，`max_tokens=400`、`temperature=0.9`，输出经 `appraise.strip_actions` 清动作旁白；抛异常或空串就返回 5 句兜底文案（"大家好，我是小鲸。…"）。
3. 组装 message 段：`mention_all=True` 时先 `{"type": "at", "data": {"qq": "all"}}` 再一个换行 text 段，最后一条 text 段是正文。
4. `POST {onebot.api_base}/send_group_msg`，`Authorization: Bearer <access_token>`（token 为空则不带），超时 30 秒；成功判定是 HTTP 成功**且** `json.status == "ok"`。
5. 成功才 `store.add_event("her", text, source="group_intro", chat_type="group", group_id=…)`——她说过的话必须进事件表，否则下一轮她自己不知道说过。
6. 返回 `{"ok", "group_id", "mention_all", "text"}`（失败时多一个 `reason`）。

**坑**：

1. 直接打 `rt.cfg.onebot` 的 HTTP API，**不经过 `app/core/channel.py` 的能力位**，所以这个功能在官方 bot 通道上不可用（官方适配器没有 OneBot 的 `api_base`）。
2. `from app.core.paths import DATA_ROOT  # noqa: F401` 是死导入。
3. `mention_all` 默认 `True`，`POST /v1/group/introduce` 的 `IntroIn.mention_all` 也是 `True`——不显式关掉就会 @所有人。
4. 发送失败时返回的 dict 也带 `text`（正文已生成但没发出去，也没落事件表）。

### 3.5 `app/group/active.py`（149 行）

**职责**：后台定期检查，满足全部条件就自己说一句。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `_send_group` | `async _send_group(group_id: Any, text: str) -> bool` | OneBot 发群消息（纯文本段），20 秒超时 |
| `_groups_seen` | `_groups_seen() -> list[Any]` | 读 kv `onebot_groups` |
| `remember_group` | `remember_group(group_id: Any) -> None` | 记最近互动群（插到最前，只留 5 个） |
| `_should_speak` | `_should_speak() -> tuple[bool, str]` | 条件链，返回 (能不能, 原因) |
| `tick_once` | `async tick_once() -> dict[str, Any]` | 检查一次，满足就说一句 |
| `loop` | `async loop(interval_seconds: int = 600) -> None` | 后台循环，最小 120 秒 |
| `start` | `start(interval_seconds: int = 600) -> bool` | 启动（重复调用返回 False） |
| `stop` | `stop() -> None` | 停（置 `_stop` 并 cancel task） |

**条件链**（`_should_speak`，顺序即优先级）：

| # | 条件 | 返回原因 |
|---|---|---|
| 1 | `is_silenced(gid)` 为真 | 静默中，还剩 N 分钟 |
| 2 | `should_be_quiet(gid)` 为真 | 没人接话（…），进入待机 |
| 3 | `quota_ok(gid)` 为假 | 夜间不主动 / 冷却中 / 今天次数已满 |
| 4 | `_groups_seen()` 为空 | 还没有群互动记录 |
| 5 | 这个群最近一条 `source='group_proactive'` < 30 分钟 | 刚主动过 |
| — | 通过 | `""`，`tick_once` 再去 `build_topic()`，拿不到就返回"没有合适话题" |

`tick_once` 成功后：`_send_group(第一个群, 文本)` → `mark_proactive(gid)`（写 `group_last_proactive:<gid>` + 这个群当天计数 +1）→ `store.add_event("her", text, source="group_proactive", chat_type="group", group_id=…)` → 返回 `{"spoke": True, "group_id", "kind", "text"}`。

**为什么默认不启动**：`app/main.py` 的 startup 只挂了"按用户主动外发"那一个 worker（且要求 `onebot.enabled` 与 `onebot.proactive_push` 同时为真），**没有任何地方自动调 `gactive.start()`**。全仓唯一的启动入口是 `POST /v1/group/start`（`app/api/group.py:67-71`），并且这个端点是 `async` 的：同步端点跑在线程池里没有运行中的事件循环，`asyncio.create_task` 会直接抛 `RuntimeError`。`start()` 里对这个 `RuntimeError` 做了退化处理——没有事件循环就起一个 `daemon` 线程自己 `asyncio.run(loop(...))`，线程名 `group-active`。也就是说：**群主动是手动开启的实验性调度，不接在服务启动路径上**。

另外三个入口：`POST /v1/group/speak` 直接 `await tick_once()`（忽略部分配额，但仍先查静默）、`POST /v1/group/stop` 停、`GET /v1/group/status` 看 `context.standby_state()`。

**坑**：

1. `stop()` 只 cancel `_task`，**不 join 线程**；如果当初走的是 daemon 线程分支（`_thread`），`stop()` 之后线程仍在，只能靠 `_stop` 事件让循环自己退出。
2. `loop()` 的 `await asyncio.sleep(max(120, interval_seconds))`：传 600 就 10 分钟一次，传小于 120 也被抬到 120 秒。
3. `tick_once` 永远发给 `_groups_seen()[0]`（最近互动的那个群），多群场景下没有"均衡"或"排除某个群"的能力。
4. `_should_speak` 里 `focus_user_silent` 没被用到，判断"没人理她"只靠 `should_be_quiet`（全局事件连续计数）。
5. `_send_group` 只发纯文本段，不带 @、不带表情包。
6. `remember_group` 每次把群插到队首并截前 5 个，所以第 6 个群永远挤掉最老的那个，且顺序会抖动。

### 3.6 能力边界：官方 bot 通道没有群主动

这是**设计边界，不是 bug**。

`app/core/channel.py` 定义了同一个内核在三类通道上的能力位（`@dataclass(frozen=True) class Caps`，字段 `name` / `stickers` / `voice` / `images` / `group_proactive` / `private_proactive` / `max_reply_chars` / `can_split`）：

| 预设 | `group_proactive` | `private_proactive` | `max_reply_chars` | `can_split` |
|---|---|---|---|---|
| `PANEL`（面板） | `True` | `True` | 4000 | `True` |
| `ONEBOT`（NapCat/LLOneBot） | `True` | `True` | 1500 | `True` |
| `QQ_OFFICIAL`（官方开放平台） | **`False`** | `True` | 1500 | `False` |

通道由适配器声明：`app/api/onebot.py:207` 调 `channel.set_channel("onebot")`、`app/adapters/qq.py:250` 调 `channel.set_channel("qq_official")`、`app/api/routes.py:152` 调 `channel.set_channel("panel")`。

三条事实把这个边界钉死：

1. 官方开放平台的 bot 只能被动回消息，没有"主动发到群"的接口；`QQ_OFFICIAL` 的 `group_proactive=False` 是对这个现实的声明。
2. `app/group/active.py` 与 `app/group/intro.py` 都是直接对 `rt.cfg.onebot.api_base` 发 `send_group_msg`，**官方通道根本没有这条路径**——所以群主动（定期调度 + 自我介绍）只在 OneBot 可用。
3. `caps().group_proactive` 目前在运行时**没有任何读取点**（全仓只有 `channel.py` 里的定义）。能力位是声明式的，边界靠代码结构保证，而不是靠运行时 `if caps().group_proactive` 拦。

同一份能力位在回复路径上确实被用了：`app/api/onebot.py:396-398` 读 `caps.can_split` 决定要不要拆多气泡，`caps.stickers` 决定要不要发表情包——官方通道 `can_split=False`，一次只能回一条。

## 4. 数据流

```text
OneBot 上报一条群消息（app/api/onebot.py:206 onebot_event）
  └─ message_type == "group"
       ├─ active.remember_group(group_id)                     [kv onebot_groups，留 5 个]
       ├─ proactive.detect_stop(text) → silence(30, gid)      [kv group_silent_until:<gid>]
       ├─ proactive.is_silenced(gid)
       │    ├─ 静默中且没 @ 也没叫名字 → 直接返回，一句不回
       │    └─ 被 @ → break_silence()
       ├─ proactive.should_be_quiet(gid) → 待机返回（只数这个群）
       ├─ context.handle_nick_change(text, uid)
       │    ├─ "别叫我X" → reset_nick + need_name
       │    └─ "以后叫我X" → set_nick(by="user") + diary.note_event
       ├─ 未 @：context.who_is_talking_to_her(text, uid, False, gid)
       │    └─ False → 返回 not talking to her
       ├─ 被 @：写 kv group_focus_user:<gid> / group_focus_at:<gid>
       └─ 进内核 mind.loop.chat_turn（chat_type="group"）

另一条前置层（官方通道/其它通道共用，app/adapters/intake.py:24 precheck）
  昵称 → 搜视频 → 群机制（remember_group → detect_stop → is_silenced/break_silence）

主动线（手动开启后，app/group/active.py:114 loop，默认 600 秒）
  tick_once
    ├─ _should_speak：静默 → 连发待机 → quota_ok(22-8 / 25min / 8条) → 群记录 → 30min
    ├─ build_topic：未聊完(45%) → 热梗(30%) → 关键词视频 → None 就闭嘴
    ├─ _send_group(send_group_msg)
    └─ 成功：mark_proactive + add_event(source="group_proactive")

自我介绍线（POST /v1/group/introduce）
  compose_intro(models.pick("reflect")) → at all + text → send_group_msg
    └─ 成功：add_event(source="group_intro", chat_type="group")
```

## 5. 改这一层要注意

1. **改静默文案/时长只改一处**：`STOP_PATTERNS` 与 `STOP_MINUTES` 都在 `group/proactive.py` 顶部；两个调用方（`api/onebot.py`、`adapters/intake.py`）都读这两个常量，别在调用侧硬编码分钟数。
2. **加一条"她该闭嘴"的判定，必须同时改调用侧**：`should_be_quiet(gid)` 的返回值被 `api/onebot.py`、`group/active.py:_should_speak` 各读一次，新增判定要确认两处都生效。
3. **改昵称解析先改顺序**：`handle_nick_change` 必须"先否定/询问、后给昵称"，`NICK_GIVE` 的捕获组是 1–8 个非空白非标点字符，改正则要同时验证"别叫我话痨"不被吞成设昵称。
4. **群维度状态一律走 `db.kv_key(key, gid)`**（2026-11-01 起）：`unanswered_streak`、`_recent`、静默/焦点/配额都已加 `group_id` 过滤；新增按群的状态**不要**再写单个全局 kv 键，否则多群互相污染（回归 D14 会红）。
5. **加群主动的发送路径必须走"发成功再记账"**：`tick_once` 的模式是 `_send_group` 为真才 `mark_proactive(gid)` + `add_event`；照这个顺序写，别先记后发。
6. **任何群主动能力都要先想清楚通道**：`app/group/active.py` / `intro.py` 直接依赖 `cfg.onebot.api_base`，新增的群外发要么复用 `_send_group`，要么显式在 `channel.Caps` 里加能力位**并且真的去读它**（目前 `group_proactive` 没人读）。
7. **`remember_group` 的上限是 5**：`onebot_groups` 是逗号分隔字符串，`active.tick_once` 只发第一个，扩到多群要么改这个上限，要么改选群策略。
8. **控制接口在 `app/api/group.py`**（前缀 `/v1/group`）：`introduce`、`status`、`silence`、`unsilence`、`speak`、`start`、`stop`、`nick/{user_id}`（GET）、`nick`（POST）、`nick/{user_id}/suggest`。新增能力按这个前缀加，`start`/`speak` 必须是 `async def`。

## 6. 交叉引用

- 群事件上报与回复组装（`onebot_event`、多气泡切分、outbox）：docs/12。
- 两个通道共用的前置层（静默/昵称/搜视频）：docs/13。
- 通道能力位 `app/core/channel.py: Caps`：docs/04。
- 私聊主动外发与免打扰（与群静默是两套 kv）：docs/10。
- 群成员印象 `member_impression` 与每日观察/进化（`app/memory/observe.py`、`evolve.py`）：docs/06。
- 热梗库与 B 站搜索（`build_topic` 的后两个分支）：docs/14。
- `events`、`web_videos`、`member_impression`、`human_notes` 表结构：docs/17。
- `onebot.proactive_push`、`onebot.group_at_only`、`mention_keywords` 等配置项：docs/18。
