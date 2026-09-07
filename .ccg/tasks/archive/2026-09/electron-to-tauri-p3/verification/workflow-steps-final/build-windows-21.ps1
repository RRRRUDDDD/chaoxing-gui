pwsh -NoProfile -File desktop/scripts/smoke-installation.ps1 -InstallerPath "desktop/release/tauri/chaoxing-gui-tauri-setup-$env:APP_VERSION-windows-x64.exe" -PortablePath "desktop/release/tauri/chaoxing-gui-tauri-portable-$env:APP_VERSION-windows-x64.zip" -EvidenceDirectory desktop/release/verification/tauri-installation
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
