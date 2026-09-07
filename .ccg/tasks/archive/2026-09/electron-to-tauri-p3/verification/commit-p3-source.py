"""Commit only the enumerated, reviewed P3 files; preserve unrelated work."""
from pathlib import Path
import hashlib
import json
import subprocess
from datetime import datetime, timezone

root = Path.cwd()
task = Path(__file__).resolve().parents[1]
paths = """
.github/workflows/main.yml
.gitignore
README.md
desktop/README.md
desktop/package-lock.json
desktop/package.json
desktop/scripts/dev-env.ps1
desktop/src-tauri/Cargo.toml
desktop/src-tauri/src/main.rs
desktop/src-tauri/src/windows_job.rs
desktop/src-tauri/tauri.conf.json
build_tauri.bat
desktop/portable/Install-WebView2.cmd
desktop/portable/Install-WebView2.ps1
desktop/portable/Portable-Common.ps1
desktop/portable/README.txt
desktop/portable/Start-Chaoxing.cmd
desktop/portable/Start-Chaoxing.ps1
desktop/scripts/build-tauri.ps1
desktop/scripts/bundle-sign.ps1
desktop/scripts/nsis-content.ps1
desktop/scripts/p3-installation.mjs
desktop/scripts/p3-smoke.mjs
desktop/scripts/package-common.ps1
desktop/scripts/package-portable.ps1
desktop/scripts/prepare-backend.ps1
desktop/scripts/sign-windows.ps1
desktop/scripts/smoke-installation.ps1
desktop/scripts/smoke-python.ps1
desktop/scripts/smoke-tauri.ps1
desktop/scripts/verify-nsis.ps1
desktop/scripts/verify-package.ps1
desktop/scripts/version.py
desktop/src-tauri/src/webview_runtime.rs
desktop/src-tauri/tests/nested_job.rs
desktop/src-tauri/windows/LICENSE_MIT
desktop/src-tauri/windows/UPSTREAM.md
desktop/src-tauri/windows/installer-hooks.nsh
desktop/src-tauri/windows/installer.nsi
desktop/tests/fixtures/nsis-paths.nsi
desktop/tests/fixtures/p3-process-child.mjs
desktop/tests/fixtures/p3-title-window.ps1
desktop/tests/fixtures/p3-windows-process.cs
desktop/tests/fixtures/p3-windows-process.ps1
desktop/tests/installation.test.mjs
desktop/tests/nsis-paths.test.mjs
desktop/tests/nsis.test.mjs
desktop/tests/p3-smoke.test.mjs
desktop/tests/packaging.test.mjs
desktop/tests/signing.test.mjs
tests/test_release_version.py
""".strip().splitlines()
protected = '.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md'
expected_hash = 'f7ec970826f014e9975a056b2e73ee9bee5dd7323f75b06db79fb7b78cdb5167'

def git(*args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, timeout=120)

assert git('branch', '--show-current').stdout.strip() == b'main'
assert git('rev-parse', 'HEAD').stdout.strip() == b'0d224f38f352f5822dfc31639df13b7b8d073675'
assert not git('diff', '--cached', '--name-only').stdout.strip(), 'Index is not empty'
assert hashlib.sha256((root/protected).read_bytes()).hexdigest() == expected_hash
status = git('status', '--porcelain=v1', '--untracked-files=all', '-z').stdout.decode('utf-8')
changed = {entry[3:] for entry in status.split('\0') if entry}
assert changed == set(paths) | {protected, 'poc-window.png'}, sorted(changed ^ (set(paths) | {protected, 'poc-window.png'}))
snapshot = json.loads((task/'research/p3-final-review-source-manifest.json').read_text(encoding='utf-8'))
for entry in snapshot['files']:
    assert hashlib.sha256((root/entry['path']).read_bytes()).hexdigest() == entry['sha256'], entry['path']
assert set(paths) <= {entry['path'] for entry in snapshot['files']}, 'Source missing from final review snapshot'

added = git('add', '--', *paths)
(task/'verification/p3-source-stage-stderr.txt').write_bytes(added.stderr)
staged = set(git('diff', '--cached', '--name-only', '-z').stdout.decode('utf-8').strip('\0').split('\0'))
assert staged == set(paths), sorted(staged ^ set(paths))
git('diff', '--cached', '--check')
(task/'verification/p3-source-staged-stat.txt').write_bytes(git('diff', '--cached', '--stat').stdout)
message = task/'verification/p3-commit-message.txt'
message.write_text('feat(desktop): add Tauri Windows packaging and CI acceptance gates\n\n'
                   'Stage complete backend resources, build independent NSIS/portable artifacts,\n'
                   'verify per-artifact host and resource hashes, and gate CI on real host smoke.\n'
                   'Keep release acceptance and external reviews pending where not executed.\n', encoding='utf-8')
result = git('commit', '-F', str(message))
(task/'verification/p3-source-commit-output.txt').write_bytes(result.stdout + result.stderr)
commit = git('rev-parse', 'HEAD').stdout.decode().strip()
assert hashlib.sha256((root/protected).read_bytes()).hexdigest() == expected_hash
report = {
    'recordedAt': datetime.now(timezone.utc).isoformat(), 'sourceCommit': commit,
    'foundationCommits': ['9219fe24c25151af12c6c79a986b86018cd6a2ef', '0d224f38f352f5822dfc31639df13b7b8d073675'],
    'paths': paths, 'protectedPlanStaged': False, 'protectedPlanSha256': expected_hash,
    'sourceReviewSnapshotSha256': snapshot['snapshotSha256'], 'externalReviewPassed': False,
    'remoteCiExecuted': False, 'releaseHostExecutedUnderCurrentUser': False,
}
(task/'verification/p3-source-commit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'sourceCommit':commit,'files':len(paths),'protectedPlanUnchanged':True}))
