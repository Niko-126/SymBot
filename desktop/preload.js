// SymBot 预加载脚本
//
// 只做两件事：
// 1. 给面板一个极小的 symbot 桥（状态 / 重启 / 日志路径 / 打开面板），不暴露任何命令执行
// 2. 给无边框窗口补一套顶栏：整条 header.topbar 可拖动，右侧挂 最小化 / 最大化 / 关闭

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('symbot', {
  status: () => ipcRenderer.invoke('kernel:status'),
  restart: () => ipcRenderer.invoke('kernel:restart'),
  logPath: () => ipcRenderer.invoke('app:log-path'),
  openPanel: () => ipcRenderer.invoke('app:open-panel'),
})

// 顶栏样式：沿用面板的纸色 / 墨色，只补拖拽区，不改面板自己的排版
const TITLEBAR_CSS = `
header.topbar { -webkit-app-region: drag; user-select: none; }
header.topbar a,
header.topbar button,
header.topbar input,
header.topbar select,
header.topbar textarea,
header.topbar [role="button"],
header.topbar [contenteditable="true"] { -webkit-app-region: no-drag; }
#symbot-winbar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 12px;
  flex: 0 0 auto;
  -webkit-app-region: no-drag;
}
#symbot-winbar button {
  width: 34px;
  height: 26px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--sv-color-ink, #0B0C0E);
  cursor: pointer;
  transition: background-color .12s ease, color .12s ease;
}
#symbot-winbar button:hover { background: rgba(11, 12, 14, .08); }
#symbot-winbar button[data-role="close"]:hover {
  background: #D64545;
  color: var(--sv-color-paper, #F2F2EF);
}
#symbot-winbar button:focus-visible {
  outline: 2px solid var(--sv-color-signal, #00E0C6);
  outline-offset: 1px;
}
#symbot-winbar svg { width: 10px; height: 10px; fill: none; stroke: currentColor; stroke-width: 1.2; }
`

const ICONS = {
  minimize: '<path d="M2 5h6"/>',
  maximize: '<rect x="2.2" y="2.2" width="5.6" height="5.6" rx="0.6"/>',
  close: '<path d="M2 2l6 6M8 2l-6 6"/>',
}

const BUTTONS = [
  { role: 'minimize', label: '最小化', channel: 'window:minimize' },
  { role: 'maximize', label: '最大化', channel: 'window:toggle-maximize' },
  { role: 'close', label: '关闭到托盘', channel: 'window:close' },
]

function buildWinbar() {
  const bar = document.createElement('div')
  bar.id = 'symbot-winbar'
  for (const item of BUTTONS) {
    const button = document.createElement('button')
    button.type = 'button'
    button.dataset.role = item.role
    button.setAttribute('aria-label', item.label)
    button.title = item.label
    button.innerHTML = `<svg viewBox="0 0 10 10" aria-hidden="true" focusable="false">${ICONS[item.role]}</svg>`
    button.addEventListener('click', () => ipcRenderer.send(item.channel))
    bar.appendChild(button)
  }
  return bar
}

function injectTitlebar() {
  if (document.getElementById('symbot-winbar')) return
  const style = document.createElement('style')
  style.id = 'symbot-titlebar-css'
  style.textContent = TITLEBAR_CSS
  document.head ? document.head.appendChild(style) : document.documentElement.appendChild(style)

  const header = document.querySelector('header.topbar')
  if (header) {
    header.appendChild(buildWinbar())
    return
  }
  // 没顶栏的兜底页（报错页 / 空壳）：贴右上角一个浮条，别让窗口关不掉
  const bar = buildWinbar()
  bar.style.position = 'fixed'
  bar.style.top = '8px'
  bar.style.right = '12px'
  bar.style.zIndex = '2147483000'
  document.body.appendChild(bar)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectTitlebar, { once: true })
} else {
  injectTitlebar()
}
