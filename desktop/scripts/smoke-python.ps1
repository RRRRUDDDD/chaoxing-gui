[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [string]$Mode = 'Backend',
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-python-smoke-evidence'),
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-smoke.mjs'), '--kind', 'python',
    '--executable-path', $ExecutablePath, '--mode', $Mode,
    '--evidence-directory', $EvidenceDirectory, '--timeout-seconds', [string]$TimeoutSeconds,
    '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
