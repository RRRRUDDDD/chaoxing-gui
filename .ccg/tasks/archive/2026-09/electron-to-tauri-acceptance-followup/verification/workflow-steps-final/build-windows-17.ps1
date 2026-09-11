pwsh -NoProfile -File desktop/scripts/verify-nsis.ps1 -InstallerPath "desktop/release/tauri/chaoxing-gui-tauri-setup-$env:APP_VERSION-windows-x64.exe" -PortablePath "desktop/release/tauri/chaoxing-gui-tauri-portable-$env:APP_VERSION-windows-x64.zip" -EvidenceDirectory desktop/release/verification/nsis-content
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
