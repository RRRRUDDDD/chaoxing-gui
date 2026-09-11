#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$HostPath,
    [string]$BackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../release/tauri'),
    [string]$Version,
    [string]$BackendManifestPath
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$hostFile = Get-PackageFullPath $HostPath
$backend = Get-PackageFullPath $BackendDirectory
$output = Get-PackageFullPath $OutputDirectory
Assert-PackageNoReparse $hostFile
Assert-PackageMutableDirectory $output
Assert-PackageDisjoint $backend $output
if ([IO.Path]::GetFileName($hostFile) -cne 'chaoxing-gui-tauri.exe' -or -not [IO.File]::Exists($hostFile) -or [IO.FileInfo]::new($hostFile).Length -eq 0) {
    throw 'HostPath must name the built, nonempty chaoxing-gui-tauri.exe release host.'
}
if (-not $BackendManifestPath) { $BackendManifestPath = Join-Path (Split-Path -Parent $backend) 'backend-manifest.json' }
$backendManifestFile = Get-PackageFullPath $BackendManifestPath
$backendManifest = Read-PackageJson $backendManifestFile
Assert-PackageManifestHeader $backendManifest 'chaoxing-backend' $packageVersion 'chaoxing-backend.exe'
$backendInventory = Get-PackageInventory $backend
Assert-BackendLayout $backend $backendInventory
Assert-PackageInventoryManifest $backendManifest $backendInventory

$portable = Get-PackageFullPath (Join-Path $PSScriptRoot '../portable')
$portableFiles = @('Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'Portable-Common.ps1', 'README.txt')
foreach ($name in $portableFiles) {
    $file = Join-Path $portable $name
    Assert-PackageNoReparse $file
    if (-not [IO.File]::Exists($file)) { throw "Missing portable entrypoint: $name" }
}
$licenseFiles = [ordered]@{
    'LICENSE' = Get-PackageFullPath (Join-Path $PSScriptRoot '../../LICENSE')
    'TAURI-LICENSE.txt' = Get-PackageFullPath (Join-Path $PSScriptRoot '../src-tauri/windows/LICENSE_MIT')
}
foreach ($file in $licenseFiles.Values) {
    Assert-PackageNoReparse $file
    if (-not [IO.File]::Exists($file) -or [IO.FileInfo]::new($file).Length -eq 0) { throw "Missing required distribution license: $file" }
}

$artifactName = "chaoxing-gui-tauri-portable-$packageVersion-windows-x64.zip"
[void][IO.Directory]::CreateDirectory($output)
$temporary = Join-Path $output ".portable-staging-$([Guid]::NewGuid().ToString('N'))"
$payload = Join-Path $temporary 'payload'
$archivePath = Join-Path $temporary $artifactName
try {
    [void][IO.Directory]::CreateDirectory($payload)
    Copy-PackageTree -Source $backend -Destination (Join-Path $payload 'backend') -Inventory $backendInventory
    Assert-PackageInventoryManifest $backendManifest (Get-PackageInventory (Join-Path $payload 'backend'))
    [IO.File]::Copy($hostFile, (Join-Path $payload 'chaoxing-gui-tauri.exe'), $false)
    [IO.File]::Copy($backendManifestFile, (Join-Path $payload 'backend-manifest.json'), $false)
    foreach ($name in $portableFiles) { [IO.File]::Copy((Join-Path $portable $name), (Join-Path $payload $name), $false) }
    foreach ($name in $licenseFiles.Keys) { [IO.File]::Copy($licenseFiles[$name], (Join-Path $payload $name), $false) }
    $payloadInventory = Get-PackageInventory $payload
    $payloadManifest = New-PackageManifest -Kind 'chaoxing-gui-tauri-portable' -Version $packageVersion -EntryPoint 'chaoxing-gui-tauri.exe' -Inventory $payloadInventory
    $payloadManifest['platform'] = 'windows-x64'
    $payloadManifestPath = Join-Path $payload 'package-manifest.json'
    Write-PackageJson $payloadManifestPath $payloadManifest

    # Explicit entries preserve empty directories and the entire onedir hierarchy.
    $zip = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create, [Text.Encoding]::UTF8)
    try {
        foreach ($directory in $payloadInventory.directories) { [void]$zip.CreateEntry("$directory/") }
        foreach ($file in @($payloadInventory.files) + @(@{ path = 'package-manifest.json' })) {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $payload $file.path), $file.path, [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $zip.Dispose() }
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $artifactManifest = [ordered]@{
        schemaVersion = 1
        kind = 'chaoxing-gui-tauri-artifact'
        version = $packageVersion
        artifact = [ordered]@{ path = $artifactName; length = [IO.FileInfo]::new($archivePath).Length; sha256 = $archiveHash }
        payloadManifest = [ordered]@{ path = 'package-manifest.json'; sha256 = (Get-FileHash -LiteralPath $payloadManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    Write-PackageJson "$archivePath.manifest.json" $artifactManifest
    [IO.File]::WriteAllText("$archivePath.sha256", "$archiveHash  $artifactName`n", [Text.UTF8Encoding]::new($false))
    & (Join-Path $PSScriptRoot 'verify-package.ps1') -PackagePath $archivePath -Version $packageVersion
    Publish-PackageItems -AllowedParent $output -Items @(
        @{ Source = $archivePath; Destination = Join-Path $output $artifactName },
        @{ Source = "$archivePath.manifest.json"; Destination = Join-Path $output "$artifactName.manifest.json" },
        @{ Source = "$archivePath.sha256"; Destination = Join-Path $output "$artifactName.sha256" }
    )
} finally { Remove-PackagePath $temporary $output }
Write-Output "Packaged portable $packageVersion`: $(Join-Path $output $artifactName)"
