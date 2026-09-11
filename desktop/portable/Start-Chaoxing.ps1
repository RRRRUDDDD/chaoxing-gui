#Requires -Version 5.1
[CmdletBinding()]
param([ValidateRange(1, 60)][int]$CheckTimeoutSeconds = 15)

. (Join-Path $PSScriptRoot 'Portable-Common.ps1')
try {
    $hostFile = Join-Path $PSScriptRoot 'chaoxing-gui-tauri.exe'
    if (-not [IO.File]::Exists($hostFile)) { throw 'chaoxing-gui-tauri.exe is missing. Extract the entire portable ZIP before starting.' }
    $check = Invoke-PortableProcess -FilePath $hostFile -Arguments '--check-webview2' -TimeoutSeconds $CheckTimeoutSeconds
    if ($check.ExitCode -eq 3) {
        Write-Host 'Microsoft Edge WebView2 Runtime is not installed.'
        Write-Host 'Run Install-WebView2.cmd in this folder, then run Start-Chaoxing.cmd again.'
        Write-Host 'For offline installation, see README.txt. Starting this app never installs a runtime automatically.'
        exit 3
    }
    if ($check.ExitCode -ne 0) {
        Write-Host "WebView2 check failed (exit $($check.ExitCode)). $($check.Error)"
        exit $check.ExitCode
    }
    # Launch the user-owned GUI after preflight. It keeps running after this
    # entrypoint closes; only the short-lived preflight belongs to our cleanup.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $hostFile
    $info.WorkingDirectory = $PSScriptRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Normal
    $info.RedirectStandardInput = $true
    $process = [Diagnostics.Process]::Start($info)
    if ($null -eq $process) { throw 'Could not start Chaoxing GUI Tauri.' }
    try {
        $process.StandardInput.Close()
        # Surface immediate native startup errors while keeping the launcher bounded.
        if ($process.WaitForExit(2000)) { exit $process.ExitCode }
    } finally { $process.Dispose() }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
