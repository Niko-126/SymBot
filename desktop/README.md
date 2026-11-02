# SymBot 桌面壳（Electron）

这个壳把衡忆（SymBot）变成双击就开的 Windows 程序：Electron 管窗口和托盘，Python 内核（`python -m app.cli serve`）在后面跑，
窗口加载 `http://127.0.0.1:<port>/`。点 × 只是收进托盘，托盘里点「退出」才真的结束，而且只杀它自己拉起来的那个 python。

## 目录里都有啥

```
desktop/
├─ main.js              主进程：单实例、探测/拉起内核、无边框窗口、托盘、IPC
├─ preload.js           预加载：symbot 桥 + 顶栏拖拽区与 最小化/最大化/关闭
├─ splash.html          启动时的过渡页（打进安装包）
├─ package.json         electron / electron-builder 配置（NSIS，per-user）
├─ pnpm-workspace.yaml  pnpm 11 的构建脚本白名单（允许 electron 跑 postinstall）
├─ assets/
│  ├─ icon.png          托盘 / 窗口图标（来源见下）
│  └─ icon.ico          安装包与 exe 图标（多尺寸）
└─ README.md
```

## 怎么跑起来

先凑齐前置：Windows + Node 20 以上 + pnpm，仓库根还得有个装好依赖的 `.venv`（`requirements.txt` 全装上）。

```powershell
pnpm -C desktop install     # 第一次会下 Electron 二进制，慢，去泡杯茶
pnpm -C desktop start       # = electron .
```

起来之后它是这么走的：先探 `http://127.0.0.1:8787/v1/health`，已经开着就直接复用，不重复起进程；
没开才 `spawn(python, ["-m","app.cli","serve","--port",port])`，最多等 45 秒。超时就弹窗，顺手把日志路径指给你。

三个环境变量：

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `SYMBOT_PORT` | 内核端口 | `8787` |
| `SYMBOT_PYTHON` | 指定 python.exe（venv 不存在时才生效） | 空，退到 PATH 里的 `python` |
| `SYMBOT_ROOT` | 指定仓库根（打包后找不到 `app/main.py` 时用） | 开发模式 = `desktop` 的上一级 |

解释器的挑法是有顺序的：`<仓库>\.venv\Scripts\python.exe` → `SYMBOT_PYTHON` → `python`。
内核日志写在 `<仓库>\data\desktop.log`，目录不在会自动建。

## 怎么打包

```powershell
pnpm -C desktop build        # electron-builder --win nsis
pnpm -C desktop pack         # 只出未打包目录 dist/win-unpacked，排障用
```

装好的包在 `desktop/dist/SymBot Setup 0.2.0.exe`（版本号跟 `package.json` 的 `version` 走，实际文件名以 `dist/` 里出来的为准）。

`package.json` 里定死的东西：

- `appId: com.symbot.desktop`，`productName: SymBot`，`asar: true`；
- `directories.output: dist`，`directories.buildResources: assets`；
- `win.target`：`nsis`，`arch: x64`，`icon: assets/icon.ico`；
- NSIS：`oneClick: false`、`perMachine: false`、`allowToChangeInstallationDirectory: true`、
  `createDesktopShortcut: true`、`shortcutName: "SymBot 衡忆"`；
- `files` 白名单就这几项：`main.js`、`preload.js`、`package.json`、`assets/**/*`、`splash.html`。
  **`app/` 和 `.venv` 都不在里面**；
- `extraResources` 配了一条 `resources` → `resources`。这个目录现在还不存在，放着不影响构建。

因为是 **per-user 安装**：默认装到 `%LOCALAPPDATA%\Programs\SymBot`，只写 HKCU，不碰 HKLM，不要管理员权限。
安装包没签名（自用包），第一次运行可能被 SmartScreen 或杀毒软件拦一下，放行就过去了。

壳里没有内核，所以装完还得让它知道内核在哪：exe 同级（或往上三层内）能找到 `app\main.py` 就直接用，找不到就用 `SYMBOT_ROOT` 指过去。
想把内核一起塞进安装包，那是下一步的事（PyInstaller 出个 python 侧 exe 再放进 `extraResources`），不在 P1-3 范围内。

## 托盘和 IPC

- 点 × 不是退出，是藏进托盘。托盘双击、或者菜单里「打开面板」，就回来了。
- 托盘菜单：打开面板 / 重启内核 / 查看日志 / 退出。
- 退出时只杀自己 spawn 的那个内核（`taskkill /pid <pid> /T /F` 整棵树）。要是启动时复用的是别人已经开着的内核，一根手指都不碰。
- 渲染进程能用的桥就四个（`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`）：
  `window.symbot.status()`、`.restart()`、`.logPath()`、`.openPanel()`。
  没有任意命令执行，别指望它当终端用。
- 重启先请内核自己来：`POST /v1/console/service/action {target:"kernel",action:"restart"}`。
  接口不在或者失败了，就在本地杀掉自派生的进程再起一个。

## 踩过的坑

**端口被占 / 页面打不开**
先看 `http://127.0.0.1:8787/v1/health` 能不能开。能开就是内核已经在跑了，壳会直接复用，这不是 bug。
真想换端口：`$env:SYMBOT_PORT="8788"; pnpm -C desktop start`（面板地址跟着变）。

**python 找不到 / 内核 45 秒没起来**
看 `<仓库>\.venv\Scripts\python.exe` 在不在。不在就建 venv 装依赖；或者用 `SYMBOT_PYTHON` 指个解释器过去。
弹窗点「查看日志」，`data\desktop.log` 里有真报错。

**杀毒软件拦**
Electron 第一次跑会解压 `node_modules\electron\dist`，NSIS 包又没签名，都可能被拦。加个信任目录再来一次。

**打包卡在下载**
electron-builder 还得下 Electron / NSIS / winCodeSign，国内直连 GitHub 容易卡住。挂镜像再 build：

```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
```

**关窗之后进程还在**
正常。窗口关了，内核还在托盘里跑。托盘右键「退出」才结束。想确认就去任务管理器看 `python.exe` 还在不在。

## 图标哪来的

`icon.png` 是直接从 `panel/assets/whale-logo-256.png` 复制过来的；`icon.ico` 拿它转的，
出了 16/24/32/48/64/128/256 多尺寸（用的 `.venv` 里的 Pillow，没引新依赖）。
LOGO 版权说明在 `panel/assets/README.md`：原作者所有，本项目自用不商用。
