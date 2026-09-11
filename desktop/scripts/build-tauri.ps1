[CmdletBinding()]
param(
    # CI prepares/tests Python and Node in dedicated steps before this entry.
    [switch]$Prepared,
    [string]$CertificateThumbprint = $env:CHAOXING_SIGN_CERT_THUMBPRINT
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'package-common.ps1')
. (Join-Path $PSScriptRoot 'nsis-content.ps1')
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$crate = Join-Path $repoRoot 'desktop/src-tauri'
$output = Join-Path $repoRoot 'desktop/release/tauri'
$target = 'x86_64-pc-windows-msvc'

function Invoke-BuildCommand {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Push-Location -LiteralPath $repoRoot
try {
    $versionJson = & python (Join-Path $PSScriptRoot 'version.py') --check --json
    if ($LASTEXITCODE -ne 0) { throw "Release version check failed: $versionJson" }
    $version = ($versionJson | ConvertFrom-Json).version
    $rustVersion = & rustc --version
    if ($LASTEXITCODE -ne 0) { throw 'Rust is unavailable; initialize the documented toolchain first' }
    if ($rustVersion -notmatch '^rustc 1\.95\.0 ') { throw "Expected Rust 1.95.0, found $rustVersion" }
    New-Item -ItemType Directory -Path $output -Force | Out-Null

    if (-not $Prepared) {
        Invoke-BuildCommand npm @('--prefix', 'web', 'ci')
        Invoke-BuildCommand npm @('--prefix', 'web', 'test')
        Invoke-BuildCommand npm @('--prefix', 'web', 'run', 'build')
        Invoke-BuildCommand npm @('--prefix', 'desktop', 'ci')
        Invoke-BuildCommand npm @('--prefix', 'desktop', 'test')
        Invoke-BuildCommand python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
        Invoke-BuildCommand python @('-m', 'PyInstaller', '--clean', '--noconfirm', 'chaoxing-backend.spec')
    }
    foreach ($required in @('web/dist/index.html', 'dist/chaoxing-backend/chaoxing-backend.exe', 'desktop/node_modules/@tauri-apps/cli/tauri.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $required) -PathType Leaf)) {
            throw "Missing prepared build input: $required"
        }
    }
    $signArguments = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Inspect')
    if ($CertificateThumbprint) { $signArguments += @('-CertificateThumbprint', $CertificateThumbprint) }
    $signingJson = & pwsh @signArguments
    if ($LASTEXITCODE -ne 0) { throw "Signing availability check failed: $signingJson" }
    $signing = $signingJson | ConvertFrom-Json
    $signingJson | Set-Content -LiteralPath (Join-Path $output 'signing-availability.json') -Encoding utf8

    $backend = Join-Path $repoRoot 'dist/chaoxing-backend/chaoxing-backend.exe'
    if ($signing.signingAvailable) {
        $CertificateThumbprint = $signing.selectedThumbprint
        Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Sign', '-CertificateThumbprint', $CertificateThumbprint, '-Path', $backend)
    }
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'prepare-backend.ps1'), '-Version', $version)

    Invoke-BuildCommand cargo @('fmt', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--all', '--check')
    Invoke-BuildCommand cargo @('check', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '--all-targets', '-j', '1')
    Invoke-BuildCommand cargo @('clippy', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '--all-targets', '-j', '1', '--', '-D', 'warnings')
    Invoke-BuildCommand cargo @('test', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '-j', '1')

    $release = Join-Path $crate "target/$target/release"
    $hostExe = Join-Path $release 'chaoxing-gui-tauri.exe'
    $captureDirectory = Join-Path $crate "target/p3-signing-$([Guid]::NewGuid().ToString('N'))"
    $signedHostRecord = $null
    $arguments = @('--prefix', 'desktop', 'run', 'tauri', '--', 'build', '--ci', '--target', $target, '--bundles', 'nsis')
    if ($signing.signingAvailable) {
        # Structured argv preserves spaces in script and executable paths. Tauri
        # also invokes this for its generated uninstaller and final installer.
        [void][IO.Directory]::CreateDirectory($captureDirectory)
        $signedHostRecord = Join-Path $captureDirectory 'nsis-host.json'
        $signConfig = Join-Path $captureDirectory 'tauri-signing.config.json'
        $configuration = @{ bundle=@{ windows=@{ signCommand=@{
            cmd=(Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
            args=@('-NoProfile', '-File', (Join-Path $PSScriptRoot 'bundle-sign.ps1'), '-CertificateThumbprint', $CertificateThumbprint,
                '-ExpectedHostPath', $hostExe, '-HostRecordPath', $signedHostRecord,
                '-BackendDirectory', (Join-Path $crate 'resources/backend'), '-BackendManifestPath', (Join-Path $crate 'resources/backend-manifest.json'), '-Path', '%1')
        } } } }
        $configuration | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $signConfig -Encoding utf8
        $arguments += @('--config', $signConfig)
    }
    # Keep integration helper binaries out of both Cargo's release build and
    # Tauri's required-features-aware bundle enumeration.
    $arguments += @('--', '--locked', '-j', '1', '--no-default-features')
    $started = Get-Date
    Invoke-BuildCommand npm $arguments

    # A fresh machine may obtain makensis only during its first Tauri bundle.
    # Run the real Win32 path fixtures now and forbid a missing-tool skip.
    $previousNsisRequirement = $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS
    try {
        $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS = '1'
        Invoke-BuildCommand node @('--test', (Join-Path $repoRoot 'desktop/tests/nsis-paths.test.mjs'))
    } finally {
        if ($null -eq $previousNsisRequirement) { Remove-Item Env:CHAOXING_REQUIRE_NSIS_PATH_TESTS -ErrorAction SilentlyContinue }
        else { $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS = $previousNsisRequirement }
    }

    if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf)) { throw 'Tauri did not produce the independently named host executable' }
    $hostVersion = (Get-Item -LiteralPath $hostExe).VersionInfo.ProductVersion
    if ($hostVersion -notin @($version, "$version.0")) { throw "Host PE product version $hostVersion differs from $version" }
    $installers = @(Get-ChildItem -LiteralPath (Join-Path $release 'bundle/nsis') -File -Filter '*.exe' | Where-Object { $_.LastWriteTime -ge $started })
    if ($installers.Count -ne 1) { throw "Expected one newly built NSIS installer, found $($installers.Count)" }
    $setup = Join-Path $output "chaoxing-gui-tauri-setup-$version-windows-x64.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $setup -Force
    # The bundler restores the unsigned UNK host after packaging the NSS host.
    # Derive the expected NSIS bytes independently; signed captures must match
    # that exact pre-sign hash. Sign the restored portable host separately.
    $hostExpectation = Get-NsisHostExpectation -HostPath $hostExe -SignedHostRecordPath $signedHostRecord
    if ($signing.signingAvailable) {
        Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Sign', '-CertificateThumbprint', $CertificateThumbprint, '-Path', $hostExe)
    }
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'package-portable.ps1'), '-HostPath', $hostExe, '-OutputDirectory', $output, '-Version', $version)
    $portable = Join-Path $output "chaoxing-gui-tauri-portable-$version-windows-x64.zip"
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'verify-package.ps1'), '-PackagePath', $portable, '-Version', $version)
    Write-NsisArtifactManifest -InstallerPath $setup -PortablePath $portable -HostExpectation $hostExpectation
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'verify-nsis.ps1'), '-InstallerPath', $setup, '-PortablePath', $portable, '-EvidenceDirectory', (Join-Path $repoRoot 'desktop/release/verification/nsis-content'))
    Invoke-BuildCommand python @((Join-Path $PSScriptRoot 'version.py'), '--check', '--artifacts', $output)

    # Call the script in this PowerShell process so Path remains an actual array.
    $verifyParameters = @{ Mode='Verify'; Path=@($hostExe, (Join-Path $crate 'resources/backend/chaoxing-backend.exe'), $setup); ReportPath=(Join-Path $output 'signatures.json') }
    if ($CertificateThumbprint) { $verifyParameters.CertificateThumbprint = $CertificateThumbprint }
    & (Join-Path $PSScriptRoot 'sign-windows.ps1') @verifyParameters

    $artifactFiles = @($setup, "$setup.manifest.json", $portable, "$portable.manifest.json", "$portable.sha256")
    $artifactManifest = [ordered]@{
        version=$version; target=$target; rust=$rustVersion; tauri='2.11.5'; signed=[bool]$signing.signingAvailable
        builtAt=(Get-Date).ToUniversalTime().ToString('o')
        artifacts=@(foreach ($file in $artifactFiles) {
            [ordered]@{ name=[System.IO.Path]::GetFileName($file); bytes=(Get-Item -LiteralPath $file).Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
        })
        acceptance='Build and content checks only; release host acceptance requires disposable Windows profile smoke'
    }
    $artifactManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output "chaoxing-gui-tauri-artifacts-$version-windows-x64.json") -Encoding utf8
    $artifactManifest.artifacts | ForEach-Object { "$($_.sha256)  $($_.name)" } | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
    Write-Output "Tauri $version build and content checks completed: $output"
} finally {
    Pop-Location
}
