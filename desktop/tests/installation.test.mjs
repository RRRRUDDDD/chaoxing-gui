import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { lstat, mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import {
  parseInstallationArguments, assertInstallationPreflight, validateInstallationInputs,
  nsisSpecification, assertInstallRegistry, removeOwnedScratch, installationPowerShellScripts,
  createInstallationJunctionFixture, assertInstallationJunctionRejected, removeInstallationJunction,
  installationRetentionFixtures, assertRetainedFiles,
  cleanupChildProfileInvocation,
} from '../scripts/p3-installation.mjs';
import { windowsContext, publishReleaseProfileOwnership, releaseProfileRoots, claimProfileRoots } from '../scripts/p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temporary = async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p3-install-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
};
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function layoutFixture(t, mode) {
  const root = await temporary(t);
  const directory = path.join(root, 'layout');
  const version = JSON.parse(await readFile(path.join(repo, 'desktop/package.json'), 'utf8')).version;
  const contents = new Map([
    ['chaoxing-gui-tauri.exe', 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK; never execute'],
    ['LICENSE', 'preserved distribution license'], ['TAURI-LICENSE.txt', 'preserved Tauri license'],
    ['backend/chaoxing-backend.exe', 'inert backend; never execute'],
    ['backend/_internal/web/dist/index.html', '<script src="/assets/app.js"></script>'],
    ['backend/_internal/web/dist/assets/app.js', 'window.p3Fixture = true;'],
    ['backend/_internal/fixture-1.0.dist-info/METADATA', 'Name: fixture\nVersion: 1.0\n'],
  ]);
  const records = () => [...contents].map(([relative, bytes]) => ({ path: relative, length: Buffer.byteLength(bytes), sha256: hash(bytes) }));
  const directories = new Set();
  for (const relative of contents.keys()) {
    for (let parent = path.posix.dirname(relative); parent !== '.'; parent = path.posix.dirname(parent)) directories.add(parent);
  }
  const backend = { schemaVersion: 1, kind: 'chaoxing-backend', version, entryPoint: 'chaoxing-backend.exe',
    files: records().filter((entry) => entry.path.startsWith('backend/')).map((entry) => ({ ...entry, path: entry.path.slice(8) })),
    directories: [...directories].filter((relative) => relative.startsWith('backend/')).map((relative) => relative.slice(8)) };
  contents.set('backend-manifest.json', JSON.stringify(backend));
  const portableManifest = { schemaVersion: 1, kind: 'chaoxing-gui-tauri-portable', version,
    entryPoint: 'chaoxing-gui-tauri.exe', platform: 'windows-x64', files: records(), directories: [...directories] };
  if (mode === 'Installed') contents.set('chaoxing-gui-tauri.exe', contents.get('chaoxing-gui-tauri.exe').replace('_VAR_UNK', '_VAR_NSS'));
  const expectedManifest = mode === 'Portable' ? portableManifest
    : { ...portableManifest, kind: 'chaoxing-gui-tauri-nsis-payload', files: records() };
  const manifestBytes = JSON.stringify(expectedManifest);
  for (const [relative, bytes] of contents) {
    await mkdir(path.dirname(path.join(directory, relative)), { recursive: true });
    await writeFile(path.join(directory, relative), bytes);
  }
  const manifest = mode === 'Portable' ? path.join(directory, 'package-manifest.json') : path.join(root, 'nsis-payload-reference.json');
  await writeFile(manifest, manifestBytes);
  if (mode === 'Installed') await writeFile(path.join(directory, 'uninstall.exe'), 'inert uninstaller; never execute');
  return { root, directory, contents, portableManifest, expectedManifest,
    request: { directory, mode, manifest, manifestSha256: hash(manifestBytes), packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1') } };
}

const runInstallationScript = (context, script, request) => exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand',
  Buffer.from(script, 'utf16le').toString('base64')], {
  env: { ...process.env, P3_INSTALLATION_REQUEST: JSON.stringify(request) }, windowsHide: true, timeout: 20000,
});
const verifyLayoutFixture = (context, fixture) => runInstallationScript(context, installationPowerShellScripts.verifyLayoutScript, fixture.request);

async function childProfileFixture(t) {
  const root = await temporary(t);
  const context = { roaming: path.join(root, 'roaming'), local: path.join(root, 'local'), temp: path.join(root, 'temp'), sid: 'fixture-sid' };
  for (const directory of [context.roaming, context.local, context.temp]) await mkdir(directory);
  const evidenceDirectory = path.join(root, 'child-evidence');
  await mkdir(evidenceDirectory);
  const evidence = await mkdtemp(path.join(evidenceDirectory, 'smoke-'));
  const runId = randomUUID();
  const ownership = await publishReleaseProfileOwnership(evidence, context, runId);
  const roots = releaseProfileRoots(context);
  for (const entry of roots) await assert.rejects(lstat(entry.path), { code: 'ENOENT' });
  await claimProfileRoots(roots, runId, context.sid);
  const identity = { pid: 1234, createdAtFileTime: '133700000000000000', executable: process.execPath };
  const invocation = { evidenceDirectory, process: { identity, cleanup: { verified: true, fallbackUsed: true,
    remaining: [], observed: [{ ...identity, alive: false }] } } };
  return { root, context, ownership, roots, invocation, handoff: path.join(evidence, 'profile-ownership.json') };
}

test('Installation selection rejects unknown switches, missing inputs, and invalid deadlines', () => {
  assert.throws(() => parseInstallationArguments(['--unknown']), /unknown/i);
  assert.throws(() => parseInstallationArguments(['--installer-path']), /value/i);
  assert.throws(() => parseInstallationArguments(['--timeout-seconds', '0']), /timeout/i);
  assert.throws(() => parseInstallationArguments(['--installer-path', 'one', '--installer-path', 'two']), /duplicate/i);
});

test('Installation CLI refuses the real user before any installer or host and saves JSON', async (t) => {
  const root = await temporary(t);
  await assert.rejects(exec(process.execPath, [path.join(repo, 'desktop/scripts/p3-installation.mjs'),
    '--installer-path', 'must-never-run.exe', '--portable-path', 'must-never-extract.zip', '--evidence-directory', root], {
    env: { ...process.env, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '' }, windowsHide: true, timeout: 15000,
  }), (error) => error.code === 1 && /Evidence:/.test(error.stdout));
  const folders = await readdir(root);
  assert.equal(folders.length, 1);
  const result = JSON.parse(await readFile(path.join(root, folders[0], 'result.json'), 'utf8'));
  assert.equal(result.success, false);
  assert.deepEqual(result.processes, []);
  assert.match(result.error, /disposable|fresh/i);
});

test('Preexisting application, legacy, default install, or registry state blocks installation', async (t) => {
  const root = await temporary(t);
  const occupied = path.join(root, 'existing app');
  await mkdir(occupied);
  await writeFile(path.join(occupied, 'sentinel'), 'untouched');
  await assert.rejects(assertInstallationPreflight([{ path: occupied, claim: false }], [], 'run', 'sid'), /preexisting|owned/i);
  assert.equal(await readFile(path.join(occupied, 'sentinel'), 'utf8'), 'untouched');
  for (const kind of ['uninstall', 'preferences', 'autorun']) {
    await assert.rejects(assertInstallationPreflight([], [{ hive: 'CurrentUser', view: 'Registry64', key: 'old Tauri record', kind }], 'run', 'sid'), /registry|installation/i);
  }
});

test('Missing or incorrectly typed installation artifacts are rejected without execution', async (t) => {
  const root = await temporary(t);
  await assert.rejects(validateInstallationInputs({ installerPath: path.join(root, 'missing.exe'), portablePath: path.join(root, 'missing.zip') }), /installer/i);
  const installerPath = path.join(root, 'setup.exe');
  const portablePath = path.join(root, 'portable.zip');
  await writeFile(installerPath, 'not an executable');
  await writeFile(portablePath, 'not a zip');
  await assert.rejects(validateInstallationInputs({ installerPath, portablePath }), /PE|MZ|executable/i);
});

test('NSIS directory tails preserve Unicode/spaces and are never general raw arguments', () => {
  const directory = 'C:\\Smoke 用户 空格\\安装 目录';
  assert.deepEqual(nsisSpecification('install', directory), {
    args: ['/S', '/NS'], nsisTail: { mode: 'install', directory },
  });
  assert.deepEqual(nsisSpecification('uninstall', directory), {
    args: ['/S'], nsisTail: { mode: 'uninstall', directory },
  });
  for (const invalid of ['C:\\', 'relative', '\\\\server\\share', 'C:\\safe" /D=C:\\other', 'C:\\safe\n/S', 'C:\\safe\\..']) {
    assert.throws(() => nsisSpecification('install', invalid), /path|directory|NSIS/i);
  }
  assert.throws(() => nsisSpecification('arbitrary', directory), /NSIS/i);
});

test('Registry acceptance requires the owned install path and exact expected uninstaller', () => {
  const directory = 'C:\\Smoke 用户\\安装';
  const good = [{ hive: 'CurrentUser', view: 'Registry64', kind: 'uninstall',
    key: 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Chaoxing GUI Tauri',
    installLocation: `"${directory}"`, uninstallString: `"${directory}\\uninstall.exe"` }];
  assert.doesNotThrow(() => assertInstallRegistry(good, directory));
  assert.throws(() => assertInstallRegistry([], directory), /registry|uninstall/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], hive: 'LocalMachine' }], directory), /CurrentUser|current.user/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], installLocation: 'C:\\Users\\real\\AppData\\Local\\Chaoxing GUI Tauri' }], directory), /location|directory/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], uninstallString: 'C:\\other\\uninstall.exe /S' }], directory), /uninstaller/i);
});

test('Scratch cleanup requires its current ownership marker and refuses junctions', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  await writeFile(path.join(scratch, 'keep'), 'owned only after marking');
  await assert.rejects(removeOwnedScratch(scratch, 'run'), /owner|owned/i);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
  const outside = path.join(root, 'outside');
  await mkdir(outside);
  await writeFile(path.join(outside, 'keep'), 'outside');
  await symlink(outside, path.join(scratch, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(removeOwnedScratch(scratch, 'run'), /reparse|symbolic|link/i);
  assert.equal(await readFile(path.join(outside, 'keep'), 'utf8'), 'outside');
});

for (const placement of ['install-directory', 'backend', 'nested-backend']) {
  test(`Installation ${placement} junction fixture checks rejection and unlinks without deleting its target`, async (t) => {
    const root = await temporary(t);
    const scratch = path.join(root, 'scratch');
    await mkdir(scratch);
    await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
    const fixture = await createInstallationJunctionFixture(scratch, 'run', placement);
    await assert.rejects(removeOwnedScratch(scratch, 'run'), /reparse|symbolic|link/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 0, []), /exit code 2/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 1, []), /exit code 2/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 2, [{ kind: 'uninstall' }]), /registry/i);
    await assertInstallationJunctionRejected(fixture, 2, []);
    assert.equal(fixture.sentinels.every((entry) => entry.after === entry.before), true);
    await removeInstallationJunction(fixture);
    await assert.rejects(lstat(fixture.junction), { code: 'ENOENT' });
    assert.match(await readFile(path.join(fixture.target, 'sentinel.txt'), 'utf8'), /must remain unchanged/);
    await removeInstallationJunction(fixture);
    await removeOwnedScratch(scratch, 'run');
    await assert.rejects(lstat(scratch), { code: 'ENOENT' });
  });
}

test('Installation junction acceptance rejects payload writes and changed target bytes', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  await assert.rejects(createInstallationJunctionFixture(scratch, 'run', 'backend'), /owned|owner/i);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
  const fixture = await createInstallationJunctionFixture(scratch, 'run', 'backend');
  const payload = path.join(fixture.installDirectory, 'chaoxing-gui-tauri.exe');
  await writeFile(payload, 'inert unexpected installer write');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /program files/i);
  await unlink(payload);
  const unexpected = path.join(fixture.target, 'unexpected');
  await writeFile(unexpected, 'inert unexpected junction write');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /inventory/i);
  await unlink(unexpected);
  await writeFile(path.join(fixture.target, 'nested', 'sentinel.txt'), 'unexpectedly replaced');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /modified.*target/i);
  await removeInstallationJunction(fixture);
});

test('Installation junction cleanup refuses an unowned scratch or a substituted target', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  const marker = path.join(scratch, '.p3-installation-owner.json');
  await writeFile(marker, JSON.stringify({ runId: 'run', path: scratch }));
  const fixture = await createInstallationJunctionFixture(scratch, 'run', 'backend');
  await assert.rejects(removeInstallationJunction({ ...fixture, runId: 'other-run' }), /owner/i);
  const outside = path.join(root, 'outside');
  await mkdir(outside);
  await writeFile(path.join(outside, 'sentinel.txt'), 'unowned target must survive');
  await unlink(fixture.junction);
  await symlink(outside, fixture.junction, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(removeInstallationJunction(fixture), /target changed/i);
  assert.equal(await readFile(path.join(outside, 'sentinel.txt'), 'utf8'), 'unowned target must survive');
  assert.equal((await lstat(fixture.junction)).isSymbolicLink(), true);
  await unlink(fixture.junction);
});

for (const mode of ['Portable', 'Installed']) {
  test(`${mode} files are verified against both manifests without executing payloads`, { skip: process.platform !== 'win32', timeout: 25000 }, async (t) => {
    const fixture = await layoutFixture(t, mode);
    const context = await windowsContext();
    const { stdout } = await verifyLayoutFixture(context, fixture);
    const report = JSON.parse(stdout);
    assert.equal(report.mode, mode);
    assert.equal(report.manifestKind, mode === 'Portable' ? 'chaoxing-gui-tauri-portable' : 'chaoxing-gui-tauri-nsis-payload');
    assert.equal(report.files, fixture.contents.size);
    assert.equal(report.backendFiles, 4);
    if (mode === 'Installed') {
      const portableHost = fixture.portableManifest.files.find((entry) => entry.path === 'chaoxing-gui-tauri.exe');
      const installedHost = fixture.expectedManifest.files.find((entry) => entry.path === 'chaoxing-gui-tauri.exe');
      assert.equal(installedHost.length, portableHost.length);
      assert.notEqual(installedHost.sha256, portableHost.sha256);
    }
  });
}

test('NSIS reference binds inert artifacts and verifies the exact installed host', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const version = fixture.portableManifest.version;
  const scratch = path.join(fixture.root, 'owned scratch');
  const runId = randomUUID();
  await mkdir(scratch);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId, path: scratch }));
  const portableManifest = path.join(fixture.root, 'portable-reference.json');
  const portableBytes = JSON.stringify(fixture.portableManifest);
  await writeFile(portableManifest, portableBytes);
  const compilerHost = path.join(fixture.root, 'compiler-host.exe');
  await writeFile(compilerHost, fixture.contents.get('chaoxing-gui-tauri.exe').replace('_VAR_NSS', '_VAR_UNK'));
  const request = { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'), nsisContent: path.join(repo, 'desktop/scripts/nsis-content.ps1'),
    scratch, runId, compilerHost, portableManifest, portableManifestSha256: hash(portableBytes),
    installerPath: path.join(fixture.root, `chaoxing-gui-tauri-setup-${version}-windows-x64.exe`),
    portablePath: path.join(fixture.root, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`) };
  // These files exercise manifest hash bindings only. Neither file is a
  // runnable program or an extractable archive, and no payload is executed.
  await writeFile(request.installerPath, 'MZ inert installer binding fixture; never execute');
  await writeFile(request.portablePath, Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.from('inert ZIP binding fixture; never extract')]));
  await assert.rejects(validateInstallationInputs(request), { code: 'ENOENT' });
  await runInstallationScript(context, String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
. $p3Request.nsisContent
$p3HostExpectation = Get-NsisHostExpectation -HostPath $p3Request.compilerHost
Write-NsisArtifactManifest -InstallerPath $p3Request.installerPath -PortablePath $p3Request.portablePath -HostExpectation $p3HostExpectation
`, request);
  await validateInstallationInputs(request);
  const { stdout } = await runInstallationScript(context, installationPowerShellScripts.createNsisReferenceScript, request);
  const generated = JSON.parse(stdout);
  assert.equal(generated.manifest, path.join(scratch, 'nsis-payload-reference.json'));
  const referenceBytes = await readFile(generated.manifest);
  const reference = JSON.parse(referenceBytes);
  assert.equal(reference.kind, 'chaoxing-gui-tauri-nsis-payload');
  assert.deepEqual(reference.files, fixture.expectedManifest.files);
  assert.deepEqual(reference.directories, fixture.expectedManifest.directories);
  assert.equal(generated.sha256, hash(referenceBytes));
  const installed = { ...fixture, request: { ...fixture.request, manifest: generated.manifest, manifestSha256: generated.sha256 } };
  await verifyLayoutFixture(context, installed);
  await writeFile(generated.manifest, `${referenceBytes.toString('utf8')}\n`);
  await assert.rejects(verifyLayoutFixture(context, installed), /Reference payload manifest changed/s);
  await unlink(generated.manifest);
  for (const filename of [request.installerPath, request.portablePath]) {
    const original = await readFile(filename);
    await writeFile(filename, Buffer.concat([original, Buffer.from(' changed')]));
    await assert.rejects(runInstallationScript(context, installationPowerShellScripts.createNsisReferenceScript, request), /artifact identity mismatch/s);
    await writeFile(filename, original);
  }
});

test('Installed NSIS layout refuses portable manifests and unchanged UNK host hashes', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const wrongManifest = path.join(fixture.root, 'wrong-reference.json');
  for (const [manifest, expectedError] of [
    [fixture.portableManifest, /Invalid chaoxing-gui-tauri-nsis-payload manifest schema/s],
    [{ ...fixture.portableManifest, kind: 'chaoxing-gui-tauri-nsis-payload' }, /length\/hash mismatch.*chaoxing-gui-tauri.exe/s],
  ]) {
    const bytes = JSON.stringify(manifest);
    await writeFile(wrongManifest, bytes);
    await assert.rejects(verifyLayoutFixture(context, { ...fixture, request: { ...fixture.request, manifest: wrongManifest, manifestSha256: hash(bytes) } }), expectedError);
  }
});

test('Installed manifest verification catches changed host bytes, missing licenses, changed metadata, and extra files', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const host = path.join(fixture.directory, 'chaoxing-gui-tauri.exe');
  await writeFile(host, fixture.contents.get('chaoxing-gui-tauri.exe').replace('never execute', 'never ExEcUtE'));
  await assert.rejects(verifyLayoutFixture(context, fixture), /length\/hash mismatch.*chaoxing-gui-tauri.exe/s);
  await writeFile(host, fixture.contents.get('chaoxing-gui-tauri.exe'));
  const license = path.join(fixture.directory, 'LICENSE');
  await unlink(license);
  await assert.rejects(verifyLayoutFixture(context, fixture), /Missing manifest payload file.*LICENSE/s);
  await writeFile(license, fixture.contents.get('LICENSE'));
  const metadata = 'backend/_internal/fixture-1.0.dist-info/METADATA';
  await writeFile(path.join(fixture.directory, metadata), 'unexpected metadata bytes');
  await assert.rejects(verifyLayoutFixture(context, fixture), /length\/hash mismatch.*METADATA/s);
  await writeFile(path.join(fixture.directory, metadata), fixture.contents.get(metadata));
  await writeFile(path.join(fixture.directory, 'unexpected.txt'), 'unexpected payload');
  await assert.rejects(verifyLayoutFixture(context, fixture), /does not cover every payload file/s);
});

test('Uninstall retention includes actual Tauri files and detects deleted LocalAppData WebView data', async (t) => {
  const root = await temporary(t);
  const context = { roaming: path.join(root, 'roaming'), local: path.join(root, 'local') };
  const retained = installationRetentionFixtures(context, path.join(root, 'scratch'), 'run');
  const webView = retained.find((entry) => entry.path === path.join(context.local, 'com.chaoxing.gui', 'EBWebView', 'Default', 'Preferences'));
  assert.ok(webView);
  for (const name of ['web_config.json', 'renderer-session.json']) {
    assert.ok(retained.some((entry) => entry.path === path.join(context.roaming, 'com.chaoxing.gui', 'data', name)));
  }
  const observed = [];
  for (const entry of retained) {
    await mkdir(path.dirname(entry.path), { recursive: true });
    await writeFile(entry.path, entry.content);
    observed.push({ path: entry.path, before: hash(entry.content) });
  }
  await assertRetainedFiles(observed);
  await unlink(webView.path);
  await assert.rejects(assertRetainedFiles(observed), { code: 'ENOENT' });
});

test('Cancelled nested smoke profiles are reclaimed from its pre-claim handoff after verified Job cleanup', { skip: process.platform !== 'win32' }, async (t) => {
  const fixture = await childProfileFixture(t);
  const outside = path.join(fixture.root, 'unrelated.txt');
  await writeFile(outside, 'unrelated data survives');
  await cleanupChildProfileInvocation(fixture.invocation, fixture.context);
  assert.equal(fixture.invocation.profileCleanup.completed, true);
  assert.equal(fixture.invocation.profileCleanup.runId, fixture.ownership.runId);
  for (const entry of fixture.roots) await assert.rejects(lstat(entry.path), { code: 'ENOENT' });
  assert.equal(await readFile(outside, 'utf8'), 'unrelated data survives');
});

test('Nested profile cleanup refuses unverified/live processes, changed handoffs, and another run ownership', { skip: process.platform !== 'win32' }, async (t) => {
  const fixture = await childProfileFixture(t);
  const { invocation, context } = fixture;
  const profile = fixture.roots.find((entry) => entry.claim).path;
  const sentinel = path.join(profile, 'web_config.json');
  await writeFile(sentinel, 'must remain until ownership and process checks pass');
  invocation.process.cleanup.verified = false;
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /verified.*process/i);
  invocation.process.cleanup.verified = true;
  invocation.process.cleanup.observed[0].alive = true;
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /live.*process/i);
  invocation.process.cleanup.observed[0].alive = false;
  await writeFile(fixture.handoff, JSON.stringify({ ...fixture.ownership, sid: 'other-sid' }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /SID mismatch/i);
  await writeFile(fixture.handoff, JSON.stringify({ ...fixture.ownership, roots: [{ path: fixture.root, claim: true }] }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /known folders/i);
  await writeFile(fixture.handoff, JSON.stringify(fixture.ownership));
  const marker = path.join(profile, '.p3-smoke-owner.json');
  await writeFile(marker, JSON.stringify({ runId: 'another-run', sid: context.sid, path: profile }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /preexisting|owned/i);
  assert.equal(await readFile(sentinel, 'utf8'), 'must remain until ownership and process checks pass');
  assert.equal(invocation.profileCleanup.completed, false);
});

test('Native formatter emits NSIS special paths last and unquoted without executing an installer', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
  const context = await windowsContext();
  const source = path.join(repo, 'desktop/tests/fixtures/p3-windows-process.cs').replaceAll("'", "''");
  const command = `[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Add-Type -Path '${source}'; [Chaoxing.P3Smoke.ProcessOwner]::FormatNsisCommand('C:\\Setup Folder\\setup.exe', [string[]]@('/S','/NS'), 'install', 'C:\\Smoke 用户 空格\\安装 目录'); [Chaoxing.P3Smoke.ProcessOwner]::FormatNsisCommand('C:\\Smoke 用户 空格\\卸载.exe', [string[]]@('/S'), 'uninstall', 'C:\\Smoke 用户 空格\\安装 目录')`;
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-Command', command], { windowsHide: true, timeout: 15000 });
  assert.deepEqual(stdout.trim().split(/\r?\n/), [
    '"C:\\Setup Folder\\setup.exe" /S /NS /D=C:\\Smoke 用户 空格\\安装 目录',
    '"C:\\Smoke 用户 空格\\卸载.exe" /S _?=C:\\Smoke 用户 空格\\安装 目录',
  ]);
});

test('Embedded installation PowerShell scripts parse without running installers or registry mutations', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
  const context = await windowsContext();
  const parser = "$ErrorActionPreference='Stop'; $p3Sources=$env:P3_TEST_SCRIPT_SOURCES | ConvertFrom-Json; foreach ($p3Source in $p3Sources) { $p3Tokens=$null; $p3Errors=$null; [Management.Automation.Language.Parser]::ParseInput($p3Source,[ref]$p3Tokens,[ref]$p3Errors) | Out-Null; if ($p3Errors.Count) { throw ($p3Errors | Out-String) } }";
  await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(parser, 'utf16le').toString('base64')], {
    env: { ...process.env, P3_TEST_SCRIPT_SOURCES: JSON.stringify(Object.values(installationPowerShellScripts)) }, windowsHide: true, timeout: 15000,
  });
});
