# SymBot 桌面壳（Electron）

把衡忆（SymBot）包成一个双击就开的 Windows 桌面程序：Electron 负责窗口与托盘，
Python 内核（`python -m app.cli serve`）在背后起，窗口加载 `http://127.0.0.1:<port>/`。
关窗只收进托盘，托盘里点「退出」才真正结束，并且只杀自己派生的那个 python。

## 目录

```
desktop/
├─ main.js              主进程：单实例、探测/拉起内核、无边框窗口、托盘、IPC
├─ preload.js           预加载：symbot 桥 + 顶栏拖拽区与 最小化/最大化/关闭
├─ package.json         electron / electron-builder 配置（NSIS，per-user）
├─ pnpm-workspace.yaml  pnpm 11 的构建脚本白名单（允许 electron 跑 postinstall）
├─ assets/
│  ├─ icon.png          托盘 / 窗口图标（来源见下）
│  └─ icon.ico          安装包与 exe 图标（多尺寸）
└─ README.md
```

## 开发

前置：Windows + Node 20 以上 + pnpm + 仓库根已有 `.venv`（`requirements.txt` 装好）。

```powershell
pnpm -C desktop install     # 首次会下 Electron 二进制，慢
pnpm -C desktop start       # = electron .
```

启动顺序：先探 `http://127.0.0.1:8787/v1/health` → 已经开着就直接复用（不重复起进程）→
没开才 `spawn(python, ["-m","app.cli","serve","--port",port])`，最多等 45 秒；超时弹窗并把日志路径指给你。

环境变量：

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `SYMBOT_PORT` | 内核端口 | `8787` |
| `SYMBOT_PYTHON` | 指定 python.exe（venv 不存在时生效） | 空，退到 PATH 里的 `python` |
| `SYMBOT_ROOT` | 指定仓库根（打包后找不到 `app/main.py` 时用） | 开发模式 = `desktop` 的上一级 |

解释器优先级：`<仓库>\.venv\Scripts\python.exe` → `SYMBOT_PYTHON` → `python`。
内核日志在 `<仓库>\data\desktop.log`（目录不存在会自动建）。

## 打包

```powershell
pnpm -C desktop build        # electron-builder --win nsis
pnpm -C desktop pack         # 只出未打包目录 dist/win-unpacked，排障用
```

安装包位置：`desktop/dist/SymBot Setup 0.1.0.exe`（文件名以 `dist/` 实际产物为准）。

- `appId: com.symbot.desktop`，`productName: SymBot`，`asar: true`。
- NSIS 参数：`oneClick: false`、`perMachine: false`、`allowToChangeInstallationDirectory: true`、
  `createDesktopShortcut: true`、`shortcutName: "SymBot 衡忆"`。
- **per-user 安装**：默认装到 `%LOCALAPPDATA%\Programs\SymBot`，只写 HKCU，不碰 HKLM，不需要管理员权限。
- 安装包没签名（自用包），首次运行可能被 SmartScreen / 杀毒拦一下，放行即可。

`files` 白名单只有 `main.js` / `preload.js` / `package.json` / `assets/**`，
**不含 `app/` 和 `.venv`**。所以装出来的壳要去仓库里找内核：exe 同级（或往上三层内）能找到 `app\main.py` 就用它，
否则用 `SYMBOT_ROOT` 指过去。要把内核一起塞进安装包属于下一步（PyInstaller 出 python 侧 exe 再放进 `extraResources`），
不在 P1-3 范围。

## 托盘与 IPC

- 关窗（右上角 ×）= 隐藏到托盘；托盘双击 / 「打开面板」恢复。
- 托盘菜单：打开面板 / 重启内核 / 查看日志 / 退出。
- 退出时只杀自己 spawn 的内核（`taskkill /T /F` 整棵树）；如果启动时复用的是别人已开的内核，一根手指都不碰。
- 渲染进程能用的桥（`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`）：
  `window.symbot.status()`、`.restart()`、`.logPath()`、`.openPanel()`。
  只有这四个，没有任意命令执行。
- 重启优先请内核自己来：`POST /v1/console/service/action {target:"kernel",action:"restart"}`；
  接口不在或失败，就本地杀掉自派生的进程再起一个。

## 常见问题

**端口被占 / 打不开**
先看 `http://127.0.0.1:8787/v1/health` 能不能开；能开说明内核已经在跑，壳会直接复用。
真想换端口：`$env:SYMBOT_PORT="8788"; pnpm -C desktop start`（面板地址跟着变）。

**python 找不到 / 内核 45 秒没起来**
`<仓库>\.venv\Scripts\python.exe` 不在就先建 venv 装依赖；或用 `SYMBOT_PYTHON` 指一个解释器。
弹窗点「查看日志」，`data\desktop.log` 里有实际报错。

**杀毒拦截**
Electron 首次运行会解压 `node_modules\electron\dist`，NSIS 包未签名，都可能被拦。加信任目录后重试。

**打包卡在下载**
electron-builder 还要下 Electron / NSIS / winCodeSign，国内直连 GitHub 容易卡，可以挂镜像再 build：

```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
```

**关窗后进程还在**
正常：窗口关了，内核还在托盘里跑。托盘右键「退出」才结束；任务管理器里看 `python.exe` 是否还在。

## 图标来源

`icon.png` 直接复制 `panel/assets/whale-logo-256.png`；`icon.ico` 用它转出
16/24/32/48/64/128/256 多尺寸（用 `.venv` 里的 Pillow，没引入新依赖）。
LOGO 版权说明见 `panel/assets/README.md`：原作者所有，本项目自用不商用。
