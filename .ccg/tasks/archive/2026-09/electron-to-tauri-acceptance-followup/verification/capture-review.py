"""Capture complete source bytes for a bounded external review, never only git diff."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("stage")
args = parser.parse_args()
if not args.stage or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in args.stage):
    raise SystemExit("Unsafe stage name")
task = Path(__file__).resolve().parents[1]
root = Path(subprocess.check_output(["git", "-C", str(task), "rev-parse", "--show-toplevel"], text=True, timeout=15).strip())
research = task / "research"
manifest_path = research / f"{args.stage}-source-manifest.json"
snapshot_path = research / f"{args.stage}-source-snapshot.md"
if manifest_path.exists() or snapshot_path.exists():
    raise SystemExit("Review evidence exists; choose a fresh stage name")

names = {
    ".github/workflows/main.yml", ".gitignore", "README.md", "desktop/README.md", "build_tauri.bat",
    "pyproject.toml", "requirements.txt", "requirements-test.txt", "chaoxing.spec", "chaoxing-backend.spec",
    "app.py", "api/desktop_runtime.py", "api/logger.py", "web/vite.config.js", "web/package.json", "web/package-lock.json",
    "desktop/main.js", "desktop/preload.js", "desktop/session-store.js",
    "desktop/package.json", "desktop/package-lock.json", "desktop/electron-builder.yml",
    "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock", "desktop/src-tauri/build.rs",
    "desktop/src-tauri/tauri.conf.json", "desktop/rust-toolchain.toml", "tests/test_release_version.py",
    "tests/test_desktop_runtime.py", "tests/test_tauri_entry_contract.py",
    ".ccg/spec/backend/index.md", ".ccg/spec/frontend/index.md",
}
for folder, patterns in {
    "desktop/src-tauri/src": ["**/*.rs"],
    "desktop/src-tauri/tests": ["**/*.rs"],
    "desktop/src-tauri/capabilities": ["*.json"],
    "desktop/src-tauri/windows": ["*"],
    "desktop/scripts": ["*.ps1", "*.mjs", "*.py"],
    "desktop/tests": ["*.mjs", "*.js"],
    "desktop/tests/fixtures": ["p3-*", "nsis-paths.nsi", "p2_backend.py"],
    "desktop/portable": ["*"],
    "web/src": ["**/*.js", "**/*.jsx", "**/*.css"],
    "web/src/api": ["*.js"],
    "web/src/lib": ["desktopBridge*", "sessionStore*"],
    "web/src/components": ["DesktopStartup*"],
}.items():
    for pattern in patterns:
        names.update(path.relative_to(root).as_posix() for path in (root / folder).glob(pattern) if path.is_file())

tracked = set(subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], timeout=15).decode("utf-8").split("\0"))
names.update(path.relative_to(root).as_posix() for pattern in ("*.ps1", "*.py", "*.mjs")
             for path in (task / "verification").glob(pattern) if path.is_file())
records = []
with snapshot_path.open("x", encoding="utf-8", newline="\n") as snapshot:
    snapshot.write("# Current P0-P3 sources, follow-up verification helpers and local specifications\n\n")
    for name in sorted(names):
        path = root / name
        contents = path.read_bytes()
        digest = hashlib.sha256(contents).hexdigest()
        lockfile = name.endswith(("package-lock.json", "Cargo.lock"))
        records.append({"path": name, "bytes": len(contents), "sha256": digest, "trackedAtCapture": name in tracked,
                        "bodyIncluded": not lockfile})
        snapshot.write(f"\n## {name}\n\nSHA256: {digest}\n\n")
        if lockfile:
            snapshot.write("Lock file retained on disk at the exact repository path; hash recorded above.\n")
        else:
            snapshot.write("````text\n" + contents.decode("utf-8-sig") + "\n````\n")
manifest = {"capturedAt": datetime.now(timezone.utc).isoformat(), "head": subprocess.check_output(
    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=15).strip(),
    "stage": args.stage, "repoRoot": str(root), "files": records,
    "protectedPlanSha256": hashlib.sha256((root / ".ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md").read_bytes()).hexdigest(),
    "snapshotSha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest()}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"snapshot": str(snapshot_path), "manifest": str(manifest_path), "files": len(records)}, ensure_ascii=False))
