#Requires -Version 7.0
param([ValidateRange(30,2400)][int]$TimeoutSeconds=1800)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$kit=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-kit.json') -Raw | ConvertFrom-Json
$inputDirectory=[IO.Path]::GetFullPath($kit.inputDirectory)
if ((Get-FileHash -LiteralPath (Join-Path $inputDirectory 'input-manifest.json')).Hash.ToLowerInvariant() -cne $kit.inputManifestSha256) { throw 'Input manifest changed.' }
if (@(Get-Process -Name WindowsSandbox,WindowsSandboxClient -ErrorAction SilentlyContinue).Count) { throw 'Existing Sandbox; refusing reuse or interruption.' }
$runDirectory=Join-Path $kit.kitDirectory ('run-' + [Guid]::NewGuid().ToString('N'))
$outputDirectory=Join-Path $runDirectory 'output'
[void][IO.Directory]::CreateDirectory($outputDirectory)
$escapedInput=[Security.SecurityElement]::Escape($inputDirectory)
$escapedOutput=[Security.SecurityElement]::Escape($outputDirectory)
$configPath=Join-Path $runDirectory 'acceptance.wsb'
$config=@"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Default</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$escapedInput</HostFolder><SandboxFolder>C:\TauriAcceptance\Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$escapedOutput</HostFolder><SandboxFolder>C:\TauriAcceptance\Output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\TauriAcceptance\Input\sandbox-bootstrap.ps1</Command></LogonCommand>
</Configuration>
"@
[xml]$parsedConfig=$config
[IO.File]::WriteAllText($configPath,$config,[Text.UTF8Encoding]::new($false))
$record=[ordered]@{startedAt=[DateTime]::UtcNow.ToString('o');configPath=$configPath;outputDirectory=$outputDirectory;sourceCommit=$kit.sourceCommit;productExecutedOnHost=$false;success=$false;fullAcceptancePassed=$false;acceptanceMode=$kit.acceptanceMode;networking='enabled for Microsoft certificate/runtime setup; no real accounts'}
$recordFile=Join-Path $PSScriptRoot ('sandbox-launch-' + [IO.Path]::GetFileName($runDirectory) + '.json')
$record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordFile -Encoding utf8
$process=$null
try {
    $process=Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/WindowsSandbox.exe') -ArgumentList ('"' + $configPath + '"') -WindowStyle Hidden -PassThru
    $record['launcherPid']=$process.Id
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordFile -Encoding utf8
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    Write-Output "Sandbox acceptance started; evidence: $outputDirectory"
    while ([DateTime]::UtcNow -lt $deadline) {
        $resultPath=Join-Path $outputDirectory 'result.json'
        if (Test-Path -LiteralPath $resultPath) {
            try { $actual=Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json } catch { Start-Sleep -Milliseconds 500; continue }
            $record['guestResultPath']=$resultPath
            $record['guestIdentity']=$actual.identity
            $record['scope']=$actual.scope
            $record.success=$actual.success -eq $true
            $record['guestPhase']=$actual.phase
            if (-not $record.success) { $record['guestError']=$actual.error }
            break
        }
        $bootstrapPath=Join-Path $outputDirectory 'bootstrap-result.json'
        if (Test-Path -LiteralPath $bootstrapPath) { throw 'Guest bootstrap finished without a product acceptance report; inspect bootstrap logs.' }
        if ($process.HasExited -and $process.ExitCode -ne 0) { throw "Sandbox launcher exited $($process.ExitCode)" }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $record.Contains('guestResultPath')) { throw 'Sandbox acceptance timed out without completed evidence.' }
} catch {
    $record['error']=$_.Exception.Message
} finally {
    $record['endedAt']=[DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordFile -Encoding utf8
    $record | ConvertTo-Json -Depth 8
    if ($process) { $process.Dispose() }
}
if (-not $record.success) { exit 1 }
