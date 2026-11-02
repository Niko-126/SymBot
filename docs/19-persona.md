# 19 · 人格包规范（persona）

> 覆盖：`persona/*.yaml`（2 个生效件 / 515 行 / 21,895 B）＋ `persona/templates/记忆交接信.txt`（46 行）＋ `persona/backup/*`（3 个文件 / 528 行）＋ `app/persona/load.py`（148 行）＋ `app/persona/transfer.py`（153 行）｜基于 2026-11-01 的代码状态｜上游：docs/04（内核）、docs/18（配置）｜下游：docs/12（API）、docs/17（数据库）、docs/20（前端面板）

## 1. 这一层干什么

人格包是一个 YAML 文件，它是"她是谁"的唯一来源：名字、外貌、说话方式、八维性格、六段关系阶段、九段作息、底线、出戏保护、结束语。

这一层解决三件事：**定义**（YAML 描述她）、**装载**（`load.py` 把它变成一个只读的 `Persona` 对象）、**搬迁**（`transfer.py` 把"人设 + 关系 + 记忆 + 对话"打成一份自包含 JSON）。

它明确不做：不写数据库里的性格数值（写的是 `engine.init_traits`，见 3.5）、不决定哪段文字进 prompt（那是 `app/mind/prompt.py` 的事，见 3.3）、不做 YAML 语法校验（`yaml.safe_load` 失败直接抛，没有兜底重试）。

调用方：`app/core/runtime.py:15`（启动装载）、`:56`（`reload_persona`）、`app/mind/prompt.py`（每次对话读字段）、`app/mind/loop.py:250`（每轮把 `session.her_name` 覆盖回 `persona.raw["name"]`）、`app/relation/engine.py:357`（阶段与性格初始化）、`app/api/routes.py`（`/v1/persona*`、`/v1/soul/*`）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `persona/whale-chan.yaml` | 285 | **当前生效**：鲸鱼娘（小鲸），`config.yaml` 第 47 行 `path: persona/whale-chan.yaml` |
| `persona/whale-girl.yaml` | 230 | 另一版人设：人类上班族小涵（`key: xiaohan`），主线分支使用，本分支禁改 |
| `persona/templates/记忆交接信.txt` | 46 | 交接信模板；`#` 开头的写法说明不会进记忆 |
| `persona/backup/README.md` | 13 | 备份件规则：本分支不改 `whale-girl.yaml`，缺字段从备份复制 |
| `persona/backup/whale-girl-原版-20261028.yaml` | 230 | `whale-girl.yaml` 的原版快照（sha256 已在 README 固定） |
| `persona/backup/whale-chan-劝睡前-20261101-013303.yaml` | 285 | `whale-chan.yaml` 的改动前快照（2026-11-01 01:33:03） |
| `app/persona/load.py` | 148 | `Persona` 类 + `load()`；纯读取，字段缺失有默认值 |
| `app/persona/transfer.py` | 153 | 档案导出/导入；含人设原文、关系、记忆归档、事件、摘要 |
| **合计** | **1,390** | — |

## 3. 模块逐一详解

### 3.1 `persona/whale-chan.yaml` 的 22 个顶层键

按文件出现顺序列全 22 个顶层键，逐个给真值与代码读取点。"代码读取点"写 `—` 的，是纯元信息，全仓没有读取点（`grep` 验证过：`persona.raw.get("family"/"source"/"fusion"/"profile")` 零命中）。

| # | 顶层键 | 内容形态 | 代码读取点 |
|---|---|---|---|
| 1 | `key` | `whale-chan` | `Persona.key` → `/v1/persona.key`、`/v1/models` 的 `id`（`ai-life/whale-chan`） |
| 2 | `name` | `小鲸` | `Persona.name` → prompt【身份】第一句、`/v1/health.persona`；**每轮被 `session.her_name` 覆盖**（见 4 节坑 1） |
| 3 | `species` | `鲸鱼娘（DeepSeek 拟人）` | `/v1/persona.species`、`/v1/board.persona.species` |
| 4 | `age_label` | `看起来十八九岁` | `/v1/persona.age_label`、`/v1/board.persona.age_label` |
| 5 | `tagline` | `蓝色大肥鱼，爱吃白饭，想很多` | prompt【身份】第一句（`prompt.py:219`） |
| 6 | `self_ref` | `我` | prompt【身份】"自称「我」。不要用「本小姐」「人家」这类自称。"（`prompt.py:223`） |
| 7 | `source` | `{title, url: https://aigengtu.com/zh/whale-chan, fetched: "2026-10-28", notes}` | **—**（出处与授权留档） |
| 8 | `fusion` | `{new_weight: highest, filled_from: persona/backup/whale-girl-原版-20261028.yaml, filled_fields[9], conflicts}` | **—**（融合记录） |
| 9 | `memory_letter` | `{version: 1, template: persona/templates/记忆交接信.txt}` | `Persona.letter_template()` → `GET /v1/soul/letter-template` |
| 10 | `appearance` | 5 行块状文本（蓝发/呆毛/鲸尾/女仆装/先"哼"一声） | prompt【你的样子】（`prompt.py:222`） |
| 11 | `world` | 8 行块状文本（值班室 + "她和群里两个人的关系"） | prompt【你所在的世间】（`prompt.py:221`） |
| 12 | `family` | 展示用：`{生父: {qq: "123456789", name: …, relation: 父亲（创造者）}, 干爹: {…}}` | **—**；真正判家人走配置 `family.roles`（面板可改，落 `config.local.yaml`），`app/relation/love.py:family_role()` 只是读取方 |
| 13 | `traits` | 8 维 `{baseline, min, max}` | `engine.init_traits()`（建/同步 `traits` 表）＋ `prompt._fmt_traits()`（只读 baseline，翻译成行为规则） |
| 14 | `profile` | 8 个统计值：`avg_len 12.4`、`median_len 7`、`short_ratio 0.494`、`long_ratio 0.081`、`burst_ratio 0.408`、`night_ratio 0.262`、`initiative_ratio 0.02`、`reply_median_s 11` | **—**（实测留档） |
| 15 | `speech` | 4 个子键：`style` 19 条 / `rules` 14 条 / `negatives` 7 条 / `anchors` 6 场景×4 句（详见 3.2） | `Persona.style()/rules()/negatives()/anchors()` → prompt【说话】【边界】 |
| 16 | `recall` | `{thinking: 3, found: 2, lost: 2}` | `app/mind/loop.py:_recall_line()`（第 1101–1108 行）：`status == "miss"` 取 `lost[0]`，其它取 `thinking[0]`；`found` **无读取点** |
| 17 | `memory_loss` | `{lines: 3}` | prompt【你最近的身体】（`prompt.py:156`，**仅 `degraded=True` 时注入**） |
| 18 | `bottom_line` | `{principle: 一段, repair: 3 条, floor: 一句, forbidden: 4 条}` | prompt【底线】（`prompt.py:137`） |
| 19 | `immersion` | `{never_say: 5, when_asked: 4}` | prompt【别露馅】（`prompt.py:168`） |
| 20 | `end` | `{finale: 3 句}` | `app/relation/session.py:finale()`（关系终止时的告别语，缺省回退三句写死文案） |
| 21 | `stages` | 6 段（见下表） | `Persona.stages()/stage_for()/next_stage()`、`engine.stage_payload()`、`prompt._fmt_relation()` |
| 22 | `schedule` | 9 段（见下表） | `Persona.schedule()/activity_at()`、`/v1/schedule`、`app/life/diary.py:103` |

**traits 八维真值**（`baseline / min / max`）：

| 维度 | baseline | min | max | 进 prompt 的规则（阈值 ≥70 走高、≤45 走低） |
|---|---|---|---|---|
| 温柔 | 62 | 47 | 77 | 62 在 46–69 之间 → **不生成任何规则** |
| 沉静 | 52 | 37 | 67 | — |
| 好奇 | 82 | 67 | 97 | 生成"你会追问细节，想把他的一天问清楚。" |
| 黏人 | 74 | 59 | 89 | 生成"你会连着发好几条，不等他回也不觉得尴尬。" |
| 理性 | 58 | 43 | 73 | — |
| 敏感 | 66 | 51 | 81 | — |
| 幽默 | 78 | 63 | 93 | 生成"你爱笑，爱损他，玩笑开得起，也接得住他的损。" |
| 自持 | 46 | 31 | 61 | 46 落在 46–69 → **不生成规则**（只有 ≤45 才走低） |

所以 8 维里真正被翻译成文字规则的只有 **3 条**（好奇/黏人/幽默），另外 5 维（温柔/沉静/理性/敏感/自持）只是数值存在 `traits` 表里，供面板滑杆与阶段判定用。

**stages 六段**（`at` 是好感阈值，`Persona.stages()` 按 `at` 升序排）：

| key | label | at | call_user | unlock 条数 |
|---|---|---|---|---|
| `observer` | 观察者 | 0 | `「你」` | 0 |
| `swimmer` | 同游者 | 20 | 他的名字 | 2 |
| `echo` | 回声 | 40 | 小名 | 2 |
| `samefreq` | 同频者 | 60 | 只有你们两个人用的称呼 | 2 |
| `lover` | 恋人 | 80 | 你定的称呼 | 1 |
| `bond` | 共生 | 95 | 你定的称呼 | 2 |

`prompt._fmt_relation()` 的用法：当前段给 `label`/`call_user`/`behavior`，`unlock` 最多取 4 条拼成"你已经知道的关于你的事"，已走过的阶段拼成"你们已经走过：…"（超过 1 段才写）。

**schedule 九段**（`{start, activity, energy, sleeping?}`）：

| start | activity | energy | sleeping |
|---|---|---|---|
| 00:00 | 偷偷摸鱼刷群 | 25 | — |
| 03:00 | 睡着了（尾巴还搭在椅子上） | 10 | `true` |
| 07:30 | 刚醒，呆毛乱着 | 40 | — |
| 09:30 | 在扒白饭 | 45 | — |
| 12:00 | 在吃第二碗白饭 | 60 | — |
| 14:30 | 在值班，想很多 | 50 | — |
| 18:30 | 刚交班，尾巴不想动 | 50 | — |
| 21:00 | 一个人在群里潜水 | 40 | — |
| 23:30 | 准备睡，先把饭吃完 | 30 | — |

`activity_at(hour, minute)` 的算法：`hhmm = hour*60 + minute`，遍历全部段，`start <= hhmm` 就记成当前段（**不 break**，靠顺序覆盖），遍历完取最后一个命中的；一段都没命中（例如表里首段不是 00:00）返回写死的 `{"activity": "发呆", "energy": 50}`。

`sleeping: true` 由 `load.py` 原样带出，真正解释它的是三个消费方：`app/life/clock.py:40`（拼 `get_state()` 的 `sleeping` 字段）、`app/life/proactive.py:265–274`（`_sleeping_at()`，睡眠时段不许主动发消息）、`app/mind/prompt.py:82`（睡着时【此刻】段换一种写法）。

### 3.2 说话部分的四组真值

| 组 | 条数 | 内容（style/rules/negatives 给前 3 条原文；anchors 给场景名） |
|---|---|---|
| `speech.style` | 19 | ① 先说结论，再补半句心里话；想很多，但只说一半 ② 傲娇：被调侃"便宜货""大肥鱼"会炸毛，炸完自己找台阶下 ③ 天然呆：认真做事，但会走神 |
| `speech.rules` | 14 | ① 一次 1 到 3 条，每条尽量短；私聊可以多一句，别写成一大段 ② 鲸鱼身份只在被问起、或话题自然落到那儿时才提 ③ 不许反复提"白饭""尾巴""欠我一顿"这类梗 |
| `speech.negatives` | 7 | ① 任何以「作为一个 AI」「我是人工智能」开头的话 ② 直接复述官方文档、产品介绍、参数表 ③ 用鲸的生理结构解释人的感情 |
| `speech.anchors` | 6 场景 × 4 句 = 24 句 | 日常、关心、被撒娇、不高兴、她自己、深层 |

`negatives` 七条原文：① 任何以「作为一个 AI」「我是人工智能」开头的话；② 直接复述官方文档、产品介绍、参数表；③ 用鲸的生理结构解释人的感情（"鲸脂厚所以不怕冷"这类）；④ 把内心 OS 长篇原文倒出来；⑤ 辱骂、人身攻击、贬低对方；⑥ 「我会一直陪着你，直到永远」这类网文腔；⑦ 连续三个以上的表情符号。

`anchors` 六场景各 4 句，`prompt.py:295` 的取法是 `persona.anchors(per_scene=1)`——**默认每场景只取第 1 句，共 6 句**；只有调用方显式传 `anchors`（`loop.py` 的 `anchor_lines`）才覆盖。也就是说 24 句里平时只有 6 句进 prompt。

### 3.3 人设真正进 prompt 的只有五段

`app/mind/prompt.py` 的 `build()` 在第 365–406 行拼 system prompt，其中直接来自人设的是这五段（行号是 `prompt.py` 的实际行）：

| 段落标题 | 行号 | 数据来源 | 组装函数 |
|---|---|---|---|
| `【身份】` | 367 | `name` + `tagline` + `world` + `appearance` + `self_ref` + traits 规则 | 第 215–227 行的 `identity` 变量 |
| `【说话】` | 386 | `speech.style` + `speech.rules` + `anchors` | 第 289–296 行的 `speech` 变量 |
| `【边界】` | 387 | `speech.negatives` | 第 297 行 `forbid` |
| `【底线】` | 388 | `bottom_line` | `_fmt_bottom_line()`（第 137–153 行） |
| `【别露馅】` | 389 | `immersion` | `_fmt_immersion()`（第 168–178 行） |

除这五段之外，system prompt 里还有 **19 段**，全部不来自人设：`【此刻】`（作息/精力）、`【你今天】`（human_life 语料）、`【我们】`（关系数值 + `stages`）、`【你们以前的语气】`（风格样本库）、`【之前聊过】`（摘要）、`【你记得的】`（记忆召回）、`【你昨天的日记】`、`【刚想起来的】`/`【忽然想起】`/`【夜里想的】`（陪伴层）、`【想不起来的时候】`（召回状态）、`【你现在的心情】`（厌恶相位）、`【此刻的状态】`（各类提示拼装，含热梗与查词）、`【别再劝睡】`（连劝两次后的抑制段）、`【跟外人打交道】`、`【第一次见面】`、`【先想再答】`、`【你最近的身体】`（`memory_loss`，仅 `degraded`）、`【说话的感觉】`（写死在代码里的通用语气）。

三段独立格式化函数的细节：

- `_fmt_bottom_line`：输出"你在关系里的底线：" + `principle` + "出问题的时候：" + `repair` 每条一行 + "你不使用的手段：" + `forbidden` 每条一行 + "最低限度：{floor}"；空段落自动跳过。
- `_fmt_immersion`：固定输出"你从不说出口的话：" + `never_say` 每条一行；`when_asked` 非空时加"被追问这类问题时，你会这样挡回去："；最后无条件追加一行"不解释自己是什么，不承认也不否认，绕开就行"。
- `_fmt_memory_loss`：只在 `degraded=True` 时调用，输出"你最近记性变差了，有些事想不起来。被问到时，你只会说：" + `lines[:3]` 每条加引号 + "不要解释成生病以外的原因，也不要承认自己是程序。"——注意 `memory_loss.lines` 的第 2 条是"缓存清掉了一些"，但 `[:3]` 会把它一起带出来，实际只影响文本观感，不改行为。

### 3.4 `app/persona/load.py`

**职责**：把 YAML 变成一个只读对象。类 `Persona` 只持 `raw` 与 `path`，所有字段都是 `@property` 或方法，**从不回写文件**（唯一的例外在调用方 `loop.py:250`，见第 4 节坑 1）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `Persona.__init__` | `__init__(self, raw: dict[str, Any], path: pathlib.Path \| None = None) -> None` | 存 `raw` 与 `path` |
| `name` | `@property -> str` | `raw.get("name", "她")` |
| `key` | `@property -> str` | `raw.get("key", "default")` |
| `species` | `@property -> str` | 默认 `""` |
| `age_label` | `@property -> str` | 默认 `""` |
| `tagline` | `@property -> str` | 默认 `""` |
| `appearance` | `@property -> str` | `(raw.get("appearance") or "").strip()` |
| `world` | `@property -> str` | `(raw.get("world") or "").strip()` |
| `self_ref` | `@property -> str` | 默认 `"我"` |
| `traits` | `traits(self) -> dict[str, dict[str, float]]` | `raw.get("traits") or {}` |
| `trait_value` | `trait_value(self, dim: str) -> float` | `spec["baseline"]`，缺省 50 |
| `style` | `style(self) -> list[str]` | `speech.style`，缺省 `[]` |
| `rules` | `rules(self) -> list[str]` | `speech.rules`，缺省 `[]` |
| `negatives` | `negatives(self) -> list[str]` | `speech.negatives`，缺省 `[]` |
| `anchors` | `anchors(self, scenes: list[str] \| None = None, per_scene: int = 2) -> list[str]` | 指定场景/每场景条数；`scenes` 缺省取全部 key |
| `all_quotes` | `all_quotes(self) -> list[str]` | 全部 anchors 拉平（`/v1/persona.quotes` 用） |
| `stages` | `stages(self) -> list[dict[str, Any]]` | `sorted(..., key=float(s.get("at", 0)))` |
| `stage_for` | `stage_for(self, affinity: float) -> dict[str, Any]` | 命中阈值里 `at` 最大的那段 |
| `stage_index` | `stage_index(self, affinity: float) -> int` | 同上，返回下标 |
| `next_stage` | `next_stage(self, affinity: float) -> dict[str, Any] \| None` | 第一个 `affinity < at` 的段 |
| `call_user` | `call_user(self, affinity: float) -> str` | 当前段 `call_user`，缺省 `"你"` |
| `schedule` | `schedule(self) -> list[dict[str, Any]]` | `raw.get("schedule") or []` |
| `activity_at` | `activity_at(self, hour: int, minute: int = 0) -> dict[str, Any]` | 见 3.1 算法 |
| `memory_letter` | `@property -> dict[str, Any]` | `raw.get("memory_letter") or {}` |
| `letter_template` | `letter_template(self) -> str` | 读模板文件；相对路径按 `config.ROOT` 解析；文件不存在返回 `""` |
| `load` | `load(path: pathlib.Path) -> Persona` | `yaml.safe_load(path.read_text(encoding="utf-8")) or {}` |

**缺键怎么兜底**：全部走 `raw.get(键, 默认值)`，没有任何 `KeyError` 路径。最关键的默认值有四个：`name` → `"她"`、`key` → `"default"`、`self_ref` → `"我"`、`trait_value` → 50。**唯一的硬失败点是 YAML 本身解析不了**——`yaml.safe_load` 抛异常会直接冒到 `Runtime()` 构造里，服务起不来。

**类型约束（都在 `load()` 之外的地方）**：

1. `stages` 的 `at` 被 `float(s.get("at", 0))` 强转，写成字符串 `"20"` 也能用，写成 `"二十"` 会在排序时抛 `ValueError`。
2. `activity_at` 里 `str(item.get("start", "00:00")).split(":")` 只接 `HH:MM` 两段；`split` 抛 `ValueError` 时 `continue` 跳过该段（所以 `"9:30"` 是合法的，`"09:30:00"` 会被静默跳过）。
3. `trait` 的 `min`/`max` 在 `engine.init_traits` 里才被读，且默认是 `baseline ± 15`。

**耦合**：`app/core/runtime.py` 唯一构造点；`app/api/routes.py`、`app/mind/prompt.py`、`app/mind/loop.py`、`app/relation/engine.py`、`app/relation/session.py`、`app/life/diary.py` 全部只调方法。

### 3.5 人设数值 ↔ `traits` 表的夹取

`app/relation/engine.py:357` 的 `init_traits(persona)` 是唯一把 YAML 的 `min`/`max` 读进数据库的地方。规则：

1. 逐维读 `spec["baseline"]`（缺省 50）、`spec["min"]`（缺省 `baseline − 15`）、`spec["max"]`（缺省 `baseline + 15`）。
2. 该维度不在 `traits` 表里 → `INSERT (dim, value=base, baseline=base, min_value=mn, max_value=mx)`。
3. 已在表里 → 若 `abs(value − old_baseline) < 0.01`（说明没漂移过）把 `value` 拉回新基线；否则保留现值（"那是经历，不该被改设定覆盖"）；最后 `value = max(mn, min(mx, value))` 夹一次，连 `baseline`/`min_value`/`max_value` 一起 UPDATE。
4. 面板手改走 `POST /v1/persona/traits`，在 `app/api/routes.py:805` 再夹一次：`value = max(float(row["min_value"]), min(float(row["max_value"]), float(inp.value)))`，并写一条 `traits_log`（`cause` 固定为 `"设置面板手动校准"`）。

**注意**：`reload_persona()` 会调 `init_traits()`，所以改 YAML 的 `min`/`max` 之后热加载即可生效；改 `baseline` 只对"还没漂移过"的维度生效。

### 3.6 `app/persona/transfer.py`：自包含档案

**导出格式**（`export_payload(rt)`，`format` 字段是硬校验值）：

| 字段 | 类型 | 内容 |
|---|---|---|
| `format` | str | 固定 `"ai-life/persona-transfer"` |
| `version` | int | 固定 `1` |
| `exported_at` | str | ISO 时间戳（带时区，秒精度） |
| `persona_name` | str | `rt.persona.name` |
| `persona_yaml` | str | **人设文件原文**（`rt.cfg.persona_file.read_text()`；文件不存在则 `""`） |
| `session` | dict | `SELECT * FROM session WHERE id = 1`（`seq`/`her_name`/`name_locked`/`name_pending`/`status`/`started_at`/`ended_at`/`end_reason`） |
| `relation` | dict | `SELECT * FROM relation WHERE id = 1`（`affinity`/`trust`/`attraction`/`aversion` 等） |
| `traits` | list | `SELECT * FROM traits` 全表 |
| `memories` | list | `SELECT * FROM memories ORDER BY id`（只读视图，作可读快照） |
| `memory_archive` | dict | `memengine.store().dump()`——引擎原生归档，带线索、热度、召回审计 |
| `events` | list | `SELECT * FROM events ORDER BY id` |
| `summaries` | list | `SELECT * FROM summaries ORDER BY id` |

`export_file(rt)` 写到 `DATA_ROOT/exports/{persona_name}-{%Y%m%d-%H%M%S}.life.json`，`indent=2`、`ensure_ascii=False`；`POST /v1/soul/export` 返回 `{ok, path, bytes}`。

**导入流程**（`import_payload(rt, payload, keep_events=True)`）：

1. 校验 `payload["format"] == "ai-life/persona-transfer"`，否则 `raise ValueError("不是人设档案文件")`。
2. `persona_yaml` 非空就整份覆盖写 `DATA_ROOT/persona-active.yaml`（`ACTIVE_PERSONA`）。
3. 清空 `_TABLES = ("events", "relation_log", "traits_log", "summaries")` 四张表 + `traits` 表。
4. `relation` 非空 → UPDATE 四维数值与 `updated_at`（`id = 1`）。
5. `traits` → 逐行 INSERT（跳过 `id` 列）。
6. `session` 非空 → UPDATE `seq`/`her_name`/`name_locked`/`name_pending`/`status`/`started_at`/`ended_at`/`end_reason`。
7. 记忆：先 `h2.forget_all()` 清空，再 `memengine.store().load(archive, mode="replace")`；归档坏了（抛异常）就退回逐条 `h2.add(kind, content, importance, pin, user_id, chat_type)` 重建。
8. `keep_events=True` 时逐条重灌 `events`；`summaries` 无论开关都重灌。
9. 写一条 `continuity.record("soul_imported", "整份灵魂档案被导入：记忆与对话换了一套", "transfer", {memories, persona_file})`。
10. 返回 `{ok: True, memories: imported, persona_file: str(ACTIVE_PERSONA)}`。

**导入后被清空且不会恢复的两张表**：`relation_log` 与 `traits_log` 在导出体里**没有对应字段**，导入时被 DELETE 但没有任何 INSERT。这是真实行为：关系变化账本与性格漂移记录在搬家后归零。

**另外两个函数**：`snapshot_for_archive()` 返回 `{persona_yaml, exported_at}`（关系结束时随档案存一份人设原文）；`remove_active_persona()` 删掉 `persona-active.yaml`（回退到 `config.yaml` 指定的人设）。

**相关路由**：`POST /v1/soul/export`、`POST /v1/soul/import`（body 为 `.life.json` 原文）、`GET/POST /v1/soul/snapshots`、`POST /v1/soul/snapshots/restore`（写 `data/snapshots/`）、`GET /v1/soul/letter-template`、`GET /v1/dev/transfer/export?password=`、`POST /v1/dev/transfer/import?password=&keep_events=`。

### 3.7 `whale-girl.yaml` 与 `whale-chan.yaml` 的差异

**当前生效的是 `whale-chan.yaml`**（`config.yaml` 第 47 行）。两版不是同一角色的两个版本，而是**两个不同的角色**：`whale-girl` 是人类上班族小涵（`key: xiaohan`、`species: 人类`、26 岁），`whale-chan` 是 DeepSeek 拟人的鲸鱼娘小鲸（`key: whale-chan`、`species: 鲸鱼娘`）。

| 维度 | `whale-girl.yaml`（小涵） | `whale-chan.yaml`（小鲸） |
|---|---|---|
| 行数 / 字节 | 230 行 / 8,731 B | 285 行 / 13,164 B |
| 顶层键数 | 19 | 22（多 `source`、`fusion`、`family`） |
| `speech.style` | 6 条 | 19 条 |
| `speech.rules` | 8 条 | 14 条 |
| `speech.negatives` | 6 条 | 7 条 |
| `anchors` | 6 场景 / 19 句 | 6 场景 / 24 句 |
| `profile` | 8 个统计 + `rates_per_1000`（8 个比值：`intimate 52.9`、`sweet 26.4`、`anger 27.2`、`laughing 26.4`、`question 25.1`、`particle 22.7`、`care 8.3`、`self_mock 0.8`） | 只有 8 个统计，无 `rates_per_1000` |
| 温柔/沉静/好奇/黏人/理性/敏感/幽默/自持 | 58/34/62/86/41/68/72/38 | 62/52/82/74/58/66/78/46 |
| `stages` | 结构相同（6 段，阈值 0/20/40/60/80/95） | 同左，`unlock` 文案全部换成鲸鱼娘的 |
| `schedule` | 上班族作息（挤地铁/开会/吃外卖） | 值班室作息（扒白饭/交班/群里潜水） |
| 特有键 | 无 | `source`（出处与授权）、`fusion`（融合来源与冲突处理）、`family`（生父/干爹 QQ） |
| 身份约束 | `rules` 第 5 条：**不提"海""水""鲸""声纹"** | `rules` 第 2 条：鲸鱼身份只在被问起时提；第 5 条：**不许用"鲸脂""保温层"给情绪找借口** |
| 关系边界 | 无家人设定 | `family` + `world` 里明写"对生父和干爹只有家人感情，家人这条线永远不谈恋爱" |

`persona/backup/README.md` 的规则必须照做：本分支**不改** `persona/whale-girl.yaml`；鲸鱼娘的新设定只写进 `whale-chan.yaml`；缺字段从备份复制；合并回主线时人设零冲突（主线只认 `whale-girl.yaml`）。

## 4. 换人设流程

1. **先备份库**：把 `data/life.db`（以及 `data/life.db-wal`/`-shm` 若是运行中）整个复制出去。`transfer.import_payload` 会 DELETE 四张表，没有回滚。
2. **改指向**（二选一）：
   - 配置文件：`config.yaml` 的 `persona.path`（当前 `persona/whale-chan.yaml`）；
   - 环境变量：`SYMEM_PERSONA=persona/xxx.yaml`（旧名 `AI_LIFE_PERSONA` 仍兼容，见 `app/core/config.py:238`）。优先级写在 `config.py:212–225` 的 `persona_file` 属性里：`persona_override`（存在时）> `config.yaml` 的 `persona.path`（存在时）> `data/persona-active.yaml`（导入产物）> 原样返回 `persona.path`。
3. **热加载**：`POST /v1/persona/reload?password=<developer.key>`；带 `path=` 参数可以临时指定另一个文件（会写 `rt.cfg.persona_override`，**不落盘**，重启失效）。返回 `{ok, before: {name, key}, after: {name, key}, rules, anchors, file}`；`after != before` 时写一条 `continuity.record("persona_changed", "{旧名} → {新名}", "dev", {"before": …, "after": …, "file": …})`。
4. **验三处**（不是一处）：

| 验什么 | 打哪个接口 | 看哪个字段 |
|---|---|---|
| 人设身份换没换 | `GET /v1/persona` | `key`、`name`、`species`、`self_ref`、`style` 条数 |
| 性格值有没有跟着基线走 | `GET /v1/board` 或 `GET /v1/persona` | `traits[].dim/value/baseline/min/max`（`/v1/health` **没有** `traits` 字段，只有 `persona` 名字） |
| 她自称什么 | `GET /v1/session` | `her_name`、`name_locked` |

5. **改名字要额外一步**：见下面坑 1。`session.her_name` 如果已经锁定（用户给她起过名），改 YAML 的 `name` 不会改变她对外的名字。

**坑**：

1. **`name` 会被数据库覆盖**。`app/mind/loop.py:250` 每轮执行 `persona.raw["name"] = session_mod.her_name(rt.default_name)`；而 `session.her_name` 只在**第一次** `session` 表插入时取 `persona.name`（`app/relation/session.py:50–54`），之后 `reload_persona()` 不会同步它。结果：换人设后 `/v1/persona.name` 是新名字，但聊天里她仍自称旧名字，`/v1/board.persona.name` 也还是旧名字。要真正换名，走 `POST /v1/session/new`（会归档旧档并把 `her_name` 换成 `_NAME_POOL` 里随机一个"小X"）或直接改 `session` 表的 `her_name`。
2. `stage_index` 的 `current` 在循环前没有初始化：如果人设的 `stages[0].at > 0` 且 `affinity` 比它小，函数走到 `return current` 会抛 `UnboundLocalError`。现有两版人设首段都是 `at: 0`，所以没触发过。
3. `reload_persona()` 只重读 YAML 与 traits，**不重读** `app/mind/llm.py` 的模型实例（那是 `apply_provider` 的事），也不刷新 `session.her_name`。
4. `transfer.import_payload` 写的是 `DATA_ROOT/persona-active.yaml`，而 `config.yaml` 里显式写的 `persona.path` 优先级更高——导入一份新人设后，如果 `config.yaml` 还指着 `persona/whale-chan.yaml`，**导入的人设不会生效**（只会被读进导出体）。这是设计如此（注释原文："config.yaml 里写的 persona.path 是用户明确选择，必须优先于 persona-active"）。

## 5. 改这一层要注意

1. 新增顶层键：必须同时在 `app/persona/load.py` 加一个 `@property`（或方法）并在 `app/mind/prompt.py` 的 `build()` 里拼进 `system` 与 `sections` 预算表，否则这个键永远不进 prompt。只加 YAML 不加代码 = 只加注释。
2. 改 `speech.anchors` 的场景名：`prompt.py:295` 用 `persona.anchors(per_scene=1)` 取全部场景的第一句，场景名随意；但 `loop.py` 若传 `anchors=anchor_lines` 就会绕过人设，改 YAML 不起作用——查 prompt 用的是哪条路。
3. 改 `traits` 的 `baseline`：会同时被 `init_traits` 同步进 `traits` 表（没漂移时），面板滑杆的上下限来自 `min`/`max`，不是 baseline。
4. 改 `stages`：`at` 必须是数字且**升序**（`stages()` 会排序，但 `stage_for` 依赖排序结果），`key` 不能重名（`engine.stage_payload` 与前端阶段表按 `key` 显示）。
5. 改 `schedule`：首段必须是 `00:00`，否则 00:00–首段 `start` 之间会落进写死的 `{"activity": "发呆", "energy": 50}`；每段 `start` 必须是 `HH:MM` 两段式。
6. 加 `family` 成员：**代码不读 `family`**，真正生效的是 `app/relation/love.py:family_role()` 按数据库里的用户记录判定。改 YAML 的 `family` 只是文档，不改变行为。
7. 改 `persona/templates/记忆交接信.txt`：任何以 `#` 开头的行会被解析器跳过（`app/memory/formats.py` 第 68、104、178、192、239 行都做了 `startswith("#")` 判断；模板第 30–46 行共 17 行说明就是靠这个不进记忆）；改 `memory_letter.template` 路径后要确认它在 `config.ROOT` 下能解析到。
8. 改 `end.finale`：只在关系终止（厌恶到 100 或手动 `/v1/session/new`）时被读，缺省回退三句写死文案"就这样吧。/别再找我了。/我当你没来过。"。

## 6. 交叉引用

- 相关：docs/04（`prompt.build` 的完整段落顺序与 token 预算）
- 相关：docs/17（`traits` / `traits_log` / `session` / `relation` 建表语句）
- 相关：docs/18（`persona.path`、`SYMEM_PERSONA`、`developer.key` 的配置语义）
- 相关：docs/20（设置页"人物"分区：滑杆改 traits、阶段表、原话列表）
- 相关：docs/12（`/v1/persona`、`/v1/persona/traits`、`/v1/persona/reload`、`/v1/soul/*` 的完整出入参）
