# 在桌面建「启动小鲸」快捷方式：图标用面板（人设网站）的鲸鱼 LOGO。
#
# 目标不是 bat，而是 `pythonw.exe scripts\start_panel.py`：
#   - pythonw = 双击不弹黑框；
#   - start_panel.py 会起内核、轮询 /v1/health 就绪、再用 Edge/Chrome 的
#     `--app=` 模式开一个**独立的**面板窗口（会抢到前台）；
#   - 图标的坑（2026-11-02 真机踩出来）：**不能直接指仓库里的 .ico**。
#     工作区目录带着 DSH 沙箱加的 Deny ACE / 受限 SID，资源管理器从那里
#     抽图标会被拒绝，表现就是桌面图标一片空白。所以先把图标复制到
#     %LOCALAPPDATA%\SymBot\ 再引用——换个正常目录就正常了。
#
# 用法：pwsh -File scripts\make_shortcut.ps1

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$starter = Join-Path $root 'scripts\start_panel.py'

# 优先用 BMP 帧版本（兼容性最好：小尺寸不是 PNG 压缩帧）
$srcIcon = Join-Path $root 'panel\assets\whale-logo-desktop.ico'
if (-not (Test-Path $srcIcon)) { $srcIcon = Join-Path $root 'panel\assets\whale-logo.ico' }

foreach ($p in @($pythonw, $starter, $srcIcon)) {
    if (-not (Test-Path $p)) { throw "缺文件：$p" }
}

# 图标落到"不受沙箱 ACL 影响"的目录
$iconDir = Join-Path $env:LOCALAPPDATA 'SymBot'
New-Item -ItemType Directory -Force -Path $iconDir | Out-Null
$icon = Join-Path $iconDir 'whale-logo.ico'
Copy-Item $srcIcon $icon -Force

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop '启动小鲸.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force }   # 先删再建：顺带把旧图标缓存清掉

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = '"{0}"' -f $starter
$s.WorkingDirectory = $root
$s.Description = '启动小鲸：内核 + 面板窗口（http://127.0.0.1:8787/）'
$s.IconLocation = "$icon,0"
$s.WindowStyle = 1
$s.Save()

# 让资源管理器重新读图标缓存（不加这步，桌面可能还显示旧/空白图标）
try { & ie4uinit.exe -show } catch { Write-Warning "icon cache 刷新跳过：$_" }

Write-Output ("created : {0}" -f $lnk)
$check = $sh.CreateShortcut($lnk)
Write-Output ("target  : {0}" -f $check.TargetPath)
Write-Output ("args    : {0}" -f $check.Arguments)
Write-Output ("workdir : {0}" -f $check.WorkingDirectory)
Write-Output ("icon    : {0}" -f $check.IconLocation)
Write-Output ("icon sz : {0} B" -f (Get-Item $icon).Length)
Write-Output ("lnk sz  : {0} B" -f (Get-Item $lnk).Length)
