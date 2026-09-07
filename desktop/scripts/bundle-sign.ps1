#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$CertificateThumbprint,
    [Parameter(Mandatory)][string]$ExpectedHostPath,
    [Parameter(Mandatory)][string]$HostRecordPath,
    [Parameter(Mandatory)][string]$BackendDirectory,
    [Parameter(Mandatory)][string]$BackendManifestPath
)
. (Join-Path $PSScriptRoot 'package-common.ps1')

if (-not $IsWindows) { throw 'The bundle signing callback requires Windows.' }
$CertificateThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
if ($CertificateThumbprint -cnotmatch '^[A-F0-9]{40}$') { throw 'Invalid signing certificate thumbprint.' }
$signingPath = Get-PackageFullPath $Path
$expectedHost = Get-PackageFullPath $ExpectedHostPath
$hostRecord = Get-PackageFullPath $HostRecordPath
$backend = Get-PackageFullPath $BackendDirectory
$backendManifest = Get-PackageFullPath $BackendManifestPath
foreach ($file in @($signingPath, $expectedHost, $hostRecord, $backend, $backendManifest)) {
    Assert-PackageNoReparse $file
}
if (-not [IO.File]::Exists($signingPath)) { throw "Signing input must be an existing regular file: $signingPath" }
if (-not [IO.Directory]::Exists($backend)) { throw "Missing backend directory: $backend" }
Assert-PackageDisjoint $expectedHost $backend
Assert-PackageDisjoint $backendManifest $backend
foreach ($inputPath in @($signingPath, $expectedHost, $backend, $backendManifest)) {
    Assert-PackageDisjoint $hostRecord $inputPath
}

function Get-BundleFileFingerprint {
    param([Parameter(Mandatory)][string]$File)
    Assert-PackageNoReparse $File
    return [ordered]@{
        length = [IO.FileInfo]::new($File).Length
        sha256 = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Get-NsisHostFingerprint {
    param([Parameter(Mandatory)][string]$File)
    Assert-PackageNoReparse $File
    $bytes = [IO.File]::ReadAllBytes($File)
    if ($bytes.Length -lt 2 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw 'Expected an NSIS host PE/MZ executable.' }
    $image = [Text.Encoding]::ASCII.GetString($bytes)
    $prefix = '__TAURI_BUNDLE_TYPE_VAR_'
    $marker = $prefix + 'NSS'
    $index = $image.IndexOf($prefix, [StringComparison]::Ordinal)
    if ($index -lt 0 -or $image.LastIndexOf($prefix, [StringComparison]::Ordinal) -ne $index -or
        $image.IndexOf($marker, [StringComparison]::Ordinal) -ne $index) {
        throw 'Expected exactly one __TAURI_BUNDLE_TYPE_VAR_NSS host marker.'
    }
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    return [ordered]@{ length = [long]$bytes.Length; sha256 = $hash }
}

if ($signingPath.StartsWith($backend + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    # Tauri calls its signer for unsigned resource EXEs/DLLs. Their bytes were
    # frozen before backend-manifest.json was written; this callback preserves
    # them instead of giving third-party files a new signature or a new hash.
    Assert-PackageChildPath $signingPath $backend
    $relative = [IO.Path]::GetRelativePath($backend, $signingPath).Replace('\', '/')
    Assert-PackageRelativePath $relative
    $manifest = Read-PackageJson $backendManifest
    Assert-PackageManifestHeader $manifest 'chaoxing-backend' (Get-PackageVersion) 'chaoxing-backend.exe'
    if (-not $manifest.Contains('files') -or $manifest.files -isnot [Collections.IList]) { throw 'Backend manifest files must be an array.' }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $expected = $null
    foreach ($entry in $manifest.files) {
        if ($entry -isnot [Collections.IDictionary] -or -not $entry.Contains('path') -or $entry.path -isnot [string] -or
            -not $entry.Contains('length') -or ($entry.length -isnot [long] -and $entry.length -isnot [int]) -or $entry.length -lt 0 -or
            -not $entry.Contains('sha256') -or $entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Invalid backend manifest file record.'
        }
        Assert-PackageRelativePath $entry.path
        if (-not $names.Add($entry.path)) { throw "Duplicate backend manifest path: $($entry.path)" }
        if ($entry.path -ceq $relative) { $expected = $entry }
    }
    if ($null -eq $expected) { throw "Signing resource is not in the backend manifest: $relative" }
    $before = Get-BundleFileFingerprint $signingPath
    if ($before.length -ne $expected.length -or $before.sha256 -cne $expected.sha256) {
        throw "Backend resource length/hash mismatch: $relative"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $signingPath
    if ($signature.Status -notin @('Valid', 'NotSigned')) {
        throw "Invalid existing backend resource signature: $relative ($($signature.Status))"
    }
    $after = Get-BundleFileFingerprint $signingPath
    if ($after.length -ne $before.length -or $after.sha256 -cne $before.sha256) { throw "Backend resource changed during verification: $relative" }
    [ordered]@{
        action = 'preserved-backend-resource'; sourcePath = $signingPath; path = $relative
        length = $after.length; sha256 = $after.sha256; signatureStatus = [string]$signature.Status
        signerThumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
    } | ConvertTo-Json -Depth 4
    return
}

$isHost = $signingPath.Equals($expectedHost, [StringComparison]::OrdinalIgnoreCase)
if ($isHost) {
    # Build supplies an already-created directory unique to this invocation.
    # Refuse an old record before signing, then publish with a no-overwrite move.
    $recordDirectory = [IO.Path]::GetDirectoryName($hostRecord)
    Assert-PackageMutableDirectory $recordDirectory
    if (-not [IO.Directory]::Exists($recordDirectory)) { throw 'Host record requires an existing unique capture directory.' }
    if (Test-Path -LiteralPath $hostRecord) { throw "Refusing to overwrite an existing host record: $hostRecord" }
    $preSign = Get-NsisHostFingerprint $signingPath
}

$signScript = Join-Path $PSScriptRoot 'sign-windows.ps1'
& $signScript -Mode Sign -CertificateThumbprint $CertificateThumbprint -Path $signingPath | Out-Null
$verificationJson = & $signScript -Mode Verify -CertificateThumbprint $CertificateThumbprint -Path $signingPath
$verification = ConvertFrom-Json -InputObject ($verificationJson -join "`n") -Depth 8
if (@($verification.files).Count -ne 1 -or $verification.files[0].status -cne 'Valid' -or
    $verification.files[0].signerThumbprint -ine $CertificateThumbprint) {
    throw 'Signing callback did not obtain one valid signature from the selected certificate.'
}

if ($isHost) {
    $signed = Get-NsisHostFingerprint $signingPath
    if ($signed.sha256 -cne $verification.files[0].sha256) { throw 'NSIS host changed after signature verification.' }
    $record = [ordered]@{
        schemaVersion = 1; kind = 'chaoxing-gui-tauri-signed-nsis-host'; sourcePath = $signingPath
        bundleType = 'nsis'; preSignSha256 = $preSign.sha256; preSignLength = $preSign.length
        length = $signed.length; sha256 = $signed.sha256; signerThumbprint = $verification.files[0].signerThumbprint
    }
    $temporaryRecord = Join-Path $recordDirectory ".host-record-$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        Assert-PackageChildPath $temporaryRecord $recordDirectory
        $stream = [IO.File]::Open($temporaryRecord, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-Json -InputObject $record -Depth 4) + "`n")
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally { $stream.Dispose() }
        Assert-PackageChildPath $hostRecord $recordDirectory
        [IO.File]::Move($temporaryRecord, $hostRecord)
    } finally {
        if ([IO.File]::Exists($temporaryRecord)) {
            Assert-PackageChildPath $temporaryRecord $recordDirectory
            [IO.File]::Delete($temporaryRecord)
        }
    }
    $record | ConvertTo-Json -Depth 4
} else {
    Write-Output $verificationJson
}
