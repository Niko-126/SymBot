# ai-life 发布打包：自检 → 打包 → 校验 → 出 zip 与校验和
#
# 用法（在仓库根目录）：
#   .\scripts\release.ps1              # 完整流程：smoke → 密钥自检 → PyInstaller → zip
#   .\scripts\release.ps1 -SkipTests   # 跳过 smoke（不推荐，只在已单独跑过时用）
#
# 产物：
#   dist/ai-life/                 onedir 可运行目录（双击 ai-life.exe）
#   dist/ai-life-<版本>-win64.zip  分发压缩包
#   dist/RELEASE.txt              版本、sha256、体积
#
# 不会把 config.local.yaml / data/ / exports/ / Girl-memory / persona-active.yaml 打进包；
# 发现这些出现在产物里会直接失败。

param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

$py = Join-Path $root '.venv\Scripts\python.exe'
$pyi = Join-Path $root '.venv\Scripts\pyinstaller.exe'
$version = (& $py -c "import app; print(app.__version__)").Trim()

Write-Output "== ai-life 发布打包 · v$version =="

$dirty = git status --porcelain
if ($dirty) {
    Write-Output ''
    Write-Output '注意：工作树有未提交改动，本次包会包含未提交内容。'
    Write-Output ''
}

if (-not $SkipTests) {
    Write-Output '-- smoke --'
    & $py scripts\smoke.py
    if ($LASTEXITCODE -ne 0) { throw 'smoke 未通过，停止打包' }
}

Write-Output '-- 密钥自检 --'
if (Test-Path (Join-Path $root 'config.local.yaml')) {
    Write-Output '  发现 config.local.yaml（本地 key）：不会被打进包，源码扫描跳过它。'
    & $py scripts\check_secrets.py --allow-local
} else {
    & $py scripts\check_secrets.py
}
if ($LASTEXITCODE -ne 0) { throw '密钥自检未通过，停止打包' }

Write-Output '-- 清理 vendor 的 __pycache__ --'
Get-ChildItem (Join-Path $root 'vendor') -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

Write-Output '-- PyInstaller --'
& $pyi ai-life.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败' }

$appDir = Join-Path $root 'dist\ai-life'
$exe = Join-Path $appDir 'ai-life.exe'
if (-not (Test-Path $exe)) { throw '没有生成 dist/ai-life/ai-life.exe' }

foreach ($forbidden in @('config.local.yaml', 'data', 'exports', 'Girl-memory', 'persona-active.yaml')) {
    if (Test-Path (Join-Path $appDir $forbidden)) {
        throw "打包产物里出现了不该有的东西：$forbidden"
    }
}

$exeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
$zip = Join-Path $root "dist\ai-life-$version-win64.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path $appDir -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
$zipSize = [math]::Round((Get-Item $zip).Length / 1MB, 1)

$lines = @(
    "ai-life v$version · Windows x64",
    "built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "exe: ai-life.exe  sha256=$exeHash",
    "zip: $(Split-Path $zip -Leaf)  ${zipSize} MB  sha256=$zipHash",
    "smoke: $(if ($SkipTests) { 'skipped' } else { 'passed' })",
    "",
    "启动：解压后双击 ai-life.exe。首次运行会在同目录生成 data/ 与 config.local.yaml。",
    "发布前请确认已吊销开发期测试 key（见 docs/交接-会话交接.md 第 13 节）。"
)
$releaseTxt = Join-Path $root 'dist\RELEASE.txt'
Set-Content -LiteralPath $releaseTxt -Value $lines -Encoding utf8

Write-Output ''
$lines | ForEach-Object { Write-Output $_ }
Write-Output ''
Write-Output "完成：$zip"
