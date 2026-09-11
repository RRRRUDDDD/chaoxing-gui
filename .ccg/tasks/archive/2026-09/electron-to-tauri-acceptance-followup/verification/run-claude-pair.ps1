param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [ValidateRange(30, 1200)][int]$TimeoutSeconds = 300,
    [string]$RepoRoot
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $RepoRoot) {
    $RepoRoot = git -C $PSScriptRoot rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve repository root' }
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$wrapperPath = 'C:/Users/RUD/.claude/bin/codeagent-wrapper.exe'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
public sealed class ReviewProcessJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long UserTime, JobTime; public uint Flags;
        public UIntPtr MinSet, MaxSet; public uint Active; public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Io { public ulong R1, R2, R3, R4, R5, R6; }
    [StructLayout(LayoutKind.Sequential)] struct Limits {
        public Basic Basic; public Io Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr h, int cls, ref Limits info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr h, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    IntPtr handle;
    public ReviewProcessJob(Process process) {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception();
        var limits = new Limits(); limits.Basic.Flags = 0x2000;
        if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)) || !AssignProcessToJobObject(handle, process.Handle)) {
            int error = Marshal.GetLastWin32Error(); Dispose(); throw new Win32Exception(error);
        }
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
}
'@
$running = [System.Collections.Generic.List[object]]::new()
$allSucceeded = $true
try {
    foreach ($lane in @('a', 'b')) {
        $prefix = Join-Path $taskRoot "research/$Stage-$lane"
        if (Test-Path -LiteralPath "$prefix.result.json") { throw "Evidence already exists: $prefix" }
        $prompt = Get-Content -LiteralPath "$prefix.prompt.md" -Raw
        $info = [System.Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $wrapperPath
        $info.WorkingDirectory = $RepoRoot
        foreach ($arg in @('--progress', '--backend', 'claude', '-', $RepoRoot)) { $info.ArgumentList.Add($arg) }
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
        $info.Environment['CODEX_TIMEOUT'] = [string]($TimeoutSeconds * 1000)
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $info
        $run = [pscustomobject]@{
            Process=$process; Job=$null; Prefix=$prefix; Lane=$lane; Started=[DateTime]::UtcNow
            OutFile=$null; ErrFile=$null; OutTask=$null; ErrTask=$null; Done=$false; StartedProcess=$false
        }
        $running.Add($run)
        [void]$process.Start()
        $run.StartedProcess = $true
        $run.Job = [ReviewProcessJob]::new($process)
        $run.OutFile = [System.IO.File]::Create("$prefix.stdout.md")
        $run.ErrFile = [System.IO.File]::Create("$prefix.stderr.log")
        $run.OutTask = $process.StandardOutput.BaseStream.CopyToAsync($run.OutFile)
        $run.ErrTask = $process.StandardError.BaseStream.CopyToAsync($run.ErrFile)
        $process.StandardInput.Write($prompt)
        $process.StandardInput.Close()
        Write-Output "Started $Stage-$lane PID=$($process.Id), deadline=${TimeoutSeconds}s, process-tree Job active"
    }
    while (@($running | Where-Object { -not $_.Done }).Count -gt 0) {
        foreach ($run in @($running | Where-Object { -not $_.Done })) {
            $timedOut = ([DateTime]::UtcNow - $run.Started).TotalSeconds -ge $TimeoutSeconds -and -not $run.Process.HasExited
            if ($timedOut) {
                $run.Process.Kill($true)
                $run.Job.Dispose()
                if (-not $run.Process.WaitForExit(5000)) { throw "Review process failed to stop: $($run.Process.Id)" }
            }
            if ($run.Process.HasExited) {
                $run.Job.Dispose()
                if (-not $run.OutTask.Wait(5000) -or -not $run.ErrTask.Wait(5000)) { throw 'Review output drain timed out' }
                $run.OutFile.Dispose()
                $run.ErrFile.Dispose()
                $hasOutput = (Get-Item -LiteralPath "$($run.Prefix).stdout.md").Length -gt 0
                $ok = -not $timedOut -and $run.Process.ExitCode -eq 0 -and $hasOutput
                $result = [ordered]@{
                    stage=$Stage; lane=$run.Lane; repoRoot=$RepoRoot; pid=$run.Process.Id
                    command='codeagent-wrapper.exe --progress --backend claude - <repoRoot>'
                    startedAt=$run.Started.ToString('o'); endedAt=[DateTime]::UtcNow.ToString('o')
                    timeoutSeconds=$TimeoutSeconds; timedOut=($timedOut -or $run.Process.ExitCode -eq 124)
                    exitCode=$run.Process.ExitCode; stdoutPresent=$hasOutput
                    invocationSucceeded=$ok; reviewPassed=$false
                    conclusion=$(if ($ok) { 'Report requires human/lead interpretation' } else { 'No passing review: invocation failed or empty report' })
                    processTreeCleanup='KILL_ON_JOB_CLOSE disposed; output streams drained'
                }
                $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$($run.Prefix).result.json" -Encoding utf8
                Write-Output ($result | ConvertTo-Json -Compress)
                $run.Done = $true
                if (-not $ok) { $allSucceeded = $false }
            }
        }
        if (@($running | Where-Object { -not $_.Done }).Count -gt 0) { Start-Sleep -Milliseconds 250 }
    }
} finally {
    foreach ($run in $running) {
        if ($run.StartedProcess -and -not $run.Process.HasExited) {
            $run.Process.Kill($true)
            [void]$run.Process.WaitForExit(5000)
        }
        if ($run.Job) { $run.Job.Dispose() }
        if ($run.OutFile) { $run.OutFile.Dispose() }
        if ($run.ErrFile) { $run.ErrFile.Dispose() }
        $run.Process.Dispose()
    }
}
if (-not $allSucceeded) { exit 1 }
