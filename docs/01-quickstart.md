# 01 · 快速开始

> 目标：一台 Windows 机器上，从零把它跑起来，有面板、有人设、能聊天、能重置。
> 配置字段详解见 [18-config.md](18-config.md)，接口见 [12-api.md](12-api.md)。

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| OS | Windows（主开发/运行环境） | 代码本身跨平台，`.venv\Scripts\` 是 Windows 路径 |
| Python | **3.14**（开发机实测 3.14.8） | 用了 `sys.monitoring`，需要 3.12+ |
| 包管理 | pip + venv | 无 poetry / uv / lockfile |
| 磁盘 | 空库约 1 MB；带语料/表情包/语音可到几百 MB | 全在 `data/`（不入库） |
| 网络 | 调模型、查词、抓梗时才出网 | 无 key 时全流程离线可跑（mock） |

## 2. 装

```powershell
# 在仓库根目录（含 requirements.txt 的那一层）执行

# 建虚拟环境（开发机上仓库目录里通常已带 .venv，重装时才需要）
py -3.14 -m venv .venv

$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt

# 只有要用 QQ 官方 bot 时才装（可选依赖，不影响内核）
& $py -m pip install -r requirements-qq.txt
```

`requirements.txt` 全部 6 个直接依赖：

```text
fastapi>=0.115
uvicorn[standard]>=0.32
httpx>=0.27
pydantic>=2.9
pyyaml>=6.0
tzdata>=2024.1
```

## 3. 配

**三层，后者覆盖前者**（细节见 [18-config.md](18-config.md)）：

| 层 | 文件 | 放什么 |
|---|---|---|
| 1 | `config.yaml` | 提交进仓库的默认值 |
| 2 | `config.local.yaml` | **你真实的 key / token，不入库** |
| 3 | 环境变量 `SYMEM_*`（旧名 `AI_LIFE_*` 仍兼容） | 临时覆盖、多开、测试隔离 |

最小配置（新建 `config.local.yaml`，或面板"设置 → 模型"保存后自动生成）：

```yaml
provider:
  base_url: https://api.siliconflow.cn/v1
  api_key: sk-你的key
  model: deepseek-ai/DeepSeek-V4.1-Flash
```

判定规则：**`provider.api_key` 为空字符串 = mock 模式**（`ProviderCfg.is_mock`）。mock 模式不联网、不花钱，回复由本地模拟，全链路照样跑通。

## 4. 跑

### 4.1 内核 + 面板（唯一推荐方式）

```powershell
& $py -m app.cli serve --port 8787
# 打开 http://127.0.0.1:8787/
```

面板与 API 同端口；`panel/index.html` 存在时静态挂载到 `/`，不存在时返回一个占位页（并提示缺哪个文件）。

### 4.2 桌面版（pywebview 窗口）

```powershell
& $py -m app.desktop
```

内部仍然是同一个 uvicorn 服务，只是套了个原生窗口。

### 4.3 最薄启动垫片

```powershell
& $py launcher.py
```

### 4.4 mock 模式（无 key 也能跑）

```powershell
$env:SYMEM_MOCK = "1"     # 注意：只认字符串 "1"，写 true 无效
& $py -m app.cli serve
```

### 4.5 启动时到底起了什么

`app/main.py: create_app()` 做四件事：

1. `runtime.init()` —— **有副作用**：建库、建表、读人设、写 `kv`。
2. 挂 5 个 router（`routes` / `onebot` / `console` / `trend` / `group`），共 124 个操作。
3. 起后台任务（每个都有独立开关，mock 下不联网）：
   - 主动私聊外发：`onebot.enabled` **且** `onebot.proactive_push`
   - 热梗抓取：`trend.harvest_enabled` **且**非 mock
   - QQ 官方 bot：`qq.enabled` 且配了 `appid`+`token`
4. `shutdown` 时停 QQ 适配器。

CORS 全开（`allow_origins=["*"]`），因为服务只监听 `127.0.0.1`；**改成对外监听前必须收窄**。

## 5. CLI 全子命令

`python -m app.cli <cmd>`：

| 子命令 | 参数 | 做什么 | 副作用 |
|---|---|---|---|
| `serve` | `--port 8787` | 启动内核与面板 | 建库、读人设 |
| `init` | — | 初始化数据库与人物 | 建表 |
| `ingest` | `<path>` | 导入聊天记录 TXT（走记忆写入唯一入口） | 写记忆 |
| `reference` | `<path>` `--force` | 导入参考语料（**只学风格，不写用户记忆**） | 写 `ref_turns`/`ref_quotes` |
| `anchors` | `--per-scene 24` `--extra 40` | 从参考语料重建锚点池 | 写 `anchors` |
| `cold` | `--dry` | 合并冷记忆（时间+主题软合并） | 写 `memory_merges` / `merged_into` |
| `compress` | — | 手动把旧对话折成线索（`summaries`） | 写摘要 |
| `letter` | — | 打印记忆交接信模板 | 只打印 |
| `arc` | `<path>` | 只看情绪分期，不写库 | 无 |
| `reset` | — | 清空全部状态（记忆、好感、对话），**人设保留** | 删库重建 |

> `reset` 是破坏性操作。真机库里她的记忆与关系会一起消失。

## 6. 第一次用：三步

1. **起名**：第一次对话会走命名剧本（不走模型）——她说「好啊。」「你想让我叫什么？」，你回一个名字（带标点也行，如「就叫小满。」），她确认后名字锁定（`session.name_locked=1`），**不可改**。
2. **聊**：关系从 `observer`(0) 起，按四维总分推进阶段：`swimmer`(20) → `echo`(40) → `samefreq`(60) → `lover`(80) → `bond`(95)。
3. **看她**：面板首页看气泡与状态；控制台页看记忆、关系流水、亲密画像、群与开发者开关。

## 7. 常见故障

| 症状 | 原因 | 处理 |
|---|---|---|
| 起了但 `api_key` 明明写了还是 mock | 环境变量 `SYMEM_MOCK=1` 残留 | `Remove-Item Env:SYMEM_MOCK` |
| 改了 `config.local.yaml` 没生效 | `config.get()` 有进程内缓存 | 重启进程 |
| 人设没换过去 | `persona.path` 写错 / 相对路径按 `RESOURCE_ROOT` 解析 | 用绝对路径或 `SYMEM_PERSONA` |
| 端口占用 `WinError 10048` | 上一个实例没退 | 关掉旧进程，或 `serve --port 8788` |
| 面板 404 / 占位页 | `panel/index.html` 不在 | 检查 `panel/` 是否完整 |
| 启动即崩、报 `TypeError: __init__() got an unexpected keyword argument` | 配置文件里多了 dataclass 没有的键 | 删掉那个键（见 18-config 第 6 节） |
| 官方 bot 连不上 | `qq.enabled=false` / appid 或 token 错 / 沙箱标志不对 | 看 `/v1/health` 的 `qq` 段 |

## 8. 真机联调前的安全纪律

**NapCat（OneBot）登录用户本人 QQ 号时，内核必须关 OneBot**：

```powershell
$env:SYMEM_ONEBOT_ENABLED = "0"
```

否则她会用那个号说话、发主动消息。真机联调时这一行必须显式加在起服务前。

## 9. 重置与备份

| 操作 | 命令 | 说明 |
|---|---|---|
| 清库重来 | `& $py -m app.cli reset` | 记忆/关系/事件全清，人设保留 |
| 手动备份 | 停服后复制 `data/life.db`（连同 `-wal`、`-shm`） | WAL 模式下三件套一起拷才完整 |
| 换电脑 | 面板「档案导出」→ 自包含档案 → 新机导入 | 见 [19-persona.md](19-persona.md) |
