npm --prefix web ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm --prefix web run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm --prefix desktop ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
