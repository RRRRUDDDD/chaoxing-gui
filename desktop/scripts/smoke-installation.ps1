#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$PortablePath,
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-installation-evidence'),
    [switch]$DisposableWindowsUser,
    [ValidateRange(1, 1200)][int]$TimeoutSeconds = 360
)
. (Join-Path $PSScriptRoot 'p3-wrapper-common.ps1')
$scriptArguments = @(
    '--installer-path', $InstallerPath,
    '--portable-path', $PortablePath, '--evidence-directory', $EvidenceDirectory,
    '--timeout-seconds', [string]$TimeoutSeconds
)
if ($DisposableWindowsUser) { $scriptArguments += '--disposable-windows-user' }
Invoke-P3NodeScript -ScriptName 'p3-installation.mjs' -ScriptArguments $scriptArguments
