[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Sign', 'Verify')][string]$Mode = 'Inspect',
    [string]$CertificateThumbprint = $env:CHAOXING_SIGN_CERT_THUMBPRINT,
    [string[]]$Path = @(),
    [string]$ReportPath,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$now = Get-Date
$candidates = @(foreach ($store in @('Cert:\CurrentUser\My', 'Cert:\LocalMachine\My')) {
    Get-ChildItem -LiteralPath $store | Where-Object {
        $_.HasPrivateKey -and $_.NotBefore -le $now -and $_.NotAfter -gt $now -and
        (@($_.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }) -contains '1.3.6.1.5.5.7.3.3')
    }
})
$certificate = $null
if ($CertificateThumbprint) {
    $CertificateThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    if ($CertificateThumbprint -notmatch '^[A-F0-9]{40}$') { throw 'Invalid signing certificate thumbprint' }
    $certificate = $candidates | Where-Object Thumbprint -eq $CertificateThumbprint | Select-Object -First 1
    if (-not $certificate) { throw 'The requested valid code-signing certificate with private key is unavailable' }
} elseif ($candidates.Count -eq 1) {
    $certificate = $candidates[0]
    $CertificateThumbprint = $certificate.Thumbprint
} elseif ($candidates.Count -gt 1) {
    throw 'Multiple signing certificates are available; set CHAOXING_SIGN_CERT_THUMBPRINT explicitly'
}

$files = @()
if ($Mode -ne 'Inspect' -and $Path.Count -eq 0) { throw 'Sign/Verify requires at least one explicit path' }
if ($Mode -eq 'Sign') {
    if (-not $certificate) { throw 'Signing requested but no usable code-signing certificate exists' }
    $signTool = (Get-Command signtool.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    foreach ($file in $Path) {
        $resolved = (Resolve-Path -LiteralPath $file).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw "Not a signing file: $resolved" }
        $arguments = @('sign', '/sha1', $CertificateThumbprint, '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
        if ($certificate.PSPath -like '*LocalMachine*') { $arguments += '/sm' }
        & $signTool @arguments $resolved
        if ($LASTEXITCODE -ne 0) { throw "signtool signing failed with exit $LASTEXITCODE" }
        & $signTool verify /pa /all $resolved
        if ($LASTEXITCODE -ne 0) { throw "signtool verification failed with exit $LASTEXITCODE" }
    }
}
if ($Mode -ne 'Inspect') {
    $files = @(foreach ($file in $Path) {
        $resolved = (Resolve-Path -LiteralPath $file).Path
        $signature = Get-AuthenticodeSignature -LiteralPath $resolved
        $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
        if ($certificate -and ($signature.Status -ne 'Valid' -or $thumbprint -ne $CertificateThumbprint)) {
            throw "Signature does not match the selected certificate: $resolved ($($signature.Status))"
        }
        if (-not $certificate -and $signature.Status -notin @('NotSigned', 'Valid')) {
            throw "Invalid existing signature: $resolved ($($signature.Status))"
        }
        [ordered]@{
            path=$resolved; status=[string]$signature.Status; signerThumbprint=$thumbprint
            sha256=(Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
}
$report = [ordered]@{
    checkedAt=(Get-Date).ToUniversalTime().ToString('o'); mode=$Mode
    signingAvailable=[bool]$certificate; selectedThumbprint=$CertificateThumbprint
    candidateCount=$candidates.Count
    conclusion=$(if ($certificate) {
        'Selected certificate available; Sign/Verify enforces matching valid signatures'
    } elseif ($Mode -eq 'Inspect') {
        'No usable code-signing certificate supplied or found; this build cannot create new signatures'
    } elseif (@($files | Where-Object status -ne 'Valid').Count -eq 0) {
        'All inspected signatures are valid; no local signing private key is available'
    } else {
        'One or more inspected files are unsigned; no local signing private key is available (see per-file status)'
    })
    files=$files
}
$json = $report | ConvertTo-Json -Depth 6
if ($ReportPath) { [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($ReportPath), $json + "`n", [System.Text.UTF8Encoding]::new($false)) }
Write-Output $json
