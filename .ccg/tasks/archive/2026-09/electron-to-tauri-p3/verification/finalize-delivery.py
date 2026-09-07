"""Validate existing results and prepare the scoped local-delivery archive."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import subprocess

root = Path.cwd()
task = Path(__file__).resolve().parents[1]
verification = task / 'verification'
archive_relative = '.ccg/tasks/archive/2026-09/electron-to-tauri-p3'
parent = root / '.ccg/tasks/electron-to-tauri-implementation'

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def log(name):
    return (verification / name).read_text(encoding='utf-8-sig', errors='replace')

source = read(verification / 'p3-source-commit.json')
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == source['sourceCommit']
for version in ('311', '313'):
    text = log(f'python{version}-complete-final.txt')
    assert re.search(r'Ran 160 tests', text) and re.search(r'^OK\s*$', text, re.M), version
desktop = log('node20-desktop-complete-final.txt')
assert '# pass 86' in desktop and '# fail 0' in desktop and '# skipped 0' in desktop
web = log('node20-web-final.txt')
assert '125 passed' in web and 'built in' in web
rust = log('build-tauri-marker-final.txt')
rust_counts = [int(number) for number in re.findall(r'test result: ok\. (\d+) passed;', rust)]
assert sum(rust_counts) == 114, rust_counts
assert 'Tauri 1.1.1 build and content checks completed:' in rust
debug = read(verification / 'debug-host-final.json')
assert debug['success'] and len(debug['checks']) == 23
for name in ('backend', 'independent'):
    assert read(verification / f'frozen-python/{name}.json')['success']
content = read(verification / 'nsis-content-final/result.json')
assert content['success'] and content['payloadFileCount'] == 825 and not content['installerExecuted']
workflow = read(verification / 'workflow-final-validation.json')
assert workflow['yamlParsed'] and workflow['powershellSyntaxParsed'] and len(workflow['runSteps']) == 23
external = read(verification / 'external-service-errors-final.json')
assert len(external) == 6
for entry in external:
    assert entry['exitCode'] == 1 and any('429' in error and 'Service Unavailable' in error for error in entry['errors'])
    result = read(task / f"research/{entry['stage']}-{entry['lane']}.result.json")
    assert not result['stdoutPresent'] and not result['reviewPassed']

artifact_directory = root / 'desktop/release/tauri'
artifacts = read(artifact_directory / 'chaoxing-gui-tauri-artifacts-1.1.1-windows-x64.json')
for entry in artifacts['artifacts']:
    file = artifact_directory / entry['name']
    assert file.stat().st_size == entry['bytes'] and digest(file) == entry['sha256'], file
protected = root / '.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md'
assert digest(protected) == source['protectedPlanSha256']
entry_poc = next(entry for entry in read(verification / 'entry-untracked.json') if entry['path'] == 'poc-window.png')
assert digest(root / 'poc-window.png') == entry_poc['sha256']

pending = [
    'P0/P1/P2 two-lane external review: service returned 429, no report',
    'P3 two-lane external review: service returned 429, no report',
    'GitHub Windows runner execution from these local source commits, including nested Job Objects',
    'Actual release host/NSIS installation and uninstall/portable acceptance under a disposable Windows user or VM without system Python',
    'Missing-WebView2 clean-machine online/offline runtime handling and release data-retention/rollback acceptance',
    'Real code-signing certificate success path and signed artifact/install verification',
    'P4 full clean Win10/Win11 matrix and P5 default-entry switch/Electron removal remain later phases',
]
retained = [
    {'path': protected.relative_to(root).as_posix(), 'reason': 'user /plan prefix; all bytes preserved; never staged or committed', 'sha256': digest(protected)},
    {'path': 'poc-window.png', 'reason': 'unrelated original untracked image, preserved', 'sha256': entry_poc['sha256']},
    {'path': '.ccg/spec/frontend/index.md', 'reason': 'ignored local guide with P3 feedback; scoped new guidance archived in spec-feedback.md', 'sha256': digest(root / '.ccg/spec/frontend/index.md')},
]
now = datetime.now(timezone.utc).isoformat()
results = {
    'recordedAt': now, 'sourceCommit': source['sourceCommit'], 'foundationCommits': source['foundationCommits'],
    'localImplementationDeliveryComplete': True, 'p3AcceptancePassed': False, 'overallMigrationComplete': False,
    'checks': {
        'python311': {'passed': 160, 'failed': 0}, 'python313': {'passed': 160, 'failed': 0},
        'webNode20': {'passed': 125, 'buildPassed': True},
        'desktopNode20': {'passed': 86, 'failed': 0, 'skipped': 0},
        'rust': {'passed': 114, 'intentionallyIgnoredSubprocessEntry': 1, 'fmt': True, 'check': True, 'clippy': True, 'test': True, 'releaseBuild': True},
        'realDebugHostNode20': {'passed': 23, 'syntheticFaultsBeforeFrozenBackend': True, 'nestedJob': True, 'systemPythonExcludedFromChildPath': True},
        'frozenBackend': {'passed': True}, 'independentFrozenPython': {'passed': True}, 'electronPackage': {'passed': True},
        'backendStaging': {'files': 815, 'directories': 116, 'sourceBytesPreserved': True},
        'nsis': {'applicationFiles': 825, 'allExpectedBytesVerified': True, 'installerExecuted': False},
        'portable': {'filesIncludingManifest': 826, 'completeResourcesVerified': True},
        'workflow': {'yamlParsed': True, 'runSteps': 23, 'powershellSourcesParsed': 40, 'executedRemotely': False},
        'localIndependentSourceReview': {'critical': 0, 'warning': 0, 'externalReviewReplacement': False},
    },
    'externalReviews': external, 'externalReviewPassed': False,
    'artifacts': artifacts, 'toolchains': read(verification / 'toolchain-final.json'),
    'signing': {'usableCertificateFound': False, 'artifactsSigned': False, 'realSigningSuccessValidated': False},
    'notExecuted': {'remoteCi': True, 'currentUserProductInstallerOrReleaseHost': True, 'realAccountOrLearningTask': True, 'pushOrTagOrReleasePublication': True},
    'pendingGates': pending, 'retainedChanges': retained,
    'cleanupRejection': {'paths': read(verification / 'residual-temp-readonly.json'), 'reason': 'blocked by policy', 'cleanupExecuted': False, 'bypassAttempted': False},
    'limitations': ['CDP with visible/enabled assertions and DOM click, not physical mouse acceptance',
        'Local release build used Node 24; Web/Desktop/debug-host verification separately used Node 20',
        'NSIS path preflight does not prevent subsequent concurrent path replacement',
        'Large local binaries/fixture profiles stay ignored; committed source closure is not proof of a clean-runner build'],
}
write(verification / 'final-results.json', results)

tree = subprocess.check_output(['git', 'ls-tree', '-r', '-z', 'HEAD']).decode('utf-8')
oids = {entry.split('\t', 1)[1]: entry.split('\t', 1)[0].split()[2] for entry in tree.split('\0') if entry}
manifest = {
    'schemaVersion': 1, 'recordedAt': now, 'archivePath': archive_relative,
    'sourceCommit': source['sourceCommit'], 'foundationCommits': source['foundationCommits'],
    'sourceFiles': [{'path': name, 'workingTreeSha256': digest(root / name), 'gitBlobOid': oids[name]} for name in source['paths']],
    'finalReviewSourceManifest': 'research/p3-final-review-source-manifest.json',
    'artifactDirectory': 'desktop/release/tauri', 'artifacts': artifacts['artifacts'],
    'retainedChanges': retained, 'pendingGates': pending,
    'localDeliveryComplete': True, 'externalReviewPassed': False, 'p3AcceptancePassed': False,
    'largeEvidence': {'committed': False, 'keptLocally': True, 'includes': ['compiled binaries', 'copied frozen-backend fixtures', 'WebView profiles', 'diagnostic extracted host']},
    'historicalPathMapping': {'.ccg/tasks/electron-to-tauri-p3/': archive_relative + '/'},
}
write(task / 'delivery-manifest.json', manifest)

record = read(task / 'task.json')
record.update(status='completed', currentPhase='completed', completedAt=now,
    completionScope='local implementation, build, verification evidence and delivery only',
    nextAction='Follow up through parent task: external reviews, clean runner release acceptance and real signing remain pending',
    archivePath=archive_relative, sourceCommit=source['sourceCommit'], localDeliveryComplete=True,
    acceptancePassed=False, externalReviewPassed=False, overallMigrationComplete=False, pendingGates=pending)
write(task / 'task.json', record)

record = read(parent / 'task.json')
record.update(status='in_progress', currentPhase='P3-local-delivery-archived-acceptance-pending',
    nextAction='Retry P0/P1/P2 and P3 external reviews; run these source commits on a clean Windows runner and disposable release profile before P4/P5',
    reviewStatus='P3 analysis, P0/P1/P2 remedial review and final P3 two-lane reviews all failed 429 with no reports; independent local review has no new Critical/Warning',
    verificationStatus='P3 local Rust114 Python311/313160each Web125 Desktop86 DebugHost23 release build NSIS/ZIP content checks passed; remote CI/release install/real signing pending',
    p3Task=archive_relative, p3TaskArchive=archive_relative, p3SourceCommit=source['sourceCommit'],
    p3FoundationCommits=source['foundationCommits'], p3AcceptancePassed=False, overallMigrationComplete=False)
write(parent / 'task.json', record)
write(parent / 'verification/p3-final-handoff.json', {
    'recordedAt': now, 'archivePath': archive_relative, 'sourceCommit': source['sourceCommit'],
    'foundationCommits': source['foundationCommits'], 'localDeliveryComplete': True,
    'externalReviewPassed': False, 'p3AcceptancePassed': False, 'overallMigrationComplete': False,
    'finalResults': archive_relative + '/verification/final-results.json', 'retainedChanges': retained, 'pendingGates': pending,
})
with (task / 'implementation.md').open('a', encoding='utf-8') as stream:
    stream.write('\n最终外部双路各在约191/182秒exit1，无正文，实际429 Service Unavailable；本轮6次外部调用均未通过。独立只读终审无新增Critical/Warning。P3源码51文件已提交6b2c5a2，Git树具备89个受审构建/运行/测试输入。准备归档本地交付，acceptancePassed=false，父迁移任务保持in_progress。\n')
print(json.dumps({'localDeliveryComplete': True, 'p3AcceptancePassed': False, 'externalReviewsPassed': 0, 'sourceFiles': len(source['paths']), 'protectedFilesUnchanged': True}))
