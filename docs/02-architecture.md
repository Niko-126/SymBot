# 02 · 架构与分层

> 覆盖：全仓模块拓扑｜本章回答"为什么这么切、谁依赖谁、加东西该放哪"。
> 逐文件清单见 [03-files.md](03-files.md)，各层内部机制见 04–16 章。

## 1. 六层结构

```text
┌─ 接入层 ──── app/adapters/（QQ 官方 botpy） · app/api/onebot.py（NapCat 上报） · panel/（HTTP 面板）
│
├─ 接口层 ──── app/api/{routes,console,trend,group}.py  （124 个 HTTP 操作）
│
├─ 主链层 ──── app/mind/{loop,prompt,llm,models,guard}.py  （chat_turn 一条链）
│
├─ 领域层 ──── app/memory  app/relation  app/intimacy  app/life  app/group  app/web  app/persona
│
├─ 基础层 ──── app/core/{config,paths,db,runtime,memengine,continuity,preflight,channel}.py
│
└─ 引擎 ────── vendor/symvolve_memory（vendored 只读）

 旁路：app/lifecorpus/（离线语料管线，不进运行时）  app/qa/（测试底座，隔离进程）
```

## 2. 依赖方向（AST 实测，2026-11-01）

按「源目录 → 目标目录」统计 `import app.*` 的边数与目标被依赖次数：

| 被依赖方 | 入度（被几个模块 import） |
|---|---|
| `app.core` | **73** |
| `app.relation` | 16 |
| `app.memory` | 14 |
| `app.mind` | 10 |
| `app.persona` | 10 |
| `app.group` | 4 |
| `app.life` | 3 |
| `app.web` | 2 |
| `app.api` | 1（只有 `main.py`） |

主要边：

```text
memory   → core(16)  mind(1)  persona(3)  relation(1)
api      → core(15)  relation(6) group(4) memory(4) mind(4) life(2) web(2) persona(1)
relation → core(9)   mind(1)   persona(1)
intimacy → core(6)   mind(1)   relation(1)
life     → core(6)   memory(4) relation(2) mind(1) persona(1)
group    → core(5)   memory(2)
adapters → core(3)   mind(1)   relation(1)
mind     → core(5)   memory(4) relation(4) persona(2) life(1)
```

两条规律：

1. **基础层不被领域层反向依赖**，唯二例外都在「组合根」里（见下节）。
2. **`mind` 是主链但不是最底层**：它同时依赖 memory / relation / life / persona，把它们串在一次对话里。

## 3. 组合根：`app/core/runtime.py`

`runtime.py` 名义上属于基础层，实际上它是**组合根（composition root）**——所有单例在这里装配：

```python
from app.mind import llm as llm_mod        # ← 基础层反向依赖主链层
from app.persona import load as persona_load
from app.relation import engine
...
from app.relation import users as users_mod   # 延迟 import（函数内）
```

`app/core/db.py:363` 同样在函数体内延迟 `from app.memory import memv2`。

**为什么允许这样**：单进程单例应用里必须有一个地方把「配置 → 数据库 → 人设 → 模型客户端 → 关系引擎」拼起来，那就是 `runtime.init()`。代价是**不能在 `core` 的其他模块里随手 import 领域层**——只有 `runtime.py` 有这个特权，新代码一律靠 `rt` 实例传入，不要新增反向 import。

## 4. 三条进入路径

### 4.1 面板 / 外部 HTTP 客户端

```text
panel/index.html ──fetch──→ POST /v1/chat {text, user_id?, chat_type?}
                              └→ routes.py 处理函数
                                   └→ mind.loop.chat_turn(rt, text, source="panel", user_id=0, ...)
                                        └→ 返回 {reply: [...], state: {...}, meta: {...}}
```

面板线 `user_id=0` 有特殊语义：**强制 `is_lover=True`**，不经过真实恋爱判定（见 [12-api.md](12-api.md)）。

### 4.2 QQ 官方 bot（botpy WS）

```text
腾讯 WS ──→ adapters/qq.py（botpy Client）
              ├→ adapters/intake.py（静默 / 改名 / 搜视频 前置层，两个通道共用）
              ├→ mind.loop.chat_turn(...)
              └→ Client.api.post_c2c_message / post_group_message（一次一条，can_split=false）
```

拿不到 QQ 号（只有 openid），`family` 按 QQ 号的判定在这条通道无效——**已知边界，不是 bug**。

### 4.3 OneBot / NapCat（HTTP 上报）

```text
NapCat ──POST──→ /onebot/event
                   └→ api/onebot.py 分流（私聊/群/@/表情/语音）
                        ├→ adapters/intake.py
                        ├→ mind.loop.chat_turn(...)
                        └→ httpx → api_base（send_msg / send_private_msg / set_friend_add_request）
                             失败 → data/onebot_outbox.jsonl
```

OneBot 是**全量能力通道**：多气泡、表情包、语音、群主动都只有它能验证。

## 5. 谁写哪张表（避免越权）

| 数据 | 唯一写入方 | 别人只能 |
|---|---|---|
| 记忆 `mem_item` / `mem_cue` / `mem_event` | `app/memory/h2.py: add()` | 读（`retrieve`） |
| 关系四维 `relation` / `user_relation` | `app/relation/engine.py: apply_appraisal()` | 读 |
| 关系流水 `relation_log` | `apply_appraisal()` | 读 |
| 会话 `session`（名字/终止） | `app/relation/session.py` | 读 |
| 恋人 `lover` | `app/relation/love.py` / `users.py` | 读 |
| 对话事件 `events` | `app/mind/loop.py`（第 6 步） | 读 |
| 摘要 `summaries` | `app/memory/compress.py` | 读 |
| 连续性 `continuity_log` | `app/core/continuity.py: record()` | 读 |
| 用量 `usage` | `app/mind/llm.py` | 读 |
| 开关 `kv` | 各子系统按自己的键 | — |
| 热梗 `gags` / `learned_gag` | `app/web/gags.py`、`harvest.py` | 读（**永不同步进记忆**） |

## 6. 扩展点（改动照这个顺序）

| 要加的东西 | 改哪 | 别忘了 |
|---|---|---|
| **HTTP 路由** | `app/api/*.py` 加 `@router.get/post` | 跑 `scripts/fullcheck.py`（会遍历 OpenAPI 要求无 5xx），面板同步 fetch |
| **记忆 kind** | `h2.add(kind=...)` + `app/mind/prompt.py` 的 kind→标签表 | 不改标签表，这个 kind 永远进不了 prompt |
| **关系维度** | `relation` + `user_relation` 两表补列、`apply_appraisal()` 改公式、阶段阈值两处（人设 `stages` 与 `users.STAGE_AT`） | 两处阈值必须同步 |
| **通道** | 新建适配器 → 事件转 `chat_turn` → 发送；启动时 `channel.set_channel()` | 别在适配器里写业务 |
| **后台任务** | `app/main.py` 的 startup 里起 | 必须有显式开关、`try/except` 兜底、mock 下不联网 |
| **Prompt 段** | `prompt.build()` 里加段并计预算 | system 段不参与历史裁剪 |
| **数据文件** | 一律挂 `DATA_ROOT`（见 [18-config.md](18-config.md)） | 别写死仓库相对路径，打包态会挂 |

## 7. 反模式（代码里明确禁止的写法）

1. **在适配器里拼 prompt / 写记忆 / 算关系**——适配器只做「事件 → `chat_turn` → 发送」。
2. **直接 `UPDATE relation SET affinity=...`**——必须走 `apply_appraisal()`，否则没有流水、没有夹取、两套表会分叉。
3. **写 `memories` 视图**——它是视图，写必失败；写只走 `h2.add()`。
4. **改她而不记录**——任何改状态的动作都要 `continuity.record()`。
5. **在 `core/` 里新增反向 import**——只有 `runtime.py`（组合根）与 `db.py` 的延迟 import 允许。
6. **测试里直接 `import app.main`**——它会建库写 `kv`；用 `app/qa/sandbox.py` 或子进程 + 临时 `SYMEM_DATA`。

## 8. 交叉引用

- 每层的实现细节：04（core）/ 05（mind）/ 06（memory）/ 08（relation）/ 10（life）/ 12（api）/ 13（entry）
- 测试如何隔离：16（qa）/ 22（测试与验收）
- 哪些"看起来像 bug 其实是约定"：23（边界与红线）
