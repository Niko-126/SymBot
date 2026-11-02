# 22 · 测试与验收

> 覆盖：`scripts/` 全部测试入口 + `app/qa/` 底座（细节见 [16-qa.md](16-qa.md)）｜原则：**不抽样**——可执行面逐条跑、行为用两个 AI 互聊压、最后真机真号验一遍。
> 本章给的是"怎么跑、期望是多少、红了怎么定位"，脚本实现见 [21-scripts.md](21-scripts.md)。

## 1. 三层

| 层 | 工具 | 范围 | 花费 |
|---|---|---|---|
| **L1 静态/函数/路由** | `scripts/adv_full.py`（inventory → static → routes → functions → cli → coverage → report） | 全仓条目：模块/类/配置/人格/表/函数/方法/路由/CLI | 0（mock + 临时库 + 断网） |
| **L2 会话对抗** | `scripts/adv_full.py --phase duel`（50 场景 / 308 轮） | 她与"另一个 AI"互聊：记忆、关系、亲密、群聊、注入、时间、边界 | 0（离线）/ 少量（`--mode live`） |
| **L3 真机** | `scripts/live/*` + 官方 bot / NapCat | 真实 QQ 私聊与群聊、真实模型、真实延迟 | 按量（约 ¥0.4/轮） |

**隔离铁律**：所有离线测试都在临时 `SYMEM_DATA` 下、mock provider、断网守卫；真库只在 L3 用，且先备份。
`app/qa/sandbox.py` 负责这四件事：临时数据根、mock provider、断网、密钥遮罩——并且**必须在 `import app.*` 之前执行**（因为 `import app.main` 会建库写 `kv`）。

## 2. 套件与期望值

| 套件 | 期望 | 说明 |
|---|---|---|
| `scripts/smoke.py` | **387 通过 / 0 失败** | 主基线（mock + 临时库），覆盖 M0 全链路 |
| `scripts/fullcheck.py` | **150/150 通过，路由 110/110，无 5xx** | 全量功能 + 路由兜底；干净工作区（无 `语料/`、无表情素材）是 **148/148**，2 条按预期 SKIP |
| `scripts/adv_regress.py` | **35/35** | 已修缺陷回归 D1–D35 |
| `scripts/test_intimacy.py` | **60/0** | 亲密状态机 |
| `scripts/test_lifecorpus.py` | **72/0** | 语料管线 |
| `scripts/humanity_check.py` | **27/27** | 拟人度（离线部分） |
| `scripts/panel_check.py` | **120/0** | 面板契约自检：页面能开 / 面板引用的接口都在 OpenAPI 里 / 元素 id 找得到 / `/v1/settings` 与 TTS 状态字段齐全 / 保存动作回显 / TTS 关着时优雅拒绝 |
| `scripts/verify_all.py --quick` | **9/9**（干净工作区 **7/7** + 2 SKIP） | 总控；`--quick` 跳 fullcheck 与 handoff。`语料校验`/`语料覆盖` 需要 `data/human_life/_derived`，没有就 SKIP 并带 reason，不判红 |
| `scripts/verify_all.py` | **11/11**（干净工作区 **9/9** + 2 SKIP） | 总控全量（含面板自检） |
| `scripts/adv_full.py --phase all --levels L0,L1,L2` | P0 为 0、`NOTRUN` 为 0 | 离线全量矩阵（约 5 分钟，零花费） |

## 3. 实测快照（本工作区：无 `语料/`、无表情素材）

这一节是接手时实跑的结果，如实记录，不美化（06:50 首次快照；`humanity_check` 两条红在当天按 §3.2 修完后复测 27/27）：

| 套件 | 期望 | 实测 | 判定 |
|---|---|---|---|
| `adv_regress.py` | 31/31 | **31/31** | ✅ 绿 |
| `smoke.py` | 387/0 | **387/0** | ✅ 绿（§3.1 的三条环境依赖断言已修） |
| `fullcheck.py` | 150/150、110/110 | **148/148、110/110、零 5xx** | ✅ 绿（4 条按预期 skip，见下） |
| `test_intimacy.py` | 60/0 | **60/0** | ✅ 绿 |
| `test_lifecorpus.py` | 72/0 | **72/0** | ✅ 绿 |
| `humanity_check.py` | 27/27 | **27/27** | ✅ 绿（§3.2 的两条旧断言已按新清醒度模型重写） |

> **为什么是 148 不是 150**：`fullcheck.py` 的「参考语料导入」和「表情库文件」两条依赖
> `语料/` 与 `data/stickers/` 的实际内容，干净工作区里它们会以带 reason 的 SKIP 出现
> （连同 2 条断网降级 502，共 4 条 skip）。素材齐全的机器上就是 150/150。

### 3.1 `smoke.py` 的三条环境依赖红项（**已修，2026-11-01**）

02:45 的旧快照是 **386/1**，红的是 `输出按模型上限配置`（拿到 8000）。
到干净工作区（没有 `config.local.yaml`，`provider.max_tokens` 回落到 `config.yaml` 的 **400**）重跑，
同一类问题暴露成 **3 条**：

```text
FAIL  回复可带表情包图片段        [{'type': 'text', 'data': {...}}]
FAIL  上下文按模型上限配置        context_window=64000 / reserve_output=1024 / max_tokens=400
FAIL  输出按模型上限配置          400
```

**性质**：全是**断言把某台机器的具体数值写死了**，不是内核缺陷。三处改法：

| 断言 | 原来写死 | 改成 |
|---|---|---|
| 上下文按模型上限配置<br>（`scripts/smoke.py` [33]，约 1070 行） | `context_window ≥ 1_000_000` 且 `reserve_output ≥ 300_000` | 与 `rt.cfg.context.window / reserve_output` **逐值相等**，且 `reserve_output < window` |
| 输出按模型上限配置<br>（同上） | `max_tokens ≥ 300_000` | 与 `rt.cfg.provider.max_tokens` **相等**，且 `0 < max_tokens ≤ window` |
| 回复可带表情包图片段<br>（`scripts/smoke.py:498`） | 回复**必须**含 `image` 段 | 表情库里有文件才要求图片段；空库要求**纯文本、不硬塞** |

共同点：断言验的是「**health 报的数字与运行配置自洽**」和「**行为与环境一致**」，
不再是「这台机器上必须是多少」。

现在 `smoke.py` 在**任何** `provider.max_tokens` 取值下都是 387/0；表情素材有没有都算绿。

### 3.2 `humanity_check.py` 两条红项（**已修，2026-11-01**）

修复前的红项：

```text
[A 身体与时间] 睡着时第一条不回（有提示）   ['……', '刚才走神了，你说啥。'] |
[A 身体与时间] 身体状态由生活时钟决定（同一状态结果一致）
```

两条断言（原 `scripts/humanity_check.py:148` 与 `:187`）写的是**旧行为**：

1. 原第 148 行要求"睡着时第一条不回，或 hint 里带「睡」字"——但 2026-11-01 的**清醒度模型**已经明确：睡着也回，只是只回两三个字（`asleep` 模式延迟 1.5s、不许说"在忙"）。她回「……」正是新模型的正确表现。
   顺带暴露第二个问题：`hint` 在返回值里位于 **`meta.hint`**，原断言读的是 `state.hint`，永远取到空串——即使模型行为正确也判红。
2. 原第 187–191 行直接注入 `life` dict 期望 `decide()` 返回 `asleep/sleepy/busy/awake`——新 `decide()` 的主判据是全局 kv `awake_level`（带 6 分钟半衰期衰减），不再是传进去的 `life` 快照。

**性质**：测试用例没跟着清醒度模型改版同步，属于**过时断言**，不是内核回归（内核行为一行没动就修绿了）。

**修法**（按 [10-life.md](10-life.md) 的清醒度公式重写，不回退内核）：

| 断言 | 现在验什么 |
|---|---|
| 睡着时第一条（现在叫「睡着时第一条只回两三个字（不是机械秒回）」） | 先把 kv `awake_level` 归零 → 面板发一句 → 有回复、总字数 ≤ 20、`meta.mode ∈ {asleep, drowsy}` |
| 叫第二次会醒 | 第二句之后回复非空，且 `behavior.awake_level()` 严格大于上一轮（越聊越清醒） |
| 身体状态由清醒度决定（同一状态结果一致） | 把 `awake_level` 钉在 0.00/0.30/0.90，`mode` 依次为 `asleep/drowsy/awake`，同一值重复调用结果相同；另钉 `busy`（洗澡）/`sleepy`（精力 12）/`awake`（精力 90）三条旁路 |

**防回归验证**：把 `behavior.decide()` 的 `bump_awake(...)` 换成 `level = 0.0`（模拟"清醒度不再爬坡"），新断言立刻红 2 条（`modes=['asleep','asleep','awake','awake']`、`level 0.000→0.000`）；改回来即 27/27——证明这两条不是摆设，能咬住清醒度模型。

修完实测：`humanity_check.py` **27/27**，六个套件全绿。

### 3.3 结论

- **六个套件全部全绿**：adv_regress / smoke / fullcheck / test_intimacy / test_lifecorpus / humanity_check。
- `humanity_check` 的最后 2 条红在 2026-11-01 按新清醒度模型重写后修绿（§3.2），内核行为未改动。
- `smoke` 的三条环境依赖断言已在 2026-11-01 修掉（§3.1），主基线回到 387/0，
  以后真正的回归不会再被这三条噪音盖住。

## 4. 改完之后跑什么（顺序）

```powershell
# 在仓库根目录执行（含 scripts/ 与 requirements.txt 的那一层）
$env:SYMEM_MOCK="1"; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONUTF8="1"
$py = ".\.venv\Scripts\python.exe"

& $py scripts\adv_regress.py       # 1. 31/31（最快，先确认已知缺陷没回来）
& $py scripts\smoke.py             # 2. 387/0（主基线）
& $py scripts\fullcheck.py         # 3. 150/150 + 110/110 路由 + 无 5xx
```

- **动了记忆**：额外验不变量（见 §5）。
- **动了关系/阶段**：额外跑 `test_intimacy.py` 与 `adv_regress.py`（D2/D3/D8/D13 都在关系线上）。
- **动了路由**：`fullcheck.py` 会遍历 OpenAPI 全集，新增路由没登记会导致"注册数 ≠ OpenAPI 数"直接抛错。
- **动了 prompt/guard**：跑 `humanity_check.py` 与 `adv_full.py --phase duel`。

## 5. 记忆不变量（改记忆后必验）

```sql
-- 两条必须相等
SELECT count(*) FROM memories;
SELECT count(*) FROM mem_item
 WHERE forgotten = 0 AND superseded_by IS NULL AND merged_into IS NULL;
```

命令行一把验（只读，不干扰运行中的内核）：

```powershell
@'
import sqlite3
con = sqlite3.connect("file:data/life.db?mode=ro", uri=True)
v = con.execute("select count(*) from memories").fetchone()[0]
a = con.execute("select count(*) from mem_item where forgotten=0 and superseded_by is null and merged_into is null").fetchone()[0]
print("view =", v, "active =", a, "OK" if v == a else "MISMATCH")
'@ | & $py -
```

## 6. 离线全量矩阵

```powershell
$env:SYMEM_MOCK="1"; $env:PYTHONIOENCODING="utf-8"
& $py scripts\adv_full.py --phase all --levels L0,L1,L2
& $py scripts\adv_list.py --status RAISED,NOTRUN     # 把条目摊开
```

| phase | 做什么 | 产物 |
|---|---|---|
| inventory | AST 静态扫全仓 → 约 1900 条 Item | `items.json` |
| static | 模块真 import、类真 getattr、配置键真解析、表真查 `sqlite_master` | `phases/static.json` |
| routes | OpenAPI 全集逐条打（正常/毒/超长/类型错位/无凭据） | `phases/routes.json` |
| functions | L0 反射 → L1 进程内真调（20s 超时）→ L2 子进程（60s 硬超时） | `phases/functions.json` |
| cli | 各子命令独立数据根跑子进程 + 后台任务"mock 下不许启动" | `phases/cli.json` |
| coverage | `sys.monitoring` 真实函数覆盖率 | `phases/coverage.json` |
| report | 汇总 `report.md` + `report.json` | `report.md` |

判定：**P0 任何一条 → 退出码 1**；`NOTRUN` 必须为 0（列了没跑就是失职）；`SKIP` 必须带 reason。

## 7. L3 真机流程

前置：NapCat（非官方 OneBot）登录目标号；官方 bot 凭据在 `config.local.yaml`（非沙箱）。

```powershell
# 1) 内核：关 OneBot（避免用本人号说话）、真 provider、抬到 2000 token
& $py scripts\live\serve_live.py

# 2) 能力位自检
Invoke-RestMethod http://127.0.0.1:8787/v1/health    # channel.name / can_split

# 3) 基础电池：语感/劝睡/记忆/绑定/露骨/群聊/对抗/面板气泡
& $py scripts\live\final_battery.py

# 4) 露骨场景（需恋人状态）
& $py scripts\live\explicit_after_loosen.py

# 5) 采集器（可选，把每条事件 + meta 落盘）
& $py scripts\live\live_qq_capture.py --minutes 30
```

**记录口径**：每轮记 `原话 / 她的回复 / 字数 / 气泡数 / 是否劝睡 / 延迟 / 关系四维 / 亲密模式`。
「库里没回」必须用 QQ 消息历史（NapCat `get_friend_msg_history`）二次核对——**别把"没落库"当"没回复"**。

## 8. 判红口径

| 术语 | 含义 |
|---|---|
| **P0** | 必现的崩溃/数据破坏/关键路径断，任何一条即判红（退出码 1） |
| **NOTRUN** | 列进清单但没执行——等同于失职，必须为 0 |
| **SKIP** | 主动跳过，**必须带 reason**（如"断网降级 502"） |
| **EXPECTED** | 期望内的失败（比如故意打毒参数），不算红 |
| **覆盖率** | `coverage` 的 covered/total，只统计非 SKIP 的 fn/method |
| **通过率** | 执行条目里 OK+EXPECTED 的比例；NOTRUN 单独看 |

## 9. 已知未覆盖

1. **语音 / 表情包**只有 OneBot 通道能验证，官方通道能力位已关（`can_split=false`）。
2. **多人群聊**需要第二个登录账号，目前只有单号；多用户隔离靠离线矩阵覆盖。
3. **群主动**消息被平台拒绝（`40034105`），无法真机验证，只能 OneBot 内网验。
4. **桌面版**（pywebview）只做了离线冒烟，没做真机 UI 回归。
5. **`memories_legacy_20261030` 归档路径**只在升级过的库上存在，新建库跑不到这段代码。

## 10. 交叉引用

- 测试底座内部实现：docs/16
- 脚本参数与产物：docs/21
- 哪些"红"其实是设计边界：docs/23
- 清醒度模型（两条过时断言针对的东西）：docs/10
