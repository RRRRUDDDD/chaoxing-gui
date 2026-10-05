#Requires -Version 7.0
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
. (Join-Path $PSScriptRoot 'p3-wrapper-common.ps1')
$scriptArguments = @(
    '--kind', 'tauri',
    '--host-path', $HostPath, '--backend-directory', $BackendDirectory,
    '--fake-backend-directory', $FakeBackendDirectory, '--evidence-directory', $EvidenceDirectory,
    '--configuration', $Configuration, '--scenario', $Scenario,
    '--timeout-seconds', [string]$TimeoutSeconds
)
if ($NestedJob) { $scriptArguments += '--nested-job' }
if ($UsePackagedLayout) { $scriptArguments += '--use-packaged-layout' }
if ($DisposableWindowsUser) { $scriptArguments += '--disposable-windows-user' }
Invoke-P3NodeScript -ScriptName 'p3-smoke.mjs' -ScriptArguments $scriptArguments
