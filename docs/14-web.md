# 14 · 联网与内容（app/web）

> 覆盖：`app/web/*.py`（5 个文件 / 1,187 行）＋ 4 张独立表（`web_lookup` / `learned_gag` / `gags` / `web_videos`）＋ 6 个 kv 键｜基于 2026-11-01 的代码状态｜上游：docs/04（内核）、docs/17（数据库）｜下游：docs/12（API）、docs/19（人格包）、docs/20（前端面板）

## 1. 这一层干什么

`app/web` 是内核里唯一**把公开互联网的内容抓回来喂给对话**的模块，解决两个具体问题：**她听不懂的词**（"city不city 是啥意思"）和**她不像人说话**（句子漂亮但不口语）。出网的地方不止它一个——模型调用在 `app/mind/llm.py`，QQ/NapCat 在 `app/api/onebot.py`，网页语料在 `app/lifecorpus/fetch.py`——但"抓公开内容进 prompt"这条线只有这里。

它只做三件事：查词（`lookup.py`）、维护热梗库（`gags.py` + `harvest.py`）、调 B 站匿名接口供前两者使用（`bili.py`）。它明确不做：不登录任何账号、不带 cookie、不绕验证码、不下载视频文件、不把外部内容写进记忆。

调用方只有四个：`app/mind/loop.py`（对话主链，第 452–458 行注入热梗提示、第 571–594 行注入查词与梗识别）、`app/api/onebot.py`（第 345–377 行 OneBot 预查词）、`app/api/trend.py`（`panel/trend.html` 的全部后端）、`app/group/proactive.py`（第 209–216 行群主动聊时取一条热梗）。下游只碰三处：`app/core/db.py`（四张表）、`app/api/trend.py`（路由）、`app/main.py`（第 100–124 行的后台 tick）。

## 2. 文件清单

| 文件 | 行数 | 一句话职责 |
|---|---|---|
| `app/web/__init__.py` | 1 | 空包标记（仅一行注释/空行，无导出） |
| `app/web/lookup.py` | 554 | 判断该不该查 → 查 → 折成一句她的话；带 24h 缓存与可信度学习 |
| `app/web/gags.py` | 208 | 热梗库读写与过滤（`gags` / `web_videos` 两张表），产出进 prompt 的可选提示 |
| `app/web/bili.py` | 236 | B 站匿名接口客户端（搜索/热门/评论/详情/热搜），只 GET |
| `app/web/harvest.py` | 188 | 后台每天一轮的抓取调度，状态全写 kv |
| **合计** | **1,187** | — |

## 3. 模块逐一详解

### 3.1 `app/web/lookup.py`

**职责**：回答"这句话要不要联网查、查哪个词、查完怎么跟她说"。查到的结果只写 `web_lookup`，绝不写 `mem_item`。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `needs_lookup` | `needs_lookup(text: str) -> str \| None` | 判断触发；返回关键词串或 `None` |
| `lookup` | `lookup(keyword: str, context: str = "") -> dict[str, Any]` | 主查询；返回 `{keyword, summary, results, source}` |
| `prompt_hint` | `prompt_hint(keyword: str, context: str = "") -> str` | 折成注入 `state_hint` 的一段话 |
| `auto_hint` | `auto_hint(text: str) -> str` | 对话入口用；不该查就返回 `""` |
| `inline_hint` | `inline_hint(text: str) -> str` | 句子里直接用梗时给"你本来就懂"的底，**不发网络请求** |
| `assess_confidence` | `assess_confidence(keyword: str, summary: str, results: list[dict[str, str]], is_ai_ctx: bool) -> tuple[float, list[str]]` | 可信度打分 `(0–1, 理由列表)` |
| `try_learn` | `try_learn(keyword: str, summary: str, results: list[dict[str, str]], is_ai_ctx: bool) -> dict[str, Any] \| None` | 够可信就写 `learned_gag` |
| `learned_gags` | `learned_gags(limit: int = 50) -> list[dict[str, Any]]` | 列自动学到的词（按 confidence 降序） |
| `delete_learned` | `delete_learned(gag_id: int) -> bool` | 删一条，返回是否删到 |
| `baidu_search` | `baidu_search(kw: str, limit: int = 5) -> list[dict[str, str]]` | 百度抓 `{title, snippet}`，任何异常返回 `[]` |
| `bing_search` | `bing_search(kw: str, limit: int = 5) -> list[dict[str, str]]` | Bing 兜底，同上 |
| `_fetch` | `_fetch(url: str, timeout: int = 15) -> str` | `urllib` GET；UTF-8 解不了退 GBK |
| `_clean` | `_clean(html: str) -> str` | 去 `script/style`、去标签、解三个实体、压空白 |
| `_cached` | `_cached(kw: str, hours: int = 24) -> dict[str, Any] \| None` | 24 小时内命中即返回 |
| `_save` | `_save(kw: str, results: list[dict[str, str]], summary: str) -> None` | 写一行 `web_lookup`，`summary[:400]` |
| `_extract_meaning` | `_extract_meaning(keyword: str, summary: str, results: list[dict[str, str]] \| None = None) -> str` | 从结果里摘一句最像释义的话，`[:120]` |

**触发条件（`needs_lookup`）**：四道闸门依次过。

1. 空串直接 `None`；`len(text) > 80` 直接 `None`（长句当聊天，不当提问）。
2. 泛问先短路：正则 `(最近|现在|网上|最近很火|最近流行).{0,8}(热梗|梗|流行语)` 或子串 `有什么热梗` → 返回固定词 `"最近网络热梗"`。
3. 五条捕获型正则按顺序试，第一条命中就返回：

| # | 正则（原文） | 抓什么 |
|---|---|---|
| 1 | `(?:知道\|了解\|听过\|晓得\|懂\|见过)\s*([^\s，。？！?]{2,16}?)\s*(?:这个\|那个)?\s*梗` | 「你知道 city不city 这个梗吗」 |
| 2 | `([^\s，。？！?]{2,16}?)\s*(?:这个\|那个)?\s*梗(?:是什么\|是啥\|什么意思\|怎么来的\|你懂\|知道\|吗)` | 「XX 这个梗是什么意思」 |
| 3 | `(?:什么是\|啥是)\s*([^\s，。？！?]{2,16})` | 「什么是豆包」 |
| 4 | `([^\s，。？！?]{2,16}?)\s*(?:是什么梗\|是啥梗\|是什么意思\|是谁\|指的什么\|是啥意思)` | 「XX 是谁」 |
| 5 | `(?:你们说的\|他们说的\|群里说的)\s*([^\s，。？！?]{2,16}?)(?:是\|指\|$)` | 「群里说的 XX 是啥」 |

4. 命中后清洗：`strip("，。？！? 的")`，再要求 `len(kw) >= 2`、不在 `COMMON`、不是 `("这个","那个","什么","最近")` 之一。

**黑名单**：`COMMON` 是 15 个高频词的硬编码集合——`你好`、`在吗`、`在不在`、`谢谢`、`哈哈`、`晚安`、`早安`、`吃了吗`、`干嘛`、`怎么了`、`为什么`、`可以`、`不行`、`好的`、`真的`。注意它只在"正则抓到词"之后生效，内置词表命中时不过这一关。

**查的源与顺序（`lookup`）**：

| 序 | 分支 | `source` 值 | 是否写表 |
|---|---|---|---|
| 1 | `MEME_GLOSSARY` 内置热梗释义（10 条：`city不city`、`尊嘟假嘟`、`泰裤辣`、`挖呀挖`、`哈基米`、`偷感`、`松弛感`、`已老实求放过`、`那咋了`、`我勒个豆`） | `meme_glossary` | 写 `web_lookup`（results 为空） |
| 1b | 泛问四词（`网络热梗`/`最近网络热梗`/`热梗`/`最近热梗`）→ 拼一段"最近常见的梗有：…"（取 `MEME_GLOSSARY` 前 8 个键） | `meme_glossary` | 写 |
| 1c | `AI_NICKNAMES` 内置 AI 圈黑话，19 条全表：`豆姐`、`豆包`、`豆大妈`、`gpt娘`、`claude娘`、`gemini娘`、`kimi娘`、`通义娘`、`文心娘`、`大肥鱼`、`鲸鱼娘`、`deepseek娘`、`猫娘`、`碳基`、`硅基`、`魔法`、`炼丹`、`幻觉`、`越狱` | `glossary` | 写 |
| 1.5 | `learned_gag` 里 `confidence >= LEARN_MIN_CONFIDENCE`（0.70）的旧词 | `learned` | 不写（带 `confidence` 字段） |
| 2 | `web_lookup` 缓存（24 小时） | `cache` | 不写 |
| 3 | 百度 `https://www.baidu.com/s?wd={q}&rn=10` | `baidu` | 写 |
| 3b | 百度空 → Bing `https://cn.bing.com/search?q={q}&setlang=zh-cn` | `bing` | 写 |

**结果怎么折成"一句她能理解的话"**：两层。第一层在 `lookup` 内——取前 3 条结果，每条拼成 `title：snippet`，用 `；` 连接，整体截 `[:300]` 存进 `summary`。第二层在 `prompt_hint`：

- 有 `summary`：`【你刚上网查了「X」】` + 摘要 + "用这个信息回答。说得像自己了解到的一样（比如「我查了下……」），不要说「根据搜索结果」「资料显示」。"
- 无 `summary`：`【你刚上网查了「X」】` + "没查到可靠说法。可以坦率说「这个我不太确定」，别编。"

**AI 语境加词**：`context` 或关键词本身命中 `AI_CONTEXT`（11 项：`ai`、`模型`、`助手`、`豆包`、`chatgpt`、`deepseek`、`claude`、`gemini`、`kimi`、`机器人`、`拟人`）时，搜索词改成 `f"{kw} AI"`，并给可信度 +0.25。

**梗识别的第二条路（`inline_hint`，不发请求）**：与本节的"不知道就问"分工不同——`needs_lookup` 管"用户在问她这是什么"，`inline_hint` 管"用户自己随口用了梗"。做法是纯本地词表匹配：

1. 句子长度上限 160（超过直接返回 `""`），转小写。
2. 合成词池 `pool = {**MEME_GLOSSARY, **_INLINE_AI, **INLINE_MEMES}`：10 条内置热梗 + 13 条 AI 圈称呼（`AI_NICKNAMES` 里带"娘"的 9 条与 `豆包`/`豆姐`/`豆大妈`/`大肥鱼`）+ 22 条 `INLINE_MEMES`（`急急国王`、`急了`、`典`、`乐`、`绷不住`、`破防`、`麻了`、`逆天`、`离谱`、`抽象`、`整活`、`开团`、`显眼包`、`小卡拉米`、`绝绝子`、`我焯`、`遥遥领先`、`栓Q`、`芭比Q`、`电子榨菜`、`搭子`、`233`）。
3. 按词长降序扫（长的先命中，避免"破防"被"防"抢先），最多取 4 条。
4. 再扫 11 条缩写黑话 `INITIALISMS`（`yyds`、`xswl`、`awsl`、`u1s1`、`nsdd`、`dddd`、`yygq`、`srds`、`zqsg`、`kdl`、`xdm`），用 `(?<![a-z0-9])词(?![a-z0-9])` 做英文边界，避免 `yyds` 命中 `xyydsx`。
5. 命中就返回 `【他这句话里的网络用语，你本来就懂】` + 每行 `「词」= 释义` + "按你听懂了的意思接话：别问「什么意思」、别解释这个词、也别复读它。"

**超时与断网降级**（四层，全部"静默失败"）：

| 层 | 位置 | 行为 |
|---|---|---|
| 请求超时 | `_fetch(url, timeout=15)` | 15 秒；`urllib` 抛异常由调用方吞 |
| 搜索吞异常 | `baidu_search` / `bing_search` | `except Exception: return []`，两个都空 → `results=[]`、`summary=""` |
| 对话入口吞异常 | `auto_hint` | `except Exception: return ""`——联网失败不影响聊天 |
| 主链吞异常 | `app/mind/loop.py:571–579`、`app/api/onebot.py:348–377` | 整段 try/except，预查词失败就当普通聊天接着走 |

所以断网时的用户体验是"她照常回，只是不知道那个梗"，而不是报错或卡住；代价是 `_save` 仍会写一行空缓存（见坑 1）。B 站的请求超时是 20 秒（`bili._get`），走的是另一条链，与查词互不影响。

**可信度打分（`assess_confidence`）**：

| 证据 | 加减分 |
|---|---|
| 结果里提到关键词 ≥3 条 | +0.35 |
| =2 条 | +0.22 |
| =1 条 | +0.08 |
| `TRUSTED_HINTS`（14 项：`百度百科`/`萌娘百科`/`维基百科`/`wikipedia`/`baike`/`moe`/`官方`/`官网`/`定义`/`全称`/`简称`/`别称`/`俗称`/`昵称`）命中 | `min(0.35, 0.12 × 命中数)` |
| AI 语境 | +0.25 |
| `summary` 长度 ≥ 60 | +0.12 |
| `DISTRUST_HINTS`（15 项：`微博`/`抖音`/`网红`/`主播`/`歌手`/`演员`/`明星`/`八卦`/`小说`/`连载`/`直播`/`带货`/`情感`/`离婚`/`恋情`）命中 | −0.45 |
| 合计 | 夹在 `[0, 1]` |

`< 0.70` 就只返回 `{keyword, confidence, learned: False, reasons}`，不落库。

**数据结构 / 存储**：

- `web_lookup`（`app/core/db.py` 第 160–167 行）：`id`、`keyword`、`results`（JSON 文本，默认 `'[]'`）、`summary`（默认 `''`）、`created_at`；索引 `idx_web_lookup_kw(keyword, created_at)`。
- `learned_gag`（第 170–180 行）：`id`、`keyword`（UNIQUE）、`meaning`、`confidence`（REAL，默认 0）、`source`（默认 `'web'`）、`raw`（JSON 前 1500 字符）、`created_at`、`updated_at`；索引 `idx_learned_conf(confidence DESC)`。
- `now()` 返回 `datetime.now().astimezone()`，全模块时间戳统一 `isoformat(timespec="seconds")`。

**耦合**：`app/mind/loop.py:575` 用 `auto_hint`；`app/api/onebot.py:352` 用 `needs_lookup`、`:373` 用 `prompt_hint`；`app/api/trend.py` 的 `/v1/trend/lookup`、`/glossary`、`/learned`、`/learned/{id}` 直接透传。

**坑**：

1. `_save` 在搜索之后**无条件**调用——断网时 `results=[]`、`summary=""` 也会写一行缓存。之后 24 小时内同一个词命中 `_cached`，`summary` 为空，`prompt_hint` 走"没查到"分支，等于断网一次锁死一天。
2. `lookup` 第 280 行引用 `LEARN_MIN_CONFIDENCE`，而它在第 340 行才定义。函数体内是延迟解析，运行没问题，但读代码时容易误判成 `NameError`。
3. `_cached` 只按 `keyword` 查，`context` 不进缓存键：先以非 AI 语境查过的词，之后带 AI 语境再问也拿旧结果。
4. 第 3、4 条正则的捕获是"2–16 个非空白字符连写"，会把修饰语一起吞进关键词：实测 `needs_lookup("什么是快乐星球这种说法呢")` 返回 `"快乐星球这种说法"`，拿这个词去百度必然搜偏，而函数没有任何二次校正。

### 3.2 `app/web/gags.py`

**职责**：维护"最近网上怎么说话"的语料库。核心是**物理隔离**：热梗存 `gags` 表，`memories` 视图底下是 `mem_item`，两张表之间没有任何写入路径。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `add_gag` | `add_gag(text: str, *, source_title: str = "", bvid: str = "", likes: int = 0, kind: str = "comment", require_meme: bool = True) -> int` | 写入或更新一条；返回 id，被拒返回 0 |
| `save_video` | `save_video(v: dict[str, Any]) -> None` | 写 `web_videos`（已存在则更新 title/play） |
| `list_gags` | `list_gags(limit: int = 100, q: str = "") -> list[dict[str, Any]]` | 只列 `enabled = 1`，按 `likes DESC, id DESC`；`q` 走 `LIKE` |
| `delete_gag` | `delete_gag(gag_id: int) -> bool` | 删一条 |
| `clear_gags` | `clear_gags() -> int` | 清空，返回删除行数 |
| `stats` | `stats() -> dict[str, Any]` | `{gags: {total, by_kind}, videos, last_harvest}` |
| `pick_gag` | `pick_gag(scene: str \| None = None, exclude: set[str] \| None = None) -> dict[str, Any] \| None` | 取点赞前 200 条、按 `_MEME_HINT` 过滤后对前 80 条等概率 `random.choice` |
| `prompt_hint` | `prompt_hint(limit: int = 6) -> str` | 生成进 prompt 的"最近流行的说法"段 |
| `_safe` | `_safe(text: str) -> bool` | 脏话/超 60 字直接拒 |
| `_is_gag` | `_is_gag(text: str) -> bool` | 像不像"人怎么说话" |

**过滤三段**（全部是正则常量，逐条列全）：

| 常量 | 作用 | 分支数 |
|---|---|---|
| `_BLOCK` | 脏话/辱骂硬拦，命中即 `_safe()` 返回 False | 17 |
| `_MEME_HINT` | "有梗感"的正向特征，用于 `_is_gag()` 的加分判定与 `pick_gag()` 的优先池 | 45（去重后 43） |
| `_NEWS_LIKE` | 时政/新闻腔，命中且无梗感时整条丢弃（"学了会让她变成键政机器人"） | 20 |

`_BLOCK` 全 17 个：`傻逼`、`傻b`、`煞笔`、`沙比`、`妈的`、`尼玛`、`你妈死`、`操你`、`狗日`、`贱人`、`去死`、`死全家`、`草泥马`、`日你`、`干你娘`、`全家死光`、`不得好死`。

`_MEME_HINT` 全 45 个（`离谱` 与 `急了` 各在原正则里出现两次）：`6{3,}`、`哈{3,}`、`艹`、`草`、`绝了`、`离谱`、`yyds`、`awsl`、`xswl`、`栓q`、`芭比q`、`我就问`、`这不是`、`什么叫`、`真就`、`绷不住`、`破防`、`会玩`、`懂的都懂`、`已经开始`、`给我`、`这波`、`属于是`、`笑死`、`无语`、`麻了`、`寄了`、`急了`、`典`、`乐子`、`抽象`、`整活`、`活了`、`梗`、`确实`、`好家伙`、`好活`、`神了`、`逆天`、`我超`、`就这`、`\d{3,}`、`[?!！？]{2,}`。

`_NEWS_LIKE` 全 20 个：`视频`、`up主`、`UP主`、`作者`、`希望能`、`保护`、`举报`、`封杀`、`官方`、`国家`、`媒体`、`愤怒`、`致敬`、`加油`、`支持`、`反对`、`真的`、`应该`、`必须`、`希望`。

`_is_gag` 的判定顺序：长度必须 `2 <= len <= 50`；纯时间戳/纯 `@`/纯表情标签/纯符号拒；`len < 4` 拒；必须含至少 2 个连续汉字；`_NEWS_LIKE` 命中且无 `_MEME_HINT` 拒；`len > 30` 且无 `_MEME_HINT` 拒。

**怎么进 prompt**：`loop.py:456` 调 `gags_mod.prompt_hint(6)`，结果拼进 `merged_hint`，最终落到 system prompt 的【此刻的状态】段。提示原文：

```text
【最近流行的说法】
- <梗 1>
- …（最多 6 条）
可以偶尔借用这种说法，但要自然；一句里最多用一个，别硬塞。
```

**数据结构 / 存储**：

- `gags`（`app/core/db.py` 第 136–147 行）：`id`、`text`、`source_title`、`bvid`、`likes`、`kind`（默认 `'comment'`）、`enabled`（默认 1）、`harvested_at`；`idx_gags_likes(enabled, likes)` + **UNIQUE** `idx_gags_text(text)`。
- `web_videos`（第 149–157 行）：`bvid` 主键、`title`、`author`、`pic`、`play`、`url`、`first_seen`。
- 两张表都不在 `memories` 视图里，也不被 `h2.add()` 写——**这是"绝不进记忆"的实现方式，不是靠约定**：全仓 `grep` 找不到任何把 gag 文本传进 `app.memory.h2.add()` 的调用。

**隔离的三条证据**：① `gags`/`web_videos`/`web_lookup`/`learned_gag` 四张表都是 `app/core/db.py` 里的普通表，`memories` 是 `app/memory/memv2.py:33` 定义的视图，`FROM mem_item m`，与这四张表无关；② 召回只走 `app/memory/h2.py:retrieve`，它的候选池是引擎表；③ 热梗的出口只有两个——`prompt_hint()` 进 system prompt 的提示段、`pick_gag()` 给群主动聊拼一句话（`app/group/proactive.py:261–263` 的 `f"刷到个说法：{gag}。是不是挺像我们群的。"`），两条路都不产生记忆条目、不影响好感（`app/api/trend.py` 模块 docstring 第 3–4 行原文："热梗单独存 `gags` 表，**不写 memories**；对话里只是偶尔给模型一段可选提示，不参与召回、不影响好感"）。

**耦合**：`harvest.py`（写入）、`app/api/trend.py`（全部读接口）、`app/group/proactive.py:213`（群主动聊取一条）、`app/mind/loop.py:456`（prompt）。

**坑**：

1. `add_gag` 的去重是先 `SELECT id FROM gags WHERE text = ?` 再 INSERT，而且表上还有 UNIQUE 索引兜底——但 `harvest` 统计的 "added" 把"更新已有行"也算进去了（`add_gag` 命中更新也返回 id），所以控制台上看到的"新增 N 条"不等于表里真多了 N 行。
2. `pick_gag` 的 `scene` 参数完全没有被使用（函数体内没有任何 `scene` 分支），是死参数；`exclude` 也只在"被排除的 id 恰好落在点赞前 200 内"时才起作用。
3. `_MEME_HINT` 里 `(6{3,}|哈{3,})` 之类的短特征很容易命中正常句子（"哈哈哈哈哈"），`_is_gag` 的 `len < 4` 与 `len > 30` 两条夹逼是唯一有效的护栏。

### 3.3 `app/web/bili.py`

**职责**：把 B 站匿名 Web 接口包成 6 个公开函数。只发 GET、只解析 JSON、不带任何凭证。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `search_videos` | `search_videos(keyword: str, limit: int = 12) -> list[dict[str, Any]]` | 关键词搜视频；`code != 0` 抛 `RuntimeError` |
| `popular` | `popular(limit: int = 10) -> list[dict[str, Any]]` | 全站热门榜 |
| `video_info` | `video_info(bvid: str) -> dict[str, Any] \| None` | 单视频详情（含 `aid`）；失败返回 `None` |
| `comments` | `comments(aid: int, limit: int = 20, hot: bool = True) -> list[dict[str, Any]]` | 评论（`hot=True` → `mode=3`，否则 `mode=2`） |
| `hot_comments` | `hot_comments(bvid: str, limit: int = 20) -> list[dict[str, Any]]` | 先 `video_info` 取 `aid` 再取评论 |
| `trending` | `trending() -> list[dict[str, Any]]` | 热搜榜（两个接口依次试） |
| `_wbi_keys` | `_wbi_keys() -> tuple[str, str]` | 取 wbi 的 `img_key`/`sub_key`，缓存 600 秒 |
| `_sign` | `_sign(params: dict[str, Any]) -> str` | wbi 签名，返回排好序的 query string |
| `_get` | `_get(url: str, timeout: int = 20) -> dict[str, Any]` | GET + `json.loads` |

**匿名接口清单**：

| 用途 | URL | 参数 | 超时 |
|---|---|---|---|
| wbi 密钥 | `https://api.bilibili.com/x/web-interface/nav` | 无 | 20s |
| 搜索 | `https://api.bilibili.com/x/web-interface/wbi/search/type` | `search_type=video`、`keyword`、`page=1`、`wts`、`w_rid` | 20s |
| 热门 | `https://api.bilibili.com/x/web-interface/popular` | `ps`（夹 1–50）、`pn=1` | 20s |
| 详情 | `https://api.bilibili.com/x/web-interface/view` | `bvid` | 20s |
| 评论（主） | `https://api.bilibili.com/x/v2/reply/main` | `type=1`、`oid=aid`、`mode`、`ps`（夹 1–50）、`next=0` | 20s |
| 评论（备用） | `https://api.bilibili.com/x/v2/reply/main` | `type=1`、`oid=aid`、`mode=3`、`ps=20`、`next=0` | 20s |
| 热搜（一） | `https://api.bilibili.com/x/web-interface/search/square?limit=50` | 无 | 20s |
| 热搜（二） | `https://api.bilibili.com/x/web-interface/wbi/search/square?limit=50` | 无 | 20s |

**wbi 签名**：`img_key + sub_key` 拼成 64 字符，按 `_MIXIN` 重排取前 32 位做 `mixin_key`；参数表加 `wts=int(time.time())`，`urlencode(sorted(...))` 拼 query，再 `md5(query + mixin_key)` 得 `w_rid`，最后返回含 `w_rid` 的排序 query。`_MIXIN` 全 64 个整数：

```text
46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
20, 34, 44, 52
```

**只 GET 不下载**：整个模块没有 POST、没有把响应体写进文件，也没有 playurl（取播放地址）这条接口；`_get` 只保留 `json.loads` 结果，`pic` 只存 URL 字符串（`//` 开头补 `https:`）。

**限流与 UA**：`HEADERS` 固定 `User-Agent`（Chrome/120）、`Referer: https://www.bilibili.com/`、`Accept: application/json, text/plain, */*`。**没有任何请求间隔、重试次数、并发上限**——`comments` 的两个 URL 是"换个参数再试一次"，`trending` 的两条 URL 是"试到有结果就 break"，不是退避。

**耦合**：`harvest.run`（搜索→详情→评论）、`app/api/trend.py`（`/search`、`/send_video`）、`app/adapters/intake.py:58` 与 `app/api/onebot.py:318`（群/私聊里"搜个视频"）。

**坑**：

1. `trending()`、`popular()`、`hot_comments()` 在全仓**没有任何调用点**（`grep` 只命中定义本身），是预留但未接线的能力。
2. `comments` 的注释写明旧接口 `/x/v2/reply` 匿名会 412、部分视频返回 `-352`（风控），代码的处理是"两个 URL 都失败就返回空列表"，上层 `harvest` 把空当"这个视频没评论"记进 `empty` 计数，风控与真空无法区分。
3. `search_videos` 的 `limit` 夹在 `[1, 50]`，但 `harvest` 传的是 `min(20, max(6, limit))`，`trend` 路由传的是用户值（1–30）——两处夹取范围不同。

### 3.4 `app/web/harvest.py`

**职责**：每天一轮的后台抓取。真正跑网络的是 `run()`，决定"该不该跑"的是 `_should_run()`，落状态的是 kv。

**对外符号**：

| 符号 | 签名 | 作用 |
|---|---|---|
| `run` | `run(limit: int = 20, keyword: str = "") -> dict[str, Any]` | 同步抓一轮（会阻塞网络），返回统计 |
| `status` | `status(rt: Any = None) -> dict[str, Any]` | 只读状态，给面板/控制台 |
| `tick` | `async def tick(rt: Any) -> dict[str, Any]` | 后台一轮；永不抛 |
| `_should_run` | `_should_run(rt: Any) -> tuple[bool, str]` | 返回 `(该跑吗, 原因)` |
| `_config_interval` | `_config_interval(rt: Any) -> int` | 读 `trend.interval_hours`，兜底 24 |
| `_cfg_enabled` | `_cfg_enabled(rt: Any) -> bool` | 读 `trend.harvest_enabled`，异常兜底 `False` |

**常量**：`DEFAULT_INTERVAL_H = 24`、`RETRY_MINUTES = 30`、`MAX_TRIES_PER_DAY = 3`、`TICK_SECONDS = 600`，`STATUS_KEYS` 六元组等于下面六个 kv 键。

**kv 状态机**：

| kv 键 | 常量名 | 写入时机 | 值域 |
|---|---|---|---|
| `gag_harvest_enabled` | `KV_ENABLED` | 人工开关（`db.kv_set`，代码里没有写入点） | `"0"`/`"false"` 为关，其它为开 |
| `gag_harvest_last_try` | `KV_LAST_TRY` | 每次真正开跑前 | ISO 时间戳 |
| `gag_harvest_last_ok` | `KV_LAST_OK` | 抓取成功 | ISO 时间戳；失败时保留旧值 |
| `gag_harvest_last_error` | `KV_LAST_ERROR` | 异常时写 `"{类型}: {消息}"[:300]`；成功时清空 | 文本 |
| `gag_harvest_tries_day` | `KV_TRIES_DAY` | 每次开跑前写当天 `date().isoformat()` | `%Y-%m-%d` |
| `gag_harvest_tries` | `KV_TRIES` | 每次开跑前 +1 | 整数字符串 |

**`_should_run` 的判定顺序**（任一命中就返回原因，且都不写 kv）：

| 序 | 原因串 | 条件 |
|---|---|---|
| 1 | `no-runtime` | `rt is None` |
| 2 | `disabled` | `cfg.trend.harvest_enabled` 为假 |
| 3 | `mock` | `cfg.provider.is_mock` 为真——**mock/无 key 永不联网** |
| 4 | `kv-disabled` | `kv_get("gag_harvest_enabled","1") in ("0","false")` |
| 5 | `quota` | 今日 tries ≥ `MAX_TRIES_PER_DAY`（3） |
| 6 | `fresh` | 距 `last_ok` 小于 `interval_hours`（默认 24h） |
| 7 | `backoff` | 距 `last_try` 小于 `RETRY_MINUTES`（30 分钟） |
| — | `due` | 以上都不命中 |

**`tick` 的七步**：① `_should_run`，不该跑就返回 `{ran: False, reason: 原因串}`；② 写 `last_try`、`tries_day`、`tries+1`（**先记账后干活**）；③ 读 `limit = cfg.trend.limit`；④ `await asyncio.to_thread(run, limit, "")`——放线程池，不阻塞事件循环；⑤ 成功写 `last_ok`、清空 `last_error`，返回 `{ran: True, ok: True, added, videos, tries_today}`；⑥ 异常写 `last_error`（`f"{type(exc).__name__}: {exc}"[:300]`）、**不写** `last_ok`，返回 `{ran: True, ok: False, error: str(exc)[:200], tries_today}`；⑦ 整段被 try 包住，后台任务不拖垮服务，不记 `last_error` 以外的日志。

**`run` 的四步**：① 来源 = 用户关键词或 `gags.MEME_SOURCES`（10 个：`网络热梗`、`热梗合集`、`谐音梗`、`抽象视频`、`整活`、`沙雕动画`、`表情包`、`网络热词`、`梗百科`、`B站热梗`），逐源 `search_videos(kw, min(20, max(6, limit)))`，单源异常就跳过；② 按 `play` 降序，取前 60 个去重 bvid，`save_video` 落库；③ 每个视频 `video_info(bvid)` 拿 `aid`，再 `comments(aid, limit=20)`；④ 每条评论 `add_gag(text, source_title=…, bvid=…, likes=…, kind="comment")`。

`run` 返回：`{ok, added, videos, scanned, empty, sources, **gags.stats()}`，其中 `videos` 是去重后的 bvid 数、`scanned` 是真正取过评论的视频数、`empty` 是评论为空的视频数。

**调度接线**：`app/main.py:100–124` 的 `@app.on_event("startup")` 里，先看 `trend.harvest_enabled`（`config.yaml` 第 67–70 行是 `harvest_enabled: true`、`interval_hours: 24`、`limit: 20`），再看 `provider.is_mock`，两关都过才 `asyncio.create_task(_loop())`，循环体是 `harvest_mod.tick(rt)` + `asyncio.sleep(600)`。

**数据结构 / 存储**：只读 `trend` 配置段、只写 6 个 kv 键、只经 `gags`/`bili` 落 `gags` 与 `web_videos`；`status()` 额外带上 `gags.stats()`。

**坑**：

1. `status()` 的 `enabled` 判定是 `not in ("0","false","")`（空串算关），`_should_run()` 里是 `in ("0","false")`（空串算开）——同一个 kv 键两处语义不一致。
2. `tick` 在开跑前就把 `tries` 记账，`last_try` 也写了；如果进程在 `run` 中途被杀，`last_ok` 不会更新，但当日次数已经消耗一次。
3. `run()` 没有任何并发保护，两个 `tick` 同时进来（例如手动 `POST /v1/trend/harvest` 与后台 tick 撞上）会重复抓同一批 bvid，`add_gag` 靠 UNIQUE 索引去重，`added` 计数会虚高。
4. `harvest.run` 的 `keyword` 参数在后台 tick 里永远传 `""`，只有手动 `POST /v1/trend/harvest` 才会用它。

### 3.5 `app/web/__init__.py`

**职责**：空包标记。1 行，无导出、无副作用；所有调用方都写全路径导入（`from app.web import gags`、`from app.web import lookup as lookup_mod`）。

## 4. 数据流

一次"她听不懂的词"从进来到用上，走这条线（OneBot 通道，`app/api/onebot.py:345–389`）：

```text
用户消息 ──→ needs_lookup(text)
              ├─ 不触发 ──→ 直接 chat_turn（loop.py 里还会试一次 auto_hint）
              └─ 触发 ──→ 先回一句「我想一想。」并发出去（记一条 event，meta.lookup_ack）
                          └─→ prompt_hint(kw, text) ──→ lookup(kw, text)
                                ├─ 内置词表/learned/缓存 → summary
                                └─ 百度 → Bing → _save(web_lookup) → try_learn(learned_gag)
                          └─→ 作为 prefetched_hint 传进 chat_turn（slow=True，强制深思）
chat_turn ──→ prompt.build(state_hint=… + prefetched_hint)
          └─→ 模型 ──→ 回复里说「我查了下……」
```

热梗那条线完全独立：

```text
后台 startup ──→ tick（600s 一跳）──→ _should_run ──→ asyncio.to_thread(run)
                                                    └─→ bili.search_videos → video_info → comments
                                                    └─→ gags.add_gag（gags 表）
对话每轮 ──→ gags.prompt_hint(6) ──→ system prompt【此刻的状态】
面板 ──→ GET /v1/trend/gags、/stats、/hint（只读）
```

两条线的共同点：**都只读表、只产出字符串**，没有任何一条写 `mem_item`。

## 5. 改这一层要注意

1. 新增一个联网来源：改 `lookup.lookup` 的分支顺序（内置表 → learned → 缓存 → 百度 → Bing），别插在缓存前面，否则 24h 缓存失效；`_save` 必须成对，否则控制台 `/v1/trend/learned` 看不到。
2. 新增一个热梗过滤规则：只改 `_BLOCK` / `_MEME_HINT` / `_NEWS_LIKE`，不要改 `_is_gag` 的长度阈值——`add_gag` 的 `require_meme` 与 `harvest` 的 `added` 计数都依赖它。
3. 新增 kv 状态键：必须同时加进 `STATUS_KEYS` 和 `status()` 的返回字典，否则 `GET /v1/trend/stats` 的 `harvest` 段不完整（面板 `trend.html` 的"上次抓取/自动抓取时间"读的就是这一段）。
4. 改抓取频率：只改 `config.yaml` 的 `trend.interval_hours`（代码里 `_config_interval` 会 `max(1, int(...))`），`TICK_SECONDS` 是探测间隔，不是抓取间隔——调小它不会让抓取变勤，只会让 `_should_run` 被多问几次。
5. 想让 mock 下也联网：只能改 `_should_run` 的第 3 条与 `main.py:110` 的闸门；但 `app/qa/cli_probe.py:243–248` 与 `:827` 会断言 mock 下 `harvest` 调用次数为 0，测试基线会红。
6. 新增 B 站接口：所有请求必须走 `bili._get`（统一 UA/Referer/超时 20s），且保持 GET；`harvest.run` 现在依赖 `search_videos` 返回里的 `bvid`、`video_info` 返回里的 `aid`、`comments` 返回里的 `text`/`likes` 三个字段，缺一个就断链。

## 6. 交叉引用

- 相关：docs/12（`/v1/trend/*` 的完整路由表与出入参）
- 相关：docs/17（`gags` / `web_videos` / `web_lookup` / `learned_gag` 建表语句与索引）
- 相关：docs/19（`state_hint` 里热梗与查词两段在 persona prompt 中的位置）
- 相关：docs/20（`panel/trend.html` 的两个区块与请求策略）

## 变更记录（2026-11-01）

- **断网不写空缓存**：`lookup._save()` 只在拿到结果时落库。以前两边搜索都失败也会写一条 `summary=""` 的缓存，同一个词 24 小时内拿不到真结果。
- **热梗开关只有一个口径**：`harvest._kv_enabled()` —— 空串跟随 `trend.harvest_enabled`，`0`/`false` 为关，其它为开。`status()` 与 `_should_run()` 现在不会各投一票。

---

## 9. 工具环节（`app/tools/`）

2026-11-01 新增：给她装"手"。分两族——**联网工具**、**识图工具**。

| 模块 | 干什么 |
|---|---|
| `registry.py` | 工具目录 `SPECS`（名字/说明/参数）+ 门闸 `gate()` + 执行器 `run()`（永不抛） |
| `web.py` | `web_search`（百度打头、空了退 Bing，复用 `lookup` 的后端）、`web_fetch`（抓正文、去标签、限域、截断） |
| `vision.py` | `image_describe`：URL / data URI / 本地路径 → 多模态模型（OpenAI 兼容 `image_url`）→ 文字描述 |
| `router.py` | 确定性路由：话术与附件决定调什么，结果拼成【刚查到的】注进 prompt |

### 9.1 路由规则（为什么不用模型 function-call）

主链已经 20 步，再加一轮模型调用等于延迟与成本翻倍；且国产兼容端点对 `tools` 参数支持参差。所以改成**确定性路由**（`router.plan()`，纯函数、可测）：

| 触发 | 工具 | 说明 |
|---|---|---|
| 消息带图片段 | `image_describe` | 最多看 `tools.vision.max_images` 张（默认 2）；只发图时用 `[图片]` 占位 |
| 正文里有 `http(s)` 链接 | `web_fetch` | 只抓第一个链接，避免连环抓 |
| 有"查一下/搜一下/是什么/多少钱…"且正文 ≤ 80 字 | `web_search` | 查询词会削掉"帮我/查一下/是什么"这些壳 |

两个约束：**一轮最多两步**；**群聊不调工具**（避免在群里烧搜索与视觉调用，判断点在 `loop.chat_turn`）。

### 9.2 mock 闸（离线测试零联网）

`registry.gate()` 按顺序判：`tools.enabled` → `provider.is_mock` → 分族开关。任一不过就返回 `{"ok": False, "reason": …}`，
**一个字节都不出网**。`adv_regress` 的 D26 直接断言 `web=mock / vision=mock / hint=''`。

### 9.3 配置（`config.yaml`）

```yaml
tools:
  enabled: true          # 总开关；关掉 = 回到纯聊天
  web:
    enabled: true
    max_results: 5
    timeout_s: 8.0
    max_chars: 1500      # 抓回来的正文截断（进 prompt，别挤爆上下文）
    allow_hosts: []      # 空 = 不限；填了就只允许这些域名（含子域）
  vision:
    enabled: true
    model: ""            # 留空 = 跟 provider.model；看图建议显式指定视觉模型
    max_tokens: 400
    timeout_s: 20.0
    max_images: 2
```

### 9.4 结果长什么样

`router.hint()` 返回一段中文，进 prompt 的独立段【刚查到的】（`sections["tools"]` 单独计预算）：

```text
搜「DeepSeek」的结果：
· xxx：yyy

图里的内容：
一只橘猫趴在键盘上，屏幕上是终端窗口。
```

模型只负责把它说成人话——**不负责决定查不查**。

## 10. 声音环节（`app/voice/`）

文字转语音（TTS）。**只实现客户端**：服务端是第三方项目
（`astrbot_plugin_feiyu_voice` / 肥鱼声音），它没有授权再分发，所以代码不进本仓库。

```powershell
.\scripts\fetch_voice_service.ps1          # 拉到 .third_party\feiyu_voice（已 gitignore）
# 按它自己的 server\README.md 起服务，默认 http://127.0.0.1:9880
```

### 10.1 HTTP 契约（我们实现的就是这三条）

| 请求 | 用途 |
|---|---|
| `POST {base}/v1/speech` `{"voice","text","style"}` | 提交合成 → `{"id": "<32hex>", "status": "pending"}` |
| `GET {base}/v1/jobs/{id}` | 轮询状态：`pending/running/success/failed/interrupted` |
| `GET {base}/v1/jobs/{id}/audio` | 取 WAV（校验 RIFF 头、≤16 MB） |

`Authorization: Bearer <voice.api_key>`；`text` 服务端上限 2000 字，本项目按 `voice.max_chars` 再截一刀。

### 10.2 开关与花费

| 键 | 默认 | 效果 |
|---|---|---|
| `voice.enabled` | `false` | 总开关。关着 → `available()` 直接返回 `voice-disabled`，一个字节不出网 |
| `voice.send_in_chat` | `false` | 合成完**直接当语音发出去**（仅 OneBot 通道，`caps.voice=true`） |

主链纪律：TTS **不阻塞对话**——`chat_turn` 不合成，发送侧（`api/onebot.py`）在"没有现成录音可发"
时才 `await synth(...)`，失败就当没合成、文字照发。

### 10.3 面板/接口开关

`GET /v1/settings` 现在多返回两段 `tools` 与 `voice`；`POST /v1/settings` 接受
`tools_enabled / group_tools_enabled / web_enabled / vision_enabled / vision_model /
vision_base_url / tts_enabled / tts_send_in_chat`——改完**立刻生效**，并写进
`data/config.local.yaml`（重启还在，不入库）。

### 10.4 独立 API（不挤聊天主链）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/v1/console/tts` | 状态：可不可用、缓存几个、本分钟额度 |
| `POST` | `/v1/console/tts` | 合成，body `{text, voice?, style?}` |
| `GET` | `/v1/console/tts/file?path=…` | 取 WAV（限定 `data/voices/` 下） |

实现要点：
- **专属线程池**（`voice.workers`，默认 2，上限 4）——不跟聊天模型/别的 `to_thread` 抢默认线程池；
- **缓存**：`sha256(voice|style|text)` → `data/voices/cache/<hash>.wav`，命中直接复用（`cache_enabled`）；
- **限流**：`voice.max_per_min`（默认 6，0=不限），用 kv 按分钟计数，超了返回 `rate-limited`；
- 关着（`voice.enabled=false`）或 mock 时，三个接口都**优雅拒绝**，不报 500、也不出网（回归 D32/D33 钉住）。

### 10.5 缓存清理

`voice.cache_ttl_days`（默认 **15**，0 = 永不清理）：`app/main.py` 的 startup 钩子调
`tts.cleanup_cache()` 清一次，**不在合成热路径上做**。规则：`mtime` 早于 TTL 的 `*.wav` 删掉，
其余保留；返回 `{ok, removed, kept, ttl_days}`。面板「工具」页签里可以改这个天数。
