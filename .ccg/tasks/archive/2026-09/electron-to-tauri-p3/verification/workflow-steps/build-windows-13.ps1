pwsh -NoProfile -File desktop/scripts/smoke-python.ps1 -ExecutablePath dist/chaoxing-backend/chaoxing-backend.exe -Mode Backend -EvidenceDirectory desktop/release/verification/python-backend
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
