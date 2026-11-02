# SymBot · 衡忆数字生命

> 一个能自己住着的对话智能体：**有性格、有记忆、有作息、有关系状态**，换模型 / 换机器 / 换聊天平台而仍然是她。

单机、本地优先、只认 OpenAI 兼容接口；全部数据落一个 SQLite 文件。

> **发布默认 = 测试环境（environment: test）**：独立测试库 data/test-life.db，OneBot / 语音 / 热梗抓取全部关闭。
> 想正式用，把 config.yaml 里 environment 改成 prod（或用 SYMEM_ENV=prod），再在面板「设置 → 连接」填自己的 key 与小号。

```text
Python 3.14 · FastAPI · SQLite(WAL) · 纯静态面板
195 个 Python 文件 / 63,611 行 · 127 个 HTTP 操作 · 37 张业务表 + 1 视图
```

## 它是什么

不是聊天机器人外壳，也不是 RAG 套壳：

| 它有 | 它没有 |
|---|---|
| **记忆**：vendored 记忆引擎（热度、线索、遗忘、压缩、软合并）+ 唯一写入口 `h2.add()` | 前端框架 / 构建步骤（面板是 4 个纯静态页） |
| **关系**：四维数值 × 两套账 × 六阶段 × 唯一恋人，唯一结算入口 `apply_appraisal()` | ORM（全部手写 SQL） |
| **人格**：YAML 人格包（22 个顶层键、8 维性格、9 段作息） | 云端依赖（只在调模型、查词、抓梗时出网） |
| **作息**：清醒度模型（她会困、会醒、越聊越精神）、日记、夜里念头 | 多机部署 / 分布式（单进程单例） |
| **通道**：一套内核，面板 / QQ 官方 bot / OneBot 三个入口，能力位自报 | 语义去重（记忆去重是**文本完全相等**） |
| **验收**：对抗式测试底座（1,893 条 Item / 127 路由逐条打 / 双 AI 互聊 / 22 条裁判规则） | — |
| **工具**：联网查资料 / 读链接 / 看用户发来的图（`app/tools/`，mock 下**永不出网**） | — |

## 快速开始

```powershell
py -3.14 -m venv .venv
$py = ".\.venv\Scripts\python.exe"

& $py -m pip install -r requirements.txt     # 6 个依赖
& $py -m app.cli serve --port 8787           # 内核 + 面板 → http://127.0.0.1:8787/
```

没有 key 也能跑（mock 模式，全链路离线）：

```powershell
$env:SYMEM_MOCK = "1"                        # 只认字符串 "1"
& $py -m app.cli serve
```

有 key 时写 `config.local.yaml`（**不入库**）：

```yaml
provider:
  base_url: https://api.siliconflow.cn/v1
  api_key: sk-你的key
  model: deepseek-ai/DeepSeek-V4.1-Flash
```

更多见 **[docs/01-quickstart.md](docs/01-quickstart.md)**。

## 架构一览

```text
接入层   panel/（HTTP）· app/adapters/qq.py（botpy WS）· app/api/onebot.py（NapCat 上报）
接口层   app/api/{routes,console,trend,group}.py        127 个操作 / 112 条路径模板
主链层   app/mind/loop.py: chat_turn()   命名 → 恋爱 → 状态 → 关系/记忆 → 情绪 → prompt → 模型 → 守卫/写库
领域层   memory · relation · intimacy · life · group · web · persona
基础层   core/{config,paths,db,runtime,memengine,continuity,preflight,channel}
引擎     vendor/symvolve_memory（vendored 只读）
```

一次对话的主链（`app/mind/loop.py`）：

```text
0.5 命名剧本 → 0.8 恋爱事件 → 1.5 状态策略（清醒度/延迟/句数）
→ 2 关系与记忆召回 → 3 情绪与语料（锚点/语气层/热梗/陪伴）
→ 4 prompt 组装（人设五段 + 关系 + 记忆 + 此刻状态 + 规则）
→ 5 调模型 → 6 出戏守卫 → 切句 → 写 events → 写记忆 → 结算关系 → 留痕
```

详见 **[docs/02-architecture.md](docs/02-architecture.md)**。

## 文档

全套 24 章，一章一个主题，从"怎么跑"到"每个函数干什么"。
后续改进方向与优先级见 **[ROADMAP.md](ROADMAP.md)**；当前要做的任务与并发分工见 **[TASKS.md](TASKS.md)**。

| 章 | 内容 |
|---|---|
| [00](docs/00-overview.md) · [01](docs/01-quickstart.md) · [02](docs/02-architecture.md) | 总览 · 快速开始 · 架构分层 |
| [03](docs/03-files.md) | **文件全表**（195 个 py 逐个列，行数/类/函数/职责） |
| [04](docs/04-core.md) · [05](docs/05-mind.md) | 内核基础 · 对话主链 |
| [06](docs/06-memory.md) · [07](docs/07-vendor-memory.md) | 记忆系统 · 引擎内部 |
| [08](docs/08-relation.md) · [09](docs/09-intimacy.md) | 关系系统 · 亲密模式 |
| [10](docs/10-life.md) · [11](docs/11-group.md) | 生活作息 · 群聊增强 |
| [12](docs/12-api.md) · [13](docs/13-entry.md) | HTTP 接口（127 条）· 装配与入口 |
| [14](docs/14-web.md) · [15](docs/15-lifecorpus.md) | 联网内容 · 语料管线 |
| [16](docs/16-qa.md) · [17](docs/17-database.md) · [18](docs/18-config.md) | 测试底座 · 数据模型 · 配置全量 |
| [19](docs/19-persona.md) · [20](docs/20-panel.md) · [21](docs/21-scripts.md) | 人格包 · 前端面板 · 脚本运维 |
| [22](docs/22-testing.md) · [23](docs/23-boundaries.md) | 测试与验收 · 已知边界与红线 |

## 测试

```powershell
$env:SYMEM_MOCK="1"; $env:PYTHONIOENCODING="utf-8"
$py = ".\.venv\Scripts\python.exe"

& $py scripts\adv_regress.py      # 31/31   已修缺陷回归 D1–D31
& $py scripts\smoke.py            # 387/0   主基线（mock + 临时库）
& $py scripts\fullcheck.py        # 150/150 + 路由 110/110，无 5xx（干净工作区 148/148，2 条按预期 SKIP）
& $py scripts\test_intimacy.py    # 60/0    亲密状态机
& $py scripts\test_lifecorpus.py  # 72/0    语料管线
& $py scripts\humanity_check.py   # 27/27   拟人度（离线部分）
& $py scripts\adv_full.py --phase all --levels L0,L1,L2   # 离线全量矩阵，零花费
& $py scripts\verify_all.py       # 总控 10/10
```

当前基线（2026-11-01）**六个套件全绿**，实测快照与红项修法写在 **[docs/22-testing.md](docs/22-testing.md)** §3，不美化。

真机联调脚本在 `scripts/live/`，流程见 [docs/22](docs/22-testing.md) §7。

**语料出口**：`python scripts\export_corpus.py --stats` 看能导多少，不带参数导出到 `exports/`
（一行一个 `{context, reply}` 训练对，只读库 + 手机号/身份证/邮箱脱敏）。见 [docs/21](docs/21-scripts.md)。

## 目录

```text
app/       内核（core · mind · memory · relation · intimacy · life · group · api · adapters · web · lifecorpus · qa）
panel/     4 个纯静态页（index / settings / console / trend）
persona/   人格包 YAML（当前 whale-chan.yaml「小鲸」）
vendor/    symvolve_memory（只读记忆引擎）
scripts/   测试与运维（含 live/ 真机脚本）
docs/      技术文档（24 章）
data/      运行数据，不入库
语料/      参考语料，只读
```

逐文件索引见 **[docs/03-files.md](docs/03-files.md)**。

## 四条不能破的不变量

1. **记忆写入只走 `app/memory/h2.py: add()`**。`memories` 是视图，不是表。
2. **关系数值唯一入口 `app/relation/engine.py: apply_appraisal()`**。规则 40% + 模型 60%，单轮硬上限 ±3.5。
3. **任何改变她状态的动作必须 `app/core/continuity.py: record()` 留痕。**
4. **`import app.main` 有副作用**（建库、读人设、写 `kv`）——测试走 `app/qa/sandbox.py` 或子进程 + 临时 `SYMEM_DATA`。

另外：`vendor/` 只读（升级=整目录覆盖）；`data/`、`Chat/`、`Girl-memory/`、`config.local.yaml` 永不入库。

## 许可证

**源码可见（source-available），但非开源。** 本仓库适用 **[SymBot 个人使用许可协议 1.0](LICENSE)**：

| 用途 | 是否免费 | 说明 |
|---|---|---|
| 个人学习 / 研究 / 自用（非商业） | ✅ 免费 | 可运行、可改源码、可本地部署 |
| 商业使用（企业内部生产、SaaS、集成进产品、代交付、招生收费） | ❌ 需授权 | 须事先取得作者书面商业授权 |
| 二次分发 / 打包发行 / 镜像站 | ❌ 禁止 | 保留协议的 GitHub fork 用于个人学习除外 |
| 用于训练模型 / 构建对外发布的数据集 | ❌ 需授权 | 见协议 3.4 |
| 提交 PR / 贡献代码 | — | 视为授予作者可再许可的使用权（协议第五条） |

**商业授权联系：【待填：邮箱 / GitHub / TG】。**

协议全文见 [LICENSE](LICENSE)，中文文本为唯一具备法律效力的版本。
`vendor/symvolve_memory/` 是作者另一项目「衡忆记忆核心 / SyMemory-Core」的只读副本，随本协议一并授权，不可单独提取分发；
6 个 pip 依赖（FastAPI / uvicorn / httpx / pydantic / PyYAML / tzdata）各自适用其原始 MIT、BSD-3-Clause、Apache-2.0 许可，不随本仓库分发。
