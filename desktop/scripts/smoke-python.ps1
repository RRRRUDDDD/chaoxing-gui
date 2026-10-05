#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [string]$Mode = 'Backend',
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-python-smoke-evidence'),
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 150
)
. (Join-Path $PSScriptRoot 'p3-wrapper-common.ps1')
Invoke-P3NodeScript -ScriptName 'p3-smoke.mjs' -ScriptArguments @(
    '--kind', 'python',
    '--executable-path', $ExecutablePath, '--mode', $Mode,
    '--evidence-directory', $EvidenceDirectory, '--timeout-seconds', [string]$TimeoutSeconds
)
