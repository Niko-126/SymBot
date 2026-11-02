# START-HERE · SymBot（公开版 / Public）

> 你是新会话，接手的是 **SymBot**：准备公开发布到 GitHub 的版本。
> 这一个文件读三遍再动代码。
>
> **开工顺序（owner 指定，2026-11-01）**：
> `START-HERE.md`（本文件，摸铁律）→ **`TASKS.md`（任务板：**列任务 → 多子代理并发开发 → Lead 收口提交**）** → 开工。
> 新会话的第一件事不是改代码，是**照 `TASKS.md` §3 建任务并分身**。

## 1. 这是什么

一个单机、本地优先的中文「数字生命体」：人格 + 记忆 + 关系数值 + 作息。
一套内核同时服务本地面板、QQ 官方 bot、OneBot 三个入口；只认 OpenAI 兼容接口；
数据落单文件 SQLite。

**规模**：195 个 Python 文件 / 63,611 行 / 127 个 HTTP 操作 / 37 张业务表 + 1 视图。

## 2. 先读什么（按顺序）

1. **`TASKS.md`** —— 任务板：当前基线、写域划分、并发编排、验收命令
2. `docs/README.md` —— 24 章文档总索引（每章多少字、覆盖什么）
3. `docs/00-overview.md` —— 定位、架构、四条不变量
4. `docs/23-boundaries.md` —— **别把设计边界当 bug 修**（已修 20 条在 §3 里逐行标着）
5. `docs/22-testing.md` —— 三层测试与实测基线（**六个套件当前全绿**，红项原因与修法见 §3）

## 3. 铁律（违反一次就够毁掉她）

| # | 规矩 |
|---|---|
| 1 | `vendor/symvolve_memory` **只读**。升级=整目录覆盖，不在原地改 |
| 2 | `memories` 是**视图**不是表。写记忆只走 `app/memory/h2.py: add()` |
| 3 | 关系四维唯一入口 `app/relation/engine.py: apply_appraisal()` |
| 4 | 任何改变她状态的动作必须 `app/core/continuity.py: record()` 留痕 |
| 5 | `data/`、`Chat/`、`Girl-memory/`、`config.local.yaml` 永不入库 |
| 6 | 打包 / git push 前先问 owner |

## 4. 这一版**故意没有**什么

- **没有 R 级插件**（`app/intimacy/r18/` 不存在）。亲密止于 SUGGESTIVE/EXPLICIT 基础三态。
- **没有语料**（`语料/` 目录不在包里，那是 owner 的私人素材）。
- 本版的目标是**能公开、能过审、任何人拿到都能跑**。

> 如果需要成人内容能力，那是 `SMD-A` 的事，不是这一版。

## 5. 跑起来

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.cli serve --port 8787   # http://127.0.0.1:8787/
```

无 key 也能跑：`$env:SYMEM_MOCK = "1"`。

## 6. 改完必跑

```powershell
$env:SYMEM_MOCK="1"; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe scripts\adv_regress.py   # 期望 35/35
.\.venv\Scripts\python.exe scripts\smoke.py         # 期望 387/0
.\.venv\Scripts\python.exe scripts\fullcheck.py     # 期望 150/150 + 路由 112/112
.\.venv\Scripts\python.exe scripts\panel_check.py   # 期望 120/0（改了 panel/ 或 /v1/settings 必跑）
.\.venv\Scripts\python.exe scripts\humanity_check.py # 期望 27/27
.\.venv\Scripts\python.exe scripts\verify_all.py    # 总控 11 项：9/9 通过 + 2 SKIP
```

> 干净工作区（没有 `语料/`、没有表情素材）跑 fullcheck 是 **148/148 + 112/112**：
> 少掉的 2 条会以带 reason 的 SKIP 出现，不算红。

## 7. 已知未解决（下一版可以动）

| 项 | 位置 | 说明 |
|---|---|---|
| ~~`humanity_check` 2 条红~~ **已修（2026-11-01）** | `scripts/humanity_check.py` | 旧清醒度模型断言已按新模型重写，现 **27/27**；改造记录与防回归验证见 `docs/22` §3.2 |
| 剩余代码缺陷 | `docs/23-boundaries.md` §3 | 已修 20 条在行内标着；剩下的按 §3 分级，改之前先读 |
| 面板视觉未验收 | `TASKS.md` T5 | 契约层（panel_check 120/0）已验；错位/对比度/动效要开浏览器看 |
| 语料缺 `_derived` | `TASKS.md` T6 | `verify_all` 因此 2 项 SKIP；补语料要 owner 的素材 |

## 8. 工作方式

- **先分身再动手**：照 `TASKS.md` 建任务（写域不重叠），spawn 多个子代理并行；Lead 只做复核、统一数字、提交。
- 一个功能一个提交，别混着来。
- 改完跑 §6 那六条，红了自己看 `docs/22` 怎么定位。
- 拿不准先读 `docs/03-files.md`，那里有每个文件的行数和职责。

---

## 9. 许可证（**已定稿，2026-11-01**）

**已确定：`SymBot 个人使用许可协议 1.0`**（`LICENSE`，自定义 source-available 许可，**不是** OSI 开源许可）。

| 用途 | 免费？ | 依据 |
|---|---|---|
| 个人学习 / 研究 / 自用（非商业） | ✅ | 第二条 |
| 商业使用（企业内部生产、SaaS、集成进产品、代交付、招生收费） | ❌ 须作者书面授权 | 1.4 / 第三条 / 第四条 |
| 二次分发 / 打包发行 / 镜像站 | ❌ 禁止（保留协议的 GitHub fork 供个人学习除外） | 3.2 |
| 训练模型 / 构建对外发布的数据集 | ❌ 须授权 | 3.4 |
| 提交 PR | — 视为授予作者可再许可的使用权 | 第五条 |

**唯一未完成的字段**：`LICENSE` 的「四、商业授权」和 `README.md` 的「## 许可证」里那段
`【待填：邮箱 / GitHub / TG】`——发布前填真实联系方式，别留着占位符上 GitHub。

**为什么能这么选（自研度结论）**：全仓 100% 自有版权，没有任何第三方源码——
`vendor/symvolve_memory` 是作者自家「衡忆记忆核心 / SyMemory-Core」的副本（同一著作权人），
6 个 pip 依赖（FastAPI MIT / uvicorn BSD / httpx BSD / pydantic MIT / PyYAML MIT / tzdata Apache-2.0）
只通过 pip 安装、不随仓库分发，全仓零 GPL/AGPL 传染。所以**可以**禁止商用、也**可以**做商业双授权。

反过来说：一旦发了 MIT/Apache，这条路就永久送人了（许可证不可撤回）——所以没选它们。

**将来想把「开源」招牌和「卖授权」两头都要**：走 AGPL-3.0 + 商业双授权（Qt/MySQL 模式），
或 BSL 1.1（若干年后自动转 Apache-2.0）。换协议只需改 `LICENSE` + README 那段 + `app/__init__.py` 的 `__license__`（若有）。

---

## 会话清理铁律（2026-11-01 定，**所有新会话必读**）

清理任何 DSH 会话之前，**先看工作区路径，不看标题**。

### 会话存在哪

```text
C:\Users\Niko\.dsh\sessions\<工作区编码>\session-<uuid>\
```

编码规则：`\` → `-`，空格 → `~0020`，首尾加 `--`。

| 编码 | 真实路径 | 能否删 |
|---|---|---|
| `--C-AIGCProgram-SymBot--` | `C:\AIGCProgram\SymBot`（已删） | ✅ 旧仓库会话 |
| `--C-AIGCProgram-Symvolve~0020Memory--` | `C:\AIGCProgram\Symvolve Memory` | ❌ **记忆系统，正在用** |
| `--C-AIGCProgram-SMD--` | `C:\AIGCProgram\SMD` | ❌ 新工作区 |
| `--C-AIGCProgram-TestWorkSpace--` | `C:\AIGCProgram\TestWorkSpace` | ❌ 其他项目 |
| `--G-Symvolve--` | `G:\Symvolve` | ❌ 记忆系统 |

**血的教训**：`HY-P2` 标题像"衡忆数字生命二期"，实际属于 `Symvolve Memory`——
差一点被按标题误删。**标题会骗人，路径不会。**

### 安全工具

```powershell
$t = "$env:USERPROFILE\.dsh\tools\session-cleanup\cleanup.ps1"

powershell -File $t                                   # 列所有工作区 + 会话数
powershell -File $t -Workspace SymBot           # 列某工作区的会话（dry-run）
powershell -File $t -Workspace SymBot -Confirm  # 真删
powershell -File $t -SessionId <uuid> -Confirm        # 删单个
```

工具默认 dry-run，不加 `-Confirm` 永远不删。文档见 `~\.dsh\tools\session-cleanup\README.md`。

### 删之前必须做的三件事

1. `-Workspace` 跑一遍，把"会话 → 工作区"对照表**贴给 owner 看**
2. 确认这几个**绝对不能碰**：`Symvolve Memory`、`SMD`、`TestWorkSpace`、`G:\Symvolve`、`DLF-Pronmode` 相关
3. owner 点头后才加 `-Confirm`
