// SymBot 桌面壳（Electron 主进程）
//
// 干三件事：
// 1. 探测 http://127.0.0.1:<port>/v1/health —— 内核已经开着就复用，绝不重复起
// 2. 没开就拉 Python 内核（.venv → SYMBOT_PYTHON → python），日志进 data/desktop.log
// 3. 无边框窗口加载本地面板，关窗只收进托盘；真退出时只杀自己派生的内核

const { app, BrowserWindow, Tray, Menu, dialog, ipcMain, shell, nativeImage } = require('electron')
const { spawn, spawnSync } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')

const DEFAULT_PORT = 8787
const PORT = Number.parseInt(process.env.SYMBOT_PORT || '', 10) || DEFAULT_PORT
const APP_URL = `http://127.0.0.1:${PORT}/`
const HEALTH_PATH = '/v1/health'
const BOOT_TIMEOUT_MS = 45_000
const ASSETS = path.join(__dirname, 'assets')

let win = null
let tray = null
let quitting = false
let kernel = null // 只有自己 spawn 的内核才记在这里；外挂内核保持 null
let kernelLogFd = null

// ---------------------------------------------------------------- 路径 / 配置

function repoRoot() {
  // 打包后 app 代码在 asar 里，内核仍靠外部仓库；SYMBOT_ROOT 可显式指定
  const candidates = []
  if (process.env.SYMBOT_ROOT) candidates.push(path.resolve(process.env.SYMBOT_ROOT))
  if (!app.isPackaged) candidates.push(path.resolve(__dirname, '..'))
  else {
    candidates.push(path.dirname(process.execPath))
    candidates.push(path.resolve(process.resourcesPath || '', '..'))
  }
  // 便携版常见摆法：exe 塞在仓库里某层子目录 —— 往上找几层，看到 app/main.py 就认
  for (const start of candidates) {
    let dir = start
    for (let depth = 0; depth < 4 && dir; depth += 1) {
      if (fs.existsSync(path.join(dir, 'app', 'main.py'))) return dir
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  }
  return candidates[0] || process.cwd()
}

function logPath() {
  return path.join(repoRoot(), 'data', 'desktop.log')
}

function logLine(text) {
  const line = `[${new Date().toISOString()}] ${text}\n`
  try {
    fs.mkdirSync(path.dirname(logPath()), { recursive: true })
    fs.appendFileSync(logPath(), line, 'utf8')
  } catch {
    /* 日志写不进去也不许拖垮启动 */
  }
  if (!app.isPackaged) process.stdout.write(line)
}

function openKernelLog() {
  if (kernelLogFd !== null) return kernelLogFd
  try {
    fs.mkdirSync(path.dirname(logPath()), { recursive: true })
    kernelLogFd = fs.openSync(logPath(), 'a')
  } catch {
    kernelLogFd = null
  }
  return kernelLogFd
}

// 内核怎么起：
// - 安装版（Electron 打包后）：用随包带的无控制台内核 resources/kernel/SymBotKernel.exe
// - 开发版：仓库 venv → SYMBOT_PYTHON → PATH 里的 python（-m app.cli serve）
function resolveKernel() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'resources', 'kernel', 'SymBotKernel.exe')
    if (fs.existsSync(exe)) {
      return { cmd: exe, args: [String(PORT)], cwd: path.dirname(app.getPath('exe')) }
    }
    logLine(`安装包里没找到内核：${exe}，回退到系统 python`)
  }
  const root = repoRoot()
  const venv = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python')
  let python = process.env.SYMBOT_PYTHON
  if (!python && fs.existsSync(venv)) python = venv
  if (!python) python = process.platform === 'win32' ? 'python' : 'python3'
  return { cmd: python, args: ['-m', 'app.cli', 'serve', '--port', String(PORT)], cwd: root }
}

// 标准版安装包会把素材放在 resources/assets_seed，首次运行释放到安装目录 data/
function seedAssets() {
  if (!app.isPackaged) return
  const seed = path.join(process.resourcesPath, 'resources', 'assets_seed')
  if (!fs.existsSync(seed)) return
  const target = path.join(path.dirname(app.getPath('exe')), 'data')
  try {
    if (fs.existsSync(target) && fs.readdirSync(target).length > 0) return
    fs.mkdirSync(target, { recursive: true })
    fs.cpSync(seed, target, { recursive: true })
    logLine(`素材已释放到 ${target}`)
  } catch (err) {
    logLine(`素材释放失败（不影响启动）：${err.message}`)
  }
}

// ---------------------------------------------------------------- 内核探测 / 拉起

function probeHealth(timeoutMs = 1500) {
  return new Promise((resolve) => {
    let settled = false
    const done = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    const req = http.get(
      { host: '127.0.0.1', port: PORT, path: HEALTH_PATH, timeout: timeoutMs },
      (res) => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', (chunk) => {
          body += chunk
          if (body.length > 256 * 1024) req.destroy()
        })
        res.on('end', () => {
          let data = null
          try {
            data = JSON.parse(body)
          } catch {
            /* 不是 JSON 也说明端口有人应答 */
          }
          done({ ok: res.statusCode === 200, status: res.statusCode, data })
        })
      },
    )
    req.on('timeout', () => req.destroy(new Error('timeout')))
    req.on('error', (err) => done({ ok: false, error: err.code || err.message }))
  })
}

function spawnKernel() {
  const k = resolveKernel()
  const fd = openKernelLog()
  const stdio = fd === null ? 'ignore' : ['ignore', fd, fd]
  logLine(`启动内核：${k.cmd} ${k.args.join(' ')}（cwd=${k.cwd}）`)
  let child
  // SYMEM_DATA 是数据根（内核会再拼 data/ 前缀），所以指到 exe 所在目录
  const appDir = path.dirname(app.getPath('exe'))
  try {
    fs.mkdirSync(path.join(appDir, 'data'), { recursive: true })
  } catch {
    /* 目录建不了就交给内核自己处理 */
  }
  try {
    child = spawn(k.cmd, k.args, {
      cwd: k.cwd,
      windowsHide: true,
      stdio,
      env: { ...process.env, SYMEM_DATA: appDir, PYTHONUTF8: '1' },
    })
  } catch (err) {
    logLine(`内核启动失败：${err.message}`)
    return null
  }
  kernel = child
  child.on('error', (err) => {
    logLine(`内核启动出错：${err.message}`)
    child.spawnFailed = true
    if (kernel === child) kernel = null
  })
  child.on('exit', (code, signal) => {
    logLine(`内核退出 code=${code} signal=${signal || ''}`)
    if (kernel === child) kernel = null
  })
  return child
}

// 只杀自己派生的内核；外挂内核（kernel === null）一根手指都不碰
function killKernel() {
  const child = kernel
  if (!child) return
  kernel = null
  if (child.exitCode !== null || child.killed) return
  const pid = child.pid
  logLine(`结束内核进程 pid=${pid}`)
  try {
    if (process.platform === 'win32' && pid) {
      // uvicorn 可能再起子进程，整棵树一起收，别在 Windows 上留僵尸 python
      spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], { windowsHide: true })
    } else {
      child.kill('SIGTERM')
    }
  } catch (err) {
    logLine(`结束内核失败：${err.message}`)
    try {
      child.kill()
    } catch {
      /* 已经没了 */
    }
  }
}

async function waitKernel(child) {
  const deadline = Date.now() + BOOT_TIMEOUT_MS
  while (Date.now() < deadline) {
    const probe = await probeHealth(1000)
    if (probe.ok) return true
    // spawn 失败（ENOENT 之类）不会给 exitCode，别傻等 45 秒
    if (child && (child.spawnFailed || child.exitCode !== null || child.killed)) return false
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

async function ensureKernel() {
  const probe = await probeHealth()
  if (probe.ok) {
    logLine(`检测到已有内核（端口 ${PORT}），直接复用，不重复起`)
    return { ok: true, reused: true }
  }
  const child = spawnKernel()
  if (!child) return { ok: false, reused: false, reason: 'spawn-failed' }
  const ok = await waitKernel(child)
  return { ok, reused: false, child }
}

async function boot() {
  const result = await ensureKernel()
  if (result.ok) return result
  logLine('内核对齐失败，弹窗提示')
  const detail = [
    `端口：${PORT}`,
    `内核：${resolveKernel().cmd}`,
    `仓库：${repoRoot()}`,
    `日志：${logPath()}`,
  ].join('\n')
  const { response } = await dialog.showMessageBox({
    type: 'error',
    title: 'SymBot 启动失败',
    message: `内核没能在 ${BOOT_TIMEOUT_MS / 1000} 秒内就绪`,
    detail: `${detail}\n\n常见原因：Python 依赖没装、端口被占用、杀毒软件拦了 python.exe`,
    buttons: ['查看日志', '退出'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  })
  if (response === 0) shell.openPath(logPath())
  return { ok: false }
}

// ---------------------------------------------------------------- 重启

function postRestart() {
  return new Promise((resolve) => {
    const body = JSON.stringify({ target: 'kernel', action: 'restart' })
    let settled = false
    const done = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    const req = http.request(
      {
        host: '127.0.0.1',
        port: PORT,
        path: '/v1/console/service/action',
        method: 'POST',
        timeout: 5000,
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        res.resume()
        res.on('end', () => done({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode }))
      },
    )
    req.on('timeout', () => req.destroy(new Error('timeout')))
    req.on('error', (err) => done({ ok: false, error: err.message }))
    req.write(body)
    req.end()
  })
}

async function restartKernel() {
  // 先请内核自己重启（接口没实现/失败就走本地杀进程重起）
  const remote = await postRestart()
  if (remote.ok) {
    logLine('已请求内核自重启')
    if (await waitKernel(kernel)) return { ok: true, via: 'remote' }
  }
  logLine(`内核自重启不可用（${remote.error || remote.status || 'no-route'}），改本地重起`)
  killKernel()
  await new Promise((resolve) => setTimeout(resolve, 600))
  const child = spawnKernel()
  if (!child) return { ok: false, via: 'local', reason: 'spawn-failed' }
  const ok = await waitKernel(child)
  return { ok, via: 'local' }
}

// ---------------------------------------------------------------- 窗口 / 托盘

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    show: false,
    frame: false, // 无边框：标题栏由 preload 注入的那一套顶栏负责，别再开 titleBarStyle
    backgroundColor: '#F2F2EF',
    title: 'SymBot 衡忆',
    icon: path.join(ASSETS, 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  })

  win.setMenuBarVisibility(false)
  win.once('ready-to-show', () => win.show())
  // 关窗 = 收进托盘，不退出
  win.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    win.hide()
  })
  win.on('closed', () => {
    win = null
  })
  win.on('page-title-updated', (event) => event.preventDefault())
  win.webContents.on('render-process-gone', (_event, details) => {
    logLine(`渲染进程挂了：${details.reason}`)
  })

  win.loadURL(APP_URL).catch((err) => logLine(`加载面板失败：${err.message}`))
}

function showPanel() {
  if (!win) {
    createWindow()
    return
  }
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(ASSETS, 'icon.png'))
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon.resize({ width: 16, height: 16 }))
  tray.setToolTip('SymBot 衡忆')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '打开面板', click: () => showPanel() },
      {
        label: '重启内核',
        click: async () => {
          const result = await restartKernel()
          logLine(`托盘重启内核：${result.ok ? '成功' : '失败'}（${result.via}）`)
          if (!result.ok) dialog.showErrorBox('重启失败', `内核没起来，先看日志：\n${logPath()}`)
        },
      },
      { label: '查看日志', click: () => shell.openPath(logPath()) },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitting = true
          app.quit()
        },
      },
    ]),
  )
  tray.on('click', () => showPanel())
  tray.on('double-click', () => showPanel())
}

// ---------------------------------------------------------------- IPC

function registerIpc() {
  ipcMain.handle('kernel:status', async () => {
    const probe = await probeHealth()
    return {
      ok: probe.ok,
      port: PORT,
      url: APP_URL,
      owned: Boolean(kernel),
      kernel: resolveKernel().cmd,
      logPath: logPath(),
      detail: probe.data || null,
      error: probe.error || null,
    }
  })

  ipcMain.handle('kernel:restart', async () => restartKernel())

  ipcMain.handle('app:log-path', async () => {
    openKernelLog()
    return logPath()
  })

  ipcMain.handle('app:open-panel', async () => {
    showPanel()
    return { ok: true, url: APP_URL }
  })

  // 无边框窗口的三种窗口操作（只做这三件事，不暴露任意命令）
  ipcMain.on('window:minimize', () => win && win.minimize())
  ipcMain.on('window:toggle-maximize', () => {
    if (!win) return
    if (win.isMaximized()) win.unmaximize()
    else win.maximize()
  })
  ipcMain.on('window:close', () => win && win.close())
}

// ---------------------------------------------------------------- 生命周期

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => showPanel())

  app.whenReady().then(async () => {
    app.setAppUserModelId('com.symbot.desktop')
    seedAssets()
    registerIpc()
    const result = await boot()
    if (!result.ok) {
      app.quit()
      return
    }
    createWindow()
    createTray()
  })

  app.on('before-quit', () => {
    // 真退出：先收尸再走，别在 Windows 上留僵尸 python
    quitting = true
    killKernel()
  })

  app.on('will-quit', () => {
    killKernel()
    if (kernelLogFd !== null) {
      try {
        fs.closeSync(kernelLogFd)
      } catch {
        /* 关不掉就算了 */
      }
      kernelLogFd = null
    }
  })

  app.on('window-all-closed', () => {
    if (quitting) app.quit()
  })

  // 异常退出也收尸，别把 python 留成孤儿
  process.on('exit', () => killKernel())
}
