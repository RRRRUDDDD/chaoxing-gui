pwsh -NoProfile -File desktop/scripts/smoke-tauri.ps1 -HostPath desktop/src-tauri/target/debug/chaoxing-desktop.exe -BackendDirectory desktop/src-tauri/resources/backend -FakeBackendDirectory desktop/src-tauri/target/p3-fixture/dist/p2-backend -EvidenceDirectory desktop/release/verification/tauri-debug -Configuration Debug -Scenario All -NestedJob
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
