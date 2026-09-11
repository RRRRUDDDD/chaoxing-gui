#Requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PackageVersion {
    param([string]$Version)
    $projectFile = Join-Path $PSScriptRoot '../../pyproject.toml'
    $project = [regex]::Match([IO.File]::ReadAllText($projectFile), '(?ms)^\[project\][^\S\r\n]*\r?\n(.*?)(?=^\[|\z)')
    $versions = [regex]::Matches($project.Groups[1].Value, '(?m)^version\s*=\s*"([^"]+)"\s*(?:#.*)?$')
    if ($versions.Count -ne 1) { throw 'Expected exactly one project version in pyproject.toml.' }
    $expected = $versions[0].Groups[1].Value
    if ($expected -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
        throw "Unsafe or unsupported project version: $expected"
    }
    if ($Version -and $Version -cne $expected) { throw "Version mismatch: requested '$Version', pyproject.toml is '$expected'." }
    return $expected
}

function Get-PackageFullPath {
    param([Parameter(Mandatory)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Unsafe empty path.' }
    if (-not [IO.Path]::IsPathFullyQualified($Path)) { $Path = Join-Path (Get-Location).ProviderPath $Path }
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd([char[]]'\/') -eq $root.TrimEnd([char[]]'\/')) { throw "Unsafe filesystem root: $full" }
    $relative = $full.Substring($root.Length).Replace('\', '/')
    Assert-PackageRelativePath $relative
    return $full.TrimEnd([char[]]'\/')
}

function Assert-PackageRelativePath {
    param([Parameter(Mandatory)][string]$Path)
    if ($Path -match '[\\:\x00-\x1f<>"|?*]' -or $Path.StartsWith('/') -or $Path.EndsWith('/')) {
        throw "Unsafe relative path: '$Path'"
    }
    foreach ($segment in $Path.Split('/')) {
        if ($segment -in @('', '.', '..') -or $segment -match '[. ]$' -or
            $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
            throw "Unsafe relative path: '$Path'"
        }
    }
}

function Assert-PackageNoReparse {
    param([Parameter(Mandatory)][string]$Path)
    $cursor = Get-PackageFullPath $Path
    while ($cursor) {
        try { $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { $item = $null }
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing reparse point/junction: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
    }
}

function Assert-PackageChildPath {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Parent)
    $full = Get-PackageFullPath $Path
    $parentFull = Get-PackageFullPath $Parent
    if (-not $full.StartsWith($parentFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside intended directory '$parentFull': $full"
    }
    Assert-PackageNoReparse $full
}

function Assert-PackageDisjoint {
    param([Parameter(Mandatory)][string]$First, [Parameter(Mandatory)][string]$Second)
    $left = Get-PackageFullPath $First
    $right = Get-PackageFullPath $Second
    if ($left.Equals($right, [StringComparison]::OrdinalIgnoreCase) -or
        $left.StartsWith($right + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $right.StartsWith($left + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe overlapping source/destination paths: '$left' and '$right'"
    }
}

function Assert-PackageMutableDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $full = Get-PackageFullPath $Path
    $protectedPaths = @([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..')), $env:USERPROFILE, $env:SystemRoot, [IO.Path]::GetTempPath())
    foreach ($protected in $protectedPaths) {
        if (-not $protected) { continue }
        $protected = [IO.Path]::GetFullPath($protected).TrimEnd([char[]]'\/')
        if ($full.Equals($protected, [StringComparison]::OrdinalIgnoreCase) -or
            $protected.StartsWith($full + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe protected directory: $full"
        }
    }
    Assert-PackageNoReparse $full
}

function Get-PackageInventory {
    param([Parameter(Mandatory)][string]$Directory, [switch]$SkipHash)
    $root = Get-PackageFullPath $Directory
    Assert-PackageNoReparse $root
    if (-not [IO.Directory]::Exists($root)) { throw "Missing required directory: $root" }
    $directories = [Collections.Generic.List[string]]::new()
    $filePaths = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $pending.Push([IO.DirectoryInfo]::new($root))
    while ($pending.Count) {
        foreach ($item in $pending.Pop().EnumerateFileSystemInfos()) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point/junction: $($item.FullName)" }
            $relative = [IO.Path]::GetRelativePath($root, $item.FullName).Replace('\', '/')
            Assert-PackageRelativePath $relative
            if ($item.Attributes -band [IO.FileAttributes]::Directory) {
                $directories.Add($relative)
                $pending.Push([IO.DirectoryInfo]$item)
            } else { $filePaths.Add($relative) }
        }
    }
    $directories.Sort([StringComparer]::Ordinal)
    $filePaths.Sort([StringComparer]::Ordinal)
    $files = @(foreach ($relative in $filePaths) {
        $file = Join-Path $root $relative
        [ordered]@{
            path = $relative
            length = [IO.FileInfo]::new($file).Length
            sha256 = if ($SkipHash) { $null } else { (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    })
    return [ordered]@{ files = $files; directories = @($directories.ToArray()) }
}

function Assert-BackendLayout {
    param([Parameter(Mandatory)][string]$Directory, [Parameter(Mandatory)]$Inventory)
    foreach ($required in @('chaoxing-backend.exe', '_internal/web/dist/index.html')) {
        $file = @($Inventory.files | Where-Object { $_.path -ceq $required -and $_.length -gt 0 })
        if ($file.Count -ne 1) { throw "Missing or empty required backend resource: $required" }
    }
    if ('_internal' -cnotin $Inventory.directories -or '_internal/web/dist/assets' -cnotin $Inventory.directories -or
        @($Inventory.files | Where-Object { $_.path.StartsWith('_internal/web/dist/assets/', [StringComparison]::Ordinal) -and $_.length -gt 0 }).Count -eq 0) {
        throw 'Incomplete backend: _internal and embedded web/dist/assets are required.'
    }
    $webRoot = Join-Path $Directory '_internal/web/dist'
    $html = [IO.File]::ReadAllText((Join-Path $webRoot 'index.html'))
    foreach ($match in [regex]::Matches($html, '(?i)<(?:script|link|img|source)\b[^>]*?\b(?:src|href)\s*=\s*["''](?<url>[^"''<>]+)["'']')) {
        $url = $match.Groups['url'].Value
        if ($url -match '^(?:[a-z][a-z0-9+.-]*:|//|#)') { continue }
        $relative = [Uri]::UnescapeDataString(($url -split '[?#]', 2)[0]).TrimStart('/')
        if ($relative.StartsWith('./')) { $relative = $relative.Substring(2) }
        Assert-PackageRelativePath $relative
        if (-not [IO.File]::Exists((Join-Path $webRoot $relative))) { throw "Missing referenced web resource: $relative" }
    }
}

function New-PackageManifest {
    param([Parameter(Mandatory)][string]$Kind, [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$EntryPoint, [Parameter(Mandatory)]$Inventory)
    return [ordered]@{
        schemaVersion = 1
        kind = $Kind
        version = $Version
        entryPoint = $EntryPoint
        files = @($Inventory.files)
        directories = @($Inventory.directories)
    }
}

function Write-PackageJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 12) + "`n", [Text.UTF8Encoding]::new($false))
}

function Read-PackageJson {
    param([Parameter(Mandatory)][string]$Path)
    Assert-PackageNoReparse $Path
    if (-not [IO.File]::Exists($Path)) { throw "Missing manifest: $Path" }
    if ([IO.FileInfo]::new($Path).Length -gt 16MB) { throw "Manifest is too large: $Path" }
    try { return ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Path)) -AsHashtable -Depth 20 }
    catch { throw "Invalid JSON manifest '$Path': $($_.Exception.Message)" }
}

function Assert-PackageManifestHeader {
    param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Version, [string]$EntryPoint)
    if ($Manifest -isnot [Collections.IDictionary] -or -not $Manifest.Contains('schemaVersion') -or
        ($Manifest.schemaVersion -isnot [long] -and $Manifest.schemaVersion -isnot [int]) -or
        $Manifest.schemaVersion -ne 1 -or -not $Manifest.Contains('kind') -or $Manifest.kind -cne $Kind) {
        throw "Invalid $Kind manifest schema."
    }
    if (-not $Manifest.Contains('version') -or $Manifest.version -cne $Version) { throw "Manifest version mismatch: expected $Version." }
    if ($EntryPoint -and (-not $Manifest.Contains('entryPoint') -or $Manifest.entryPoint -cne $EntryPoint)) { throw 'Manifest entry point mismatch.' }
}

function Assert-PackageInventoryManifest {
    param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)]$Inventory)
    if (-not $Manifest.Contains('files') -or $Manifest.files -isnot [Collections.IList] -or
        -not $Manifest.Contains('directories') -or $Manifest.directories -isnot [Collections.IList]) {
        throw 'Invalid manifest: files and directories must be arrays.'
    }
    $actualFiles = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    foreach ($file in $Inventory.files) { $actualFiles.Add($file.path, $file) }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $Manifest.files) {
        if ($file -isnot [Collections.IDictionary] -or -not $file.Contains('path') -or $file.path -isnot [string] -or
            -not $file.Contains('length') -or ($file.length -isnot [long] -and $file.length -isnot [int]) -or $file.length -lt 0 -or
            -not $file.Contains('sha256') -or $file.sha256 -isnot [string] -or $file.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Invalid file record in manifest.'
        }
        Assert-PackageRelativePath $file.path
        if (-not $names.Add($file.path)) { throw "Duplicate manifest path: $($file.path)" }
        if (-not $actualFiles.ContainsKey($file.path)) { throw "Missing manifest payload file: $($file.path)" }
        $actual = $actualFiles[$file.path]
        if ($actual.length -ne $file.length -or $actual.sha256 -cne $file.sha256) { throw "Payload length/hash mismatch: $($file.path)" }
    }
    if ($Manifest.files.Count -ne $Inventory.files.Count) { throw 'Manifest does not cover every payload file.' }
    $actualDirectories = [Collections.Generic.HashSet[string]]::new([string[]]$Inventory.directories, [StringComparer]::Ordinal)
    foreach ($directory in $Manifest.directories) {
        if ($directory -isnot [string]) { throw 'Invalid directory path in manifest.' }
        Assert-PackageRelativePath $directory
        if (-not $names.Add($directory)) { throw "Duplicate manifest path: $directory" }
        if (-not $actualDirectories.Contains($directory)) { throw "Missing manifest payload directory: $directory" }
    }
    if ($Manifest.directories.Count -ne $Inventory.directories.Count) { throw 'Manifest does not cover every payload directory.' }
}

function Copy-PackageTree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)]$Inventory)
    Assert-PackageDisjoint $Source $Destination
    Assert-PackageNoReparse $Source
    Assert-PackageNoReparse $Destination
    if (Test-Path -LiteralPath $Destination) { throw "Copy destination already exists: $Destination" }
    [void][IO.Directory]::CreateDirectory($Destination)
    foreach ($relative in $Inventory.directories) { [void][IO.Directory]::CreateDirectory((Join-Path $Destination $relative)) }
    foreach ($file in $Inventory.files) { [IO.File]::Copy((Join-Path $Source $file.path), (Join-Path $Destination $file.path), $false) }
}

function Remove-PackagePath {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$AllowedParent)
    $full = Get-PackageFullPath $Path
    Assert-PackageChildPath $full $AllowedParent
    if (-not (Test-Path -LiteralPath $full)) { return }
    if ([IO.Directory]::Exists($full)) {
        $null = Get-PackageInventory -Directory $full -SkipHash
        Remove-Item -LiteralPath $full -Recurse -Force
    } else { Remove-Item -LiteralPath $full -Force }
}

function Move-PackagePath {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)][string]$AllowedParent)
    $from = Get-PackageFullPath $Source
    $to = Get-PackageFullPath $Destination
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        # Recheck scope and reparse points on every attempt. Windows scanners can
        # briefly deny rename after a large onedir copy; never replace an existing
        # destination or bypass permissions to make publication succeed.
        Assert-PackageChildPath $from $AllowedParent
        Assert-PackageChildPath $to $AllowedParent
        $isDirectory = [IO.Directory]::Exists($from)
        if ($isDirectory) { $null = Get-PackageInventory -Directory $from -SkipHash }
        try {
            if ($isDirectory) { [IO.Directory]::Move($from, $to) }
            else { [IO.File]::Move($from, $to) }
            return
        } catch {
            $cause = $_.Exception
            while ($null -ne $cause.InnerException) { $cause = $cause.InnerException }
            $code = if ($cause -is [ComponentModel.Win32Exception]) { $cause.NativeErrorCode } else { $cause.HResult -band 0xffff }
            # ERROR_ACCESS_DENIED / SHARING_VIOLATION / LOCK_VIOLATION only.
            # Persistent errors still fail and enter the existing rollback path.
            $remaining = 10000 - $timer.ElapsedMilliseconds
            if ($code -notin @(5, 32, 33) -or $remaining -le 0) { throw }
            Write-Verbose "Retrying scoped rename after Windows error $code ($($timer.ElapsedMilliseconds) ms): $from"
            Start-Sleep -Milliseconds ([Math]::Min(200, $remaining))
        }
    }
}

function Publish-PackageItems {
    param([Parameter(Mandatory)][object[]]$Items, [Parameter(Mandatory)][string]$AllowedParent)
    $token = [Guid]::NewGuid().ToString('N')
    $published = [Collections.Generic.List[object]]::new()
    try {
        foreach ($item in $Items) {
            Assert-PackageChildPath $item.Source $AllowedParent
            Assert-PackageChildPath $item.Destination $AllowedParent
            $record = @{ Destination = $item.Destination; Backup = Join-Path $AllowedParent ".previous-$token-$($published.Count)"; Saved = $false; Installed = $false }
            $published.Add($record)
            if (Test-Path -LiteralPath $item.Destination) {
                Move-PackagePath $item.Destination $record.Backup $AllowedParent
                $record.Saved = $true
            }
            Move-PackagePath $item.Source $item.Destination $AllowedParent
            $record.Installed = $true
        }
    } catch {
        $failure = $_
        for ($i = $published.Count - 1; $i -ge 0; $i--) {
            $record = $published[$i]
            if ($record.Installed) { Remove-PackagePath $record.Destination $AllowedParent }
            if ($record.Saved) { Move-PackagePath $record.Backup $record.Destination $AllowedParent }
        }
        throw $failure
    }
    foreach ($record in $published) {
        if ($record.Saved) { Remove-PackagePath $record.Backup $AllowedParent }
    }
}
