# 06 · 记忆系统（app/memory）

> 覆盖：`app/memory/*.py`（21 个文件 / 4 952 行，其中 `__init__.py` 是 0 行空文件）｜基于 2026-11-01 的工作区代码状态（HEAD `89a1c94` + 当时未提交的改动；本文所有行号以工作区文件为准）｜上游：`app/core/db.py`（业务表 + 整库 `init()`）、`app/core/memengine.py`（把 vendor 插进 `sys.path` 并缓存引擎单例）、`vendor/symvolve_memory/`（只读引擎本体）｜下游：`app/mind/loop.py`（每轮对话）、`app/mind/prompt.py`（kind → 标签表）、`app/api/routes.py` 与 `app/api/console.py`（面板/调试口）、`app/cli.py`（导入与维护）、`app/life/*`（日记、念头、主动消息）

## 1. 这一层干什么

`app/memory` 是"她会记得什么"的全部代码：一条记忆从哪来、存在哪、热度怎么掉、什么时候被想起来、什么时候被合并、什么时候被遗忘，以及"不是她的记忆"的语料（参考对话、风格样本、锚点）怎么进来和怎么隔离。

它解决的核心矛盾是：**业务代码只认一套旧字段**（`kind / content / importance / heat / cues / pinned`），而记忆本体已经搬进了 `vendor/symvolve_memory` 引擎的 `mem_item / mem_cue / mem_event / mem_recall_log` 四张表。中间那层语义对齐就是 `h2.py`：写只有 `h2.add()`，读只有 `h2.retrieve()` 与一组查询函数，`memories` 这个名字退化成"给二十几处老 SQL 用的只读视图"（`memv2.py` 建，`app/core/db.py:362` 启动时调）。

它调用谁：引擎的 `Store / MemoryLifecycle / MemoryRecall / MemoryRenderer`（经 `app/core/memengine.py` 单例）、`app/core/db.py` 的 `rows/one/execute/kv_get/kv_set`、`app/mind/models.py`（抽模型档位）、`app/relation/*`（冷启动写入关系数值）。谁调用它：`app/mind/loop.py` 每轮对话做 `retrieve → 组装 prompt → 后台抽事实`，`app/api/*` 做面板读写，`app/cli.py` 做语料导入、锚点重建、冷压缩。

它明确不做的事：不自己建 schema（引擎迁移脚本建）、不改 vendor 一个字节、不把 `mem_event.raw_text` 原文塞进 prompt（只吃 `mem_item.text`）、不负责留痕（`app/memory` 21 个文件里 0 处引用 `app.core.continuity`）。最后一条是本层最重要的边界，见 §5。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `__init__.py` | 0 | 空文件，只为让 `app.memory` 成为包 |
| `h2.py` | 704 | 记忆公共 API：唯一写入口 `add()`、唯一召回入口 `retrieve()`、温度/清晰度/遗忘/维护/导出 |
| `store.py` | 160 | 薄壳：`events` 表的真身 + 7 个转发到 `h2` 的旧签名函数 |
| `memv2.py` | 292 | 衡忆 2.0 落地：旧 `memories` 表搬进引擎 → 改名 `memories_legacy_20261030` → 建只读视图 |
| `facts.py` | 142 | 每 6 条用户消息让模型抽一次事实（最多 2 条）并写进记忆 |
| `extract.py` | 220 | 无模型时的规则抽取、冷启动好感反推、第三方污染扫描 |
| `cold.py` | 226 | 冷记忆软合并：按"月 + kind + 归属"合成一条摘要，`merged_into` 可还原 |
| `compress.py` | 219 | 工作记忆压缩：把聊过的折成"线索"，不折成事实；`covered_to` 往前推 |
| `ingest.py` | 225 | 导入管线：判定格式 → 解析 → 写 events / 原话池 / 钦定记忆 → 冷启动 |
| `declare.py` | 209 | 声明层：一个 TXT 到底是什么格式，以及"谁是我、谁是她" |
| `formats.py` | 242 | 五种格式各自的解析器，统一产出 `{ts, role, text}` 序列 + 原话池 |
| `reference.py` | 347 | 参考语料导入：只写 `ref_turns / ref_quotes`，与用户记忆物理隔离 |
| `scrub.py` | 198 | 去敏：11 类正则 + 人名识别，宁可多删 |
| `anchors.py` | 271 | 锚点池：从语料抽"她会说的话"，频次 + 分层取材，按关系阶段放行 |
| `style.py` | 343 | 风格取样：把 `data/human_life/_derived` 压成"给 prompt 的一点点东西" |
| `threads.py` | 86 | 没说完的话题：抓到"明天再讲"建提醒，到点注入一次 |
| `companion.py` | 290 | 陪伴层接入：情绪 → 召回 → 共振 → "要不要主动提旧事"，冷却账本落 kv |
| `observe.py` | 275 | 人类观察 / 每人看法 / 思考报告 / 记忆自评指标，只管数据与指标 |
| `evolve.py` | 277 | 进化生成层：调模型写观察、写思考报告、更新看法、做周自评 |
| `stickers.py` | 96 | 表情包选择器：只挑 `safe=true`，按场景优先，不回同一张 |
| `voices.py` | 130 | 本地语音素材：文件名即触发词，按需转 amr 发送 |

文件之间的依赖是单向的：`h2` 依赖 `memengine`，`store` 依赖 `h2`，`cold` 依赖 `h2`，`facts` 依赖 `h2`，`ingest` 依赖 `declare/formats/extract/store`，`reference` 依赖 `scrub`，`evolve` 依赖 `observe`。反向依赖一处都没有。

## 3. 模块逐一详解

21 个文件里 `app/memory/__init__.py` 是 0 行空文件（只为让 `app.memory` 成为包，无任何符号），下面按"1 个核心 + 19 个专题"逐节展开；`store.py` 作为薄壳排在 `h2.py` 之后，因为读它必须先懂 `h2`。

### 3.1 `app/memory/h2.py`（704 行）

**职责**：唯一写入口、唯一召回入口、以及温度/清晰度/遗忘/维护/导出的一组函数。文件头三行写死了三条设计约束：不写第二套存储、`mem_event` 原文永不进 prompt、`pinned` 豁免一切衰减。

#### 3.1.1 常量（真实数值）

| 常量 | 值 | 含义 |
|---|---|---|
| `INITIAL_HEAT` | `0.90` | 新写入且未钉住条目的初始热度 |
| `HOT_THRESHOLD` | `0.50` | ≥ 此值算 `hot`，每轮进上下文候选 |
| `COLD_THRESHOLD` | `0.12` | ≤ 此值算 `cold`；也是 `cold.py` 冷压缩的准入线 |
| `REHEAT` | `0.28` | 召回命中的加热量（引擎实际用的是 `decay.HEAT_RECALL_DELTA`，同值） |
| `DECAY_PER_DAY` | `0.97` | 旧口径常量，代码里没有任何地方用它算热度（见 §3.1.8 的坑） |
| `PROTECTED_KINDS` | `("boundary", "promise", "quote")` | 不参与冷压缩：雷点、约定是承诺，quote 是语气锚点 |
| `RECALL_WORDS` | 26 个词 | 召回意图词，`retrieve()` 用它判断"这句是不是在回忆"，同时当感官线索 |
| `PIN_WORDS` | 11 个词 | 钉住指令词，`wants_pin()` 与 `loop.py` 用它触发 `pin=True` 写入 |

`RECALL_WORDS` 全部 26 个：`记得 / 记不记得 / 还记得 / 想起来 / 想起 / 上次 / 那次 / 那天 / 那年 / 以前 / 去年 / 前年 / 小时候 / 那时候 / 之前 / 上次说 / 你说过 / 照片 / 照片里 / 歌 / 音乐 / 梦到 / 梦见 / 味道 / 雨 / 声音`。

`PIN_WORDS` 全部 11 个：`记住这个 / 记住这件事 / 别忘了 / 别忘记 / 你要记得 / 要记住 / 记着 / 永远记得 / 千万别忘 / 不要忘记 / 永久记住`。

`_STOP_BIGRAMS` 30 个二字停用词（`什么/怎么/这样/那样/这个/那个/我们/你们/他们/自己/知道/觉得/可以/应该/现在/刚才/时候/真的/有点/一下/是不/不是/没有/一个/一些/如果/因为/所以/但是/然后`），`bigrams()` 产出的片段会把这些剔掉，避免"我们""可以"这种高频虚词把线索表撑爆。

#### 3.1.2 `add()` —— 唯一写入口

```python
def add(
    kind: str,
    content: str,
    importance: float = 0.6,
    stability: float = 0.5,
    pin: bool = False,
    cues: list[str] | None = None,
    source_ids: list[int] | None = None,
    user_id: int = 0,
    chat_type: str = "",
    extra_meta: dict[str, Any] | None = None,
) -> int:
```

| 参数 | 类型/默认 | 真实含义（读代码得到，不是猜的） |
|---|---|---|
| `kind` | `str`，无默认 | 条目类型，写进 `mem_item.kind`；同时塞进 `meta["kind"]`。仓库里出现过的值：`fact`、`preference`、`event`、`boundary`、`quote`、`thought`、`diary`、`promise` |
| `content` | `str`，无默认 | 正文。进函数先 `strip()`，空串直接 `return 0`（不写任何行） |
| `importance` | `float = 0.6` | **旧口径 0–1**，经 `_importance_engine()` 乘 100 后夹到 `1..100` 落库；读出来时 `to_old()` 再用 `_importance_old()` 除回 100 |
| `stability` | `float = 0.5` | 兼容老签名保留的参数，函数体内**一次都没用过**，不落库（见 §3.1.8） |
| `pin` | `bool = False` | `True` → `heat=1.0`、`pinned=1`，此后豁免衰减与遗忘 |
| `cues` | `list[str] \| None = None` | `None` = 用 `extract_cues(content)` 自动抽（上限 12）；传 `[]` = 一个线索都不挂；传值 = 只挂这些，不再自动抽 |
| `source_ids` | `list[int] \| None = None` | 旧 `events.id` 列表。**不写进** `source_event_ids`（那是指向 `mem_event` 的引擎 id，两套 id 混用会指错行），而是塞进 `meta["ai_life_source_ids"]` |
| `user_id` | `int = 0` | 业务侧是整数；`_uid()` 转成字符串。`0` = 共用/她的（旧系统的单用户面板语义） |
| `chat_type` | `str = ""` | 空串 = 不分场合。同时算出 `scope = chat_type or "shared"` |
| `extra_meta` | `dict[str, Any] \| None = None` | 并入 `meta`，**在** `{"kind": kind}` **之后** update，所以传 `{"kind": "x"}` 能把 `meta["kind"]` 改掉（`mem_item.kind` 不受影响） |

返回值：新条目 id；命中重复时返回已存在的 id；`content` 为空返回 `0`。

写入流程（照着重写一遍就能跑）：
1. `strip()` 正文；空就 `return 0`。
2. 取 `store = memengine.store()`、`life = memengine.lifecycle()`；算 `uid / ct / sc / legacy_ids`。
3. `_find_duplicate(uid, ct, kind, content)`：在**同 (user_id, chat_type, kind)** 分区里顺序扫 `store.list_items(...)`，`item.text == content` 即命中。
4. 命中就走"加温"分支：`importance = max(旧, 新)`；`heat = clamp(max(旧heat, 0.90) + 0.1)`；`pinned = 旧 or 新`；`store.update_item()`；`life.mark_seen()`（把 `last_seen` 刷成现在、`clarity` 打回 `vivid`、`meta["heat_anchor"]` 前移，衰减重新起算）；再 `add_cues()` 合并新线索；`return existing`。
5. 没命中就组装 `meta`：先 `{"kind": kind}`，再 `update(extra_meta)`，有 `legacy_ids` 就加 `meta["ai_life_source_ids"]`。
6. `store.append_event(MemoryEvent(...))`：`chat_type = ct or "private"`、`scope = sc`、`raw_text = content`。这一步保证**原文永远有一份 append-only 归档**，`mem_event` 行不会被改也不会被删。
7. 组装 `MemoryItem`：`title = _title(content)`（按 `。！？!?\n` 切第一段，取前 24 字）、`importance` 夹到 1–100、`heat = 1.0 if pin else 0.90`、`source_event_ids=[event_id]`，`store.insert_item()` 拿新 id。
8. 线索：`cues is not None` 用它，否则 `extract_cues(content)`；逐个 `_cue_kind()` 判类型后 `add_cues(mid, [(cue, kind, 1.0), ...])`。`_cue_kind()` 的分派是：整串匹配 `_TIME_CUE` → `time`；在 `RECALL_WORDS` 里 → `sensory`；含数字 → `entity`；其余 `content`。

同一个 `(kind, content, user, chat)` 重复写等于加温、不新增行——这条幂等是导入管线、日记、冷压缩都依赖的性质。

#### 3.1.3 `retrieve()` —— 召回怎么打分

```python
def retrieve(text: str, hot_limit: int = 10, user_id: int = 0,
             chat_type: str = "") -> Recall:
```

一次 `retrieve()` 分两段：先拿"手边的"（热层），再拿"靠线索捞的"（引擎召回），最后合并成 `Recall`。

**第 0 步**：`maybe_tick()`——最多每小时推进一次稳定衰减与遗忘（见 §3.1.7）。

**第 1 步 · 分区**（`_partitions()`，最多 4 个分区）。旧系统只有两层隔离（`user_id` + `chat_type` 空=不分场合），引擎是严格相等过滤，所以这里把旧语义展开成最多 4 次检索：
- `chat_type` 传给引擎前先归一：空串 → `None`（引擎侧 `None` = 不限会话类型，等价于旧的"不筛场合"），非空 → 原字符串。
- `user_id > 0`：分区是 `[(这个人, 场合), (这个人, ""), ("0", 场合), ("0", "")]`（`uid == "0"` 时不重复加）。
- `user_id <= 0`：先 `all_user_ids()`（`SELECT DISTINCT user_id FROM memories`）枚举所有归属，每个归属再展开成上面那套，即"面板不筛人，全库都算她的记忆"。

**第 2 步 · 热层**（`_hot_from()`）。逐分区 `store.list_items(user_id, chat_type)`，跳过 `kind in ("quote", "thought")`（原话是语气锚点、念头是自言自语，两者都不占"记得的"位置），剩下的只要 `pinned` 或 `_heat_of(item) >= 0.50` 就进候选。去重后按 `(-pinned, -heat, -importance, -id)` 排序，取前 `hot_limit`（默认 10）。

**第 3 步 · 引擎召回**。逐分区调 `recall_engine.search(user_id=..., query=text, chat_type=..., limit=5, log=False, bump_heat=True)`：`limit=5` 是故意多取，因为 `quote / thought` 会在后面被滤掉；`log=False` 是因为 4 次分区检索只该留一条审计。

引擎的打分是"线索分 + 属性分"，权重全部来自 `RecallWeights`（`vendor/symvolve_memory/recall.py:72`）：

| 权重/阈值 | 数值 | 怎么用 |
|---|---|---|
| `cue_exact` | `34.0` | 查询 token 与条目线索**完全相等**时加的分（乘线索自身 `weight`） |
| `cue_partial` | `18.0` | 包含命中：token 是线索子串，或线索整串出现在查询文本里 |
| `cue_cap` | `60.0` | 线索分上限（与 `entity` 分分开裁，合计上限 `cue_cap + entity*2 = 140`） |
| `max_cue_weight` | `2.0` | 单条线索 `weight` 的截断上限 |
| `importance` | `30.0` | 属性分：`importance/100 * 30` |
| `heat` | `20.0` | 属性分：`clamp(heat) * 20` |
| `pinned` | `25.0` | 属性分：钉住直接 +25 |
| `recency` | `12.0` | 属性分：`max(0, 1 - 距 last_seen 天数/30) * 12`，所以 30 天前被提及就归零 |
| `recency_days` | `30.0` | 上面那条的新鲜度窗口 |
| `entity` | `40.0` | 标识符（`entities.py` 的紧键）命中，**每条条目最多按 2 个计**，即 +40 / +80 |
| `co_cue` | `12.0` | 二轮扩展：与高分条目共享线索的条目最多加 `12.0 * 2.0` |
| `vector` | `30.0` | 向量通道权重；`vector_min_similarity = 0.35` 以下不计分 |
| `hit_threshold` | `55.0` | 总分 ≥ 55 判 `hit` |

清晰度也直接折算成分（`CLARITY_POINTS`）：`vivid 15.0 / clear 10.0 / fuzzy 4.0 / trace 0.0`。

**硬门槛**：一条候选如果既没线索命中、也没实体命中，`_score_item()` 直接返回 `None`（不进入排序）。所以"热但完全不相关"的条目不会靠 `heat + pinned + importance` 混进召回结果——它们的上限是 `30 + 20 + 25 + 12 + 15 = 102`，但没有线索命中就一分都拿不到。

**判状态**（`_judge()`，`recall.py:923`）：没有任何命中 → `miss`；`top >= 55` 或 `exact=True`（精确线索/实体命中）→ `hit`；`top > 0` 且有 token → `uncertain`；`confidence = min(1.0, top/100)`。

**第 4 步 · 合并**。`hit` 取 2 条、`uncertain` 取 1 条，并且**剔除已经在热层里的 id**（否则 prompt 里同一句出现两遍）；`result.in_hot` 记录"想起来的这条本来就在热层"。`best_status == "miss"` 且这句话没有任何 `RECALL_WORDS` → 状态降级成 `none`（她没在想事情，就是普通接话）。线索取第一次成功的分区结果的 `cues[:12]`。

**第 5 步 · 审计**。无论 4 次分区检索结果如何，只写**一条** `store.log_recall()`：`status = "miss" if none else status`、`confidence`、`item_ids = 召回条目的 id 或排序前 3 的 id`、`created_at=now`。

**第 6 步 · 加热**。这一步发生在引擎内部：`search(bump_heat=True)` 里只有 `status == "hit"` 才逐条 `lifecycle.mark_recalled()`（`recall.py:527`）——`heat = clamp(decayed_heat + 0.28)`、`last_recall = now`、`recall_count += 1`、`meta["heat_anchor"] = now`（`lifecycle.py:265`）。也就是说：**召回会加热度，但只在她真的"想起来"（hit）时加热；`uncertain` 不加**。`hot` 层本身不加（`_hot_from` 只读不写），要手动加温就调 `touch()`。

举个算例（照着能验）：某条 `heat=0.60`、`importance=62`、`clarity=clear`、未钉住、`last_seen` 在 10 天前，查询里有一个 token 与它的某条线索完全相等：
`34.0（cue_exact） + 18.6（62/100*30） + 12.0（0.6*20） + 0（clarity=4.0 时是 4.0，这里 clear 是 10.0）` —— 完整算式为 `34.0 + 18.6 + 12.0 + 10.0 + (1-10/30)*12.0 = 82.6`，≥ 55 → `hit`，`confidence = 0.826`，随后加热到 `0.60 + 0.28 = 0.88`。

#### 3.1.4 热度衰减与 25 天归零

热度是**惰性结算**的：不在后台跑定时任务，每次读的时候用"上次结算时刻（anchor）到现在的天数"现算（`decay.py:144` `heat_after_days`）：

```text
heat(t) = clamp01( heat_anchor_value - 0.04 * 天数 )
```

- `HEAT_DECAY_PER_DAY = 0.04`（`decay.py:31`）：满热 `1.0` 线性回落到 0 恰好 **25 天**（`1.0 / 0.04`）。
- 新条目从 `INITIAL_HEAT = 0.90` 起，所以自然老去时 **22.5 天**归零；`0.90 → 0.50`（跌出 hot）需要 **10 天**；`0.90 → 0.12`（跌进 cold）需要 **19.5 天**。
- 一次命中 `+0.28`（`decay.py:29`），等价于"续 7 天"（`0.28 / 0.04`）。所以一条已经把热度耗尽到 `0.0` 的条目被想起来是 `0.28`，还够不上 `HOT_THRESHOLD=0.50`——从冷回到热需要连续两次命中（`0.56`）。
- `pinned` 条目 `_heat_of()` 直接返回 `1.0`，引擎侧 `decayed_heat()` 也提前返回（`decay.py:163`）；`heat <= 0` 同样原样返回，不会算出负数。
- 结算基准 `heat_anchor` 取 `meta["heat_anchor"]`，缺失时回落到 `max(last_seen, created_at, last_recall)`（`decay.py:133`）。因为 `mark_seen / mark_recalled` 都会前移 anchor，重复调用是幂等的，**不会重复扣**。

#### 3.1.5 清晰度分档

`clarity` 只有一个方向：往下掉；只有新的提及（`mark_seen`）能把它打回 `vivid`。

| 分档 | 距上次被提及 `since_seen_days` | 召回折算分 |
|---|---|---|
| `vivid` | `≤ 7.0` 天 | `15.0` |
| `clear` | `> 7.0 且 ≤ 30.0` 天 | `10.0` |
| `fuzzy` | `> 30.0 且 ≤ 60.0` 天 | `4.0` |
| `trace` | `> 60.0` 天 | `0.0` |

边界值是 `DecayPolicy`（`decay.py:40-42`）：`vivid_days=7.0 / clear_days=30.0 / fuzzy_days=60.0`。两条豁免：`pinned` 条目清晰度原样返回；关系阶段到 `INTIMATE`（`RelationStage.INTIMATE = 3`，即家人/恋人）时最低只降到 `fuzzy`，不落 `trace`（`decay.py:125`）。

#### 3.1.6 遗忘判定

`should_forget()`（`decay.py:175`）是下面六条同时成立才软删除：

| 条件 | 数值/来源 |
|---|---|
| 未钉住、未遗忘、仍然活跃（`superseded_by` 与 `merged_into` 都为空） | `MemoryItem.is_active` |
| 不是高关系条目 | `meta["relation_stage"] >= INTIMATE` **或** `importance >= 60` 都算高关系 |
| 够老 | `age_days(created_at) >= 60.0` |
| 只剩 `trace` | `clarity == "trace"` |
| 热度低 | `heat <= 0.10` |
| 重要度低 | `importance < 40` |

高关系下限 60 分这一条很关键：`importance >= 60` 的条目永远进不了遗忘流程，而 `cold.py` 的摘要条目会继承组内最大 `importance`，所以它们也不会被下一次 tick 顺手抹掉。

#### 3.1.7 `forget_all()` 为什么不自带留痕

```python
def forget_all() -> int:
    life = memengine.lifecycle()
    n = 0
    for item in memengine.store().list_items():
        try:
            life.forget(int(item.id or 0), reason="reset")
            n += 1
        except Exception:
            continue
    return n
```

它做的事只有一件：把每行 `forgotten` 置 1、`meta["forget_reason"]="reset"`、`meta["forgotten_at"]=now`（`lifecycle.py:278`），返回条数。**它不写 `continuity_log`** —— `app/memory/` 21 个文件里没有任何一处 `import app.core.continuity`（`grep` 结果为 0）。

留痕由"发起动作的那一层"负责，代码里三处调用点都是这么做的：

| 调用点 | 留痕 |
|---|---|
| `app/api/routes.py:426`（`POST /v1/dev/memory/clear`） | `continuity.record("memory_forgotten", "她被清空了记忆（开发者操作）", "dev", {"count": n})` |
| `app/api/routes.py:346`（`GET /v1/dev/session/clear`） | `continuity.record("session_reset", ...)`，`forgotten` 只是 `meta` 里的一个数 |
| `app/persona/transfer.py:109`（导入灵魂档案） | 先 `h2.forget_all()`，最后 `continuity.record("soul_imported", ...)` |
| `app/relation/session.py:270`（`terminate()` 结束关系） | 先 `archive(reason)` 落归档文件，再 `forget_all()` |

分工的理由在代码里是自洽的：`h2` 是**记忆层的机械接口**（它连"你为什么要清空"都不知道），`continuity` 是**审计层**，让两者互相 import 会把记忆层绑在审计层的初始化顺序上（而 `import app.main` 本身有建库副作用，见 docs/00）。所以规矩是：谁发起动作，谁写留痕；`forget_all()` 只负责如实返回"清掉了几条"。

#### 3.1.8 其余公开函数（全表）

| 符号 | 签名 | 作用 |
|---|---|---|
| `Recall`（dataclass） | `hot / recalled / cues / status / confidence / is_recall_attempt / in_hot` + `as_dict()` | 一次召回的结果容器；`as_dict()` 里 `confidence` 保留 2 位、`cues` 只给 6 个、`hot` 只给 id、`recalled` 给 `{id, content, kind}` |
| `bigrams` | `bigrams(text: str) -> set[str]` | 只留字母数字与汉字，切 2-gram，剔掉 30 个停用词 |
| `extract_cues` | `extract_cues(text: str, limit: int = 12) -> list[str]` | 线索 = 2-gram + 命中的 `RECALL_WORDS` + `_TIME_CUE` 匹配（`\d{4}年 / \d{2}月 / 二十X / X十` 等），去重后截 `limit` |
| `wants_pin` | `wants_pin(text: str) -> bool` | 正文是否含 11 个钉住词之一 |
| `temperature` | `temperature(heat: float) -> str` | `>=0.50 → "hot"`；`>=0.12 → "warm"`；否则 `"cold"` |
| `effective_heat` | `effective_heat(row: Any, now: datetime \| None = None) -> float` | 兼容旧调用：传 `MemoryItem` 直接算；传行（dict/`sqlite3.Row`）先用 `id` 从引擎取条目；都取不到就返回行里 `heat` 的原值 |
| `to_old` | `to_old(item: MemoryItem, *, cues: list[str] \| None = None, now: int \| None = None) -> dict[str, Any]` | 引擎条目 → 旧记忆行，19 个字段：`id/kind/content/title/importance/stability(恒定 0.5)/source_ids/first_seen/last_seen/recall_count/last_recall/heat/pinned/cues/last_touch/user_id(整数)/chat_type/clarity/temperature` |
| `pin` | `pin(mid: int, pinned: bool = True) -> bool` | 钉住/取消钉住；`pinned=True` 强制 `heat=1.0`，`False` 回落 `0.90`；条目不存在返回 `False` |
| `touch` | `touch(ids: list[int]) -> None` | 逐条 `lifecycle.mark_recalled()`，单条失败（被合并/遗忘了）静默跳过 |
| `mark_recalled` | `mark_recalled(ids: list[int]) -> None` | `touch` 的别名，旧调用点用这个名字 |
| `find_by_content` | `find_by_content(content: str, user_id: int = 0, chat_type: str = "") -> int` | 给 `facts.py` 去重用：同分区同正文返回已有 id，否则 0（`kind` 固定按 `"fact"` 找） |
| `recall_debug` | `recall_debug(query: str, user_id: int = 0, chat_type: str = "") -> dict[str, Any]` | `retrieve(...).as_dict()`；`/v1/memory/recall` 的路由自己调 `retrieve().as_dict()`，这个函数在仓库里没有调用点 |
| `all_user_ids` | `all_user_ids() -> list[str]` | `SELECT DISTINCT user_id FROM memories`；视图没建好时返回 `["0"]` |
| `get_memory` | `get_memory(mid: int) -> dict[str, Any] \| None` | 单条读出，已遗忘/被取代/被合并一律返回 `None`（HTTP 层据此给 404） |
| `search_memories` | `search_memories(q: str = "", kind: str \| None = None, limit: int = 50) -> list[dict[str, Any]]` | 有 `q` 时先扫 `max(limit, 200)` 条再在 Python 里做子串包含过滤（SQL 里没有 LIKE） |
| `recent_memories` | `recent_memories(limit: int = 5) -> list[dict[str, Any]]` | 最近记忆展示位：`quote` 全部后置到最后 |
| `memory_stats` | `memory_stats() -> dict[str, Any]` | `{"total": n, "by_kind": {...}}` |
| `stats` | `stats() -> dict[str, Any]` | 面板四个数：`{"total","hot","warm","cold","pinned"}`，按引擎温度分层统计，`pinned` 单独再数一遍 |
| `counts_for_user` | `counts_for_user(user_id: int) -> dict[str, Any]` | 某个人的 `total` + `by_kind` |
| `list_for_user` | `list_for_user(user_id: int, limit: int = 50) -> list[dict[str, Any]]` | 控制台用，按 `created_at` 倒序 |
| `delete_memory` | `delete_memory(mid: int) -> bool` | 软删除单条（`reason="manual"`）；已非活跃返回 `False` |
| `forget_user` | `forget_user(user_id: int) -> int` | 只清这个人的分区（`reason="user_reset"`），返回条数 |
| `forget_all` | `forget_all() -> int` | 全库软删除（`reason="reset"`），返回条数 |
| `maybe_tick` | `maybe_tick(force: bool = False) -> dict[str, Any] \| None` | 最多每小时一次 `lifecycle().tick(merge=False)`；成功写 `kv["memory_engine_last_tick"]=now` |
| `engine_stats` | `engine_stats() -> dict[str, Any]` | 直通 `memengine.stats()`，只留 `schema_version/events/items/items_active/cues/recall_logs/distinct_users/db_bytes` |
| `export_archive` | `export_archive(path) -> str` | `store.export_file(path)`，引擎原生格式，可被 standalone 项目直接 import |
| `import_archive` | `import_archive(path, mode: str = "merge") -> dict[str, Any]` | `store.import_file(path, mode=mode)`；`mode` 只接受 `merge` / `replace` |
| `health` | `health() -> dict[str, Any]` | 自检：`ok = schema_version >= 2`，附 `db` 路径与五项计数 |

`maybe_tick()` 的 `tick(merge=False)` 是本层一个刻意选择：引擎 `tick()` 本来是"衰减 → 合并 → 遗忘"三段（`lifecycle.py:405`），这里把合并关掉，冷合并只由 `cold.py` 显式触发。原因写在 `cold.py` 头部：引擎自带的 `merge_cold` 走 `meta["merged_summary"]` 那套，而本项目要的是"摘要进 `memories` 视图、源条目 `merged_into` 指向摘要"的可还原链路，所以两套合并只启用一套。

**关键实现里的坑（真实存在）**：
- `add()` 的 `stability` 参数是死参数：函数体里零引用，`mem_item` 表也没有这一列，`to_old()` 里恒定输出 `0.5`。调用方传 `1.0` 不会有任何效果。
- `DECAY_PER_DAY = 0.97` 是旧口径遗留：真正的衰减量在 `vendor/symvolve_memory/decay.py` 的 `HEAT_DECAY_PER_DAY = 0.04`（线性），不是 `0.97`（指数）。改衰减去改 vendor 只会被下一版覆盖，正确位置是 `DecayPolicy`。
- `add()` 命中重复时的加温是 `max(heat, 0.90) + 0.1`，最高只到 `1.0`——重复写同一条记忆**不会**把它顶成 pinned，也不会改变 `first_seen`。
- `_find_duplicate()` 用 `==` 全等比较正文、只扫 `list_items()` 的默认页（无 `limit`，引擎全量）。同义不同字的记忆会重复存两条。
- `retrieve()` 里 `chat_type` 空串被映射成引擎的 `None`（不限场合），而 `_hot_from()` 拿到的分区值是 `None` 时同样是"不限"；但 `add()` 写入时 `chat_type=""` 是**真的空串分区**。读宽写窄，这是为了兼容旧数据，不是笔误。
- `effective_heat()` 传行对象时会为每行做一次 `store.get_item()`，面板批量渲染时是 N+1 查询；`_rows()` 已经用 `cues_map()` 批量预取线索，但热度仍然是逐条算的（纯内存计算，不打库）。

### 3.2 `app/memory/store.py`（160 行）

**职责**：两个完全不同的部分缝在一个文件里。上半部分是 `events` 表的**真身**（记忆系统之外唯一还在直接写业务表的地方），下半部分是 7 个转调 `h2` 的**薄壳**。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `PRIORITY_KINDS` | `("boundary", "promise")` | 优先级种类常量，本文件内无引用（被 `prompt.py` 的标签表间接体现） |
| `now_iso` | `now_iso() -> str` | 本地时区 ISO 时间串（`timespec="seconds"`），全仓 3 处在用（`mind/models.py`、`relation/engine.py`、`compress.py`） |
| `add_event` | `add_event(role: str, text: str, source: str = "panel", kind: str = "text", meta: dict \| None = None, user_id: int = 0, chat_type: str = "", group_id: str = "", ts: str \| None = None) -> int` | 写一条 `events`；`ts` 只在测试/补发时传 |
| `recent_events` | `recent_events(limit: int = 40, chat_type: str = "", user_id: int = 0) -> list[dict[str, Any]]` | 最近事件，返回前 `reversed()` 成正序 |
| `events_between` | `events_between(after_id: int, upto_id: int, chat_type: str = "", user_id: int = 0) -> list[dict[str, Any]]` | 按 id 区间取原文段，工作记忆压缩用它 |
| `count_events` | `count_events() -> int` | `SELECT COUNT(*)` |
| `add_memory` | `add_memory(kind: str, content: str, importance: float = 0.6, stability: float = 0.5, source_ids: list[int] \| None = None, pin: bool = False, user_id: int = 0, chat_type: str = "") -> int` | 转发 `h2.add()` |
| `get_memory` / `delete_memory` / `search_memories` / `memory_stats` / `recent_memories` / `mark_recalled` | 见 `h2` 表 | 逐个转发，签名与 `h2` 一致 |

**关键实现**：
1. `recent_events` 与 `events_between` 的场合过滤是同一段逻辑：`chat_type == "group"` 时收 `chat_type='group'` **或**（`chat_type=''` 且 `group_id` 非空）的老数据；其它场合收 `chat_type=?` 或（`chat_type=''` 且 `group_id` 为空）。老数据的场合信息记在 `group_id` 上，这是升级兼容，不是新设计。
2. 私聊再叠一层 `user_id = ?` 隔离；群聊不按 `user_id` 过滤（群聊原文是公共的）。
3. 7 个薄壳全部**函数内**`from app.memory import h2`，不是模块顶层 import。这样 `store` 被业务代码导入时不会连带拉起引擎（`h2` 顶层 import 会触发 `memengine` → 打开 `life.db`）。

**数据结构 / 存储**：写 `events`（`ts/role/kind/text/source/meta` 五列 DDL + `user_id/chat_type/group_id` 三列由 `db.py` 补列迁移加上）。

**耦合**：被 `app/mind/loop.py`、`app/group/*`、`app/life/*`、`app/api/*`、`compress.py`、`ingest.py` 调用；内部转发到 `h2`。

**坑**：
- ~~`add_memory()` 默认 0.5、`h2.add()` 默认 0.6~~ **已统一（2026-11-01）**：`add_memory()` 默认改成 0.6，两个入口不再有两种默认重要度（docs/23 §3.2 第 8 条）。
- `_to_dict()` 只把 `source_ids` 这一列从 JSON 串解成列表（`events` 表并没有这列，是历史遗留的兼容分支），其余列原样。
- 文件头的注释说"这里的函数是老调用点的薄壳，签名保持不变"，但 `add_memory` 的实际签名比旧版少了 `extra_meta`——需要 `extra_meta` 的调用点（`app/life/thoughts.py`）直接用了 `h2.add()`。

### 3.3 `app/memory/memv2.py`（292 行）

**职责**：把旧 `memories` 表搬进引擎，把表改名留档，再建一个同名只读视图。`app/core/db.py:362` 每次启动都调 `memv2.ensure_ready()`，所以它必须幂等。

**常量**：`LEGACY_TABLE = "memories_legacy_20261030"`、`VIEW_NAME = "memories"`、`KV_KEY = "memory_engine_migrated_at"`。

**视图 SQL 原文**（`memv2.py:32` 的 `_VIEW_SQL`，与 `docs/17` §2.39 一致）：

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

**过滤条件**是三个 `IS NULL` / `= 0`：`forgotten = 0`（没被遗忘）、`superseded_by IS NULL`（没被新事实取代）、`merged_into IS NULL`（没被冷合并进摘要）。三条同时成立才算"她现在还记得"。这与 `MemoryItem.is_active` 完全等价。注意视图**不**过滤 `clarity` 也不过滤 `heat`：`trace` 与 `0.0` 热度的条目照样可见，筛选交给召回。

**旧表归档成 `memories_legacy_20261030` 的过程**（`ensure_ready()`，`memv2.py:215`）：
1. 用独立 `sqlite3.connect()` 探测：`memories` 是 `table` 还是 `view`、引擎表 `mem_item` 是否已存在、`table` 状态下有多少行。
2. **快速返回**：已经是 `view` 且引擎就绪且没传 `force` → `{"ok": True, "state": "ready"}`，什么都不做。
3. `dry_run=True` → 只返回 `memories_kind / legacy_rows / would_backup`，不写库。
4. `_backup()`：整库复制成 `life.db.before-memv2`（连 `-wal`/`-shm` 一起拷；已有备份就不覆盖）。
5. `memengine.store()` —— 引擎的迁移脚本在这里跑完，`mem_item / mem_cue / mem_event / mem_recall_log / mem_render_cache` 建好。
6. `_migrate_rows()`：`SELECT * FROM memories ORDER BY id` 逐行搬。
   - 空正文跳过（计入 `skipped`）。
   - `_already_there()` 按 `(user_id, chat_type, kind, text)` 全等查 200 条，命中就跳过——这就是"搬到一半断了，重跑能接着搬"的幂等来源。
   - 时间：`first_seen/last_seen/last_recall` 用 `_epoch()` 从 ISO 串解回 epoch 秒，解不出来按 0 处理，`created_at` 兜底为 `now`。
   - `clarity` 用 `decay.clarity_for_age(days)` 按 `last_seen` 到今天的真实天数重算（旧表没有 `clarity` 列）。
   - 逐行 `append_event()` → `insert_item()` → `add_cues()`（线索类型统一标 `content`，`weight` 统一 `1.0`）。
   - `meta` 打三样：`{"kind": kind, "legacy_row_id": 旧id, "legacy": True}`，有旧 `source_ids` 再补 `ai_life_source_ids`。
7. `_archive_table()`：`ALTER TABLE memories RENAME TO memories_legacy_20261030`；名字已被占就依次试 `_2`、`_3`……
8. `_create_view()`：`DROP VIEW IF EXISTS memories` 后 `executescript(_VIEW_SQL)`；如果探测到 `memories` 还是 `table`，直接 `raise RuntimeError`（提示先跑迁移、否则会撞上同名对象）。
9. 写 `kv["memory_engine_migrated_at"]`（失败只吞异常，不影响记忆可用），返回 `engine_items` 计数。

`status()` 是只读诊断：返回 `db / memories_kind / view_rows / legacy_table / engine_ready`，引擎就绪时再附 `schema_version/items/items_active/events/cues/recall_logs`。

**为什么改名而不是删表重建**：文件头写得很直白——老表是唯一的历史档案，改名留档 + 视图 = 读得到、写不进、随时能对账。

**坑**：
- `_create_view()` 里 `DROP VIEW IF EXISTS` 之后立刻 `executescript`，中间没有事务包裹（SQLite 的 DDL 隐式提交）；进程在两句之间被杀会留下"没有 `memories` 这个名字"的库，下次启动 `kind == ""`，走 `view_created` 分支重建，能自愈。
- 迁移不搬 `pinned` 之外的"阶段/关系"信息；旧表的 `importance` 是 0–1 浮点，乘 100 后过 `clamp_importance()` 夹到 1–100，`0.004` 这种极小值会变成 1 而不是 0。
- `_migrate_rows()` 里的 `con.row_factory = sqlite3.Row` 改的是传入连接工厂，跑完之后该连接一直是 `Row` 工厂（`ensure_ready()` 之后没有人再用这条连接，所以无害）。
- `ensure_ready(*, parent=None, backup: bool = True, dry_run: bool = False, force: bool = False)` 的 `parent` 形参在函数体内零引用，是死参数；`backup=False` 才会跳过整库备份，而默认 `True` 意味着**每个老库升级时都会多出 3 个 `.before-memv2*` 文件**。

### 3.4 `app/memory/facts.py`（142 行）

**职责**：让"记住你"真的发生。模型从对话里挑出关于用户的、以后还用得上的具体事实，交给 `h2` 存。文件头一句是这层的纲领：抽不出来就不存，噪音比空白更伤。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `maybe_extract` | `async def maybe_extract(rt, seq: int = 1, user_id: int = 0, chat_type: str = "") -> int` | 到点就抽一次，返回新增条数 |
| `_clean`（私有） | `_clean(items: list) -> list[str]` | 过滤模型输出 |
| `_scrub_openids`（私有） | `_scrub_openids(text: str, names_by_openid: dict[str, str]) -> str` | 把残留 openid 换成名字 |

**触发点（每 6 条用户消息一次）**：
1. 先数用户消息条数：`user_id > 0` 时 `SELECT COUNT(*) FROM events WHERE role='user' AND user_id=?`，否则数全部 `role='user'`。
2. `if not user_count or user_count % 6 != 0: return 0` —— **只有条数正好是 6 的整数倍才抽**。这是"每 6 条一次"的实现方式：不记游标，用总数取模，天然幂等，重启也不会漏。
3. 取最近 14 条事件（`ORDER BY id DESC LIMIT 14`）；`user_id > 0` 时只取 `(user_id=? OR user_id=0)`（本人 + 共用），再叠 `chat_type=? OR chat_type=''`（本场合 + 不分场合）。

**有多少条上限**：`_SYSTEM` 里的指令写了"最多 2 条，每条 30 字以内"；`_clean()` 有独立的三道闸——长度 `4 <= len <= 40`、整句等于 `用户/对方/他/她` 的丢掉、以 `用户说/对方说/用户是/他是一个` 开头的丢掉，最后 `out[:2]`。`max_tokens=160`，`thinking=False`。

**取名顺序（QQ 昵称 → kv `nick:<uid>` → 空）**：
```python
for r in db.rows("SELECT id, openid, nickname FROM qq_user"):
    nick = str(r["nickname"] or "").strip()
    if not nick:
        nick = str(db.kv_get(f"nick:{int(r['id'])}", "") or "").strip()
    names[int(r["id"])] = nick
    nicks_by_openid[str(r["openid"] or "")] = nick
```
三级：`qq_user.nickname` 优先；空则读 kv `nick:<qq_user.id>`（用户自己设的称呼）；仍为空就是空串。拼对话时 `_label()` 把空名字兜底成 `"对方"`。文件里有一句注释把动机写清楚了："绝不用 openid 当名字：官方通道拿不到昵称时会退化成 `49BEBF…明天有面试` 进长期记忆"。

`_label()` 的完整判定：`role == "her"` → `"你"`；`user_id` 在"自己"集合（`qq_user.openid == cfg.onebot.self_id`）里 → 空串（只保留正文）；其余 → 名字或 `"对方"`。

**`_scrub_openids` 的替换逻辑**：
```python
out = str(text or "")
for oid, name in names_by_openid.items():
    if oid and oid in out:
        out = out.replace(oid, name or "对方")
return out.strip()
```
遍历 `openid → 名字` 映射，命中就整串替换；名字为空时换成 `"对方"`，保持句子通顺（"对方明天有面试"而不是"49BEBF…明天有面试"）。**注意**：生产路径里这段逻辑是**内联写在 `maybe_extract()` 里**的（`facts.py:131-136`，同样的循环、同样的 `name or "对方"`），`_scrub_openids()` 本身在 `app/` 下没有调用点，只有 `scripts/adv_regress.py:190` 拿它做回归断言。

**写入**：逐条 `h2.find_by_content(text, user_id, chat_type)` 去重（同分区同正文就跳过），然后 `h2.add("fact", text, importance=0.62, user_id=user_id, chat_type=chat_type)`。`importance=0.62` 是硬编码——高于默认 `0.6`，低于冷启动写入的 `0.8`。

**数据结构 / 存储**：读 `events`、`qq_user`、`kv`；写经 `h2.add()` 进 `mem_item/mem_cue/mem_event`。模型档位走 `app.mind.models.pick("summary")`，用量记 `models.record(spec.key, "summary", ...)`。

**耦合**：`app/mind/loop.py:834` 用 `asyncio.create_task()` 起它（不阻塞回复）；内部调 `rt.llm.chat`、`h2.add`、`h2.find_by_content`。

**坑**：
- `_NOISE`（第 23 行）定义了 8 个词却没有任何引用；真正生效的是 `_clean()` 里手写的两个元组。改 `_NOISE` 不会有任何效果。
- 整个函数体被 `try/except Exception: return 0` 包住（模型调用部分），任何解析失败都静默返回 0，日志里看不到原因。
- `user_count % 6` 用的是**全库累计条数**，不是"上次抽取之后新增的条数"；同一个用户每次 +6 才抽一次，但如果库里已经有 5 条，第 1 条新消息就会直接触发抽取——触发时机在库龄不等于 6 的倍数时会偏移。
- `items = _clean(json.loads(match.group(0)))` 用正则 `\[.*\]` 抠 JSON 数组；模型输出里带第二个方括号（例如示例正文）时，`json.loads` 会抛异常，被外层 `except` 吞掉整批。

### 3.5 `app/memory/extract.py`（220 行）

**职责**：没有模型时的兜底抽取（规则版）、从历史记录反推初始好感（冷启动）、以及"第三方痕迹"污染扫描。三个功能互不依赖，共用一个文件只因为它们都属于"导入侧的工具"。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `ExtractedItem`（dataclass） | `kind / content / importance` | 一条抽出来的条目 |
| `ExtractResult`（dataclass） | `items / quotes / stats` | 一次抽取的结果 |
| `heuristic_extract` | `heuristic_extract(events: list[dict[str, Any]], persona: Persona) -> ExtractResult` | 规则抽取，无 API Key 也能跑 |
| `cold_start_from` | `cold_start_from(events: list[dict[str, Any]], persona: Persona) -> dict[str, Any]` | 反推初始 `affinity/trust/attraction` |
| `scan_contamination` | `scan_contamination(events: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]` | 报可疑第三方痕迹，只报不删 |

**`heuristic_extract()` 的规则表**（`_FACT_PATTERNS`，7 条，权重是该项的 `importance`）：

| 正则 | 权重 | 产出 |
|---|---|---|
| `我叫\s*([\u4e00-\u9fffA-Za-z]{1,8})` | `0.95` | `fact`（原文） |
| `我是\s*([^。，,；;！!？?\n]{2,14})` | `0.70` | `fact` |
| `我今年\s*(\d{1,3})\s*(?:岁\|了)` | `0.85` | `fact` |
| `我住在\s*([^。，,；;！!？?\n]{2,14})` | `0.80` | `fact` |
| `我在\s*([^。，,；;！!？?\n]{2,16}?)\s*(?:工作\|上班\|上学)` | `0.75` | `fact` |
| `我的\s*([\u4e00-\u9fff]{1,6})\s*叫\s*([\u4e00-\u9fffA-Za-z]{1,10})` | `0.85` | `fact`，两个捕获组时拼成 `他的{g0}叫{g1}` |
| `我养了\s*(?:一只\|一个\|条)\s*([^。，,；;！!？?\n]{2,12})` | `0.75` | `fact` |

偏好走另一条正则 `_PREF_RE`：`我[^。，,；;！!？?\n]{0,8}?(不喜欢|最怕|怕|喜欢|讨厌|受不了|习惯)\s*(...)`，动词权重表 `_PREF_WEIGHT` = `不喜欢 0.85 / 最怕 0.92 / 怕 0.88 / 喜欢 0.70 / 讨厌 0.85 / 受不了 0.80 / 习惯 0.60`，产出 `preference`（正文是"动词+宾语"，例如"不喜欢香菜"）。

另外两条提示词表：`_BOUNDARY_HINTS` = `别再/不要提/不许/别叫我/别提/忌讳/底线`（命中即整句存成 `boundary`，`importance=0.95`，取前 50 字）；`_DEEP_HINTS` = `我想过/其实我/说实话/从来没人/第一次跟人说/我从来没`（存 `event`，`0.8`，取前 56 字）；最后一条兜底——长度 ≥16 且含 `昨天/今天/上周/那次/记得` 之一，存 `event`，`0.6`。

`role == "her"` 或 `who` 在 `her_names` 集合（`persona.name` 加 `她/TA/ta/ai/AI/助手/bot`）里的句子进 `quotes`：长度 `4..42`、不含 `http`、去引号后不重复。最终 `items[:60]`、`quotes[:80]`，`items` 按 `importance` 降序。

**`cold_start_from()` 的算式**（`n` = 事件数，`user_n` = 用户发言数）：
- `base = min(52.0, 6.0 + 7.0 * log2(1 + n/60))`
- `night_bonus = 22.0 * (凌晨 0–4 点发言占比)`
- `intimate_bonus = min(18.0, 14.0 * (亲密称呼条数 / max(1, user_n)))`，亲密词表 `宝宝/老婆/老公/爱你/想你/亲亲/抱抱`
- `length_bonus = min(6.0, 平均字数 / 12.0)`
- `raw = base + night_bonus + intimate_bonus + length_bonus`，`affinity = round(min(72.0, raw), 1)`（迁移上限 72，一次导入不把关系点满）
- `trust = round(min(90.0, affinity*0.78 + 8.0), 1)`
- `attraction = round(min(85.0, affinity*0.48 + intimate_bonus), 1)`
- 返回里带 `reason`（"共 N 条记录，跨 M 天" + 凌晨占比 + 亲密次数 + 是否封顶）、`samples`、`clipped`、`raw_affinity`。

**`scan_contamination()`**：只扫前 20000 条。拉丁词用 `(?:跟|和|对|找|问|叫|说|帮)\s*([A-Za-z][A-Za-z0-9_]{1,7})` 抓，剔掉 28 个常见词（`ai/ok/hh/app/api/id/qq/wx/vx/wifi/cpu/gpu/ios/pc/tv/bgm/cp/dd/gg/mm/emmm/emm/bo/nm/sb/yyds/emo/vlog`），出现 ≥2 次才报；人名用 `(?:^|[，。！？\s…~～])(?P<n>[\u4e00-\u9fff]{2,4})(?=啊|呀|呢|哈)` 抓"称呼语气"，首字在 `_SURNAMES`（79 个字的姓氏集合）里、不含 `的了是我你他她它`、次数在 `2..40`、且不在 `_NAME_STOP`（`何必/何况/何止/马上/于是/周围/周末/周期/安排/安全/常规`）里才报。结果按次数降序截 `limit=6`。

**耦合**：`ingest.py` 三个函数全用（`heuristic_extract` 只在非语料型文件上跑、`cold_start_from` 决定是否改写关系、`scan_contamination` 只进 warning）。

**坑**：
- `scan_contamination()` 的注释写"不自动删"，`ingest()` 也确实只把它拼进 warning 字符串。
- `_SURNAMES` 与 `scrub.py` 的姓氏集合是**两份独立的拷贝**（`extract.py:164` 与 `scrub.py:102`），内容不同：`extract.py` 那份 79 个字（2 行），`scrub.py` 那份 157 个字（5 行）——后者多出的部分是补充姓氏。改姓氏表要改两处。
- `heuristic_extract()` 对 `role` 的判定是 `role == "her"` **或** `who` 在 `her_names` 里——`who` 字段只有 `formats.py` 的解析器会填，`parse_plain()` 填的 `who="我"` 永远进不了 quotes 分支。
- `cold_start_from()` 读 `e.get("ts")`，导入管线里 `ts` 在 `_fill_timestamps()` 之前是 `None`，此时该条不计入"凌晨"统计。

### 3.6 `app/memory/cold.py`（226 行）

**职责**：两个月前的一条条零碎记忆留着占检索位、也不该被完整记住。按"时间 + 主题"合并成一条概要，保留线索、保留溯源。**软合并**：源条目的行不动，只打 `merged_into`。

**常量**：`COLD_AGE_DAYS = 30`、`MIN_GROUP = 3`、`PROTECTED_KINDS = h2.PROTECTED_KINDS`（即 `("boundary", "promise", "quote")`）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `candidates` | `candidates() -> list[dict[str, Any]]` | 挑出可合并的冷记忆（旧行格式），按 id 升序 |
| `group` | `group(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]` | 按四键分桶，丢弃少于 3 条的小组 |
| `compact_cold` | `async def compact_cold(rt, persona, dry_run: bool = False) -> dict[str, Any]` | 执行一次合并，返回明细 |
| `restore` | `restore(merge_id: int) -> dict[str, Any]` | 把一次合并还原 |
| `stats` | `stats() -> dict[str, Any]` | `{"cold_candidates", "groups", "merges"}` |

**准入的四个条件**（`candidates()`）：未钉住；`kind` 不在 `PROTECTED_KINDS`；`to_old(item)["heat"] < h2.COLD_THRESHOLD`（即 `< 0.12`）；`first_seen` 到今天的年龄 `>= 30` 天。

**分组用的是四键**（`group()`）：`(first_seen 的前 7 位即"年-月", kind, user_id, chat_type)`。三个维度同时对齐——同月、同类型、同一个人、同一个场合才合并。跨人的记忆绝不合并（注释原话）。

**合并流程**（`compact_cold()`）：
1. `candidates()` → `group()`；没有组就直接返回 `{"ok": True, "groups": 0, "merged": 0, "freed": 0, "detail": []}`。
2. 每组摘要：`_llm_summary()` 优先——`cfg.provider.is_mock` 为真时返回 `None`，否则把组内前 8 条正文发给 `models.pick("summary")`，`max_tokens=120`，取回后 `[:120]`；失败或 mock 走 `_rule_summary()`：按 `importance` 降序取前 3 条、每条截 34 字、以 `；` 连接、总长 `[:110]`。
3. `h2.add("event", summary, importance=max(组内), cues=_merge_cues(g), source_ids=[源 id...], user_id, chat_type)` —— 摘要条目**也是走唯一写入口**进来的，不是手搓 SQL。
4. 摘要条目再补两笔：`heat = min(1.0, max(0.05, 组内最大 heat))`、`clarity = "fuzzy"`。
5. 源条目逐条 `item.merged_into = new_id` 后 `update_item()`；已经 `pinned` 的跳过（第二道保险）。
6. 写 `memory_merges`：`(ts, session_seq, summary_id, source_ids, sources)`，`sources` 是 `[{"id","kind","content"}...]` 全量快照——这是"能还原"的真凭据，不是只存 id。
7. 返回 `{"groups", "merged", "freed", "detail"}`，`freed = 被合并条数 - 摘要条数`，`detail` 只给前 12 组。

**`restore(merge_id)`**：读 `memory_merges` 行 → 逐个源条目 `merged_into = None` → `lifecycle().forget(summary_id, reason="merge_restore")` 软删摘要 → `DELETE FROM memory_merges WHERE id=?`。因为源条目从来没被删，热度、召回次数、线索一个不丢——这正是文件头说的"跟旧实现的区别"：旧版是"插摘要 → 删原条目 → 还原时重插"的硬删除。

**触发方**：`app/cli.py:259`（`cli.py` 的冷压缩命令）与 `app/api/routes.py:468`（`/v1/dev/memory/compact-cold`，带 `dry` 参数）。`h2.maybe_tick()` 走的是 `tick(merge=False)`，所以引擎自带的 `merge_cold`（准入线 `60 天 / heat ≤ 0.15 / importance ≤ 45`）在本项目里**从不执行**。

**数据结构 / 存储**：读 `mem_item/mem_cue`（经 `h2.to_old`）；写 `mem_item`（摘要行 + 源行 `merged_into`）、`memory_merges`（`ts/session_seq/summary_id/source_ids/sources`）；读 `session.seq`。

**坑**：
- `_current_seq()` 读 `session WHERE id = 1`，拿不到就返回 1——多会话库里这个字段只记主会话的序号。
- `dry_run=True` 时也会跑 `_llm_summary()`（真花钱），只是不落库。
- `restore()` 只按 `sources` 里的 id 还原 `merged_into`，如果某条源条目后来又被手动遗忘（`forgotten=1`），还原会把 `merged_into` 清掉但 `forgotten` 仍然是 1，它不会回到视图里。
- 合并后摘要条目的 `kind` 是 `"event"`，与 `extract.py` 抽出来的 `event` 混在同一桶里；靠 `meta["kind"]` 与 `source_ids` 区分来源。

### 3.7 `app/memory/compress.py`（219 行）

**职责**：把"聊过的"折成**线索**，不折成事实。摘要只记三件事：在聊什么、有什么没聊完、她现在的态度。事实不写摘要——那是 `h2` 的活，重复存两处早晚对不上。

**常量**：`SUMMARY_LEVEL = 1`、`_UNRESOLVED`（11 个未完成信号词：`下次/明天/回头/等你/答应/以后/再聊/等我/改天/说好了/还没`）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `latest_summary` | `latest_summary(session_seq: int = 1) -> dict[str, Any] \| None` | 该会话最近一条 `level=1` 摘要 |
| `covered_to` | `covered_to(session_seq: int = 1) -> int` | 摘要已覆盖到哪个 `events.id`（含），没摘要返回 0 |
| `should_compact` | `should_compact(used_tokens: int, cfg, verbatim_count: int = 0) -> bool` | 要不要压 |
| `rule_summary` | `rule_summary(prev: str, folded: list[dict[str, Any]], persona, relation: dict[str, Any]) -> str` | 无模型时的结构化摘要 |
| `llm_summary` | `async def llm_summary(rt, prev: str, folded: list[dict[str, Any]], persona) -> str \| None` | 有模型时让她自己总结 |
| `compact` | `async def compact(rt, persona, relation: dict[str, Any], session_seq: int = 1) -> dict[str, Any]` | 执行一次压缩 |
| `reset` | `reset(session_seq: int = 1) -> None` | 删掉该会话全部摘要 |
| `status` | `status(session_seq: int = 1) -> dict[str, Any]` | `{covered_to, summary, updated_at, degraded}` |

**`should_compact()` 的两个条件，满足任一就压**：
1. `verbatim_count > cfg.context.verbatim_max` —— 未压缩原文条数超上限。注释说"这个才是日常真正会触发的"。
2. `cfg.context.mode == "ratio"` 时 `used_tokens > cfg.context.window * cfg.context.force_ratio`；否则 `used_tokens > cfg.context.trigger`。

**`covered_to` 的语义**：它是**已折进摘要的最后一个 `events.id`（含）**。`compact()` 里的推进方式是：
1. `keep = cfg.context.keep_recent`；`recent = store.recent_events(limit=keep)`。
2. `boundary = recent[0]["id"] - 1` —— 最近 `keep` 条之前的那一条 id，即"再往前都还没被覆盖"。
3. `start = covered_to()`；`boundary <= start` 直接返回 `{"ok": False, "reason": "没有新的可压缩区间"}`。
4. `folded = store.events_between(start, boundary)` —— 只折 `(start, boundary]` 这个开区间。
5. 写 `summaries` 行：`covered_to = boundary`、`period_start = folded[0].ts`、`period_end = folded[-1].ts`、`tokens_est = max(1, len(text)//2)`。
6. 只保留最近 5 条同会话摘要（`DELETE ... WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT 5)`），`covered_to` 一直往前推。

调用点是 `app/mind/loop.py:816`：`if compress.should_compact(bundle.used_tokens, rt.cfg, len(recent)): await compress.compact(...)`，整段包在 `try/except` 里——摘要失败不影响这一轮回复。

**`rule_summary()` 的输出结构**（3 行，`[:420]`）：
```text
在聊：<最多 5 条，用户发言截 18 字，旧线索在前>
没聊完的：<最多 3 条，含未完成信号词，截 24 字>
现在的状态：<情绪词>，关系在「<阶段 label>」
```
情绪词从 `session_mod.aversion_phase()` 的五档映射：`calm→情绪平稳 / uneasy→心里有点不舒服，但没说 / irritated→在烦 / cold→很冷 / ended→已经结束`。阶段 label 来自 `persona.stage_for(affinity)`。

**`llm_summary()` 的系统提示**（照抄关键约束）：只写三件事、不要写他的偏好/承诺/具体经历、200 字以内、第一人称、短句、不要写"用户""对话记录"。`max_tokens=320`，返回 `[:800]`。`is_mock` 为真直接返回 `None`。

**数据结构 / 存储**：`summaries`（`session_seq/level/period_start/period_end/text/covered_to/tokens_est/created_at`）；读 `events`（经 `store.events_between`）。

**坑**：
- `_parse_prev()` 只认 `在聊：` 与 `没聊完的：` 两个前缀；`llm_summary()` 产出的自由文本解析不出来，于是下一轮 `prev` 里的旧线索全丢——规则摘要与本模型摘要交替时会出现"线索忽然变少"。
- `status()` 里的 `degraded` 读的是全局 kv `memory_degraded`（由 `loop.py:813` 在"上下文被丢事件"时写），不是摘要自身的状态。
- `compact()` 的 `boundary` 用 `recent[0]["id"]`，而 `recent_events()` 不筛 `chat_type`——群聊和私聊的原文会一起被折进同一条摘要，`covered_to` 也是全局 id。

### 3.8 `app/memory/ingest.py`（225 行）

**职责**：一个 TXT 从"文件"变成"她能用的东西"的全部流程。文件头没写长注释，但常量把边界说清了：`ACCEPT_SUFFIX = {".txt", ".md", ".jsonl", ".json", ".log"}`、`MAX_QUOTE_MEMORIES = 120`、`EXTRACT_CHUNK_CAP = 300`、`CORPUS_FORMATS = {"scene_corpus", "style_library", "training_pairs"}`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `read_text` | `read_text(path: pathlib.Path) -> str` | 按 `utf-8-sig → utf-8 → gb18030 → utf-16 → latin-1` 顺序试解码，全失败用 `utf-8 + errors="replace"` |
| `chunk` | `chunk(events: list[dict[str, Any]], max_chars: int = 1400) -> list[list[dict[str, Any]]]` | 按累计字数切块，块上限 1400 字 |
| `ingest` | `ingest(path: pathlib.Path, persona: Persona, apply_cold_start: bool = True, max_events: int = 0, force: bool = False, extract_facts: bool \| None = None) -> dict[str, Any]` | 主流程 |

**完整流程**（编号即代码顺序）：
1. 文件不存在 → `raise FileNotFoundError`；后缀不在 `ACCEPT_SUFFIX` → 返回 `{"ok": False, "error": "不支持的文件类型：..."}`。
2. 算 `sha256(文件内容)[:16]`；`kv["ingest:<digest>"] == "done"` 且没传 `force` → 直接返回 `{"already_imported": True}`（幂等靠这个）。
3. `read_text()` 读全文。
4. `declare.parse(path, text, persona)` 得到 `Declared`（格式 + 谁是我/谁是她 + 钦定记忆 + 时间范围 + warning）。
5. 按 `decl.fmt` 分派解析器：`scene_corpus → parse_scene`、`style_library → parse_style_library`、`training_pairs → parse_training_pairs`、`memory_letter → parse_letter`、`generic_dialogue → parse_generic`、其余 → `parse_plain`。
6. `max_events` 截断（测试模式），warning 里记一句。
7. 既没有 events 也没有 quotes → 返回 `{"ok": False, "error": "没有解析出任何可用的对话或台词", "format_detected": ..., "declared": ...}`。
8. `_fill_timestamps()`：任何一条缺 `ts` 就以"现在 - 6 小时"为终点、按 45 秒一条倒推补齐；`len(ts)==16` 的补 `:00`。
9. `_check_time_conflict()`：声明了时间范围、而正文日期落在范围外 → 追加一条 warning，并且**按正文日期导入**（因为新鲜度权重看的就是它）。
10. `scan_contamination()`：有可疑残留就拼一条 warning（前 5 个 token）。
11. events 一次事务批量写：`INSERT INTO events (ts, role, kind, text, source) VALUES (?,?,?,?,?)`，`source="ingest"`。
12. **原话池**：`quote_counter` 先收 `parsed.quotes` 的 `(文本, 频次)`；再遍历 events 里 `role=="her"` 的句子，长度 `4..42`、不含 `http`、结尾两字不含"了"的 +1。取 `most_common(120)`，逐条 `store.add_memory("quote", q, importance, 0.7)`，`importance = round(min(0.95, 0.5 + 0.45 * (1 - rank/len)), 2)`——排名越靠前分越高，第 1 名 0.95，末位 0.5。
13. **钦定记忆**：`parsed.remember` 里含 `别/不要/不能/忌讳/绝对` 之一的存成 `boundary`，否则 `fact`，`importance=1.0`、`stability=1.0`。
14. **规则抽取**：`do_extract = (decl.fmt not in CORPUS_FORMATS) if extract_facts is None else extract_facts`。语料型文件跳过（warning 说明"规则会把「不许」「我叫啥」记成回忆"）。非语料按 1400 字切块，最多处理前 300 块，每块 `heuristic_extract()` 后逐条 `store.add_memory()`。
15. **冷启动**：`cold_start_from(events)`；`apply_cold_start` 为真且当前 `affinity < stats["affinity"] * 0.6` 时才 `relation_engine.set_relation(...)`——只往上抬、不往下压。
16. 写 `kv["ingest:<digest>"]="done"`、`kv["last_ingest"]`（含 `path/at/events/format`）。
17. 返回 `ok/path/format_detected/declared/warning/events_parsed/memories_written/facts_written/quotes_collected/chunks/extract_facts/cold_start/contamination/samples`。

**"一个 TXT 被判定成什么"**（判定全在 `declare.detect()`，见 §3.9）：对白（`generic_dialogue`）、台词库（`style_library`）、训练对（`training_pairs`）、迁移信（`memory_letter`）、场景语料（`scene_corpus`）、普通文本（`plain`）。每种格式的解析器各产出什么，见 §3.10 的对照表。

**坑**：
- `_fill_timestamps()` 的补齐是"整批倒推"，不按 `parsed.events` 的顺序精确到条；同一秒内的多条会拿到不同时间（45 秒步长），语义上假装它们在半小时内说完。
- `chunk()` 的 `size` 只累加 `text` 长度，不把 `ts/role` 算进去，切出来的块实际语料量比 1400 字略多。
- 步骤 12 与 14 都写 `quote` / `fact`，但用 `store.add_memory()`（转发 `h2.add`）——导入侧不直接碰引擎，这点与 `cold.py`、`facts.py` 一致。
- `parsed.forget`（"不要记得的"）只用来追加一句 warning，没有任何"跳过"逻辑真的执行——那些内容本来就没被写进任何表，因为它只出现在迁移信的独立段落里。

### 3.9 `app/memory/declare.py`（209 行）

**职责**：判断一个 TXT 到底是什么，以及最关键的——确定"谁是我、谁是她"。文件头把动机写透了：语料里的"对方"是相对的，女主包里的"对方"是男主，男主包里的"对方"是女主；没有声明的解析器只能猜，猜错就是把两个人的口吻焊在一起。

**`Declared` 数据类字段**：`fmt`（默认 `"generic"`）、`her_labels: set[str]`、`me_labels: set[str]`、`target`、`her_name`、`me_name`、`time_start`、`time_end`、`remember: list[str]`、`forget: list[str]`、`notes: list[str]`、`warning: list[str]`；方法 `role_of(who) -> "her"|"me"|"unknown"` 与 `as_dict()`。

**格式判定 `detect()`（按顺序短路）**：

| 顺序 | 条件 | 判定 |
|---|---|---|
| 1 | 前 3000 字含 `ai-life/memory-letter`，或匹配 `[—─━]{2,}\s*给.{0,12}的信\s*[—─━]{2,}` | `memory_letter` |
| 2 | 后缀是 `.jsonl` / `.json` | `training_pairs` |
| 3 | 非空行前 300 行里，匹配"频次 + 两空格 + 台词"的行**超过 60%** | `style_library` |
| 4 | 前 50 行里有 `【…】` 场景行，**且** 第 2–60 行里有 `谁：内容` 行 | `scene_corpus` |
| 5 | 前 40 行里有"时间戳 + 谁：内容" | `generic_dialogue` |
| 6 | 前 40 行里有"谁：内容" | `generic_dialogue` |
| 7 | 都不匹配 | `plain` |

**解析顺序 `parse()`**：
1. 一层（所有格式共用，扫前 400 行）：`#` 开头 → 进 `notes`；`(?:训练)?目标[方]?\s*[：:]` → `target`；`来源\s*[：:]` 后跟日期区间（中文"从 A 到 B"或"A ~ B"）→ `time_start/time_end`。
2. 二层（仅迁移信）：在 `正文` 之前的头部里找 `写「X」的是你` → `her_labels`；`写「X」的是我` → `me_labels`；`你叫「X」` → `her_name`；`我叫「X」` → `me_name`；日期区间同上。
3. 三层：`target` 为空时看文件名前缀（`女主/男主/女方/男方`）。
4. `_resolve_labels()` 决定标签集：

| 情况 | her_labels | me_labels |
|---|---|---|
| 迁移信两套都写了 | 加 `persona.name`、`"她"` | 加 `"我"` |
| 只写了 her 一半 | 加 `persona.name` | `{"我","他","男主","男方","对方"}` |
| target 指向她 | `{target,"女主","女方","她",name,"你"}` | `{"对方","我","他","男主","男方"}` |
| target 指向他 | `{"对方","女主","女方","她",name}` | `{target,"男主","男方","他","我","你"}` + warning |
| 都没写 | `{"女主","女方","她",name}` | `{"我","他","男主","男方","对方"}` |

`role_of()` 先做精确匹配，再对每个标签做一次**子串**兜底（语料里偶见全角空格或别名）。

**`map_unknown()`**：`return "me"`——未知标签一律记成"我"。注释解释："不猜。宁可记成 me，也不要把陌生人的口吻当成她。"（`formats.py` 的两个解析器在 `role_of()` 返回 `unknown` 时也硬编码 `role = "me"`，并统计到 warning 里。）

**坑**：
- `_RE_SECTION`（`declare.py:29`）在本文件里没有任何使用点；真正用它切分节的是 `formats.py` 里同名的一份。
- `her_name` / `me_name` 解析出来之后**没有任何下游使用**（`formats.py` 的 `parse_training_pairs` 用的是 `d.target`），只在 `Declared` 字段里躺着。
- `target` 指向他时会追加一条 warning 劝换女主包，但**不中止导入**——语料照样按"他是用户、她是对方"导进去。

### 3.10 `app/memory/formats.py`（242 行）

**职责**：每种格式只做一件事——把文本切成 `{ts, role, text}`（外加 `who`）的线性序列，再顺手吐原话池。不做记忆抽取、不做时间推断。

**`Parsed` 数据类**：`events: list[dict]`、`quotes: list[tuple[str, int]]`、`remember: list[str]`、`forget: list[str]`、`warning: list[str]`、`stats: dict`。

**六个解析器各产出什么**：

| 解析器 | 输入形态 | `events` | `quotes` | 其它 |
|---|---|---|---|---|
| `parse_scene` | `【2026-03-14 深夜】` 场景行 + `谁：内容` | 每条一个事件，`ts` 来自场景行的日期 + 时段小时 | 无 | 连续的非 `谁：` 行追加到上一条正文；未知标签按 `me` 并记 warning |
| `parse_style_library` | `频次<TAB或空格>台词` | 无 | `(文本, 频次)`，长度 `2..60`，按频次降序 | 不合格式的行计入 `skipped` 并 warning |
| `parse_training_pairs` | 每行一个 JSON：`context/prompt` + `reply/response/completion` | `context` 每条一个 `me` 事件；`reply` 按换行拆成多个 `her` 事件 | 无 | `bad_lines` 计数；`ts` 全为 `None`（交给 ingest 补） |
| `parse_letter` | 迁移信（`--- 正文 ---`、`--- 说给你听的 ---`、`--- 不要记得的 ---` 分节） | 正文交给 `parse_scene` | 无 | `remember`/`forget` 取项目符号，长度 ≥4 |
| `parse_generic` | 有时间戳或无时间戳的 `谁：内容` | 有 `ts` 用行内时间戳（`/`、`.` 归一成 `-`，空格换成 `T`，16 位补 `:00`）；否则沿用场景行 `ts` | 无 | `who` 长度 ≤6 才当说话人；其余行并入上一条；首行无归属则造一个 `me` 事件 |
| `parse_plain` | 任意文本 | 每行一个 `me` 事件，`ts=None` | 无 | 不解析身份 |

`ts_from_when(when, seq)` 是把场景行里的人话时间转成时间戳的唯一入口：先用 `_RE_DATE` 找 `YYYY年/月/日`；小时从 `_HOURS` 映射取（`凌晨 2 / 清晨 6 / 早上 7 / 早晨 7 / 上午 9 / 中午 12 / 下午 15 / 傍晚 18 / 晚上 20 / 夜里 22 / 深夜 1`，默认 20）；分钟 `(seq * 3) % 60`，所以同一场景里的第 n 句落在 `3n` 分钟。

**坑**：
- `_blank_to_none(ts)` 是恒等函数（`return ts`），文件内无调用点。
- `parse_scene()` 的 `stats` 写成 `{"lines": len(lines), "speakers": dict(unknown) and unknown or {}}`，那个 `and/or` 表达式的效果等价于 `unknown or {}`，语义上没问题但读起来像笔误。
- `parse_generic()` 在 `p.events` 为空时把首个归属不了说话人的行造成 `{"ts": None, "role": "me", "who": "我"}`，这条会被 `ingest` 当成用户说的第一句话，进原话池不会，进规则抽取会。
- `parse_letter()` 的正文兜底：`sections["正文"]` 为空时，把所有非 `说给你听的/不要记得的/信到此为止` 的节拼起来当正文——顺序依赖 `dict` 插入序，即文件里节的先后顺序。

### 3.11 `app/memory/reference.py`（347 行）

**职责**：学"人是怎么相处的"，不碰"用户是谁"。与 `ingest.py` 是两条永不相交的数据流：`ingest.py` 写 `memories / relation`（**关于这个用户的事实**），`reference.py` 只写 `ref_turns / ref_quotes`（**关于人类行为的样本**）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `Arc`（dataclass） | `months: list[dict]` + `period_of(month)` / `table()` / `counts()` | 按月的情感阶段曲线 |
| `classify_stats` | `classify_stats(stats: dict[str, dict[str, Any]]) -> Arc` | 从月度统计定阶段 |
| `classify_arc` | `classify_arc(events: list[dict[str, Any]]) -> Arc` | 上面那层的便捷入口 |
| `ingest_reference` | `ingest_reference(path, her_name, names=None, max_events=0, force=False, min_mid=3, write_candidates=True, arc=None) -> dict[str, Any]` | 主流程 |
| `status` | `status() -> dict[str, Any]` | `{turns, quotes, periods}` |
| `sample_quotes` | `sample_quotes(limit: int = 12) -> list[str]` | 按频次取原话样本（`/v1/memory/reference` 路由用） |

**解析**（`_parse_file()`）：头部前 10 行里以 `#` 开头的注释行抽 `对方昵称：` 与 `导出账号：`；消息头用 `^(日期) (时间) (说话人)$` 匹配，头部之后的行累积成一条正文，遇到下一个消息头就 `flush()`。角色判定：`who == 导出账号` → `me`，其余 → `her`。系统消息（`你撤回了一条消息`、`对方撤回了一条消息`、`以下是新消息`、`以上是打招呼的内容`、`我通过了你的朋友验证请求...`、`你已添加了对方...` 共 6 条）直接丢掉。

**情绪分期**（`_month_stats()` + `classify_stats()`）：
- 每月统计 `n`（总条数）、`her`（她的条数）、`sweet`（含 11 个甜蜜词）、`strain`（含 20 个负面词）。
- 派生三列：`her_ratio`、`sweet_rate = sweet/n*1000`、`strain_rate = strain/n*1000`（千分比）。
- 阈值：`high = 70 分位的 sweet_rate`、`low_vol = 30 分位的月消息量`；"尾部月份"= 最后 `max(2, len//6)` 个月。
- 判定顺序：尾部月且（`strain_rate >= 6.5` 或 `messages < low_vol*0.6`）→ `break`；`sweet_rate >= high` 且 `> 0` → `honeymoon`；`messages < low_vol` 且 `strain_rate >= 4.5` → `cooling`；其余 `stable`。
- 标签表 `PERIOD_LABELS`：`honeymoon 热恋期 / stable 稳定期 / cooling 降温期 / break 破裂期 / unknown 未定`。

**写入前的清洗**（逐条过 7 道闸）：
1. `scrub()` 去敏（详见 §3.12）。
2. 结果为空或整条只剩 `{名字}` → 丢。
3. 整条是 `[...]` 占位符 → 丢（没有语料价值）。
4. 以 `[名片 / [转账 / [红包 / [文件 / [位置` 开头 → 整条丢（这类消息本身携带真实信息）。
5. 命中 `_SYSTEM_LINES` → 丢。
6. 打上 `period = arc.period_of(ts[:7])`。
7. 只写 `ref_turns (ts, role, text, period, source)`，`source` 是文件名。

**原话池**（`ref_quotes`）：只取 `role=="her"` 且 `period in ("honeymoon","stable")` 的句子；长度 `4..40`、不含 `{`、不重复（`len(set(t)) > 2`）、不是系统消息、不含 `_EXPLICIT_WORDS`（11 个露骨词）；`most_common(300)` 后 `INSERT OR IGNORE`，`period` 列统一写 `"honeymoon_stable"`。

**候选名单**（`_write_candidates()`）：把"高置信已替换"和"中置信待确认"两段写到 `data/name_candidates.txt`，头部注释说明"确认要删的，复制到 `data/denylist.txt` 或 `语料/jilu/denylist.txt`，然后加 force 重新导入"。

**返回值**里带：`events_parsed / turns_written / quotes_written / scrub / detected_names（前 15，high）/ suspected_names（前 15，mid）/ denylist / arc（逐月表）/ period_counts / warning / digest`。

**坑**：
- `her_name` 形参在函数体里**没有被使用**（角色判定只看"是不是导出账号"），传谁都不影响结果。
- `force=True` 时先 `DELETE FROM ref_turns WHERE source = ?`（按文件名删），但**不删** `ref_quotes` 里旧的原话——重导后旧原话还在池子里。
- `_MSG_HEAD` 要求"日期 时间 说话人"三者齐全；QQ 导出的某些版本只有"日期 时间"两段，这类行会被当成正文拼进上一条。
- `arc` 参数可以外部传入以跳过分期计算（面板先预览再导入时用）。

### 3.12 `app/memory/scrub.py`（198 行）

**职责**：把真实信息摘干净，只留"人怎么说话"。文件头把两条原则摆在一起："一段语气学不学得到，是精度问题；一个真实身份漏出去，是安全问题。"——所以规则只有一个方向：宁可多删。

**`_PATTERNS` 的 11 类正则**（顺序即替换顺序）：

| 名称 | 正则 | 替换成 |
|---|---|---|
| `wxid` | `wxid_[A-Za-z0-9_]+` | `{账号}` |
| `qq` | `(?<!\d)QQ\s*[:：]?\s*\d{5,12}`（忽略大小写） | `{QQ}` |
| `phone` | `(?<!\d)1[3-9]\d{9}(?!\d)` | `{手机}` |
| `idcard` | `(?<!\d)\d{17}[\dXx](?!\d)` | `{证件}` |
| `email` | `[\w.+-]+@[\w-]+\.[\w.]{2,}` | `{邮箱}` |
| `url` | `https?://\S+` | `{链接}` |
| `longdigit` | `(?<!\d)\d{6,}(?!\d)` | `{号码}` |
| `ip` | `\b\d{1,3}(?:\.\d{1,3}){3}\b` | `{地址}` |
| `money` | `(?<!\d)\d{4,}(?=\s*(?:块\|元\|万\|千))` | `{金额}` |
| `money2` | `[￥¥]\s*[\d.,]+` | `{金额}` |
| `wechat_id` | `(?:微信号\|微信号码\|weixin\|vx\|VX)\s*[：:]?\s*[A-Za-z][A-Za-z0-9_-]{3,}` | `{微信号}` |

**人名替换**：`names` 里的普通串按**长度降序**处理（长的先替换，避免"张三丰"被"张三"切坏）；长度 ≥2 且在正文里就换成 `{名字}`；`_KEEP` 里的 10 个占位符（`[图片] [语音] [视频] [表情] [动画表情] [文件] [位置] [链接] [红包] [转账]`）跳过不换。`names` 里以 `re:` 开头的条目当成正则编译后单独替换（给"只在当昵称时替换、当副词时保留"的上下文敏感词用）。

最后 `re.sub(r"(\{[^}]{1,6}\})+(?=\1)", r"\1", out)` 把连续重复的占位符压成一个，避免 `{号码}{号码}` 这种噪声进语料。

**人名识别（`detect_names()`）**：
- 高置信（直接替换）：`[名片]...备注：X` 命中 `_RE_NAMECARD` 且通过 `_plausible()` → `+5` 分；`(mua|亲亲|抱抱|叫|喊)X` 命中 `_RE_CALL_STRICT` 且 X 首字在 157 个字的姓氏集合里 → `+1`。
- 中置信（只报不改）：同上正则但首字不是姓氏 → 进 `mid` 计数，`>= min_mid`（默认 3）才进返回值。
- `_plausible()` 五条闸：长度 2–3；不在 `_COMMON_WORDS`（116 个高频词）/`_KEEP`/`_NAME_BLOCKLIST`（27 个"姓氏打头但其实是普通词"，如 `岳父/和好/安慰/平方/安全感/安静/平时/方向/方面/万一/于是/高兴/周围/安全/干净/方便/明天/礼貌/程度/颜色/江湖/黄昏/童年/白天/白天鹅/乌云/马上`）；不含 `的了是我你他她它们吗呢吧啊呀哦嘛`；不以"宝"结尾。

**`ScrubReport`**：`hits: dict[kind, n]` + `names: list[str]`，`total` 是 `hits` 之和，`as_dict()` 返回 `{total, by_kind, names}`。

**"宁可多删"在代码里的三处体现**：`_plausible()` 的否决条件远多于通过条件；只有姓氏打头与名片备注才自动替换，中置信一律只报；`anchors.is_usable()`（见 §3.13）把含 `{`/`}` 的句子整个丢掉——宁可少一条锚点，也不用一条带占位符的。

**坑**：
- `scrub_events()` 与 `suggest_names()` 在 `app/` 下没有调用点（`reference.py` 直接逐条调 `scrub()`），属于暴露给脚本的 API。
- `load_denylist()` 里 `import pathlib as _p` 之后又用 `"pathlib.Path | None"` 的字符串注解——注解是字符串所以不在运行期解析，但这行 `import` 是为它服务的。
- `_PATTERNS` 里 `longdigit`（`\d{6,}`）会先于 `idcard` 之外的所有纯数字匹配；顺序敏感，插新正则要放在合适位置。

### 3.13 `app/memory/anchors.py`（271 行）

**职责**：从真实语料里抽出"她会说的话"，存进 `anchors` 表，供 prompt 每轮取若干句当声纹参考。两条原则写在文件头：频次 + 分层并用（纯频次会把她压成"哈哈哈 + 服了"，纯随机又抓不到口头禅）；内容不过滤，但按关系阶段放行（脏话、亲昵、露骨的话都留着，只是还不熟时说不出来——"这不是审查，是节奏"）。

**场景表 `SCENES`（6 个桶）**：

| 场景 | 触发词（部分） |
|---|---|
| `亲密` | 宝宝、宝贝、老婆、老公、爱你、想你、亲亲、抱抱、mua、么么、乖 |
| `撒娇` | 好不好、求求、呜呜、不理你、要抱、要亲、贴贴、人家、嘛？、嘛?、嘛。、嘛~ |
| `生气` | 服了、无语、有病、滚、烦、气死、恶心、特码、妈了个、奶奶滴、别烦、不想说、随便你、bbll、焯、卧槽、草、裂开 |
| `关心` | 早点睡、别熬夜、喝口水、吃药、冷不冷、饿不饿、注意、照顾好、感冒、疼、休息、别硬撑、吃饭 |
| `自嘲` | 我笨、我好蠢、我疯了、我不行、救命、废物、我怀疑、我好菜、我裂开 |
| `深夜` | 睡不着、晚安、睡了、困、凌晨、梦、夜里 |

`scene_of()` 先查 31 个 `EXPLICIT_WORDS`（命中即 `亲密`），再按上表顺序匹配，都不中返回 `日常`——所以数据库里的 `scene` 取值共有 **7 种**（6 个桶 + `日常`）。

**"6 场景 × 4 句"的结构**在 persona 文件里：`persona/whale-chan.yaml` 的 `speech.anchors` 是 6 个场景（`日常 / 关心 / 被撒娇 / 不高兴 / 她自己 / 深层`）各 4 句。`persona.anchors(per_scene=1)` 会从每个场景取 1 句，作为**数据库锚点池为空时**的兜底（`prompt.py:295`）；数据库锚点池存在时优先用它。

**阶段闸门**：`STAGE_ORDER = ["observer","swimmer","echo","samefreq","lover","bond"]`（6 档）；`MIN_STAGE = {"intimate": 3, "explicit": 4, "profane": 2, "plain": 0}`；`SCENE_STAGE = {"日常":0,"关心":0,"深夜":0,"自嘲":1,"生气":2,"撒娇":3,"亲密":3}`。`stage_min_for(text, scene)` 取"场景下限"与"内容类型下限"的较大值——称呼类到 `同频者`（索引 3）解锁，露骨到 `恋人`（索引 4）解锁，脏话到 `回声`（索引 2）解锁。

**`build()`（重建整池）**：
1. `SELECT text, COUNT(*) c FROM ref_turns WHERE role='her' AND period IN ('honeymoon','stable') GROUP BY text ORDER BY c DESC LIMIT 40000`。
2. 逐条过 `is_usable()`（8 条否决：长度 `4..42`；含 `{}`；含 `http`；命中 16 个 `_REJECT_MARKERS`（`引用/转发/撤回/拒收/新消息/位置共享/已经结束/以下/[图片]/[表情]/[语音]/[视频]/[文件]/[名片]/[转账]/[红包]`）；`：` 出现 ≥1 次或 `:` ≥2 次；含 `他` 或 `她`（第三方指代，严格版核心禁令）；含两个连续空格；以 `比如/然后/所以/而且/但是/因为/如果/就是/还有/而不是` 开头）。
3. 按 `scene_of()` 分桶；每桶按频次降序取前 `min_per_scene=24` 条，再从剩下的里用 `random.Random(20261028)` 随机取 `sample_extra=40` 条——**频次与随机分层**就是"频次 + 分层并用"的落地。
4. `DELETE FROM anchors` 后批量 `INSERT OR IGNORE`，字段 `(text, scene, stage_min, explicit, profane, freq, enabled=1)`。
5. 返回 `candidates / written / by_scene / by_stage / explicit / profane / intimate` 六项统计。

**`quality()`（声纹质量分，`pick_*` 的排序键）**：
`log1p(freq)*0.8`；`len<6` 减 `1.2`；不同字符 ≤3 减 `2.0`；命中 `GENERIC`（34 条纯语气/纯提问，如"哈哈哈哈""在吗""好的""我也爱你"）减 `2.2`；长度 `7..24` 加 `0.9`；含 `我` 或 `你` 加 `0.7`，否则减 `0.6`；含 `，。？…！` 加 `0.3`；以 `。…？?` 结尾加 `0.2`。

**取样**：`pick_fixed(stage_index, n=10)` 跨场景轮流取（顺序 `日常/亲密/关心/撒娇/自嘲/生气/深夜`），并限制"笑声最多 2 条、问句最多 2 条"；`pick_dynamic(scene, stage_index, n=6)` 单场景按 `quality` 取前 n；`scene_for_text(text, mood=0)` 在 `mood <= -1` 且场景判定为 `日常` 时改判 `关心`。

**在对话里的用法**（`app/mind/loop.py:417`）：`pick_fixed(anchor_stage, 10) + pick_dynamic(scene, anchor_stage, 6)` 共 16 条候选交给 `prompt.build(anchors=...)`，prompt 侧再截 `[:6]`（`prompt.py:295`）。`app/life/proactive.py` 的两处主动消息用 `pick_dynamic(scene, stage, 2)`。

**坑**：
- `build()` 里的 `DELETE FROM anchors` 是整表清空重建，不带事务包裹（`db.executemany` 之后才提交），中途失败会留下空池，prompt 侧自动回落到 persona 兜底。
- `_stage_idx()` 对未知 `stage_key` 返回 0（陌生人档），拼错阶段名会静默降权。
- `pick_fixed()` 的 `while` 循环里 `laughs`/`questions` 计数在 `continue` 时不回滚，所以取不满 `n` 时会提前退出（`progressed` 为假就 `break`）。

### 3.14 `app/memory/style.py`（343 行）

**职责**：把 `data/human_life/_derived` 的语料压成"给 prompt 的一点点东西"。文件头把取舍写成了两条理由：原文成段塞进 prompt 又长又贵、且私聊原文本身就是用户隐私。

**读什么**（`FILES`，4 个 JSONL）：

| key | 文件名 | 内容 |
|---|---|---|
| `partner_snippet` | `partner.snippet.jsonl` | 伴侣相处片段（100 条量级，按 `note` 分"相爱语料"/"冲突样本"） |
| `partner_scene` | `partner.social_scene.jsonl` | 伴侣场景语料（分词切出来的，汉字间有空格） |
| `stranger_snippet` | `stranger.snippet.jsonl` | 陌生人/服务场景（60 条量级） |
| `csds_snippet` | `csds.snippet.jsonl` | 客服对话（60 条量级） |

目录默认 `DATA_ROOT / "data" / "human_life" / "_derived"`，环境变量 `AI_LIFE_STYLE_DIR` 优先；缓存 `CACHE_TTL_SECONDS = 60.0`。

**吐三样东西**：

| 函数 | 签名 | 输出规模 |
|---|---|---|
| `partner_examples` | `partner_examples(limit: int = 2, rng: random.Random \| None = None) -> list[str]` | 最多 `min(limit, 3)` 条，每条 ≤ `MAX_EXAMPLE_CHARS = 80` 字，单句 ≤ `MAX_LINE_CHARS = 40` 字 |
| `stranger_rules` | `stranger_rules() -> str` | 固定中文规则，`[:400]` |
| `service_note` | `service_note() -> str` | 一句"客服腔降权"提醒 |

**恋人 85/15 抽样**：`LOVE_WEIGHT = 0.85`、`FIGHT_WEIGHT = 1.0 - LOVE_WEIGHT`（注释写明"别写死 0.15：改 LOVE 时这里自动跟着变"）。每轮 `picker.random() < FIGHT_WEIGHT` 决定要不要一条拌嘴样本；拌嘴样本只出**她那一句**并标 `（偶尔闹别扭）`，相爱样本出 `她：「…」；他：「…」` 两侧。这个比例是策略不是巧合——防止越聊越苦。

**压成"一点点东西"的四道压缩**：
1. 只取 `messages` 里 `role == "self"` / `"other"` 的第一句（`_text_of()`）。
2. 把分词产生的汉字间空格去掉（`re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)`），否则 prompt 里是"我 今天 很 累"。
3. 单句截 `[:40]`。
4. 整行超 80 字时截断并补闭合引号，末尾加 `…`；重复行去重。

**显式/谐音样本的第二道闸**：`EXPLICIT_WORDS` 共 19 个短语——`做爱/性欲/裸/胸部/床事/乳房/内裤/亲热/开房/上床/湿了/硬了/叫床/大又粗/榨干/嘴巴酸/好涩/爆炒/香草泥`（末三个是谐音梗）。`_has_explicit()` 检查一条片段的**全部** messages，命中就整条丢弃（不是丢掉那一句）。注释写"语料管线也有一条同名单，这里是第二道闸"，且刻意避开了裸子串（不用"胸/下面/插/射"），免得误杀"胸口疼"。

**降级**：任何异常都不外抛——目录不存在返回全空结构（`ok=False`）、坏行只计入 `skipped`、单个文件读失败当空列表。`snapshot()` 同时给**原始条数**（`partner_love/partner_fight`）与**过闸后条数**（`partner_love_usable/partner_fight_usable`），注释提醒"面板别把能读当成能用"。

**调用条件**（`app/mind/loop.py:426`）：`is_lover` 且 `chat_type in ("private", "")` 才给 `partner_examples(2)`；`chat_type == "group"` 或（不是恋人且 `stage_idx <= 1`）才给 `stranger_rules() + service_note()`。群聊永远拿不到伴侣语气样本。

**坑**：
- `load_dir()` 用 `time.monotonic()` 做缓存时间戳、用目录绝对路径做 key；换目录后旧缓存会失效，但同一目录里**改文件**要等 60 秒才生效（`clear_cache()` 只给测试用）。
- `FIGHT_WEIGHT` 的注释是"改 LOVE 时这里自动跟着变"，但它是**模块加载时**算出来的常量，改 `LOVE_WEIGHT` 需要重载模块才生效。
- `_format_line()` 的引号闭合补丁只补 `」`，不补 `「`；极端截断下会产出 `她：「...…` 这种半截句子。

### 3.15 `app/memory/companion.py`（290 行）

**职责**：陪伴层接入。引擎侧（`vendor/symvolve_memory/companion/`）负责读情绪、做共振重排、给"要不要主动提旧事"的决定与冷却防复读；本模块只做宿主该做的事——把决定翻成一句 prompt 提示、给独处场景挑料、把冷却账本持久化到 kv。

**关键常量/状态**：`_LEDGER_KEY = "companion:ledger"`、`_POOL_DATA: dict[str, CompanionMemory]`（按用户一个门面）、`_LAST: dict[str, dict]`（最近一轮结果，进程内）、`_LEDGER_LOCK` / `_POOL_LOCK` 两把 `RLock`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `get` | `get(user_id: int \| str = 0) -> CompanionMemory` | 按用户取引擎门面（独立冷却/念头账本） |
| `hear` | `hear(text: str, user_id: int \| str = 0, chat_type: str = "") -> dict[str, Any]` | 听一句：情绪 → 召回 → 共振 → 决定，返回可 JSON 化结果 |
| `hint_for` | `hint_for(text: str, user_id: int \| str = 0, chat_type: str = "") -> str` | 把决定翻成一句 prompt 提示；没情绪/不该说返回空串 |
| `last_turn` | `last_turn(user_id: int \| str = 0) -> dict[str, Any]` | 最近一轮结果（进程内） |
| `idle` | `idle(user_id: int \| str = 0, limit: int = 3) -> list[dict[str, Any]]` | 独处时该想什么（只挑料，不生成文字） |
| `ruminate` | `ruminate(user_id: int \| str = 0, limit: int = 2, generator=None) -> dict[str, Any]` | 独处那一整轮：挑料 → 去重/限额 → 写回 |
| `save_ledgers` | `save_ledgers() -> int` | 账本写回 kv，返回字节数，失败返回 0 |
| `ledger_counts` | `ledger_counts() -> dict[str, int]` | `{bringup_entries, thought_writes}` |
| `status` | `status(user_id: int \| str = 0) -> dict[str, Any]` | 策略、冷却、24h 念头额度、最近决定、引擎版本 |
| `reset` | `reset(clear_persisted: bool = False) -> None` | 清进程内池；`clear_persisted=True` 连 kv 一起清 |

**`hint_for()` 的两段文案**（决定它"像人突然想起来"而不是"在背资料"）：`action == ASK` 时返回"你忽然想起一件旧事（…）。可以顺势问一句，像刚想起来，别像在背资料；不要提「记忆」「记录」这类词。"；否则返回"你可以主动提一句旧事（…），语气自然，说成你刚想起来的样子；不要提「记忆」「记录」这类词。"进 prompt 前先过 `_clean_fragment()`：去掉 `【记忆】`、只留最后一行、拆掉"标题：正文"的标题、压成单行、截 `[:90]`。

**触发点**：`app/mind/loop.py:371` 每轮调 `hint_for()`，整段包 `try/except`（注释：陪伴层是加料，不许拖垮聊天）。`hear()` 只在 `detect_emotion(text).charged` 为真时才真正跑引擎——情绪不浓的时候连召回都不做。

**持久化**：`_restore_ledgers()` 从 `kv["companion:ledger"]` 回放 `BringUpLedger.turn/entries` 与 `ThoughtLedger.window_seconds/daily_limit/entries`；坏数据（`KeyError/TypeError/ValueError`）逐段跳过，绝不让陪伴层拖垮主流程。`save_ledgers()` 在 `hear()` 与 `ruminate()` 之后各调一次。`reset()` 默认**不清 kv**，注释写明"等价于重启进程，冷却仍然记得"。

**坑**：
- `_POOL_DATA` 按用户 id 持有引擎门面，但 `_build()` 里所有门面共享同一个 `memengine` 单例与同一对账本对象——所以"每用户独立账本"其实只在 `BringUpLedger.entries` 里按 `user_id` 区分，账本容器本身是共享的。
- `_LAST` 是纯进程内 dict，重启后 `last_turn()` 返回 `{}`，而 `status()` 里的 `last_turn` 就靠它——面板重启后会短暂显示空。
- `get()` 每次都会走 `_pool()`，`_pool()` 首次会 `_build()`；`_build()` 里的 `CompanionMemory` 构造在引擎版本升级、参数变化时不重建（进程内缓存永不失效）。

### 3.16 `app/memory/observe.py`（275 行）

**职责**：群聊进化的**数据与指标层**。三张表：`human_notes`（她观察到的人类习惯/说话方式）、`member_impression`（她对每个群成员的看法）、`reflect_report`（思考报告与记忆自评）。真正调模型生成在 `evolve.py`。

**常量**：`NOTE_TOPICS = ("习惯", "说话方式", "情绪", "仪式", "相处")`、`REPORT_THINK = "daily_think"`、`REPORT_MEMORY_EVAL = "memory_eval"`、`KEY_THINK_DAY = "reflect_think_date"`、`KEY_EVAL_DAY = "reflect_eval_date"`。

**对外符号**：

| 符号 | 签名 | 读写的表 |
|---|---|---|
| `now_iso` / `today` | `-> str` | — |
| `add_note` | `add_note(topic: str, content: str, *, confidence: float = 0.6, user_id: int \| None = None, session_seq: int = 1, chat_type: str = "") -> int` | 写 `human_notes` |
| `recent_notes` | `recent_notes(days: int = 7, limit: int = 80) -> list[dict]` | 读 `human_notes`（`day >= cutoff`，结果反转成正序） |
| `notes_for_user` | `notes_for_user(user_id: int, limit: int = 20) -> list[dict]` | 读 `human_notes` |
| `upsert_impression` | `upsert_impression(user_id: int, summary: str, *, traits_seen: list[str] \| None = None, habits: list[str] \| None = None) -> dict` | 写 `member_impression`（`traits/habits` 合并去重、各留最后 12 条、`version` 自增） |
| `get_impression` / `recent_impressions` | → `dict \| None` / `list[dict]` | 读 `member_impression` |
| `add_report` | `add_report(kind: str, period_start: str, period_end: str, content: str, metrics: dict \| None = None) -> int` | 写 `reflect_report`（`kind` 只收两种，其它 `raise ValueError("未知报告类型")`） |
| `latest_report` / `reports` | → `dict \| None` / `list[dict]` | 读 `reflect_report` |
| `memory_metrics` | `memory_metrics() -> dict[str, Any]` | 读 `memories` 视图 + 6 张表 |
| `eval_period` | `eval_period(now: datetime \| None = None) -> tuple[str, str, str]` | 自然周：返回（上周一, 上周日, 本周一） |
| `eval_due` / `think_due` | `-> bool` | 读 `reflect_report` / kv |
| `mark_think_done` / `mark_eval_done` | `-> None` | 写 kv `reflect_think_date` / `reflect_eval_date` |
| `week_label` | `week_label(period=None) -> str` | `"2026-10-19 – 2026-10-25"` |

**`memory_metrics()` 的全部键**（给 7 天自评用的量化指标）：`总记忆`（优先取 `COUNT(*) FROM memories`，取不到退回 `by_kind` 之和）、`按类型`（`SELECT kind, COUNT(*) ... GROUP BY kind`）、`钉住`、`平均热度`（`AVG(heat)`，3 位小数）、`累计召回`（`SUM(recall_count)`）、`14天未触碰`（`last_seen < 14 天前`）、`归属用户数`（`COUNT(DISTINCT user_id) WHERE user_id > 0`）、`摘要条数`、`冷记忆合并`、`参考轮次`、`参考原话`、`事件条数`、`人类观察条数`、`各人看法条数`，最后再算 `钉住占比` 与 `未触碰占比`。每个查询都包在 `try/except` 里——缺表缺列都不抛错。

**`eval_due()`**：上一自然周还没出过 `memory_eval` 报告就该出——`latest_report("memory_eval")["period_end"] >= 上周日` 则返回 `False`。

**坑**：
- `_count()` 的返回值解析有一段 `isinstance(row, tuple)` 分支——`db.one()` 返回的其实是可下标对象（`sqlite3.Row`），这个分支是为老实现留的。
- `eval_period()` 的"周一算起"用的是 `date.weekday()`（周一=0），所以周日属于上一周。
- `add_note()` 的 `topic` 不在 `NOTE_TOPICS` 里会被**静默改成**"习惯"，不给任何提示。

### 3.17 `app/memory/evolve.py`（277 行）

**职责**：群聊进化的**生成层**。触发由 `app/mind/loop.py:340` 的 `evolve_tick(rt, persona)` 负责，两个时机：每天第一次聊天（`think_due()`）与每个自然周（`eval_due()`）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `HER_OWN_WORDS` | `("白饭","尾巴","鲸","大肥鱼","呆毛","交班","摸鱼")` | 她自己的口癖词——观察里出现就丢（防止把她自己写成观察对象） |
| `write_human_notes` | `async def write_human_notes(rt, persona) -> int` | 按"具体的人"观察，写 `human_notes` |
| `write_think_report` | `async def write_think_report(rt, persona) -> str` | 写 `daily_think` 报告 |
| `update_impressions` | `async def update_impressions(rt, persona, limit_users: int = 5, min_lines: int = 2) -> int` | 更新 `member_impression` |
| `write_memory_eval` | `async def write_memory_eval(rt, persona) -> str` | 写 `memory_eval` 周报 |
| `evolve_tick` | `async def evolve_tick(rt, persona) -> dict[str, Any]` | 总入口，返回 `{notes, think, impressions, eval, intimate_profiles}` |

**`write_human_notes()` 的硬边界**：取最近 8 个 `qq_user`（`ORDER BY last_seen DESC`）；跳过"自己"（`openid == cfg.onebot.self_id`）；每人取 24 小时内 20 条 `role='user'` 发言，少于 3 条就跳过；模型输出要求"2 行、格式 `类别|一句话`、类别只能是 5 个 `NOTE_TOPICS` 之一"；每行过三道闸——类别合法且内容非空、不含 `HER_OWN_WORDS`（除非句子里有"他"）、不以 `我/她说/她刚` 开头；每人最多 2 条，全局最多 6 条。

**`update_impressions()` 的隐私约束**：**只用群聊**（`_recent_event_lines(24*7, 30, user_id=uid, chat_type="group")`）——私聊内容不冒到群花名册里；少于 `min_lines=2` 条就跳过；模型输出两行，第一行一句话总结、第二行 `特征：a,b,c`，`summary[:200]`、`traits` 各 `[:40]`。

**`write_memory_eval()` 的输入**：`observe.memory_metrics()` 全部键（去掉 `按类型` 后逐行列出）+ `_memory_samples(12)`（`SELECT kind, content, importance, pinned, heat, last_seen FROM memories ORDER BY pinned DESC, importance DESC LIMIT 12`，每条渲染成 `[kind/钉住|普通] 正文（热度 0.xx）`）。模型失败时兜底写一句人话："这周记着 N 件事，钉住 M 件。有 K 件很久没碰了。"

**`evolve_tick()` 的顺序**：`think_due()` 为真 → 写观察 → 写思考报告 → 更新看法 → 顺手做私密偏好档案（`app.intimacy.analyze.pending_users(2)`，最多 2 人/天）；`eval_due()` 为真 → 写周自评。整段一个 `try/except Exception: pass`（注释：生成失败也拖不垮聊天）。

**坑**：
- `_recent_event_lines()` 的 `chat_type` 过滤是 `chat_type = ?` 精确匹配，**不**包含空串分区——老数据（`chat_type=''`）在看法生成里被漏掉。
- `HER_OWN_WORDS` 只在 `write_human_notes()` 里生效，`update_impressions()` 不做这层过滤，于是"她自己的口癖"会出现在对别人的看法里。
- `write_think_report()` 的 `observe.add_report(..., day, day, ...)` 把 `period_start` 与 `period_end` 都写成同一天。
- `evolve_tick()` 里 `intimate_profiles` 的 `except` 是独立的一层，隐私档案失败不影响前面三项。

### 3.18 `app/memory/threads.py`（86 行）

**职责**：她说"明天再讲/回头再说"的时候，到点要记得接上。只存她自己的承诺句，不存用户隐私原文；到点注入一次提示，之后标记 `surfaced`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `capture` | `capture(user_id: int, chat_type: str, text: str) -> int` | 从她的话里抓一句"以后再说"，建提醒；返回新 id，抓不到/重复返回 0 |
| `due_threads` | `due_threads(user_id: int, chat_type: str, limit: int = 2) -> list[dict[str, Any]]` | 到点且没提过的，取到就标 `surfaced` |
| `prompt_hint` | `prompt_hint(user_id: int, chat_type: str, limit: int = 2) -> str` | 拼成 prompt 段 |
| `stats` | `stats(user_id: int) -> dict[str, int]` | 按 status 分组计数 |

**抓取规则**：正则 `([^。！？!?\n]*(?:明天|回头|等会|待会|晚点|下次|睡醒|醒了再|再说|改天)[^。！？!?\n]*)` 取整句；`user_id <= 0` 或正文空直接返回 0；片段 `[:80]`，短于 4 字丢弃；同一句 **48 小时**内不重复建（`created_at >= now-48h` 且 `text` 相同就返回 0）；到期时间 `gap_h = 8` 小时（片段含 `明天/睡醒/醒了再/改天/下次`）或 `2` 小时；写 `open_threads(status='open')`。

**`prompt_hint()` 的输出**（`limit=2`）：
```text
【之前没说完的】
- <片段，最多 80 字>
可以自然地接一句，别像在背待办；他不想聊就放下。
```
`due_threads()` 在返回前就把这几行 `UPDATE ... status='surfaced', surfaced_at=now`——所以同一条只会提一次，进程崩了也不会重复。

**调用点**：`app/mind/loop.py:744` 用 `thread_mod.capture(uid, chat_type, reply_text)` 抓她自己的回复；`loop.py:500` 把 `prompt_hint()` 拼进额外提示段。

**坑**：
- `due_threads()` 的 `chat_type` 是精确匹配，私聊建的提醒不会在面板（`chat_type=""`）里弹出。
- `capture()` 只抓"她"的回复，用户自己说"明天聊"不会建提醒——设计如此（这是她的待办，不是用户的）。
- `stats()` 返回的是 `{"open": n, "surfaced": m}`，没有任何"过期"状态；过期条目一直躺在 `open` 里，要等下一次到点被取出。

### 3.19 `app/memory/stickers.py`（96 行）

**职责**：从本地 `data/stickers` 库里按场景挑一张安全图。库由 `scripts/fetch_stickers.py` 抓取（全量 2449 张，`safe=true` 为安全子集）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `load_index` | `load_index(use_cache: bool = True) -> list[dict[str, Any]]` | 读 `index.json`，只留含 `id` 的 dict 条目 |
| `file_path` | `file_path(entry: dict[str, Any]) -> pathlib.Path \| None` | 在 `data/stickers` 与 `RESOURCE_ROOT/data/stickers` 两处找实体文件，要求存在且大小 >0 |
| `stats` | `stats() -> dict[str, Any]` | `{total, safe, scenes, index}` |
| `pick` | `pick(scene: str \| None = None, *, safe_only: bool = True, exclude_ids: set[str] \| None = None, rng: random.Random \| None = None) -> dict[str, Any] \| None` | 挑一张；库不存在/没有可用图返回 `None` |

**选择规则**：候选必须 `id` 不在 `exclude_ids` 里、`safe` 为真（`safe_only` 默认开）、实体文件存在且非空；有 `scene` 就先筛同场景，筛完为空则退回全量候选；最后 `rng.choice()`。

**调用点**：`app/api/onebot.py:411`。档案馆里有成人/攻击性梗，默认只发安全子集。

**坑**：`_cache` 只在 `path` 变化时失效，不校验文件 mtime——换库后要重启进程。

### 3.20 `app/memory/voices.py`（130 行）

**职责**：先用现成音频，不走语音模型。录音放 `data/voices/`，**文件名即触发词**（`晚安.mp3`、`想你.wav`、`笑.mp3`），也可以放子目录当场景（`关心/多喝热水.mp3`）。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `AUDIO_EXT` | `(".mp3",".wav",".amr",".silk",".m4a",".ogg",".flac")` | 支持的格式（7 种，注释里说的 6 种没有 `.flac`） |
| `list_voices` | `list_voices() -> list[pathlib.Path]` | 递归列出全部音频，排序 |
| `stats` | `stats() -> dict[str, Any]` | `{dir, count, names(前 20)}` |
| `wants_audio` | `wants_audio(text: str) -> bool` | 命中 16 个 `SING_HINTS`（唱/唱歌/来一首/来首歌/唱首/唱个/哼/哼歌/歌/情书/表演/发声/语音/声音/听你/唱给我） |
| `wants_sing` | `wants_sing(text: str) -> bool` | 命中 7 个 `STRONG_SING`（唱/来一首/唱首/唱个/哼歌/情书/唱给我） |
| `pick_for_text` | `pick_for_text(text: str, scene: str \| None = None) -> pathlib.Path \| None` | 三级命中 |
| `to_amr` | `to_amr(path: pathlib.Path) -> pathlib.Path \| None` | 转 amr，没有 ffmpeg 返回 `None` |
| `sendable` | `sendable(path: pathlib.Path) -> pathlib.Path` | 发送前选格式 |

**`pick_for_text()` 的三级命中**：① 文件名（stem，忽略大小写）出现在文本里 → 直接返回；② `wants_audio()` 为真 → 优先 `audio/`、`歌/`、`sing/` 三个目录里的任一条，都没有就返回 `items[0]`；③ 给了 `scene` → 找父目录名等于 `scene` 的。

**转码**：`_ffmpeg()` 先找 `DATA_ROOT/tools/ffmpeg.exe`、再找 `RESOURCE_ROOT/tools/ffmpeg.exe`、最后 `shutil.which("ffmpeg")`；`to_amr()` 的参数是 `-ar 8000 -ac 1 -ab 12.2k`（QQ 语音最稳的规格），`timeout=180`，产物已存在且比源文件新就复用。`sendable()` 对 `.amr/.silk` 直接返回，其余尝试转 amr，转不动就原样发。

**坑**：`to_amr()` 的产物落在**源文件旁边**（`path.with_suffix(".amr")`），会往素材目录里写文件；素材目录在只读资源目录里时这一步会失败，而失败被吞成 `None`，最后原样发原格式。

## 4. 数据流

### 4.1 一轮对话（`app/mind/loop.py` 的 `chat_turn`）

```text
用户一句话
  │
  ├─(1) evolve.evolve_tick()            每天第一次 / 每自然周：写观察、思考报告、看法、周自评
  │
  ├─(2) h2.retrieve(text, uid, chat_type)
  │       ├ maybe_tick()                最多每小时一次：衰减 + 遗忘（merge=False）
  │       ├ _hot_from()                 分区取 heat>=0.50 或 pinned，剔 quote/thought，取 10
  │       ├ recall.search() × ≤4 分区    线索/实体命中才算候选，按权重打分，rank 前 5
  │       └ 只写一条 log_recall；status=="hit" 时逐条 mark_recalled（heat+0.28, recall_count+1）
  │
  ├─(3) companion.hint_for()            情绪浓才跑：要不要主动提旧事 → 一句 prompt 提示
  ├─(4) anchors.pick_fixed(阶段,10) + pick_dynamic(场景,阶段,6)   → prompt 截 [:6]
  ├─(5) style.partner_examples(2) / stranger_rules()+service_note()
  ├─(6) compress.covered_to() + store.events_between()  → 原文段
  │      compress.should_compact() 为真 → compress.compact()  → summaries
  ├─(7) h2.wants_pin(text) 为真 → h2.add("fact", …, importance=1.0, pin=True)
  │
  ├─(8) prompt.build(memories=recall.hot, recalled=recall.recalled, anchors=…)
  │       └ _fmt_memories()：按 kind 分组贴标签，每组最多 8 条
  │
  ├─(9) 模型生成回复 → store.add_event("her", reply)
  │      └ threads.capture(uid, chat_type, reply)   抓到"明天再说"就建提醒
  │
  └─(10) asyncio.create_task(facts.maybe_extract(...))   用户消息数 ÷6 == 0 才抽，最多 2 条
```

### 4.2 写入路径（唯一）

```text
业务代码（diary / thoughts / facts / cold / ingest / transfer / relation.users）
  → h2.add(kind, content, importance, pin, cues, source_ids, user_id, chat_type, extra_meta)
      → _find_duplicate() 命中？ → 加温（importance max、heat+0.1、pinned or、mark_seen）→ 返回旧 id
      → 未命中：store.append_event(MemoryEvent)  → mem_event（append-only 原文）
                store.insert_item(MemoryItem)   → mem_item
                store.add_cues(...)             → mem_cue（UNIQUE(item_id, cue)）
      → memories 视图自动跟着变（它是 mem_item 的投影）
```

### 4.3 用户语料导入（`ingest.py`）

```text
文件 → read_text(5 种编码) → sha256[:16] 去重（kv ingest:<digest>）
  → declare.parse()：detect() 定格式 + 定 her/me 标签 + 钦定记忆 + 时间范围
  → formats.parse_*()：{ts, role, text} 序列 + 原话池 (+remember/forget)
  → _fill_timestamps()（缺 ts 就按 45 秒倒推）→ 时间冲突检查 → 污染扫描
  → events 批量写（source="ingest"）
  → 原话池 top120 → store.add_memory("quote", …)
  → 钦定记忆 → store.add_memory("boundary"|"fact", importance=1.0)
  → 非语料型：chunk(1400 字) → heuristic_extract() → store.add_memory(...)
  → cold_start_from() → relation.set_relation()（只在当前 affinity 明显偏低时）
```

### 4.4 参考语料（与用户记忆物理隔离）

```text
别人的聊天记录
  → reference._parse_file()（谁是谁：导出账号 = me）
  → 7 道清洗闸（scrub / 空 / 全占位 / 名片转账 / 系统消息）→ 打 period
  → ref_turns（只此一张表，绝不进 memories / relation）
  → her 的 honeymoon+stable 句子 → ref_quotes（top300，过显式闸）
  → anchors.build() → anchors 表 → prompt 每轮 6 句
  → style.load_dir() → partner_examples / stranger_rules → prompt 每轮 1–2 条
```

## 5. 改这一层要注意

这一层的四条不变量在代码里各有一处"唯一真相"，改动时照着定位：

**① `memories` 是只读视图，写只走 `h2.add()`。**
- 视图 DDL 唯一出处：`app/memory/memv2.py:32` 的 `_VIEW_SQL`；启动时由 `app/core/db.py:362-365` 调 `memv2.ensure_ready()` 建。
- 唯一写入口：`app/memory/h2.py:257` 的 `def add(`；`store.add_memory()` 只是转发（`store.py:108-123`）。
- 仓库里所有写记忆的地方都收敛到 `h2.add`：`facts.py:140`、`cold.py:138`、`ingest.py:165/170/179`、`diary.py:74/93`、`thoughts.py:427`、`transfer.py:118`、`relation/session.py:270`（反向是 `forget_all`）。
- 对 `memories` 做 `INSERT/UPDATE/DELETE` 会被 SQLite 直接拒（视图无 INSTEAD OF 触发器）；`docs/17` 的回归用例 D1 专门钉这一条。
- `mem_item` 的 `CHECK (importance BETWEEN 1 AND 100)`、`CHECK (heat 0..1)`、`CHECK (clarity IN (vivid,clear,fuzzy,trace))`、`CHECK (pinned IN (0,1))` 是最后一道硬闸，绕过 `h2` 手写 SQL 会撞上它们。

**② 召回会加热度：`+0.28`、`recall_count + 1`。**
- 触发链：`h2.retrieve()` 里 `recall_engine.search(..., bump_heat=True)`（`h2.py:474-481`）→ 引擎 `recall.py:527` 的 `if bump_heat and status == "hit"` → `lifecycle.py:265 mark_recalled()`：`heat = clamp(decayed_heat + 0.28)`、`last_recall = now`、`recall_count += 1`、`meta["heat_anchor"] = now`。
- 只有 `hit` 加热，`uncertain` / `miss` 都不加；热层（`_hot_from`）不加。
- 手动加温：`h2.touch(ids)` / `h2.mark_recalled(ids)`（同一函数两名字）。
- 常量唯一出处：`vendor/symvolve_memory/decay.py:29-31` 的 `HEAT_RECALL_DELTA` / `HEAT_DECAY_PER_DAY`；`h2.REHEAT = 0.28` 只是镜像值，改它不会改行为。
- 注意 `mark_seen()` 与 `mark_recalled()` 的分工：重复写入同一句话走 `mark_seen`（刷新 `last_seen`、清晰度回 `vivid`、**不加** `recall_count`），被想起来才走 `mark_recalled`（加 `recall_count`）。

**③ 新增 kind 必须同步 `prompt.py` 的标签表。**
- 标签表唯一出处：`app/mind/prompt.py:41-48` 的 `labels` 字典，目前 6 个 kind：`boundary 不能碰的 / promise 答应过的事 / fact 关于他 / preference 他的口味和习惯 / event 一起经历过的 / quote 他说过的原话`。
- `_fmt_memories()` 只遍历这个字典的键——**表里没有的 kind，条目会被静默丢弃**，一行都不会进 prompt（`buckets` 里还在，`for label in labels.items()` 取不到）。这是最容易踩的一脚：写完新 kind 发现"她根本不记得"，因为标签表没加。
- 已经存在但**没有**标签的 kind：`thought`（她自己的自言自语）、`diary`（日记）。它们被有意挡在外面：`thought` 在 `h2.py:442` 与 `h2.py:491` 被排除出热层与召回；`diary` 进视图但标签表里没有，所以进不了 prompt 的记忆段（日记有独立的 `diary.prompt_line()` 通道）。
- 另两处与 kind 强耦合的表：`h2.PROTECTED_KINDS = ("boundary","promise","quote")`（`h2.py:60`，冷压缩不碰）；`recent_memories()` 把 `quote` 后置（`h2.py:557`）。

**④ 原文与摘要各归各的。**
- `mem_event.raw_text` 永不进 prompt（`h2.py` 头部铁律）；prompt 只吃 `mem_item.text`（视图的 `content`）。
- 工作记忆的原文段走 `store.events_between()`，那是 `events` 表，不是 `mem_event`——两套"原文"分属两个系统：`events` 是对话流水（供上下文组装、压缩），`mem_event` 是记忆的溯源归档（供审计与导出）。

其它改动约束（都是代码里真实存在的）：
1. **时间单位有两套**：引擎侧全是 epoch 秒（`int`），视图与旧行全是本地时区 ISO 字符串（`strftime(..., 'unixepoch', 'localtime')`）。`h2._epoch()` / `_iso()` 是唯一的双向转换点，新代码混用会拿到 0 或 `None`。
2. **`user_id` 也是两套**：引擎 `TEXT`，业务 `INTEGER`；`_uid()` / `_int_user()` 是唯一转换点；`0` 是"共用/她的"，`<= 0` 在 `_partitions()` 里表示"全库检索"。
3. **`chat_type` 三态**：`""`（不分场合，引擎里是真的空串）、`"private"` / `"group"`（具体场合）、`None`（只在引擎 `list_items`/`recall` 里出现，表示不限）。`_chat()` 归一成前两态，`_partitions()` 自己会把空串映射成 `None` 去查。
4. **`h2.maybe_tick()` 只跑衰减与遗忘**（`tick(merge=False)`），冷合并只由 `cold.compact_cold()` 触发；改这里等于改"她多久忘一次"。
5. **kv 键清单**（记忆层写的）：`memory_engine_last_tick`（上次 tick 秒）、`memory_engine_migrated_at`（迁移时间）、`ingest:<sha256[:16]>`、`ref_ingest:<sha256[:16]>`、`last_ingest`、`companion:ledger`、`reflect_think_date`、`reflect_eval_date`、`memory_degraded`（由 `loop.py` 写、`compress.status()` 读）、`nick:<qq_user.id>`（`facts.py` 读）。
6. **导入幂等靠两个 kv 键**：`ingest:<digest>` 与 `ref_ingest:<digest>`；`force=True` 会重跑，但 `reference.py` 的 `force` 不清 `ref_quotes`（见 §3.11）。
7. **vendor 只读**：`app/core/memengine.py` 只做"插 `sys.path` + 打开同一个库 + 缓存单例"三件事，连迁移账本的 checksum 修正（`_KNOWN_CHECKSUM_REWRITES`）都只改账本不改表结构。要改引擎行为得改上游；`vendor/` 目录始终保持原样。
8. **新增一个子模块时的接线点**：需要被 chat 用的，接进 `app/mind/loop.py` 的 `chat_turn`；需要面板可读的，接进 `app/api/routes.py` 或 `app/api/console.py`；需要 CLI 的，接进 `app/cli.py`；需要 QA 覆盖的，加进 `app/qa/scenarios.py`（已有 `memory.h2.add`、`memory.h2.retrieve`、`memory.facts.maybe_extract`、`memory.style.partner_examples` 等特征名）。

## 6. 交叉引用

- **`docs/17-database.md`**：38 张表 + 1 个视图的完整 DDL 与行数；本章的 `memories` 视图 SQL 与它 §2.39 是同一条，`mem_item` 的列定义与它 §2.16 对齐。查"这列到底叫什么"看它，查"这列怎么变"看本章。
- **`docs/00-overview.md`**：四条硬设计约定（写只走 `h2.add`、关系数值唯一结算入口、状态改动必须留痕、`import app.main` 有副作用）的全景位置。
- **`docs/03-files.md`**：`app/memory/*.py` 在整仓里的位置与依赖关系。
- **`docs/18-config.md`**：`cfg.context.keep_recent`、`cfg.context.verbatim_max`、`cfg.context.mode/window/force_ratio/trigger`、`cfg.provider.is_mock`、`cfg.onebot.self_id` 这些被本章读取的配置项。
- 代码入口：`app/core/memengine.py`（引擎单例）、`app/mind/prompt.py`（kind → 标签表）、`app/mind/loop.py`（每轮调用顺序）、`vendor/symvolve_memory/{recall,decay,lifecycle,scoring}.py`（打分/衰减/生命周期的真实实现）。

## 变更记录（2026-11-01）

- **`stability` 不再收了不用**：`h2.add()` 把它写进 `mem_item.meta.stability` 留痕（vendor 引擎没有对应字段，衰减还是走 `clarity` + `heat`）。`source_ids` 依旧落 `meta.ai_life_source_ids`。
- **姓氏表统一**：`extract._SURNAMES` 与 `scrub._SURNAMES` 合并成 `app/memory/surnames.py` 的 `SURNAMES`（157 条），两边 `import` 的是**同一个 set 对象**，不会再漂移。