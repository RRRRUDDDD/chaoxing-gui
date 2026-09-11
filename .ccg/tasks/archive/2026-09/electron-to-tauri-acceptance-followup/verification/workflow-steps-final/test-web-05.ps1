foreach ($source in @('desktop/main.js', 'desktop/preload.js', 'desktop/session-store.js', 'desktop/scripts/p3-smoke.mjs', 'desktop/scripts/p3-installation.mjs')) {
  node --check $source
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
