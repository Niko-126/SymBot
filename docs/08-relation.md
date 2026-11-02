# 08 · 关系系统（app/relation）

> 覆盖：`app/relation/*.py`（11 个文件 / 1877 行，其中 `__init__.py` 为 0 字节空文件）｜基于 2026-11-01 的代码状态｜上游：docs/17（`relation` / `user_relation` / `lover` / `qq_user` / `session` / `friend_request` 表结构）、docs/19（人设 `stages` 阈值）｜下游：docs/09（亲密模式）、docs/05（对话主循环）

## 1. 这一层干什么

这一层把「她对你什么感觉」变成四个 0–100 的数，再把这些数翻译成阶段、称呼、恋爱状态和离开的条件。它解决的问题是**让模型没有定价权**：模型只能说「这一轮像什么」（标签 + 强度 + 三轴建议），真正的加减、封顶、落库只在这里发生一次。

调用它的是对话主循环 `app/mind/loop.py`（每轮结束时结算一次），落库落在 `app/core/db.py` 管的 `relation` / `user_relation` / `relation_log` / `lover` / `qq_user` / `session` / `traits` / `friend_request` 这八张表，以及 `kv` 里的日上限计数与若干状态键。它不拼上下文、不生成台词——`judge.py` 确实会调一次模型，但只拿它吐的 JSON 评估结果，不拿它写的话。它明确不做的事：不管记忆（记忆在 `app/memory/h2.py`）、不管亲密模式的状态机（在 `app/intimacy/mode.py`）、不决定她这一轮回不回（在 `app/life/behavior.py`）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/relation/__init__.py` | 0 | 空文件，无导出 |
| `app/relation/engine.py` | 395 | 关系数值唯一结算入口：标签权重、融合、限幅、厌恶、阶段、人格向量 |
| `app/relation/appraise.py` | 96 | 无 Key 也可用的规则词表评估，输出 `{valence,intensity,tags,reason}` |
| `app/relation/judge.py` | 78 | 语言分析式评估：让模型输出同结构 + 三轴与厌恶建议，失败返回 `None` |
| `app/relation/users.py` | 321 | QQ 群成员、每人独立关系、唯一恋人绑定、分手冷却、拉黑、针对性失忆 |
| `app/relation/session.py` | 302 | 会话生命周期：命名锁定、厌恶相位、归档、终止、重启 |
| `app/relation/love.py` | 223 | 表白/分手/她主动提议的确定性接线（不走模型）、家人判定、复合冷却话术 |
| `app/relation/finale.py` | 265 | 告别信 + 自述 + 档案，写进 `Girl-memory/`，桌面留副本 |
| `app/relation/friends.py` | 86 | 好友申请的记账与状态机（`friend_request` 表） |
| `app/relation/greet.py` | 55 | 主动招呼生面孔：话术池 + 每人只招呼一次 |
| `app/relation/rituals.py` | 56 | 晚安/早安/认识第 N 天的轻提示与当日确认 |

## 3. 模块逐一详解

`app/relation/__init__.py` 是 0 字节空文件，不导出任何符号，包内模块之间全部走显式 `from app.relation import xxx`，没有任何副作用导入。以下从 `engine.py` 开始逐文件讲。

### 3.1 `app/relation/engine.py`

**职责**：关系数值的唯一结算入口。`apply_appraisal()` 收一个「评估结果」，吐回这一轮实际发生了什么（增量、新值、阶段），中途完成限幅、日上限、厌恶、人格调制、落库、流水。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `TAG_WEIGHTS` | `dict[str, tuple[float, float, float]]` | 标签 → (affinity, trust, attraction) 权重 |
| `DAILY_POS_CAP` / `DAILY_NEG_CAP` | `5.0` / `-8.0` | 日上限缺省值（配置读不到时用） |
| `get_relation` | `get_relation() -> dict` | 读 `relation` 单行，缺行返回三轴 0 |
| `relation_for` | `relation_for(user_id: int = 0) -> dict` | 这一轮该看谁的关系（uid>0 读 `user_relation`，否则读全局） |
| `stage_payload` | `stage_payload(persona: Persona, affinity: float) -> dict` | 阶段、称呼、下一阶段、整张阶段表 |
| `caps_limits` | `caps_limits() -> tuple[float, float, bool]` | 返回 `(正上限, 负上限, 是否启用)` |
| `log` | `log(trigger: str, reason: str, da: float, dt: float, dr: float) -> None` | 写 `relation_log` |
| `apply_appraisal` | `apply_appraisal(appraisal: dict, persona: Persona, trigger: str = "chat", user_id: int \| None = None) -> dict` | 唯一结算入口 |
| `set_relation` | `set_relation(affinity: float \| None = None, trust: float \| None = None, attraction: float \| None = None, reason: str = "手动校准") -> dict` | 手动校准（面板/开发者），夹 0–100 并记 manual 流水 |
| `recent_log` | `recent_log(limit: int = 20) -> list[dict]` | 读最近流水 |
| `init_traits` | `init_traits(persona: Persona) -> None` | 用人设基线同步 `traits` 表 |
| `get_traits` | `get_traits() -> list[dict]` | 读人格向量 |

**四维落在两张表里**：

| 维度 | `relation`（单行，`id=1`，面板线） | `user_relation`（每 `user_id` 一行，QQ 多人线） |
|---|---|---|
| 好感 | `affinity REAL NOT NULL DEFAULT 0` | `affinity REAL NOT NULL DEFAULT 0` |
| 信任 | `trust REAL NOT NULL DEFAULT 0` | `trust REAL NOT NULL DEFAULT 0` |
| 心动 | `attraction REAL NOT NULL DEFAULT 0` | `attraction REAL NOT NULL DEFAULT 0` |
| 厌恶 | `aversion`——建表语句里没有，由 `db.py` 迁移补：`ALTER TABLE relation ADD COLUMN aversion REAL NOT NULL DEFAULT 0` | `aversion REAL NOT NULL DEFAULT 0`（原生列） |
| 阶段 | 无此列，永远从 `affinity` 现算 | `stage TEXT NOT NULL DEFAULT 'observer'`，写库时存快照 |
| 到顶时间 | 无 | `hit_100_at TEXT`，实际记的是「首次 ≥ 80」的时间 |
| 时间戳 | `updated_at` | `updated_at NOT NULL` |

0–100 不是数据库约束（没有 CHECK），是代码里每一处写库前的 `max(0.0, min(100.0, ...))` 兜住的。

**标签权重表（真值）**：

| tag | affinity | trust | attraction |
|---|---|---|---|
| `care` | 0.90 | 0.30 | 0.15 |
| `self_disclosure` | 0.70 | 0.95 | 0.20 |
| `companionship` | 0.75 | 0.20 | 0.10 |
| `night` | 0.55 | 0.35 | 0.45 |
| `sweetness` | 0.40 | 0.05 | 0.85 |
| `repair` | 0.55 | 0.90 | 0.30 |
| `attack` | -1.60 | -0.60 | -0.30 |
| `conflict` | -1.00 | -0.35 | -0.25 |
| `neglect` | -0.45 | -0.30 | -0.15 |
| `test` | -0.70 | -0.45 | -0.40 |
| `comparison` | -0.90 | -0.40 | -0.35 |

表里没有的标签权重按 `(0.0, 0.0, 0.0)` 计，不会抛异常。

**`apply_appraisal()` 的完整流程**：

1. `appraisal` 为空直接早退，返回 `{"d_affinity": 0.0, "d_trust": 0.0, "d_attraction": 0.0, "capped": False}`（这个分支没有 aversion 键）。
2. 取 `intensity = max(0.0, min(1.5, float(appraisal.get("intensity", 0.4))))`，`tags` 转字符串表，`valence` 默认 `"neutral"`。
3. 读 `kv["last_appraisal"]` 解析上一轮标签；若上轮命中 `conflict` / `test` / `comparison` 且本轮 `valence == "positive"`，判为修复，往 `tags` 追加 `repair`。
4. 把**本轮** appraisal（此时已带 `llm` 子键）写回 `kv["last_appraisal"]`。
5. `base_tags = set(tags)`——这是模型标签合并前的集合（含第 3 步补的 `repair`），后面安全限幅只认它。
6. 按 `TAG_WEIGHTS` 累加出 `w_a / w_t / w_r`。
7. `valence == "negative"` 且 `w_a > 0` 时反号并打七折：`w_a = -w_a * 0.7`。
8. `valence == "neutral"` 且没有任何标签时给 `w_a = 0.15`（纯来打招呼也不至于零增长）。
9. 换算：`raw_a = w_a * intensity * 1.2`、`raw_t = w_t * intensity * 1.2`、`raw_r = w_r * intensity * 1.0`（心动不吃那 1.2 的放大）。
10. 修复加成：`raw_a += 0.35`、`raw_t += 0.55`。
11. 取 `llm = appraisal["llm"]`，算 `risky = tags ∩ {attack, test, comparison, conflict}` 非空；`risky` 为真时把模型三轴全部裁成「不许为正」（`min(0.0, ...)`），厌恶不为正时置 0。
12. **规则 40% + 模型 60% 融合**：`raw_x = raw_x * 0.4 + llm[x] * 0.6`（x ∈ affinity / trust / attraction）；模型给的 `tags` 用 `dict.fromkeys` 去重合并后截断到 6 个；模型给的 `reason` 覆盖规则理由。
13. 算厌恶增量（见下节独立流程）。
14. 落库：`uid > 0` 走 `users.update_relation()`（顺带刷 `stage` 与 `hit_100_at`），否则 `UPDATE relation SET affinity=?, trust=?, attraction=?, updated_at=? WHERE id=1`。
15. 写流水 `relation_log`，返回四维新值 + 三个增量 + `capped` + `stage_payload()`。

**单轮硬上限与日上限的真实顺序**：

```text
raw_a = 融合结果
raw_a = max(-3.5, min(3.5, raw_a))      # 单轮硬上限，三轴各一份
da    = _pos(raw_a) 或 _neg(raw_a)      # 日上限过滤（caps.enabled=True 才裁剪）
```

- 单轮硬上限：`-3.5 .. +3.5`，三轴独立，常量直接写在函数体里（`engine.py:270-272`），没有命名常量。
- 日上限：`caps_limits()` 读 `cfg.relationship.caps`，默认值 `(5.0, -8.0, False)`；`config.local.yaml` 里是 `relationship.caps.enabled: false`，也就是**生产环境默认不限**。
- 计数键：`_cap_key(uid)` = `relation_cap:<YYYY-MM-DD>`，uid 非 0 时追加 `:<uid>`；值是一个 JSON `{"pos": float, "neg": float}`，存 `kv`。

**厌恶另算（不参与 40/60 融合，只取两者较大值）**：

| 触发条件 | 增量 | 代码位置 |
|---|---|---|
| `attack` ∈ `base_tags` | `+6.0 * intensity` | `engine.py:209` |
| `conflict` ∈ tags 且 `attack` ∉ base_tags | `+0.8 * intensity` | `engine.py:211` |
| `comparison` 或 `test` ∈ tags | `+0.35 * intensity` | `engine.py:213` |
| 模型给了 `aversion` 且 `risky` | `max(delta, min(llm_av, 4.0 或 1.5))`：明确辱骂封 4.0，其它封 1.5 | `engine.py:214-219` |
| 模型给了 `aversion` 且非 `risky` | `max(delta, min(llm_av, 0.5))`：普通聊天顶不上去 | `engine.py:220-222` |
| 无模型判断，且修复 / 正向且无攻击冲突 | `-1.2` | `engine.py:223-224` |

然后三道夹紧：整体 `max(-6.0, min(6.0, ...))`；非辱骂封顶 `1.5`；只有 `test`/`comparison` 而没有 `attack`/`conflict` 时封顶 `0.8`。最后是人格系数乘算（只在增量为正时）：

```text
aversion_delta *= 0.7 + (敏感 / 100) * 0.6 - (自持 / 100) * 0.3
```

`persona.trait_value()` 缺维度时返回 `50.0`，所以缺省等于系数 `0.85`；`whale-girl`（敏感 68 / 自持 38）算出 `0.994`，`whale-chan`（敏感 66 / 自持 46）算出 `0.958`。

厌恶落点：`uid > 0` 写 `user_relation.aversion`（夹 0–100）；否则调 `session.adjust_aversion()` 写 `relation.aversion`。整个面板分支包在 `try/except` 里，异常时 `aversion` 保持 `0.0` 继续往下走。

**阶段阈值两处同步**：

| key | 标签 | at | 人设 `call_user` |
|---|---|---|---|
| `observer` | 观察者 | 0 | 「你」 |
| `swimmer` | 同游者 | 20 | 他的名字 |
| `echo` | 回声 | 40 | 小名 |
| `samefreq` | 同频者 | 60 | 只有你们两个人用的称呼 |
| `lover` | 恋人 | 80 | 你定的称呼 |
| `bond` | 共生 | 95 | 你定的称呼 |

一处是 `persona/whale-girl.yaml` 与 `persona/whale-chan.yaml` 的 `stages:` 列表（`at` 分别是 0 / 20 / 40 / 60 / 80 / 95），由 `Persona.stage_for()` 按 `affinity >= at` 顺序取最后一个命中；另一处是 `users.STAGE_AT = (("observer", 0.0), ("swimmer", 20.0), ("echo", 40.0), ("samefreq", 60.0), ("lover", 80.0), ("bond", 95.0))`，由 `users.stage_for()` 写进 `user_relation.stage`。两份数值现在完全一致，但**没有任何代码校验它们一致**。

**数据结构 / 存储**：

- 写：`relation`（`affinity, trust, attraction, aversion, updated_at`）、`user_relation`（四维 + `stage` + `hit_100_at` + `updated_at`）、`relation_log`（`ts, d_affinity, d_trust, d_attraction, trigger, reason`）、`traits`（`dim, value, baseline, min_value, max_value`）。
- `kv` 键：`last_appraisal`（上一轮评估原始 JSON）、`relation_cap:<date>[:<uid>]`（当日累计增减）。

**耦合**：被 `app/mind/loop.py:760`（每轮结算）、`app/relation/love.py:128`（恋爱事件返回 state）、`app/api/routes.py:95/699/794`（面板与开发者接口）、`app/life/proactive.py:431/497`（主动消息看关系）调用；内部调 `db`、`users`、`session`、`persona`。

**坑**：

- 日上限只钳好感：信任的正增量走 `raw_t if raw_t >= 0 else _neg(raw_t)`（正数完全不过 `_pos`），心动 `dr = raw_r` 直接跳过两道上限。返回值的 `capped` 也只比较 `d_affinity` 与 `raw_a`。
- `relation_log` 没有 `user_id` 列，QQ 多人的流水混在一张表里，`finale._conflict_summary()` 读这张表时会把他人的辱骂算进当前这封信。
- `_phase()`（`engine.py:318-327`）与 `session.aversion_phase()`（`session.py:208-217`）是同一组阈值（100 / 85 / 60 / 30）的两份实现。
- 第 7 步反号只处理 `w_a`：`w_t` / `w_r` 在 `valence == "negative"` 且标签全为正时仍是正数，所以「说了甜话又骂人」这一类混合语句里，信任照样涨。
- `init_traits()` 只覆盖人设有、库里没有的维度；人设删掉的维度会永远留在 `traits` 里。

### 3.2 `app/relation/appraise.py`

**职责**：无 API Key 也能跑的规则评估。只决定「这一轮有哪几个标签」，权重在 engine 里。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `appraise` | `appraise(text: str, hour: int = 12) -> dict[str, Any]` | 词表命中 → 标签 → `{valence,intensity,tags,reason}` |
| `strip_actions` | `strip_actions(text: str) -> str` | 去掉模型偶尔吐的括号动作（1–20 字） |

**关键实现**：

1. 文本转小写，按固定顺序判定：`test`（`_TEST` 8 个词）→ `comparison`（`_COMPARE` 6 个）→ `attack`（`_ATTACK_SEVERE` 16 个）或 `conflict`（`_CONFLICT` 14 个，互斥）→ `sweetness`（`_SWEET` 17 个）→ `self_disclosure`（`_DISCLOSE` 28 个）→ `care`（`_CARE` 15 个）。
2. 一条都没命中且原文长度 ≥ 25 字 → `companionship`；仍没有且命中 `_COMPANION_HINT`（11 个词）→ `companionship`。
3. 兜底：只要 `len(text.strip()) >= 2` 就给 `companionship`——否则「小群里聊几十条好感不动」。
4. `valence`：命中 `test`/`comparison`/`conflict`/`attack` 任一 → `negative`；有标签 → `positive`；否则 `neutral`。
5. `intensity = 0.35 + 0.12 * len(tags)`，`hour in range(0, 5)` 时补 `night` 标签并 `+0.1`，最后 `round(min(1.2, ...), 2)`；上限 `1.2`，比 engine 允许的 `1.5` 低。
6. `reason` 是命中的中文注记用 `；` 拼接，没命中写「普通的聊天」。

**坑**：`night` 在 `valence` 与 `intensity` 算完之后才追加，所以凌晨聊天不会把 `neutral` 变成 `positive`；`_DISCLOSE` 里的单字「烦」会让「烦死」同时命中 `_CONFLICT` 与 `_DISCLOSE`，两个标签一起加。

### 3.3 `app/relation/judge.py`

**职责**：让模型看「怎么说的」，输出和规则评估同构的建议。失败一律 `None`，退回规则。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `judge` | `judge(rt, user_text: str, her_reply: str, relation: dict, aversion: float, session_seq: int = 1) -> dict \| None` | 调模型拿 JSON 建议 |
| `_clamp` | `_clamp(v, lo: float, hi: float) -> float` | 容错夹紧，非法值归 0 |

**关键实现**：

1. `rt.cfg.provider.is_mock` 为真直接返回 `None`（没 Key 不浪费一次请求）。
2. 拼 brief：当前四维 + 「【他说】（要评估的是这句）」+「【她回】（只作背景）」。
3. `models.pick("summary")` 选模型，`max_tokens=160`、`temperature=0.2`、`thinking=False`，记 `models.record(spec.key, "summary", ...)`。
4. 从回复里正则抠 `\{.*\}`，`json.loads`；任何异常 → `None`。
5. 字段裁剪：`tags` 取前 4 个；`intensity` 夹 `0.05–1.2`；三轴各夹 `-3..3`；`aversion` 夹 `-4..4`；`reason` 截 40 字。
6. 空信号（无标签且四项绝对值都 < 0.05）返回 `None`。

**坑**：`_SYSTEM` 里写的是 `"intensity":0-1`，但裁剪上限是 `1.2`；`reason` 截 40 字而提示词要求「20字以内」，实际不校验。

### 3.4 `app/relation/users.py`

**职责**：QQ 群多人线的全部数据与规则。只碰表，不碰 prompt/LLM。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `STAGE_AT` / `THRESHOLD` | 元组 / `80.0` | 阶段阈值；恋爱绑定门槛 |
| `ensure_user` | `ensure_user(openid: str, nickname: str = "") -> dict[str, Any]` | 按 openid 建/取用户，刷昵称与 `last_seen`，openid 空则抛 `ValueError` |
| `get_user` | `get_user(user_id: int) -> dict[str, Any] \| None` | 按内部 id 取 |
| `get_user_by_openid` | `get_user_by_openid(openid: str) -> dict[str, Any] \| None` | 按 openid 取 |
| `relation_of` | `relation_of(user_id: int) -> dict[str, Any]` | 取关系，缺行就建一条全 0 的 |
| `update_relation` | `update_relation(user_id: int, *, affinity: float \| None = None, trust: float \| None = None, attraction: float \| None = None, aversion: float \| None = None) -> dict[str, Any]` | 写四维 + 刷 `stage` / `hit_100_at` |
| `current_lover` | `current_lover(session_seq: int = 1) -> dict[str, Any] \| None` | 当前会话唯一恋人行 |
| `lover_of` | `lover_of(user_id: int, session_seq: int = 1) -> dict[str, Any] \| None` | 这个人是不是恋人 |
| `bind_lover` | `bind_lover(session_seq: int, user_id: int) -> dict[str, Any]` | 绑定恋人，失败抛 `ValueError` |
| `breakup` | `breakup(session_seq: int, reason: str = "", cooldown_days: int = 7) -> dict[str, Any] \| None` | 结束并写冷却截止 |
| `cooldown_state` | `cooldown_state(user_id: int, session_seq: int = 1) -> dict[str, Any]` | `{"active","until","days_left"}` |
| `can_confess` | `can_confess(user_id: int, session_seq: int = 1) -> tuple[bool, str, int]` | `(可不可以, 原因, 剩余天数)` |
| `is_blocked` | `is_blocked(user_id: int) -> bool` | 是否拉黑 |
| `block_user` | `block_user(user_id: int, reason: str = "aversion") -> dict[str, Any]` | 只拉黑这个人，同时把厌恶写 100 |
| `unblock_user` | `unblock_user(user_id: int) -> dict[str, Any]` | 解除拉黑 |
| `purge_self` | `purge_self(openid: str) -> int` | 把她自己的小号从群成员里删掉 |
| `forget_user` | `forget_user(user_id: int) -> dict[str, Any]` | 针对性失忆：关系/记忆/印象/观察/私密记录清空，身份保留 |

**恋人机制的真实语义**：

| 说法 | 代码事实 |
|---|---|
| 门槛 | `THRESHOLD = 80.0`，与阶段表「恋人 80」对齐；`config.yaml` 另有 `relationship.lover.threshold: 80`，但 `bind_lover` 用的是模块常量，不读配置 |
| `hit_100_at` | 名字叫到 100，实际在 `new_aff >= 80` 时第一次写入，用于「谁最早够格」排序，不阻断任何判断 |
| 唯一性 | `current_lover()` 查 `session_seq=? AND status='active'`，`bind_lover()` 发现已有 active 行就抛「已经有恋人了」 |
| 分手释放 | `breakup()` 把该行改成 `status='ended'` 并写 `ended_at` / `end_reason` / `cooldown_until` |
| 冷却 | 默认 `cooldown_days=7`，`cooldown_until = 现在 + 7 天`；`cooldown_state()` 取该 user 最近一条带 `cooldown_until` 的行，`days_left = max(1, int(剩余秒数 // 86400) + 1)` |
| 能否接受 | `can_confess()` 判定顺序：恋人位被别人占 → 「已经有恋人了」；自己在冷却 → 「冷却中」+ 天数；好感不足 80 → 「好感还没到」；否则放行 |

**关键实现（`update_relation`）**：先 `relation_of()` 保证行存在，四个字段各自 `max(0.0, min(100.0, ...))`；`new_aff >= THRESHOLD and not hit_100_at` 时把 `hit_100_at` 记成当前时间；一条 UPDATE 同时写四维 + `stage_for(new_aff)` + `hit_100_at` + `updated_at`，最后回读返回。

**关键实现（`forget_user`）**：先按 `lover_of(uid)` 结束这一条恋人（`end_reason='forgotten'`，`cooldown_until=NULL`，即遗忘不掉冷却），再依次删 `events`、走 `h2.forget_user()` 软删记忆、删 `member_impression` / `human_notes` / `intimate_context` / `intimate_profile` / `user_relation`；`qq_user` 那行保留并清 `blocked`；最后把 `intimate_mode`、`intimate_mode_ts`、`love_pending`、`proactive_last_ts`、`proactive_window`、`proactive_nag`、`proactive_angry` 七个 `:<uid>` 键和 `greeted:<openid>` 置空。

**关键实现（`purge_self`）**：先 `h2.forget_user()` 软删记忆（注释写明 `memories` 是只读视图，硬删会抛 `cannot modify memories because it is a view` 且是半途删除），失败再退化成 `UPDATE mem_item SET forgotten=1`；然后硬删 `user_relation` / `member_impression` / `human_notes` / `intimate_context` / `intimate_profile` / `lover` 六张表里该 uid 的行、删他的 `events`、删 `qq_user` 行。

**坑**：`forget_user()` 用 `sql.split()[2]` 当计数字典的键，`DELETE FROM events WHERE...` 的 `split()[2]` 是 `events`，但记忆那条被覆盖成 `counts["memories"]`；`forget_user()` 不删 `friend_request`，删好友后申请记录仍在；`purge_self()` 与 `forget_user()` 是两套相似但不相同的清理逻辑；`user_relation` 与 `lover` 都写了 `REFERENCES qq_user(id)`，但连接从不执行 `PRAGMA foreign_keys=ON`（`db.py` 只设了 `journal_mode=WAL` 与 `busy_timeout=15000`），删 `qq_user` 不会级联——本地 `data/life.db` 实测就有 `lover.user_id=2` 而 `qq_user` 只剩 `id=1` 的悬空行，`lover_of(2)` 照样会查到它。

### 3.5 `app/relation/session.py`

**职责**：会话生命周期。模块 docstring 已经写明这里有**三件不可逆的事**：名字锁定、厌恶到 100 主动结束、结束后清除关于这个用户的记忆。所以所有破坏性动作先归档。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `ARCHIVE_DIR` | `pathlib.Path("data/archives")` | 归档目录（相对 CWD） |
| `NAME_STEPS` | `{"idle": 0, "asked": 1, "pending": 2, "locked": 3}` | 命名四态（代码实际用整数，此表未被引用） |
| `ensure` / `get` | `ensure(default_name: str) -> dict[str, Any]` | 建/取 `session` 单行 |
| `her_name` | `her_name(default_name: str) -> str` | 当前名字 |
| `set_pending` | `set_pending(name: str) -> None` | 写候选名 |
| `confirm_name` | `confirm_name() -> str` | 落定：写 `her_name`、`name_locked=1`、清 `name_pending` |
| `extract_name` | `extract_name(text: str) -> str` | 从自然语句里挑名字，认不出返回空串 |
| `is_answer_word` | `is_answer_word(text: str) -> bool` | 确认/拒绝/寒暄词，永远不当名字 |
| `looks_like_name` | `looks_like_name(text: str) -> bool` | 裸名字判定（2–4 字、无功能词、无标点） |
| `wants_naming` | `wants_naming(text: str) -> bool` | 是否「要给她改名」 |
| `is_affirm` / `is_decline` | `is_affirm(text: str) -> bool` | 肯定 / 否定判定 |
| `get_aversion` / `set_aversion` / `adjust_aversion` | `adjust_aversion(delta: float) -> float` | 全局厌恶读写（夹 0–100） |
| `aversion_phase` | `aversion_phase(value: float) -> str` | `ended`(≥100) / `cold`(≥85) / `irritated`(≥60) / `uneasy`(≥30) / `calm` |
| `archive` | `archive(reason: str = "aversion_limit") -> pathlib.Path` | 写 `data/archives/session-<seq 三位>-<时间戳>.json` |
| `terminate` | `terminate(persona, reason: str = "aversion_limit") -> dict[str, Any]` | 归档 → 清记忆 → 换人 |
| `finale` | `finale(persona, old_name: str) -> list[str]` | 分手台词，取 `persona.raw["end"]["finale"]`，空则用三句兜底 |
| `session_payload` | `session_payload(persona) -> dict[str, Any]` | 给前端/prompt 的会话快照 |

**命名流程（由 `loop._naming_turn()` 驱动，`session.py` 只提供判定）**：

1. `_NAME_PATTERNS` 六条正则依次试：`叫你(?:做)?X`、`你就叫X`、`名字(?:就)?(?:叫|是)X`、`起(?:个|一个)?名[字]?[，,\s]*(?:就)?叫X`、`^(?:就叫|叫)X$`、`^(?:就叫|叫)X[\s。.！!？?，,…]*$`（最后一条是「就叫小满。」带句号被坑过之后补的）。
2. 候选剥尾巴（`好了/吧/呗/啊/呀/吗/的/就行/就好/算了/可以/嗯`）、剥引号。
3. `_plausible_name()` 把关：长度 2–6、不含 `？?。，,！!…、\n\t ` 任一字符、不含 `_NAME_BAD_WORDS`（`这/那/什么/名字/数字/以后/一个/这句/那串/这串/刚才/一直/这么/那么`）。
4. 不满足再试 `looks_like_name()` 裸名字路径：2–4 字、不含 `？?。，,！!…`、不含 `_BAD_NAME_CHARS`（`在吗什么怎么干嘛你我他她它的了是有没不呢吧啊呀嘛哦嗯`）、不在 `_COMMON`。
5. `wants_naming()` 先用两个否决：「叫我」出现且「叫你」没出现 → 不是给她起名（他在给自己起名）；否则要含 `_NAMING_INTENT` 十个词之一（`起个名/起个名字/起名/取个名/改个名/改名/以后就叫你/以后叫你/给你起/帮你起`）。

**厌恶与终止**：`terminate()` 顺序是 ① `archive()` 写全量档案 → ② 一个事务里 `DELETE events` / `DELETE relation_log` / `DELETE traits_log`、`relation` 四维归零、`session.status='ended'` 并写 `ended_at` / `end_reason` → ③ `h2.forget_all()` 软删记忆 → ④ 从 `_NAME_POOL`（12 个「小X」）里随机取一个不等于旧名的，`seq + 1`、`name_locked=1`、`status='active'`、清 `ended_at` / `end_reason`。

**档案内容（`archive()` 的 payload）**：`archived_at`、`reason`、`session`（整行）、`relation`（整行）、`traits`（全部）、`events`（全部）、`memories`（只读视图全量）、`relation_log`（全部）、`traits_log`（全部），再加 `persona_snapshot`（`transfer.snapshot_for_archive()` 存人设 YAML 原文），失败就跳过这一项。

**耦合**：被 `loop._naming_turn()` / `loop._end_session()` / `routes.py`（`/v1/session/new`、`/v1/dev/state/set`、`/v1/dev/relation/reset`）调用；内部调 `db`、`h2`、`persona.transfer`。

**坑**：`ARCHIVE_DIR` 是相对路径 `data/archives`（跟 CWD 走），而 `finale.py` 用 `DATA_ROOT / "Girl-memory"`（跟程序位置走），打包成 exe 后两份归档落在两个不同的根目录；`confirm_name()` 里硬编码了兜底名「小涵」；`_AFFIRM` 含单字「是」「好」「对」，`is_affirm("是")` 为真；`NAME_STEPS` 是没人引用的死常量。

### 3.6 `app/relation/love.py`

**职责**：把「表白 / 分手 / 答应她的主动提议」接成**确定性回复**——命中就不走模型，直接返回台词与 state。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `CONFESS_WORDS` / `BREAKUP_WORDS` / `AGREE_WORDS` | 元组 | 表白 9 词、分手 6 词、同意 8 词 |
| `family_role` | `family_role(user_id: int = 0, openid: str = "", nickname: str = "") -> str` | 返回「生父」/「干爹」/空 |
| `try_turn` | `try_turn(rt, persona, text: str, user_id: int) -> dict[str, Any] \| None` | `None` = 这轮不是恋爱事件 |
| `maybe_set_pending` | `maybe_set_pending(persona, user_id: int) -> str` | 到门槛且无恋人时挂 `love_pending`，返回给模型的暗示句 |

**确定性路径表**：

| 条件 | action | 她说的话 |
|---|---|---|
| 家人身份 + 表白 | `rejected_family` | 「……你是我爹啊，说什么呢。」「这种话别乱讲。」 |
| 已是这个人的恋人 + 表白 | `lover_again` | 「我也爱你。」「别一直说……我会当真的。」 |
| 恋人位被别人占 | `rejected_taken` | 「我现在心里只有一个人了。」「你别往下说了。」 |
| 自己在冷却 | `rejected_cooldown` | 「我最近不想谈这个。」「……再给我 N 天吧。」 |
| 好感不足 80 | `rejected_early` | 「……我们还不到那一步。」「别急。」 |
| 通过全部判定 | `bound` | `persona.anchors(["被撒娇"], 2)` 抽两句，抽不到用「……嗯。」 |
| 提分手且确有恋人 | `breakup` | 「好。」「那就到这儿吧。」（`cooldown_days` 取 `rt.cfg.relationship.lover.cooldown_days`，无 cfg 时 7） |
| 提分手但没恋人 | `breakup_none` | 「我们又没在一起。」「……你在说什么。」 |
| `love_pending` 已挂 + 是肯定回答 + 可接受 + 无恋人 | `bound_active` | 「那我们就算在一起了。」「你别后悔。」 |

`_result()` 统一塞 `meta.delays_ms = [700 + len(x) * 30 for x in reply]`，`state` 由 `_state()` 现算（四维 + `stage_payload` + `session_payload` + 全 0 的 `delta`）。

**`_is_agreement()` 逐条判定（全部通过才算同意）**：

1. 去空白后非空，且长度 `<= 16`。
2. 不以 `?` / `？` 结尾，且不匹配 `_QUESTION_TAIL = [?？]\s*$|(吗|呢|么)\s*[?？…。!！]*\s*$`。
3. 不含 `_QUESTION_HINTS` 任一（`愿不愿/要不要/可不可以/会不会/是不是/能不能/对不对/好不好/行不行/想不想/答不答应/做不做`）。
4. 不含 `_NEGATIONS` 任一（`不/没/别/算了/拒绝/拉倒/再说吧`）。
5. 不匹配 `_PRAISE_ONLY = (挺好|很好|不错|还好|好点|好感|好意思|好听|好看)`。
6. 必须以同意词开头（允许「我」前缀）：`^(?:我)?(?:好|嗯|行|可以|愿意|同意|答应|没问题|就这个|确定|成)`。
7. 且含 `AGREE_WORDS` 任一，或以 `没问题` / `就这个` / `确定` 开头。

**`_is_confession()` 的防误判**：先看 `_STRONG_CONFESS`（`我们在一起|在一起吧|做我女朋友|当我女朋友|表个?白|想和你谈`）命中即真；再看 `_CONFESS_LEAD`（`跟你说个事`/`跟你讲个事`）命中即假；再看 `_COMPLIMENT_ONLY`（「喜欢你现在的样子」这类夸奖）命中即假；最后才回落到 `CONFESS_WORDS` 子串匹配。

**家人判定**：靠配置 `family.roles`（`openid` / `role` / `aliases`），面板「设置 → 连接 → 家人」可增删，落 `config.local.yaml`；运行时还支持 kv `family_roles` 覆盖。解析优先级：kv → 配置 → 别名/角色名命中 → 用 `user_id` 反查 `qq_user` 再递归一次（清库重建后身份不丢）。**代码里不再有硬编码 QQ**（旧 `FAMILY_QQ` 已删）。

**为什么回复必须显式落库**：这些分支是 `loop.chat_turn()` 里 `return` 掉的确定性回复，早期直接返回导致「用户收到了，库里没有」。`loop._store_her_script()` 会把 `result["reply"]` 拼成一段写进 `events`（带 `user_id` / `chat_type` / `group_id`），并用 `meta.stored` 防重复写入。不写这一笔，下一轮上下文里就没有「她刚拒绝过他」，模型会自相矛盾。

**坑**：`try_turn()` 里所有调用都写死 `session_seq=1`（`bind_lover(1, user_id)`、`breakup(1, ...)`、`lover_of(user_id)` 默认值），而 `session.terminate()` 会把 `session.seq` 加一——换人之后 `lover` 表里 `session_seq=1` 的 active 行还在，新会话仍会被判定「已经有恋人」；`FATHER_ALIASES` 定义了但没有任何引用；`maybe_set_pending()` 只在好感 ≥ 80 时挂标记，挂上后不会因好感回落自动撤销。

### 3.7 `app/relation/finale.py`

**职责**：她走之后留下的东西——一封给他的信、一份给开发者的自述、一份可恢复的全量档案。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `GIRL_DIR` | `DATA_ROOT / "Girl-memory"` | 档案根目录 |
| `compose` | `compose(rt, archive_path: pathlib.Path) -> dict[str, Any]`（`async`） | 生成信 + 自述 + 三个文件 + 桌面副本 |
| `status` | `status() -> dict[str, Any]` | 读 `kv` 里的信状态 |
| `clear_status` | `clear_status() -> None` | 清六个 `letter_*` 键 |

**关键实现（`compose`）**：

1. 读入 `archive.json`（由 `session.archive()` 写的那个文件），取 `seq`、`her_name`、`_stats()`、`_conflict_summary()`。
2. 非 mock 时生成信：`_sanitize_facts()` 从 `memories` 里挑最多 6 条（跳过空串、含 `程序/AI/模型/测试/设定/系统/人工智能`、以及含 `{` 的模板残留），拼成「【可以引用的事实】冲突：…\n- …」。
3. 用 `_LETTER_SYSTEM` 调模型（`max_tokens=500`，`models.pick("letter")`）。
4. **穿帮检查**：`_reveals()` 扫 18 个词（`不是人/不是你想找的人/不像人/我不存在/不是真实/没有影子/记忆是假/剧本/代码/程序/设定/模型/AI/人工智能/演员/扮演/被创造/造出来`），命中就带上「上一次写坏了」重写一次；再命中就把信清空走兜底模板。
5. 用 `_REFLECT_SYSTEM` 生成 200 字自述（`max_tokens=400`）。
6. 兜底：`_FALLBACK_LETTER`（含 `{name}` / `{conflict}`）与 `_FALLBACK_REFLECT`（含 `{lesson}` / `{conflict}`），`provider` 标 `template`。
7. 写三个文件到 `Girl-memory/session-<seq 三位>-<YYYYmmdd-HHMM>-<旧名>/`：`archive.json`（原档案）、`离开记录.md`（标题「她走了」+ 会话序号/起止/句数/记忆数/三轴 + 三节正文）、`给你的信.txt`（只有信）。
8. 桌面副本：`Path.home()/"Desktop"/"她留给你的信-<旧名>.txt"`，失败吞异常并留空字符串。
9. 写 `kv`：`letter_status="ready"`、`letter_desktop`、`letter_path`（目录）、`letter_text`、`letter_name`、`letter_provider`。

**`_conflict_summary()` 的人话映射**（把内部流水理由翻成能写进信里的话）：

| 流水理由含 | 信里的话 |
|---|---|
| `攻击` / `骂` / `脏` / `废物` / `滚` | 「你骂过我，我也没让着你」 |
| `试探` / `程序` / `测试` / `模型` | 「你一直不太信我」 |
| `比较` | 「你拿我跟别人比过」 |
| `冲突` / `发脾气` / `吵` | 「我们吵过几次，谁也不肯先低头」 |
| 都不含 | 「最后那阵子，我们说话越来越短。」 |

只取前两条（`dict.fromkeys` 去重后 `bits[:2]`），用「，」连成一句。

**坑**：`compose()` 是异步尾巴——`loop._end_session()` 先写台词、`session.terminate()` 换人、把 `letter_status` 置 `"pending"` 并记下 `letter_archive`，再 `asyncio.create_task(finale.compose())`；而 `GET /v1/farewell` 看到 `status == "pending"` 且档案文件存在时会**当场再生成一次**，两个入口都能触发，后写的那次会覆盖 `离开记录.md`、`给你的信.txt` 与桌面副本。桌面副本只在 `Path.home() / "Desktop"` 存在时才写，桌面被 OneDrive 重定向、该目录不存在的机器上直接跳过，`letter_desktop` 记空串。

### 3.8 `app/relation/friends.py`

**职责**：好友申请的唯一事实落点。OneBot 没有「主动加好友」接口，所以这里只做「记下来 + 能通过就通过」。

| 符号 | 签名 | 作用 |
|---|---|---|
| `PENDING` / `ACCEPTED` / `REJECTED` / `FAILED` | `"pending"` / `"accepted"` / `"rejected"` / `"failed"` | 四态字符串 |
| `record` | `record(flag: str, openid: str, nickname: str = "", comment: str = "", source: str = "onebot", seq: int = 0) -> int` | 建/更新一行，返回行 id |
| `mark` | `mark(request_id: int, status: str, note: str = "") -> bool` | 改状态，返回是否命中行 |
| `get` / `by_flag` | `get(request_id: int) -> dict[str, Any] \| None` | 按 id / flag 查 |
| `listing` | `listing(status: str = "", limit: int = 50) -> list[dict[str, Any]]` | 列表 |
| `pending_count` | `pending_count() -> int` | 待处理数 |

触发链路：`onebot.py` 收到 `post_type=request` 且 `request_type=friend` → `friends.record()` → 若 `onebot.auto_accept_friend_request`（默认 `True`）为真则立刻 `accept_request()`：回 `set_friend_add_request(flag, approve)`，NapCat 确认才 `mark(ACCEPTED/REJECTED)` 并 `users.ensure_user(openid, nickname)`；没确认就 `mark(FAILED, "OneBot 没确认…")` 留着重试。控制台「添加好友」只走 `users.ensure_user()`，是**本地登记**，不动 QQ 好友列表。

### 3.9 `app/relation/greet.py`

**职责**：主动招呼还没认识的人。

| 符号 | 签名 | 作用 |
|---|---|---|
| `LINE_POOL` | 4 条问名字的话术 | 随机取一句 |
| `line` | `line() -> str` | 随机一句 |
| `already_greeted` | `already_greeted(openid: str) -> bool` | 读 `kv["greeted:<openid>"] == "1"` |
| `mark_greeted` | `mark_greeted(openid: str) -> None` | 写该键为 `"1"` |
| `family_skip` | `family_skip(openid: str) -> bool` | `love.family_role(0, openid=openid)` 非空即跳过 |
| `greeting_enabled` | `greeting_enabled() -> bool` | `kv["testing_reply"] != "1"` |

两个入口的条件差异：

| 入口 | 触发条件 | 落库 |
|---|---|---|
| 自动（`onebot.py`） | `message_type == "group"` 且这个人 `_known_before` 为假、`greeting_enabled()`、非家人、未被拉黑、`already_greeted` 为假 | 发 `send_group_msg`（@ + 话术）→ `mark_greeted` → `store.add_event("her", 话术, meta={"greet": True})` |
| 手动（`console.py`） | 控制台「群招呼」拉群成员列表，只处理不在 `qq_user` 里的生面孔，`limit` 默认 3 人 | 同上，`source="console"`；发送失败直接 502，不标记 |

### 3.10 `app/relation/rituals.py`

**职责**：给 prompt 一句轻提示，不硬塞。

| 符号 | 签名 | 作用 |
|---|---|---|
| `hint` | `hint(user_id: int, hour: int, chat_type: str) -> str` | 拼 0–3 行提示 |
| `confirm` | `confirm(user_id: int, reply_text: str) -> None` | 她真说了才标记 |

`kv` 键形如 `ritual:<kind>:<uid>:<YYYY-MM-DD>`，`kind` 取 `goodnight` / `morning` / `day7` / `day30` / `day100`。

1. `hour >= 22 或 hour <= 2` 且当天 `goodnight` 未标记 → 追加「【今天的收尾】…」。
2. `6 <= hour <= 9` 且当天 `morning` 未标记 → 追加「【早上的话】…」。
3. 查 `qq_user.first_seen` 算认识天数，落在 `(7, 30, 100)` 且对应键未标记 → 追加「【认识第 N 天】…」。
4. `confirm()` 只看她的回复文本：含「晚安」标记 `goodnight`，含「早安」或「早上好」标记 `morning`。

**坑**：`hint()` 收了 `chat_type` 却没用（群聊和私聊给同样提示）；`day7` / `day30` / `day100` 三个键没有任何写入点（`confirm()` 只写 `goodnight` 与 `morning`），所以「认识第 N 天」那句提示会在当天的每一轮里重复注入；`uid <= 0` 直接返回空串，面板线没有仪式提示。

## 4. 数据流

```text
用户说一句话
  └─ loop.chat_turn(text, user_id, chat_type)
        ├─ relation = engine.relation_for(uid)        # uid>0 → user_relation；uid=0 → relation
        ├─ phase    = session.aversion_phase(aversion)
        ├─ 命名未锁 → loop._naming_turn() → session.extract_name/wants_naming/confirm_name
        ├─ love.try_turn() 命中 → 确定性台词 → loop._store_her_script() → return（不调模型）
        ├─ love.maybe_set_pending() → kv["love_pending:<uid>"]
        ├─ 模型生成回复
        ├─ appraise.appraise(text, hour)                # 规则 40%
        ├─ judge.judge(...) → appraisal["llm"]           # 模型 60%（mock 时 None）
        └─ settlement = engine.apply_appraisal(appr, persona, "chat", uid)
              ├─ 融合 + 限幅 + 日上限 + 厌恶 + 人格系数
              ├─ 写 relation 或 user_relation + relation_log
              └─ 返回 stage_payload（阶段/称呼/下一阶段）
                    └─ aversion ≥ 100 → uid>0 拉黑他一个；uid=0 → _end_session()
                          └─ session.terminate() → archive() → h2.forget_all() → 换名重开
                                └─ asyncio.create_task(finale.compose()) → Girl-memory/
```

## 5. 改这一层要注意

1. **加一个关系标签**：`engine.TAG_WEIGHTS` 加一行 + `appraise.py` 加词表 + `judge._SYSTEM` 的 `tags` 枚举里加名字。三处漏一处，模型给出的新标签会因为 `TAG_WEIGHTS.get(tag, (0,0,0))` 静默变成零权重。
2. **改阶段阈值**：`persona/*.yaml` 的 `stages[].at` 与 `users.STAGE_AT` 必须同时改，否则面板显示「恋人」而 `user_relation.stage` 写的是 `samefreq`。阈值还牵动 `love.THRESHOLD`（80）与 `config.relationship.lover.threshold`（80）。
3. **改 `lover` 表**：会牵动 `users.current_lover/lover_of/bind_lover/breakup/cooldown_state/can_confess`、`love.try_turn` 的九个分支（`rejected_family` / `lover_again` / `rejected_taken` / `rejected_cooldown` / `rejected_early` / `bound` / `breakup` / `breakup_none` / `bound_active`）、`app/intimacy/gate.is_lover_of()`（它决定能不能进 EXPLICIT）、`app/life/proactive.py:523`（恋人主动调情）。
4. **加一张会写 `user_id` 的表**：`users.forget_user()` 与 `console.delete_friend()` 是两个独立的手写清理清单，都要加；`purge_self()` 里还有第三份六表清单。
5. **动 `session.terminate()`**：它必须排在 `finale.compose()` 之前完成归档，且必须调 `h2.forget_all()` 而不是删 `memories` 视图。`loop._end_session()` 顺序是「说完台词 → 归档换人 → 后台写信」，不能提前到台词之前。
6. **动确定性回复**：任何在 `loop.chat_turn()` 里 `return` 掉的分支都必须过 `_store_her_script()`，否则下一轮上下文缺她自己那句。

## 6. 交叉引用

- 相关：docs/17（`relation` / `user_relation` / `lover` / `qq_user` / `session` / `friend_request` / `relation_log` / `traits` 的建表语句与迁移补列）
- 相关：docs/09（`app/intimacy/*` 用 `lover_of()` 决定能否进 EXPLICIT，用 `is_blocked()` 短路整条门控）
- 相关：docs/05（`loop.chat_turn()` 是唯一调用 `apply_appraisal()` 的地方；`_naming_turn` / `_end_session` / `_store_her_script` 都在那里）
- 相关：docs/19（`persona/*.yaml` 的 `stages` 表与 `users.STAGE_AT` 是同一套阈值；`traits` 的敏感/自持喂给厌恶的人格系数）
- 相关：`app/relation/users.py` 的 docstring 写着「设计见 docs/20」——那是旧文档编号，对应的内容在本次重写里归入 QQ 群多用户相关章节；本层的表与规则以本文为准
- 相关：docs/04（`continuity.record()` 在 `app/core/continuity.py`；本层触发的记录见下表）

本层触发的 `continuity.record()` 调用点：

| 调用点 | kind | 时机 |
|---|---|---|
| `api/routes.py:347` | `session_reset` | `/v1/dev/session/clear` 清会话 |
| `api/routes.py:428` | `memory_forgotten` | `/v1/dev/memory/clear` |
| `api/routes.py:441` | `relation_reset` | `/v1/dev/relation/reset` |
| `api/routes.py:643` | `model_changed` | 换 provider/model |
| `api/routes.py:1025` | `persona_changed` | 换人设文件 |
| `api/console.py:93` | `memory_forgotten` | 控制台清某人记忆 |
| `api/console.py:388` | `person_forgotten` | 控制台删好友 |
| `persona/transfer.py:136` | `soul_imported` | 导入整份灵魂档案 |

注意：`session.terminate()`（厌恶到顶或开发者强制换人）**不写** `continuity_log`，它只写 `data/archives/*.json`；`love.py` 的绑定/分手也不写。

## 变更记录（2026-11-01）

- **日上限 `caps` 三维都钳**：`apply_appraisal()` 里信任/吸引不再绕过 `_pos/_neg`，三维共用同一份每日预算，`capped` 也按三维修。默认 `caps.enabled=false`（不配就是原行为，只在 config.local.yaml 里开了才生效）。
- **恋人位按会话序号记账**：`love.try_turn()` 不再硬编码 `session_seq=1`，改用 `session.current_seq()`；`session.terminate()` 会把旧 seq 下 `status='active'` 的 `lover` 行一起结掉。换人之后不会再出现"她还在跟上一任谈"的幽灵恋人位。
- **关系流水带 `user_id`**：`relation_log` 补了 `user_id` 列（`_migrate` 自动给老库 `ALTER TABLE`），`engine.log()` 写入归属；`finale._conflict_summary(payload, uid)` 只汇"这封信收件人"的账，多用户时不再把别人的辱骂写进来。`session.archive()` 的档案里也带上 `lover` 表，用来判断收件人。

- **恋人门槛读配置**：`config.relationship.lover.threshold` 现在真的被读（`users.threshold()`，回落常量 80）；`bind_lover` / `can_confess` / `love.try_turn` / `qa.oracle` 全走它。以前改配置等于白改。