pwsh -NoProfile -File desktop/scripts/build-tauri.ps1 -Prepared
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
