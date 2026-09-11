#Requires -Version 5.1
[CmdletBinding()]
param([string]$InstallerPath, [ValidateRange(30, 1800)][int]$TimeoutSeconds = 600)

. (Join-Path $PSScriptRoot 'Portable-Common.ps1')
$temporaryDirectory = $null
$downloadedInstaller = $null
try {
    $offlineName = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    if (-not $InstallerPath -and [IO.File]::Exists((Join-Path $PSScriptRoot $offlineName))) {
        $InstallerPath = Join-Path $PSScriptRoot $offlineName
    }
    if ($InstallerPath) {
        $InstallerPath = [IO.Path]::GetFullPath($InstallerPath)
        if ([IO.Path]::GetFileName($InstallerPath) -ine $offlineName -or -not [IO.File]::Exists($InstallerPath)) {
            throw "Provide the official x64 offline installer named $offlineName."
        }
        Assert-PortablePlainPath $InstallerPath
    } else {
        # This download occurs only when the user explicitly runs this install entry.
        $temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("chaoxing-webview2-" + [Guid]::NewGuid().ToString('N'))
        Assert-PortablePlainPath $temporaryDirectory
        [void][IO.Directory]::CreateDirectory($temporaryDirectory)
        $downloadedInstaller = Join-Path $temporaryDirectory 'MicrosoftEdgeWebview2Setup.exe'
        Write-Host 'Downloading the official Microsoft WebView2 bootstrapper...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $downloadedInstaller -TimeoutSec 120
        $InstallerPath = $downloadedInstaller
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
    if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O="?Microsoft Corporation"?(?:,|$)') {
        throw 'Installer signature is not a valid Microsoft Authenticode signature. The installer was not run.'
    }
    Write-Host 'Installing Microsoft Edge WebView2 Runtime. This may take several minutes...'
    $result = Invoke-PortableProcess -FilePath $InstallerPath -Arguments '/silent /install' -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -eq 0) { Write-Host 'WebView2 installation completed. Run Start-Chaoxing.cmd.' }
    elseif ($result.ExitCode -eq 3010) { Write-Host 'WebView2 installation completed. Restart Windows before starting the app.' }
    else { Write-Host "WebView2 installer failed (exit $($result.ExitCode)). $($result.Error)" }
    exit $result.ExitCode
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($temporaryDirectory) {
        # Only delete the exact download and the empty directory this invocation owns.
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char[]]'\/')
        $resolved = [IO.Path]::GetFullPath($temporaryDirectory)
        if ([IO.Path]::GetDirectoryName($resolved) -ine $tempRoot -or [IO.Path]::GetFileName($resolved) -notmatch '^chaoxing-webview2-[0-9a-f]{32}$') {
            throw 'Unsafe WebView2 download cleanup path.'
        }
        Assert-PortablePlainPath $resolved
        if ($downloadedInstaller) { Assert-PortablePlainPath $downloadedInstaller; [IO.File]::Delete($downloadedInstaller) }
        if ([IO.Directory]::Exists($resolved)) { [IO.Directory]::Delete($resolved, $false) }
    }
}
