# JSON-lines controller for a single captured Windows process tree.
# Standard input is the controller protocol; the child has its own real pipe.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p3Owner = $null
$p3Input = $null
try {
    # Console.ReadLine follows the inherited Windows console code page and can
    # truncate a UTF-8 JSON line at a Chinese title. Decode the pipe bytes with
    # an explicit reader instead; the Node controller always writes UTF-8.
    $p3Input = [IO.StreamReader]::new([Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false, $true), $false, 4096, $false)
    Add-Type -Path (Join-Path $PSScriptRoot 'p3-windows-process.cs')
    $p3Owner = [Chaoxing.P3Smoke.ProcessOwner]::new()
    while ($null -ne ($p3Line = $p3Input.ReadLine())) {
        $p3Request = $p3Line | ConvertFrom-Json
        try {
            $p3Result = switch ($p3Request.operation) {
                'start' {
                    $p3Spec = $p3Request.specification
                    $p3Environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
                    foreach ($p3Property in $p3Spec.env.PSObject.Properties) { $p3Environment.Add($p3Property.Name, [string]$p3Property.Value) }
                    if ($null -ne $p3Spec.PSObject.Properties['nsisTail']) {
                        $p3Owner.Start($p3Spec.executable, [string[]]$p3Spec.args, $p3Spec.cwd, $p3Environment, $p3Spec.stdoutPath, $p3Spec.stderrPath, $p3Spec.nsisTail.mode, $p3Spec.nsisTail.directory)
                    } else {
                        $p3Owner.Start($p3Spec.executable, [string[]]$p3Spec.args, $p3Spec.cwd, $p3Environment, $p3Spec.stdoutPath, $p3Spec.stderrPath)
                    }
                }
                'snapshot' { $p3Owner.Inspect() }
                'stdin-eof' { $p3Owner.CloseStdin(); $true }
                'close-window' { $p3Owner.CloseWindow([string]$p3Request.title) }
                'kill-host' { $p3Owner.KillHost(); $true }
                'kill-member' { $p3Owner.KillMember([uint32]$p3Request.pid, [string]$p3Request.createdAtFileTime); $true }
                'tcp-listener' {
                    $p3Listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort ([uint16]$p3Request.port) -State Listen -ErrorAction Stop | ForEach-Object {
                        @{ pid = [uint32]$_.OwningProcess; address = [string]$_.LocalAddress; port = [uint16]$_.LocalPort }
                    })
                    # Preserve array shape even for a single listener.
                    ,$p3Listeners
                }
                'finish' { $p3Owner.Finish() }
                default { throw "Unknown supervisor operation: $($p3Request.operation)" }
            }
            [Console]::WriteLine((@{ id = $p3Request.id; ok = $true; result = $p3Result } | ConvertTo-Json -Depth 12 -Compress))
            if ($p3Request.operation -eq 'finish') { break }
        } catch {
            [Console]::WriteLine((@{ id = $p3Request.id; ok = $false; error = $_.Exception.ToString() } | ConvertTo-Json -Depth 4 -Compress))
            if ($p3Request.operation -eq 'start') { break }
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
} finally {
    try { if ($null -ne $p3Owner) { $p3Owner.Dispose() } }
    finally { if ($null -ne $p3Input) { $p3Input.Dispose() } }
}
