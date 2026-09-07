pwsh -NoProfile -File desktop/scripts/prepare-backend.ps1 -DestinationDirectory desktop/backend/chaoxing-backend
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm --prefix desktop exec -- electron-builder --win --publish never --projectDir desktop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
