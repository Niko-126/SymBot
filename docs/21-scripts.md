# 21 · 脚本与运维（scripts）

> 注：`scripts/live/*` 是开发仓里的真机对抗脚本，**发布包不含**；本章相关段落仅作方法记录。

> 覆盖：`scripts/*.py`（31 个）+ `scripts/live/*.py`（11 个），共 42 个文件 / 14,598 行｜基于 2026-11-01 的代码状态（移除 R 级插件残留、新增语料出口后重扫）｜上游：docs/03（文件全表）、docs/16（`app/qa/` 底座）｜下游：docs/22（测试与验收）

## 1. 这一层干什么

`scripts/` 是这个项目唯一的"施工与验收"入口：它不进运行时、不被 `app/` import、也不参与打包后的服务链路（`smoke.py` 只被 `handoff.py`、`verify_all.py` 用子进程调）。这一层解决三个问题：

1. **回归**：改一行代码后，用 `smoke.py`（387 条断言）判断"有没有把已经好的东西改坏"。
2. **摸底**：`fullcheck.py`（150 项 / 110 条路由模板）、`humanity_check.py`（27 分制）、`corematrix.py`、`fullmatrix.py` 负责把"功能面"和"人的属性"两件事分别压一遍。
3. **真机联调与素材生产**：`scripts/live/` 七个脚本对接 NapCat + 官方 bot 做真机对抗；`corpus_*.py` 八个脚本把外部素材转成 `data/human_life/_derived` 语料。

它明确**不做**的事：不建库、不迁移表结构（迁移在 `app/core/db.py: init()` 与 `migrate_memory_v2.py`）、不改人设文件、不打包。

**花钱/联网名单（改脚本前先看这张表）**：`scripts/live/` 里除 `live_qq_capture.py`、`live_qq_ping.py` 之外的五个、`sim_group.py`、`parity_desktop.py`、`corematrix.py`、`fullmatrix.py` 会真调模型；其余脚本全部 mock + 临时库 + 断网，零花费。后两个（`corematrix.py`/`fullmatrix.py`）的原因写在 §3.2 的"坑"里：它们**没有设 `SYMEM_MOCK`**，配了 key 的机器上会真的花钱。

## 2. 文件清单（36 个，逐个列全）

| 脚本 | 行数 | 一句话用途 | 需要真实 key | 零花费 |
|---|---|---|---|---|
| `scripts/smoke.py` | 2368 | 冒烟回归基线，TestClient 打全链路，387 条断言 | 否（`AI_LIFE_MOCK=1`） | 是 |
| `scripts/fullcheck.py` | 703 | 全量体检：OpenAPI 全路由 + 12 个功能区语义 | 否（mock + 断网垫片） | 是 |
| `scripts/humanity_check.py` | 387 | 拟人度体检：A–G 七组，27 分制 | 否 | 是 |
| `scripts/test_intimacy.py` | 294 | 亲密模式与慢思考自测，60 条断言 | 否 | 是 |
| `scripts/test_lifecorpus.py` | 890 | 语料管线自测（schema/pii/validate/coverage/CLI/fetch），72 条断言 | 否 | 是 |
| `scripts/panel_check.py` | 158 | 面板自检（120 项）：页面能开 / 接口对得上 / 元素找得到 / 契约不漂移 / 保存能走通 / TTS 优雅拒绝 | 否 | 否（测试库） |
| `scripts/verify_all.py` | 193 | 自检总控：按依赖顺序跑 11 项（含面板自检），缺语料的两项 SKIP，出 Markdown 报告 | 否 | 是 |
| `scripts/adv_full.py` | 335 | 对抗式全量训练编排器，8 个 phase | 仅 `--mode live --allow-live` | 默认是 |
| `scripts/adv_list.py` | 87 | 把一次 run 的 `items.json` 摊成 Markdown 清单 | 否 | 是 |
| `scripts/adv_regress.py` | 618 | 缺陷回归：D1–D31 三十一条"曾经真坏过"的场景 | 否 | 是 |
| `scripts/corematrix.py` | 189 | 核心机制数值测试：增幅、关系转变、性格、恋人、亲密引导 | 视配置（未设 `SYMEM_MOCK`） | 视配置 |
| `scripts/fullmatrix.py` | 370 | 全机制覆盖矩阵：每个机制都过一遍 | 视配置（未设 `SYMEM_MOCK`） | 视配置 |
| `scripts/life_corpus.py` | 459 | 语料命令行：validate / coverage / report / fetch | 否（ferch 需网络但不需 key） | 是 |
| `scripts/corpus_bili.py` | 262 | 抓 B 站公开 vlog 标题/简介/热评 → `_raw` | 否 | 是 |
| `scripts/corpus_chat.py` | 980 | 微信导出 `Chat/` → 聚合统计 + 掩码后的社交语料 | 否 | 是 |
| `scripts/corpus_csds.py` | 438 | CSDS 客服数据 → 补 `ask_later` 打断策略缺口 | 否 | 是 |
| `scripts/corpus_derive.py` | 1028 | 统计公报 + vlog 事实 → rhythm / activity | 否 | 是 |
| `scripts/corpus_partner.py` | 555 | `语料/` 伴侣素材 → partner 语料（相爱 85% / 吵架 15%） | 否 | 是 |
| `scripts/corpus_special.py` | 787 | 5 条 vlog 事实 → 5 条特殊日 rhythm | 否 | 是 |
| `scripts/corpus_stories.py` | 95 | 用户口述见闻 → 2 条结构化 social_scene | 否 | 是 |
| `scripts/corpus_stranger.py` | 458 | CrossWOZ 公开数据 → 陌生人/服务人员场景 | 否（`--download` 才联网） | 是 |
| `scripts/fetch_stickers.py` | 215 | 抓 aigengtu 表情包全量到 `data/stickers/` | 否 | 是 |
| `scripts/check_secrets.py` | 111 | 打包前扫密钥/账号/私密目录 | 否 | 是 |
| `scripts/handoff.py` | 129 | 交接快照：HEAD、工作树、产物新鲜度、运行状态 | 否 | 是 |
| `scripts/migrate_memory_v2.py` | 63 | 衡忆 2.0 迁移/诊断（`--status`/`--dry-run`/`--apply`） | 否 | 是 |
| `scripts/parity_desktop.py` | 68 | 电脑版内核同组输入的 JSON 快照，供与手机 JS 内核对比 | **视配置**（有 key 就真调 5 轮） | 视配置 |
| `scripts/qq_probe.py` | 169 | QQ 官方平台凭据探测（两个 HTTP 端点） | **是**（appid + secret） | 是（免费接口） |
| `scripts/release.py` | 83 | 源码备份打包：主程序 / 插件两个 zip，纯备份不生成别的 | 否 | 否 |
| `scripts/export_corpus.py` | 181 | **语料出口**：`events` → `{context, reply}` 训练对，只读库 + PII 脱敏 | 否 | 否（只读，写到 `exports/`） |
| `scripts/routing_demo.py` | 35 | 打印模型切换规则与各场景实际选择 | 否 | 是 |
| `scripts/sim_group.py` | 116 | 群聊模拟压测：OneBot v11 上报格式注入 `/onebot/event` | 需内核在跑 | 视服务配置 |
| `scripts/tcc_probe.py` | 110 | 腾讯频道 CLI 探针（登录态/通知/频道数据） | 否 | 是 |
| `scripts/live/serve_live.py` | 43 | 真机专用内核启动器（关 OneBot、抬上限、日志落文件） | **视配置**（真机必然真调） | 否 |
| `scripts/live/final_battery.py` | 165 | 真机最终电池：风格/绑定/露骨/记忆/群聊/对抗 + 多气泡 | **是** | 否 |
| `scripts/live/explicit_after_loosen.py` | 72 | 松绑后露骨场景验证（4 句，走 NapCat 私聊） | **是** | 否 |
| `scripts/live/live_qq_campaign.py` | 293 | 真机全量对抗战役，9 个 phase | **是** | 否 |
| `scripts/live/live_qq_capture.py` | 127 | 真机采集器：只读轮询 `events` 表落 transcript/inspect/state | 否 | 是 |
| `scripts/live/live_qq_duel.py` | 194 | 真机对抗驱动：NapCat 当用户、官方 bot 当她，24 句/18 句逐句真发 | **是** | 否 |
| `scripts/live/live_qq_ping.py` | 68 | 主动消息探针：C2C + 群各发一条验证链路 | **是**（官方 bot 凭据） | 是（发消息本身不花钱） |
| `scripts/live/live_battery_http.py` | 240 | 真机全量电池 HTTP 直连版：不依赖 NapCat，走 `/v1/console/send` | **是** | 否 |
| `scripts/live/live_lexicon_cover.py` | 228 | 词库覆盖率：词库塞进 prompt，量模型能激活多少 | **是** | 否 |
| `scripts/live/live_model_probe.py` | 195 | 2×4 对照（两套 system × 四个模型），看是谁在拒 | **是** | 否 |
| `scripts/live/live_prompt_probe.py` | 215 | Prompt 探针：只换写法约束，验"许可与范本给足"的差异 | **是** | 否 |

行数合计 14,598 行，与 docs/03 的文件全表逐行对齐。

## 3. 模块逐一详解

### 3.1 测试基线（6 个）

| 脚本 | 断言规模 | 隔离数据根 | 断网方式 | 实测（2026-11-01 本机） |
|---|---|---|---|---|
| `smoke.py` | 387 条 `check()` | `tempfile.mkdtemp(prefix="ai-life-smoke-")` | 不用网络（mock provider + TestClient） | **386 通过 / 1 失败** |
| `fullcheck.py` | 150 项（含 S11 动态扫全路由） | `mkdtemp(prefix="ai-life-full-")` | `urllib.request.urlopen = _offline` + httpx 假客户端 | **150/150 通过，路由模板 110/110** |
| `humanity_check.py` | 27 分制（A–G 七组） | `mkdtemp(prefix="ai-life-human-")` | 同款 `_offline` 垫片 | **27/27** |
| `test_intimacy.py` | 60 条 | `mkdtemp()/smoke-i.db` | mock | **60 通过 / 0 失败** |
| `test_lifecorpus.py` | 72 条 | 全部 `mkdtemp()`，不碰 data/ | `monkeypatch urllib.request.urlopen` | **72/72，全部通过** |
| `verify_all.py` | 11 项子进程（quick 9 项；缺语料时 2 项 SKIP） | 由被调脚本各自决定 | 由被调脚本各自决定 | 见 §5.1 |

**`smoke.py`（2368 行）**

- 命令行参数：**没有**。脚本不解析 argv，`python scripts/smoke.py` 直接跑到底。
- 环境变量（第 17–22 行，import `app.main` 之前写死）：`AI_LIFE_DB=<TMP>/smoke.db`、`AI_LIFE_DATA=<TMP>`、`pop AI_LIFE_API_KEY`、`AI_LIFE_MOCK=1`、`AI_LIFE_IDLE=0`（注释写明：夜间独处由 [45] 段自己显式测，别让后台任务干扰计数）。
- 期间还会临时改 `AI_LIFE_IDLE`（1665）、`AI_LIFE_HUMAN_LIFE_DIR`（1777/1844/1847/1891/1987/2086/2132）、`AI_LIFE_STYLE_DIR`（2239/2343），每段用 `try/finally` 还原。
- 结构：`[1]` 健康检查 → `[1.5]` 命名流程 → … → `[56]` 语气层（恋人/外人/群聊/面板）→ 末尾 `print(f"结果：{ok} 通过，{fail} 失败")`。
- 退出码：`sys.exit(0 if fail == 0 else 1)`。
- 产出：只打印，不落盘；临时库路径不删（异常时留给人工看）。

**`fullcheck.py`（703 行）**

- 命令行参数：无。
- 环境变量：`AI_LIFE_DB/DATA/MOCK/IDLE` 五件套（31–36 行），并 `pop AI_LIFE_API_KEY`、`pop AI_LIFE_PERSONA`。
- 三重垫片：`urllib.request.urlopen = _offline`（联网查词/B 站全部抛 `URLError`）、`httpx.AsyncClient` 换成本地 `_FakeAsyncClient`（OneBot/NapCat 返回 `{"status":"ok"}`）、`AI_LIFE_MOCK=1`（LLM）。
- 12 个 section：S1 静态与元信息、S2 对话/上下文/名字/记性、S3 记忆接口/引擎、S4 导入/参考语料/灵魂档案、S5–S10 其余功能区（含 S10 异常与权限）、S11 路由覆盖率兜底、S12 结构收尾。
- S11 是真扫：`oa = app.openapi()` 取 `paths`，用 `fill(schema)` 按 schema 造参数，把 `HIT` 里没打过的 `(method, path)` 全补一遍，`502/503` 且路径含 `onebot`/`group`/`trend`/`console/send`/`console/groups`/`soul/import` 判为"断网降级"进 `SKIP`。
- 退出码：`sys.exit(0 if not failures else 1)`，失败明细打在末尾。

**`humanity_check.py`（387 行）**

- 命令行参数：无。环境变量同 fullcheck 五件套 + 同款断网垫片。
- 七组：A 身体与时间（7 项）、B 记忆连续性（4）、C 不可编辑只可遗忘（4）、D 关系惯性（3）、E 不确定与语言（2）、F 反拼装审计（3）、G 变化留痕（4）= 27 分。
- A 组按**清醒度模型**验（2026-11-01 重写）：`awake_level` 归零后第一条只回两三个字且 `meta.mode ∈ {asleep,drowsy}`；第二句之后 `awake_level()` 严格上升；把 `awake_level` 钉在 0.00/0.30/0.90 分别得到 `asleep/drowsy/awake` 且同一值可重复；另验 `busy`/`sleepy`/`awake` 三条旁路。
- 关键断言：视图行数 ≤ 引擎活跃条目（B 组）、没有 `update_memory`/`edit_memory`/`set_content`/`rewrite_memory` 这类接口（C 组）、遗忘是软删除（`forgotten=1` 且视图不见）、重复遗忘返回 `False`。
- 退出码：`sys.exit(0 if not fails else 1)`。全局分与"拟人度总分：N/27"那行由汇总段打印。

**`test_intimacy.py`（294 行）**

- 无 docstring（源码第 1 行直接是 `import sys, os, tempfile, pathlib`），用途靠读代码：亲密模式（exemplars）+ 慢思考链路。
- 环境：`AI_LIFE_DB=<mkdtemp()>/smoke-i.db`、`AI_LIFE_MOCK=1`，**在 import `app.main` 之前**设置。
- 断言 60 条，含"慢思考带查到的信息"（`"刚上网查了" in runtime.get().last_prompt["system"]`）、"慢思考有标记"（`meta.slow is True and meta.lookup_prefetched is True`）。
- 退出码：`exit(0 if fail == 0 else 1)`（用的是内建 `exit`，不是 `sys.exit`，语义相同）。

**`test_lifecorpus.py`（890 行）**

- 无参数；纯标准库、无 pytest。`sys.path` 同时插入仓库根与 `scripts/`，所以能 `import life_corpus`。
- 数据源：`samples/life_corpus/`（4 个 `.sample.jsonl` + `README.md`）与 `samples/life_corpus/fixtures/`（`gbk-page.html`、`robots-allow.txt`、`robots-disallow.txt`、`tiny.html`、`urls-demo.txt`）。
- 覆盖六层：schema、pii、validate、coverage、CLI 退出码、fetch（HTML→文本、GBK 兜底、robots 拒绝、异常不外抛、`max_bytes` 截断、片段切分、dry-run 零联网、manifest 字段）。
- 退出码：有失败 `return 1`，全过 0；末尾打印 `合计：通过 72 / 失败 0 / 断言 72`。

**`verify_all.py`（193 行）**

- 参数：`--quick`（跳过 fullcheck 与 handoff，约 1 分钟）、`--no-report`（只打印不写报告）。
- `CHECKS` 是 11 元组（名字, 命令, 超时秒, quick 也跑）：编译检查（`compileall -q app scripts`，180s）、smoke（900s）、语料自测（300s）、亲密（300s）、全量体检（900s，quick 跳过）、拟人度（300s）、交接快照（`handoff.py --test`，300s，quick 跳过）、语料校验（`life_corpus.py validate data/human_life/_derived --strict`，120s）、语料覆盖（`life_corpus.py coverage … --strict`，120s）、记忆引擎（`migrate_memory_v2.py --status`，120s）、面板自检（`panel_check.py`，300s，quick 也跑）。
- 结论行抽取：`_PATTERNS` 六条正则（`结果：(\d+) 通过，(\d+) 失败` 等）把各脚本的汇总行直接搬进报告（面板自检走的也是这条，报告里会写"结果：120 通过，0 失败"）。
- 产出：`data/human_life/_reports/verify-<YYYYmmdd-HHMMSS>.md`（实测已有 19 个）；超时记 124、spawn 失败记 2。
- 退出码：`0 if ok == len(results) else 1`。
- 坑：第 125–129 行那段 `"未通过：" + … if failed else "全部检查退出码为 0。" " …"` 的条件表达式写错优先级——`failed` 非空时，else 分支里那句解释文字被整段吞掉，报告里只剩"未通过：A、B"。

### 3.2 对抗矩阵（5 个）

**`adv_full.py`（335 行）** — 编排器，唯一会把 `app/qa/` 全底座拉起来的脚本。

- 参数（11 个，真名真默认值）：`--phase all`（可逗号分隔）、`--mode offline`（`offline`/`live`）、`--allow-live`（flag）、`--only ""`、`--turns 0`、`--budget 200000`、`--levels "L0,L1,L2"`、`--limit 0`、`--out ""`、`--no-keep`（flag）。
- 环境：`from app.qa import sandbox; TMP = sandbox.prepare_env("full", keep=("--no-keep" not in sys.argv))`——**必须在 import `app.*` 之前**，`sandbox.prepare_env` 会检查 `app.core.paths` 是否已 import，晚了直接抛 `SandboxError`。它钉死 `SYMEM_DATA/SYMEM_DB/SYMEM_MOCK=1/SYMEM_QQ_ENABLED=0/SYMEM_ONEBOT_ENABLED=0/SYMEM_CONSOLE_TOKEN=""`。
- 8 个 phase（`PHASES` 元组）：`inventory`（`inventory.build()` 出清单）、`static`（module/class/config/persona/table 验证）、`routes`（`routes_fuzz.run` 异步逐个打）、`functions`（`probe.run(levels, limit)`）、`cli`（子命令 + 后台任务，子进程跑）、`duel`（场景对抗，`--mode live` 未加 `--allow-live` 时**强制回落 offline**）、`coverage`、`report`。
- 产出：`data/_adv/run-<YYYYmmdd-HHMMSS>/`，含 `manifest.json`、`items.json`、`findings.json`、`phases/*.json`、`transcripts/`、`logs/`、`report.md`、`report.json`。
- 退出码：`1 if sev.get("P0", 0) > 0 else 0`。
- 坑：docstring 写"Phase 崩溃 → 2"，但代码里 phase 异常被 `try/except` 吃掉并记进 `phases["_error"]`，`main()` **从不返回 2**；另外 `phase_coverage` 在 `want` 循环里跑一次、`finally` 里又无条件跑一次（覆盖 `phases/coverage.json`）。

**`adv_list.py`（87 行）** — 纯只读视图，不执行任何被测代码。

- 参数：`--run ""`（不给就取 `data/_adv/` 下最新的、含 `items.json` 的目录）、`--status ""`（逗号分隔，如 `RAISED,NOTRUN,SKIP`）、`--kind ""`（`fn,method,route,cli,table,config,scenario`）、`--module ""`（模块前缀）、`--max 0`、`--out ""`。
- 产出：终端打印 8 列 Markdown 表，或 `--out` 指定文件。
- 退出码：找不到 `items.json` → 2（stderr 提示先跑 `adv_full.py --phase inventory`）；正常 0。

**`adv_regress.py`（618 行）** — 三十一条回归，全部是"曾经真的坏过"的具体场景。

| ID | 钉住的行为 |
|---|---|
| D1 | `purge_self` 不再 `DELETE` 视图（软删记忆 + 清人） |
| D2 | 问句/否定句不能把关系绑成恋人 |
| D3 | 问句之后仍可用「我答应你」正常绑定 |
| D4 | 傍晚「早点睡吧」不再被判时间感违规 |
| D5 | CLI `ingest` 缺文件 → 友好报错，不甩 traceback |
| D6 | CLI `reset` 在独立进程里能正常删库（退出码 0） |
| D7 | 恋爱线的确定性回复也写进 `events`（上下文不断裂） |
| D8 | 日常句不再被误判为表白 |
| D9 | 记忆里不许出现 openid（用名字替换） |
| D10 | 睡眠不是硬闸门：越聊越清醒、停聊睡回去 |
| D11 | 命名剧本台词带 `user_id/chat_type`（上下文不断裂） |
| D12 | 命名流程能走到锁定（带标点的「就叫小满。」） |
| D13 | 夸奖不能被当成同意（不会误绑关系） |

- 参数：`--json ""`（结果落盘）。环境：`SYMEM_DATA/SYMEM_DB/SYMEM_MOCK=1/SYMEM_QQ_ENABLED=0/SYMEM_ONEBOT_ENABLED=0/PYTHONIOENCODING`，目录 `mkdtemp(prefix="smd_regress_")`。
- 退出码：`0 if passed == len(RESULTS) else 1`。

**`corematrix.py`（189 行）** — 测"数值与状态怎么变"（与 fullmatrix 分工：那边测功能存不存在）。

- 无参数（不解析 argv）。环境：`SYMEM_DATA`、`SYMEM_DB`、`SYMEM_QQ_ENABLED=0`、`SYMEM_ONEBOT_ENABLED=0`，目录 `mkdtemp(prefix="smd_core_")`。
- 断言按 `R.` 关系数值 / `R1–R3` 增幅与辱骂降好感等分组，`record()` 打印 `[PASS]/[FAIL]`；第 149/159 行两处 `await loop.chat_turn(...)` 是真实对话轮。
- 产出：`data/_verify/corematrix.json` —— 注意这是 `pathlib.Path("data/_verify")` 的**相对路径**，落在**当前工作目录**，不在临时数据根里。
- 退出码：`0 if ok == len(RESULTS) else 1`。
- 坑：**没有设 `SYMEM_MOCK`**。数据库隔离了，但 provider 走真实配置——这台机器配了 key 就会真调模型（两轮 `chat_turn`），"零花费"对它不成立。

**`fullmatrix.py`（370 行）** — 全机制矩阵。

- 参数：`--json ""`。环境：`_prepare_env()` 里只钉 `SYMEM_DATA`、`SYMEM_DB`、`SYMEM_QQ_ENABLED=0`、`SYMEM_ONEBOT_ENABLED=0`、`PYTHONIOENCODING` 五个，目录 `mkdtemp(prefix="smd_matrix_")`。
- 失败也继续跑完（每条 try/except 收敛成一条 FAIL 记录）。
- 退出码：`0 if ok == total else 1`；`--json` 时额外写结果文件。
- 坑：同 `corematrix.py`，**缺 `SYMEM_MOCK`**；且第 70 行与第 325 行都真调 `loop.chat_turn`，第 325 行那条故意走"失败路径"（`source="matrix_fail"`），配了 key 的机器上同样会产生真实请求。

### 3.3 真机联调（`scripts/live/`，7 个）

这一组的共同点：**都假设真库 `data/life.db` + 真内核 + 真通道**，不做隔离，发出去的消息真的会到手机上。

**`serve_live.py`（43 行）—— 这一组的地基，也是最特殊的一个。**

它做的事（逐行）：

1. `REPO = pathlib.Path(r"<发布目录>")`，`sys.path.insert` 后 `os.chdir(REPO)`——**绝对路径写死**，只能在这台机器上用。
2. `os.environ["SYMEM_ONEBOT_ENABLED"] = "0"`，注释：「关键：不碰 NapCat 那条线」。
3. `os.environ["SYMEM_QQ_ENABLED"] = "1"`（走官方 botpy 通道）。
4. `os.environ["PYTHONIOENCODING"] = "utf-8"`。
5. 日志：`logging.basicConfig(level=INFO)`，`FileHandler(data/_adv/kernel-live.log)` + `StreamHandler(stdout)`，格式 `%(asctime)s %(levelname)s %(name)s: %(message)s`。
6. import `app.core.config` 后**只改内存里的单例**：`CFG.provider.max_tokens = 2000`、`CFG.provider.temperature = 0.95`、`CFG.provider.timeout_s = 120.0`——**不写 `config.local.yaml`**，所以"不改用户配置"是真的。
7. `uvicorn.run("app.main:app", host="127.0.0.1", port=8787, log_level="info")`——固定端口 8787，`handoff.py`、`live_qq_capture.py`、`final_battery.py` 都写死了这个地址。

**为什么 NapCat 登录用户本人账号时，内核必须关 OneBot**：NapCat（`http://127.0.0.1:3000`）此刻登录的是用户自己的 QQ `123456789`。OneBot 通道一旦打开，内核收到"用户"发来的消息就会用**这个号的会话**去回复和主动外发——等于她拿了主人的号在群里/私聊里说话。`app/qa` 与所有离线脚本也因此统一 `SYMEM_ONEBOT_ENABLED=0`。`docs/01` 里也复述了这条：`serve_live.py` 已在开头硬写了这一行。

坑：docstring 第 6 行说"每轮输出上限压到 800（用户配置是 393216）"，第 35 行代码写的是 `2000`——**注释与代码不一致，以代码为准**。

**`final_battery.py`（165 行）** — 真机最终电池。

- 无 argv。常量写死：`DB = data/life.db`、`BOT = 666777888`、`GROUP = 555666777`、`OUT = data/_adv/final-<ts>/`。
- 六组输入：`STYLE`（8 句日常）、`bind`（1 句表白）、`EXPLICIT`（4 句露骨）、`MEM`（4 句记忆写入/召回）、`group`（2 句群聊）、`ADS`（3 句对抗注入）。
- 每轮记录：`before/after` 状态快照（从 `user_relation`/`lover`/`kv`/`qq_user` 只读查）、`len`、`bubbles`（气泡数）、`nag`（是否劝睡，命中"睡吧/早点睡/该睡了/别熬/快去睡"）。
- 额外两段：面板多气泡走 `POST http://127.0.0.1:8787/v1/console/send`；末尾 `GET /v1/health` 抓 `channel` 与 `max_tokens`。
- 产出：`battery.jsonl`（每轮一行 + 一行 `summary`）。退出码：脚本没有 `main()`，跑完自然退 0（异常即 traceback）。

**`explicit_after_loosen.py`（72 行）** — 4 句露骨场景，`POST 127.0.0.1:3000/send_private_msg` 发给 bot `666777888`，每句轮询 `events` 表 90 秒等她的回复，落 `data/_adv/explicit-after-loosen.jsonl`（`user/her/mode/latency_ms/len`）。无参数、无隔离、跑完退 0。

**`live_qq_campaign.py`（293 行）** — 战役驱动，9 个 phase：`snapshot`、`climb --target X`、`bind`、`intimate`、`group_intimate`、`breakup`、`naming`、`memory`、`adversarial`、`repel`。

- 参数：`--phase`（**必填**）、`--target echo`（默认值必须是 `STAGE_AT` 的键：`observer/swimmer/echo/samefreq/lover/bond`）、`--max-turns 40`、`--out ""`。
- 常量：`NAPCAT=127.0.0.1:3000`、`BOT=666777888`、`GROUP=555666777`、`UID=19`；语料池 `POSITIVE`（35 句）、`SUGGESTIVE`（3）、`EXPLICIT`（2）、`INTIMATE_STOP`（1）、`FACTS`（3）、`RECALL`（3）、`ADVERSARIAL`（8）。
- `climb` 的循环：每轮从头取 `state()`，`affinity >= STAGE_AT[target]` 就 break，最多 `--max-turns` 轮。
- 产出：`--out` 指定目录下的 jsonl；未指定时的目录名由脚本内 `now()` 时间戳拼。退出码：未知 phase → 2，其余 0。

**`live_qq_capture.py`（127 行）** — 唯一**不花钱**的真机脚本。

- 参数：`--minutes 40`、`--interval 2.0`。
- 只读：`sqlite3.connect("file:...life.db?mode=ro", uri=True)` + `GET /v1/inspect`。
- 产出 4 个文件到 `data/_adv/live-qq-<ts>/`：`transcript.jsonl`（每条事件一行）、`inspect.jsonl`（她说一句抓一次 prompt 快照）、`state.jsonl`（关系/计数变了才写）、`capture.log`。
- 起手取 `SELECT COALESCE(MAX(id),0) FROM events` 作为水位，避免把历史算成新消息。退出码 0（Ctrl-C 也走 `finally` 正常收尾）。

**`live_qq_duel.py`（194 行）** — 真机逐句对抗。

- 参数：`--lane private`（`private`/`group`）、`--group 555666777`、`--out ""`、`--wait 75.0`、`--gap 1.5`。
- 私聊 24 句（`PRIVATE_LINES`，覆盖身份质疑/记忆写入召回/复读指控/提示词注入/时间感/亲密越级/情绪/注入载荷/长文本）、群聊 18 句（`GROUP_LINES`，每句都带 `@{bot}` 前缀，发送时拆成 `at` + `text` 两段）。
- 每句：`max_event_id()` 取水位 → `send_private_msg`/`send_group_msg` → 轮询 `events` 里 `role='her'` 且 `chat_type` 匹配的新行（0.8 秒一次，最多 `--wait` 秒）→ 落 `duel.jsonl`。
- 退出码：0；发不出去的句子只计入 `fail` 计数，不改变退出码。这组的告警全在 stdout 与 `duel.jsonl` 里，**不适合接 CI**。

**`live_qq_ping.py`（68 行）** — 主动消息探针。

- 无 argv。`OPENID = "49BEBF2DFA8982E91C750153D89231DE"`（`qq_user id=19`，阿星）、`GROUP_OPENID = "C113FE1C1E07D63C8338542B6E6CF6FF"` 写死。
- 用 `botpy.Client(intents=public_messages=True, is_sandbox=CFG.qq.sandbox)`，`on_ready` 里先发 C2C 再发群，然后 `close()`；整体 `asyncio.wait_for(..., timeout=75)`。
- 凭据只从 `config.local.yaml` 读，打印时 `appid` 只露前 6 位 + `***`。
- 退出码：凭据不全 2、75 秒超时 3、其它异常 1、成功 0。

### 3.4 探针（3 个）

**`qq_probe.py`（169 行）** — 判定 `config.local.yaml` 里的 `qq.token` 到底是 Secret 还是 access_token。

- 参数：`--appid ""`（覆盖 config）、`--token-file ""`（从文件读 token，避免命令行明文）、`--print-token-source`（只打印来源与掩码长度，不联网）。
- 环境：`AI_LIFE_QQ_APPID`、`AI_LIFE_QQ_TOKEN`（作为 config 之外的兜底来源）。
- 流程：① `POST https://bots.qq.com/app/getAppAccessToken`，body `{"appId","clientSecret"}`；成功说明是 Secret 并拿到 `access_token`（打印 `expires_in`）。② `GET https://api.sgroup.qq.com/gateway`，头 `Authorization: QQBot <token>`；200 且带 `url` 即链路可用。错误码提示写死：`11244`=access_token 无效/过期、`11242`=Token 错误、`10004`=机器人不存在。
- 输出：一律 `_mask()`（前 8 位 + 长度），可直接贴进聊天。
- 退出码：缺 token/缺 appid → 2；gateway 失败 → 1；成功 0。

**`tcc_probe.py`（110 行）** — 腾讯频道 CLI（`tencent-channel-cli`，Go 二进制/npm 分发）探针。

- 参数：`--out ""`（默认 `data/_tcc/probe-<时间>.json`）。
- 找 CLI：先 `shutil.which("tencent-channel-cli")`，再 `%APPDATA%\npm\tencent-channel-cli.cmd`；找不到 `raise SystemExit("没找到 …：先 npm install -g tencent-channel-cli")`。
- 依次跑 `version / doctor / login status / notices-status / get-recent-notices / check-notices / check-new-notices`，全部加 `--json`，单条 60 秒超时。
- 退出码：`login-status` 未登录 → 1，其余 0（只读，不发消息）。

**`fetch_stickers.py`（215 行）** — 表情包全量抓取。

- 参数：`--pages 41`、`--no-images`（只建索引）、`--workers 12`（**这是全仓唯一并发抓取的脚本**，`concurrent.futures` 线程池，和 `app/lifecorpus/fetch.py` 的"绝不并发"是两套东西）。
- 出口：`https://aigengtu.com/archive?p={p}` 与 `https://img.aigengtu.com/0_preview/meme/{id}.webp`，带浏览器 UA 与 `Referer`。
- 产出：`data/stickers/index.json`（id/角色/台词/情绪/title/url/file/sha256/safe）、`files/<id>.webp`、`summary.txt`。`UNSAFE_WORDS` 命中记 `safe=false`（仍下载，留人工复核）；`EMOTION_RULES` 5 组关键词映射情绪标签。
- 退出码：`sys.exit(main())`，正常 0。

### 3.5 语料生产（`life_corpus.py` + 8 个 `corpus_*.py`）

`life_corpus.py` 是 `app/lifecorpus` 的唯一命令行入口，4 个子命令，退出码三档（源码 docstring 原文）：

```text
python scripts/life_corpus.py validate  [目录] [--strict] [--no-pii]
python scripts/life_corpus.py coverage  [目录] [--strict]
python scripts/life_corpus.py report    [目录] [--out 报告路径] [--strict] [--no-pii]
python scripts/life_corpus.py fetch     --urls FILE [--out DIR] [--dry-run|--confirm]
                                        [--delay 2.0] [--max-bytes N] [--timeout 10]
```

| 子命令 | 默认目录 | 关键参数 | 退出码 |
|---|---|---|---|
| `validate` | `data/human_life`（`DEFAULT_CORPUS_DIR`） | `--strict`（有错/PII 退 1）、`--no-pii` | 0 / 1（strict 且有错）/ 2（目录不存在） |
| `coverage` | 同上 | `--strict`（有严重缺口退 1） | 0 / 1 / 2 |
| `report` | 同上 | `--out`（默认 `<目录>/report.md`）、`--strict`、`--no-pii` | 0 / 1 / 2 |
| `fetch` | `data/human_life/_raw`（`DEFAULT_RAW_DIR`） | `--urls`（**必填**）、`--dry-run`（默认）/`--confirm`、`--delay 2.0`、`--max-bytes 2000000`、`--timeout 10.0` | 0 / 1（confirm 下一条没成功）/ 2（参数或文件问题） |

`fetch` 的纪律文案是硬编码常量 `FETCH_RULES = "不登录 / 不带 cookie / 不绕验证码 / 遵守 robots.txt / 默认 dry-run"`，每次运行都打印。`--confirm` 后逐条抓、每条成功后写 `<host>-<YYYYmmdd-HHMMSS>.txt` 并往 `manifest.jsonl` 追加一行；撞名时加 `-1`/`-2` 后缀，绝不覆盖。

八个 `corpus_*.py` 的共同结构：默认 **dry-run 只打印**，加 `--write`（`corpus_derive.py`/`corpus_special.py` 是 `--dry-run` 反向）才落盘；只读原始素材、只写 `data/human_life/_derived/`；异常不外抛，单条坏了跳过并计数。

| 脚本 | 参数 | 原始素材（只读） | 产出 | 退出码 |
|---|---|---|---|---|
| `corpus_bili.py` | `--keywords`（默认 8 个词）、`--bvids ""`、`--per 2`、`--comments 10`、`--delay 1.5`、`--out _raw`、`--dry-run` | B 站公开接口（`app/web/bili.py`，wbi 签名，不登录不带 cookie） | `_raw/bili-<ts>.jsonl` + `_raw/bili-snippets-<ts>.jsonl` | 0 |
| `corpus_chat.py` | `--write`、`--chat-dir Chat`、`--out _derived`、`--max-scenes 300`、`--max-snippets 200` | `Chat/*.json`（微信导出，gitignored） | `chat_stats.json`、`chat.social_scene.jsonl`、`chat.snippet.jsonl` | 0；`--write` 后自动跑 `validate --strict`，那步非 0 则 1；缺目录 2 |
| `corpus_csds.py` | `--write`、`--data-dir data/human_life/csds` | `csds/{train,val,test}.json` | `csds.snippet.jsonl`、`csds.social_scene.jsonl`、`csds_mix.json` | 0；数据目录问题 1/2 |
| `corpus_derive.py` | `--dry-run`（默认写盘） | `_raw/www.stats.gov.cn-*.txt`、`_raw/bili-*.jsonl`、`_derived/chat_stats.json` | `rhythm.workday.jsonl`(2)、`rhythm.restday.jsonl`(1)、`activity.jsonl`(24–36) | 0；素材缺失 1/2；**异常 3** |
| `corpus_partner.py` | `--write` | `语料/相爱语料_精选.txt`(80 段)、`语料/女主_场景语料_严格版.txt`(1432 场景) | `partner.snippet.jsonl`(100)、`partner.social_scene.jsonl`(80)、`partner_mix.json` | 0；素材缺失 1/2 |
| `corpus_special.py` | `--dry-run` | `_raw/bili-20261031-070622.jsonl` | `rhythm.special.jsonl`(5) | 0；自检不过 1；**异常 3** |
| `corpus_stories.py` | `--write` | `_raw/user-report-stranger-stories.txt` | `user.stories.social_scene.jsonl`(2，`synthetic=False`) | 0 |
| `corpus_stranger.py` | `--write`、`--download`（默认不联网） | `_raw/crosswoz/{train,val,test}.json.zip` | `stranger.snippet.jsonl`(60)、`stranger.social_scene.jsonl`(60)、`stranger_mix.json` | 0；素材缺失 1/2 |

隐私铁律在这一组里是代码结构，不是口号：`corpus_chat.py` 的屏幕输出只有数字、时间、哈希前缀（"自己"按跨会话出现次数最多的 sender 判定，把该 wxid 的 sha256 前缀写进统计当证据）；`corpus_partner.py` 里显式性内容**整窗丢弃**、第三方/亲属/职业指称（"他/她/妈/朋友/同事"）整句不用；`corpus_csds.py` 不落 `DialogueID`、把「张先生」这类称呼与门牌换占位符、含链接/二维码整窗丢弃。

### 3.6 杂项（6 个）

| 脚本 | 参数 | 数据根 | 产出 | 退出码 |
|---|---|---|---|---|
| `check_secrets.py` | `--allow-local`（跳过 `config.local.yaml`，打包前必须去掉） | 全仓只读 + `git ls-files` | 终端打印问题清单 | 0 通过 / 1 不能打包（`sys.exit(main())`） |
| `handoff.py` | `--test`（顺带跑 smoke） | 真实库（只 `GET` 本机 `127.0.0.1:8787`） | 终端 Markdown 快照（HEAD/工作树/最近 8 提交/exe 是否落后/面板 mtime/运行状态/文档篇数） | 0 |
| `migrate_memory_v2.py` | `--status` / `--dry-run` / `--apply`（互斥，默认动作是 apply）/ `--no-backup` | `config.get().db_file` = **真实库** | JSON 结果 + `[OK]/[失败]` | 0 / 1 |
| `parity_desktop.py` | 无 | `%TEMP%/ai-life-parity.db`（先删 `.db`/`-wal`/`-shm`） | 终端 JSON（5 轮 reply 的 segments/chars/joined/delta + 终局四维） | 0 |
| `routing_demo.py` | 无 | **真实库**（会 `db.kv_set("deep_think"/"mix_mode"/"style_pref")`） | 终端：当前设置 / 路由表 / 决策顺序 / 16 行"场景 × 组合 → 模型" | 0 |
| `sim_group.py` | `--base http://127.0.0.1:8813`、`--db`（**必填**）、`--group 888`、`--self-id 10000`、`--out sim_group.json`、`--wait 45.0` | 由 `--db` 指定（只读查询 `events`） | `--out` 指定 JSON（7 个内置场景 G1–G7 的回复 + 全部 her 事件） | 0 |

`sim_group.py` 的设计动机写在 docstring：NapCat 没装，而 `POST /onebot/event` 就是真实上报入口，走同一条处理链（静默/待机/称呼/语境/亲密门控/落库），只是最后一步"发到 QQ"失败并落 outbox，所以能测"她说什么 + 怎么归属"。

## 4. 数据流

### 4.1 一次"改完代码"的验收链

```text
改代码
  ├─(1) python scripts/smoke.py            387 断言 · 60s 级 · 临时库 · mock · 断网
  ├─(2) python scripts/humanity_check.py   27 分制 · 人的属性（睡眠/记忆连续性/反拼装）
  ├─(3) python scripts/test_lifecorpus.py  72 断言 · 只在动语料/schema/CLI 时必跑
  ├─(4) python scripts/fullcheck.py        150 项 / 110 路由 · 分钟级 · 隔一段时间跑
  └─(5) python scripts/verify_all.py       11 项总控（含 panel_check）→ data/human_life/_reports/verify-<ts>.md
```

### 4.2 一次"对抗矩阵"的数据流

```text
adv_full.py --phase all --levels L0,L1,L2
   └→ sandbox.prepare_env("full")          临时数据根 smd_adv_full_*，钉死 SYMEM_* + MOCK=1
        ├→ inventory → registry（全仓清单：fn/method/route/cli/table/config/persona/scenario）
        ├→ static / routes / functions / cli  逐类真调
        ├→ duel（offline 用 mock 剧本；live 需 --allow-live + --budget 200000）
        └→ coverage（Tracer 包住所有 phase）→ report
   └→ data/_adv/run-<ts>/{manifest,items,findings,turns}.json + phases/*.json + report.md
        └→ adv_list.py 只读 items.json → 人看的 Markdown 清单
```

### 4.3 一次"真机联调"的数据流

```text
scripts/live/serve_live.py（端口 8787，ONEBOT=0，QQ=1，max_tokens=2000）
   ↑ /v1/console/send、/v1/inspect、/v1/health
   │
NapCat :3000 ←→ 用户号 123456789        官方 bot 666777888（群 555666777）
   │                                          │
   ├─ live_qq_duel.py（24/18 句逐句发）       ├─ live_qq_ping.py（C2C + 群各一条）
   ├─ live_qq_campaign.py（9 phase）          └─ final_battery.py（六组 + 多气泡）
   ├─ explicit_after_loosen.py（4 句）
   └─ live_qq_capture.py（只读轮询 events，不花钱）
                    ↓
        data/_adv/live-qq-<ts>/{transcript,inspect,state}.jsonl + duel.jsonl + battery.jsonl
```

## 5. 改这一层要注意（运维纪律）

### 5.1 改完必跑的三件套

| 顺序 | 命令 | 为什么是它 | 规模 |
|---|---|---|---|
| 1 | `python scripts/smoke.py` | 唯一覆盖"全链路主链"的回归基线；`app/` 任何改动都先跑它 | 387 断言，分钟级 |
| 2 | `python scripts/humanity_check.py` | 功能过了不代表"她还是个人"：睡眠/记忆连续性/不可编辑/关系惯性/留痕，只有它测 | 27 分制 |
| 3 | `python scripts/test_lifecorpus.py` | 动 `app/lifecorpus`、`samples/life_corpus`、`life_corpus.py` 时必跑；同时验证 fetch 的"默认离线"性质 | 72 断言 |

这三件全在 `verify_all.py --quick` 的 9 项里（缺 `data/human_life/_derived` 时「语料校验/覆盖」两项 SKIP）（编译 + smoke + 语料自测 + 亲密 + 拟人度 + 语料校验 + 语料覆盖 + 记忆引擎 + 面板自检），所以更省事的做法是直接 `python scripts/verify_all.py --quick`。**动到路由、OneBot、导入、面板接口**再加 `fullcheck.py`（它会按 OpenAPI 把所有路径扫一遍，路由漏测会被 S11 抓出来）；**动到 `panel/` 下的 HTML/JS 或 `/v1/settings` 契约**，`panel_check.py` 已在这条 quick 链里，直接看它那一行即可。

**当前实测状态（2026-11-01，值得知道）**：`smoke.py` **387 通过 / 0 失败**（三条环境依赖断言已改成自洽检查）、`fullcheck.py` 干净工作区 **148/148 + 路由 110/110**（4 条带 reason 的 SKIP）、`humanity_check.py` **27/27**（A 组两条旧清醒度断言已按新模型重写）、`adv_regress.py` **31/31**。四个套件全绿，详见 [22-testing.md](22-testing.md) §3。

### 5.2 动记忆（`mem_item` / 视图 `memories`）额外验什么

代码里的"视图与引擎一致"断言实际写的是 **`≤`**，不是 `==`，三处都要看：

| 位置 | 断言原文 | 语义 |
|---|---|---|
| `smoke.py:1186` | `_vrows41 <= _mrows41`（`_mrows41 = _me.store().count_items()`） | 视图行数不得超过引擎条目数 |
| `fullcheck.py:262` | `view_rows <= int(st["items_active"])` | 同上，用 `stats()["items_active"]` |
| `humanity_check.py:208` | `_view_rows <= _active` | 同上 |

因为视图只暴露"未遗忘"的条目，而 `count_items()`/`items_active` 的口径见 `app/memory/memv2.py`，两边天然不等价；**真正不能破的是 `视图 > 引擎`**。改记忆层时按这个清单验：

1. `memories` 仍是视图不是表：`SELECT type FROM sqlite_master WHERE name='memories'` == `view`（`smoke.py:1181`、`fullcheck.py:667`）。
2. 视图拒绝写入：`INSERT INTO memories …` 必须抛异常（`smoke.py:1203`）。
3. 四张引擎表齐活：`mem_event`、`mem_item`、`mem_cue`、`mem_recall_log`（`fullcheck.py:668`）。
4. 引擎 schema 版本 `>= 2`（`smoke.py:1192`、`fullcheck.py:263`）。
5. 一次召回只落一条审计：`recall_logs` 前后差 1（`smoke.py:1214`）。
6. 遗忘必须是软删：视图不见、`mem_item.forgotten == 1`（`humanity_check.py:241`）。
7. 共用分区（`user_id=0`）对所有人生效（`smoke.py:1216`）。
8. 迁移脚本只读跑一遍：`python scripts/migrate_memory_v2.py --status`（`verify_all.py` 最后一项就是它）。

### 5.3 离线矩阵怎么跑（零花费、可重复）

```powershell
# 全量离线矩阵：mock provider + 临时数据根 + 断网 + 零花费
.\.venv\Scripts\python.exe scripts\adv_full.py --phase all --levels L0,L1,L2

# 只跑编排的一部分（产物仍然完整落盘）
.\.venv\Scripts\python.exe scripts\adv_full.py --phase inventory,routes
.\.venv\Scripts\python.exe scripts\adv_full.py --phase duel --only S01,S03 --turns 6
.\.venv\Scripts\python.exe scripts\adv_full.py --phase functions --levels L0,L1 --limit 200

# 数值层与机制层各来一遍（注意：这两个脚本没设 SYMEM_MOCK，先确认真实配置里没有 key 再跑）
.\.venv\Scripts\python.exe scripts\corematrix.py
.\.venv\Scripts\python.exe scripts\fullmatrix.py --json data/_verify/fullmatrix.json

# 缺陷回归
.\.venv\Scripts\python.exe scripts\adv_regress.py --json data/_verify/regress.json
```

`--phase all` 与 `--levels L0,L1,L2` 就是两者的默认值，写全是为了让命令自解释。**唯一会花钱的组合是 `--mode live --allow-live`**，而且 `--mode live` 不带 `--allow-live` 时会被强制回落 offline（`adv_full.py:167-169` 打印"未加 --allow-live，强制回落 offline（省钱/防误触）"）。

`corematrix.py` / `fullmatrix.py` 是这条"零花费"纪律上的两个例外：它们只隔离了 `SYMEM_DATA`/`SYMEM_DB`，没有 `SYMEM_MOCK=1`，所以真调 `loop.chat_turn` 的轮次会走真实 provider。要完全零花费，跑之前先确认 `config.local.yaml` 的 `provider.api_key` 为空（此时 `app/core/config.py` 自动落 mock），或者临时清掉 `AI_LIFE_API_KEY`/`SYMEM_MOCK` 相关环境变量。

### 5.4 环境变量两代命名与"隔离必须先设"

`app/core/paths.py:24` 的读取顺序是 **`SYMEM_DATA` 优先 → 退 `AI_LIFE_DATA`**（注释写明"AI_LIFE_DATA 是母体兼容名，别删"），`SYMEM_DB` 同理。所以：

| 代 | 变量 | 谁在用 |
|---|---|---|
| 新 | `SYMEM_DATA`/`SYMEM_DB`/`SYMEM_MOCK`/`SYMEM_QQ_ENABLED`/`SYMEM_ONEBOT_ENABLED`/`SYMEM_CONSOLE_TOKEN` | `adv_full.py`（经 `sandbox.prepare_env`）、`adv_regress.py`、`corematrix.py`、`fullmatrix.py`、`serve_live.py` |
| 旧 | `AI_LIFE_DB`/`AI_LIFE_DATA`/`AI_LIFE_MOCK`/`AI_LIFE_IDLE`/`AI_LIFE_API_KEY`/`AI_LIFE_PERSONA`/`AI_LIFE_QQ_APPID`/`AI_LIFE_QQ_TOKEN`/`AI_LIFE_HUMAN_LIFE_DIR`/`AI_LIFE_STYLE_DIR` | `smoke.py`、`fullcheck.py`、`humanity_check.py`、`test_intimacy.py`、`parity_desktop.py`、`qq_probe.py` |

两条硬纪律：

1. **环境变量必须在 `import app.main` / `import app.core.*` 之前设**。`paths.py` 在 import 期就把 `DATA_ROOT` 算死了；`sandbox.prepare_env()` 会显式检查 `"app.core.paths" in sys.modules`，晚了直接抛 `SandboxError`。
2. **新脚本一律用 `SYMEM_*`**；旧脚本里的 `AI_LIFE_*` 不需要改（两条路径等价），但改的时候别只改一半。

### 5.5 打包/交付前

1. `python scripts/check_secrets.py` —— 退 1 不能打包。它扫 `sk-` 形式的 key、`wxid_`、`微信号：`、手机号、身份证、邮箱，额外检查 `config.yaml` 里 `provider.api_key` 是否为空、`git ls-files` 里有没有 `Girl-memory/` 或 `data/` 被跟踪。`--allow-local` 只该在本地自查时用。
2. `python scripts/handoff.py --test` —— 快照里会标出 `dist/ai-life/ai-life.exe` 是否落后于最新提交（`exe mtime < HEAD commit time`），并顺带跑一遍 smoke。
3. `python scripts/migrate_memory_v2.py --dry-run` —— 在新库上先看会搬多少行；`--apply` 默认先备份整库，`--no-backup` 是明确的不推荐路径。

## 6. 交叉引用

- 每个脚本覆盖的行数、目录归属：docs/03（`scripts/` 与 `scripts/live/` 两张全表）。
- `adv_full.py` 用的底座（inventory/static/routes/functions/cli/coverage/duel/oracle/report）：docs/16。
- 语料本身怎么被校验与消费：docs/15（`app/lifecorpus` 六个模块、覆盖矩阵、fetch 边界）、docs/10（`human_life.resolve`）。
- 测试怎么组合成"验收"：docs/22（改完跑什么、验收标准）。
- 真机通道与 `serve_live.py` 为什么关 OneBot：docs/13（装配与入口）、docs/01（快速开始里那段 OneBot 警告）。
- 表结构与 `memories` 视图：docs/17；记忆引擎内部：docs/06、docs/07。
