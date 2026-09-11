python -m PyInstaller --clean --noconfirm chaoxing-backend.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
