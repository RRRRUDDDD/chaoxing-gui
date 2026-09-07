python -m PyInstaller --clean --noconfirm chaoxing.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
