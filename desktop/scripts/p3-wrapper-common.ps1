#Requires -Version 7.0
# Shared boilerplate for the smoke-*.ps1 wrappers that forward to a p3 node
# script. Dot-sourcing applies the error preference and strict mode to the
# caller; Invoke-P3NodeScript resolves node (first PATH match, asserted by
# p3-smoke.test.mjs), appends the standard --powershell-path tail argument and
# propagates the node exit code.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-P3NodeScript {
    param(
        [Parameter(Mandatory)][string]$ScriptName,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$ScriptArguments
    )
    $p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    & $p3Node (Join-Path $PSScriptRoot $ScriptName) @ScriptArguments '--powershell-path' (Join-Path $PSHOME 'pwsh.exe')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
