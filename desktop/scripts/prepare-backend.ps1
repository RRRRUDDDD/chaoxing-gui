#Requires -Version 7.0
[CmdletBinding()]
param(
    [Alias('BackendDirectory')][string]$SourceDirectory = (Join-Path $PSScriptRoot '../../dist/chaoxing-backend'),
    [Alias('StagingDirectory')][string]$DestinationDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$Version
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$source = Get-PackageFullPath $SourceDirectory
$destination = Get-PackageFullPath $DestinationDirectory
Assert-PackageDisjoint $source $destination
Assert-PackageMutableDirectory $destination
Assert-PackageNoReparse $source
$parent = Split-Path -Parent $destination
Assert-PackageDisjoint $source $parent
$manifestPath = Join-Path $parent 'backend-manifest.json'
Assert-PackageNoReparse $manifestPath
if ([IO.Directory]::Exists($manifestPath)) { throw "Expected a manifest file, found directory: $manifestPath" }
if ([IO.File]::Exists($destination)) { throw "Expected a staging directory: $destination" }
if ([IO.Directory]::Exists($destination)) { $null = Get-PackageInventory -Directory $destination -SkipHash }

# Validate the complete source before creating or replacing any staging files.
$inventory = Get-PackageInventory $source
Assert-BackendLayout $source $inventory
$manifest = New-PackageManifest -Kind 'chaoxing-backend' -Version $packageVersion -EntryPoint 'chaoxing-backend.exe' -Inventory $inventory
[void][IO.Directory]::CreateDirectory($parent)
$token = [Guid]::NewGuid().ToString('N')
$temporary = Join-Path $parent ".backend-staging-$token"
$temporaryManifest = Join-Path $parent ".backend-manifest-$token.json"
try {
    Copy-PackageTree -Source $source -Destination $temporary -Inventory $inventory
    $copied = Get-PackageInventory $temporary
    Assert-PackageInventoryManifest $manifest $copied
    Assert-BackendLayout $temporary $copied
    Write-PackageJson $temporaryManifest $manifest
    # Same-volume renames preserve the previous directory and manifest on failure.
    Publish-PackageItems -AllowedParent $parent -Items @(
        @{ Source = $temporary; Destination = $destination },
        @{ Source = $temporaryManifest; Destination = $manifestPath }
    )
} finally {
    Remove-PackagePath $temporary $parent
    Remove-PackagePath $temporaryManifest $parent
}
Write-Output "Prepared backend $packageVersion ($($inventory.files.Count) files): $destination"
