# 03 · 文件全表

> 覆盖：**195 个 Python 文件 / 63,611 行 / 210 个类 / 1826 个顶层函数 / 480 个方法**，外加前端、配置、人设、示例数据｜基于 2026-11-01 的代码状态（移除 R 级插件残留 + 新增语料出口后重扫）。
>
> 本章是**索引章**：每个文件一行，给出行数、类/函数数量与模块 docstring 首句。实现细节按目录跳转到对应专题章。行数与符号数量由 AST 静态扫描得出（不含注释行内的空行差异）。

## 1. 规模分布

| 目录 | 文件数 | 行数 | 类 | 顶层函数 |
|---|---|---|---|---|
| `app/` | 116 | 36,282 | 97 | 1292 |
| `scripts/` | 43 | 14,860 | 23 | 304 |
| `vendor/` | 35 | 12,465 | 90 | 227 |
| `根目录` | 1 | 4 | 0 | 0 |
| **合计** | **195** | **63,611** | **210** | **1826** |

## 2. app/ 内核目录

### 2.1 `app/`

内核顶层：`main.py`（FastAPI 装配）、`cli.py`（10 个子命令）、`desktop.py`（pywebview 桌面版）、`__init__.py`。

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/__init__.py` | 7 | 0 | 0 | 衡忆数字生命（SymBot）内核。 |
| `app/cli.py` | 487 | 0 | 13 | — |
| `app/desktop.py` | 143 | 1 | 5 | 独立桌面应用入口。 |
| `app/main.py` | 168 | 0 | 1 | — |

### 2.2 `app/core/`

**基础设施层**：配置单例、路径解析、SQLite 连接与建表迁移、全局运行时单例、vendor 引擎桥、连续性留痕、启动预检、通道能力位。→ docs/04

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/core/__init__.py` | 0 | 0 | 0 | — |
| `app/core/channel.py` | 51 | 1 | 3 | 通道能力位：同一套内核，不同通道能干什么不一样。 |
| `app/core/config.py` | 496 | 22 | 7 | 三层配置合并 + `_apply_env` 环境变量覆盖（含 `SYMEM_DEVELOPER_KEY`）。 |
| `app/core/continuity.py` | 118 | 0 | 6 | 连续性账本：她可以被改变，但改变不能没有痕迹。 |
| `app/core/db.py` | 611 | 0 | 12 | — |
| `app/core/memengine.py` | 181 | 0 | 10 | 衡忆 2.0 引擎接线（Symvolve Memory vendored 副本的唯一入口）。 |
| `app/core/paths.py` | 26 | 0 | 0 | 路径解析：开发态与打包态分开。 |
| `app/core/preflight.py` | 69 | 0 | 2 | 启动预检：模型名同源 + 控制台暴露面（token 空且非回环时告警）。 |
| `app/core/runtime.py` | 85 | 1 | 2 | — |

### 2.3 `app/mind/`

**对话主链**：`loop.chat_turn` 串起命名/恋爱/状态策略/关系/记忆/情绪/prompt/模型/守卫/写事件。→ docs/05

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/mind/__init__.py` | 0 | 0 | 0 | — |
| `app/mind/guard.py` | 71 | 0 | 5 | 出戏守卫：拦住"作为一个人工智能…"这类句子。 |
| `app/mind/llm.py` | 195 | 4 | 1 | — |
| `app/mind/loop.py` | 1346 | 0 | 25 | — |
| `app/mind/models.py` | 323 | 1 | 13 | 模型库与路由。 |
| `app/mind/prompt.py` | 454 | 1 | 10 | — |

### 2.4 `app/memory/`

**记忆系统**：唯一写入口 `h2.add()`、只读视图 `memories`、事实抽取、压缩与冷合并、导入导出、锚点、语料风格层、陪伴层。→ docs/06

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/memory/__init__.py` | 0 | 0 | 0 | — |
| `app/memory/anchors.py` | 271 | 1 | 11 | 锚点池：从真实语料里抽出"她会说的话"。 |
| `app/memory/cold.py` | 226 | 0 | 11 | 冷记忆压缩（M3.5，衡忆 2.0 版）。 |
| `app/memory/companion.py` | 290 | 0 | 19 | 衡忆 2.0 陪伴层（companion-emotion 分支）接入。 |
| `app/memory/compress.py` | 219 | 0 | 10 | 工作记忆压缩：把"聊过的"折成线索，不折成事实。 |
| `app/memory/declare.py` | 209 | 1 | 6 | 记忆交接的声明层。 |
| `app/memory/evolve.py` | 277 | 0 | 8 | 群聊进化生成层：思考报告 / 人类观察 / 每人看法 / 每周记忆自评。 |
| `app/memory/extract.py` | 217 | 2 | 5 | — |
| `app/memory/facts.py` | 142 | 0 | 3 | 轻量事实抽取：让"记住你"真的发生。 |
| `app/memory/formats.py` | 242 | 1 | 10 | 各格式的对白解析器。 |
| `app/memory/h2.py` | 708 | 1 | 45 | 衡忆 2.0 · 记忆公共 API（本项目唯一的记忆入口）。 |
| `app/memory/ingest.py` | 225 | 0 | 5 | — |
| `app/memory/memv2.py` | 292 | 0 | 11 | 衡忆 2.0 落地：引擎表接管 · 旧 `memories` 表归档 · 只读视图。 |
| `app/memory/observe.py` | 275 | 0 | 21 | 群聊进化：人类观察、每人看法、周期报告、记忆系统自评指标。 |
| `app/memory/reference.py` | 347 | 1 | 10 | 参考语料：学"人是怎么相处的"，不碰"用户是谁"。 |
| `app/memory/scrub.py` | 195 | 1 | 6 | 去敏：把真实信息摘干净，只留"人怎么说话"。 |
| `app/memory/stickers.py` | 96 | 0 | 5 | 表情包选择器：从本地 `data/stickers` 库里按场景挑一张安全图。 |
| `app/memory/store.py` | 160 | 0 | 13 | — |
| `app/memory/style.py` | 343 | 0 | 12 | 语料风格取样层：把 `data/human_life/_derived` 的语料压成"给 prompt 用的一点点东西"。 |
| `app/memory/threads.py` | 86 | 0 | 5 | 没说完的话题：她说了“明天再讲/回头再说”，到点要记得接上。 |
| `app/memory/voices.py` | 130 | 0 | 9 | 本地语音/录音素材：先用现成音频，不走语音模型。 |

### 2.5 `app/relation/`

**关系系统**：四维结算唯一入口 `apply_appraisal()`、阶段、恋人绑定、表白分手、命名、厌恶终止、告别、好友与仪式。→ docs/08

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/relation/__init__.py` | 0 | 0 | 0 | — |
| `app/relation/appraise.py` | 96 | 0 | 2 | — |
| `app/relation/engine.py` | 406 | 0 | 15 | — |
| `app/relation/finale.py` | 279 | 0 | 9 | 告别：一封信、一份档案、一个真的结束。 |
| `app/relation/friends.py` | 86 | 0 | 7 | 加好友：OneBot 没有"主动加好友"的接口。 |
| `app/relation/greet.py` | 55 | 0 | 6 | 主动招呼还没认识的人。 |
| `app/relation/judge.py` | 78 | 0 | 2 | 语言分析式情感评估（多信号）。 |
| `app/relation/love.py` | 225 | 0 | 10 | 恋爱机制接线：表白 / 分手 / 她的主动提议 / 复合冷却话术。 |
| `app/relation/rituals.py` | 56 | 0 | 4 | 关系里的小仪式：晚安、早安、认识第 N 天。 |
| `app/relation/session.py` | 325 | 0 | 24 | 会话生命周期：命名、厌恶、终止、重启。 |
| `app/relation/users.py` | 332 | 0 | 20 | QQ 群多用户：群成员、每人独立关系、唯一恋人绑定。 |

### 2.6 `app/intimacy/`

**亲密模式**：OFF/SUGGESTIVE/EXPLICIT 状态机、群聊降级、私密记忆与画像分析、引导模式。→ docs/09

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/intimacy/__init__.py` | 1 | 0 | 0 | 亲密模式模块：管理暗示→直白的状态机与私密记忆。 |
| `app/intimacy/analyze.py` | 193 | 0 | 10 | 私密偏好分析：把亲密记录提炼成"他喜欢/讨厌/边界"档案。 |
| `app/intimacy/exemplars.py` | 85 | 0 | 5 | dirty talk 范本库：用户给的样句，长期保留（kv JSON，可审计）。 |
| `app/intimacy/gate.py` | 125 | 0 | 8 | 亲密门控：OneBot 和控制台共用同一条判定 / 记录链路。 |
| `app/intimacy/guide.py` | 124 | 0 | 10 | 引导模式：从暗示一步一步带到露骨，达成后收尾睡觉。 |
| `app/intimacy/memory.py` | 105 | 0 | 5 | 私密记忆：亲密模式下的上下文记录与分析。 |
| `app/intimacy/mode.py` | 214 | 1 | 12 | 亲密模式引擎：私聊大胆、群聊暗示、非恋人调侃拒绝。 |
| `app/intimacy/prompt.py` | 255 | 0 | 12 | 亲密模式 prompt 注入：根据模式修改她说话的方式。 |

### 2.7 `app/life/`

**生活与作息**：清醒度模型、作息时钟、日记、人类日常语料接管、主动外发、夜里念头。→ docs/10

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/life/__init__.py` | 0 | 0 | 0 | — |
| `app/life/behavior.py` | 194 | 1 | 7 | 状态驱动的回复行为。 |
| `app/life/clock.py` | 95 | 0 | 4 | — |
| `app/life/diary.py` | 191 | 0 | 12 | 她的一天：每天写一段只给自己看的日记，第二天聊天时自然提起。 |
| `app/life/human_life.py` | 554 | 0 | 21 | 她的一天：把 data/human_life/_derived 里的作息语料读进运行时（只读接管）。 |
| `app/life/proactive.py` | 872 | 0 | 53 | 时间机制：她会等你，也会烦。 |
| `app/life/thoughts.py` | 848 | 0 | 41 | 夜里独处：让她自己想点事。 |

### 2.8 `app/group/`

**群聊增强**：群语境判断、每人印象、群主动开口、自我介绍、定期调度。→ docs/11

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/group/__init__.py` | 5 | 0 | 0 | 群聊增强模块（QQ 群专属，见 docs/26）。 |
| `app/group/active.py` | 160 | 0 | 8 | 群聊主动调度：定期检查是否该开口，通过 OneBot 发到群里。 |
| `app/group/context.py` | 191 | 0 | 10 | 群聊语境：判断"在跟谁说话"、"她是不是焦点"、昵称管理。 |
| `app/group/intro.py` | 105 | 0 | 2 | 群自我介绍：让她在新群/重新开始时介绍自己，可 @所有人。 |
| `app/group/proactive.py` | 303 | 0 | 21 | 群聊主动行为：找话题、随机活跃、语境判断、昵称、待机。 |

### 2.9 `app/api/`

**HTTP 接口**：`routes`/`console`/`trend`/`group`/`onebot` 五个 router，共 124 个操作。→ docs/12

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/api/__init__.py` | 0 | 0 | 0 | — |
| `app/api/console.py` | 990 | 8 | 50 | 小鲸 Web 控制台的后端接口（panel/console.html 用）。 |
| `app/api/group.py` | 104 | 3 | 10 | 群聊控制接口：静默 / 主动说话 / 昵称 / 待机状态。 |
| `app/api/onebot.py` | 555 | 0 | 10 | OneBot v11 接入（小号路线）：NapCat / LLOneBot / Lagrange。 |
| `app/api/routes.py` | 1198 | 16 | 63 | — |
| `app/api/trend.py` | 154 | 2 | 13 | 热梗库 / B 站搜索接口（panel/trend.html 用）。 |

### 2.10 `app/adapters/`

**外部接入**：QQ 官方 botpy 长连、两通道共用的入站前置层。→ docs/13

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/adapters/__init__.py` | 1 | 0 | 0 | 外部接入适配器（QQ / 以后的飞书等）。 |
| `app/adapters/intake.py` | 85 | 0 | 1 | 两个通道共用的入站前置层。 |
| `app/adapters/qq.py` | 303 | 1 | 13 | QQ 官方开放平台适配器（WebSocket 长连接 · 群 @ + 私聊 C2C）。 |

### 2.11 `app/web/`

**联网与内容**：查词、热梗库、B 站客户端、后台抓取。与她的记忆物理隔离。→ docs/14

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/web/__init__.py` | 1 | 0 | 0 | 外部网络内容模块：B 站等热梗来源（与她的记忆物理隔离）。 |
| `app/web/bili.py` | 236 | 0 | 9 | B 站匿名接口客户端（无需登录）：搜索视频、热门、评论、视频详情。 |
| `app/web/gags.py` | 208 | 0 | 11 | 热梗库：从 B 站评论区/视频标题学"网络热语"，**独立存放、绝不进她的记忆**。 |
| `app/web/harvest.py` | 201 | 0 | 10 | 热梗自动抓取（路线 A）：后台每天抓一轮 B站热梗评论。 |
| `app/web/lookup.py` | 557 | 0 | 17 | 联网查词：遇到看不懂的词就上网查，把结果折成一句她能理解的话。 |

### 2.11.1 `app/tools/`

**工具环节**：她能用的"手"——联网查资料 / 读链接 / 看图片。mock 模式一律不出网（`registry.gate()` 第一道闸）。→ docs/14

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/tools/__init__.py` | 20 | 0 | 0 | 工具环节：给她装"手"。 |
| `app/tools/registry.py` | 109 | 0 | 4 | 工具注册表：SPECS 目录 + `gate()` 门闸 + `run()` 兜异常。 |
| `app/tools/router.py` | 141 | 0 | 5 | 确定性路由：话术/附件 → 调哪个工具 → 拼成【刚查到的】。 |
| `app/tools/vision.py` | 125 | 0 | 3 | 识图：本地/URL/data URI → 多模态模型 → 文字描述。 |
| `app/tools/web.py` | 127 | 0 | 4 | 联网：搜索（百度→Bing）+ 抓正文（去标签/限域/截断）。 |

### 2.11.2 `app/voice/`

**声音环节**：文字转语音（TTS）。只实现客户端，服务端是第三方项目、不随仓库分发（`scripts/fetch_voice_service.ps1` 自己拉）。→ docs/14 §10

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/voice/__init__.py` | 13 | 0 | 0 | 声音环节：文字转语音（TTS）。 |
| `app/voice/tts.py` | 271 | 0 | 10 | 对接本地声音服务：`/v1/speech` 提交 → 轮询 `/v1/jobs/{id}` → 取 WAV。 |

### 2.12 `app/lifecorpus/`

**语料管线**：四种语料的 schema/校验/覆盖矩阵/PII/只读抓取脚手架。→ docs/15

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/lifecorpus/__init__.py` | 47 | 0 | 0 | 人类作息与社交语料管线（life corpus pipeline）。 |
| `app/lifecorpus/coverage.py` | 484 | 2 | 8 | 覆盖矩阵与缺口清单：24 小时、日型、活动类型、社交关系、场景、对话轮数。 |
| `app/lifecorpus/fetch.py` | 631 | 1 | 29 | 人类日常语料采集脚手架：只读 GET、守 robots、默认离线。 |
| `app/lifecorpus/pii.py` | 208 | 1 | 11 | PII 检测与遮掩：中国手机号、18 位身份证号、常见邮箱。 |
| `app/lifecorpus/schema.py` | 508 | 2 | 10 | 记录 schema：四种语料（rhythm / activity / social_scene / snippet）的字段定义与校验。 |
| `app/lifecorpus/validate.py` | 365 | 3 | 7 | JSONL 读取、逐行校验与目录汇总。 |

### 2.13 `app/qa/`

**对抗式测试底座**：inventory/static/routes/functions/cli/coverage/duel/oracle/report 九个 phase。→ docs/16

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/qa/__init__.py` | 16 | 0 | 0 | 对抗式全量训练（Adv) 测试底座。 |
| `app/qa/cli_probe.py` | 1129 | 0 | 22 | CLI 全子命令 + 后台任务探针（phase = ``cli``）。 |
| `app/qa/coverage.py` | 392 | 1 | 7 | coverage —— 运行期覆盖率：哪些 `app/` 下的函数真的被执行过。 |
| `app/qa/duel.py` | 1506 | 3 | 46 | 双 AI 互聊引擎（duel）：让「她」跟另一个 AI 说话，把破绽逼出来。 |
| `app/qa/inventory.py` | 1122 | 1 | 34 | inventory —— 全量静态清单：把仓库里每个可调用面变成一条有 ID 的 Item。 |
| `app/qa/oracle.py` | 1574 | 1 | 60 | 出戏 / 关系 / 亲密 的裁判（oracle）：把对话与状态折成可复核的 Finding。 |
| `app/qa/probe.py` | 1539 | 3 | 46 | 逐函数动态探针（probe）：把仓库里**每一个可调用对象**真调一次。 |
| `app/qa/registry.py` | 96 | 1 | 1 | Item 注册表：先列全清单，再逐条执行。 |
| `app/qa/report.py` | 1272 | 0 | 49 | 对抗式全量训练的汇总报告生成器（CONTRACT §2 / `app/qa/report.py`）。 |
| `app/qa/routes_fuzz.py` | 1129 | 0 | 22 | L4 · 全量 HTTP 路由的对抗式模糊测试（`app.qa.routes_fuzz`）。 |
| `app/qa/sandbox.py` | 190 | 2 | 7 | 隔离沙箱：临时数据根 + mock provider + 断网 + 密钥遮罩。 |
| `app/qa/scenarios.py` | 1601 | 1 | 8 | 对抗场景库（全量功能域覆盖）——供 `app/qa/duel.py` 消费。 |
| `app/qa/static_probe.py` | 220 | 0 | 4 | 静态项执行层：把 inventory 列出来的「非函数」条目真的验一遍。 |
| `app/qa/types.py` | 116 | 4 | 1 | 对抗式训练的数据契约：Item / Finding / TurnRecord / PhaseResult。 |

### 2.14 `app/persona/`

**人设加载与档案迁移**：YAML → 运行时结构、整包导出导入。→ docs/19

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `app/persona/__init__.py` | 0 | 0 | 0 | — |
| `app/persona/load.py` | 148 | 1 | 1 | — |
| `app/persona/transfer.py` | 161 | 0 | 7 | 人设与记忆的导出 / 导入。 |

## 3. scripts/ 脚本目录

### 3.1 `scripts/`

**测试与运维脚本**：三件套基线、对抗矩阵、真机联调、探针。→ docs/21

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `scripts/adv_full.py` | 335 | 0 | 14 | 对抗式全量训练 · 编排入口。 |
| `scripts/adv_list.py` | 87 | 0 | 2 | 把一次对抗训练的全部条目摊开成清单（给人看 / 给 AI 复核）。 |
| `scripts/adv_regress.py` | 647 | 0 | 3 | 对抗训练修完的缺陷，逐条钉成回归用例（隔离库，零联网，零花费）。 |
| `scripts/check_secrets.py` | 111 | 0 | 2 | 发布前自检：扫一遍仓库，确认没有把密钥、账号、真实人名带进去。 |
| `scripts/corematrix.py` | 189 | 0 | 5 | 核心机制测试：亲密度增幅、关系转变、性格、恋人与亲密引导（隔离库）。 |
| `scripts/corpus_bili.py` | 262 | 0 | 7 | 采集 B 站公开 vlog 的标题/简介/热评，落成"人类日常"原始素材。 |
| `scripts/corpus_chat.py` | 980 | 3 | 28 | 把本机微信聊天导出（`Chat/`，已 gitignore）转成"人类社交作息"语料。 |
| `scripts/corpus_csds.py` | 438 | 2 | 12 | 用 CSDS 公开客服数据补"真实服务对话"，并清掉覆盖报告里最后一个缺口。 |
| `scripts/corpus_derive.py` | 1028 | 0 | 33 | 从官方统计公报 + B 站 vlog 片段派生 rhythm（作息）与 activity（活动）语料。 |
| `scripts/corpus_partner.py` | 555 | 1 | 21 | 把 `语料/` 里的伴侣素材做成 partner 语料（相爱 85% / 吵架 15%，吵架硬上限）。 |
| `scripts/corpus_special.py` | 787 | 0 | 18 | 特殊日作息派生：把用户挑的 5 条 B 站 vlog 事实推成 5 条 rhythm。 |
| `scripts/corpus_stories.py` | 95 | 0 | 2 | 用户口述的"陌生人/平台"真实经历 → 结构化 social_scene（不装原文）。 |
| `scripts/corpus_stranger.py` | 458 | 1 | 13 | 用 CrossWOZ 公开数据补"陌生人 / 服务人员"场景（Apache-2.0）。 |
| `scripts/export_corpus.py` | 181 | 0 | 6 | **语料出口**：`events` → `{context, reply}` 训练对，只读库 + PII 脱敏，落 `exports/`。 |
| `scripts/fetch_stickers.py` | 215 | 0 | 5 | 抓取 aigengtu「蓝色大肥鱼档案馆」全量表情包到本地。 |
| `scripts/fullcheck.py` | 705 | 3 | 6 | AI-Life 全量体检（不登 QQ、断网、独立临时库）。 |
| `scripts/fullmatrix.py` | 370 | 0 | 5 | 全机制覆盖测试（隔离库）：把内核每个机制都过一遍，输出矩阵。 |
| `scripts/handoff.py` | 129 | 0 | 4 | 生成交接快照。 |
| `scripts/humanity_check.py` | 389 | 5 | 7 | 拟人度体检：她像不像一个"活人"，而不是一台可拼装的机器。 |
| `scripts/life_corpus.py` | 459 | 0 | 9 | life corpus 命令行：validate / coverage / report / fetch。 |
| `scripts/migrate_memory_v2.py` | 63 | 0 | 1 | 衡忆 2.0 迁移 / 诊断 CLI（docs/49 §6）。 |
| `scripts/parity_desktop.py` | 70 | 0 | 0 | 对照测试：电脑版内核（Python）在同组输入下的表现。 |
| `scripts/qq_probe.py` | 169 | 0 | 8 | QQ 官方开放平台凭据探测（不连 WebSocket，只打两个 HTTP 端点）。 |
| `scripts/release.py` | 83 | 0 | 3 | 源码备份打包：两个 zip（主程序 / 插件），纯备份，不生成任何额外文件。 |
| `scripts/routing_demo.py` | 35 | 0 | 0 | 打印模型切换规则与当前配置，用于核对"到底什么时候换模型"。 |
| `scripts/sim_group.py` | 116 | 0 | 2 | 群聊模拟压测（P1-1）：用 OneBot v11 上报格式注入群消息，观察她在群里的表现。 |
| `scripts/smoke.py` | 2370 | 6 | 9 | 冒烟测试：不依赖网络，验证 M0 全链路。 |
| `scripts/tcc_probe.py` | 110 | 0 | 4 | 腾讯频道 CLI 探针：一条命令看清「登录态 / 通知 / 频道数据」的真实形状。 |
| `scripts/test_intimacy.py` | 294 | 0 | 2 | — |
| `scripts/test_lifecorpus.py` | 890 | 1 | 19 | life corpus 管线自测：不依赖 pytest，只用标准库，退出码 0 表示全过。 |
| `scripts/verify_all.py` | 164 | 0 | 3 | 全功能自检总控：一条命令跑完所有自检，产出可复核的 Markdown 报告。 |

### 3.2 `scripts/live/`

**真机联调专用**：`serve_live.py` 起内核、`final_battery.py` 基础电池、`explicit_after_loosen.py` 露骨场景、`live_qq_*` 采集与驱动。→ docs/21

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `scripts/live/explicit_after_loosen.py` | 75 | 0 | 3 | 松绑后的露骨场景验证（恋人已绑定、affinity 95、官方通道）。 |
| `scripts/live/final_battery.py` | 165 | 0 | 7 | 最终真机全量回归（松绑 + 劝睡优化 + 多气泡兼容 + 通道能力位）。 |
| `scripts/live/live_battery_http.py` | 240 | 0 | 7 | 真机全量电池 HTTP 直连版：不依赖 NapCat，直接驱动内核的 `/v1/console/send`。 |
| `scripts/live/live_lexicon_cover.py` | 228 | 0 | 4 | 词库覆盖率测试：把词库塞进 prompt，量模型能激活多少。 |
| `scripts/live/live_model_probe.py` | 195 | 0 | 6 | 模型对比探针：同一 prompt 换模型，看谁的直白度更高。 |
| `scripts/live/live_prompt_probe.py` | 215 | 0 | 5 | Prompt 探针：不改源码，只验"许可与范本给足"之后的写法差异。 |
| `scripts/live/live_qq_campaign.py` | 293 | 0 | 8 | 真机全量对抗战役（123456789 × 官方 bot 666777888）。 |
| `scripts/live/live_qq_capture.py` | 127 | 0 | 4 | 真机聊天采集器：轮询真实 events 表，把每一条 QQ 消息连同她的回复、meta、状态一起落盘。 |
| `scripts/live/live_qq_duel.py` | 194 | 0 | 5 | 真机对抗驱动：用 NapCat（123456789 星辰）当“用户”，官方 bot「屑鲸鱼」当“她”。 |
| `scripts/live/live_qq_ping.py` | 68 | 1 | 1 | 真机主动消息探针：用官方 bot 主动给「阿星」私聊 + 群 555666777 各发一条。 |
| `scripts/live/serve_live.py` | 43 | 0 | 0 | 真机测试专用内核启动器（不进仓库、不改用户配置）。 |

## 4. vendor/ 只读引擎

### `vendor/symvolve_memory/`

引擎本体：schema/recall/heat/embedding/迁移/对外 API。→ docs/07

| 文件 | 行数 | 类 | 函数 | 首行职责（模块 docstring 首句） |
|---|---|---|---|---|
| `vendor/symvolve_memory/__init__.py` | 163 | 0 | 0 | Symvolve Memory: model-agnostic, multi-user memory engine (衡忆 2.0 core). |
| `vendor/symvolve_memory/capabilities.py` | 57 | 1 | 0 | Capability switches. |
| `vendor/symvolve_memory/clock.py` | 42 | 3 | 0 | Injectable clocks so decay and recall are testable without waiting. |
| `vendor/symvolve_memory/config.py` | 66 | 1 | 3 | Runtime settings. Every field can be overridden through SYMM_* env vars. |
| `vendor/symvolve_memory/decay.py` | 225 | 1 | 13 | Clarity decay, heat and the forgetting decision (M2). |
| `vendor/symvolve_memory/entities.py` | 143 | 0 | 5 | 实体归一化：代码、工单号、标的、版本号这类「唯一标识」的精确匹配。 |
| `vendor/symvolve_memory/errors.py` | 26 | 6 | 0 | Error hierarchy for Symvolve Memory. |
| `vendor/symvolve_memory/lifecycle.py` | 465 | 3 | 4 | Memory lifecycle (M2): score, decay, refresh, recall, merge, supersede, compress |
| `vendor/symvolve_memory/models.py` | 285 | 11 | 6 | Symvolve Memory data models. |
| `vendor/symvolve_memory/recall.py` | 964 | 7 | 6 | Recall engine (M3): cue matching, scoring, status, confidence, audit log. |
| `vendor/symvolve_memory/render.py` | 397 | 3 | 5 | Render layer (M3): temperature tiers, token budget, cache write-through. |
| `vendor/symvolve_memory/scoring.py` | 248 | 3 | 6 | Rule-based importance scoring (M2). |
| `vendor/symvolve_memory/stability.py` | 666 | 2 | 12 | 工业稳定性验证（离线自检工具，不进运行时热路径）。 |
| `vendor/symvolve_memory/timeline.py` | 807 | 7 | 10 | 时间线视图（工业线 T1）：按实体与时间窗还原「结论是怎么演变的」。 |

> `vendor/` 整目录只读：升级方式是整目录覆盖，不在原地改。详见 docs/07。

## 5. 非 Python 资产

### 5.1 `panel/` 前端（纯静态，无构建）

| 文件 | 行数 | 体积 | 作用 |
|---|---|---|---|
| `panel/.preview-scroll.html` | 2240 | 93.8 KB | — |
| `panel/assets/README.md` | 12 | 0.5 KB | 资源说明 |
| `panel/assets/whale-logo-128.png` | 247 | 35.3 KB | 图标 128px |
| `panel/assets/whale-logo-256.png` | 862 | 115.7 KB | 图标 256px |
| `panel/assets/whale-logo-64.png` | 95 | 10.4 KB | 图标 64px |
| `panel/assets/whale-logo.ico` | 1492 | 174.0 KB | 图标 |
| `panel/console.html` | 3081 | 134.7 KB | 控制台页（记忆/关系/群/开发者） |
| `panel/index.html` | 2740 | 118.1 KB | 主聊天页 |
| `panel/settings.html` | 1799 | 75.6 KB | 设置页（模型/人设/关系/记忆/作息） |
| `panel/symvolve-components.js` | 2820 | 126.6 KB | 自研组件库 |
| `panel/symvolve-tokens.css` | 132 | 4.5 KB | 设计令牌 |
| `panel/trend.html` | 951 | 37.8 KB | 热梗页 |

### 5.2 `persona/` 人格包

| 文件 | 行数 | 作用 |
|---|---|---|
| `persona/backup/README.md` | 14 | 备份目录说明 |
| `persona/backup/whale-chan-劝睡前-20261101-013303.yaml` | 286 | 人设备份 |
| `persona/backup/whale-girl-原版-20261028.yaml` | 231 | 人设备份 |
| `persona/templates/记忆交接信.txt` | 47 | 记忆交接信模板 |
| `persona/whale-chan.yaml` | 286 | **当前生效**人设（小鲸） |
| `persona/whale-girl.yaml` | 231 | 备用人设（小涵） |

### 5.3 `samples/` 示例数据

| 文件 | 行数 | 作用 |
|---|---|---|
| `samples/life_corpus/README.md` | 140 | 示例/测试夹具 |
| `samples/life_corpus/activity.sample.jsonl` | 3 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/README.md` | 15 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/gbk-page.html` | 12 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/robots-allow.txt` | 4 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/robots-disallow.txt` | 3 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/tiny.html` | 21 | 示例/测试夹具 |
| `samples/life_corpus/fixtures/urls-demo.txt` | 6 | 示例/测试夹具 |
| `samples/life_corpus/rhythm.sample.jsonl` | 3 | 示例/测试夹具 |
| `samples/life_corpus/snippet.sample.jsonl` | 3 | 示例/测试夹具 |
| `samples/life_corpus/social_scene.sample.jsonl` | 3 | 示例/测试夹具 |
| `samples/sample-chat.txt` | 38 | 示例/测试夹具 |
| `samples/记忆交接信_示例.txt` | 39 | 示例/测试夹具 |

### 5.4 `docs/` 文档

| 文件 | 作用 |
|---|---|
| `docs/00-新会话交接提示词.md` | 技术文档（本章所在体系） |
| `docs/10-程序结构说明.md` | 技术文档（本章所在体系） |
| `docs/11-技术设计说明.md` | 技术文档（本章所在体系） |
| `docs/12-测试方法与验收.md` | 技术文档（本章所在体系） |

## 6. 根目录文件

| 文件 | 作用 | 是否入库 |
|---|---|---|
| `.gitignore` | 忽略规则：`.venv` / `__pycache__` / `data/` / `*.log` / `config.local.yaml` / `Chat/` / `Girl-memory/` 等 | 是 |
| `config.yaml` | 三层配置的第一层（提交进仓库） | 是 |
| `config.local.yaml` | 第二层：本地真实 key/token，覆盖 config.yaml | **否** |
| `requirements.txt` | 运行依赖（fastapi/uvicorn/pyyaml/httpx 等） | 是 |
| `requirements-qq.txt` | 官方 bot 额外依赖（botpy） | 是 |
| `launcher.py` | 4 行：`python -m app.cli serve` 的最薄启动垫片 | 是 |
| `ai-life.spec` | PyInstaller 打包描述（打包需用户开口，脚本 release.ps1） | 是 |
| `README.md` | 项目门面 | 是 |
| `LICENSE` | SymBot 个人使用许可协议 1.1（source-available，非 OSI 开源） | 是 |
| `START-HERE.md` | 新会话入口：铁律 + 开工顺序 | 是 |
| `TASKS.md` | 任务板：现状快照、写域划分、并发编排、验收命令 | 是 |
| `ROADMAP.md` | 改进方向与优先级（发布工程化 / 数据安全 / 可观测性…） | 是 |

## 7. 只读区与禁区（改之前先看这里）

| 路径 | 规矩 |
|---|---|
| `vendor/symvolve_memory/**` | **只读**。升级 = 整目录覆盖，不在原地改。 |
| `memories`（视图） | **只读**。写记忆只走 `app/memory/h2.py: add()`；对它执行 DELETE 会被 D1 回归抓住。 |
| `app/relation/engine.py: apply_appraisal()` | 关系数值的**唯一**结算入口，别的路径改四维都算越权。 |
| `app/core/continuity.py: record()` | 改她 / 改状态必须留痕，漏了就是不可审计的改动。 |
| `data/` | 不入库。里面是她的运行数据（`life.db`、`archives/`、`human_life/`、`stickers/`、`voices/`）。 |
| `Chat/`、`Girl-memory/` | 用户私人数据，`.gitignore` 忽略，绝不入库、绝不入包。 |
| `config.local.yaml` | 含明文 key/token，不入库。 |

## 8. 交叉引用

- 目录为什么这么切：docs/02
- 每个目录的机制细节：docs/04 – docs/16
- 表结构：docs/17｜配置：docs/18｜测试：docs/22
