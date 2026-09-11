$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq 'DESKTOP-3DSSD2K') { throw 'Sandbox guest required.' }
$code=1
try {
    & 'C:\TauriAcceptance\Input\tools\pwsh\pwsh.exe' -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\TauriAcceptance\Input\sandbox-guest.ps1' -ShutdownGuest 1> 'C:\TauriAcceptance\Output\bootstrap.stdout.log' 2> 'C:\TauriAcceptance\Output\bootstrap.stderr.log'
    $code=$LASTEXITCODE
} catch {
    $_ | Out-String | Set-Content -LiteralPath 'C:\TauriAcceptance\Output\bootstrap-error.log'
} finally {
    @{identity=$identity;exitCode=$code;endedAt=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath 'C:\TauriAcceptance\Output\bootstrap-result.json'
    & "$env:SystemRoot\System32\shutdown.exe" /s /t 5
}
