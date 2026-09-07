python -m PyInstaller --noconfirm --onedir --name p2-backend --specpath desktop/src-tauri/target/p3-fixture --workpath desktop/src-tauri/target/p3-fixture/build --distpath desktop/src-tauri/target/p3-fixture/dist desktop/tests/fixtures/p2_backend.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked -j 1 --features custom-protocol --bin chaoxing-desktop
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
