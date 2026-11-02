# 23 · 已知边界与红线

> 本章只写三件事：**哪些"看着像 bug 其实是设计边界"**（别去修）、**哪些红线不能碰**、**代码里真实存在的缺陷与坑**（知道就好，改之前看清楚牵动谁）。
> 来源：全套文档撰写期间的逐文件审阅 + 2026-11-01 02:45 的实测。

## 1. 设计边界（不是 bug，别修）

| # | 现象 | 为什么是这样 | 在哪 |
|---|---|---|---|
| 1 | 官方 bot 通道一次只能发一条、多句拼成一条长文本 | 官方开放平台不给多消息能力，能力位 `can_split=false`；`_reply_text()` 用 `\n` 拼接并截 1500 字 | `app/core/channel.py`、`app/adapters/qq.py` |
| 2 | 官方通道**没有**群主动开口 | 平台无权限，发群主动会被拒（错误码 `40034105`）；能力位 `group_proactive=false` | `channel.py`、`app/group/active.py` |
| 3 | 官方通道**没有**表情包 / 语音 | 能力位 `stickers=false`、`voice=false`；只有 OneBot 通道能发 | `channel.py` |
| 4 | 夜里她会带困意，回得又短又慢 | 清醒度模型 + 作息是**故意**的拟人机制（不是"睡着就不回"的老设计）；`asleep` 只回两三个字、延迟 1.5s | `app/life/behavior.py` |
| 5 | 官方通道拿不到 QQ 号，只有 openid | 平台设计如此；`family` 按 QQ 号的判定在这条通道无效 | `app/adapters/qq.py`、`app/relation/users.py` |
| 6 | 长对话里她爱用同一种句式（"先接话 + 反应 + 提要求"） | 模型本身（DeepSeek-V4.1-Flash）偏编程、聊天弱；换模型能改善，调 prompt 收益有限 | 非代码问题 |
| 7 | 多人群聊只有单账号可验 | 只有一个小号，多用户隔离只能靠离线矩阵覆盖 | `docs/22` §9 |
| 8 | `keep_recent` 配置改了没反应 | 主链实际取 `covered_to` 之后的全部事件，这个键是**死变量** | `app/mind/prompt.py` / `compress` |
| 9 | `config.yaml` 里的 `experience:` 段（通知/自启/悬浮窗） | 预留结构，`Config` 根本不取这个键，配了也不生效 | `config.yaml`、`app/core/config.py` |
| 10 | `/v1/health` 里**没有 `traits`** | traits 在 `GET /v1/board` 或 `GET /v1/persona`；她的名字在 `GET /v1/session` | `app/api/routes.py` |
| 11 | kv `gag_harvest_enabled` **没有任何写入点** | 无 UI、无路由，只能手工改库 | `app/web/harvest.py` |

## 2. 工程红线（违反就是事故）

| 红线 | 正确做法 | 违反后果 |
|---|---|---|
| `vendor/symvolve_memory` 只读 | 升级 = 整目录覆盖 + 更新版本记录 | 改了引擎，下次覆盖升级全丢 |
| `memories` 是**视图** | 写记忆只走 `app/memory/h2.py: add()` | 写视图直接报错；`purge_self` 若对视图 DELETE 会半途失败（D1 回归钉的就是这个） |
| 关系四维只走 `apply_appraisal()` | 规则 40% + 模型 60%、单轮 ±3.5、两表同步、落 `relation_log` | 绕过它 → 没流水、没夹取，`relation` 与 `user_relation` 分叉 |
| 改她 / 改状态必须 `continuity.record()` | 谁、什么 source、什么 meta 全记 | 不可审计的改动 |
| `data/`、`Chat/`、`Girl-memory/`、`config.local.yaml` 不入库 | 已写进 `.gitignore` | 私人数据 / 明文 key 上仓库 |
| `config.yaml` 的 `developer.key` 必须留空 | 本机口令写 `config.local.yaml`（不入库）或 `SYMEM_DEVELOPER_KEY` | 带着口令发布 = 谁都能开开发者接口（`_dev_ok` 对空口令一律 403） |
| `exports/`、`.tmp/`、`场景模拟-*.md` 不入库 | 已进 `.gitignore` | 真实对话 / 实跑产物被误提交 |
| 打包与 git 提交要 owner 先开口 | `release.ps1` / PyInstaller 不擅自跑 | 产物污染工作区 |
| NapCat 登本人号时内核必须关 OneBot | `SYMEM_ONEBOT_ENABLED=0` | **她会用你的号说话**（`data/onebot_outbox.jsonl` 里有真实记录） |
| 适配器只做「事件 → chat_turn → 发送」 | 业务逻辑放 `mind` / 领域层 | 两套通道行为分叉，测试覆盖不到 |

## 3. 代码里真实存在的缺陷与坑

> 以下都是静态审阅 + grep 核对得出的，**没有实跑复现**。分四级：①行为偏差 ②数据口径偏差 ③死代码 ④注释与实现不符。

### 3.1 一级：行为偏差（会让她做错事）

| # | 位置 | 现象 | 影响 |
|---|---|---|---|
| 1 | `app/api/onebot.py` | ~~`if group_at_only and not ats: pass` 是空分支~~ **已修（2026-11-01）**：改为真闸门——没 @、没叫名字、也不是"刚被她接过话的人"就不回 | 群里不 @ 她也可能回，与配置项语义相反 |
| 2 | `app/memory/proactive` 相关：`today_count()` | ~~统计主动次数时漏算 `proactive_thought`~~ **已修（2026-11-01）**：改用 `PROACTIVE_SOURCES` 一起算 | 主动发言配额算少，可能超额打扰 |
| 3 | `app/life/thoughts.py: mark_surface()` | ~~撞唯一索引时仍返回 True~~ **已修（2026-11-01）**：先查再插，撞了返回 `False` | 额度照扣，但念头没真正浮现 |
| 4 | `app/life/diary.py: _week_events()` | ~~不过滤 chat_type~~ **已修（2026-11-01）**：`chat_type != 'group'` 才进日记 | 群聊内容会进她的日记 |
| 5 | `app/life/proactive.py: unanswered_streak` | ~~不按 `group_id` 过滤~~ **已修（2026-11-01）**：`unanswered_streak(gid)` 按群过滤 | 跨群串数，一个群的冷场算到另一个群头上 |
| 6 | `app/core/channel.py` + `app/adapters/qq.py` | ~~`start_background()` 第一行无条件 set_channel~~ **已修（2026-11-01）**：只有真的起来了（enabled + appid + token + auto_start）才切能力位 | 即使 `qq.enabled=false`，进程起来后全局能力位也是官方通道（面板/OneBot 会被按单条发送处理） |
| 7 | `app/relation/engine.py` 的每日上限 `caps` | ~~只钳好感~~ **已修（2026-11-01）**：三维共用同一套 `_pos/_neg` 预算，`capped` 三维修（默认 `caps.enabled=false`，不配就是原行为） | 开了日上限也只限住一维，另两维照样涨 |
| 8 | `app/relation/love.py: try_turn()` | ~~写入硬编码 session_seq=1，terminate 又不动 lover 表~~ **已修（2026-11-01）**：新增 `session.current_seq()`，恋爱线全按 live seq 记账；`terminate()` 顺手把旧 seq 的 active 行人结掉 | 换人后旧 `active` 行仍在，新会话判"已经有恋人" |
| 9 | `app/memory/memory.end_session()` | 在 `app/` 里**没有任何调用点**（只有测试脚本调） | `intimate_context.ended_at` 永远 NULL → `analyze.pending_users()` 恒空 → 亲密画像的自动路径空转，只能靠 `POST /v1/console/intimate/<uid>/analyze` 手动产出 |
| 10 | `app/persona/*` 与 `prompt.py` 的 kind 标签表 | `thought`、`diary` 两个记忆 kind **没有标签** | 这两类记忆被 `prompt.build()` 静默丢弃，永远进不了记忆段 |
| 11 | `app/core/db.py` | 连接从不 `PRAGMA foreign_keys=ON`，`REFERENCES` 是**装饰性的** | 真库已出现 `lover.user_id=2` 而 `qq_user` 只剩 `id=1` 的悬空行 |
| 12 | `app/web/lookup.py: _save()` | ~~断网时仍写空缓存~~ **已修（2026-11-01）**：`results` 为空就不落缓存 | 同一个词 24 小时内拿不到真结果——**断网一次锁一天** |
| 13 | `app/web/harvest.py: status()` vs `_should_run()` | ~~空串语义相反~~ **已修（2026-11-01）**：统一走 `_kv_enabled()`，空串=跟随配置 | 手工清空这个开关会得到互相矛盾的两票 |
| 14 | `app/persona/transfer.py: import_payload()` | ~~DELETE 两本账但导出体没有~~ **已修（2026-11-01）**：`export_payload()` 带上 `relation_log` / `traits_log`，import 原样恢复（老档案没这两个键就跳过） | 搬家之后两本账本归零，历史不可追溯 |
| 15 | `app/mind/loop.py` + `app/relation/session.py` | ~~reload_persona 不同步 her_name~~ **已修（2026-11-01）**：`reload_persona()` 在 `name_locked=0` 时把 `session.her_name` 改成新人设名；用户定过的名保持不动 | 换了人设 YAML 里的 `name` **不生效**，必须 `POST /v1/session/new` 或直接改 `session` 表 |

### 3.2 二级：数据口径偏差（数字可能对不上，但不崩）

| # | 位置 | 现象 |
|---|---|---|
| 1 | vendor `recall` 打分 | `cue_partial > cue_exact` 的分支**恒假**（18 > 34 永不成立），包含匹配的分支实际不可达 |
| 2 | vendor 召回缓存 | 缓存命中时返回 `item_ids=()`，且 `status` 硬编码 `"hit"` |
| 3 | `app/life/behavior.py: decide()` | `persona` 是死参数（传了不用）；返回值里的 `will_reply` 恒 `True`，所以 `loop` 里依赖它的分支不可达 |
| 4 | `app/mind/loop.py` | ~~被拉黑的分支不落 user 事件~~ **已修（2026-11-01）**：返回前 `store.add_event("user", …)`，拉黑期他的每句话照样留痕 |
| 5 | `app/memory/h2.py` vs vendor `draft_item()` | 两处算 `clarity` 的口径不一致（`h2` 走 0.90 初始热度 + `_importance_engine`，`draft_item` 走 `score_importance` + vivid） |
| 6 | `app/relation/users.py` + `config.yaml` | ~~`lover.threshold` 配置没被读~~ **已修（2026-11-01）**：`users.threshold()` 读配置（回落常量 80），`bind_lover`/`can_confess`/`love`/`oracle` 全走它；`hit_100_at` 字段名仍然骗人（实际记的是"首次跨门槛"），改名要动列，先留档 |
| 7 | `app/relation/relation_log` | ~~表里没有 user_id 列~~ **已修（2026-11-01）**：补列 + `engine.log()` 写入 + 索引；`finale._conflict_summary(payload, uid)` 只汇收件人的账 |
| 8 | `app/memory/store.py` | ~~importance 默认值 0.5 / 0.6 不一致~~ **已修（2026-11-01）**：`store.add_memory` 默认改 0.6，与 `h2.add` 对齐 |
| 9 | `app/memory/h2.py: add()` | ~~`stability` 是死参数~~ **已修（2026-11-01）**：写进 `mem_item.meta.stability` 留痕（vendor 没有对应字段，衰减还是走 clarity/heat）；`source_ids` 仍落 `extra["ai_life_source_ids"]`（口径已写进 docs/06） |
| 10 | `app/memory/` 姓氏表 | ~~两份拷贝（79 / 157）~~ **已修（2026-11-01）**：合并成 `app/memory/surnames.py`（157 条），`extract` 与 `scrub` 共享同一个 set 对象 |

### 3.3 三级：死代码 / 零调用符号

| 位置 | 符号 | 说明 |
|---|---|---|
| `app/mind/guard.py` | `_SAFE_OPENERS` | 全仓无引用 |
| `app/mind/loop.py` | `_display_name`、`_is_echo_reply` | grep 全仓确认无调用点 |
| `app/mind/loop.py` | `keep` 局部变量 | 读了不用 |
| `app/mind/loop.py` | `_store_her_script()` 的 `stored` 键 | 无人设置，判断恒为假 |
| `app/group/context.py` | `is_focus_lost`、`focus_user_silent` | 零调用 |
| `app/group/*` | `BREAK_ON_MENTION`、`NO_REPLY_LIMIT` | 零调用常量 |
| `app/core/channel.py` | `caps().group_proactive` | **运行时没有任何读取点**，纯声明式能力位 |
| `app/relation/judge.py` | `FATHER_ALIASES` | 定义后全仓无引用 |
| `app/intimacy/guide.py` | `start()` | `app/` 内无调用点，控制台也没有对应路由——运行期只能手动写 kv 打开 |
| `app/web/bili.py` | `trending()` / `popular()` / `hot_comments()` | 全仓零调用点（预留未接线） |
| `panel/settings.html` / `console.html` / `trend.html` | `symvolve-components.js` 的 18 个 `sv-*` 组件 | 三个页面都 `<script src>` 引了 127 KB，但**一个 `<sv-*>` 标签都没用**（全仓 grep 零命中）；`index.html` 两个都不引、令牌是内联的 83 个 |
| `persona/whale-chan.yaml` | `family` / `source` / `fusion` / `profile` 四个顶层键 | 代码零读取（家人判定实际走 `love.family_role()` 的数据库记录） |

### 3.4 四级：注释 / docstring 与实现不符

| 位置 | 注释说 | 代码是 |
|---|---|---|
| `app/group/proactive.py: quota_ok()` | "每小时 2 次" | 只做 25 分钟间隔 + 8 条上限 |
| `app/mind/loop.py: _out_tokens` | 注释写 384K | 实际取 `provider.max_tokens`（默认 400） |
| `app/api/onebot.py` & `app/adapters/intake.py` | 两处都做"入站前置" | **逻辑重复且顺序不同**：onebot 是「静默→待机→改名→语境→搜视频」，intake 是「改名→搜视频→静默」 |
| `scripts/live/serve_live.py` | docstring 写"每轮输出上限压到 800" | 代码是 `CFG.provider.max_tokens = 2000` |
| `scripts/adv_full.py` | docstring 写"Phase 崩溃 → 退出码 2" | 代码把 phase 异常吞进 `phases["_error"]`，`main()` **从不返回 2**；另外 `phase_coverage` 在 `finally` 里又无条件跑一次（重复执行） |
| `scripts/verify_all.py` | 想打印"哪几项失败"的提示 | ~~条件表达式优先级写错~~ **已修（2026-11-01）**：先算 `summary` 再拼尾巴，`failed` 非空时解释文字不再被吞 |

### 3.5 结构性瑕疵

| # | 现象 | 评价 |
|---|---|---|
| 1 | `app/core/runtime.py` 顶层 import `app.mind` / `app.persona` / `app.relation` | 基础层反向依赖，但它是**组合根**，属于必要之恶；新代码不许再加反向 import |
| 2 | `app/core/db.py:363` 函数内延迟 import `app.memory.memv2` | 同上，为避免循环依赖 |
| 3 | `app/api/routes.py` 一个文件 1113 行 62 个函数 | 已知单体，拆分需要同步改面板与 fullcheck 的路由计数 |
| 4 | `app/mind/loop.py` 1262 行 | 主链 + 21 个非主链函数挤在一个文件 |
| 5 | `scripts/corematrix.py` / `scripts/fullmatrix.py` | **没设 `SYMEM_MOCK`**，且都真调 `loop.chat_turn`——配了 key 的机器上会真花钱，**不能算"零花费"**；`corematrix` 还把结果写成相对路径（落在 cwd，不在临时数据根） |
| 6 | `scripts/routing_demo.py` | 会 `db.kv_set` 写**真实库**的 `deep_think` / `mix_mode` / `style_pref`，没有任何隔离 |
| 7 | `panel/index.html` | ~~两份独立 api()：第二块无超时且不拼 API_BASE~~ **已修（2026-11-01）**：全页只留一份（8s AbortController + file:// 指向 127.0.0.1:8787），弹窗那块改用 `window.__panelApi` | `file://` 直开时设置/用量弹窗会失败 |
| 8 | `panel/symvolve-tokens.css` 的生成器 `tools/build-tokens.mjs` 与源 `01-visual/design-tokens.json` **不在本仓库** | 令牌在本仓库是只读成品，改样式只能手改 |

## 4. 测试与配置的陷阱

1. **`smoke.py` 曾有 3 条断言依赖本机配置与素材**（2026-11-01 已改成自洽检查）：`上下文按模型上限配置` / `输出按模型上限配置` 原来写死 `context_window ≥ 1_000_000`、`max_tokens ≥ 300_000`；`回复可带表情包图片段` 原来无条件要求图片段。现在验的是「health 与运行配置一致」和「行为与环境一致」（详见 [22-testing.md](22-testing.md) §3.1）。
2. **`humanity_check.py` 曾有两条断言停留在旧清醒度模型**（第 148 行"睡着时第一条不回"、第 187–191 行"注入 life dict 就能决定 mode"）：新 `decide()` 读的是全局 kv `awake_level`，已于 2026-11-01 按新模型重写并修绿（[22-testing.md](22-testing.md) §3.2）。
3. **测试隔离靠 `SYMEM_DATA`**，但 `config.local.yaml` 的搜索路径包含 `RESOURCE_ROOT`，所以临时库仍会读到真配置——想在测试里改配置，必须用环境变量（`SYMEM_MODEL` / `SYMEM_API_KEY` 等）而不是改文件。
4. **`MOCK` 只认字符串 `"1"`**：`SYMEM_MOCK=true` 不会进 mock，会真连 provider（真花钱）。
5. **`import app.main` 有副作用**：建库、读人设、写 `kv`。任何测试必须走 `app/qa/sandbox.py` 或子进程 + 临时 `SYMEM_DATA`。
6. **`config.get()` 有进程内缓存**：改完 `config.local.yaml` 不重启看不到效果。

## 5. 已知未覆盖

| # | 未覆盖 | 原因 |
|---|---|---|
| 1 | 语音 / 表情包真机链路 | 官方通道能力位已关，只有 OneBot 能验 |
| 2 | 多人群聊多用户隔离 | 只有一个登录账号 |
| 3 | 群主动开口真机验证 | 平台拒绝 `40034105` |
| 4 | 桌面版（pywebview）UI 回归 | 只做了离线冒烟 |
| 5 | `memories_legacy_20261030` 归档路径 | 只在升级过的库上存在，新库跑不到 |
| 6 | vendor `stability.crash_recovery` | 要真 `os._exit` 子进程，未实跑 |

## 6. 交叉引用

- 清醒度模型为什么这么设计：docs/10
- 通道能力位与适配器纪律：docs/13
- 记忆与关系的不变量实现：docs/06、docs/08
- 测试红项怎么定位：docs/22
- 引擎内部的两处打分瑕疵：docs/07

## 7. 发布前自检清单（2026-11-01 定）

照顺序打勾，**全绿才 tag / push**：

| # | 项 | 命令 / 判据 |
|---|---|---|
| 1 | 六套件全绿 | `adv_regress` 31/31、`smoke` 387/0、`fullcheck` 148/148（素材齐 150/150）、`test_intimacy` 60/0、`test_lifecorpus` 72/0、`humanity_check` 27/27 |
| 2 | 离线全量矩阵无 P0、无 NOTRUN | `python scripts\adv_full.py --phase all --levels L0,L1,L2` |
| 3 | 没有真 key / 真手机号进仓库 | `python scripts\check_secrets.py`（`samples/` 里的假号是误报） |
| 4 | 隐私目录全部被忽略 | `git check-ignore -v data/life.db config.local.yaml exports/ .tmp/` |
| 5 | 工作树只有想提交的东西 | `git status --short`——`data/`、`exports/`、`场景模拟-*.md` 不许出现 |
| 6 | 协议与联系方式已填 | `LICENSE`「四、商业授权」与 README「## 许可证」里没有 `【待填】` |
| 7 | 面板只在本机 | 默认 `server.host=127.0.0.1`；要对外先设 `SYMEM_CONSOLE_TOKEN`，否则 `/v1/health.preflight` 会告警 |
| 8 | 开发者口令不随仓库走 | `config.yaml` 的 `developer.key` 为空；本机自用走 `config.local.yaml` 或 `SYMEM_DEVELOPER_KEY` |
| 9 | 语料出口可用（要微调时） | `python scripts\export_corpus.py --stats` 能跑，导出落 `exports/`（已忽略） |
