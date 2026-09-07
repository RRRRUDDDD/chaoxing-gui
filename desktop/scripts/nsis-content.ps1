# Shared by read-only package validation and its payload fault tests.
# package-common.ps1 must be loaded first.

function Get-NsisHostExpectation {
    param([Parameter(Mandatory)][string]$HostPath, [string]$SignedHostRecordPath)
    $hostFile = Get-PackageFullPath $HostPath
    Assert-PackageNoReparse $hostFile
    if (-not [IO.File]::Exists($hostFile) -or [IO.FileInfo]::new($hostFile).Length -gt 64MB) { throw 'Missing or oversized compiler host.' }
    $bytes = [IO.File]::ReadAllBytes($hostFile)
    if ($bytes.Length -lt 2 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw 'Compiler host has no PE header.' }
    $sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    # Tauri CLI 2.11.4 patches exactly this marker, signs the patched host, then
    # restores the original unsigned bytes. Never normalize arbitrary bytes or
    # derive an expectation from the installer we are about to inspect.
    $unknown = '__TAURI_BUNDLE_TYPE_VAR_UNK'
    $nsis = '__TAURI_BUNDLE_TYPE_VAR_NSS'
    $text = [Text.Encoding]::Latin1.GetString($bytes)
    $offset = $text.IndexOf($unknown, [StringComparison]::Ordinal)
    if ($offset -lt 0 -or $text.IndexOf($unknown, $offset + 1, [StringComparison]::Ordinal) -ge 0 -or
        $text.Contains($nsis, [StringComparison]::Ordinal)) { throw 'Expected exactly one unpatched Tauri bundle marker.' }
    [Text.Encoding]::ASCII.GetBytes($nsis).CopyTo($bytes, $offset)
    $patchedHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    $record = [ordered]@{
        path='chaoxing-gui-tauri.exe'; length=[long]$bytes.Length; sha256=$patchedHash
        sourceSha256=$sourceHash; preSignLength=[long]$bytes.Length; preSignSha256=$patchedHash
        markerOffset=$offset; signed=$false; signerThumbprint=$null; derivation='tauri-cli-2.11.4-bundle-marker'
    }
    if ($SignedHostRecordPath) {
        $capture = Read-PackageJson (Get-PackageFullPath $SignedHostRecordPath)
        if ($capture.schemaVersion -ne 1 -or $capture.kind -cne 'chaoxing-gui-tauri-signed-nsis-host' -or
            $capture.bundleType -cne 'nsis' -or -not $hostFile.Equals((Get-PackageFullPath $capture.sourcePath), [StringComparison]::OrdinalIgnoreCase) -or
            $capture.preSignSha256 -cne $patchedHash -or $capture.preSignLength -ne $bytes.Length -or
            ($capture.length -isnot [long] -and $capture.length -isnot [int]) -or $capture.length -lt $bytes.Length -or $capture.length -gt 64MB -or
            $capture.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $capture.signerThumbprint -cnotmatch '^[A-F0-9]{40}$') {
            throw 'Signed NSIS host capture does not match the exact unsigned compiler output.'
        }
        $record.length = $capture.length
        $record.sha256 = $capture.sha256
        $record.signed = $true
        $record.signerThumbprint = $capture.signerThumbprint
        $record.derivation = 'tauri-cli-2.11.4-bundle-marker-and-sign-command-capture'
    }
    return $record
}

function Assert-NsisHostExpectation {
    param([Parameter(Mandatory)]$Record)
    if ($Record -isnot [Collections.IDictionary] -or $Record.path -cne 'chaoxing-gui-tauri.exe' -or
        ($Record.length -isnot [long] -and $Record.length -isnot [int]) -or $Record.length -le 0 -or $Record.length -gt 64MB -or
        $Record.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $Record.sourceSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Record.preSignSha256 -cnotmatch '^[0-9a-f]{64}$' -or $Record.signed -isnot [bool]) { throw 'Invalid NSIS host expectation.' }
    if ($Record.signed) {
        if ($Record.signerThumbprint -cnotmatch '^[A-F0-9]{40}$' -or $Record.derivation -cne 'tauri-cli-2.11.4-bundle-marker-and-sign-command-capture') { throw 'Invalid signed NSIS host expectation.' }
    } elseif ($null -ne $Record.signerThumbprint -or $Record.derivation -cne 'tauri-cli-2.11.4-bundle-marker' -or
        $Record.sha256 -cne $Record.preSignSha256 -or $Record.length -ne $Record.preSignLength) { throw 'Invalid unsigned NSIS host expectation.' }
}

function Get-NsisArtifactIdentity {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$ExpectedName)
    $file = Get-PackageFullPath $Path
    Assert-PackageNoReparse $file
    if ([IO.Path]::GetFileName($file) -cne $ExpectedName -or -not [IO.File]::Exists($file) -or [IO.FileInfo]::new($file).Length -eq 0) {
        throw "Missing or incorrectly named artifact: $ExpectedName"
    }
    return [ordered]@{ path=$ExpectedName; length=[IO.FileInfo]::new($file).Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
}

function Write-NsisArtifactManifest {
    param([Parameter(Mandatory)][string]$InstallerPath, [Parameter(Mandatory)][string]$PortablePath, [Parameter(Mandatory)]$HostExpectation)
    $version = Get-PackageVersion
    Assert-NsisHostExpectation $HostExpectation
    $record = [ordered]@{
        schemaVersion=1; kind='chaoxing-gui-tauri-nsis-artifact'; version=$version; platform='windows-x64'
        installer=(Get-NsisArtifactIdentity $InstallerPath "chaoxing-gui-tauri-setup-$version-windows-x64.exe")
        portable=(Get-NsisArtifactIdentity $PortablePath "chaoxing-gui-tauri-portable-$version-windows-x64.zip")
        host=$HostExpectation
    }
    $sidecar = (Get-PackageFullPath $InstallerPath) + '.manifest.json'
    Assert-PackageNoReparse $sidecar
    Write-PackageJson $sidecar $record
}

function Read-NsisPayloadManifest {
    param([Parameter(Mandatory)][string]$InstallerPath, [Parameter(Mandatory)][string]$PortablePath, [Parameter(Mandatory)]$PortableManifest)
    $version = Get-PackageVersion
    Assert-PackageManifestHeader $PortableManifest 'chaoxing-gui-tauri-portable' $version 'chaoxing-gui-tauri.exe'
    Assert-PackageInventoryManifest $PortableManifest $PortableManifest
    $sidecar = Read-PackageJson ((Get-PackageFullPath $InstallerPath) + '.manifest.json')
    Assert-PackageManifestHeader $sidecar 'chaoxing-gui-tauri-nsis-artifact' $version
    if ($sidecar.platform -cne 'windows-x64' -or $PortableManifest.platform -cne 'windows-x64') { throw 'Invalid NSIS payload platform.' }
    $identities = @{
        installer=(Get-NsisArtifactIdentity $InstallerPath "chaoxing-gui-tauri-setup-$version-windows-x64.exe")
        portable=(Get-NsisArtifactIdentity $PortablePath "chaoxing-gui-tauri-portable-$version-windows-x64.zip")
    }
    foreach ($name in $identities.Keys) {
        $record = $sidecar[$name]
        if ($record -isnot [Collections.IDictionary] -or $record.path -cne $identities[$name].path -or
            $record.length -ne $identities[$name].length -or $record.sha256 -cne $identities[$name].sha256) { throw "NSIS manifest $name artifact identity mismatch." }
    }
    Assert-NsisHostExpectation $sidecar.host
    $hosts = @($PortableManifest.files | Where-Object { $_.path -ceq 'chaoxing-gui-tauri.exe' })
    if ($hosts.Count -ne 1) { throw 'Portable manifest must contain exactly one host.' }
    if (-not $sidecar.host.signed -and ($hosts[0].sha256 -cne $sidecar.host.sourceSha256 -or $hosts[0].length -ne $sidecar.host.preSignLength)) {
        throw 'Unsigned NSIS host source differs from the verified portable host.'
    }
    return [ordered]@{
        schemaVersion=1; kind='chaoxing-gui-tauri-nsis-payload'; version=$version; platform='windows-x64'; entryPoint='chaoxing-gui-tauri.exe'
        files=@(foreach ($file in $PortableManifest.files) {
            if ($file.path -ceq 'chaoxing-gui-tauri.exe') { [ordered]@{ path=$file.path; length=$sidecar.host.length; sha256=$sidecar.host.sha256 } }
            else { $file }
        })
        directories=@($PortableManifest.directories); hostExpectation=$sidecar.host
    }
}

function Read-NsisContentListing {
    param([Parameter(Mandatory)][string]$Text)
    $parts = $Text -split '(?m)^----------\r?$', 2
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '(?m)^Type = Nsis\r?$') { throw 'Expected a 7-Zip NSIS archive listing.' }
    if ($parts[0] -match 'BadCmd=') { throw 'This 7-Zip version cannot fully parse NSIS 3; install a recent 7-Zip and retry.' }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    foreach ($block in ($parts[1].Trim() -split '\r?\n\r?\n')) {
        $fields = @{}
        foreach ($line in ($block -split '\r?\n')) {
            if ($line -match '^([^=]+) = (.*)$') {
                if ($fields.ContainsKey($Matches[1])) { throw 'Duplicate NSIS listing field.' }
                $fields[$Matches[1]] = $Matches[2]
            }
        }
        if (-not $fields.ContainsKey('Path') -or -not $fields.ContainsKey('Size')) { throw 'Incomplete NSIS listing record.' }
        $relative = $fields.Path.Replace('\', '/')
        Assert-PackageRelativePath $relative
        if (-not $names.Add($relative) -or $names.Count -gt 100000) { throw "Duplicate or excessive NSIS paths: $relative" }
        [long]$length = 0
        # NSIS synthesizes the uninstaller from its embedded stub. 7-Zip can
        # list this entry without a length; enforce bounds after extraction.
        $generatedUninstaller = $relative -ceq 'uninstall.exe' -and $fields.Size -ceq ''
        if (-not $generatedUninstaller -and (-not [long]::TryParse($fields.Size, [ref]$length) -or $length -lt 0 -or $length -gt 2GB)) { throw "Invalid NSIS file length: $relative" }
        $total += $length
        if ($total -gt 8GB) { throw 'NSIS payload exceeds the allowed size.' }
        if (($fields.ContainsKey('Attributes') -and $fields.Attributes -match '[LD]') -or
            $fields.ContainsKey('Symbolic Link') -or $fields.ContainsKey('Hard Link') -or
            ($fields.ContainsKey('Folder') -and $fields.Folder -eq '+')) {
            throw "Unexpected link or directory NSIS entry: $relative"
        }
        [ordered]@{ path=$relative; length=$(if ($generatedUninstaller) { $null } else { $length }) }
    }
}

function Assert-NsisContent {
    param([Parameter(Mandatory)][object[]]$Entries, [Parameter(Mandatory)]$Manifest, [string]$ExtractedDirectory)
    $expected = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    foreach ($file in $Manifest.files) { $expected.Add($file.path, $file) }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    # These are the stock NSIS installer plugins, never application payload.
    $plugins = @('System.dll', 'modern-wizard.bmp', 'modern-header.bmp', 'nsDialogs.dll', 'nsis_tauri_utils.dll', 'StartMenu.dll', 'NSISdl.dll', 'LangDLL.dll', 'UserInfo.dll')
    foreach ($entry in $Entries) {
        $relative = $entry.path
        # Some current 7-Zip-compatible decoders additionally expose the
        # decompiled install script/license; these are archive metadata.
        if ($relative -cin @('[NSIS].nsi', '[LICENSE].txt')) { continue }
        if ($relative -ceq 'uninstall.exe') {
            if ($ExtractedDirectory) {
                $uninstaller = Join-Path $ExtractedDirectory $relative
                Assert-PackageChildPath $uninstaller $ExtractedDirectory
                if (-not [IO.File]::Exists($uninstaller) -or [IO.FileInfo]::new($uninstaller).Length -gt 20MB) { throw 'Missing or oversized generated NSIS uninstaller.' }
                $stream = [IO.File]::OpenRead($uninstaller)
                try { if ($stream.ReadByte() -ne 0x4d -or $stream.ReadByte() -ne 0x5a) { throw 'Invalid generated NSIS uninstaller PE header.' } }
                finally { $stream.Dispose() }
            }
            continue
        }
        if ($relative.StartsWith('$PLUGINSDIR/', [StringComparison]::Ordinal)) {
            if ($relative.Substring(12) -cnotin $plugins) { throw "Unexpected NSIS installer helper: $relative" }
            continue
        }
        if (-not $expected.ContainsKey($relative)) { throw "Unexpected NSIS payload file: $relative" }
        $file = $expected[$relative]
        if (-not $seen.Add($relative) -or $entry.length -ne $file.length) { throw "NSIS payload length/collision mismatch: $relative" }
        if ($ExtractedDirectory) {
            $actual = Join-Path $ExtractedDirectory $relative
            Assert-PackageChildPath $actual $ExtractedDirectory
            if (-not [IO.File]::Exists($actual) -or [IO.FileInfo]::new($actual).Length -ne $file.length -or
                (Get-FileHash -LiteralPath $actual -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.sha256) {
                throw "NSIS payload hash/length mismatch: $relative"
            }
        }
    }
    foreach ($relative in $expected.Keys) {
        if (-not $seen.Contains($relative)) { throw "Missing NSIS payload file: $relative" }
    }
}
