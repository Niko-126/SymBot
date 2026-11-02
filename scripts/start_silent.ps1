# 小鲸一键启动（无控制台窗口）
# - 拉起 NapCat（快速登录）→ 拉起内核（隐藏窗口）
# - 全部就绪后自动打开 Web 控制台
# - 所有日志写文件，不弹黑窗
param(
    [string]$QQ = "123456789",
    [int]$Port = 8787,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
if (-not $env:NAPCAT_DIR) { $env:NAPCAT_DIR = 'C:\AIGCProgram\OtherWork\NapCat-Shell' }
$napcatDir = $env:NAPCAT_DIR
$python = Join-Path $root '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-Port([int]$p) {
    return [bool](Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue)
}

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Add-Content -Path (Join-Path $logDir 'startup.log') -Value $line -Encoding utf8
}

Log '=== 启动 ==='

# ---------- NapCat ----------
if (Test-Port 3000) {
    Log 'NapCat 已在运行'
} else {
    Log '拉起 NapCat'
    Get-Process -Name QQ, NapCatWinBootMain -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', "`"$(Join-Path $napcatDir 'launcher.bat')`" $QQ" `
        -WorkingDirectory $napcatDir -Verb RunAs -WindowStyle Minimized
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Port 3000) { Log 'NapCat OK'; break }
    }
    if (-not (Test-Port 3000)) { Log 'NapCat 未就绪'; }
}

# ---------- 内核（隐藏窗口）----------
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*app.cli*serve*--port*$Port*" }
if ($running) {
    Log '内核已在运行'
} else {
    Log '启动内核'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $python
    $psi.Arguments = "-m app.cli serve --port $Port"
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables['AI_LIFE_PERSONA'] = 'persona/whale-chan.yaml'
    [void][System.Diagnostics.Process]::Start($psi)
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port $Port) { Log '内核 OK'; break }
    }
}

# ---------- 打开控制台 ----------
if (Test-Port $Port) {
    $url = "http://127.0.0.1:$Port/console.html"
    Log "打开 $url"
    if (-not $NoBrowser) { Start-Process $url }
} else {
    Log '内核未就绪，跳过打开页面'
}
