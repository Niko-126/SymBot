# ai-life 一键启动（NapCat 小号 + 内核）
# 用法：双击桌面快捷方式「启动小鲸.bat」
# 作用：
#   1. 以管理员权限用快速登录拉起 NapCat（已登录过的小号 123456789）
#   2. 等 3000 端口就绪
#   3. 用鲸鱼娘人设启动 ai-life 内核（8787）
param(
    [string]$QQ = "123456789",
    [int]$KernelPort = 8787
)

$ErrorActionPreference = 'Continue'
if (-not $env:NAPCAT_DIR) { $env:NAPCAT_DIR = 'C:\path\to\NapCat-Shell' }
$napcat = Join-Path $env:NAPCAT_DIR 'launcher.bat'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'

function Test-Port([int]$port) {
    return (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction SilentlyContinue)
}

Write-Host ''
Write-Host '=== ai-life 启动器 ===' -ForegroundColor Cyan

# ---------- 1. NapCat ----------
if (Test-Port 3000) {
    Write-Host '[1/3] NapCat 已在运行（3000 端口通），跳过登录' -ForegroundColor Green
} else {
    Write-Host '[1/3] 拉起 NapCat（需要管理员授权，会弹 UAC）...' -ForegroundColor Yellow
    # 先清掉可能残留的 QQ 进程，否则注入失败
    Get-Process -Name QQ, NapCatWinBootMain -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2

    $launcher = "`"$napcat`" $QQ"
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $launcher -WorkingDirectory (Split-Path $napcat) -Verb RunAs -WindowStyle Minimized
    Write-Host '      等待 NapCat 登录并开 3000 端口（最多 90 秒）...' -ForegroundColor Yellow
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port 3000) { $ok = $true; break }
    }
    if ($ok) {
        Write-Host '      NapCat OK：127.0.0.1:3000 已就绪' -ForegroundColor Green
    } else {
        Write-Host '      [失败] 3000 端口没起来。检查 NapCat 控制台，或手动跑：' -ForegroundColor Red
        Write-Host "      $napcat $QQ" -ForegroundColor Red
        Write-Host '      按任意键退出...'
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
        exit 1
    }
}

# ---------- 2. 内核 ----------
if (Test-Port $KernelPort) {
    Write-Host "[2/3] 内核已在运行（$KernelPort 端口通），跳过启动" -ForegroundColor Green
} else {
    Write-Host "[2/3] 启动内核（端口 $KernelPort，人设 whale-chan）..." -ForegroundColor Yellow
    $env:AI_LIFE_PERSONA = 'persona/whale-chan.yaml'
    $log = Join-Path $env:TEMP 'ai-life-kernel.log'
    $err = Join-Path $env:TEMP 'ai-life-kernel.err'
    Start-Process -FilePath $python -ArgumentList '-m', 'app.cli', 'serve', '--port', "$KernelPort" `
        -WorkingDirectory $project -WindowStyle Hidden `
        -RedirectStandardOutput $log -RedirectStandardError $err
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port $KernelPort) { $ok = $true; break }
    }
    if ($ok) {
        Write-Host "      内核 OK：http://127.0.0.1:$KernelPort/" -ForegroundColor Green
    } else {
        Write-Host '      [失败] 内核没起来，看日志：' -ForegroundColor Red
        Write-Host "      $log" -ForegroundColor Red
        Write-Host "      $err" -ForegroundColor Red
        $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
        exit 1
    }
}

# ---------- 3. 状态 ----------
Write-Host '[3/3] 检查链路...' -ForegroundColor Yellow
try {
    $h = Invoke-RestMethod "http://127.0.0.1:$KernelPort/v1/health" -TimeoutSec 10
    $q = Invoke-RestMethod 'http://127.0.0.1:3000/get_login_info' -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
    Write-Host ''
    Write-Host '  ===== 就绪 =====' -ForegroundColor Cyan
    Write-Host ("  小号    : {0} ({1})" -f $q.data.user_id, $q.data.nickname) -ForegroundColor Green
    Write-Host ("  人设    : {0}" -f $h.persona) -ForegroundColor Green
    Write-Host ("  模型    : {0}" -f $h.active_model) -ForegroundColor Green
    Write-Host ("  内核    : http://127.0.0.1:{0}/" -f $KernelPort) -ForegroundColor Green
    Write-Host ("  面板    : http://127.0.0.1:{0}/settings.html" -f $KernelPort) -ForegroundColor Green
    Write-Host ''
    Write-Host '  现在去 QQ 里私聊她，或在群里 @她 / 喊「小鲸鱼」「大肥鱼」。' -ForegroundColor White
    Write-Host '  关闭本窗口不会停止服务；要停就在任务管理器结束 QQ 和 python。' -ForegroundColor DarkGray
} catch {
    Write-Host ("  [警告] 检查失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
}
Write-Host ''
Write-Host '按任意键关闭本窗口...' -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
