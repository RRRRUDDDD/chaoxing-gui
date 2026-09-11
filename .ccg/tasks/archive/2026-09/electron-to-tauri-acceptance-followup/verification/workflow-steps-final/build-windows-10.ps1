pwsh -NoProfile -File desktop/scripts/smoke-python.ps1 -ExecutablePath dist/chaoxing-gui.exe -Mode Independent -EvidenceDirectory desktop/release/verification/python-independent
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
