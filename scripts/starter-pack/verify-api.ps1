# ai-life API self-check for the tester package.
# ASCII-only output on purpose: works with Windows PowerShell 5.1 on any locale.
# Starts a temporary kernel on port 8799 with an isolated data dir, checks every
# endpoint, then stops it. It never touches the tester's own data on port 8787.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSCommandPath
$exe = Join-Path $root 'ai-life\ai-life.exe'
if (-not (Test-Path $exe)) {
  Write-Host '[FAIL] ai-life\ai-life.exe not found. Unzip the whole package first.'
  exit 1
}

$port = 8799
$base = "http://127.0.0.1:$port"
$tmpDir = Join-Path $env:TEMP ('ai-life-verify-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$oldDb = $env:AI_LIFE_DB
$oldMock = $env:AI_LIFE_MOCK
$proc = $null

function Get-Endpoint([string]$path) {
  try {
    $r = Invoke-WebRequest -Uri ($base + $path) -UseBasicParsing -TimeoutSec 20
    return [pscustomobject]@{ Code = [int]$r.StatusCode; Body = $r.Content }
  } catch {
    return [pscustomobject]@{ Code = 0; Body = $_.Exception.Message }
  }
}

try {
  $probe = Get-Endpoint '/v1/health'
  if ($probe.Code -ne 200) {
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    # 只隔离数据库：config.local.yaml（内置 Key）仍然从 ai-life 目录读取
    $env:AI_LIFE_DB = Join-Path $tmpDir 'verify.db'
    Remove-Item Env:\AI_LIFE_MOCK -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $exe -ArgumentList @('--no-gui', '--port', "$port") -PassThru -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 600
      if ($proc.HasExited) { break }
      $probe = Get-Endpoint '/v1/health'
      if ($probe.Code -eq 200) { break }
    }
  }

  if ($probe.Code -ne 200) {
    Write-Host "[FAIL] kernel did not start on port $port"
    Write-Host "       log: $(Join-Path $root 'ai-life\data\app.log')"
    exit 1
  }

  $checks = @(
    @{ Path = '/'; Name = 'chat panel' },
    @{ Path = '/settings.html'; Name = 'settings page' },
    @{ Path = '/v1/docs'; Name = 'openapi docs' },
    @{ Path = '/v1/health'; Name = 'health' },
    @{ Path = '/v1/board'; Name = 'board' },
    @{ Path = '/v1/persona'; Name = 'persona' },
    @{ Path = '/v1/settings/models'; Name = 'model settings' },
    @{ Path = '/v1/schedule'; Name = 'schedule' },
    @{ Path = '/v1/diary'; Name = 'diary' },
    @{ Path = '/v1/soul/snapshots'; Name = 'soul snapshots' }
  )

  $lines = New-Object System.Collections.ArrayList
  $fail = 0
  foreach ($c in $checks) {
    $r = Get-Endpoint $c.Path
    $ok = ($r.Code -eq 200)
    if (-not $ok) { $fail++ }
    $tag = if ($ok) { 'PASS' } else { 'FAIL' }
    [void]$lines.Add(('{0}  GET  {1,-22} {2}' -f $tag, $c.Path, $c.Name))
  }

  $health = $null
  try {
    $health = (Invoke-WebRequest -Uri "$base/v1/health" -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json
  } catch { }
  $providerOk = [bool]($health -and $health.provider -eq 'openai')
  if (-not $providerOk) { $fail++ }
  $tag = if ($providerOk) { 'PASS' } else { 'FAIL' }
  $name = if ($health) { $health.provider } else { '?' }
  [void]$lines.Add(('{0}  provider={1} (expected openai: built-in key loaded)' -f $tag, $name))

  $chatOk = $false
  try {
    $r = Invoke-WebRequest -Uri "$base/v1/chat" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes('{"text":"hello"}')) -UseBasicParsing -TimeoutSec 90
    $data = $r.Content | ConvertFrom-Json
    $chatOk = [bool]($r.StatusCode -eq 200 -and $data.reply -and $data.reply.Count -gt 0)
  } catch { }
  if (-not $chatOk) { $fail++ }
  $tag = if ($chatOk) { 'PASS' } else { 'FAIL' }
  [void]$lines.Add(('{0}  POST /v1/chat             dialog endpoint' -f $tag))

  foreach ($line in $lines) { Write-Host $line }
  Write-Host ''
  $total = $lines.Count
  if ($fail -eq 0) {
    Write-Host "RESULT: all API checks passed ($total/$total)."
    Write-Host 'Next: double-click the launcher bat in this folder.'
    exit 0
  }
  Write-Host "RESULT: $fail check(s) failed. See ai-life\data\app.log for details."
  exit 1
} finally {
  if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
  if ($null -eq $oldDb) {
    Remove-Item Env:\AI_LIFE_DB -ErrorAction SilentlyContinue
  } else {
    $env:AI_LIFE_DB = $oldDb
  }
  if ($null -ne $oldMock) { $env:AI_LIFE_MOCK = $oldMock }
  if (Test-Path $tmpDir) {
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}
