# 20 · 前端面板（panel）

> 覆盖：`panel/*.html`（4 个单文件页 / 8,567 行 / 375,006 B ≈ 366 KB）＋ `panel/symvolve-components.js`（2,819 行）＋ `panel/symvolve-tokens.css`（131 行）＋ `panel/assets/*`（1 个 README + 4 个图）｜基于 2026-11-01 的代码状态｜上游：docs/12（API）、docs/14（联网与内容）、docs/19（人格包）｜下游：docs/18（配置）

## 1. 这一层干什么

面板是**四个纯静态 HTML 文件**，没有前端框架、没有构建步骤、没有 `package.json`、没有 `node_modules`。浏览器直接打开就能跑，FastAPI 用一个 `StaticFiles` 挂在 `/` 上把它们当静态资源发出去。

四个页面分工：`index.html` 是**日常对话**（她要陪的那个人用的），`settings.html` 是**设定与校准**（人物/关系/记忆/日程/模型/灵魂六个 tab，滑杆改人格、手动改好感、钉住或删记忆、导入导出灵魂档案），`console.html` 是**QQ 好友/群聊/记忆的后台**（运维用的），`trend.html` 是**热梗库与 B 站视频**。

它明确不做：不实现任何业务逻辑（全部推给后端 `/v1/*`）、不做前端路由（页面之间用 `<a href>` 硬跳）、不缓存接口响应（每个页面自己 `fetch` 一遍）、不做鉴权体系（除控制台口令外，靠"服务只监听 127.0.0.1"）。

## 2. 文件清单

| 文件 | 行数 | 字节 | 一句话职责 |
|---|---|---|---|
| `panel/index.html` | 2,739 | 120,934 | 对话主界面：聊天 + 右侧状态栏 + 告别层 + 开发者面板 + 设置/用量弹窗 |
| `panel/settings.html` | 1,798 | 77,404 | 设置页：人物/关系/记忆/日程/模型/灵魂 六个 tab |
| `panel/console.html` | 3,080 | 137,911 | 控制台：好友列 + 聊天列 + AI 想法列（看法/观察/思考/日记/管理） |
| `panel/trend.html` | 950 | 38,757 | 热梗库：搜视频（可发给 QQ 好友）+ 热梗列表/抓取/统计 |
| `panel/symvolve-components.js` | 2,819 | 129,625 | 自研 Web Components 库（18 个 `sv-*` 组件），`window.SVComponents` |
| `panel/symvolve-tokens.css` | 131 | 4,597 | 设计令牌（四色 + 字体 + 间距 + 形状 + 动效 + 语义色） |
| `panel/assets/README.md` | 11 | 486 | LOGO 来源与四个尺寸的用途 |
| `panel/assets/whale-logo-64.png` | — | 10,654 | 顶栏 26px / 设置页 22px |
| `panel/assets/whale-logo-128.png` | — | 36,121 | 中间尺寸 |
| `panel/assets/whale-logo-256.png` | — | 118,468 | 空状态大头像 72px |
| `panel/assets/whale-logo.ico` | — | 178,221 | 桌面快捷方式图标 |
| **合计** | **11,528 行** | **833,178 B** | 文本 11,528 行 / 509,714 B（≈498 KB），四个图共 343,464 B |

行数只统计文本文件：4 个 HTML + 1 个 JS + 1 个 CSS = 11,517 行，加 `README.md` 11 行 = 11,528 行。图片是二进制，只算字节。

## 3. 模块逐一详解

### 3.1 `panel/index.html`：对话页

**DOM 分区**（`<body>` 第 917 行起，13 个区块）：

| 区块 | 起点 | 主要元素 id | 职责 |
|---|---|---|---|
| `.topbar` | 919 | `whaleLogo`、`engineBadge`/`engineName`、`provider`/`providerName`、`railToggle`、`settingsBtn`、`refresh`、`winMin`/`winClose` | 品牌 + 模型徽标 + 通道状态 + 窗口按钮（`winBtns` 仅在桌面壳里显示） |
| `section.chat` | 951 | `personaName`、`stageTag`、`tagline`、`personaMeta`、`nowLine`/`nowTimeTop`/`activityTop`/`energyTop`/`sleepTag`、`messages`、`emptyState`、`input`、`send`、`chatStatus` | 聊天头（她是谁/现在几点/在干嘛/精力）+ 消息流 + 输入框 |
| `aside.rail` 第 1 段 | 992 | `relationMeta`、`stageName`、`stageNext`、`dims`、`scale`、`deltaLine` | 关系四维（好感/信任/心动/厌恶）+ 阶段刻度尺 |
| `.rail` 第 2 段 | 1004 | `nowTime`、`dayPhase`、`nowActivity`、`meters` | 此刻（虚拟时间/时段/在做什么/精力条），标题旁标 `30s` |
| `.rail` 第 3 段 | 1013 | `traitsMeta`、`traits` | 八维人格条 |
| `.rail` 第 4 段 | 1019 | `memTotal`、`memTags`、`memRecent` | 记忆总数 / 类型标签 / 最近 5 条 |
| `.rail` 第 5 段 | 1026 | `usageSection`、`statRange`、`statTokens`、`statCost`、`statsOpen` | 本会话 tokens 与预估花费 |
| `.rail` 第 6 段 | 1034 | `pathInput`、`importBtn`、`importNote`、`ingestResult` | 导入 TXT 语料（路径直填） |
| `details.debug` | 1045 | `debug`、`debugBody` | 上下文调试（展开时拉 `/v1/inspect`） |
| `.farewell` | 1057 | `farewell`、`farewellTitle`、`farewellLetter`、`farewellLeave`、`farewellImport` | 她离开时的全屏告别层 |
| `.letter-bar` | 1079 | `letterBar`、`letterOpen` | 告别信浮条 |
| `.dev-modal` / `.dev-panel` | 1085 / 1096 | `devPass`、`devMsg`、`devList`、`devRecord`、`dvAffinity`、`dvTrust`、`dvAttraction`、`dvAversion`、`dvEnergy`、`dvMood`、`dvApply`、`dvAwake` | 开发者面板（连点 LOGO 12 次进入） |
| `#settingsModal` | 1134 | `mixSwitch`、`deltaSwitch`、`llmSwitch`、`deepSwitch`、`styleList`、`provUrl`/`provKey`/`provModel`/`provSave`/`provMsg` | 轻量设置（模型接入 + 四个开关 + 风格选择） |
| `#statsModal` | 1182 | `statsTabs`、`statIn`/`statOut`/`statCost2`、`statBars`、`statModels`、`statHistory` | 用量明细（时/日/月/年四档） |

**两个 `<script>` 块**（1207–2415、2417–2736），是两个独立 IIFE：

| 块 | 范围 | 职责 | `api()` 实现 |
|---|---|---|---|
| 第一块 | 聊天与状态栏 | `/v1/chat`、`/v1/board`、`/v1/health`、`/v1/ingest`、`/v1/inspect`、`/v1/farewell`、`/v1/dev/*` | 第 1270 行：`AbortController` + **8 秒超时**；`API_BASE` 在 `file://` 下改指 `http://127.0.0.1:8787` |
| 第二块 | 设置与用量 | `/v1/settings`、`/v1/settings/provider`、`/v1/stats`、`/v1/health`（引擎徽标）、`/v1/dev/session/clear`、`/v1/dev/anchors`、`/v1/dev/last`、`/v1/dev/logs/export` | 第 2430 行：无超时、**不拼 `API_BASE`**（相对路径） |

**主要交互**：

| 交互 | 触发 | 行为 |
|---|---|---|
| 发消息 | `send` 点击 / `Enter` | 先插"正在输入"，`POST /v1/chat`，按返回 `reply` 数组逐条追加气泡；每条之间的间隔由 `delaysFor()`（1406–1423 行）算：接口给了 `meta.delays_ms` 就照用（下限 400ms，首条超 900ms 时会先把"正在输入"停满再出气泡），没给就兜底 `300 + 上一条长度 × 45` 并压到 400–900ms |
| 30 秒刷新 | `setInterval`（2097） | `busy \|\| document.hidden` 时跳过；只刷侧栏，不动消息区与输入框 |
| 好感变化提示 | `deltaSwitch` | 每条回复后弹 `#deltaToast`，4200ms 自动隐藏 |
| 导入语料 | `importBtn` / `pathInput` 回车 | `POST /v1/ingest`，结果渲染进 `ingestResult` |
| 展开上下文 | `details#debug` 的 `toggle` | `GET /v1/inspect` 拿上一轮 prompt 的 `sections`/`used_tokens` |
| 告别 | `GET /v1/farewell` | 轮询 2500ms；`status == "ready"` 时亮 `letterBar`；点"读信"再拉一次 farewell 取信文 |
| 开发者入口 | 连点 `whaleLogo` 12 次（3 秒内） | 弹 `devModal`，`POST /v1/dev/login` 校验 `developer.key` |
| 开发者动作 | `devPanel` 十个功能按钮（另有 × 与"关闭"两个） | 见下表 |

**开发者面板动作 → 接口**：

| 按钮 | 接口 |
|---|---|
| 合并冷记忆 | `POST /v1/dev/memory/compact`（第二块里由 `devCall(path)` 统一拼 `?password=`） |
| 清空会话 | `POST /v1/dev/session/clear?password=` |
| 清空记忆 | `POST /v1/dev/memory/clear?password=` |
| 重置好感 | `POST /v1/dev/relation/reset?password=` |
| 看锚点 | `GET /v1/dev/anchors?limit=40&password=` |
| 看 Prompt | `GET /v1/inspect` |
| 单步 | `GET /v1/dev/last?password=` |
| 导出日志 | `GET /v1/dev/logs/export?password=` |
| 导出档案 | `GET /v1/dev/transfer/export?password=` |
| 导入档案 | `POST /v1/dev/transfer/import?password=&keep_events=1` |
| 应用属性 | `POST /v1/dev/state/set?password=`（好感/信任/心动/厌恶/精力/心情六个数字） |
| 假装醒着 | `POST /v1/dev/awake?on=1&password=` |

**坑**：

1. 两个 `api()` 语义不同：第一块有 8 秒超时且支持 `file://` 直开，第二块两者都没有。双击 HTML 用 `file://` 打开时，聊天能通、设置/用量弹窗会失败。
2. 开发者密码从前端传 query string（`?password=…`），会进浏览器历史与服务日志；这是现有实现，不是文档遗漏。
3. `winBtns`（最小化/关闭）默认 `hidden`，只有桌面壳（`app/desktop.py` 的 pywebview）触发 `pywebviewready` 事件后（`index.html:2619–2625`）才显示，并调 `window.pywebview.api.minimize()/close()`。
4. `rail` 的第 5、6 段在 HTML 里嵌套写错了（`#usageSection` 被包在"导入"那个 `section` 里，缩进与注释标注的"5 导入/6 调试"错位一行），视觉正常，DOM 结构不规整。
5. 开发者口令保存在 `window.__devKey`（`index.html:2290`），刷新页面即丢失，需要重新连点 LOGO 12 次。

### 3.2 `panel/settings.html`：设置页

**六个 tab**（`nav.tabs` 第 426 行，面板是 `section.panel`，靠 `hidden` 切换，不换 URL）：

| tab | 面板 id | 内容 | 接口 |
|---|---|---|---|
| 人物 | `panel-persona` | 名字/年龄/物种、八维滑杆、原话列表、反向锚点、阶段表、日记 | `GET /v1/persona`、`POST /v1/persona/traits`、`GET /v1/diary`、`POST /v1/diary/run` |
| 关系 | `panel-relation` | 四维条形 + 阶段 + 关系变化日志 + 手动校准 | `GET /v1/board`、`GET /v1/relation/log?limit=20`、`POST /v1/relation` |
| 记忆 | `panel-memory` | 按类型/关键词查记忆、钉住/删除、召回调试 | `GET /v1/memories?…`、`POST /v1/memories/{id}/pin`、`DELETE /v1/memories/{id}`、`GET /v1/memory/recall?q=` |
| 日程 | `panel-schedule` | 此刻状态 + 作息表 + 主动消息开关（冷却/每日上限） | `GET /v1/schedule`、`POST /v1/schedule` |
| 模型 | `panel-models` | 接入信息、任务路由表、参数、四个开关、用量与按模型统计 | `GET /v1/settings/models`、`POST /v1/settings/models`、`GET /v1/settings`、`POST /v1/settings`、`GET /v1/stats?bucket=hour&limit=24` |
| 灵魂 | `panel-soul` | 导出档案、快照列表/新建/恢复、导入文件 | `POST /v1/soul/export`、`GET/POST /v1/soul/snapshots`、`POST /v1/soul/snapshots/restore`、`POST /v1/soul/import` |

**主要交互**：滑杆 `input` 事件只更新数字标签，`change` 事件（松手）才 `POST /v1/persona/traits`——写库时机是"松手"，不是"拖动中"；记忆列表的钉住/删除是即时请求；导入档案走 `<input type="file" accept=".json">` + `FileReader`，body 直接是 `.life.json` 原文。

**坑**：页面标题与副标题写死"小涵 · 人物与运行配置"（第 421 行），而当前生效人设是 `whale-chan`（小鲸）——文案没跟着人设走，属静态残留。

### 3.3 `panel/console.html`：控制台

**三列布局**（`main.layout` 第 517 行）：

| 列 | 起点 | 内容 | 接口 |
|---|---|---|---|
| 左 `col--friends` | 519 | 好友列表 + 搜索 + NapCat 状态/同步 + 登记已知用户 + 好友申请 | `GET /v1/console/friends`、`POST /v1/console/friends`、`GET /v1/console/friends/requests`、`POST/DELETE /v1/console/friends/requests/{id}`、`GET /v1/console/onebot/login`、`POST /v1/console/onebot/sync`、`POST /v1/console/friends/{id}/forget`、`DELETE /v1/console/friends/{id}` |
| 中 `col--chat` | 563 | 与某人的聊天记录 + 输入框 + "以小鲸身份/模拟对方" + "私聊/群聊" + 表情选择 | `GET /v1/console/chat/{user_id}`、`POST /v1/console/send`、`GET /v1/console/stickers?limit=60`、`POST /v1/console/send_sticker`、`GET /v1/console/sticker/{id}.webp` |
| 右 `col--mind` | 619 | 五个 tab：看法/观察/思考/日记/管理 | `GET /v1/console/mind`、`POST /v1/console/mind/run`、`GET /v1/console/observations`、`DELETE /v1/console/observations`、`GET /v1/console/relation/{user_id}`、`POST /v1/console/relation` |

**"管理"tab 的六张卡**（`#adminPanels` 第 644 行起）：记忆（`GET/DELETE /v1/console/memory/{user_id}`、`DELETE /v1/console/memory/item/{id}`）、印象（`GET/DELETE /v1/console/impression/{user_id}`）、好感度（`GET /v1/console/relation/{user_id}` + `POST /v1/console/relation`）、观察（`GET /v1/console/observations?limit=50` + 两条 DELETE）、私密记录（`GET /v1/console/intimate/{user_id}`、`POST /v1/console/intimate/{user_id}/analyze`、`DELETE /v1/console/intimate/{user_id}`）、群招呼（`GET /v1/console/groups`、`POST /v1/console/groups/{gid}/greet`）。

**底部"运行设置"**（`#runPanel`，全局项，不随好友切换）：深度思考（`GET/POST /v1/console/thinking`）、测试期应答（`GET/POST /v1/console/testing`）、群静默（`GET /v1/group/status`、`POST /v1/group/silence`、`POST /v1/group/unsilence`）。

**轮询**：`loadHealth` 每 30 秒；当前会话聊天每 4 秒（`document.visibilityState !== 'visible'` 或 `sending` 时跳过，且"不在底部就不拽走"）；好友列表每 15 秒。

**坑**：控制台是唯一会读 `localStorage['ai-life-console-token']` 并在 401 时弹 `window.prompt('控制台口令（config.yaml 的 console.token）')` 的页面（第 1000–1011、1045–1056 行）；`settings.html` 完全不发这个头，但它也只调主路由（`/v1/persona`、`/v1/soul/*` 等），不碰 `/v1/console/*`，所以配了口令也不会坏。

### 3.4 `panel/trend.html`：热梗页

**两个区块**：

| 区块 | 起点 | 内容 | 接口 |
|---|---|---|---|
| A 搜视频 | 330 | 关键词 + 发送对象下拉 + 视频卡片网格 | `GET /v1/trend/search?q=`（同时把视频写进 `web_videos`）、`POST /v1/trend/send_video`、`GET /v1/console/friends`（填下拉） |
| B 热梗库 | 356 | 三个统计块（热梗总数/抓取视频/上次抓取）+ 抓取按钮 + 过滤 + 列表 | `GET /v1/trend/stats`、`GET /v1/trend/gags`、`DELETE /v1/trend/gags/{id}`、`POST /v1/trend/harvest` |

无轮询：进页面拉一次，按钮手动刷新。`api()` 第 497 行会带 `X-Console-Token`（`/v1/console/friends` 需要），失败时把 `detail` 抽成可读文案（数组取 `[0].msg`）。

**坑**：`POST /v1/trend/harvest` 是同步阻塞接口，一轮实测约 30 秒（`harvest.py` 模块 docstring 原文"实测一轮 ~30s、上百次外网请求"），页面没有超时提示也没有进度，点完只能等。

### 3.5 `panel/symvolve-components.js`

**是什么**：自研的原生 Web Components 库（版本 `3.1.0`，`window.SVComponents`），零依赖、经典脚本（非 ES module），按 `DOMContentLoaded` 自动注册。每个组件继承 `HTMLElement`，内部 `attachShadow({mode:'open'})`，样式全部消费 `--sv-*` 令牌，**组件内不出现裸色值**。

**导出的 18 个组件**：

| 类 | 标签 | 类 | 标签 |
|---|---|---|---|
| `SvButton` | `sv-button` | `SvTable` | `sv-table` |
| `SvInput` | `sv-input` | `SvDialog` | `sv-dialog` |
| `SvSelect` | `sv-select` | `SvLoading` | `sv-loading` |
| `SvTag` | `sv-tag` | `SvError` | `sv-error` |
| `SvStatus` | `sv-status` | `SvNav` | `sv-nav` |
| `SvEmpty` | `sv-empty` | `SvNavItem` | `sv-nav-item` |
| `SvToast` | `sv-toast` | `SvNavSection` | `sv-nav-section` |
| `SvTab` | `sv-tab` | `SvDataCard` | `sv-data-card` |
| `SvPanel` | `sv-panel` | `SvTabs` | `sv-tabs` |

**`window.SVComponents` 的公开字段**：`version`（`'3.1.0'`）、`components`（15 个主组件的字符串数组）、`subComponents`（`['sv-tab','sv-panel','sv-nav-section']`）、`toast(text, opts)`（命令式弹提示）、`reducedMotion()`（读 `prefers-reduced-motion`）。

**默认中文文案**（可用属性覆盖）：`loading: '处理中'`、`dismiss: '关闭'`、`remove: '关闭'`；状态六档 `running 运行中 / done 已完成 / failed 失败 / paused 已暂停 / idle 空闲 / attention 待复核`。

**事实核查**：`settings.html`、`console.html`、`trend.html` 都在第 9 行引入了这个文件，但**三个页面里没有任何一个 `<sv-*>` 标签**（全仓 `grep '<sv-'` 零命中），页面全是手写 div + 自备 CSS。这个 127 KB 的文件目前是**加载但未使用的资源**；四个页面里只有它和 `symvolve-tokens.css` 被引用，`index.html` 两个都不引（令牌是内联复制的一份）。

### 3.6 `panel/symvolve-tokens.css`

由 `tools/build-tokens.mjs` 从 `01-visual/design-tokens.json` 生成，文件头写死 `DO NOT EDIT`。**注意：这个生成器与源 JSON 不在本仓库里**（`tools/`、`01-visual/`、`06-design-language/` 三个目录都不存在，全仓也搜不到 `design-tokens.json`），所以 `symvolve-tokens.css` 在本仓库是"只读成品"，改它只能直接改 CSS 本身。三层结构：

| 层 | 前缀 | 条目 |
|---|---|---|
| primitive · color | `--sv-color-*` | **只允许四个十六进制值**：`ink #0B0C0E`、`paper #F2F2EF`、`steel #9AA0A6`、`signal #00E0C6` |
| primitive · typography | `--sv-font-*` / `--sv-weight-*` / `--sv-text-*` / `--sv-leading-*` / `--sv-tracking-*` | 4 套字体栈（en-display / en-text / zh / mono）、4 个字重（300/400/500/700）、9 档字号（`display-xl 96px` → `micro 11px`）、4 个行高（1 / 1.2 / 1.65 / 1.45）、5 个字距（`-0.03em` → `+0.12em`） |
| primitive · space | `--sv-space-*` / `--sv-page-margin-*` / `--sv-section-gap` / `--sv-grid-unit` | 4px 网格：4/8/12/16/24/32/48/64/96/128/192；页边距 mobile 24 / desktop 64 / print 12；段间距 128 |
| primitive · shape | `--sv-radius-*` / `--sv-border-width-*` | 圆角 0/2/4/8/9999px；描边 1/1.5/2px |
| primitive · motion | `--sv-duration-*` / `--sv-ease-standard` | 240ms（micro/state）、600ms（transition）、1200ms（brand-reveal）；`cubic-bezier(0.2,0,0,1)`；**只允许 180° 旋转** |
| primitive · opacity | `--sv-opacity-*` | full 1 / secondary 0.62 / tertiary 0.38 / hairline 0.16 / disabled 0.28 / tint 0.04 / scrim 0.72 |
| semantic · surface / text / border / status | `--sv-surface-*`、`--sv-text-*`、`--sv-border-*`、`--sv-focus-ring`、`--sv-selection-*`、`--sv-disabled-*`、`--sv-status-*`、`--sv-link-*` | 全部由四个原色 + `rgb(... / %)` 派生；语义层是页面唯一该引用的层 |

**坑（逐项核过）**：`index.html` 没有 `<link>` 这个文件，而是在 `<style>`（第 8–915 行）里内联了一份同名令牌（`:root` 从第 15 行起）。两份**不是同一份**：

1. 内联副本只有 83 个令牌，`symvolve-tokens.css` 有 103 个——缺的 20 个是 `--sv-page-margin-mobile/desktop/print`、`--sv-section-gap`、`--sv-duration-brand-reveal`、`--sv-opacity-disabled/tint/scrim`、`--sv-surface-scrim/accent`、`--sv-text-inverse-muted`、`--sv-border-inverse-strong`、`--sv-focus-ring-inverse`、`--sv-accent-signal/on-paper`、`--sv-status-running/done/paused`、`--sv-link-text/inverse`。
2. 共有的 83 个里有 11 个**写法不同**：CSS 用 `rgb(11 12 14 / 62%)` 这类 alpha 写法，内联副本用 `color-mix(in srgb, var(--sv-color-ink) 62%, transparent)`（`--sv-text-secondary`、`--sv-text-muted`、`--sv-surface-sunken`、`--sv-border-hairline`、`--sv-border-control`、`--sv-border-inverse`、`--sv-disabled-text`、`--sv-disabled-surface`、`--sv-disabled-border`、`--sv-status-idle`、`--sv-text-inverse-secondary`）。语义等价（内联副本第 12–13 行的注释自称"与 generated/tokens.css 的 alpha 语义等价"），文本不同步。

另外，三个引用 `symvolve-tokens.css` 的页面各写了一大段自己的 CSS（`console.html` 第 10–495 行 486 行、`settings.html` 第 10–404 行 395 行、`trend.html` 第 10–310 行 301 行），令牌只被少量消费。

### 3.7 面板线与 uid=0

面板这条线在数据层是"单人模式"，用 `user_id = 0` 表示，四处语义：

| 位置 | 语义 |
|---|---|
| `POST /v1/chat`（`routes.py:150–162`） | `uid = 0` 时 `is_lover = True`——面板语气的默认前提是"你们是恋人"，不查关系表 |
| `app/memory/h2.py:_partitions`（第 387–415 行） | `user_id <= 0` 时**不筛人**：把所有 `user_id` 的分区拼一起，全库都算她的记忆；QQ 侧 `user_id > 0` 才只看"本人 + 共用" |
| `app/life/proactive.py:420` / `routes.py:865` | 面板上的免打扰是**全局**（`set_dnd(0, …)`），QQ 私聊那条线也会遵守 |
| `app/life/thoughts.py:546–556` | 念头的冷却/去重按 `user_id = ? OR user_id = 0` 查，面板与共用记录互相可见 |

### 3.8 三个通道的能力差异

`app/core/channel.py` 用 `Caps` 数据类描述差异，面板只读 `channel.caps()` 决定怎么渲染回复：

| 能力位 | `panel` | `onebot`（NapCat） | `qq_official`（botpy） |
|---|---|---|---|
| `stickers` 发表情包 | ✅ | ✅ | ❌ |
| `voice` 发语音 | ❌ | ✅ | ❌ |
| `images` 发图 | ✅ | ✅ | ✅ |
| `group_proactive` 群主动开口 | ✅ | ✅ | ❌ |
| `private_proactive` 私聊主动 | ✅ | ✅ | ✅ |
| `max_reply_chars` 单条上限 | 4000 | 1500 | 1500 |
| `can_split` 多气泡 | ✅ | ✅ | ❌（官方 API 一次只能回一条） |

**面板能做的**：直接跟内核对话（不经过 QQ）、看/改全部状态、走 `/v1/console/send` 借用 OneBot 把话发到 QQ、跑记忆与关系的迁移、连点 LOGO 进开发者面板改数值。
**只有 OneBot 能做的**：真实收发 QQ 消息与群消息、@ 提醒（`app/api/onebot.py:271` 里 `mention_keywords` + 她自己的名字）、表情包真发送（`send_sticker` 需要 OneBot 的 `send_private_msg` 带 image 段）、语音、好友申请处理（`/v1/console/friends/requests/*` 的通过/拒绝要 OneBot 侧接口）、群成员同步（`/v1/console/onebot/sync`）、群招呼（`/v1/console/groups/{gid}/greet`）。面板是"驾驶舱"，OneBot 是"手和脚"。

## 4. 数据流

```text
浏览器（127.0.0.1:8787）
  ├─ GET  /                → FastAPI StaticFiles(panel, html=True) → panel/index.html
  ├─ GET  /settings.html   → 同上（静态直出）
  ├─ GET  /console.html    → 同上
  └─ GET  /trend.html      → 同上
页面脚本 fetch('/v1/...')
  ├─ 同源直连（http://127.0.0.1:8787）→ app/api/routes.py / console.py / trend.py / group.py
  ├─ file:// 直开时：第一块脚本改指 http://127.0.0.1:8787（CORS allow_origins=["*"] 兜住）
  └─ 控制台口令：请求头 X-Console-Token（值存 localStorage['ai-life-console-token']）
路由 → app.mind.loop.chat_turn / app.core.db / app.relation.engine / app.persona.*
  └─ 返回 JSON → 页面 render*（无状态，刷新即重拉）
```

**挂载顺序**（`app/main.py:30–159`）：`create_app()` 先 `include_router` 五个 router（`routes`、`onebot`、`console`、`trend`、`group`），**最后**才 `app.mount("/", StaticFiles(directory=str(panel), html=True), name="panel")`——`/v1/*` 全部先注册，所以静态挂载不会盖住接口。`panel/index.html` 不存在时退化成占位页（`_PLACEHOLDER`）+ `/index.html → /` 重定向。

**通信约定**：全程 `fetch` + JSON，**没有 SSE、没有 WebSocket**。等待靠轮询：index 30s（状态栏）/ 30s（用量卡）/ 2.5s（告别信，仅在告别层打开时）；console 30s（健康）/ 4s（当前聊天）/ 15s（好友）；settings 与 trend 不轮询。

## 5. 改这一层要注意

1. **无构建**：改完 HTML/JS/CSS 刷新即可，没有打包、没有热更新、没有 sourcemap。新增页面只需把 `.html` 丢进 `panel/`，`/xxx.html` 立刻可访问（`StaticFiles(html=True)`）。
2. **新增接口调用**：必须自己判断是否要带 `X-Console-Token`——只有 `/v1/console/*` 需要（见 `app/api/console.py:28–42`，配了 `console.token` 才校验，否则只靠回环地址）。
3. **新增交互**：四个页面各自引一份独立的 `api()` 实现（index 里甚至有两份），改超时/错误处理要**分别改**，没有共享工具文件。
4. **改设计令牌**：`symvolve-tokens.css` 头部写 `DO NOT EDIT`，但它引用的生成器（`tools/build-tokens.mjs`）与源 JSON（`01-visual/design-tokens.json`）**都不在本仓库**，所以现实做法是直接改这个 CSS；改完还要同步 `index.html` 里第 15 行起的内联副本，而后者只有 83 个令牌、11 个用 `color-mix` 写法，漏同步就会出现两套色值。
5. **用组件库**：现在没有任何页面用 `sv-*` 标签；要启用，直接在 HTML 里写 `<sv-button>` 这类标签即可（脚本 9 行已加载，`DOMContentLoaded` 后自动 `customElements.define`），但页面自带的 CSS 只有 `.btn` 这类类名，混用会得到两套观感。
6. **改 uid 语义**：`uid = 0` 同时是"面板单人"和"全局免打扰"的键，改 `_partitions` 或 `proactive.set_dnd(0, …)` 会同时影响 QQ 侧行为（`docs/17` 有表结构，`docs/04` 有分区规则）。
7. **静态资源路径**：所有页面都用相对路径（`./assets/whale-logo-64.png`、`./symvolve-components.js`），页面必须留在 `panel/` 根目录；放进子目录会 404。
8. **不要加前端路由**：`<a href="./console.html">` 是硬跳转，页面之间没有共享状态，任何"全局状态"都只能放 `localStorage`（现有唯一用例是控制台口令）。

## 6. 交叉引用

- 相关：docs/12（每个页面用到的 `/v1/*` 的完整出入参）
- 相关：docs/14（`trend.html` 背后的 `gags` / `harvest` 状态机）
- 相关：docs/19（`settings.html` 人物 tab 改的是 `traits` 表，不是 YAML）
- 相关：docs/18（`console.token`、`developer.key`、`server.host` 的配置语义）

## 7. 工具与声音（2026-11-01 新增）

`settings.html` 多了一个「工具」页签，右上角七号位，深链可直接打开：`settings.html#tools`。

| 区块 | 控件 | 写入 |
|---|---|---|
| 工具环节 | 总开关 / 群聊也调 / 联网 / 识图 + 视觉模型 + 视觉端点 | `POST /v1/settings`（`tools_*` 一组） |
| 声音（TTS） | 总开关 / 直接发语音 / 复用缓存 + 服务地址 / 音色 / 风格 / 每分钟上限 / 缓存保留天数 | `POST /v1/settings`（`tts_*` 一组） |
| 试听 | 「试听」按钮 → `POST /v1/console/tts` → `<audio>` 播 `/v1/console/tts/file?path=…` | 只读 + 合成 |

设计沿用这套面板的既有组件（`.switch` / `.field` / `.kv-grid` / `.status`），
只加了**一处签名动效**：标题后的信号扫描线（`.sv-scan`，2.6s 循环，`prefers-reduced-motion` 下自动停）。

### 7.1 面板自检（因为"以前很多功能不能用"）

`scripts/panel_check.py` 把"面板能不能用"变成可自动跑的检查，六项：

1. 四个页面都 200；
2. 面板 JS 里出现的每个 `/v1/...`，后端 OpenAPI 里必须存在；
3. `$('id')` 引用的元素，要么页面里有、要么 JS 动态创建；
4. `/v1/settings` 必须覆盖面板读取的**每一个键**（工具/声音最容易改后端忘改面板）；
5. 面板的保存动作（`POST /v1/settings`）能走通并回显；
6. TTS 试听在关着时优雅拒绝，不 500。

跑法：`.\.venv\Scripts\python.exe scripts\panel_check.py`（期望 `120 通过，0 失败`）。

已接进 `scripts/verify_all.py`：全量与 `--quick` 都跑（约 3 秒），所以总控现在是全量 **11 项**、quick **9 项**，改完面板直接看报告里「面板自检」那一行；期望值同步在 [22-testing.md](22-testing.md) §2。
