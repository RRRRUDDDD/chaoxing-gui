param(
    [string]$OutputDirectory = 'C:\TauriAcceptance\Output',
    [string]$HostComputerName = 'DESKTOP-3DSSD2K'
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq $HostComputerName) {
    throw 'Probe may run only in Windows Sandbox under its real WDAGUtilityAccount.'
}
$report = [ordered]@{
    startedAt = [DateTime]::UtcNow.ToString('o')
    identity = $identity
    computerName = $env:COMPUTERNAME
    operatingSystem = [Environment]::OSVersion.VersionString
    powershell = [string]$PSVersionTable.PSVersion
    computer = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,HypervisorPresent
    profiles = [ordered]@{
        roaming = [Environment]::GetFolderPath('ApplicationData')
        local = [Environment]::GetFolderPath('LocalApplicationData')
        temp = [IO.Path]::GetTempPath()
    }
    webview2 = @()
    pythonCommands = @(Get-Command python.exe,python3.exe,py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object Name,Source)
    productExecuted = $false
    networkUse = $false
}
foreach ($hive in @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients', 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients', 'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients')) {
    if (Test-Path -LiteralPath $hive) {
        foreach ($key in Get-ChildItem -LiteralPath $hive) {
            if ($key.GetValue('name') -match 'WebView2') {
                $report.webview2 += [ordered]@{name=$key.GetValue('name'); version=$key.GetValue('pv'); registryPath=$key.Name}
            }
        }
    }
}
$report['productProfilesExist'] = @(foreach ($base in @($report.profiles.roaming, $report.profiles.local)) {
    foreach ($app in @('com.chaoxing.gui', 'chaoxing-desktop')) {
        $profile = Join-Path $base $app
        [ordered]@{path=$profile; exists=(Test-Path -LiteralPath $profile)}
    }
})
$report['completedAt'] = [DateTime]::UtcNow.ToString('o')
$report['success'] = $true
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'probe-result.json') -Encoding UTF8
# This is reached only after the actual guest identity check above.
& "$env:SystemRoot\System32\shutdown.exe" /s /t 5
