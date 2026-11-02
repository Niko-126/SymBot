# 18 · 配置项全量（三层合并）

> 覆盖：`config.yaml`、`config.local.yaml`（不入库）、`app/core/config.py`(356 行)、`app/core/paths.py`(26 行)｜基于 2026-11-01 代码状态｜上游：docs/04｜下游：docs/13

## 1. 加载顺序（实现在 `app/core/config.py: load()`）

三层，后者覆盖前者，**段内浅合并**：

| 层 | 文件 | 位置常量 | 是否入库 |
|---|---|---|---|
| 1 | `config.yaml` | `RESOURCE_ROOT / "config.yaml"` | 是（提交进仓库） |
| 2 | `config.local.yaml` | 先找 `DATA_ROOT / "config.local.yaml"`，没有再找 `RESOURCE_ROOT / "config.local.yaml"` | **否**（`.gitignore` 已忽略） |
| 3 | 环境变量 | `env_var()` → `SYMEM_<NAME>` 优先，`AI_LIFE_<NAME>` 兜底 | — |

合并细节（`load()` 第 316–327 行）：

1. `raw = yaml.safe_load(config.yaml)`。
2. `local = next((p for p in LOCAL_CONFIG_PATHS if p.exists()), None)` —— **只取第一个存在的**，不会两个都读。
3. 对 local 的每个顶层键：若两边都是 dict，则 `{**raw[key], **value}`（**只合一层，子段整体替换**）；否则 local 值直接覆盖。
4. 用 `ServerCfg(**raw["server"])` 这种方式逐段构造 dataclass。
5. 最后 `_apply_env(cfg)` 做环境变量覆盖。

`get()` 带模块级缓存 `_cached`：**同一进程内配置只读一次**；改了 `config.local.yaml` 必须重启进程或 `POST /v1/persona/reload`（人设）/ 面板的 provider 保存接口才会生效。

## 2. 路径根（`app/core/paths.py`）

| 常量 | 开发态 | 打包态（PyInstaller） |
|---|---|---|
| `RESOURCE_ROOT` | 仓库根（`app/core/paths.py` 往上两层） | `sys._MEIPASS`（临时解包目录） |
| `DATA_ROOT` | 同 `RESOURCE_ROOT` | `Path(sys.executable).parent`（exe 同目录，可写可迁移） |
| 覆盖 | `SYMEM_DATA` 或 `AI_LIFE_DATA` 环境变量可强制改写 `DATA_ROOT` | 同左 |

`DATA_ROOT` 一处生效、处处生效：`store.db_path` 的相对路径、`data/media`、`persona-active.yaml` 回退、`config.local.yaml` 搜索、`data/` 下一切子系统（`human_life`/`stickers`/`voices`/`archives`/`_adv`）都挂在它上面。**所有离线测试脚本靠 `SYMEM_DATA` 指向临时目录来实现隔离。**

## 3. 配置段与字段全表

### 3.1 `server` → `ServerCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `host` | str | `127.0.0.1` | 只监听本机 |
| `port` | int | `8787` | 被 `SYMEM_PORT` 覆盖 |

### 3.2 `provider` → `ProviderCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `base_url` | str | `https://api.deepseek.com/v1` | OpenAI 兼容根 |
| `api_key` | str | `""` | **空 = mock 模式**（`is_mock` 属性） |
| `model` | str | `deepseek-chat` | 主模型 |
| `small_model` | str | `""` | 后台任务（抽取/摘要）用；空则等于 `model`（`small` 属性） |
| `temperature` | float | `0.85` | |
| `max_tokens` | int | `400` | 单轮输出上限 |
| `timeout_s` | float | `60.0` | 请求超时 |
| `force_model` | str | `""` | **非空时所有任务都走它**，跳过风格/混合/深思路由 |

派生属性：

- `is_mock` → `not api_key.strip()`
- `small` → `small_model or model`

### 3.3 `context` → `ContextCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `window` | int | `64000` | 模型上下文窗口，只是上限 |
| `reserve_output` | int | `1024` | 预留输出 |
| `warn_ratio` | float | `0.55` | ratio 模式水位线 |
| `force_ratio` | float | `0.75` | |
| `hard_ratio` | float | `0.90` | |
| `mode` | str | `budget` | `budget`（推荐）/ `ratio`；被 `SYMEM_CONTEXT_MODE` 覆盖 |
| `budget` | int | `6000` | 工作集目标 tokens |
| `trigger` | int | `5000` | 超过就压缩 |
| `hard_cap` | int | `8000` | 超过就降级 |
| `keep_recent` | int | `12` | 永远保留原文的轮数（**注意：主链实际取 `covered_to` 之后全量，这个键目前是死变量**） |
| `verbatim_max` | int | `30` | 未压缩原文上限 |
| `degrade` | bool | `False` | 降级开关 |

派生：`usable = max(2000, window - reserve_output)`。

### 3.4 `store` → `StoreCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `db_path` | str | `data/life.db` | 相对路径按 `DATA_ROOT` 拼（`Config.db_file` 属性） |
| `media_dir` | str | `data/media` | |

### 3.5 `persona` → `PersonaCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `path` | str | `persona/whale-girl.yaml` | 人设文件；**`config.yaml` 里实际写的是 `persona/whale-chan.yaml`** |

`Config.persona_file` 的解析优先级（代码第 212–225 行）：

1. `persona_override`（来自 `SYMEM_PERSONA` 环境变量，且文件存在）
2. `config.yaml` 的 `persona.path`（相对 `RESOURCE_ROOT`，存在才用）
3. `DATA_ROOT / "persona-active.yaml"`（历史导入残留，**排在显式配置之后**，避免人设莫名回退）
4. 兜底返回第 2 步的路径（哪怕不存在）

### 3.6 `life` → `LifeCfg`

| 字段 | 类型 | 默认值 |
|---|---|---|
| `timezone` | str | `Asia/Shanghai` |

### 3.7 `developer` → `DeveloperCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `True` | 开启后才能手动设定好感、强制新建会话 |
| `key` | str | `""` | 口令（`config.yaml` 里写了明文口令，连点 LOGO 后输入） |
| `logo_taps` | int | `12` | 连点次数 |

### 3.8 `qq` → `QQCfg`（官方开放平台 botpy）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `False` | 被 `SYMEM_QQ_ENABLED` 覆盖；多开时设 `0` 避免抢同一 AppID |
| `auto_start` | bool | `True` | |
| `appid` | str | `""` | 被 `SYMEM_QQ_APPID` 覆盖 |
| `token` | str | `""` | 开放平台 Secret；被 `SYMEM_QQ_TOKEN` 覆盖；**只写 config.local.yaml** |
| `sandbox` | bool | `True` | 生产要设 `false` |
| `group_at_only` | bool | `True` | 群里只回 @ |

### 3.9 `onebot` → `OneBotCfg`（NapCat / LLOneBot / Lagrange）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | bool | `False` | 被 `SYMEM_ONEBOT_ENABLED` 覆盖 |
| `api_base` | str | `http://127.0.0.1:3000` | NapCat 的 HTTP API |
| `access_token` | str | `""` | |
| `self_id` | str | `""` | 小号 QQ 号 |
| `group_at_only` | bool | `True` | |
| `timeout_s` | float | `10.0` | |
| `ignore_ids` | list[str] | `[]` | 这些 QQ 号的消息一律不当人处理（防两个 bot 互相抬杠） |
| `mention_keywords` | list[str] | `["小鲸鱼","大肥鱼","鲸鱼娘","小鲸","DeepSeek娘","超可爱大肥鱼","超级大肥鱼"]` | 群里不 @ 也能触发的名字/关键词 |
| `sticker_chance` | float | `0.45` | 回完带表情包的概率 |
| `voice_chance` | float | `0.15` | 回完带语音的概率 |
| `auto_accept_friend_request` | bool | `True` | 默认直接通过好友申请 |
| `proactive_push` | bool | `False` | 按用户主动外发；**默认关** |
| `proactive_interval_min` | int | `5` | 主动外发间隔（分钟） |

### 3.10 `console` → `ConsoleCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `token` | str | `""` | 空 = 不校验，靠"只监听 127.0.0.1"兜底；非空则要求请求头 `X-Console-Token` |

### 3.11 `trend` → `TrendCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `harvest_enabled` | bool | `False` | mock / 无 key 时永远跳过 |
| `interval_hours` | int | `24` | |
| `limit` | int | `20` | 每次抓多少条 |

### 3.12 `relationship` → `RelationshipCfg`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `lover.enabled` | bool | `True` | |
| `lover.threshold` | float | `80.0` | 恋人门槛（**不是 100**） |
| `lover.cooldown_days` | int | `7` | 分手复合冷却 |
| `caps.enabled` | bool | `False` | 每日关系变化上限，内部小群默认**关闭** |
| `caps.daily_pos` | float | `5.0` | |
| `caps.daily_neg` | float | `-8.0` | |

## 4. 环境变量全表

`env_var(name)` = `os.environ["SYMEM_" + name] or os.environ["AI_LIFE_" + name]`。

| 变量名（`SYMEM_*` 优先） | 作用 | 实现在 |
|---|---|---|
| `MOCK` | 值为 `"1"` 时清空 api_key → mock 模式（**只认字符串 `1`**） | `_apply_env` |
| `PERSONA` | 覆盖人设路径，同时设置 `persona_override`（绝对路径则原样用） | `_apply_env` |
| `QQ_ENABLED` | `1/true/yes/on` → 开官方 bot | `_apply_env` |
| `ONEBOT_ENABLED` | 同上 → 开 OneBot | `_apply_env` |
| `QQ_APPID` | 覆盖 `qq.appid` | `_apply_env` |
| `QQ_TOKEN` | 覆盖 `qq.token` | `_apply_env` |
| `BASE_URL` | 覆盖 `provider.base_url` | `_apply_env` |
| `API_KEY` | 覆盖 `provider.api_key` | `_apply_env` |
| `MODEL` | 覆盖 `provider.model` | `_apply_env` |
| `SMALL_MODEL` | 覆盖 `provider.small_model` | `_apply_env` |
| `PORT` | 覆盖 `server.port` | `_apply_env` |
| `CONTEXT_MODE` | 覆盖 `context.mode` | `_apply_env` |
| `DB` | 覆盖 `store.db_path` | `_apply_env` |
| `CONSOLE_TOKEN` | 覆盖 `console.token` | `_apply_env` |
| `DEVELOPER_KEY` | 覆盖 `developer.key`；**留空 = 开发者接口全部 403**（公开仓库默认留空，本机自用才设） | `_apply_env` |
| `DEVELOPER_ENABLED` | `1/true/yes/on` → 覆盖 `developer.enabled` | `_apply_env` |
| `DATA` | 改写 `DATA_ROOT`（不在 config.py，在 `paths.py`，模块导入时立刻生效） | `paths.py` |

## 5. 写回：只写 `config.local.yaml`

两个函数，都**只写 `DATA_ROOT / "config.local.yaml"`**，绝不改 `config.yaml`、绝不进数据库：

| 函数 | 写什么 |
|---|---|
| `save_provider(base_url, api_key, model) -> Path` | provider 三段 |
| `save_provider_fields(fields: dict) -> Path` | provider 下的附加字段（`temperature` / `max_tokens` / `small_model` 等） |

两者都是「读出已有 YAML → 改 provider 段 → `yaml.safe_dump(allow_unicode=True, sort_keys=False)` 写回」，保留文件里其它段。面板的"模型设置"保存走这条路径。

## 6. 坑（代码里真实存在的）

1. **未知键会炸。** `ServerCfg(**(raw.get("server") or {}))` 这种写法，配置里多写一个 dataclass 没有的键（比如在 `server:` 下写 `workers: 4`）会直接 `TypeError`，进程起不来。`config.yaml` 里的 `experience:` 段之所以不出事，是因为 `Config(...)` 根本不取这个键——**它是一段不生效的预留配置**。
2. **`MOCK` 只认 `"1"`。** 写 `true` 不会进 mock，会真的去连 provider。
3. **`get()` 有进程内缓存。** 改完 `config.local.yaml` 不重启就看不到变化；`save_provider` 只写文件，不刷缓存。
4. **`context.keep_recent` 是死变量**（主链实际用 `covered_to` 之后的全部事件），见 docs/05。
5. **`DATA_ROOT` 决定一半仓库的落点。** 用 `SYMEM_DATA` 跑测试时，`config.local.yaml`、`persona-active.yaml`、`data/` 全跟着搬——这也是离线测试能做到零污染的原因。
6. **`config.local.yaml` 里有明文 key/token**（`provider.api_key`、`qq.token`）。它已被 `.gitignore` 忽略，任何提交前请确认它没有被强行 add。
7. **`persona.path` 的默认值**在 dataclass 里是 `persona/whale-girl.yaml`，而当前 `config.yaml` 写的是 `persona/whale-chan.yaml`——**以配置文件为准**，dataclass 默认值只在配置缺段时兜底。

## 7. 交叉引用

- 配置怎么被消费：docs/04（config 单例与 runtime）、docs/05（context 预算裁剪）、docs/13（启动时读哪些段）
- 人设解析：docs/19
- 通道开关：docs/13

## 7. 工具环节的配置段（`tools:`）

```yaml
tools:
  enabled: true            # 总开关：关掉 = 回到纯聊天，不查资料也不看图
  web:
    enabled: true
    max_results: 5         # 一次搜索取几条
    timeout_s: 8.0
    max_chars: 1500        # 抓正文截断（进 prompt，别挤爆上下文）
    allow_hosts: []        # 空 = 不限制；填了就只允许这些域名（含子域）
  vision:
    enabled: true
    model: ""              # 留空 = 跟 provider.model 一致；看图建议显式指定视觉模型
    max_tokens: 400
    timeout_s: 20.0
    max_images: 2          # 一轮最多看几张
```

对应 `ToolsCfg` / `WebToolCfg` / `VisionToolCfg`（`app/core/config.py`）。
`provider.is_mock` 为真时**所有工具强制关闭**——离线测试因此零联网、零花费（回归 D26 钉住）。

## 8. 模型与视觉的分家（2026-11-01）

默认接的是**一个原生多模态模型**：`provider.base_url = https://api.siliconflow.cn/v1`、
`provider.model = deepseek-ai/DeepSeek-V4.1-Flash`。看图走同一套端点。

想把"聊天"和"看图"拆给两家，只配 `tools.vision`：

```yaml
tools:
  vision:
    base_url: "https://your-vision-endpoint/v1"   # 留空 = 跟 provider 一致
    api_key: ""                                   # 留空 = 用 provider.api_key
    model: "Qwen/Qwen2.5-VL-7B-Instruct"          # 留空 = 用 provider.model
```

`app/tools/vision.py` 的取值顺序就是：`tools.vision.*` → `provider.*`。
所以"语言模型用 A 家、视觉模型用 B 家"是配置问题，不用改代码。

## 9. 声音（TTS）配置段

```yaml
voice:
  enabled: false                 # 总开关；关着不连本机服务
  base_url: http://127.0.0.1:9880
  api_key: ""
  voice: feiyu
  style: cute                    # cute / gentle
  timeout_s: 60.0                # 异步作业轮询上限
  max_chars: 200                 # 超过就截断（服务端上限 2000）
  send_in_chat: false            # 合成完直接发语音（仅 OneBot）
  cache_enabled: true            # 同一句话复用已合成的 WAV
  max_per_min: 6                 # 每分钟最多合成几条（0 = 不限）
  workers: 2                     # 专属合成线程池大小（1–4）
  cache_ttl_days: 15             # 缓存保留天数（启动时清一次；0 = 永不清理）
```
