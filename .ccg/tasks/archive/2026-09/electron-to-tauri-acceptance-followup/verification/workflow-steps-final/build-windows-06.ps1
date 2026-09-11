$versionJson = python desktop/scripts/version.py --check --json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$version = ($versionJson | ConvertFrom-Json).version
$releaseTag = "v$version"
if ($env:GITHUB_REF_TYPE -eq 'tag') { $releaseTag = $env:GITHUB_REF_NAME }
elseif ($env:REQUESTED_RELEASE_TAG) { $releaseTag = $env:REQUESTED_RELEASE_TAG }
python desktop/scripts/version.py --check --tag $releaseTag
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
"APP_VERSION=$version" >> $env:GITHUB_ENV
"RELEASE_TAG=$releaseTag" >> $env:GITHUB_ENV
New-Item -ItemType Directory -Force desktop/release/verification | Out-Null
$versionJson | Set-Content desktop/release/verification/version.json -Encoding utf8
