#Requires -Version 7.0
param(
    [ValidateSet('Standard','NativeInstallationPartial')][string]$AcceptanceMode='Standard',
    [ValidateSet('NanaZip','SevenZip25')][string]$ArchiveDecoder='NanaZip'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=(git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Missing repository' }
$head=(git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve source HEAD.' }
& git -C $repo merge-base --is-ancestor 37fde9160229e99d2fa837d2f1a850203c260d81 $head
if ($LASTEXITCODE -ne 0) { throw 'Source is not based on the accepted handoff.' }
$sourceChanges=@(git -C $repo diff --name-only HEAD -- desktop web pyproject.toml LICENSE)
if ($LASTEXITCODE -ne 0 -or $sourceChanges.Count) { throw 'Commit reviewed source changes before exporting acceptance inputs.' }
$target=[IO.Path]::GetFullPath((Join-Path $repo 'desktop/src-tauri/target'))
$kit=Join-Path $target ('acceptance-followup-kit-' + [Guid]::NewGuid().ToString('N'))
if (-not $kit.StartsWith($target + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid kit path.' }
$inputDirectory=Join-Path $kit 'input'
[void][IO.Directory]::CreateDirectory($inputDirectory)
$tools=Join-Path $inputDirectory 'tools'
foreach ($directory in @($tools,(Join-Path $tools 'node'),(Join-Path $tools '7zip'),(Join-Path $inputDirectory 'artifacts'),(Join-Path $inputDirectory 'fixture'))) {
    [void][IO.Directory]::CreateDirectory($directory)
}
$node=Join-Path $env:TEMP 'chaoxing-p3-node20/node_modules/node/bin/node.exe'
$nodeVersion=(& $node --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -cne 'v20.20.0') { throw 'Node 20.20.0 is required.' }
$runtime=Join-Path $target 'acceptance-followup-tools/MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
$signature=Get-AuthenticodeSignature -LiteralPath $runtime
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Official runtime signature verification failed.' }
$sevenArchive=Join-Path $target 'acceptance-followup-tools/7z2501-x64.exe'
$sevenDirectory=Join-Path $tools '7zip'
if ($ArchiveDecoder -ceq 'NanaZip') {
    $decoderSource=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'nanazip-tool-provenance.json') -Raw | ConvertFrom-Json
    $decoderCache=Join-Path $target 'acceptance-followup-tools/nanazip-6.5.1767.0'
    if ([IO.Path]::GetFullPath($decoderSource.cacheDirectory) -ine $decoderCache) { throw 'Unexpected NanaZip cache path.' }
    foreach ($file in $decoderSource.files) {
        $name=[IO.Path]::GetFileName($file.destination)
        if ($name -cnotin @('7z.exe','NanaZip.Codecs.dll','NanaZip.Core.dll','K7Base.dll','K7User.dll')) { throw 'Unexpected decoder file.' }
        $cached=Join-Path $decoderCache $name
        if ((Get-FileHash -LiteralPath $cached).Hash.ToLowerInvariant() -cne $file.sha256) { throw 'Cached decoder hash mismatch.' }
        [IO.File]::Copy($cached,(Join-Path $sevenDirectory $name),$false)
    }
    $decoderRecord=@{kind='NanaZip';version='6.5.1767.0';provenance='verification/nanazip-tool-provenance.json';files=$decoderSource.files}
} else {
    & (Get-Command 7z.exe -CommandType Application | Select-Object -First 1).Source x -y "-o$sevenDirectory" $sevenArchive 7z.exe 7z.dll
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract standalone 7-Zip tools.' }
    $decoderRecord=@{kind='7-Zip';version='25.01';downloadUrl='https://www.7-zip.org/a/7z2501-x64.exe';sha256=(Get-FileHash -LiteralPath $sevenArchive).Hash.ToLowerInvariant()}
}
& (Join-Path $sevenDirectory '7z.exe') i > (Join-Path $PSScriptRoot '7zip-portable-version.txt')
if ($LASTEXITCODE -ne 0) { throw 'Transferred 7-Zip executable cannot run independently.' }
Copy-Item -LiteralPath $node -Destination (Join-Path $tools 'node/node.exe')
Copy-Item -LiteralPath $PSHOME -Destination (Join-Path $tools 'pwsh') -Recurse
Copy-Item -LiteralPath (Join-Path $repo 'desktop/node_modules/playwright-core') -Destination (Join-Path $tools 'playwright-core') -Recurse
Copy-Item -LiteralPath $runtime -Destination (Join-Path $tools 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe')
Copy-Item -LiteralPath (Join-Path $target 'acceptance-followup-fixture/dist/p2-backend') -Destination (Join-Path $inputDirectory 'fixture/p2-backend') -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-guest.ps1') -Destination $inputDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-bootstrap.ps1') -Destination $inputDirectory
@{mode=$AcceptanceMode;fullAcceptancePassed=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $inputDirectory 'acceptance-mode.json') -Encoding utf8
if ($AcceptanceMode -ceq 'NativeInstallationPartial') {
    $nativeDirectory=Join-Path $inputDirectory 'native'
    [void][IO.Directory]::CreateDirectory($nativeDirectory)
    $nativeModule=Join-Path $nativeDirectory 'p3-installation-native-partial.mjs'
    $preparation=& python (Join-Path $PSScriptRoot 'prepare-native-installation.py') --source (Join-Path $repo 'desktop/scripts/p3-installation.mjs') --output $nativeModule
    if ($LASTEXITCODE -ne 0) { throw 'Native supplemental installation preparation failed.' }
    $prepared=($preparation -join [Environment]::NewLine) | ConvertFrom-Json
    if ($prepared.fullAcceptancePassed -ne $false -or $prepared.scope -cne 'native-installation-partial') { throw 'Incorrect supplemental preparation scope.' }
    $prepared | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $nativeDirectory 'preparation.json') -Encoding utf8
    & $node --check $nativeModule
    if ($LASTEXITCODE -ne 0) { throw 'Node 20 supplemental module syntax failed.' }
}
$artifactRoot=Join-Path $repo 'desktop/release/tauri'
$expected=@{
    'chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip'='3318ca14cf4c20818f69fedded0f52b479ab542003cc12625e19398f7b993e76'
    'chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe'='70c18ac432d6e05dff8eea4680b1a3f3c9c1b840370c4460202865d8446ddc09'
}
foreach ($name in $expected.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $artifactRoot $name)).Hash.ToLowerInvariant() -cne $expected[$name]) { throw "P3 artifact changed: $name" }
}
foreach ($file in Get-ChildItem -LiteralPath $artifactRoot -File) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $inputDirectory 'artifacts')
}
$sourceZip=Join-Path $inputDirectory 'source.zip'
& git -C $repo -c core.autocrlf=false -c core.eol=lf archive --format=zip "--output=$sourceZip" $head desktop web pyproject.toml LICENSE
if ($LASTEXITCODE -ne 0) { throw 'Could not export committed acceptance source.' }
if ($AcceptanceMode -ceq 'NativeInstallationPartial') {
    $sourceArchive=[IO.Compression.ZipFile]::OpenRead($sourceZip)
    $sourceStream=$null
    $sourceHasher=[Security.Cryptography.SHA256]::Create()
    try {
        $sourceStream=$sourceArchive.GetEntry('desktop/scripts/p3-installation.mjs').Open()
        $exportedHash=[Convert]::ToHexString($sourceHasher.ComputeHash($sourceStream)).ToLowerInvariant()
        if ($exportedHash -cne $prepared.sourceSha256) { throw 'Exported installation source differs from the pinned preparation input.' }
    } finally {
        if ($sourceStream) { $sourceStream.Dispose() }
        $sourceHasher.Dispose()
        $sourceArchive.Dispose()
    }
}
$manifest=[ordered]@{schemaVersion=1;baselineCommit='37fde9160229e99d2fa837d2f1a850203c260d81';sourceCommit=$head;sourcePaths=@('desktop','web','pyproject.toml','LICENSE');createdAt=[DateTime]::UtcNow.ToString('o');files=@()}
foreach ($file in Get-ChildItem -LiteralPath $inputDirectory -File -Recurse) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked kit file: $($file.FullName)" }
    $manifest.files += [ordered]@{path=[IO.Path]::GetRelativePath($inputDirectory,$file.FullName).Replace('\','/');length=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant()}
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Destination (Join-Path $PSScriptRoot 'sandbox-input-manifest.json')
$metadata=[ordered]@{createdAt=[DateTime]::UtcNow.ToString('o');kitDirectory=$kit;inputDirectory=$inputDirectory;sourceCommit=$head;files=$manifest.files.Count;acceptanceMode=$AcceptanceMode;
    inputManifestSha256=(Get-FileHash -LiteralPath (Join-Path $inputDirectory 'input-manifest.json')).Hash.ToLowerInvariant();
    runtime=[ordered]@{downloadUrl='https://go.microsoft.com/fwlink/?LinkId=2124701';sha256=(Get-FileHash -LiteralPath $runtime).Hash.ToLowerInvariant();signer=$signature.SignerCertificate.Subject;signature=[string]$signature.Status};
    archiveDecoder=$decoderRecord;
    productExecutedOnHost=$false;node=$nodeVersion;powershell=[string]$PSVersionTable.PSVersion;gitArchiveConfig=@{coreAutoCrlf=$false;coreEol='lf'}}
$metadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-kit.json') -Encoding utf8
$kitId=[IO.Path]::GetFileName($kit).Substring('acceptance-followup-kit-'.Length)
$metadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot ("sandbox-kit-$kitId.json")) -Encoding utf8
Copy-Item -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Destination (Join-Path $PSScriptRoot ("sandbox-input-manifest-$kitId.json"))
$metadata | ConvertTo-Json -Depth 6
