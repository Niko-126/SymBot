# 00 · 项目总览

> 本章是全套文档的入口：它是什么、规模多大、一次对话怎么跑、四条不能破的不变量、文档怎么读。
> 细节章节见 [docs/README.md](README.md)。

## 1. 一句话定位

**单机、本地优先的中文「数字生命体」**：人格 + 记忆 + 关系数值 + 作息，一套内核同时服务本地面板、QQ 官方 bot、OneBot（NapCat）三个入口；只认 OpenAI 兼容接口；全部数据落一个 SQLite 文件 `data/life.db`。

它不是聊天机器人外壳，也不是 RAG 套壳：

| 它有 | 它没有 |
|---|---|
| 一套人格包 YAML（22 个顶层键、8 维性格、6 段关系阶段、9 段作息） | 前端框架 / 构建步骤（面板是 4 个纯静态页） |
| 一套 vendored 记忆引擎（热度、线索、遗忘、压缩、软合并） | ORM（全部手写 SQL） |
| 一套关系数值系统（四维 × 两套账 × 六阶段 × 唯一恋人） | 云端依赖（只在调模型、查词、抓梗时出网） |
| 一套清醒度模型（她会困、会醒、越聊越精神） | 多机部署 / 分布式（单进程单例） |
| 一套对抗式测试底座（9 个模块 / 127 条路由逐条打 / 双 AI 互聊） | 语义去重（记忆去重是**文本完全相等**） |

## 2. 规模（2026-11-01 实测）

| 项 | 值 |
|---|---|
| Python 文件 | **195 个 / 63,611 行**（`app/` 116 个 36,282 行、`scripts/` 43 个 14,860 行、`vendor/` 35 个 12,465 行） |
| 类 / 顶层函数 / 方法 | 210 / 1,826 / 480 |
| HTTP 操作 / 路径模板 | **127 / 112**（五个 router） |
| 数据库 | **37 张业务表 + 1 个视图 `memories`**（另有 SQLite 内部的 `sqlite_sequence` 不计入），单文件 WAL |
| 前端 | 4 个单文件页（index / settings / console / trend），纯静态 |
| 运行时 | Python 3.14 + FastAPI + SQLite |
| 测试基线 | smoke 387 项、fullcheck 150 项、adv_regress 31 条（D1–D31） |

逐文件的规模与职责见 [docs/03-files.md](03-files.md)。

## 3. 三个入口，一套内核

```text
                    ┌──────────────────────────────┐
本地面板 :8787  ───→│                              │
QQ 官方 bot  ──────→│   app/mind/loop.py           │──→ 模型（OpenAI 兼容）
（botpy WS）        │   chat_turn(...)             │
OneBot/NapCat  ───→│                              │──→ 写 events / 写记忆 / 结算关系
（HTTP 上报）      └──────────────────────────────┘
        │                      │
        └─ app/core/channel.py ─┘  同一个内核，通道能力位不同
```

通道能力差异（`app/core/channel.py`）：

| 能力 | 官方 bot | OneBot | 面板 |
|---|---|---|---|
| 文本 | ✅ | ✅ | ✅ |
| 多气泡 `can_split` | ❌（一次只能一条） | ✅ | ✅ |
| 表情包 / 语音 | ❌ / ❌ | ✅ / ✅ | ✅ / ❌ |
| 群主动开口 | ❌（平台无权限） | ✅ | ✅ |
| 私聊主动 | ✅（有配额） | ✅ | ✅ |

**适配器纪律**：适配器只做「平台事件 → `chat_turn` → 平台发送」；不许在适配器里拼 prompt、写记忆、算关系。

## 4. 一次对话的主链

`app/mind/loop.py: chat_turn(rt, text, source, user_id, chat_type, group_id)`：

```text
0.5  命名流程       剧本、不走模型；台词必须带 user_id/chat_type 落库
0.8  恋爱事件       表白/分手/答应提议：确定性回复，但必须落库（否则上下文断）
1.5  状态策略       behavior.decide：清醒度 / 忙 / 低精力 → 句数 + 延迟 + 提示
 2   关系与记忆     relation_for / h2.retrieve / 线索 / 摘要
 3   情绪与语料     锚点（只看语感）/ 语气层 / 热梗 / 陪伴层 / 夜里念头
 4   prompt 组装    prompt.build：人设五段 + 关系 + 记忆 + 此刻状态 + 规则 + 语感
 5   调模型         llm.chat（OpenAI 兼容；api_key 空 = mock）
 6   收尾           出戏守卫 → 句子切分 → 写 events → 写记忆 → 结算关系 → 留痕
```

细节见 [docs/05-mind.md](05-mind.md)。四个子系统分别在 [06 记忆](06-memory.md) / [08 关系](08-relation.md) / [09 亲密](09-intimacy.md) / [10 生活](10-life.md)。

## 5. 数据与配置落点

| 落点 | 内容 | 是否入库 |
|---|---|---|
| `data/life.db` | 37 张业务表 + 1 视图：会话、关系、记忆、事件、kv、用量… | 否（`.gitignore`） |
| `data/archives/`、`data/human_life/`、`data/stickers/`、`data/voices/` | 档案、语料、表情包、语音素材 | 否 |
| `persona/*.yaml` | 人格包 | 是 |
| `config.yaml` | 配置第一层 | 是 |
| `config.local.yaml` | 真实 key / token（覆盖第一层） | **否** |
| `语料/` | 参考语料（只学相处方式，不进用户记忆） | 是（只读） |
| `Chat/`、`Girl-memory/` | 用户私人数据 | **否** |

配置三层合并（`config.yaml` → `config.local.yaml` → 环境变量）与全部字段见 [docs/18-config.md](18-config.md)；表结构见 [docs/17-database.md](17-database.md)。

## 6. 四条不能破的不变量

这四条会被回归用例钉住，任何改动都必须守住：

1. **记忆写入只走 `app/memory/h2.py: add()`。** `memories` 是视图，不是表；对它执行 DELETE 会失败（D1 回归专门测这个）。
2. **关系数值唯一入口 `app/relation/engine.py: apply_appraisal()`。** 规则 40% + 模型 60%，单轮硬上限 ±3.5；两套表（`relation` 单行 / `user_relation` 按 uid）都要同步，流水落 `relation_log`。
3. **任何改变她状态的动作必须写 `app/core/continuity.py: record()`。** 她可以被改变，但改变必须留痕。
4. **`import app.main` 有副作用**（建库、读人设、写 `kv`）。所以脚本与测试要么用子进程，要么走 `app/qa/sandbox.py`。

另外三条工程纪律：`vendor/symvolve_memory` 只读（升级=整目录覆盖）；`data/`、`Chat/`、`config.local.yaml` 永不入库；打包（PyInstaller）与 git 提交都要 owner 先开口。

## 7. 文档地图

| 章 | 内容 | 读它的时候 |
|---|---|---|
| [01](01-quickstart.md) | 快速开始（装、配、跑、重置） | 第一次上手 |
| [02](02-architecture.md) | 架构与分层、依赖方向 | 想知道为什么这么切 |
| [03](03-files.md) | **文件全表**（195 个 py 逐个列） | 找文件 |
| [04](04-core.md) / [05](05-mind.md) | 内核基础 / 对话主链 | 改主流程 |
| [06](06-memory.md) / [07](07-vendor-memory.md) | 记忆系统 / 引擎内部 | 改记忆 |
| [08](08-relation.md) / [09](09-intimacy.md) | 关系 / 亲密模式 | 改关系与尺度 |
| [10](10-life.md) / [11](11-group.md) | 生活作息 / 群聊 | 改她的生活感 |
| [12](12-api.md) / [13](13-entry.md) | HTTP 接口（127 条）/ 装配与入口 | 接接口 |
| [14](14-web.md) / [15](15-lifecorpus.md) | 联网内容 / 语料管线 | 改外部内容 |
| [16](16-qa.md) | 对抗式测试底座 | 写测试 |
| [17](17-database.md) / [18](18-config.md) | 数据模型 / 配置全量 | 查字段 |
| [19](19-persona.md) / [20](20-panel.md) | 人格包 / 前端面板 | 换人设、改面板 |
| [21](21-scripts.md) | 脚本与运维 | 跑测试、联调 |
| [22](22-testing.md) | 测试与验收 | 改完怎么验 |
| [23](23-boundaries.md) | 已知边界与红线 | 别把设计当 bug 修 |
