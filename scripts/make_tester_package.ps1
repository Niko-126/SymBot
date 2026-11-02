# 生成「内部测试分发包」：带依赖的运行目录 + 内置 API Key + 接口自检脚本
#
# Key 只作为参数传入，永远不写进任何会提交的文件；它只落在 dist\ 分发包里。
#
# 用法（在仓库根目录）：
#   .\scripts\make_tester_package.ps1 -ApiKey 'sk-...'
#   .\scripts\make_tester_package.ps1 -ApiKey 'sk-...' -SkipBuild          # 用现有 dist\ai-life
#   .\scripts\make_tester_package.ps1 -ApiKey 'sk-...' -BaseUrl 'https://api.siliconflow.cn/v1' -Model '...'
#
# 产物：
#   dist\分发-ai-life-<版本>\               分发包目录（ai-life\ + 使用说明 + 启动.bat + 验证接口.bat）
#   dist\ai-life-<版本>-内部测试包-win64.zip
#
# 分发包里 ai-life\config.local.yaml 含内置测试 Key；仓库里没有它（.gitignore 已忽略）。

param(
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$BaseUrl = 'https://api.siliconflow.cn/v1',
    [string]$Model = 'deepseek-ai/DeepSeek-V4-Flash',
    [string]$SmallModel = 'Qwen/Qwen3-30B-A3B-Instruct-2507',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = 'utf-8'

if (-not $ApiKey.StartsWith('sk-') -or $ApiKey.Length -lt 20) {
    throw 'ApiKey 看起来不像 sk- 开头的密钥，已中止'
}

$py = Join-Path $root '.venv\Scripts\python.exe'
$version = (& $py -c "import app; print(app.__version__)").Trim()

if (-not $SkipBuild) {
    Write-Output '-- 重新打包（release.ps1 -SkipTests）--'
    & (Join-Path $root 'scripts\release.ps1') -SkipTests
}

$appSrc = Join-Path $root 'dist\ai-life'
$exe = Join-Path $appSrc 'ai-life.exe'
if (-not (Test-Path $exe)) { throw '没有 dist\ai-life\ai-life.exe，先跑 scripts\release.ps1' }

$stage = Join-Path $root "dist\分发-ai-life-$version"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

Write-Output '-- 复制运行目录（含 Python 运行时与全部依赖）--'
Copy-Item -LiteralPath $appSrc -Destination (Join-Path $stage 'ai-life') -Recurse
$stageApp = Join-Path $stage 'ai-life'
foreach ($junk in @('data', 'exports', 'Girl-memory', 'persona-active.yaml')) {
    $p = Join-Path $stageApp $junk
    if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}

Write-Output '-- 写入内置 Key（只进分发包）--'
$cfg = @(
    'provider:'
    ("  base_url: `"$BaseUrl`"")
    ("  api_key: `"$ApiKey`"")
    ("  model: `"$Model`"")
    ("  small_model: `"$SmallModel`"")
    ''
) -join "`n"
Set-Content -LiteralPath (Join-Path $stageApp 'config.local.yaml') -Value $cfg -Encoding utf8 -NoNewline

Write-Output '-- 复制启动与接口自检文件 --'
$pack = Join-Path $root 'scripts\starter-pack'
foreach ($f in @('使用说明.txt', '启动.bat', '验证接口.bat', 'verify-api.ps1')) {
    Copy-Item -LiteralPath (Join-Path $pack $f) -Destination (Join-Path $stage $f) -Force
}

$exeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $exe).Hash
$meta = @(
    "ai-life v$version · 内部测试分发包（Windows x64）",
    "built: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    'runtime: onedir，依赖已全部打包，测试机不需要装 Python',
    'api base: http://127.0.0.1:8787',
    "exe sha256: $exeHash",
    '',
    '内置测试 Key 已写入 ai-life/config.local.yaml，仅限内部开发组使用；',
    '测试结束后请统一吊销该 Key 并重新打包（旧包会同时失效）。'
)
Set-Content -LiteralPath (Join-Path $stage 'RELEASE-测试.txt') -Value $meta -Encoding utf8

$zip = Join-Path $root "dist\ai-life-$version-内部测试包-win64.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Write-Output '-- 压缩 --'
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
$zipSize = [math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Output ''
Write-Output "分发包目录：$stage"
Write-Output "压缩包：$zip"
Write-Output "大小：$zipSize MB"
Write-Output "zip sha256：$zipHash"
Write-Output "exe sha256：$exeHash"
Write-Output ''
Write-Output '建议在分发前跑： 分发包目录\verify-api.ps1（或双击 验证接口.bat）'
