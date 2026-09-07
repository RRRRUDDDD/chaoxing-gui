#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$PortablePath,
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-installation-evidence'),
    [switch]$DisposableWindowsUser,
    [ValidateRange(1, 1200)][int]$TimeoutSeconds = 360
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-installation.mjs'), '--installer-path', $InstallerPath,
    '--portable-path', $PortablePath, '--evidence-directory', $EvidenceDirectory,
    '--timeout-seconds', [string]$TimeoutSeconds, '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
if ($DisposableWindowsUser) { $p3Args += '--disposable-windows-user' }
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
