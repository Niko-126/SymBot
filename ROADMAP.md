# ROADMAP · SymBot 改进方案

> 基线：2026-11-01 实测。所有数字都是跑出来的，不是估的。
> 本文只写"还能往哪改"和"为什么按这个顺序改"；已修的缺陷在 [docs/23-boundaries.md](docs/23-boundaries.md) §3。

---

## 0. 现状快照（2026-11-01）

| 项 | 实测 |
|---|---|
| 代码 | **194 个 py / 63,305 行**（app 116、scripts 42、vendor 35、根 1） |
| 接口 | **127 个操作 / 112 条路径**，分布在 5 个 router（`routes.py` 1198 行、`console.py` 990 行） |
| 数据 | 37 张业务表 + 1 视图；`app` 侧 27 个索引 + vendor 侧 14 个 |
| 文档 | 24 章 / 10,588 行 / 约 10.63 万汉字 |
| 测试 | adv_regress **35/35**（2.1s）、smoke **387/0**（58.5s）、fullcheck **148/148 + 112/112**（10.1s）、intimacy **60/0**（7.5s）、lifecorpus **72/72**（0.25s）、humanity **27/27**（5.3s）→ **全套约 84 秒** |
| 工程设施 | **无 CI、无 pyproject、无 lint/format 配置、无 pre-commit**；依赖只写 `>=`，无锁版本 |
| 运行时 | `logging` 一次都没配置；WAL 只设了 `journal_mode`，**没有 checkpoint**；**没有库备份机制** |
| 前端 | `console.html` 134.7 KB、`index.html` 118.1 KB、**`symvolve-components.js` 126.6 KB（一个 `<sv-*>` 都没用）**、`whale-logo.ico` 174 KB |

**一句话诊断**：内核与文档的完成度远高于工程化程度——**代码是全绿的，工程是裸奔的。**

---

## 1. P0 · 发布就绪（先把门打开）

| # | 事项 | 依据 | 工作量 |
|---|---|---|---|
| 1 | 填 `LICENSE` 与 README 里的商业授权联系方式 | 现在还是 `【待填】`，发布即缺陷 | 5 分钟 |
| 2 | **Python 下限从 3.14 改成 3.11+** | 实测：`py -3.10 -m compileall app` **全过**（语法 3.10 兼容），3.10 跑挂只因 vendor 用了 `enum.StrEnum`（3.11 才有）。全仓无 `ExceptionGroup` / `tomllib` / `TaskGroup` / `typing.Self`，`sys.monitoring` 只在 `app/qa/coverage.py`（测试底座） | 30 分钟（改 README + docs/01 + docs/13） |
| 3 | `.github/workflows/ci.yml`：3.11 / 3.12 / 3.14 矩阵跑六套件 | 全套 84 秒，矩阵约 4 分钟，完全跑得起 | 半天 |
| 4 | `pyproject.toml`：元数据 + ruff + 版本与 LICENSE 一致 | 现在连 packaging 都没有，`pip install -e .` 都不成立 | 半天 |
| 5 | 锁依赖：`requirements.txt` 用 `~=`，另建 `requirements-dev.txt` | 现在 6 个包全是 `>=`，一年后 clone 下来必炸 | 1 小时 |

> **为什么 Python 下限排这么前**：3.14 是 2025-10 刚发的版本，绝大多数人机器上没有。这一条不动，
> 就等于把 90% 的潜在用户挡在第一步。改的是文档，不是代码，风险为零、收益最大。

---

## 2. P1 · 数据安全（本地优先应用的生命线）

她的记忆全在 `data/life.db` 一个文件里。**现在这个文件没有任何自动备份**——删掉、写坏、磁盘坏，就是永久失忆。

| # | 事项 | 具体做法 |
|---|---|---|
| 6 | 在线备份 + 滚动保留 | 用 `sqlite3.Connection.backup()`（WAL 下安全），落 `data/backups/life-YYYYMMDD-HHMMSS.db`，保留最近 N 份（默认 7）。触发：启动一次 + 每 6 小时 + `POST /v1/console/backup` |
| 7 | WAL 维护 | 启动与关停各做一次 `PRAGMA wal_checkpoint(TRUNCATE)`；每天一次 `PRAGMA optimize`。现在只设了 `journal_mode=WAL`，长跑用户的 `life.db-wal` 会一直涨 |
| 8 | 补关停钩子 | `main.py` 的 `shutdown` 现在只停 QQ 适配器；应补 checkpoint + 关连接，别让 WAL 留在半截 |

**验收口径**：跑 24 小时不重启，`life.db-wal` 大小稳定；`data/backups/` 里至少有一份能 `PRAGMA integrity_check` 通过的副本。

---

## 3. P2 · 可观测性（出事时能不能自救）

| # | 事项 | 具体做法 |
|---|---|---|
| 9 | 统一日志 | `logging.basicConfig` + `RotatingFileHandler` 落 `data/logs/app.log`（10 MB × 5）。现在全仓**一次 `basicConfig` 都没有**，出问题只有 uvicorn 的启动行 |
| 10 | `/v1/health` 扩容 | 加 `uptime_s`、`db_size_bytes`、`wal_size_bytes`、`last_error_at`、`requests_total / requests_failed`（进程内计数器）。现在 health 只报配置，不知道"她跑得好不好" |
| 11 | 审计上屏 | `continuity_log` 已经在写，但控制台看不到。加一个只读页（12 条操作里的 `/v1/console/*` 加一组），"谁改了她"一眼可见 |

---

## 4. P3 · 工程质量（让下一个人敢改）

| # | 事项 | 依据 / 注意 |
|---|---|---|
| 12 | 把 6 个脚本包成 pytest（或 `pytest --pyargs` 子进程跑） | 现在测试是手写 `check()`，没有覆盖率报告、IDE 里点不动、不能 `-k` 过滤。转 pytest 后可以用 `--cov` 量化 |
| 13 | smoke 58.5 秒的冷热分离 | 它是全套的 70% 时间。拆成"快回归（≈2s，语法/接口/关系线）"+"全量（58s，含全链路）"，CI 分两个 job |
| 14 | 清掉 126.6 KB 死 JS + 压缩 174 KB ico | `symvolve-components.js` 三个页面都引了，**一个 `<sv-*>` 标签都没用**；ico 里塞了 256 尺寸。发布包体积能立省 ~300 KB |
| 15 | `routes.py`（1116 行 / 62 函数）按域拆 router | **注意**：`fullcheck` 校验"注册路由数 == OpenAPI 数"，拆完必须同步 docs/12 与 docs/16 §4.1，否则测试直接抛错 |
| 16 | 70 处 `SELECT *` 收敛成显式列 | 加列时不会悄悄把大字段（`meta_json`/`text`）带进热路径 |

---

## 5. P4 · 能力上限（现在最弱的三处）

| # | 弱点 | 现状 | 改进方向 |
|---|---|---|---|
| 17 | **记忆去重是"文本完全相等"** | `h2._find_duplicate(uid, ct, kind, content)` 直接比字符串 | 加一层归一化（去标点/空白/繁简）+ 编辑距离阈值；再往上才是向量。这是文档里自己承认的最大短板 |
| 18 | 控制台多用户开关 | `deep_think` / `mix_mode` / `style_pref` 是全局 kv（只有面板能写） | 若将来允许按人设置，走 `db.kv_key(key, uid)`（本轮已经把群维度全改成作用域键了） |
| 19 | 前端 4 个静态页无构建 | 改 UI 要手改 118 KB 单文件 HTML | 先定"要不要引入构建"；不引入就只做减法（删死代码、拆公共块） |

---

## 6. 建议节奏

| 阶段 | 内容 | 产出 |
|---|---|---|
| **第 1 周** | P0 全部（1–5） | 一个 clone 下来 `pip install && python -m app.cli serve` 就能跑、CI 绿灯的公开仓库 |
| **第 2 周** | P1 全部（6–8） | 数据不会因为一次误删而永久失忆 |
| **第 3 周** | P2（9–11）+ P3 的 14 | 出事能看日志、能看审计、发布包瘦身 |
| **第 4 周起** | P3 的 12/13/15 + P4 的 17 | 测试可量化、记忆去重上一个台阶 |

---

## 7. 明确**不做**的事（避免下一个人踩）

| 不做 | 为什么 |
|---|---|
| 改 `vendor/symvolve_memory` | 只读铁律：升级=整目录覆盖，改了两边打架。要改先改上游项目再整目录换 |
| 为了拆 `routes.py` 引入新框架 | 127 条路由 + 112 条路径已经被 `fullcheck` 逐条钉住，换框架的收益抵不上回归风险 |
| 给面板引入构建链（除非要做大改 UI） | 现在"clone 下来双击就能看"是优点，加 webpack/vite 会毁掉它 |
| 把 SQLite 换成 Postgres | 单机本地优先是定位，不是限制；换库等于重写 37 张表的全部手写 SQL |
| 追 `smoke` 单文件行数 | 2357 行的测试脚本不算技术债，拆它只增加 import 成本 |

---

## 8. 怎么判断改进有效

三个可复现的指标，别凭感觉：

1. **冷启动到能聊天**：空环境 `git clone` → 第一条回复耗时（目标 < 3 分钟，含装依赖）
2. **全套测试耗时**：现在 84 秒（目标：快回归 < 3 秒可单独跑）
3. **`data/life.db` 的可恢复性**：删库后能否从 `data/backups/` 一条命令恢复
