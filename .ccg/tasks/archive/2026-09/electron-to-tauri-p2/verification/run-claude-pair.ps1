param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [int]$TimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $taskRoot '../../..')).Path
$wrapperPath = 'C:/Users/RUD/.claude/bin/codeagent-wrapper.exe'
$running = @()
foreach ($lane in @('a', 'b')) {
    $prefix = Join-Path $taskRoot "research/$Stage-$lane"
    $prompt = Get-Content -LiteralPath "$prefix.prompt.md" -Raw
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $wrapperPath
    $info.WorkingDirectory = $repoRoot
    foreach ($arg in @('--progress', '--backend', 'claude', '-', $repoRoot)) { $info.ArgumentList.Add($arg) }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
    $info.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $info.Environment['CODEX_TIMEOUT'] = [string]($TimeoutSeconds * 1000)
    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $info
    $started = [DateTime]::UtcNow
    [void]$proc.Start()
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $proc.StandardInput.Write($prompt)
    $proc.StandardInput.Close()
    $running += [pscustomobject]@{ Process=$proc; Prefix=$prefix; Started=$started; Out=$outTask; Err=$errTask; Done=$false }
    Write-Output "Started $Stage-$lane pid=$($proc.Id), timeout=$TimeoutSeconds seconds"
}
while ($running.Where({ -not $_.Done }).Count -gt 0) {
    foreach ($run in $running.Where({ -not $_.Done })) {
        $elapsed = ([DateTime]::UtcNow - $run.Started).TotalSeconds
        $timedOut = $elapsed -ge $TimeoutSeconds -and -not $run.Process.HasExited
        if ($timedOut) { $run.Process.Kill($true); [void]$run.Process.WaitForExit(5000) }
        if ($run.Process.HasExited) {
            $run.Out.GetAwaiter().GetResult() | Set-Content -LiteralPath "$($run.Prefix).stdout.md" -Encoding utf8
            $run.Err.GetAwaiter().GetResult() | Set-Content -LiteralPath "$($run.Prefix).stderr.log" -Encoding utf8
            $result = [ordered]@{ stage=$Stage; lane=(Split-Path -Leaf $run.Prefix); startedAt=$run.Started.ToString('o'); endedAt=[DateTime]::UtcNow.ToString('o'); timeoutSeconds=$TimeoutSeconds; timedOut=$timedOut -or $run.Process.ExitCode -eq 124; exitCode=$run.Process.ExitCode; success=(-not $timedOut -and $run.Process.ExitCode -eq 0) }
            $result | ConvertTo-Json | Set-Content -LiteralPath "$($run.Prefix).result.json" -Encoding utf8
            Write-Output ($result | ConvertTo-Json -Compress)
            $run.Done = $true
            $run.Process.Dispose()
        }
    }
    if ($running.Where({ -not $_.Done }).Count -gt 0) { Start-Sleep -Milliseconds 500 }
}
