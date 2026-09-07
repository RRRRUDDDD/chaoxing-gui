# Read-only extraction of one known installer payload file; never execute it.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (& git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate repository' }
$scratch = Join-Path $PSScriptRoot ('nsis-host-diagnostic-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($scratch)
$sevenZip = (Get-Command 7z.exe -CommandType Application | Select-Object -First 1).Source
$setup = Join-Path $repo 'desktop/release/tauri/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe'
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = $sevenZip
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
foreach ($arg in @('x', '-y', '-sccUTF-8', "-o$scratch", $setup, 'chaoxing-gui-tauri.exe')) { $info.ArgumentList.Add($arg) }
$proc = [Diagnostics.Process]::new()
$proc.StartInfo = $info
$started = $false
try {
    [void]$proc.Start()
    $started = $true
    $out = $proc.StandardOutput.ReadToEndAsync()
    $err = $proc.StandardError.ReadToEndAsync()
    $proc.StandardInput.Close()
    if (-not $proc.WaitForExit(120000)) { $proc.Kill($true); [void]$proc.WaitForExit(5000); throw 'Archive extraction timed out' }
    if (-not $out.Wait(5000) -or -not $err.Wait(5000)) { throw 'Archive output drain timed out' }
    $out.Result | Set-Content -LiteralPath (Join-Path $scratch 'extract.stdout.txt') -Encoding utf8
    $err.Result | Set-Content -LiteralPath (Join-Path $scratch 'extract.stderr.txt') -Encoding utf8
    if ($proc.ExitCode -ne 0) { throw "Archive extraction failed: $($proc.ExitCode)" }
} finally {
    if ($started -and -not $proc.HasExited) { $proc.Kill($true); [void]$proc.WaitForExit(5000) }
    $proc.Dispose()
}
$scratch
