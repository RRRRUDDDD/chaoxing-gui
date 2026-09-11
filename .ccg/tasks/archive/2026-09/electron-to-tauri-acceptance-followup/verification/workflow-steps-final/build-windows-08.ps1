python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$requirements = Get-Content requirements.txt | Where-Object { $_ -notmatch '^\s*paddleocr\s*' }
$requirements | Set-Content "$env:RUNNER_TEMP\requirements-packaging.txt"
python -m pip install -r "$env:RUNNER_TEMP\requirements-packaging.txt"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pip install pyinstaller==6.21.0
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
