#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][Alias('ZipPath')][string]$PackagePath,
    [string]$Version,
    [string]$ArtifactManifestPath
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$archivePath = Get-PackageFullPath $PackagePath
Assert-PackageNoReparse $archivePath
$artifactName = "chaoxing-gui-tauri-portable-$packageVersion-windows-x64.zip"
if ([IO.Path]::GetFileName($archivePath) -cne $artifactName -or -not [IO.File]::Exists($archivePath)) { throw "Missing or incorrectly named package artifact: $artifactName" }
if (-not $ArtifactManifestPath) { $ArtifactManifestPath = "$archivePath.manifest.json" }
$outer = Read-PackageJson (Get-PackageFullPath $ArtifactManifestPath)
Assert-PackageManifestHeader $outer 'chaoxing-gui-tauri-artifact' $packageVersion
if (-not $outer.Contains('artifact') -or $outer.artifact -isnot [Collections.IDictionary] -or
    -not $outer.artifact.Contains('path') -or $outer.artifact.path -cne $artifactName -or
    -not $outer.artifact.Contains('length') -or $outer.artifact.length -ne [IO.FileInfo]::new($archivePath).Length -or
    -not $outer.artifact.Contains('sha256') -or $outer.artifact.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid artifact manifest path/length/hash.' }
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -cne $outer.artifact.sha256) { throw 'Artifact SHA256 checksum mismatch.' }
Assert-PackageNoReparse "$archivePath.sha256"
if (-not [IO.File]::Exists("$archivePath.sha256") -or [IO.File]::ReadAllText("$archivePath.sha256").TrimEnd([char[]]"`r`n") -cne "$archiveHash  $artifactName") {
    throw 'Missing or invalid artifact .sha256 checksum file.'
}

function Read-ZipManifest {
    param([Parameter(Mandatory)]$Archive, [Parameter(Mandatory)][string]$Name)
    $entry = $Archive.GetEntry($Name)
    if ($null -eq $entry -or $entry.Length -gt 16MB) { throw "Missing or oversized ZIP manifest: $Name" }
    $reader = [IO.StreamReader]::new($entry.Open(), [Text.Encoding]::UTF8)
    try {
        try { return ConvertFrom-Json -InputObject $reader.ReadToEnd() -AsHashtable -Depth 20 }
        catch { throw "Invalid ZIP manifest '$Name': $($_.Exception.Message)" }
    } finally { $reader.Dispose() }
}

$zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $files = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    $directories = [Collections.Generic.List[string]]::new()
    $rootFiles = @('chaoxing-gui-tauri.exe', 'backend-manifest.json', 'package-manifest.json', 'Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'Portable-Common.ps1', 'README.txt', 'LICENSE', 'TAURI-LICENSE.txt')
    [long]$totalLength = 0
    if ($zip.Entries.Count -gt 100000) { throw 'Package has too many payload entries.' }
    foreach ($entry in $zip.Entries) {
        $directory = $entry.FullName.EndsWith('/')
        $relative = if ($directory) { $entry.FullName.Substring(0, $entry.FullName.Length - 1) } else { $entry.FullName }
        Assert-PackageRelativePath $relative
        if (-not $names.Add($relative)) { throw "Duplicate/colliding ZIP path: $relative" }
        if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000 -or ($entry.ExternalAttributes -band 0x400)) {
            throw "Unsafe symbolic link/reparse ZIP path: $relative"
        }
        if ($directory) {
            if ($entry.Length -ne 0 -or ($relative -cne 'backend' -and -not $relative.StartsWith('backend/', [StringComparison]::Ordinal))) { throw "Invalid payload directory: $relative" }
            $directories.Add($relative)
            continue
        }
        if (-not $relative.StartsWith('backend/', [StringComparison]::Ordinal) -and $relative -cnotin $rootFiles) { throw "Unexpected root payload file: $relative" }
        $totalLength += $entry.Length
        if ($entry.Length -gt 2GB -or $totalLength -gt 8GB) { throw "Oversized payload: $relative" }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $hash = [Convert]::ToHexString($hasher.ComputeHash($stream)).ToLowerInvariant() }
        finally { $hasher.Dispose(); $stream.Dispose() }
        $files.Add($relative, [ordered]@{ path = $relative; length = $entry.Length; sha256 = $hash })
    }
    foreach ($required in $rootFiles + @('backend/chaoxing-backend.exe', 'backend/_internal/web/dist/index.html')) {
        if (-not $files.ContainsKey($required) -or $files[$required].length -eq 0) { throw "Missing or empty required payload file: $required" }
    }
    foreach ($required in @('backend', 'backend/_internal', 'backend/_internal/web/dist/assets')) {
        if ($required -cnotin $directories) { throw "Missing required payload directory: $required" }
    }
    foreach ($relative in $names) {
        $parent = $relative
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
            if ($parent -cnotin $directories) { throw "Missing parent directory in ZIP: $parent" }
        }
    }
    if (-not $outer.Contains('payloadManifest') -or $outer.payloadManifest -isnot [Collections.IDictionary] -or
        -not $outer.payloadManifest.Contains('path') -or $outer.payloadManifest.path -cne 'package-manifest.json' -or
        -not $outer.payloadManifest.Contains('sha256') -or $outer.payloadManifest.sha256 -cne $files['package-manifest.json'].sha256) {
        throw 'Artifact payload manifest hash mismatch.'
    }
    $payloadManifest = Read-ZipManifest $zip 'package-manifest.json'
    Assert-PackageManifestHeader $payloadManifest 'chaoxing-gui-tauri-portable' $packageVersion 'chaoxing-gui-tauri.exe'
    if (-not $payloadManifest.Contains('platform') -or $payloadManifest.platform -cne 'windows-x64') { throw 'Manifest platform mismatch.' }
    $payloadInventory = @{ files = @($files.Values | Where-Object { $_.path -cne 'package-manifest.json' }); directories = @($directories.ToArray()) }
    Assert-PackageInventoryManifest $payloadManifest $payloadInventory
    $backendManifest = Read-ZipManifest $zip 'backend-manifest.json'
    Assert-PackageManifestHeader $backendManifest 'chaoxing-backend' $packageVersion 'chaoxing-backend.exe'
    $backendInventory = @{
        files = @(foreach ($file in $files.Values) {
            if ($file.path.StartsWith('backend/', [StringComparison]::Ordinal)) {
                @{ path = $file.path.Substring(8); length = $file.length; sha256 = $file.sha256 }
            }
        })
        directories = @(foreach ($directory in $directories) {
            if ($directory.StartsWith('backend/', [StringComparison]::Ordinal)) { $directory.Substring(8) }
        })
    }
    Assert-PackageInventoryManifest $backendManifest $backendInventory
    if (@($backendInventory.files | Where-Object { $_.path.StartsWith('_internal/web/dist/assets/', [StringComparison]::Ordinal) -and $_.length -gt 0 }).Count -eq 0) {
        throw 'Missing embedded web resources in package.'
    }
} finally { $zip.Dispose() }
Write-Output "Verified portable $packageVersion ($($files.Count) files): $archivePath"
