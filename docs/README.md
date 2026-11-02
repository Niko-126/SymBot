# 文档索引（SymBot）

> 全套 **24 章 / 10,625 行 / 约 10.69 万汉字**，覆盖 `app/` 116 个 Python 文件（36,282 行）、`scripts/` 43 个（14,860 行）、`vendor/` 35 个（12,465 行）、`panel/` 前端、`persona/` 人格包、38 张表结构与 124 个 HTTP 操作。

## 1. 这套文档是什么

- **唯一事实来源是代码本体**：所有函数签名、常量值、表名/列名、路由路径、kv 键、配置字段都从源码与 `sqlite_master` 取，不抄旧文档、不凭记忆。
- **生成方式**：AST 静态扫描（195 个 py 的类/函数/签名/行数）＋ `sqlite_master` / `pragma table_info`（表结构）＋ 逐文件精读（每个目录至少一名撰写者）＋ 实跑（测试基线、inventory 计数）。规模数字见 [03-files.md](03-files.md) 与 [16-qa.md](16-qa.md) §4.1。
- **不美化**：测试红项、过时断言、死代码、字段语义错位、配置不生效，全部如实写进 [22-testing.md](22-testing.md) 与 [23-boundaries.md](23-boundaries.md)。

## 2. 全部章节

| # | 文件 | 行数 | 汉字 | 覆盖 |
|---|---|---|---|---|
| 00 | [00-overview.md](00-overview.md) | 119 | 1,122 | 定位、规模、三入口、主链、四条不变量、文档地图 |
| 01 | [01-quickstart.md](01-quickstart.md) | 163 | 1,031 | 装 / 配 / 跑 / CLI 全表 / 故障排查 / 真机纪律 |
| 02 | [02-architecture.md](02-architecture.md) | 152 | 861 | 六层结构、依赖方向实测、组合根、谁写哪张表、扩展点 |
| 03 | [03-files.md](03-files.md) | 415 | 3,849 | **192 个 py 全表**（行数/类/函数/职责）+ 前端/人设/示例/根文件 |
| 04 | [04-core.md](04-core.md) | 565 | 4,872 | `app/core` 9 文件：config / paths / db / runtime / memengine / continuity / preflight / channel |
| 05 | [05-mind.md](05-mind.md) | 453 | 6,146 | `app/mind` 6 文件：loop 主链 20 步、prompt 24 段、llm、models、guard |
| 06 | [06-memory.md](06-memory.md) | 1,128 | 15,083 | `app/memory` 21 文件：h2 唯一入口、视图、召回权重、压缩、导入、锚点、语气层 |
| 07 | [07-vendor-memory.md](07-vendor-memory.md) | 719 | 9,493 | `vendor/symvolve_memory` 35 文件：7 表 14 索引、迁移 checksum、衰减、召回、渲染 |
| 08 | [08-relation.md](08-relation.md) | 494 | 5,509 | `app/relation` 11 文件：四维结算、阶段、恋人、表白分手、命名、告别 |
| 09 | [09-intimacy.md](09-intimacy.md) | 442 | 4,774 | `app/intimacy` 8 文件：三态机、TTL、群聊降级、画像、引导 |
| 10 | [10-life.md](10-life.md) | 665 | 7,854 | `app/life` 7 文件：清醒度模型、时钟、日记、语料接管、主动外发、夜里念头 |
| 11 | [11-group.md](11-group.md) | 299 | 3,705 | `app/group` 5 文件：群语境、每人印象、群主动、自我介绍、调度 |
| 12 | [12-api.md](12-api.md) | 497 | 4,654 | **124 个 HTTP 操作全表** + Pydantic 模型 + 鉴权全景 |
| 13 | [13-entry.md](13-entry.md) | 290 | 3,909 | `main.py` 装配、`cli.py` 10 子命令、`desktop.py`、`adapters`（qq / intake） |
| 14 | [14-web.md](14-web.md) | 478 | 5,062 | `app/web` 5 文件：查词、热梗、B 站客户端、后台抓取 |
| 15 | [15-lifecorpus.md](15-lifecorpus.md) | 533 | 5,733 | `app/lifecorpus` 6 文件：四类语料 schema、校验、覆盖矩阵、PII、只读抓取 |
| 16 | [16-qa.md](16-qa.md) | 313 | 1,947 | `app/qa` 测试底座：1,893 条 Item、六 phase、双 AI 对聊、22 条裁判规则 |
| 17 | [17-database.md](17-database.md) | 1,226 | 2,595 | **37 张业务表 + 1 视图**的完整 DDL、列、索引、行数快照 |
| 18 | [18-config.md](18-config.md) | 276 | 1,587 | 三层合并、14 个配置段全字段（含 `tools:` / `voice:`）、17 个环境变量、写回机制、7 个坑 |
| 19 | [19-persona.md](19-persona.md) | 298 | 3,719 | 人格包 22 个顶层键、五段进 prompt、加载与档案迁移 |
| 20 | [20-panel.md](20-panel.md) | 285 | 3,401 | 四个纯静态页、组件库、设计令牌、与后端的通信 |
| 21 | [21-scripts.md](21-scripts.md) | 440 | 5,308 | `scripts/` 43 个脚本全表 + 运维纪律 + 真机启动器 |
| 22 | [22-testing.md](22-testing.md) | 216 | 1,996 | 三层测试、期望值、**实测快照、红项原因与修法**、判红口径 |
| 23 | [23-boundaries.md](23-boundaries.md) | 159 | 2,667 | 设计边界（不是 bug）、工程红线、真实缺陷分级、**发布前自检清单** |

契约原件：`app/qa/CONTRACT.md`（测试底座写代码前必读）。

## 3. 按角色的阅读路径

| 你是谁 / 想干什么 | 读这条线 |
|---|---|
| 第一次跑起来 | 01 → 00 → 09（面板怎么用） |
| 改对话主流程 | 02 → 05 → 04 → 18 |
| 改记忆 | 06 → 07 → 17 §3（不变量）→ 22 §5 |
| 改关系 / 亲密 | 08 → 09 → 23 §3（已知口径偏差） |
| 换人设 | 19 → 18 §3.5（`persona.path` 优先级） |
| 接新通道 | 13 → 12 → 02 §6（扩展点）→ 23 §2（适配器纪律） |
| 写测试 / 排红 | 22 → 16 → 21 |
| 接手维护（第一周） | 00 → 02 → 03 → 23 → 22 |

## 4. 约定与口径

**术语**

| 词 | 意思是 |
|---|---|
| 主链 | `app/mind/loop.py: chat_turn()` 的一次完整执行 |
| 面板线 | `user_id = 0` 的对话路径（`/v1/chat`），强制 `is_lover=True`、不进亲密状态机 |
| QQ 线 | 官方 bot（按 openid 映射 `user_id`）与 OneBot（按 QQ 号）两条 |
| 能力位 | `app/core/channel.py` 里声明的通道能力（`can_split` / `stickers` / `voice` / `group_proactive`） |
| 活跃记忆 | `mem_item` 里 `forgotten=0 AND superseded_by IS NULL AND merged_into IS NULL` 的行 |
| 常驻进程 | `.venv\Scripts\python.exe -m app.cli serve`（唯一推荐启动方式） |

**数字口径**

- 表数量：**37 张业务表 + 1 视图**。`sqlite_master` 里还有 `sqlite_sequence`（SQLite 内部表，不计）。
- 路由数量：**124 个操作 / 110 条路径模板**；测试底座的 inventory 会多扫到 2 条（`/` 与 `/index.html` 占位页），所以它报 126。
- 代码规模：**195 个 py / 63,611 行**（`app/` 36,282、`scripts/` 14,860、`vendor/` 12,465、根 4）。用 `wc -l` 口径会因空行偏差，本套文档统一用 `ReadAllLines` 口径（见 06 §文件清单的注）。
- 「视图行数 == 活跃 mem_item 行数」：语义上**严格相等**（视图 SQL 的 `WHERE` 与"活跃"定义一字不差，见 [17-database.md](17-database.md) §2.39）；但现有测试断言写的是 `≤`（`smoke.py` / `fullcheck.py` / `humanity_check.py` 三处），是更宽松的写法。要验就按严格相等验。

## 5. 维护约定（改了代码，同步改哪章）

| 你动了 | 必须同步 |
|---|---|
| `app/api/*.py` 的路由 | 12（路由表）、16 §4.1（inventory 条数） |
| 新增表 / 列 | 17（DDL 与列）、03（`app/core/db.py` 行数） |
| 新增配置键 | 18（字段表 + 环境变量表） |
| 新增人格键 / 换人设 | 19、03 §5.2 |
| 新增记忆 kind | 06 §5（还要同步 `prompt.py` 的 kind→标签表，否则进不了 prompt） |
| 新增脚本 | 21（全表）、22（若属基线） |
| 改清醒度 / 关系阈值 | 10、08、22 §3.2（旧断言） |
| 改文件规模 | 03（可重跑 AST 扫描重建） |

## 6. 本套文档的已知局限

1. **行号会漂移**：各章的「第 N 行」以 2026-11-01 工作区状态为准，代码改动后需重新核对。
2. **不含运行时数据**：`data/life.db` 的行数是快照，真库随时变化。
3. **未实跑的部分**：vendor 的 `stability.crash_recovery`、`tools/http_server` 的 401 路径、桌面版 UI 回归、语音/表情包真机链路——这些在 22 §9 与 23 §5 标为未覆盖。
4. **历史红项已清零**：`humanity_check.py` 的两条旧清醒度断言已于 2026-11-01 按新模型重写（22 §3.2，含防回归验证）；`smoke.py` 的三条环境依赖断言同日修掉。
## 7. 上架收尾新增（2026-11-02，编号 30–33）

| # | 文件 | 覆盖 |
|---|---|---|
| 30 | [30-依赖与安装评估.md](30-依赖与安装评估.md) | 装起来有多难、自研/第三方/公开依赖三分、四类用户耗时 |
| 31 | [31-上架GitHub收尾清单.md](31-上架GitHub收尾清单.md) | 发布前逐条打勾（卫生/隐私/合规/安装/测试/文档/水印） |
| 32 | [32-模块热插拔.md](32-模块热插拔.md) | `modules/<id>/manifest.json`、加载生命周期、面板开关、失败排查 |
| 33 | [33-水印与指纹.md](33-水印与指纹.md) | 零宽字符隐写 + 派生常量锚 + 结构指纹，`apply/verify` 用法与取证边界 |
| 07 | [07-vendor-memory.md](07-vendor-memory.md) | 追加 §7：闭源打包（Nuitka/PyArmor/seal 三模式）与 .pyd/加密容器分发 |