[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HostPath,
    [string]$BackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$FakeBackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p2-fixture/dist/p2-backend'),
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-smoke-evidence'),
    [string]$Configuration = 'Release',
    [string]$Scenario = 'All',
    [switch]$NestedJob,
    [switch]$UsePackagedLayout,
    [switch]$DisposableWindowsUser,
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-smoke.mjs'), '--kind', 'tauri',
    '--host-path', $HostPath, '--backend-directory', $BackendDirectory,
    '--fake-backend-directory', $FakeBackendDirectory, '--evidence-directory', $EvidenceDirectory,
    '--configuration', $Configuration, '--scenario', $Scenario,
    '--timeout-seconds', [string]$TimeoutSeconds, '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
if ($NestedJob) { $p3Args += '--nested-job' }
if ($UsePackagedLayout) { $p3Args += '--use-packaged-layout' }
if ($DisposableWindowsUser) { $p3Args += '--disposable-windows-user' }
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
