# 拉取第三方「肥鱼声音」服务（TTS / RVC / 唱歌）
#
# 为什么不随仓库分发：那个项目 **没有授权再分发**
#（见它自己的 NOTICE.md：「No blanket license is assigned to locally authored source」）。
# SymBot 只实现客户端（app/voice/tts.py），服务端你自己拉、自己跑。
#
# 用法：
#   .\scripts\fetch_voice_service.ps1                       # 默认拉到 .third_party\feiyu_voice
#   .\scripts\fetch_voice_service.ps1 -Dest D:\voicе-service
#   .\scripts\fetch_voice_service.ps1 -Ref main
#
# 拉完按它自己的 server\README.md 起服务（默认 http://127.0.0.1:9880），
# 然后在 config.yaml 的 voice: 段填 base_url / api_key，把 enabled 和 send_in_chat 打开。

param(
    [string]$Dest = (Join-Path (Split-Path -Parent $PSScriptRoot) ".third_party\feiyu_voice"),
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"
$repo = "https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice"

Write-Host "拉取 $repo ($Ref) → $Dest"
if (Test-Path $Dest) {
    Write-Host "目录已存在，改为 git pull"
    Push-Location $Dest
    try { git pull --ff-only } finally { Pop-Location }
} else {
    $parent = Split-Path -Parent $Dest
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    git clone --depth 1 --branch $Ref $repo $Dest
}

Write-Host ""
Write-Host "完成。下一步："
Write-Host "  1) 读 $Dest\server\README.md 起声音服务（默认 http://127.0.0.1:9880）"
Write-Host "  2) config.yaml 的 voice: 段填 base_url / api_key，enabled: true"
Write-Host "  3) 想把语音直接发进聊天，再开 send_in_chat: true（仅 OneBot 通道）"
