#Requires -Version 5.1
# Keep this file ASCII: Windows PowerShell 5.1 reads scripts using the system code page.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-PortableProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string]$Arguments = '')
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $Arguments
    $info.WorkingDirectory = Split-Path -Parent $FilePath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    return $process
}

function Stop-PortableProcessTree {
    param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)
    if ($Process.HasExited) { return }
    # Framework PowerShell lacks Process.Kill(entireProcessTree). Use the Windows
    # system tool directly, without a shell or a visible helper window.
    $killer = New-PortableProcess -FilePath (Join-Path $env:SystemRoot 'System32/taskkill.exe') -Arguments "/PID $($Process.Id) /T /F"
    $started = $false
    try {
        $started = $killer.Start()
        if (-not $started) { throw 'Could not start process-tree cleanup.' }
        $killer.StandardInput.Close()
        $outTask = $killer.StandardOutput.ReadToEndAsync()
        $errTask = $killer.StandardError.ReadToEndAsync()
        if (-not $killer.WaitForExit(10000)) {
            $killer.Kill()
            if (-not $killer.WaitForExit(5000)) { throw 'Process-tree cleanup timed out.' }
        }
        if (-not $Process.WaitForExit(5000)) { throw 'Process-tree cleanup failed.' }
        if (-not $outTask.Wait(2000) -or -not $errTask.Wait(2000)) { throw 'Process-tree cleanup output did not close.' }
        if ($killer.ExitCode -ne 0 -and -not $Process.HasExited) { throw "Process-tree cleanup exited $($killer.ExitCode)." }
    } finally {
        if ($started -and -not $killer.HasExited) { $killer.Kill(); [void]$killer.WaitForExit(5000) }
        $killer.Dispose()
        if (-not $Process.HasExited) { $Process.Kill(); [void]$Process.WaitForExit(5000) }
    }
}

function Invoke-PortableProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string]$Arguments = '',
        [ValidateRange(1, 1800)][int]$TimeoutSeconds = 15)
    $process = New-PortableProcess -FilePath $FilePath -Arguments $Arguments
    $started = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw "Could not start: $FilePath" }
        # A genuine stdin pipe, closed to EOF; no inherited terminal or file handle.
        $process.StandardInput.Close()
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw "Process timed out after $TimeoutSeconds seconds: $FilePath" }
        if (-not $outTask.Wait(2000) -or -not $errTask.Wait(2000)) { throw 'Process output pipes did not close.' }
        return @{ ExitCode = $process.ExitCode; Output = $outTask.Result; Error = $errTask.Result }
    } finally {
        try { if ($started -and -not $process.HasExited) { Stop-PortableProcessTree $process } }
        finally { $process.Dispose() }
    }
}

function Assert-PortablePlainPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a symbolic link or junction: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
    }
}
