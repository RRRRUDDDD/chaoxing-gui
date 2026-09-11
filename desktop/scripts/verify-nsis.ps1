#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [Parameter(Mandatory)][string]$PortablePath,
    [string]$EvidenceDirectory,
    [string]$SevenZipPath
)
. (Join-Path $PSScriptRoot 'package-common.ps1')
. (Join-Path $PSScriptRoot 'nsis-content.ps1')
$version = Get-PackageVersion
$installer = Get-PackageFullPath $InstallerPath
$portable = Get-PackageFullPath $PortablePath
Assert-PackageNoReparse $installer
if ([IO.Path]::GetFileName($installer) -cne "chaoxing-gui-tauri-setup-$version-windows-x64.exe" -or -not [IO.File]::Exists($installer)) {
    throw 'Missing or incorrectly named NSIS artifact.'
}
& (Join-Path $PSScriptRoot 'verify-package.ps1') -PackagePath $portable -Version $version
if (-not $SevenZipPath) {
    $command = Get-Command 7z.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw 'NSIS inspection requires a recent 7-Zip (7z.exe on PATH) or explicit -SevenZipPath.' }
    $SevenZipPath = $command.Source
}
$sevenZip = Get-PackageFullPath $SevenZipPath
if (-not [IO.File]::Exists($sevenZip)) { throw 'Missing 7-Zip inspection executable.' }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $PSScriptRoot '../release/verification/nsis-content' }
$evidence = Get-PackageFullPath $EvidenceDirectory
Assert-PackageMutableDirectory $evidence
Assert-PackageDisjoint $evidence $installer
Assert-PackageDisjoint $evidence $portable
[void][IO.Directory]::CreateDirectory($evidence)
$run = Join-Path $evidence "nsis-$([Guid]::NewGuid().ToString('N'))"
[void][IO.Directory]::CreateDirectory($run)
$extracted = Join-Path $run 'payload'

function Invoke-ArchiveTool {
    param([string[]]$Arguments, [string]$LogName)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $sevenZip
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(120000)) {
            $process.Kill($true)
            [void]$process.WaitForExit(5000)
            throw '7-Zip archive inspection timed out.'
        }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw '7-Zip output drain timed out.' }
        [IO.File]::WriteAllText((Join-Path $run "$LogName.stdout.txt"), $stdout.Result, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText((Join-Path $run "$LogName.stderr.txt"), $stderr.Result, [Text.UTF8Encoding]::new($false))
        if ($process.ExitCode -ne 0) { throw "7-Zip $LogName failed with exit $($process.ExitCode): $($stderr.Result)" }
        return $stdout.Result
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(5000) }
        $process.Dispose()
    }
}

$result = [ordered]@{ success=$false; version=$version; installer=$installer; portable=$portable; archiveTool=$sevenZip; installerExecuted=$false }
try {
    $archive = [IO.Compression.ZipFile]::OpenRead($portable)
    try {
        $reader = [IO.StreamReader]::new($archive.GetEntry('package-manifest.json').Open(), [Text.Encoding]::UTF8)
        try { $manifest = ConvertFrom-Json -InputObject $reader.ReadToEnd() -AsHashtable -Depth 20 }
        finally { $reader.Dispose() }
    } finally { $archive.Dispose() }
    $manifest = Read-NsisPayloadManifest -InstallerPath $installer -PortablePath $portable -PortableManifest $manifest
    $listing = Invoke-ArchiveTool @('l', '-slt', '-sccUTF-8', $installer) 'list'
    $entries = @(Read-NsisContentListing $listing)
    Assert-NsisContent -Entries $entries -Manifest $manifest
    # Validate every archive path before any extraction; never execute setup.
    [void](Invoke-ArchiveTool @('x', '-y', '-sccUTF-8', "-o$extracted", $installer) 'extract')
    Assert-NsisContent -Entries $entries -Manifest $manifest -ExtractedDirectory $extracted
    $signatureFiles = @($installer, (Join-Path $extracted 'chaoxing-gui-tauri.exe'))
    if ($manifest.hostExpectation.signed) { $signatureFiles += Join-Path $extracted 'uninstall.exe' }
    $result['signatures'] = @(foreach ($file in $signatureFiles) {
        $signature = Get-AuthenticodeSignature -LiteralPath $file
        $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
        if ($manifest.hostExpectation.signed) {
            if ($signature.Status -ne 'Valid' -or $thumbprint -cne $manifest.hostExpectation.signerThumbprint) { throw "NSIS payload signature mismatch: $file ($($signature.Status))" }
        } elseif ($signature.Status -ne 'NotSigned') { throw "Expected unsigned NSIS artifact: $file ($($signature.Status))" }
        [ordered]@{ file=[IO.Path]::GetFileName($file); status=[string]$signature.Status; signerThumbprint=$thumbprint }
    })
    $result.success = $true
    $result['payloadFileCount'] = $manifest.files.Count
    $result['installerSha256'] = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['portableSha256'] = (Get-FileHash -LiteralPath $portable -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['hostExpectation'] = $manifest.hostExpectation
    $result['manifestSha256'] = (Get-FileHash -LiteralPath "$installer.manifest.json" -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['conclusion'] = 'Every NSIS application byte matches its build expectation: the independent NSIS host record and the verified portable resources; installation execution is a separate gate.'
    Write-Output "Verified NSIS $version ($($manifest.files.Count) application files): $installer"
} catch {
    $result['error'] = $_.Exception.Message
    throw
} finally {
    Write-PackageJson (Join-Path $run 'result.json') $result
    if (Test-Path -LiteralPath $extracted) {
        Assert-PackageChildPath $extracted $run
        Remove-Item -LiteralPath $extracted -Recurse -Force
    }
}
