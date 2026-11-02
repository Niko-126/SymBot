# 给 Releases 里的 exe 签名：证书 / 时间戳 / 校验 / 重算 SHA256SUMS
#
# 用法：
#   .\scripts\sign_release.ps1 -WhatIf                        # 只看环境，不签
#   .\scripts\sign_release.ps1 -Thumbprint ABCDEF...          # 用已安装的证书
#   .\scripts\sign_release.ps1 -Pfx .\cert.pfx -Password ***  # 用 pfx（部分 CA 仍给）
#
# 为什么用 Set-AuthenticodeSignature 而不是 signtool：
# 系统自带、不依赖 Windows SDK；签名效果与 signtool 等价（同一套 Authenticode）。
# 某些硬件令牌驱动要求走 signtool，加 -UseSignTool 即可（需要已安装 SDK）。

[CmdletBinding()]
param(
    [string]$Dir = "C:\AIGCProgram\GitHub\Releases\v0.1.0",
    [string]$Thumbprint = "",
    [string]$Pfx = "",
    [string]$PfxPassword = "",
    [string]$Timestamp = "http://timestamp.digicert.com",
    [switch]$UseSignTool,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

function Info($m) { Write-Host "  $m" }
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [ERR] $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Dir)) { Fail "找不到目录：$Dir" }
$exes = @(Get-ChildItem (Join-Path $Dir '*.exe') -File -ErrorAction SilentlyContinue)
if ($exes.Count -eq 0) { Fail "$Dir 下没有 exe" }

Write-Host "=== 代码签名 ===" -ForegroundColor Cyan
Info "目录：$Dir"
Info "待签：$($exes.Count) 个 exe"

# ── 1. 找证书 ────────────────────────────────────────────────
$cert = $null
if ($Pfx) {
    if (-not (Test-Path $Pfx)) { Fail "找不到 pfx：$Pfx" }
    $sec = ConvertTo-SecureString $PfxPassword -AsPlainText -Force
    $cert = Get-PfxCertificate -FilePath $Pfx -Password $sec
} elseif ($Thumbprint) {
    $cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
        Where-Object { $_.Thumbprint -eq ($Thumbprint -replace '\s', '') } |
        Select-Object -First 1
    if (-not $cert) { Fail "当前用户证书库找不到指纹 $Thumbprint" }
} else {
    $certs = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert)
    if ($certs.Count -eq 0) {
        Warn "本机没有代码签名证书（Cert:\CurrentUser\My）"
        Warn "买证书后 CA 会让你把令牌插上并装证书，然后重跑本脚本"
        exit 3
    }
    $cert = $certs[0]
}

$now = Get-Date
if ($cert.NotAfter -lt $now) { Fail "证书已过期：$($cert.NotAfter)" }
if ($cert.NotAfter -lt $now.AddDays(30)) { Warn "证书 $([int]($cert.NotAfter - $now).TotalDays) 天后过期" }
Info "证书：$($cert.Subject)"
Info "到期：$($cert.NotAfter.ToString('yyyy-MM-dd'))"

if ($WhatIf) {
    Warn "-WhatIf：只做环境检查，不签名"
    foreach ($e in $exes) { Info "将签：$($e.Name)  $([math]::Round($e.Length / 1MB, 1)) MB" }
    exit 0
}

# ── 2. signtool 可选路径 ─────────────────────────────────────
$signtool = $null
if ($UseSignTool) {
    $signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'x64' } | Select-Object -First 1 -ExpandProperty FullName
    if (-not $signtool) { Fail "-UseSignTool 需要 Windows SDK 的 signtool.exe，没找到" }
    Info "signtool：$signtool"
}

# ── 3. 逐个签名 ─────────────────────────────────────────────
$results = @()
foreach ($exe in $exes) {
    Write-Host "`n  ── $($exe.Name)" -ForegroundColor DarkCyan
    if ($signtool) {
        & $signtool sign /fd SHA256 /tr $Timestamp /td SHA256 /sha1 $cert.Thumbprint $exe.FullName | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "signtool 签名失败（退出码 $LASTEXITCODE）" }
    } else {
        $sig = Set-AuthenticodeSignature -FilePath $exe.FullName -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $Timestamp
        # 自签名时本机未信任根证书，状态会是 UnknownError（链未受信任）——
        # 这不是签名失败：只要签名者证书指纹对得上、状态不是 NotSigned 就算签上了。
        if ($sig.Status -eq 'NotSigned' -or -not $sig.SignerCertificate) {
            Fail "签名失败：$($sig.Status) / $($sig.StatusMessage)"
        }
        if ($sig.Status -ne 'Valid') {
            Warn "签名状态 $($sig.Status)（自签名/未信任根证书时属预期）"
        }
    }
    $check = Get-AuthenticodeSignature -FilePath $exe.FullName
    if ($check.Status -eq 'NotSigned' -or -not $check.SignerCertificate) {
        Fail "签名校验不通过：$($check.Status)"
    }
    if ($check.SignerCertificate.Thumbprint -ne $cert.Thumbprint) {
        Fail "签名者证书与预期不一致：$($check.SignerCertificate.Thumbprint)"
    }
    if ($check.Status -ne 'Valid') { Warn "校验状态 $($check.Status)（自签名未信任，属预期）" }
    $hash = (Get-FileHash $exe.FullName -Algorithm SHA256).Hash.ToLower()
    Ok "$($exe.Name)  签名有效  签名者=$($check.SignerCertificate.Subject)"
    Ok "SHA256=$hash"
    $results += [pscustomobject]@{ Name = $exe.Name; sha256 = $hash }
}

# ── 4. 重算 SHA256SUMS（签名改了哈希，旧的作废） ───────────────
$sums = Join-Path $Dir 'SHA256SUMS.txt'
if (Test-Path $sums) {
    $lines = Get-ChildItem $Dir -File | Where-Object { $_.Extension -in '.exe', '.md', '.bat' } | ForEach-Object {
        $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        "$h  $($_.Name)"
    }
    Set-Content -Path $sums -Value ($lines -join "`r`n") -Encoding UTF8
    Ok "已重写 $sums"
}

Write-Host "`n=== 完成：$($results.Count) 个 exe 已签名 ===" -ForegroundColor Green
