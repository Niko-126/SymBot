from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.core.paths import DATA_ROOT, RESOURCE_ROOT

ROOT = RESOURCE_ROOT
CONFIG_PATH = RESOURCE_ROOT / "config.yaml"
LOCAL_CONFIG_PATHS = [
    DATA_ROOT / "config.local.yaml",
    RESOURCE_ROOT / "config.local.yaml",
]


@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass
class ProviderCfg:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    small_model: str = ""
    temperature: float = 0.85
    max_tokens: int = 400
    timeout_s: float = 60.0
    # 全局强制模型：非空时所有任务都走它（不走风格/混合/深思路由）﻿‌‍﻿﻿﻿​﻿​﻿‌‌‌﻿​‍‌​﻿‍‌‌‌‍‍‌‌‍‌‍‌‌﻿﻿
    force_model: str = ""

    @property
    def is_mock(self) -> bool:
        return not self.api_key.strip()

    @property
    def small(self) -> str:
        return self.small_model or self.model


@dataclass
class ContextCfg:
    window: int = 64000
    reserve_output: int = 1024
    warn_ratio: float = 0.55
    force_ratio: float = 0.75
    hard_ratio: float = 0.90
    # 工作记忆：默认按固定预算，不按窗口比例（窗口只是上限）
    mode: str = "budget"          # budget | ratio
    budget: int = 6000            # 工作集目标
    trigger: int = 5000           # 超过就压缩
    hard_cap: int = 8000          # 超过就降级
    keep_recent: int = 12         # 永远保留原文的轮数
    verbatim_max: int = 30        # 未压缩原文的上限（超过就折成线索）
    degrade: bool = False         # 降级模式：默认不开启，触顶后才启用

    @property
    def usable(self) -> int:
        return max(2000, self.window - self.reserve_output)


@dataclass
class StoreCfg:
    db_path: str = "data/life.db"
    media_dir: str = "data/media"


@dataclass
class PersonaCfg:
    path: str = "persona/whale-girl.yaml"


@dataclass
class LifeCfg:
    timezone: str = "Asia/Shanghai"


@dataclass
class DeveloperCfg:
    """开发者开关：只有打开时才能手动设定好感、强制新建会话。

    以后加口令/多因子验证，开关留着，验证方式换掉即可。
    """

    enabled: bool = True
    key: str = ""
    logo_taps: int = 12


@dataclass
class QQCfg:
    """QQ 官方开放平台机器人（qq-group 分支用）。凭据只写 config.local.yaml。

    token 是开放平台「开发设置」里的 Token/Secret（SDK 用它换 access_token）。
    """

    enabled: bool = False
    auto_start: bool = True
    appid: str = ""
    token: str = ""
    sandbox: bool = True
    group_at_only: bool = True


@dataclass
class LoverCfg:
    """恋人绑定：好感 100 只是资格；唯一恋人；分手复合冷却。"""

    enabled: bool = True
    threshold: float = 80.0       # 表白的最低好感
    cooldown_days: int = 7
    # 2026-11-02 owner：一天就处上对象不像人。加"认识够久"这道门——
    # 从他第一次跟她说话算起，至少满这么多天才接受表白。
    min_days: int = 3
    # 绑定成功后问一句"要不要公开"，答案记进 kv（lover_public:<uid>）
    ask_public: bool = True


@dataclass
class CapsCfg:
    """每日关系变化上限：内部小群默认**关闭**（不限）。"""

    enabled: bool = False
    daily_pos: float = 5.0
    daily_neg: float = -8.0


@dataclass
class RelationshipCfg:
    lover: LoverCfg = field(default_factory=LoverCfg)
    caps: CapsCfg = field(default_factory=CapsCfg)


@dataclass
class IntimacyCfg:
    """亲密模块的门控与安全阀（R 级插件也用这一段配置）。

    默认值按"最保守也可用"设定：家人/未成年硬门控开着、群聊只到 L2、
    aftercare 必给、直白连续不超过两句。可在 config.local.yaml 里覆盖。

    注意：`enabled` 是**整块亲密模式的总开关**，也是 R 级插件的启用位——
    `app/intimacy/r18/` 存在且本开关为 True 时，gate 才会切到插件链路。
    """

    # 总开关：关掉整块亲密模式（含 safeword、aftercare 注入），也等于禁用 r18 插件
    enabled: bool = True
    # 非恋人 + 性意图 → 调侃拒绝（关了就当普通聊天，不进模式）
    non_lover_deflect: bool = True
    # 家人（生父/干爹）永不进入亲密模式
    kinship_block: bool = True
    # 自称未成年 / 明显未成年 → 永不进入
    minor_block: bool = True
    # safeword：红灯立刻停、黄灯减速、绿灯继续
    safeword: bool = True
    # 退出直白后必须给事后照护
    aftercare: bool = True
    # 群聊最高阶段（L0-L5 里的级数，L2=明示；直白永远不给）
    group_max_stage: int = 2
    # 私聊恋人最高阶段
    private_max_stage: int = 5
    # 连续直白句数上限（第 N+1 句必须转成反应/问句）
    max_consecutive_explicit: int = 2
    # 每条回复至少一个身体反应 + 一个感官（生成质量）
    sensory_check: bool = True


@dataclass
class OneBotCfg:
    """OneBot v11（NapCat / LLOneBot / Lagrange）—— 小号对话用。

    账号登录在 NapCat 里；这里只连它的 HTTP API + 接收 HTTP 上报。
    """

    enabled: bool = False
    api_base: str = "http://127.0.0.1:3000"
    access_token: str = ""
    self_id: str = ""
    group_at_only: bool = True
    timeout_s: float = 10.0
    #: 这些 QQ 号的消息一律不当人处理（比如群里另一个机器人，否则两个 bot 会互相抬杠）
    ignore_ids: list[str] = field(default_factory=list)
    # 群里不 @ 也能触发她的名字/关键词（小鲸鱼、大肥鱼…）
    mention_keywords: list[str] = field(
        default_factory=lambda: [
            "小鲸鱼", "大肥鱼", "鲸鱼娘", "小鲸", "DeepSeek娘", "超可爱大肥鱼", "超级大肥鱼",
        ]
    )
    # 回复后附带表情包的概率（0~1）；内部小群玩可以调高
    sticker_chance: float = 0.45
    # 回复后附带本地语音（record 段）的概率；命中触发词才发
    voice_chance: float = 0.15
    # 好友申请：默认直接通过（回 set_friend_add_request）；设 False 就只记下来，等人点
    auto_accept_friend_request: bool = True
    # 按用户主动消息外发：默认关（不给你真实好友发未经触发的话）
    proactive_push: bool = False
    proactive_interval_min: int = 5


@dataclass
class LauncherCfg:
    """桌面启动器（scripts/start_panel.py）与 NapCat WebUI 用的连接参数。

    `qq` / `napcat_dir` 属于私人信息，只写 config.local.yaml（不入库）；
    仓库里不预置真实号，也不猜 NapCat 装在哪。
    """

    qq: str = ""
    napcat_dir: str = ""
    webui_port: int = 0


@dataclass
class FamilyCfg:
    """家人名单（生父/干爹）：谁算家人由配置给，代码里不再硬编码 QQ 号。

    每项形如 `{openid: "123456789", role: "生父", aliases: ["老爸"]}`。
    面板写入时会同时落 config.local.yaml 的 `family` 段与 db kv（`family_roles`），
    `love.family_role()` 优先读 kv（面板即时生效），没有才回落到这里。

    `father_roles` 是「要通知哪些角色」的默认值；`announce_godfather` 是
    「干爹也通知」的默认开关（kv `announce_to_godfather` 优先，见 announce.py）。
    """

    roles: list[dict] = field(default_factory=list)
    father_roles: list[str] = field(default_factory=lambda: ["生父"])
    announce_godfather: bool = False


@dataclass
class ConsoleCfg:
    """Web 控制台（/v1/console/*）。

    token 留空 = 不校验，靠"服务只监听 127.0.0.1"兜着。
    填了就必须带 `X-Console-Token` 头（控制台页面会弹一次输入并存在 localStorage）。
    """

    token: str = ""


@dataclass
class TrendCfg:
    """热梗自动抓取（路线 A）：后台每天抓一轮 B站热梗评论。

    mock / 无 key 时永远跳过（测试不联网）；只在真实 provider 下生效。
    """

    harvest_enabled: bool = False
    interval_hours: int = 24
    limit: int = 20


@dataclass
class WebToolCfg:
    """联网工具：查资料 / 抓网页。mock 或 tools.enabled=false 时一律不联网。"""

    enabled: bool = True
    max_results: int = 5
    timeout_s: float = 8.0
    max_chars: int = 1500      # 抓回来的正文截断长度（进 prompt，别把上下文挤爆）
    allow_hosts: tuple[str, ...] = ()  # 空 = 不限制；填了就只允许这些域名（含子域）


@dataclass
class VisionToolCfg:
    """识图工具：把用户发来的图交给一个多模态模型换成文字。

    走 OpenAI 兼容的 /chat/completions（image_url 那种多模态 content）。
    `model` 留空 = 用 provider.model（但多数纯文本模型不支持看图，建议显式指定）。
    """

    enabled: bool = True
    # 视觉可以走**另一家**：留空 = 跟 provider 同端点同 key；填了就用这套
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 400
    timeout_s: float = 20.0
    max_images: int = 2        # 一轮最多看几张图（多了烧钱）


@dataclass
class VoiceCfg:
    """文字转语音：对接本地声音服务（与「肥鱼声音」AstrBot 插件同一套 HTTP 契约）。

    服务本身是**第三方项目、不随本仓库分发**（它没有授权再分发）；
    用 `scripts/fetch_voice_service.ps1` 自己拉，或指向任何兼容这套接口的服务。

    默认关：开不开、要不要把语音直接发进聊天，都会花算力。
    """

    enabled: bool = False
    base_url: str = "http://127.0.0.1:9880"
    api_key: str = ""
    voice: str = "feiyu"
    style: str = "cute"           # cute / gentle
    timeout_s: float = 60.0       # 单次合成等待上限（服务是异步作业）
    max_chars: int = 200          # 超过就切句（服务端上限 2000，这里更保守）
    send_in_chat: bool = False    # 合成完直接当语音发出去（仅 OneBot 通道）
    cache_enabled: bool = True    # 同一句话复用已合成的 WAV（省算力）
    max_per_min: int = 6          # 每分钟最多合成几条（0 = 不限）
    workers: int = 2              # 专属合成线程池大小（1–4）
    cache_ttl_days: int = 15      # 缓存保留天数（0 = 永不清理）
    # ---- 语音会话节奏（2026-11-02）：连发几条后主动"说话累了"退回打字 ----
    ask_every: int = 3            # 每发几条语音就问一句"还要听吗"（问法交给模型变）
    streak_limit: int = 4         # 连发到几条就喊累、退回文字（用户明确要语音可解除）
    idle_reset_min: int = 30      # 多久没说话就把"累了"状态自动忘掉
    # ---- 点歌翻唱（点歌 → 搜 B 站 → 分离 → 换声 → 混音 → 发回）----
    cover_enabled: bool = True    # 关掉就只回文字，不自动去找歌
    cover_ack: str = "我先练一下。"        # 开唱前先说一句，拖住等待时间
    cover_max_wait_s: int = 240   # 整条管线最多等多久（超时就说不唱了）


@dataclass
class ToolsCfg:
    """工具环节总开关：联网工具 + 识图工具。

    默认开，但**mock 模式下永远不会真的出网**（测试零联网、零花费）。
    关掉它 = 她的主链回到"纯聊天"，不查资料、不看图。
    """

    enabled: bool = True
    # 群聊里要不要调工具：默认关（群里人多，搜索与视觉调用容易烧钱）
    group_enabled: bool = False
    web: WebToolCfg = field(default_factory=WebToolCfg)
    vision: VisionToolCfg = field(default_factory=VisionToolCfg)


@dataclass
class Config:
    server: ServerCfg = field(default_factory=ServerCfg)
    provider: ProviderCfg = field(default_factory=ProviderCfg)
    context: ContextCfg = field(default_factory=ContextCfg)
    store: StoreCfg = field(default_factory=StoreCfg)
    persona: PersonaCfg = field(default_factory=PersonaCfg)
    life: LifeCfg = field(default_factory=LifeCfg)
    developer: DeveloperCfg = field(default_factory=DeveloperCfg)
    trend: TrendCfg = field(default_factory=TrendCfg)
    tools: ToolsCfg = field(default_factory=ToolsCfg)
    voice: VoiceCfg = field(default_factory=VoiceCfg)
    intimacy: IntimacyCfg = field(default_factory=IntimacyCfg)
    qq: QQCfg = field(default_factory=QQCfg)
    onebot: OneBotCfg = field(default_factory=OneBotCfg)
    console: ConsoleCfg = field(default_factory=ConsoleCfg)
    relationship: RelationshipCfg = field(default_factory=RelationshipCfg)
    # 启动器/NapCat（小号、安装目录）与家人名单：都放 config.local.yaml
    launcher: LauncherCfg = field(default_factory=LauncherCfg)
    family: FamilyCfg = field(default_factory=FamilyCfg)
    # 运行环境：prod（默认，用真实库）/ test（发布默认，独立测试库 + 关外发通道）
    # 为什么要有它：仓库要能公开、要能被别人 clone 就试，不能一启动就往本地真库里写。
    environment: str = "prod"
    # 环境变量 AI_LIFE_PERSONA 指定的 人设文件：优先级最高（用于 QQ 分支等场景）
    persona_override: pathlib.Path | None = None

    @property
    def db_file(self) -> pathlib.Path:
        p = pathlib.Path(self.store.db_path)
        return p if p.is_absolute() else DATA_ROOT / p

    @property
    def persona_file(self) -> pathlib.Path:
        # 优先级：环境变量 > config.yaml 显式路径 > persona-active.yaml（导入产物）
        # 注意：config.yaml 里写的 persona.path 是"用户明确选择"，必须优先于
        # persona-active（那是历史导入残留，会让人设莫名其妙变回旧版本）
        if self.persona_override is not None and self.persona_override.exists():
            return self.persona_override
        p = pathlib.Path(self.persona.path)
        explicit = p if p.is_absolute() else ROOT / p
        if explicit.exists():
            return explicit
        active = DATA_ROOT / "persona-active.yaml"
        if active.exists():
            return active
        return explicit

    @property
    def panel_dir(self) -> pathlib.Path:
        return RESOURCE_ROOT / "panel"


def env_var(name: str) -> str | None:
    """读一个配置环境变量：新名 `SYMEM_<NAME>` 优先，母体旧名 `AI_LIFE_<NAME>` 兜底。

    改名口径见 docs/62：新项目对外只宣传 SYMEM_*，但旧脚本/旧文档里的
    AI_LIFE_* 永远还能用（387 项 smoke、150 项 fullcheck 都靠它跑）。
    """
    return os.environ.get(f"SYMEM_{name}") or os.environ.get(f"AI_LIFE_{name}")


def _str_list(value: object) -> list[str]:
    """把 YAML / JSON 里的「字符串或字符串列表」统一成 list[str]。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_bool(value: object, default: bool = False) -> bool:
    """宽松布尔：手写 YAML 里的 "false"/"0" 这类字符串也是 False（`bool("false")` 会是 True）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def normalize_family_roles(value: object) -> list[dict]:
    """归一化家人清单：每项只留 `{openid, role, aliases}`。

    收两种写法：`openid`（新）与 `qq`（旧人设 `family` 段的叫法）；
    `aliases` 允许写成逗号/顿号串。脏数据直接跳过——热路径要读它，不能因为
    一行 YAML 写错就抛异常。
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        openid = str(raw.get("openid") or raw.get("qq") or "").strip()
        role = str(raw.get("role") or "").strip()
        aliases = raw.get("aliases")
        if isinstance(aliases, str):
            aliases = [part.strip() for part in re.split(r"[,，、;；\s]+", aliases) if part.strip()]
        else:
            aliases = _str_list(aliases)
        if not (openid or role or aliases):
            continue
        out.append({"openid": openid, "role": role, "aliases": aliases})
    return out


def _apply_env(cfg: Config) -> Config:
    if env_var("MOCK") == "1":
        cfg.provider.api_key = ""
    if v := env_var("PERSONA"):
        cfg.persona.path = v
        _p = pathlib.Path(v)
        cfg.persona_override = _p if _p.is_absolute() else ROOT / _p
    if (v := env_var("QQ_ENABLED")) is not None:
        # 多开时用 SYMEM_QQ_ENABLED=0 关掉官方 bot，避免两个实例抢同一个 AppID
        cfg.qq.enabled = v.strip().lower() in ("1", "true", "yes", "on")
    if (v := env_var("ONEBOT_ENABLED")) is not None:
        cfg.onebot.enabled = v.strip().lower() in ("1", "true", "yes", "on")
    if v := env_var("QQ_APPID"):
        cfg.qq.appid = v
    if v := env_var("QQ_TOKEN"):
        cfg.qq.token = v
    if v := env_var("BASE_URL"):
        cfg.provider.base_url = v
    if v := env_var("API_KEY"):
        cfg.provider.api_key = v
    if v := env_var("MODEL"):
        cfg.provider.model = v
    if v := env_var("SMALL_MODEL"):
        cfg.provider.small_model = v
    if v := env_var("PORT"):
        cfg.server.port = int(v)
    if v := env_var("CONTEXT_MODE"):
        cfg.context.mode = v
    if v := env_var("DB"):
        cfg.store.db_path = v
    if v := env_var("CONSOLE_TOKEN"):
        cfg.console.token = v
    # 开发者口令：公开仓库里 config.yaml 留空，本机自用走 config.local.yaml 或环境变量。
    # 留空 = 开发者接口全部 403（见 app/api/routes.py: _dev_ok）。
    if v := env_var("DEVELOPER_KEY"):
        cfg.developer.key = v
    if (v := env_var("DEVELOPER_ENABLED")) is not None:
        cfg.developer.enabled = v.strip().lower() in ("1", "true", "yes", "on")
    # 启动器小号 / NapCat 目录：本来只在 config.local.yaml 里，测试与多开也允许
    # 用环境变量临时压过去（SYMEM_QQ 只给启动器用，和官方 bot 的 QQ_APPID 无关）。
    if v := env_var("QQ"):
        cfg.launcher.qq = v.strip()
    if v := env_var("NAPCAT_DIR"):
        cfg.launcher.napcat_dir = v.strip()
    # 家人清单：测试用 JSON 数组字符串直接注入，避免测试依赖私人号/私人配置。
    if v := env_var("FAMILY_ROLES"):
        try:
            parsed = json.loads(v)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            cfg.family.roles = normalize_family_roles(parsed)
    # 运行环境：发布版默认 environment: test —— 独立测试库 + 关掉所有对外通道，
    # 保证"clone 下来就能试，但绝不动用户真实库、也绝不替他往 QQ 里发东西"。
    # 显式设 SYMEM_DB 时尊重调用方（测试基线就是这么隔离的）。
    if v := env_var("ENV"):
        cfg.environment = v.strip().lower()
    if str(cfg.environment).lower() in ("test", "debug"):
        if not env_var("DB"):
            cfg.store.db_path = "data/test-life.db"
        cfg.onebot.enabled = False
        cfg.onebot.proactive_push = False
        cfg.qq.enabled = False
        cfg.voice.enabled = False
        cfg.trend.harvest_enabled = False
        cfg.tools.group_enabled = False
    return cfg


def save_section(section: str, values: dict[str, Any]) -> pathlib.Path:
    """把某个配置段的字段写进 `config.local.yaml`（不入库）。

    只做**浅合并**：段内已有的其它键保留，`None` 表示"这次不改"。
    面板上的工具/语音开关就走它——改完立刻写盘，重启仍然生效。
    """
    path = DATA_ROOT / "config.local.yaml"
    data: dict = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    seg = data.get(section)
    seg = dict(seg) if isinstance(seg, dict) else {}
    for key, value in (values or {}).items():
        if value is not None:
            seg[key] = value
    data[section] = seg
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path

def save_provider(base_url: str, api_key: str, model: str) -> pathlib.Path:
    """把模型配置写进 config.local.yaml（不入库），并返回路径。"""
    path = DATA_ROOT / "config.local.yaml"
    data: dict = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    provider = data.get("provider") if isinstance(data.get("provider"), dict) else {}
    provider["base_url"] = base_url.strip()
    provider["api_key"] = api_key.strip()
    provider["model"] = model.strip()
    data["provider"] = provider
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def save_provider_fields(fields: dict) -> pathlib.Path:
    """把 provider 下的附加字段（temperature / max_tokens / small_model）写进本地配置。

    和 save_provider 一样只写 config.local.yaml（不入库、不提交）。
    """
    path = DATA_ROOT / "config.local.yaml"
    data: dict = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    provider = data.get("provider") if isinstance(data.get("provider"), dict) else {}
    for key, value in fields.items():
        if value is not None:
            provider[key] = value
    data["provider"] = provider
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def load() -> Config:
    raw: dict = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    local = next((p for p in LOCAL_CONFIG_PATHS if p.exists()), None)
    if local is not None:
        extra = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(raw.get(key), dict):
                raw[key] = {**raw[key], **value}
            else:
                raw[key] = value

    _rel_raw = raw.get("relationship") or {}
    _launcher_raw = raw.get("launcher") if isinstance(raw.get("launcher"), dict) else {}
    _family_raw = raw.get("family") if isinstance(raw.get("family"), dict) else {}
    cfg = Config(
        server=ServerCfg(**(raw.get("server") or {})),
        provider=ProviderCfg(**(raw.get("provider") or {})),
        context=ContextCfg(**(raw.get("context") or {})),
        store=StoreCfg(**(raw.get("store") or {})),
        persona=PersonaCfg(**(raw.get("persona") or {})),
        life=LifeCfg(**(raw.get("life") or {})),
        developer=DeveloperCfg(**(raw.get("developer") or {})),
        trend=TrendCfg(**(raw.get("trend") or {})),
        tools=ToolsCfg(
            enabled=bool((raw.get("tools") or {}).get("enabled", True)),
            group_enabled=bool((raw.get("tools") or {}).get("group_enabled", False)),
            web=WebToolCfg(**((raw.get("tools") or {}).get("web") or {})),
            vision=VisionToolCfg(**((raw.get("tools") or {}).get("vision") or {})),
        ),
        intimacy=IntimacyCfg(**(raw.get("intimacy") or {})),
        qq=QQCfg(**(raw.get("qq") or {})),
        onebot=OneBotCfg(**(raw.get("onebot") or {})),
        console=ConsoleCfg(**(raw.get("console") or {})),
        voice=VoiceCfg(**(raw.get("voice") or {})),
        relationship=RelationshipCfg(
            lover=LoverCfg(**_rel_raw.get("lover") or {}),
            caps=CapsCfg(**_rel_raw.get("caps") or {}),
        ),
        launcher=LauncherCfg(**_launcher_raw),
        family=FamilyCfg(
            roles=normalize_family_roles(_family_raw.get("roles")),
            father_roles=_str_list(_family_raw.get("father_roles")) or ["生父"],
            announce_godfather=_as_bool(_family_raw.get("announce_godfather"), False),
        ),
        environment=str(raw.get("environment") or "prod").strip().lower(),
    )
    return _apply_env(cfg)


_cached: Config | None = None


def get() -> Config:
    global _cached
    if _cached is None:
        _cached = load()
    return _cached


def refresh() -> Config:
    """丢掉缓存，重新读一遍配置文件（下次 `get()` 一定拿到新值）。

    面板保存 `family` / `launcher`（只写 config.local.yaml）后调用它：
    `love.family_role()` 每次都现读 `config.get()`，刷新后新名单立刻生效。
    **不要**在别处直接写 `config._cached = None`——缓存的失效口只有这一个。
    """
    global _cached
    fresh = load()   # 先读成功再换缓存：配置文件半截写坏时不至于把旧配置也弄丢
    _cached = fresh
    return fresh
