#Requires -Version 7.0
param(
    [string]$InputDirectory = 'C:\TauriAcceptance\Input',
    [string]$OutputDirectory = 'C:\TauriAcceptance\Output',
    [switch]$ShutdownGuest
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$machine = Get-CimInstance Win32_ComputerSystem
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq 'DESKTOP-3DSSD2K' -or $machine.Model -ne 'Virtual Machine') {
    throw 'Product acceptance is restricted to the actual Windows Sandbox guest.'
}
if ($OutputDirectory -cne 'C:\TauriAcceptance\Output' -or $InputDirectory -cne 'C:\TauriAcceptance\Input') {
    throw 'Unexpected Sandbox mapping.'
}
$work = Join-Path 'C:\' ('tauri-acceptance-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$evidence = Join-Path $work 'evidence'
[void][IO.Directory]::CreateDirectory($evidence)
$report = [ordered]@{
    schemaVersion=1; startedAt=[DateTime]::UtcNow.ToString('o'); success=$false; identity=$identity
    sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; computerName=$env:COMPUTERNAME
    computerModel=$machine.Model; os=[Environment]::OSVersion.VersionString; workDirectory=$work
    sourceCommit=$null; stages=@(); checks=@(); phase='input-verification'
    remoteCI=$false; realAccountsUsed=$false; learningTasksCreated=$false
    scope='unselected'; fullAcceptancePassed=$false; standardLocalReleaseAcceptancePassed=$false
    limitations=@('Windows Sandbox on Windows 11 build 26100, not the full Win10/Win11 matrix',
        'No physical mouse acceptance', 'Artifacts are unsigned; no actual code-signing success path',
        'Guest success applies only to the selected local scope, not full P3/external-review/CI acceptance')
}
function Save-Status {
    $report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'status.json') -Encoding utf8
}
function Assert-NoProductProfiles {
    $states = @(foreach ($folder in @('ApplicationData','LocalApplicationData')) {
        foreach ($app in @('com.chaoxing.gui','chaoxing-desktop')) {
            $target = Join-Path ([Environment]::GetFolderPath($folder)) $app
            [ordered]@{path=$target; exists=(Test-Path -LiteralPath $target)}
        }
    })
    if (@($states | Where-Object exists).Count) { throw 'Preexisting application data in guest; refusing release execution.' }
    return $states
}
function Run-Stage {
    param([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$ExpectedExit=0, [int]$Timeout=300)
    $report.phase=$Name
    Save-Status
    $stage = [ordered]@{name=$Name; executable=$Executable; args=$Arguments; expectedExit=$ExpectedExit; startedAt=[DateTime]::UtcNow.ToString('o')}
    $report.stages += $stage
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Executable
    $info.WorkingDirectory=$work
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new()
    $process.StartInfo=$info
    $outFile=$null; $errFile=$null; $started=$false
    try {
        $outFile=[IO.File]::Open((Join-Path $OutputDirectory ($Name + '.stdout.log')), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $errFile=[IO.File]::Open((Join-Path $OutputDirectory ($Name + '.stderr.log')), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        [void]$process.Start()
        $started=$true
        $stage['pid']=$process.Id
        $outTask=$process.StandardOutput.BaseStream.CopyToAsync($outFile)
        $errTask=$process.StandardError.BaseStream.CopyToAsync($errFile)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit($Timeout * 1000)) {
            $stage['timedOut']=$true
            $process.Kill($true)
            [void]$process.WaitForExit(10000)
            throw "$Name timed out after $Timeout seconds."
        }
        if (-not $outTask.Wait(10000) -or -not $errTask.Wait(10000)) { throw "$Name output drain timed out." }
        $stage['exitCode']=$process.ExitCode
        $stage['success']=$process.ExitCode -eq $ExpectedExit
        if (-not $stage.success) { throw "$Name returned $($process.ExitCode), expected $ExpectedExit." }
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(10000) }
        if ($outFile) { $outFile.Dispose() }
        if ($errFile) { $errFile.Dispose() }
        $process.Dispose()
        $stage['endedAt']=[DateTime]::UtcNow.ToString('o')
        Save-Status
    }
}
function Read-OnlyRunReport {
    param([string]$Directory, [string]$Kind)
    $paths=@(Get-ChildItem -LiteralPath $Directory -Directory | ForEach-Object { Join-Path $_.FullName 'result.json' } | Where-Object { Test-Path -LiteralPath $_ })
    if ($paths.Count -ne 1) { throw "$Kind must produce exactly one run report." }
    $actual=Get-Content -LiteralPath $paths[0] -Raw | ConvertFrom-Json
    if ($actual.success -ne $true -or @($actual.checks).Count -eq 0) { throw "$Kind did not pass its actual assertions." }
    return $actual
}
try {
    $report['initialProductProfiles']=Assert-NoProductProfiles
    $python=@(Get-Command python.exe,python3.exe,py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object Name,Source)
    $report['initialPythonCommands']=$python
    $report['pythonRegistryPresent']=(Test-Path -LiteralPath 'HKLM:\SOFTWARE\Python') -or (Test-Path -LiteralPath 'HKCU:\SOFTWARE\Python')
    if ($python.Count -ne 0 -or $report.pythonRegistryPresent) { throw 'Guest must have no system Python before acceptance.' }
    $manifest=Get-Content -LiteralPath (Join-Path $InputDirectory 'input-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.sourceCommit -cnotmatch '^[0-9a-f]{40}$' -or $manifest.baselineCommit -cne '37fde9160229e99d2fa837d2f1a850203c260d81') { throw 'Unexpected source manifest.' }
    $report.sourceCommit=$manifest.sourceCommit
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.files) {
        if ($entry.path -match '(^|/)\.\.?(/|$)|[\\:\x00-\x1f]' -or $entry.path.StartsWith('/') -or -not $seen.Add($entry.path)) { throw 'Unsafe manifest path.' }
        $source=Join-Path $InputDirectory $entry.path
        $item=Get-Item -LiteralPath $source
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -ne $entry.length) { throw "Invalid input: $($entry.path)" }
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw "Input hash mismatch: $($entry.path)" }
        $destination=Join-Path $work $entry.path
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        [IO.File]::Copy($source,$destination,$false)
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw "Copied input hash mismatch: $($entry.path)" }
    }
    $report['verifiedInputFiles']=$seen.Count
    $acceptanceMode=(Get-Content -LiteralPath (Join-Path $work 'acceptance-mode.json') -Raw | ConvertFrom-Json).mode
    if ($acceptanceMode -cnotin @('Standard','NativeInstallationPartial')) { throw 'Unexpected acceptance mode.' }
    $report['acceptanceMode']=$acceptanceMode
    $report.scope=if ($acceptanceMode -ceq 'Standard') { 'standard-local-release-acceptance' } else { 'native-installation-partial' }
    $sourceRoot=Join-Path $work 'source'
    [IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $work 'source.zip'),$sourceRoot)
    $tools=Join-Path $work 'tools'
    $pwsh=Join-Path $tools 'pwsh/pwsh.exe'
    $node=Join-Path $tools 'node/node.exe'
    $sevenZip=Join-Path $tools '7zip/7z.exe'
    $env:PATH=(Join-Path $tools 'node') + ';' + (Join-Path $tools 'pwsh') + ';' + (Join-Path $tools '7zip') + ";$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:PYTHONUTF8='1'
    $env:NO_PROXY='localhost,127.0.0.1'
    $nodeVersion=(& $node --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -cne 'v20.20.0') { throw 'Guest Node must be 20.20.0.' }
    $report['node']=$nodeVersion
    $report['powershell']=[string]$PSVersionTable.PSVersion
    [void][IO.Directory]::CreateDirectory((Join-Path $sourceRoot 'desktop/node_modules'))
    Copy-Item -LiteralPath (Join-Path $tools 'playwright-core') -Destination (Join-Path $sourceRoot 'desktop/node_modules/playwright-core') -Recurse
    Run-Stage '7zip-version' $sevenZip @('i') 0 30
    $artifacts=Join-Path $work 'artifacts'
    $portable=Join-Path $artifacts 'chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip'
    $installer=Join-Path $artifacts 'chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe'
    $layout=Join-Path $work 'portable-preflight'
    [IO.Compression.ZipFile]::ExtractToDirectory($portable,$layout)
    $hostExe=Join-Path $layout 'chaoxing-gui-tauri.exe'
    Run-Stage 'missing-webview2-native-check' $hostExe @('--check-webview2') 3 30
    Run-Stage 'missing-webview2-portable-entry' $pwsh @('-NoProfile','-File',(Join-Path $layout 'Start-Chaoxing.ps1')) 3 40
    $report.checks += [ordered]@{name='missing-webview2-exit3-without-data';success=$true;profiles=(Assert-NoProductProfiles)}
    if (@(Get-Process -Name 'chaoxing*','p2-backend' -ErrorAction SilentlyContinue).Count) { throw 'Missing-runtime preflight left a product process.' }
    $runtime=Join-Path $tools 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    $signature=Get-AuthenticodeSignature -LiteralPath $runtime
    $report['runtimeInstallerSignature']=[ordered]@{status=[string]$signature.Status;subject=$signature.SignerCertificate.Subject;thumbprint=$signature.SignerCertificate.Thumbprint;sha256=(Get-FileHash -LiteralPath $runtime).Hash.ToLowerInvariant()}
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Runtime installer is not validly Microsoft signed.' }
    Run-Stage 'install-offline-webview2' $pwsh @('-NoProfile','-File',(Join-Path $layout 'Install-WebView2.ps1'),'-InstallerPath',$runtime,'-TimeoutSeconds','480') 0 520
    Run-Stage 'installed-webview2-native-check' $hostExe @('--check-webview2') 0 30
    Run-Stage 'native-release-build-profile' $hostExe @('--check-debug-build') 4 30
    $report.checks += [ordered]@{name='offline-microsoft-runtime-install';success=$true}
    [void][IO.Directory]::CreateDirectory((Join-Path $sourceRoot 'web'))
    Copy-Item -LiteralPath (Join-Path $layout 'backend/_internal/web/dist') -Destination (Join-Path $sourceRoot 'web/dist') -Recurse
    $scripts=Join-Path $sourceRoot 'desktop/scripts'
    if ($acceptanceMode -ceq 'NativeInstallationPartial') {
        $report.limitations += 'Supplemental native lifecycle only; no CDP, frontend, IPC or business readiness pass'
        $prepared=Get-Content -LiteralPath (Join-Path $work 'native/preparation.json') -Raw | ConvertFrom-Json
        if ($prepared.sourceSha256 -cne (Get-FileHash -LiteralPath (Join-Path $scripts 'p3-installation.mjs')).Hash.ToLowerInvariant() -or
            $prepared.scope -cne 'native-installation-partial' -or $prepared.fullAcceptancePassed -ne $false) { throw 'Native preparation provenance mismatch.' }
        $nativeSource=Join-Path $work 'native/p3-installation-native-partial.mjs'
        $nativeTarget=Join-Path $scripts 'p3-installation-native-partial.mjs'
        [IO.File]::Copy($nativeSource,$nativeTarget,$false)
        if ((Get-FileHash -LiteralPath $nativeTarget).Hash.ToLowerInvariant() -cne $prepared.generatedSha256) { throw 'Native supplemental module copy hash mismatch.' }
        $installationEvidence=Join-Path $evidence 'native-installation-partial'
        Run-Stage 'native-installation-partial' $node @($nativeTarget,
            '--installer-path',$installer,'--portable-path',$portable,'--powershell-path',$pwsh,
            '--evidence-directory',$installationEvidence,'--timeout-seconds','480','--disposable-windows-user') 0 1200
        $installation=Read-OnlyRunReport $installationEvidence 'Native installation partial'
        if ($installation.scope -cne 'native-installation-partial' -or $installation.fullAcceptancePassed -ne $false -or
            $installation.formalReleaseSmokePassed -ne $false) { throw 'Supplemental result misstates full acceptance.' }
        foreach ($required in @('portable-native-release-layout','installed-native-release-layout','uninstall-retains-tauri-data-and-old-electron')) {
            if (-not @($installation.checks | Where-Object { $_.name -ceq $required -and $_.result -ceq 'PASS' }).Count) { throw "Missing supplemental assertion: $required" }
        }
        $report['nativeInstallationPartial']=[ordered]@{success=$true;checks=@($installation.checks).Count;runId=$installation.runId;fullAcceptancePassed=$false}
    } else {
        $fakeEvidence=Join-Path $evidence 'release-fake'
        Run-Stage 'release-fake' $pwsh @('-NoProfile','-File',(Join-Path $scripts 'smoke-tauri.ps1'),
            '-HostPath',$hostExe,'-BackendDirectory',(Join-Path $layout 'backend'),'-FakeBackendDirectory',(Join-Path $work 'fixture/p2-backend'),
            '-EvidenceDirectory',$fakeEvidence,'-Configuration','Release','-Scenario','Fake','-NestedJob','-DisposableWindowsUser','-TimeoutSeconds','300') 0 420
        $fake=Read-OnlyRunReport $fakeEvidence 'Release fake'
        if ($fake.selection.configuration -cne 'Release' -or $fake.selection.nestedJob -ne $true) { throw 'Incorrect fake smoke selection.' }
        $report['releaseFake']=[ordered]@{success=$true;checks=@($fake.checks).Count;runId=$fake.runId}
        $report['betweenStageProfiles']=Assert-NoProductProfiles
        $installationEvidence=Join-Path $evidence 'installation'
        Run-Stage 'installation' $pwsh @('-NoProfile','-File',(Join-Path $scripts 'smoke-installation.ps1'),
            '-InstallerPath',$installer,'-PortablePath',$portable,'-EvidenceDirectory',$installationEvidence,'-DisposableWindowsUser','-TimeoutSeconds','480') 0 650
        $installation=Read-OnlyRunReport $installationEvidence 'Installation'
        if (-not @($installation.checks | Where-Object { $_.name -ceq 'uninstall-retains-tauri-data-and-old-electron' }).Count) { throw 'Missing actual retention/uninstall assertion.' }
        $report['installation']=[ordered]@{success=$true;checks=@($installation.checks).Count;runId=$installation.runId}
        $report.standardLocalReleaseAcceptancePassed=$true
    }
    $report['finalProductProfiles']=Assert-NoProductProfiles
    if (@(Get-Process -Name 'chaoxing*','p2-backend' -ErrorAction SilentlyContinue).Count) { throw 'Product process remains after acceptance.' }
    $report.success=$true
    $report.phase='completed'
} catch {
    $report['error']=$_ | Out-String
    $report['scriptStackTrace']=$_.ScriptStackTrace
} finally {
    $exportRoot=Join-Path $OutputDirectory 'evidence'
    [void][IO.Directory]::CreateDirectory($exportRoot)
    foreach ($file in Get-ChildItem -LiteralPath $evidence -File -Recurse) {
        $relative=[IO.Path]::GetRelativePath($evidence,$file.FullName)
        if ($relative -match '(^|[\\/])fixtures([\\/]|$)' -or $file.Extension -notin @('.json','.txt','.log','.md','.png')) { continue }
        $destination=Join-Path $exportRoot $relative
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    $report['endedAt']=[DateTime]::UtcNow.ToString('o')
    Save-Status
    $report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'result.json') -Encoding utf8
    if ($ShutdownGuest) { & "$env:SystemRoot\System32\shutdown.exe" /s /t 5 }
}
if (-not $report.success) { exit 1 }
