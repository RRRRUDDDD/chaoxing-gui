#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate repository' }
$base = Join-Path $repoRoot ('desktop/src-tauri/target/acceptance-followup-probe-' + [Guid]::NewGuid().ToString('N'))
$inputDirectory = Join-Path $base 'input'
$outputDirectory = Join-Path $base 'output'
[void][IO.Directory]::CreateDirectory($inputDirectory)
[void][IO.Directory]::CreateDirectory($outputDirectory)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-probe.ps1') -Destination $inputDirectory
$configPath = Join-Path $base 'probe.wsb'
$escapedInput = [Security.SecurityElement]::Escape($inputDirectory)
$escapedOutput = [Security.SecurityElement]::Escape($outputDirectory)
$config = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$escapedInput</HostFolder><SandboxFolder>C:\TauriAcceptance\Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$escapedOutput</HostFolder><SandboxFolder>C:\TauriAcceptance\Output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\TauriAcceptance\Input\sandbox-probe.ps1</Command></LogonCommand>
</Configuration>
"@
[IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
if (@(Get-Process -Name WindowsSandbox,WindowsSandboxClient -ErrorAction SilentlyContinue).Count) {
    throw 'An existing Windows Sandbox is active; refusing to reuse or stop it.'
}
$run = [ordered]@{startedAt=[DateTime]::UtcNow.ToString('o'); configPath=$configPath; inputDirectory=$inputDirectory; outputDirectory=$outputDirectory; productExecuted=$false; success=$false}
$process = $null
try {
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/WindowsSandbox.exe') -ArgumentList ('"' + $configPath + '"') -WindowStyle Hidden -PassThru
    $run['launcherPid'] = $process.Id
    $deadline = [DateTime]::UtcNow.AddSeconds(150)
    while ([DateTime]::UtcNow -lt $deadline) {
        $resultPath = Join-Path $outputDirectory 'probe-result.json'
        if (Test-Path -LiteralPath $resultPath) {
            $probe = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            $run['guest'] = $probe
            $run.success = $probe.success -eq $true
            Copy-Item -LiteralPath $resultPath -Destination (Join-Path $PSScriptRoot 'sandbox-probe-result.json')
            break
        }
        if ($process.HasExited) {
            $run['launcherExitCode'] = $process.ExitCode
            if ($process.ExitCode -ne 0) { throw "Windows Sandbox launcher exited $($process.ExitCode)" }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $run.success) { throw 'No guest probe evidence within 150 seconds.' }
} catch {
    $run['error'] = $_.Exception.Message
} finally {
    $run['endedAt'] = [DateTime]::UtcNow.ToString('o')
    $run | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-probe-launch.json') -Encoding utf8
    $run | ConvertTo-Json -Depth 10
    if ($process) { $process.Dispose() }
}
if (-not $run.success) { exit 1 }
