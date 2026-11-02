# 09 · 亲密模式（app/intimacy）

> 覆盖：`app/intimacy/*.py`（8 个文件 / 977 行）｜基于 2026-11-01 的代码状态｜上游：docs/08（恋人判定 `users.lover_of()`）、docs/17（`intimate_context` / `intimate_profile` 表）｜下游：docs/05（对话主循环的四处置入点）

## 1. 这一层干什么

这一层管一件事：**她这一轮该用哪种尺度的说话方式，以及这些话说出去之后记在哪**。三级状态 OFF / SUGGESTIVE / EXPLICIT 由用户自己说的话驱动（`detect_intent`），由「是不是私聊」「是不是恋人」两个布尔值裁剪，落在 `kv` 的两个键上（模式值与最后触碰时间）。

调用它的是三条聊天入口——OneBot 的 `api/onebot.py`、QQ 官方的 `adapters/qq.py`、控制台的 `api/console.py`（模拟用户发言那一路），它们都走同一个 `gate.prepare()` / `gate.finish()`。它自己调 `app/relation/users.py` 判恋人、调 `app/core/db.py` 读写 `kv` 与两张亲密表。它明确不做的事：不改关系四维（那是 `app/relation/engine.py` 的事，亲密模式聊得再多也不直接加好感）、不生成台词（台词由 `app/mind/prompt.py` 组装的 prompt 交给模型）、不决定她要不要回。

两条贯穿全层的硬约束，写死在代码里：**群聊永远到不了 EXPLICIT**（`transition()` 会把 EXPLICIT 打回 SUGGESTIVE，`sanitize_group_reply()` 再把漏出的直白词换成谐音）；**非恋人永远到不了 EXPLICIT**（只给「调侃拒绝」，模式不动）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/intimacy/__init__.py` | 1 | 只有一行 docstring，无导出 |
| `app/intimacy/mode.py` | 204 | 三级状态机：意图检测、模式转移、TTL 降级、`kv` 读写 |
| `app/intimacy/gate.py` | 63 | 三条入口共用的判定/记录链路：`prepare()` + `finish()` |
| `app/intimacy/prompt.py` | 235 | 按模式注入 system 段、群聊谐音替换、色色句数压制 |
| `app/intimacy/analyze.py` | 160 | 把私密记录折成 `intimate_profile`（likes / dislikes / boundaries / summary） |
| `app/intimacy/exemplars.py` | 85 | 脏话范本库，存 `kv["intimate_exemplars"]` |
| `app/intimacy/guide.py` | 124 | 引导模式：暗示 → 明示 → 露骨 → 收尾，四阶段推进 |
| `app/intimacy/memory.py` | 105 | `intimate_context` 表的读写，与普通记忆物理隔离 |

## 3. 模块逐一详解

`app/intimacy/__init__.py` 只有一行 docstring（`"""亲密模式模块：管理暗示→直白的状态机与私密记忆。"""`），不导出任何符号。以下按「状态机 → 门控 → prompt → 档案 → 范本 → 引导 → 记忆」的顺序讲。

### 3.1 `app/intimacy/mode.py`

**职责**：状态机本体。纯 `kv` 驱动，没有内存态，进程重启后状态还在。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `Mode` | `class Mode(str, Enum)`：`OFF="off"`、`SUGGESTIVE="suggestive"`、`EXPLICIT="explicit"` | 三态枚举，值即落库字符串 |
| `EXPLICIT_TTL_MIN` / `SUGGESTIVE_TTL_MIN` | `45` / `8 * 60` | 直白 45 分钟、暗示 480 分钟 |
| `detect_intent` | `detect_intent(text: str) -> str` | 返回 `explicit` / `suggestive` / `stop` / `none` |
| `get_mode` | `get_mode(user_id: int) -> Mode` | 读 `kv`，非法值退回 OFF |
| `set_mode` | `set_mode(user_id: int, mode: Mode) -> None` | 写 `kv` |
| `touch` | `touch(user_id: int) -> None` | 把 `intimate_mode_ts` 刷成当前时刻 |
| `expire` | `expire(user_id: int, now: datetime \| None = None) -> Mode` | 过期降级，返回降级后的模式 |
| `transition` | `transition(user_id: int, intent: str, *, is_private: bool, is_lover: bool) -> tuple[Mode, str]` | 状态转移，返回 `(新模式, 说明给 prompt)` |
| `is_intimate_context` | `is_intimate_context(user_id: int) -> bool` | 当前是否非 OFF |
| `mark_event` | `mark_event(user_id: int, is_private: bool) -> str` | 返回 `"explicit\|private"` 这类事件标记 |

**`kv` 键（真值）**：

| 键 | 值域 | 写入者 | 清除者 |
|---|---|---|---|
| `intimate_mode:<uid>` | `off` / `suggestive` / `explicit` | `set_mode()` | `users.forget_user()`、`console.delete_friend()`（写空串） |
| `intimate_mode_ts:<uid>` | ISO 时间戳（带时区，秒精度） | `touch()` | `expire()` 降级到 OFF 时写空串；同上两处 |

**TTL 与降级（`expire()` 的执行顺序）**：

1. 读当前模式，已是 OFF 直接返回 OFF。
2. 读 `intimate_mode_ts` 解析为 `datetime`；解析失败或为空 → `touch()` 后**保持原模式**（宁可续期，也不因为时间戳坏了就掉档）。
3. 无时区的时间戳按本地时区补（`ts.astimezone()`）。
4. `age_min = (now - ts).total_seconds() / 60`。
5. 跨天（`ts.date() < now.date()`）**或** `age_min > 480` → 落 OFF 并清空时间戳，返回 OFF。
6. 否则若当前是 EXPLICIT 且 `age_min > 45` → 降为 SUGGESTIVE 并 `touch()`（重新计一轮 480 分钟）。
7. 否则原样返回。

也就是说：**跨天必降**这条会先于 45 分钟判定命中；只要在同一自然日内，EXPLICIT 最多连坐 45 分钟，SUGGESTIVE 最多 8 小时。

**`transition()` 的完整转移表**（先跑一次 `expire()`，`current` 是降级后的结果）：

| intent | 私聊 | 恋人 | current | 结果模式 | 返回值里的说明 |
|---|---|---|---|---|---|
| `stop` | — | — | 任意 | OFF，清时间戳 | `stop_soft` |
| `explicit` | 否 | 是 | 任意 | SUGGESTIVE + `touch` | `group_limit` |
| `explicit` | 否 | 否 | 任意 | OFF（时间戳不清） | `""` |
| `explicit` | 是 | 是 | 任意 | EXPLICIT + `touch` | `""` |
| `explicit` | 是 | 否 | 任意 | 保持 `current` 不变 | `not_lover_deflect` |
| `suggestive` | — | — | OFF | SUGGESTIVE + `touch` | `""` |
| 其它 | 否 | — | EXPLICIT | SUGGESTIVE + `touch` | `group_limit` |
| `none` | — | — | OFF | OFF | `""` |
| 其它 | — | — | 非 OFF | 保持，并 `touch()` | `""` |

注意 `explicit` 四行是**逐条 return** 的：非恋人私聊说直白话时只返回 `not_lover_deflect`，模式一动不动，`touch` 也不执行——所以「调侃拒绝」不会替她把已有模式续期。

**意图检测（`detect_intent` 的判定顺序：先 stop，再 explicit，再 suggestive）**：

| 返回 | 正则 | 词条 |
|---|---|---|
| `stop` | `_STOP_INTIMATE` | `不色了`、`别色了`、`不玩了`、`正常聊`、`别这样`、`算了算了`、`不想了`、`不要了`、`停`、`够了`、`打住` |
| `explicit` | `_EXPLICIT_INTENT` | `我想操`、`想操`、`好想操`、`想肏`、`想日`、`想上`、`想上你`、`想做爱`、`想睡`、`想睡你`、`想插`、`想被插`、`被.*?插`、`欲仙欲死`、`想肏你`、`想干你`、`想日你`、`想让你`、`想.*?舔`、`想.*?吸`、`想摸你`、`想舔你`、`想吸你`、`硬了`、`湿了`、`想要你`、`想宠你`、`做爱`、`爱爱`、`papapa`、`打炮`、`来一炮`、`那个嘛`、`色色`、`骚话`、`荤话`、`露骨`、`dirty`、`大鸡巴`、`大屌`、`肉棒`、`小穴`、`逼`、`乃子`、`奶子` |
| `suggestive` | `_SUGGESTIVE_INTENT` | `亲亲`、`抱抱`、`抱我`、`摸摸`、`摸摸我`、`贴贴`、`蹭蹭`、`想抱`、`想亲`、`想摸`、`想黏`、`黏你`、`喜欢你`、`爱你`、`想你`、`想见你`、`亲爹`、`乖女儿`、`宝贝`、`宝宝`、`陪我睡`、`欧金金`、`棒棒糖`、`福福`、`逼逼`、`查.*?学历`、`查.*?避税`、`想查`、`调查`、`大调查` |

**坑**：`explicit` 表里 `逼` 是裸字，会命中「逼真」「逼近」；`想上` 会命中「我想上厕所」；`stop` 里 `停`、`够了`、`不要了` 是裸字，会命中「停车」「我够了」。这些误判都会真的改模式（一个是拉进 EXPLICIT，一个是打回 OFF），只有 `is_lover` / `is_private` 两道布尔值兜着；面板线和群聊非恋人时，`explicit` 误判的后果被吃掉了，但 `stop` 误判没有。另外，代码里留着一条明确不修的 TODO（`mode.py:183-185`，标注 `docs/29 §十`）：**断恋后 EXPLICIT 残留**——`current == EXPLICIT and not is_lover` 时不会回落 OFF，只有 45 分钟 TTL 会把它降下来。

### 3.2 `app/intimacy/gate.py`

**职责**：把「检测意图 → 切换模式 → 记私密上下文」抽成一个入口，三条聊天通道共用。模块 docstring 写的动机是：以前 `onebot.py` 里手写了一遍这套，控制台模拟对话完全没走这条路，于是「控制台里怎么试都进不了亲密模式」。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `is_private_of` | `is_private_of(chat_type: str) -> bool` | `chat_type.strip().lower() == "private"` |
| `is_lover_of` | `is_lover_of(user_id: int) -> bool` | `users.lover_of(uid) is not None`，异常时返回 `False` |
| `prepare` | `prepare(user_id: int, text: str, chat_type: str) -> tuple[Mode, str, bool, bool]` | 返回 `(模式, 说明, 是否恋人, 是否私聊)` |
| `finish` | `finish(user_id: int, mode: Mode, reply: Any, is_private: bool) -> None` | 把她的回复也记进本次私密会话 |

**`prepare()` 的顺序（照着重写一遍）**：

1. `uid = int(user_id or 0)`；`is_private = is_private_of(chat_type)`；`is_lover = is_lover_of(uid)`（uid=0 时 `lover_of(0)` 查不到行，恒为 `False`）。
2. `uid > 0` 且 `users.is_blocked(uid)` → 直接返回 `(Mode.OFF, "blocked", False, is_private)`：注意这里把`是否恋人`强制成 `False`，后面的 prompt 注入就不会带恋人语义。
3. `exemplar_mod.ensure_seeded()` —— 第一次调用时把 10 条范本写进 `kv["intimate_exemplars"]`。这是运行期自动播种范本的唯一位置（控制台 `GET /v1/console/intimate/exemplars` 也会调它，但那只在有人点开面板时发生）。
4. `intent = mode_mod.detect_intent(text)`。
5. `mode, note = mode_mod.transition(uid, intent, is_private=is_private, is_lover=is_lover)` —— 模式在这里被改写落库。
6. `mode != Mode.OFF` 时 `memory_mod.record(uid, "user", text, mode.value, is_private)`，把她读到的那句话记进 `intimate_context`。
7. 返回四元组，调用方把 `mode.value` 当 `intimate_mode`、`note` 当 `intimate_note`、`is_lover` / `is_private` 继续往 `loop.chat_turn()` 传。

**`finish()`**：`mode == OFF` 直接返回；`reply` 是 list / tuple 时用 `\n` 拼成一段，否则 `str()`；非空才 `memory_mod.record(uid, "her", text, mode.value, is_private)`。

**四个调用点（三条路径）**：

| 入口 | prepare | finish |
|---|---|---|
| OneBot（NapCat 小号） | `api/onebot.py:382` | `api/onebot.py:392` |
| QQ 官方开放平台 | `adapters/qq.py:97` | `adapters/qq.py:108` |
| 控制台「模拟对方发言」 | `api/console.py:478` | `api/console.py:484` |

**面板的 `/v1/chat` 不调 gate**（`api/routes.py:150-162`），它只算一个 `is_lover` 就进 `loop.chat_turn()`。所以面板会话里 `intimate_mode` 一直是空串，`prompt.build_system_prompt()` 根本不会被调用——**面板里永远进不了亲密模式**。要测这条链路只能用控制台「模拟发言」（`POST /v1/console/send`，`as="me"`）或真 QQ。

### 3.3 `app/intimacy/prompt.py`

**职责**：模式 → system 段；群聊 → 谐音替换；句数 → 硬压。注入点在 `app/mind/loop.py`，本模块只产出字符串。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `METAPHOR_MAP` | `dict[str, str]`，15 条 | 直白词 → 谐音 |
| `SUGGESTIVE_TEMPLATES` | `list[str]`，7 条 | 暗示级参考句 |
| `sanitize_group_reply` | `sanitize_group_reply(text: str) -> str` | 群聊兜底：按 16 条表逐词替换 |
| `has_explicit_word` | `has_explicit_word(text: str) -> bool` | 是否含替换表里的词 |
| `limit_intimate_sentences` | `limit_intimate_sentences(text: str, mode_value: str, is_private: bool) -> str` | 色色句数硬上限 |
| `build_system_prompt` | `build_system_prompt(mode: Mode, is_private: bool, is_lover: bool, persona_name: str) -> str` | 主入口，OFF 返回空串 |

**`build_system_prompt()` 的派发表**：

| mode | is_private | is_lover | 实际调用的函数 |
|---|---|---|---|
| OFF | — | — | 返回 `""` |
| SUGGESTIVE | 是 | 是 | `_suggestive_private_lover()` |
| SUGGESTIVE | 是 | 否 | `_suggestive_private_not_lover()` |
| SUGGESTIVE | 否 | 是 | `_suggestive_group_lover()` |
| SUGGESTIVE | 否 | 否 | `_suggestive_group_cautious()`（只有一句「保持正常，不要主动说暧昧的话」） |
| EXPLICIT | 任意 | 任意 | `_explicit_private_lover()`——不判断 `is_private`，因为 `transition()` 已保证 EXPLICIT 只在私聊成立 |

每段注入的内容边界：

| 段 | 注入要点 |
|---|---|
| 私聊 + 恋人 + SUGGESTIVE | 用双关表达；语气撒娇欲拒还迎；主动挑逗每次最多两句；附 5 条变体参考；他说直白词时害羞纠正；不越级进直白 |
| 私聊 + 非恋人 + SUGGESTIVE | 「被撩到了但嘴上不认」；明说例的调侃拒绝；附 `exemplar_mod.prompt_lines("deflect", 2)` 两条范本；不主动进直白 |
| 群聊 + 恋人 + SUGGESTIVE | 表面完全正常、内里调情；附 4 条变体；每次最多一句；绝对不用直白词；禁止把私聊内容搬进群 |
| 私聊 + 恋人 + EXPLICIT | 可以直白用词（`操/插/穴/逼/湿/硬/射/奶`）；每次最多两句直白；先问他喜欢什么；附 2 条参考句；声明这段上下文会被记进私密记录 |

**谐音表 `METAPHOR_MAP`（15 条真值，按源码顺序）**：

| # | 直白 | 谐音 |
|---|---|---|
| 1 | 插 | 查 |
| 2 | 操 | 超 |
| 3 | 肏 | 搞 |
| 4 | 日 | 入 |
| 5 | 穴 | 学历 |
| 6 | 逼 | 避税 |
| 7 | 奶 | 耐 |
| 8 | 舔 | 填 |
| 9 | 屌 | 调查 |
| 10 | 鸡巴 | 机霸 |
| 11 | 肉棒 | 大调查 |
| 12 | 硬 | 愣 |
| 13 | 湿 | 师 |
| 14 | 高潮 | 高抄 |
| 15 | 射 | 设 |

`METAPHOR_MAP` 只在 `_EXPLICIT_SENT_WORDS` 的构造里被用到（当成「哪些字算色色」的词库），**不参与任何替换**。真正做替换的是下面这张独立的 `_GROUP_REPLACE`。

**`sanitize_group_reply()` 的替换表（16 条，按列表顺序逐词 `str.replace`，长词在前）**：

| 原词 | 换成 | 原词 | 换成 |
|---|---|---|---|
| 大鸡巴 | 大调查 | 操我 | 超我 |
| 大屌 | 大调查 | 插你 | 查你 |
| 肉棒 | 大调查 | 插我 | 查我 |
| 鸡巴 | 机霸 | 插进 | 查进 |
| 做爱 | 做点大人的事 | 小穴 | 学历 |
| 想操 | 想超 | 奶子 | 耐子 |
| 操你 | 超你 | 肏 | 搞 |
| 屌 | 调查 | 逼 | 避税 |

**句数压制 `limit_intimate_sentences()`**：

| 模式 | 私聊 | 上限 | 判定用词表 |
|---|---|---|---|
| `explicit` | 是 | 2 句 | `_EXPLICIT_SENT_WORDS`（`METAPHOR_MAP` 的全部键 + `做爱/想要你/硬了/湿了/欲仙欲死/干你/奶子/大鸡巴/大屌/肉棒`） |
| `explicit` | 否 | 1 句 | `_SUGGESTIVE_SENT_WORDS`（`查/超/调查/学历/避税/棒棒糖/福福/欧金金`） |
| `suggestive` | 任意 | 1 句 | `_SUGGESTIVE_SENT_WORDS` |
| `off` / 其它 | — | 不处理 | — |

实现方式：`re.findall(r"[^。！？!?~～\n]+[。！？!?~～]?", raw)` 切句，逐句累计命中数，超过上限的句子整句换成 `……`（不删句，避免看起来像断线），最后 `"".join(out).strip()`，结果为空则退回原文。

**`loop.py` 里的四处置入点**：

| 行 | 做什么 |
|---|---|
| `loop.py:528-543` | `intimate_mode` 非 `off` 时 `build_system_prompt()` 拼进 `state_hint` |
| `loop.py:545-552` | 同一分支里叠 `analyze.profile_hint(uid)`（偏好档案） |
| `loop.py:701-709` | 回复生成后：群聊先 `sanitize_group_reply()`，再 `limit_intimate_sentences()` |
| `loop.py:252-254` / `619-629` | `_in_intimate` 为真即 `_forced_deep=True`，强制走深思模型（防出戏），不受全局深度思考开关影响 |

**坑**：五个 builder（`_suggestive_private_lover` / `_suggestive_private_not_lover` / `_suggestive_group_lover` / `_suggestive_group_cautious` / `_explicit_private_lover`）都收了 `persona_name` 参数，**没有一个用到它**（签名保留、内部全是固定文案）；`build_system_prompt()` 的 EXPLICIT 分支不看 `is_private`，如果哪天 `transition()` 的保证被改掉，群聊会直接拿到直白段；`_GROUP_REPLACE` 的 `逼 → 避税` 会把「逼近」「逼真」也换掉；`sanitize_group_reply()` 只在 `loop.py:705` 的 `chat_type == "group"` 分支被调，`chat_type` 为空的场景（面板）不走它。

### 3.4 `app/intimacy/analyze.py`

**职责**：把 `intimate_context` 里攒的私密对话折成一张「他喜欢什么 / 不喜欢什么 / 边界在哪」的档案，下次亲密模式注入。只写他表达过的，模型解析失败就给空档案，不编。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `due` | `due(user_id: int) -> bool` | 已结束的会话数 > `sessions_seen` |
| `profile_of` | `profile_of(user_id: int) -> dict[str, Any] \| None` | 读档案，三个列表字段反序列化 |
| `profile_hint` | `profile_hint(user_id: int) -> str` | 给 prompt 的偏好段（无档案返回空串） |
| `pending_users` | `pending_users(limit: int = 2) -> list[int]` | 待分析用户列表 |
| `analyze_user` | `analyze_user(rt, user_id: int) -> dict[str, Any] \| None`（`async`） | 真正跑一次分析 |

**档案表 `intimate_profile`**（`user_id` 是主键，所以一人一份，永远覆盖写）：

| 列 | 类型/默认 | 内容 |
|---|---|---|
| `user_id` | `INTEGER PRIMARY KEY` | QQ 内部 user_id |
| `likes` | `TEXT DEFAULT '[]'` | JSON 字符串数组 |
| `dislikes` | `TEXT DEFAULT '[]'` | 同上 |
| `boundaries` | `TEXT DEFAULT '[]'` | 同上 |
| `summary` | `TEXT DEFAULT ''` | 一句话概括 |
| `sessions_seen` | `INTEGER DEFAULT 0` | 分析时的会话总数，用来判「有没有新的」 |
| `updated_at` | `TEXT NOT NULL` | 时间戳 |

**`analyze_user()` 的执行顺序**：

1. `_collect(uid)`：取最近 6 条 `intimate_context` 行，每行 `events` 反序列化后取末尾 60 条，`role == "user"` 记成「他」、非 `user` 记成「你」，正文 `[:160]`，拼成多行文本。
2. 统计 `COUNT(*) FROM intimate_context WHERE user_id = ?`（**含未结束的**）写进 `sessions_seen`。
3. 文本为空 → 返回 `None`（不建空档案）。
4. 用固定 system 提示要求只输出一个 JSON 对象：`{"likes":[...],"dislikes":[...],"boundaries":[...],"summary":"..."}`。
5. `models.pick("reflect")` 选模型，`max_tokens=400`、`temperature=0.4`、`thinking=False`；失败 → `text=""`。
6. `appraise.strip_actions()` 去括号动作 → `_parse_json()` 正则抠 `\{.*\}` → `_save()`。
7. `_save()`：三个列表各取前 6 条、每条 `[:80]`、转 JSON；`summary` 截 300 字；用 `INSERT ... ON CONFLICT(user_id) DO UPDATE SET ...` 覆盖写；返回 `profile_of()`。

**`profile_hint()` 的输出形状**：第一行固定「【你已经知道的他】」，然后按有无内容追加「他吃这一套：A、B、C、D」「他不太喜欢：…」「他的边界：…」「一句话：…」——三个列表各取前 4 条，全部为空则返回空串。

**它到底会不会自动触发（这条必须写准）**：

| 路径 | 事实 |
|---|---|
| 手动 | `POST /v1/console/intimate/<uid>/analyze` → `console.intimate_analyze()` → 直接 `await analyze_user()`。控制台「分析偏好」按钮就走这条（`panel/console.html:2806`） |
| 自动 | `app/memory/evolve.py:253-271` 的 `evolve_tick()` 里，当 `observe.think_due()` 为真（每个自然日第一次满足）时，`for uid in ianalyze.pending_users(2)` 逐个分析，每人每天最多 2 个 |
| 自动路径的实际效果 | `pending_users()` 与 `due()` 都只统计 `ended_at IS NOT NULL` 的行，而 `memory.end_session()` 在 `app/` 里没有任何调用点（运行期没人关会话；调用它的只有 `scripts/test_intimacy.py` 与 `scripts/fullmatrix.py` 这两支测试脚本），所以 `ended_at` 永远是 `NULL` → 这两个函数恒返回空 → `evolve_tick()` 这条自动路径实际上一份档案也生成不出来 |

结论：**目前唯一能产出 `intimate_profile` 的路径是控制台的 `POST /v1/console/intimate/<uid>/analyze`**。这不是设计意图（`evolve.py:263` 的注释写的是「有新的私密会话就顺手折一份」），是 `end_session()` 没接线导致的空转。本地 `data/life.db` 实测印证：`intimate_context` 只有 1 行且 `ended_at` 为空，`intimate_profile` 0 行。

**坑**：`analyze_user()` 的 `sessions_seen` 用 `COUNT(*)`（含未结束），`due()` 用 `ended_at IS NOT NULL` 的口径，两个数永远对不上；`_collect()` 取的是「最近 6 条会话」，但 `ORDER BY id DESC LIMIT 6` 之后再 `events[-60:]`，所以老的上下文会被新会话挤掉。

### 3.5 `app/intimacy/exemplars.py`

**职责**：用户给的脏话范本，长期存在 `kv` 里（随库走、可审计、可被控制台查看），不藏在代码里。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `KV_KEY` | `"intimate_exemplars"` | 存放位置 |
| `SEED_EXEMPLARS` | `list[dict[str, str]]`，10 条 | 首次播种内容 |
| `ensure_seeded` | `ensure_seeded() -> None` | 键为空时写入种子；已有值不覆盖 |
| `all_items` | `all_items() -> list[dict[str, str]]` | 读全部；空或解析失败退回种子 |
| `by_kind` | `by_kind(kind: str) -> list[str]` | 按 `kind` 取文本 |
| `prompt_lines` | `prompt_lines(kind: str, limit: int = 6) -> str` | 拼成 `- xxx` 多行 |
| `stats` | `stats() -> dict[str, Any]` | `{"total": n, "kinds": {kind: n}}` |

**种子内容（10 条，`kind` 三档）**：

| kind | 条数 | 文本 |
|---|---|---|
| `metaphor` | 6 | 「你身下的棒棒糖呐~」/「只想被你查（插）」/「是不是想要查我的学历（插我的穴）」/「是不是想狠狠的查出我的避税（插出我的逼水）」/「我想要你的一个大调查进来（大屌插进来）」/「你身下的那个棒棒糖，还在不在呀」 |
| `deflect` | 2 | 「不会吧不会吧，你就是想查我的福福（插我的逼逼）？你真是一个肤浅的人……」/「我是好女孩，我希望我们在一起了再放开了去做这些色色的事，因为我爱你」 |
| `explicit` | 2 | 「我想你了，想要你抱着我」/「我有点想被你碰，但只说两句，再说就害羞了」 |

**谁在读它（进 EXPLICIT / SUGGESTIVE 时怎么用）**：

| 消费点 | 用法 |
|---|---|
| `gate.prepare()` → `ensure_seeded()` | 每次门控都调，但只在键为空时真写 |
| `prompt._suggestive_private_not_lover()` | `prompt_lines("deflect", 2)`：非恋人私聊时给两条「调侃拒绝」范本 |
| `app/life/proactive.py:548-556` | 主动消息兜底链最后一级：恋人 + 开关打开时取 `by_kind("metaphor")[:1]` 当主动调情那一句 |
| `api/console.py` 的 `GET /v1/console/intimate/exemplars` | 控制台查看「她学过的范本」+ 统计 |
| `prompt._suggestive_private_lover()` / `_suggestive_group_lover()` | **不用**范本库，用的是模块里硬编码的 `SUGGESTIVE_TEMPLATES` |

`kind == "explicit"` 那两条目前**没有任何运行时消费点**，只有控制台列表能看到。想让它生效必须改 `prompt.py` 自己接。

**坑**：`ensure_seeded()` 判空用的是 `kv_get(KV_KEY, "")` 真值判断，所以用 `kv_set(KV_KEY, "")` 清库等效于「重置为种子」；`all_items()` 遇到列表里全是非法项时 `return out or list(SEED_EXEMPLARS)`，会静默退回种子而不是报空。

### 3.6 `app/intimacy/guide.py`

**职责**：只对「恋人 + 私聊」生效的一条引导链：暗示 → 明示 → 露骨 → 收尾，用户明确露骨 3 次后进入收尾，收尾时把过程存成经验文件。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `active` | `active(uid: int) -> bool` | `kv["guide_mode:<uid>"] == "1"` |
| `stage` | `stage(uid: int) -> int` | 读阶段，夹 0–3 |
| `set_stage` | `set_stage(uid: int, value: int) -> None` | 写阶段（夹 0–3） |
| `start` | `start(uid: int) -> None` | 开引导：`mode=1`、`stage=0`、`explicit=0` |
| `explicit_count` | `explicit_count(uid: int) -> int` | 露骨次数 |
| `bump` | `bump(uid: int, text: str) -> int` | 按这句话推进阶段，返回新阶段 |
| `hint` | `hint(uid: int, is_lover: bool, chat_type: str) -> str` | 按阶段给 prompt 提示 |
| `finish` | `finish(uid: int, note: str = "") -> str` | 写经验文件、复位三个键，返回路径 |

**`kv` 键**：`guide_mode:<uid>`（`"1"`/`"0"`）、`guide_stage:<uid>`（0–3）、`guide_explicit:<uid>`（计数）。

**阶段推进（`bump()`）**：

1. 未 `active` 直接返回 0。
2. `detect_intent(text)` 复用 `mode.py` 的同一个函数。
3. `intent == "explicit"`：露骨计数 +1；计数 ≥ 3 时阶段直接置 3；否则阶段若 < 2 就升到 2。
4. `intent == "suggestive"` 且阶段 < 1：升到 1。
5. 返回当前阶段。

**四段提示词（`hint()` 的真实文案要点）**：阶段 0「暗示」——用棒棒糖/查学历/欧金金勾他，问「想不想查」，最多两句；阶段 1「明示」——说你想要他抱/碰，问他想要什么，仍最多两句；阶段 2「露骨」——先黏一句，再直接问喜欢怎么碰，每次不超过两句；阶段 3「收尾」——「这轮只做收尾：说你困了、要睡了，明天再继续；不要再加新的露骨内容」。

**`hint()` 的生效条件**：`active(uid)` 且 `is_lover` 且 `str(chat_type) == "private"`，三者缺一返回空串。

**`finish()`**：从 `events` 取该 uid 最近 40 条 `chat_type='private'` 记录（倒序后 `reversed()` 还原成时间正序，`text[:300]`）当 transcript；payload 是 `{user_id, finished_at, stages:["暗示","明示","露骨","收尾"], explicit_requests, note, transcript}`，写到 `DATA_ROOT / "experience" / "guide-<uid>-<YYYYmmdd-HHMMSS>.json"`；然后把三个 `kv` 键复位成 `"0"`。

**调用点**：`loop.py:520-524` 每轮 `bump()` + `hint()` 并入 `state_hint`；`loop.py:746-747` 在她那条回复落库之后，`stage >= 3` 就 `finish()`。

**坑**：`start()` 在 `app/` 里**没有任何调用点**，控制台也没有对应路由（调它的只有 `scripts/smoke.py`、`scripts/corematrix.py`、`scripts/fullmatrix.py` 这三支测试脚本），所以运行期引导模式的开关只能靠直接把 `kv["guide_mode:<uid>"]` 写成 `"1"`（或脚本里 `db.kv_set`）；`bump()` 里的 `elif s < 2` 分支意味着「第 1 次露骨就跳到阶段 2」，跳过了阶段 1 的明示；`finish()` 里 `if not active(uid) and stage>=3` 的判定在 `loop.py` 侧只查了 `active + stage`，没有查 `stage==3` 之外的收尾条件。

### 3.7 `app/intimacy/memory.py`

**职责**：亲密模式下的上下文记录。与普通记忆**物理隔离**——不进 `app/memory/h2.py: add()`，不进 `mem_item`，也不进只读视图 `memories`，因此不参与 `h2.retrieve()` 的召回、不进 prompt 的「记忆」段。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `start_session` | `start_session(user_id: int, mode: str, is_private: bool) -> int` | 新建一条会话行（`events='[]'`） |
| `end_session` | `end_session(user_id: int) -> int \| None` | 给最近一条未结束的行写 `ended_at` |
| `record` | `record(user_id: int, role: str, text: str, mode: str, is_private: bool) -> None` | 追加一条对话到当前会话 |
| `recent` | `recent(user_id: int, limit: int = 30) -> list[dict[str, Any]]` | 读会话（`events` 反序列化） |
| `stats` | `stats(user_id: int) -> dict[str, Any]` | `{"sessions": n, "user_id": uid}` |

**表 `intimate_context`**：

| 列 | 类型/默认 | 说明 |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | 会话 id |
| `user_id` | `INTEGER NOT NULL` | 无外键约束，`uid=0` 也允许 |
| `mode` | `TEXT DEFAULT 'suggestive'` | 写入时是 `mode.value` |
| `is_private` | `INTEGER DEFAULT 1` | 布尔转 0/1 |
| `started_at` | `TEXT NOT NULL` | 带时区 ISO |
| `ended_at` | `TEXT`（可空） | 只有 `end_session()` 会写 |
| `events` | `TEXT DEFAULT '[]'` | JSON 数组，每条 `{ts, role, text, mode, private}` |

索引 `idx_intimate_user(user_id, ended_at)` 就是给「找某个 uid 的未结束会话」用的。

**`record()` 的顺序**：

1. 找该 uid `ended_at IS NULL` 的最近一条（`ORDER BY id DESC LIMIT 1`）。
2. 找不到先 `start_session()`，再重查一次；仍找不到就返回（不抛）。
3. `events` 反序列化（坏 JSON 当空数组）。
4. 追加 `{"ts": 现在, "role": role, "text": text[:500], "mode": mode, "private": is_private}`。
5. `UPDATE ... SET events = ?`，只保留**最后 200 条**（`events[-200:]`）。

**与普通记忆的隔离边界（逐条）**：

| 维度 | 普通记忆 | 亲密上下文 |
|---|---|---|
| 写入接口 | `app/memory/h2.py: add()` | `intimacy.memory.record()` |
| 实体表 | `mem_item` / `mem_cue`（引擎） | `intimate_context` |
| 是否进 `memories` 视图 | 是 | 否 |
| 是否参与召回 | `h2.retrieve()` | 否 |
| 是否进 prompt 的记忆段 | 是 | 否，只通过 `analyze.profile_hint()` 间接进 |
| 清理入口 | `h2.forget_user()` / `h2.forget_all()` | `users.forget_user()`、`console.delete_friend()`、`DELETE /v1/console/intimate/<uid>` |

**坑**：`end_session()` 没有调用点，所以一条 `intimate_context` 行会从第一次进入亲密模式起一直「未结束」地追加下去，直到满 200 条开始滚雪球——`record()` 永远只往同一行里塞；`mode` / `private` 只在开会话那一行写，后续翻转（比如私聊转群聊）只体现在每条 event 的字段里，行级字段不再更新；`uid=0`（面板）也能写，但面板根本不会调到 `record()`，所以这张表里不会有 `user_id=0` 的行。

## 4. 数据流

```text
QQ / 控制台来一句话
  └─ gate.prepare(uid, text, chat_type)
        ├─ is_private = (chat_type == "private")
        ├─ is_lover    = users.lover_of(uid) is not None      # docs/08
        ├─ users.is_blocked(uid) → 直接 (OFF, "blocked", False, is_private)
        ├─ exemplars.ensure_seeded()                          # kv["intimate_exemplars"]
        ├─ mode.detect_intent(text) → stop / explicit / suggestive / none
        ├─ mode.transition(uid, intent, is_private, is_lover)
        │     ├─ expire(uid) 先过期降级（45 分钟 / 8 小时 / 跨天）
        │     ├─ 写 kv["intimate_mode:<uid>"]、kv["intimate_mode_ts:<uid>"]
        │     └─ 返回 (mode, note)   note ∈ {"", "stop_soft", "group_limit", "not_lover_deflect"}
        └─ mode != OFF → memory.record(uid, "user", text, mode.value, is_private)

  └─ loop.chat_turn(..., intimate_mode=mode.value, intimate_note=note, is_lover, chat_type)
        ├─ _in_intimate → _forced_deep → 强制深思模型
        ├─ mode != off → prompt.build_system_prompt()  拼进 state_hint
        │                + analyze.profile_hint(uid)    拼进 state_hint
        ├─ note == "not_lover_deflect" / "stop_soft" → 追加对应软化提示
        ├─ guide.active(uid) → guide.bump() + guide.hint()
        ├─ 模型出回复
        └─ _in_intimate → chat_type == "group" 时 prompt.sanitize_group_reply()
                          → prompt.limit_intimate_sentences(reply, mode, is_private)

  └─ gate.finish(uid, mode, reply, is_private)
        └─ mode != OFF → memory.record(uid, "her", 回复拼接, mode.value, is_private)
```

## 5. 改这一层要注意

1. **加一个意图词**：`mode.py` 三个正则 + `prompt._GROUP_REPLACE`（群聊兜底）+ `prompt._EXPLICIT_SENT_WORDS` / `_SUGGESTIVE_SENT_WORDS`（句数压制判定）+ `guide.bump()`（它复用 `detect_intent`）。漏掉句数词表的后果是「群聊里那句没被压成 `……`」，漏掉替换表的后果是「直白词原样发到群里」。
2. **加一档模式**：`Mode` 枚举 + `transition()` 每个分支 + `prompt.build_system_prompt()` 派发表 + `loop.py` 里所有 `intimate_mode != "off"` 的字符串比较 + 控制台 `intimate_view` 的模式显示。`loop` 里比的是字符串而不是枚举，加档位必须先改这些地方。
3. **改 TTL**：`EXPLICIT_TTL_MIN` / `SUGGESTIVE_TTL_MIN` 两个常量 + `expire()` 里的跨天判断。「跨天必降」不是常量，是 `ts.date() < now.date()` 这个条件，改 TTL 不影响它。
4. **接线 `end_session()`**：想让 `analyze` 自动路径真正跑起来，必须在运行期某处（比如模式降级到 OFF、或 `gate.finish()` 检测到 `expire()` 刚掉档）调 `memory.end_session(uid)`；现在它只被 `scripts/test_intimacy.py` 与 `scripts/fullmatrix.py` 调用，而 `analyze.due()` 与 `pending_users()` 的口径都依赖它。
5. **动 uid 维度的键**：新键必须同时加进 `users.forget_user()` 的七个键清单（`intimate_mode`、`intimate_mode_ts`、`love_pending`、`proactive_last_ts`、`proactive_window`、`proactive_nag`、`proactive_angry`）与 `console.delete_friend()` 的四个键清单（`intimate_mode`、`intimate_mode_ts`、`love_pending`、`proactive_last_ts`）——两处清单本来就不一样，别只改一处。
6. **动私密表**：`intimate_context` / `intimate_profile` 的清理入口有三处（`users.forget_user()`、`console.delete_friend()`、`DELETE /v1/console/intimate/<uid>` 只删前者），新增表要照这三处补。
7. **想让面板也能进亲密模式**：必须给 `/v1/chat` 接上 `igate.prepare()` / `igate.finish()`（`routes.py:150-162` 现在没有），并把结果当 `intimate_mode` / `intimate_note` 传进 `loop.chat_turn()`。只改 `prompt.py` 没有用。
8. **面板线的 `is_lover` 特例**：`routes.py:159` 写的是 `is_lover = _igate.is_lover_of(uid) if uid > 0 else True`——**`uid=0` 时强制按恋人处理**。所以面板不走真实恋爱判定（`lover` 表里根本没有 `user_id=0` 的行），实际影响只有两处：`loop.py:429` 让面板拿到「恋人语气样本」（`chat_type` 空也算恋人），`loop.py:508` 的「恋人私聊更主动」块要求 `chat_type == "private"`，面板不满足所以不触发。同理 `routes.py:1091` 的 OpenAI 兼容口也直接传 `is_lover=True`。

## 6. 交叉引用

- 相关：docs/08（`users.lover_of()` / `is_blocked()` / `forget_user()` 是全层的准入与清理依赖；恋人门槛 `THRESHOLD = 80.0`）
- 相关：docs/17（`intimate_context` / `intimate_profile` 的建表语句与索引）
- 相关：docs/05（`loop.chat_turn()` 的 4 处置入点、`_forced_deep` 强制深思、`gate` 的三个调用方）
- 相关：docs/06（`app/memory/evolve.py` 的每日 tick 是 `analyze` 自动路径的唯一入口，而它依赖 `observe.think_due()`）
- 相关：`mode.py:183` 的 TODO 原文写着「断恋后 EXPLICIT 残留待修——（docs/29 §十）」，`db.py` 里 `intimate_profile` 的注释也指向 docs/29；这两处引用的是旧文档编号，对应内容就是本章 §3.1 的「断恋后 EXPLICIT 残留」与 §3.4 的偏好档案
