import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import fileSystem, { copyFile, mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import {
  parseArguments, assertReleasePermission, assertOwnedOrAbsent, claimProfileRoots,
  releaseProfileRoots, removeProfilesAfterVerifiedCleanup, sanitizedEnvironment, validateInputs, until, withCleanup,
  NativeSupervisor, windowsContext, assertBuildProfile,
} from '../scripts/p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temporary = async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p3-test-'));
  t.after(() => rm(root, { recursive: true, force: true, maxRetries: 4, retryDelay: 100 }));
  return root;
};
const syntheticContext = {
  windows: 'C:\\Windows', roaming: 'C:\\Users\\fixture\\AppData\\Roaming',
  local: 'C:\\Users\\fixture\\AppData\\Local', userProfile: 'C:\\Users\\fixture',
  temp: 'C:\\Users\\fixture\\AppData\\Local\\Temp', sid: 'S-1-5-21-fixture',
};
const isAlive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

test('P3 rejects unknown, missing, and mismatched smoke selections', () => {
  assert.throws(() => parseArguments(['--scenario', 'Frozne']), /scenario/i);
  assert.throws(() => parseArguments(['--configuration', 'Relase']), /configuration/i);
  assert.throws(() => parseArguments(['--kind', 'python', '--mode', 'Unknown']), /mode/i);
  assert.throws(() => parseArguments(['--host-path']), /value/i);
  assert.throws(() => parseArguments(['--unknown', 'x']), /unknown/i);
  assert.throws(() => parseArguments(['--scenario', 'Fake', '--scenario', 'Frozen']), /duplicate/i);
  assert.throws(() => parseArguments(['--use-packaged-layout', '--scenario', 'Fake']), /Frozen/i);
  assert.equal(parseArguments(['--scenario', 'frozen']).scenario, 'Frozen');
});

test('Release requires an explicit disposable user or a hosted runner, never GITHUB_ACTIONS alone', () => {
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, {}), /disposable|fresh/i);
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, { GITHUB_ACTIONS: 'true' }), /disposable|fresh/i);
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, {
    GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'self-hosted', RUNNER_OS: 'Windows', GITHUB_RUN_ID: '123',
  }), /disposable|fresh/i);
  assert.equal(assertReleasePermission({ configuration: 'Release', disposableWindowsUser: true }, {}).allowed, true);
  assert.equal(assertReleasePermission({ configuration: 'Release' }, {
    GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted', RUNNER_OS: 'Windows', GITHUB_RUN_ID: '123',
  }).allowed, true);
  assert.equal(assertReleasePermission({ configuration: 'Debug' }, {}).required, false);
});

test('Compiled profile mismatches and unsupported native probe exits fail closed', () => {
  assert.doesNotThrow(() => assertBuildProfile(0, 'Debug'));
  assert.doesNotThrow(() => assertBuildProfile(4, 'Release'));
  assert.throws(() => assertBuildProfile(4, 'Debug'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(0, 'Release'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(1, 'Debug'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(null, 'Release'), /profile.*refused/i);
});

test('Production paths come from Windows known folders and include Electron legacy data', () => {
  const roots = releaseProfileRoots(syntheticContext);
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Roaming\\com.chaoxing.gui'));
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Local\\com.chaoxing.gui'));
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Roaming\\chaoxing-desktop' && !root.claim));
});

test('Release profile ownership refuses existing data and previous smoke runs without modifying them', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'existing');
  await mkdir(data);
  await writeFile(path.join(data, 'web_config.json'), 'private sentinel');
  const roots = [{ path: data, claim: true }];
  await assert.rejects(assertOwnedOrAbsent(roots, 'current-run', 'fixture-sid'), /preexisting|owned/i);
  assert.equal(await readFile(path.join(data, 'web_config.json'), 'utf8'), 'private sentinel');
  await writeFile(path.join(data, '.p3-smoke-owner.json'), JSON.stringify({ runId: 'older-run', sid: 'fixture-sid', path: data }));
  await assert.rejects(assertOwnedOrAbsent(roots, 'current-run', 'fixture-sid'), /preexisting|owned/i);
});

test('Only current smoke ownership permits profile reuse; legacy roots are never created', async (t) => {
  const root = await temporary(t);
  const roots = [{ path: path.join(root, 'app'), claim: true }, { path: path.join(root, 'legacy'), claim: false }];
  await claimProfileRoots(roots, 'same-run', 'fixture-sid');
  await claimProfileRoots(roots, 'same-run', 'fixture-sid');
  await assertOwnedOrAbsent(roots, 'same-run', 'fixture-sid');
  await assert.rejects(readFile(path.join(root, 'legacy', '.p3-smoke-owner.json')), { code: 'ENOENT' });
  await assert.rejects(assertOwnedOrAbsent(roots, 'other-run', 'fixture-sid'), /preexisting|owned/i);
});

test('Owned profiles survive missing or unverified supervisor cleanup reports', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'profile');
  const roots = [{ path: data, claim: true }];
  await claimProfileRoots(roots, 'run', 'sid');
  const sentinel = path.join(data, 'web_config.json');
  await writeFile(sentinel, 'retain until captured cleanup is verified');
  const neverStarted = new NativeSupervisor('never-run-powershell.exe', root);
  const defaultReport = await neverStarted.dispose();
  for (const report of [undefined, defaultReport, { verified: false, remaining: [], observed: [] },
    { verified: 'true', remaining: [], observed: [] }]) {
    await assert.rejects(removeProfilesAfterVerifiedCleanup(roots, 'run', 'sid', report), /verified captured process tree/i);
    assert.equal(await readFile(sentinel, 'utf8'), 'retain until captured cleanup is verified');
    await assertOwnedOrAbsent(roots, 'run', 'sid');
  }
});

test('Owned profile cleanup rejects live processes and incomplete captured Job reports', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'profile');
  const roots = [{ path: data, claim: true }];
  await claimProfileRoots(roots, 'run', 'sid');
  const sentinel = path.join(data, 'renderer-session.json');
  await writeFile(sentinel, 'retain incomplete cleanup evidence');
  for (const report of [
    { verified: true, observed: [{ alive: false }] },
    { verified: true, remaining: [{ alive: true }], observed: [{ alive: false }] },
    { verified: true, remaining: [] },
    { verified: true, remaining: [], observed: [] },
    { verified: true, remaining: [], observed: [{ alive: true }] },
    { verified: true, remaining: [], observed: [{}] },
  ]) {
    await assert.rejects(removeProfilesAfterVerifiedCleanup(roots, 'run', 'sid', report), /empty captured Job|observed dead/i);
    assert.equal(await readFile(sentinel, 'utf8'), 'retain incomplete cleanup evidence');
  }
});

test('Verified empty Job cleanup still enforces run and SID ownership before removing a profile', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'profile');
  const roots = [{ path: data, claim: true }];
  await claimProfileRoots(roots, 'run', 'sid');
  const sentinel = path.join(data, 'web_config.json');
  await writeFile(sentinel, 'current run data');
  const report = { verified: true, fallbackUsed: false, remaining: [], observed: [{ pid: 123, alive: false }] };
  await assert.rejects(removeProfilesAfterVerifiedCleanup(roots, 'another-run', 'sid', report), /preexisting|owned/i);
  await assert.rejects(removeProfilesAfterVerifiedCleanup(roots, 'run', 'another-sid', report), /preexisting|owned/i);
  assert.equal(await readFile(sentinel, 'utf8'), 'current run data');
  await removeProfilesAfterVerifiedCleanup(roots, 'run', 'sid', report);
  await assert.rejects(readFile(sentinel), { code: 'ENOENT' });
  await assert.rejects(readdir(data), { code: 'ENOENT' });
});

test('Primary GUI failure and supervisor failure remain observable while owned profiles are retained', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'profile');
  const roots = [{ path: data, claim: true }];
  await claimProfileRoots(roots, 'run', 'sid');
  const sentinel = path.join(data, 'chaoxing.log');
  await writeFile(sentinel, 'retain failed GUI evidence');
  const primary = new Error('fixture CDP startup failure');
  const cleanupFailure = new Error('fixture supervisor exited before verification');
  const owner = { dispose: async () => { throw cleanupFailure; } };
  await assert.rejects(withCleanup({}, async () => { throw primary; }, async () => {
    const cleanup = await owner.dispose();
    await removeProfilesAfterVerifiedCleanup(roots, 'run', 'sid', cleanup);
  }), (error) => error instanceof AggregateError && error.errors[0] === primary && error.errors[1] === cleanupFailure);
  assert.equal(await readFile(sentinel, 'utf8'), 'retain failed GUI evidence');
  await assert.rejects(withCleanup({}, async () => { throw primary; },
    () => removeProfilesAfterVerifiedCleanup(roots, 'run', 'sid', { remaining: [], observed: [] })),
  (error) => error instanceof AggregateError && error.errors[0] === primary && /verified captured process tree/i.test(error.errors[1].message));
  assert.equal(await readFile(sentinel, 'utf8'), 'retain failed GUI evidence');
});

test('A profile created after the absent preflight cannot be adopted by this smoke run', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'racing-profile');
  const originalStat = fileSystem.lstat;
  let injected = false;
  const replacement = t.mock.method(fileSystem, 'lstat', async (filename, ...args) => {
    try { return await originalStat(filename, ...args); }
    catch (error) {
      if (filename === data && error.code === 'ENOENT' && !injected) {
        injected = true;
        await mkdir(data);
        await writeFile(path.join(data, 'web_config.json'), 'concurrent application data');
      }
      throw error;
    }
  });
  syncBuiltinESMExports();
  try {
    await assert.rejects(claimProfileRoots([{ path: data, claim: true }], 'run', 'sid'), /preexisting|owned/i);
    assert.equal(injected, true);
    assert.equal(await readFile(path.join(data, 'web_config.json'), 'utf8'), 'concurrent application data');
    await assert.rejects(readFile(path.join(data, '.p3-smoke-owner.json')), { code: 'ENOENT' });
  } finally {
    replacement.mock.restore();
    syncBuiltinESMExports();
  }
});

test('Profile guard refuses junctions before following them', async (t) => {
  const root = await temporary(t);
  const target = path.join(root, 'target');
  const link = path.join(root, 'link');
  await mkdir(target);
  await symlink(target, link, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(assertOwnedOrAbsent([{ path: link, claim: true }], 'run', 'sid'), /reparse|symbolic|link/i);
});

test('Host environment cannot resolve system Python or use inherited development overrides', () => {
  const contaminated = {
    PATH: 'C:\\Python311;C:\\Tools', Path: 'C:\\OtherPython',
    PYTHONPATH: 'private', PYTHONHOME: 'private', VIRTUAL_ENV: 'private',
    APPDATA: 'C:\\fake-appdata', LOCALAPPDATA: 'C:\\fake-local',
    CHAOXING_TAURI_DEV_ROOT: 'real-data', CHAOXING_TAURI_DEV_BACKEND: 'real-backend',
    CHAOXING_LEGACY_DATA_DIR: 'real-legacy', CHAOXING_DATA_DIR: 'real-data',
    WEBVIEW2_USER_DATA_FOLDER: 'real-webview', GITHUB_TOKEN: 'private-token',
  };
  const release = sanitizedEnvironment(contaminated, syntheticContext, { configuration: 'Release' });
  assert.equal(release.PATH, 'C:\\Windows\\System32;C:\\Windows;C:\\Windows\\System32\\Wbem');
  assert.equal(release.APPDATA, syntheticContext.roaming);
  assert.equal(release.LOCALAPPDATA, syntheticContext.local);
  for (const key of Object.keys(release)) assert.ok(!/^PYTHON|^VIRTUAL_ENV$|^CHAOXING_|^GITHUB_TOKEN$|^WEBVIEW2_USER_DATA_FOLDER$/i.test(key), key);
  const debug = sanitizedEnvironment(contaminated, syntheticContext, {
    configuration: 'Debug', profile: 'C:\\isolated\\profile', backend: 'C:\\isolated\\backend.exe',
  });
  assert.equal(debug.CHAOXING_TAURI_DEV_ROOT, 'C:\\isolated\\profile');
  assert.equal(debug.CHAOXING_TAURI_DEV_BACKEND, 'C:\\isolated\\backend.exe');
  assert.equal(debug.CHAOXING_TAURI_DEV_HIDDEN, '1');
});

test('Missing host and incomplete frozen resources fail validation before launch', async (t) => {
  const root = await temporary(t);
  const options = { kind: 'tauri', configuration: 'Debug', scenario: 'Frozen', hostPath: path.join(root, 'host.exe'), backendDirectory: path.join(root, 'backend') };
  await assert.rejects(validateInputs(options), /host/i);
  await writeFile(options.hostPath, 'not launched by validation');
  await mkdir(options.backendDirectory);
  await writeFile(path.join(options.backendDirectory, 'chaoxing-backend.exe'), 'not launched by validation');
  await assert.rejects(validateInputs(options), /_internal|onedir/i);
});

test('Installed layout requires the host adjacent backend and never rewrites its source files', async (t) => {
  const root = await temporary(t);
  const hostPath = path.join(root, 'installed', 'chaoxing-gui-tauri.exe');
  const backendDirectory = path.join(root, 'installed', 'backend');
  await mkdir(path.join(backendDirectory, '_internal'), { recursive: true });
  await writeFile(hostPath, 'original host sentinel');
  await writeFile(path.join(backendDirectory, 'chaoxing-backend.exe'), 'original backend sentinel');
  const options = { kind: 'tauri', configuration: 'Release', scenario: 'Frozen', usePackagedLayout: true, hostPath, backendDirectory };
  await assert.rejects(validateInputs({ ...options, backendDirectory: path.join(root, 'elsewhere') }), /parent\/backend/i);
  await validateInputs(options);
  assert.equal(await readFile(hostPath, 'utf8'), 'original host sentinel');
  assert.equal(await readFile(path.join(backendDirectory, 'chaoxing-backend.exe'), 'utf8'), 'original backend sentinel');
});

test('Timeout always runs cleanup and cleanup failure cannot turn into a pass', async () => {
  let cleaned = 0;
  await assert.rejects(withCleanup({}, () => until(() => false, 'intentional fixture timeout', 30, 5), async () => { cleaned++; }), /timed out/);
  assert.equal(cleaned, 1);
  await assert.rejects(withCleanup({}, () => until(() => new Promise(() => {}), 'stalled readiness', 30), async () => { cleaned++; }), /timed out/);
  assert.equal(cleaned, 2);
  await assert.rejects(withCleanup({}, async () => { throw new Error('primary failure'); }, async () => { throw new Error('cleanup failure'); }), /primary failure.*cleanup failure/s);
});

test('Release CLI fails closed and writes failure evidence without launching a host', async (t) => {
  const root = await temporary(t);
  await assert.rejects(exec(process.execPath, [path.join(repo, 'desktop/scripts/p3-smoke.mjs'),
    '--configuration', 'Release', '--host-path', 'never-launch.exe', '--evidence-directory', root], {
    env: { ...process.env, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '' }, windowsHide: true, timeout: 15000,
  }), (error) => error.code === 1 && /Evidence:/.test(error.stdout));
  const { readdir } = await import('node:fs/promises');
  const directories = await readdir(root);
  assert.equal(directories.length, 1);
  const result = JSON.parse(await readFile(path.join(root, directories[0], 'result.json'), 'utf8'));
  assert.equal(result.success, false);
  assert.equal(result.processes.length, 0);
  assert.match(result.error, /disposable|fresh/i);
});

test('PowerShell smoke wrappers choose the first real Node when PATH contains multiple matches', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const first = path.join(root, 'first node');
  const second = path.join(root, 'second node');
  await mkdir(first);
  await mkdir(second);
  for (const filename of [path.join(first, 'node.exe'), path.join(first, 'node'), path.join(second, 'node.exe')]) {
    // Independent copies can be removed while this test's Node is still
    // running; Windows can lock every hardlink to a loaded executable.
    await copyFile(process.execPath, filename);
  }
  const preload = path.join(root, 'record-node.cjs');
  await writeFile(preload, "require('node:fs').writeFileSync(process.env.P3_WRAPPER_PROBE, JSON.stringify({ execPath: process.execPath, version: process.version }));");
  const inheritedPath = Object.entries(process.env).find(([name]) => name.toLowerCase() === 'path')?.[1] || '';
  const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !['path', 'node_options'].includes(name.toLowerCase())));
  Object.assign(environment, { PATH: `${first};${second};${inheritedPath}`, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '',
    NODE_OPTIONS: `--require "${preload.replaceAll('\\', '/')}"` });
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-Command',
    '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @(Get-Command node -CommandType Application).Source | ConvertTo-Json -Compress'],
  { env: environment, windowsHide: true, timeout: 10000 });
  const matches = JSON.parse(stdout);
  assert.ok(Array.isArray(matches) && matches.length >= 3, 'The regression must exercise multiple executable matches');
  assert.equal(matches[0].toLowerCase(), path.join(first, 'node.exe').toLowerCase());
  for (const [script, args] of [
    ['smoke-tauri.ps1', ['-HostPath', path.join(root, 'never-run.exe'), '-Configuration', 'Release']],
    ['smoke-python.ps1', ['-ExecutablePath', path.join(root, 'missing-backend.exe')]],
    ['smoke-installation.ps1', ['-InstallerPath', path.join(root, 'never-run.exe'), '-PortablePath', path.join(root, 'never-extract.zip')]],
  ]) {
    const evidence = path.join(root, `${script}-evidence`);
    const probe = path.join(root, `${script}-node.json`);
    await assert.rejects(exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/scripts', script),
      ...args, '-EvidenceDirectory', evidence], { env: { ...environment, P3_WRAPPER_PROBE: probe }, windowsHide: true, timeout: 15000 }),
    (error) => {
      assert.equal(error.code, 1, `${script}: ${error.stderr}`);
      assert.match(error.stdout, /Evidence:/, `${script}: ${error.stderr}`);
      return true;
    }, `${script} must reach the Node validation and preserve its exit code`);
    const selected = JSON.parse(await readFile(probe, 'utf8'));
    assert.equal(selected.execPath.toLowerCase(), path.join(first, 'node.exe').toLowerCase(), script);
    assert.equal(selected.version, process.version, script);
    const reports = await readdir(evidence);
    assert.equal(reports.length, 1, script);
    const report = JSON.parse(await readFile(path.join(evidence, reports[0], 'result.json'), 'utf8'));
    assert.equal(report.success, false, script);
    assert.deepEqual(report.processes, [], script);
  }
});

test('Windows supervisor reports start failure without leaving a captured process', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await assert.rejects(supervisor.start({ executable: path.join(root, 'missing.exe'), args: [], cwd: root, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) }), /CreateProcess|not found|找不到/i);
    assert.equal(supervisor.identity, null);
  } finally { await supervisor.dispose(); }
});

test('Windows supervisor supplies live stdin and observes a clean EOF shutdown', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await supervisor.start({ executable: process.execPath, args: [path.join(repo, 'desktop/tests/fixtures/p3-process-child.mjs')], cwd: root,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    await until(async () => (await readFile(path.join(root, 'stdout.log'), 'utf8')).includes('stdin-open'), 'child readiness', 5000);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const snapshot = await supervisor.snapshot();
    assert.ok(snapshot.active.some((identity) => identity.pid === supervisor.identity.pid && identity.inOuterJob));
    await supervisor.command('stdin-eof');
    await until(async () => { const state = await supervisor.snapshot(); return state.active.length === 0 && state.observed.every((identity) => !identity.alive); }, 'EOF cleanup', 5000);
    const cleanup = await supervisor.dispose();
    assert.equal(cleanup.fallbackUsed, false);
    assert.deepEqual(cleanup.remaining, []);
  } finally { await supervisor.dispose(); }
});

test('Timed-out Windows tree is cleaned while an unrelated process survives', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { windowsHide: true, stdio: 'ignore' });
  await new Promise((resolve, reject) => { unrelated.once('spawn', resolve); unrelated.once('error', reject); });
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await supervisor.start({ executable: process.execPath, args: [path.join(repo, 'desktop/tests/fixtures/p3-process-child.mjs'), '--tree'], cwd: root,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    const snapshot = await until(async () => {
      const current = await supervisor.snapshot();
      return current.active.filter((identity) => identity.executable === supervisor.identity.executable).length === 2 && current;
    }, 'descendant observed', 5000);
    await assert.rejects(withCleanup(supervisor, () => until(() => false, 'intentional startup timeout', 40, 5), (value) => value.dispose()), /timed out/);
    for (const identity of snapshot.active) await until(() => !isAlive(identity.pid), 'captured process cleanup', 5000);
    assert.equal(isAlive(unrelated.pid), true);
  } finally {
    await supervisor.dispose();
    unrelated.kill();
    await until(() => !isAlive(unrelated.pid), 'unrelated fixture cleanup', 5000);
  }
});

test('Chinese title crosses the supervisor pipe and WM_CLOSE reaches only the captured real window', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const target = new NativeSupervisor(context.powerShell, path.join(root, 'target'));
  const other = new NativeSupervisor(context.powerShell, path.join(root, 'other'));
  const spec = { executable: context.powerShell, args: ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/tests/fixtures/p3-title-window.ps1')],
    cwd: root, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) };
  try {
    await target.start(spec);
    await other.start(spec);
    for (const directory of ['target', 'other']) await until(async () => (await readFile(path.join(root, directory, 'stdout.log'), 'utf8')).includes('window-ready'), 'real title window ready', 5000);
    assert.equal(await target.command('close-window', { title: '超星学习通 · 自动化学习助手' }), 1);
    await until(async () => { const snapshot = await target.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive); }, 'Chinese window close', 5000);
    assert.equal((await other.snapshot()).host.alive, true, 'A separate process with the same title must survive');
    assert.equal(await other.command('close-window', { title: '超星学习通 · 自动化学习助手' }), 1);
    await until(async () => { const snapshot = await other.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive); }, 'second fixture close', 5000);
    assert.equal((await target.dispose()).fallbackUsed, false);
    assert.equal((await other.dispose()).fallbackUsed, false);
  } finally {
    try { await target.dispose(); } finally { await other.dispose(); }
  }
});
