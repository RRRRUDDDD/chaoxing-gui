// P3 Windows smoke: a real packaged host, an isolated onedir resource copy,
// and a retained outer Job. Importing this module never starts an application.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { cp, copyFile, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const marker = '.p3-smoke-owner.json';
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const canonical = (value) => path.resolve(value).toLowerCase();
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
const readable = async (filename) => readFile(filename, 'utf8').catch((error) => {
  if (error.code === 'ENOENT') return '';
  throw error;
});

export function parseArguments(argv) {
  const names = new Map([
    ['kind', 'kind'], ['host-path', 'hostPath'], ['backend-directory', 'backendDirectory'],
    ['fake-backend-directory', 'fakeBackendDirectory'], ['evidence-directory', 'evidenceDirectory'],
    ['configuration', 'configuration'], ['scenario', 'scenario'], ['executable-path', 'executablePath'],
    ['mode', 'mode'], ['timeout-seconds', 'timeoutSeconds'], ['powershell-path', 'powerShell'],
  ]);
  const switches = new Map([['nested-job', 'nestedJob'], ['disposable-windows-user', 'disposableWindowsUser'], ['use-packaged-layout', 'usePackagedLayout']]);
  const options = { kind: 'tauri', configuration: 'Release', scenario: 'All', mode: 'Backend', timeoutSeconds: 150 };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index].replace(/^--/, '');
    if (!argv[index].startsWith('--') || (!names.has(flag) && !switches.has(flag))) throw new Error(`Unknown smoke argument: ${argv[index]}`);
    if (seen.has(flag)) throw new Error(`Duplicate smoke argument: --${flag}`);
    seen.add(flag);
    if (switches.has(flag)) options[switches.get(flag)] = true;
    else {
      const value = argv[++index];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for --${flag}`);
      options[names.get(flag)] = value;
    }
  }
  for (const [key, allowed] of Object.entries({ kind: ['tauri', 'python'], configuration: ['Debug', 'Release'], scenario: ['Fake', 'Frozen', 'All'], mode: ['Independent', 'Backend'] })) {
    const selected = allowed.find((value) => value.toLowerCase() === String(options[key]).toLowerCase());
    if (!selected) throw new Error(`Unknown smoke ${key}: ${options[key]}`);
    options[key] = selected;
  }
  options.timeoutSeconds = Number(options.timeoutSeconds);
  if (!Number.isInteger(options.timeoutSeconds) || options.timeoutSeconds < 1 || options.timeoutSeconds > 600) throw new Error('timeout-seconds must be between 1 and 600');
  if (options.usePackagedLayout && (options.kind !== 'tauri' || options.scenario !== 'Frozen')) throw new Error('UsePackagedLayout requires the Tauri Frozen scenario');
  return options;
}

export function assertReleasePermission(options, env = process.env) {
  if (options.configuration !== 'Release' || options.kind === 'python') return { required: false, allowed: true };
  const hosted = env.GITHUB_ACTIONS === 'true' && env.RUNNER_ENVIRONMENT === 'github-hosted'
    && env.RUNNER_OS === 'Windows' && /^\d+$/.test(env.GITHUB_RUN_ID || '');
  if (!options.disposableWindowsUser && !hosted) {
    throw new Error('Release smoke requires a fresh GitHub-hosted Windows runner or -DisposableWindowsUser asserting a disposable Windows user/VM. This guard cannot be bypassed with APPDATA or development environment overrides.');
  }
  return { required: true, allowed: true, basis: hosted ? 'github-hosted-windows-runner; profile freshness still required' : 'caller asserts disposable Windows user/VM' };
}

export function releaseProfileRoots(context) {
  return [
    { path: path.win32.join(context.roaming, 'com.chaoxing.gui'), claim: true },
    { path: path.win32.join(context.local, 'com.chaoxing.gui'), claim: true },
    { path: path.win32.join(context.roaming, 'chaoxing-desktop'), claim: false },
    { path: path.win32.join(context.local, 'chaoxing-desktop'), claim: false },
    { path: path.win32.join(context.temp, 'chaoxing-desktop'), claim: false },
  ];
}

export async function checkNoLinks(target, recursive = false) {
  const absolute = path.resolve(target);
  const ancestors = [];
  for (let current = absolute; ; current = path.dirname(current)) {
    ancestors.push(current);
    if (current === path.dirname(current)) break;
  }
  for (const current of ancestors.reverse()) {
    const info = await lstat(current).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
    if (!info) return false;
    if (info.isSymbolicLink()) throw new Error(`Smoke refuses symbolic links/reparse points: ${current}`);
  }
  if (recursive && (await lstat(absolute)).isDirectory()) {
    for (const entry of await readdir(absolute)) await checkNoLinks(path.join(absolute, entry), true);
  }
  return true;
}

export async function assertOwnedOrAbsent(roots, runId, sid) {
  for (const root of roots) {
    if (!(await checkNoLinks(root.path))) continue;
    await checkNoLinks(path.join(root.path, marker));
    const ownership = await json(path.join(root.path, marker)).catch((error) => { if (error.code === 'ENOENT' || error instanceof SyntaxError) return null; throw error; });
    if (!ownership || ownership.runId !== runId || ownership.sid !== sid || typeof ownership.path !== 'string' || canonical(ownership.path) !== canonical(root.path)) {
      throw new Error(`Release smoke refuses preexisting application/legacy data not owned by this smoke run: ${root.path}`);
    }
  }
}

export async function claimProfileRoots(roots, runId, sid) {
  await assertOwnedOrAbsent(roots, runId, sid);
  for (const root of roots.filter((entry) => entry.claim)) {
    // Parents are OS known folders that already exist. Exclusive mkdir rejects
    // a concurrent application's new directory. Reusing any existing directory
    // requires its ownership to be checked again after that creation attempt.
    try { await mkdir(root.path); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      await assertOwnedOrAbsent([root], runId, sid);
      continue;
    }
    await writeFile(path.join(root.path, marker), JSON.stringify({ runId, sid, path: path.resolve(root.path) }), { flag: 'wx' });
  }
}

export async function publishReleaseProfileOwnership(evidence, context, runId) {
  const roots = releaseProfileRoots(context);
  await assertOwnedOrAbsent(roots, runId, context.sid);
  assert.ok(await checkNoLinks(evidence), 'Profile ownership evidence directory must exist without links');
  const ownership = { schemaVersion: 1, configuration: 'Release', runId, sid: context.sid,
    evidenceDirectory: path.resolve(evidence), roots };
  // Complete this handoff before any profile is claimed. An enclosing Job
  // owner can then clean this invocation's roots if its finally is interrupted.
  await writeFile(path.join(evidence, 'profile-ownership.json'), JSON.stringify(ownership), { flag: 'wx' });
  return ownership;
}

export async function removeOwnedProfiles(roots, runId, sid) {
  await assertOwnedOrAbsent(roots, runId, sid);
  for (const root of roots.filter((entry) => entry.claim)) {
    if (!(await checkNoLinks(root.path, true))) continue;
    const resolved = await realpath(root.path);
    if (canonical(resolved) !== canonical(root.path)) throw new Error(`Refusing unexpected profile target: ${resolved}`);
    // Only exact known-folder children with this run's ownership marker reach rm.
    await rm(resolved, { recursive: true, force: false, maxRetries: 4, retryDelay: 200 });
  }
}

export async function removeProfilesAfterVerifiedCleanup(roots, runId, sid, cleanup) {
  assert.equal(cleanup?.verified, true, 'Profile cleanup requires a verified captured process tree');
  assert.deepEqual(cleanup.remaining, [], 'Profile cleanup requires an empty captured Job');
  assert.ok(Array.isArray(cleanup.observed) && cleanup.observed.length > 0
    && cleanup.observed.every((identity) => identity?.alive === false), 'Profile cleanup requires all captured processes to be observed dead');
  await removeOwnedProfiles(roots, runId, sid);
}

export function sanitizedEnvironment(source, context, options = {}) {
  const allowed = new Set(['COMSPEC', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432',
    'PUBLIC', 'HOMEDRIVE', 'HOMEPATH', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'USERNAME', 'USERDOMAIN']);
  const env = {};
  for (const [key, value] of Object.entries(source)) if (allowed.has(key.toUpperCase())) env[key.toUpperCase()] = value;
  Object.assign(env, {
    SystemRoot: context.windows, WINDIR: context.windows, USERPROFILE: context.userProfile,
    APPDATA: context.roaming, LOCALAPPDATA: context.local, TEMP: context.temp, TMP: context.temp,
    PATH: [path.win32.join(context.windows, 'System32'), context.windows, path.win32.join(context.windows, 'System32', 'Wbem')].join(';'),
  });
  if (options.configuration === 'Debug') {
    assert.ok(options.profile && options.backend, 'Debug launch requires explicit isolated profile and backend');
    Object.assign(env, { CHAOXING_TAURI_DEV_ROOT: options.profile, CHAOXING_TAURI_DEV_BACKEND: options.backend, CHAOXING_TAURI_DEV_HIDDEN: '1' });
  }
  return env;
}

export async function windowsContext(powerShell) {
  if (process.platform !== 'win32') throw new Error('P3 real process smoke requires Windows');
  const executable = powerShell || 'pwsh.exe';
  const { stdout } = await exec(executable, ['-NoProfile', '-NonInteractive', '-Command',
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @{ windows=[Environment]::GetFolderPath('Windows'); roaming=[Environment]::GetFolderPath('ApplicationData'); local=[Environment]::GetFolderPath('LocalApplicationData'); userProfile=[Environment]::GetFolderPath('UserProfile'); temp=[IO.Path]::GetTempPath(); sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; powerShell=(Join-Path $PSHOME 'pwsh.exe') } | ConvertTo-Json -Compress"],
  { windowsHide: true, timeout: 15000 });
  const context = JSON.parse(stdout);
  for (const key of ['windows', 'roaming', 'local', 'userProfile', 'temp', 'powerShell']) assert.ok(path.isAbsolute(context[key]), `Windows known folder ${key} must be absolute`);
  return context;
}

async function requireFile(filename, label) {
  if (!filename) throw new Error(`${label} path is required`);
  const info = await lstat(filename).catch((error) => { throw new Error(`${label} is missing: ${filename}: ${error.message}`); });
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`${label} must be a regular file: ${filename}`);
}

export async function validateInputs(options) {
  if (options.kind === 'python') {
    await requireFile(options.executablePath, 'Frozen Python executable');
    if (options.mode === 'Backend') {
      const internal = path.join(path.dirname(options.executablePath), '_internal');
      assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Frozen backend must retain its complete onedir _internal directory: ${internal}`);
    }
    return;
  }
  await requireFile(options.hostPath, 'Tauri host');
  if (options.usePackagedLayout) {
    assert.equal(options.scenario, 'Frozen', 'UsePackagedLayout requires Frozen');
    assert.equal(canonical(options.backendDirectory), canonical(path.join(path.dirname(options.hostPath), 'backend')), 'UsePackagedLayout requires BackendDirectory equal to HostPath parent/backend');
    await checkNoLinks(path.dirname(options.hostPath));
  }
  if (options.scenario !== 'Fake') {
    await requireFile(path.join(options.backendDirectory || '', 'chaoxing-backend.exe'), 'Frozen backend');
    const internal = path.join(options.backendDirectory, '_internal');
    assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Frozen backend must retain its complete onedir _internal directory: ${internal}`);
  }
  if (options.scenario !== 'Frozen') {
    await requireFile(path.join(options.fakeBackendDirectory || '', 'p2-backend.exe'), 'Frozen P2 fixture');
    const internal = path.join(options.fakeBackendDirectory, '_internal');
    assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Fixture must be a complete onedir package: ${internal}`);
  }
}

export function assertBuildProfile(exitCode, configuration) {
  const expected = configuration === 'Debug' ? 0 : 4;
  if (exitCode !== expected) throw new Error(`Build profile probe refused ${configuration}: expected exit ${expected}, received ${exitCode}. A release executable cannot use a Debug smoke profile.`);
}

async function probeBuildProfile(options, context, evidence, result, record) {
  // Keep Debug fail-closed before running an unknown host on a developer profile.
  // Optimized Release builds can split the flag into overlapping SIMD constants;
  // only a disposable profile may classify those builds by their native exit.
  if (options.configuration === 'Debug') {
    const bytes = await readFile(options.hostPath);
    assert.ok(bytes.includes(Buffer.from('--check-debug-build')), 'Host lacks the P3 side-effect-free --check-debug-build probe; rebuild the current P3 host before smoke');
  } else { assertReleasePermission(options); }
  const roots = options.configuration === 'Release' ? releaseProfileRoots(context) : [];
  const directory = path.join(evidence, 'build-profile-probe');
  await mkdir(directory);
  const owner = new NativeSupervisor(context.powerShell, directory);
  const item = { scenario: 'build-profile-probe', requested: options.configuration };
  result.processes.push(item);
  let profilesClaimed = false;
  await withCleanup(item, async () => {
    if (roots.length) {
      await claimProfileRoots(roots, result.runId, context.sid);
      profilesClaimed = true;
    }
    item.hostIdentity = await owner.start({ executable: options.hostPath, args: ['--check-debug-build'], cwd: directory,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    item.shutdown = await waitForEmptyJob(owner, 'side-effect-free native build profile probe', 10000);
    assertBuildProfile(item.shutdown.host.exitCode, options.configuration);
    record('compiled-host-profile-verified', { configuration: options.configuration, exitCode: item.shutdown.host.exitCode });
  }, async () => {
    item.cleanup = await owner.dispose();
    if (profilesClaimed) {
      await removeProfilesAfterVerifiedCleanup(roots, result.runId, context.sid, item.cleanup);
      item.profileCleanup = { ownedRootsRemoved: true };
    }
  });
  assert.equal(item.cleanup.fallbackUsed, false, 'Build probe must exit without forced cleanup');
}

export async function until(check, label, timeout = 20000, interval = 100) {
  const deadline = Date.now() + timeout;
  let latest;
  while (Date.now() < deadline) {
    try { const result = await bounded(check, Math.max(1, deadline - Date.now()), label); if (result) return result; }
    catch (error) { if (error.fatal) throw error; latest = error; }
    await pause(Math.min(interval, Math.max(0, deadline - Date.now())));
  }
  throw new Error(`${label} timed out after ${timeout}ms${latest ? `: ${latest.message}` : ''}`);
}

async function bounded(action, timeout, label) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeout}ms`)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

export async function withCleanup(resource, action, cleanup) {
  let result;
  let failure;
  try { result = await action(resource); } catch (error) { failure = error; }
  try { await cleanup(resource); } catch (error) { failure = failure ? new AggregateError([failure, error], `${failure.message}; cleanup failed: ${error.message}`) : error; }
  if (failure) throw failure;
  return result;
}

export class NativeSupervisor {
  constructor(powerShell, evidenceDirectory) {
    this.powerShell = powerShell;
    this.evidenceDirectory = evidenceDirectory;
    this.identity = null;
    this.pending = new Map();
    this.counter = 0;
    this.diagnostics = '';
    this.finished = false;
  }
  async start(specification) {
    await mkdir(this.evidenceDirectory, { recursive: true });
    this.child = spawn(this.powerShell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
      path.join(repo, 'desktop/tests/fixtures/p3-windows-process.ps1')], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdin.on('error', () => {});
    this.child.stderr.on('data', (bytes) => { this.diagnostics += bytes.toString(); });
    this.lines = createInterface({ input: this.child.stdout });
    this.lines.on('line', (line) => {
      let message;
      try { message = JSON.parse(line); } catch { this.diagnostics += `${line}\n`; return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id); clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(message.error));
    });
    const failed = (error) => {
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
      this.pending.clear();
    };
    this.child.on('error', failed);
    this.child.on('exit', (code, signal) => failed(new Error(`Process supervisor exited ${code ?? signal}: ${this.diagnostics}`)));
    try {
      this.identity = await this.command('start', { specification: { ...specification,
        stdoutPath: path.join(this.evidenceDirectory, 'stdout.log'), stderrPath: path.join(this.evidenceDirectory, 'stderr.log') } }, 20000);
      return this.identity;
    } catch (error) {
      await this.dispose().catch((cleanup) => { error.message += `; launch cleanup: ${cleanup.message}`; });
      throw error;
    }
  }
  command(operation, properties = {}, timeout = 10000) {
    if (!this.child || this.child.exitCode !== null || this.child.signalCode !== null) return Promise.reject(new Error('Process supervisor is not running'));
    const id = ++this.counter;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Supervisor ${operation} timed out`)); }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(`${JSON.stringify({ id, operation, ...properties })}\n`, (error) => {
        if (error && this.pending.delete(id)) { clearTimeout(timer); reject(error); }
      });
    });
  }
  snapshot() { return this.command('snapshot'); }
  async dispose() {
    if (this.finished) return this.cleanup;
    this.finished = true;
    let failure;
    try {
      if (this.identity && this.child?.exitCode === null && this.child?.signalCode === null) {
        this.cleanup = await this.command('finish');
        this.cleanup.verified = true;
        if (this.cleanup.remaining.length) throw new Error('Captured Job still contains processes after cleanup');
      } else if (this.identity) {
        this.cleanup = { fallbackUsed: true, verified: false, reason: 'Supervisor exited before its final process-state report' };
        throw new Error('Supervisor exited before cleanup could verify the captured tree');
      }
    } catch (error) { failure = error; }
    finally {
      this.child?.stdin.end();
      if (this.child) {
        try { await until(() => this.child.exitCode !== null || this.child.signalCode !== null, 'supervisor exit', 6000); }
        catch {
          // Closing the supervisor's only Job handle kills only its captured tree.
          this.child.kill();
          await until(() => this.child.exitCode !== null || this.child.signalCode !== null, 'supervisor termination', 5000);
          failure ||= new Error('Supervisor required forced cleanup');
        }
      }
      this.lines?.close();
      if (this.diagnostics) await writeFile(path.join(this.evidenceDirectory, 'supervisor.log'), this.diagnostics);
    }
    if (failure) throw failure;
    return this.cleanup || { fallbackUsed: false, remaining: [], observed: [] };
  }
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function fingerprint(filename) {
  return readFile(filename).then((bytes) => createHash('sha256').update(bytes).digest('hex')).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
}

function loadChromium() {
  try { return createRequire(path.join(repo, 'desktop/package.json'))('playwright-core').chromium; }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND' || !process.env.P2_TOOLS_DIR) throw error;
    return createRequire(path.join(path.resolve(process.env.P2_TOOLS_DIR), 'package.json'))('playwright-core').chromium;
  }
}

async function safeCopyDirectory(source, destination) {
  assert.ok(await checkNoLinks(source, true), `Missing resource directory: ${source}`);
  await cp(source, destination, { recursive: true, errorOnExist: true, force: false });
}

function fatal(message) { const error = new Error(message); error.fatal = true; return error; }

async function assertRunning(owner) {
  const snapshot = await owner.snapshot();
  if (!snapshot.host?.alive) throw fatal(`Captured executable exited before the assertion completed (exit ${snapshot.host?.exitCode})`);
  return snapshot;
}

async function fetchBounded(url, options = {}, timeout = 2000) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(timeout) });
}

async function waitForEmptyJob(owner, label, timeout = 12000) {
  return until(async () => { const snapshot = await owner.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot; }, label, timeout);
}

async function runTauri(options, context, evidence, result, record) {
  const chromium = loadChromium();
  const work = path.join(evidence, 'fixtures');
  await mkdir(work);
  const roots = options.configuration === 'Release' ? releaseProfileRoots(context) : [];
  const runId = result.runId;
  const windowTitle = (await json(path.join(repo, 'desktop/src-tauri/tauri.conf.json'))).app.windows[0].title;
  result.profilePolicy = { windowsKnownFolders: true, sid: context.sid, roots, productionPathsOverridden: false };
  if (roots.length) await assertOwnedOrAbsent(roots, runId, context.sid);
  let index = 0;

  async function launch(name, kind, fault) {
    const scenarioRoot = path.join(work, `${++index}-${name}`);
    const scenarioEvidence = path.join(evidence, `${index}-${name}`);
    const packageRoot = options.usePackagedLayout ? path.dirname(options.hostPath) : path.join(scenarioRoot, 'package');
    const profile = path.join(scenarioRoot, 'profile');
    const fixture = path.join(scenarioRoot, 'fixture');
    if (!options.usePackagedLayout) await mkdir(packageRoot, { recursive: true });
    await mkdir(fixture, { recursive: true });
    const host = options.usePackagedLayout ? options.hostPath : path.join(packageRoot, path.basename(options.hostPath));
    if (!options.usePackagedLayout) await copyFile(options.hostPath, host);
    const backend = path.join(packageRoot, 'backend', 'chaoxing-backend.exe');
    const source = kind === 'fake' ? options.fakeBackendDirectory : options.backendDirectory;
    if (!options.usePackagedLayout && fault !== 'missing') {
      await safeCopyDirectory(source, path.dirname(backend));
      if (kind === 'fake') {
        await copyFile(path.join(path.dirname(backend), 'p2-backend.exe'), backend);
        await rm(path.join(path.dirname(backend), 'p2-backend.exe'));
      }
      if (fault === 'bad-executable') await writeFile(backend, 'P3 deliberately invalid Windows executable');
    }
    const port = await freePort();
    const env = sanitizedEnvironment(process.env, context, { configuration: options.configuration, profile, backend });
    env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port} --force-device-scale-factor=1`;
    if (kind === 'fake') Object.assign(env, { P2_FIXTURE_ROOT: fixture, P2_WEB_DIST: path.join(repo, 'web/dist') });
    if (fault === 'slow') env.P2_READY_DELAY_MS = '30000';
    const owner = new NativeSupervisor(context.powerShell, scenarioEvidence);
    const item = { scenario: name, host, expectedBackend: backend, configuration: options.configuration,
      packagedResourceLookup: options.configuration === 'Release', layout: options.usePackagedLayout ? 'input package executed in place' : 'owned fixture package copy',
      sanitizedPath: env.PATH, stdin: 'real pipe held by supervisor',
      outerJobAssignedBeforeResume: false, processes: [], cleanup: null };
    result.processes.push(item);
    let browser;
    const app = { owner, item, fixture, scenarioEvidence, profile, backend, kind,
      data: roots.length ? path.win32.join(context.roaming, 'com.chaoxing.gui', 'data') : path.join(profile, 'data') };
    app.installLogs = [path.join(path.dirname(backend), 'chaoxing.log'), path.join(path.dirname(backend), '_internal', 'chaoxing.log')];
    app.installLogBaseline = await Promise.all(app.installLogs.map(fingerprint));
    try {
      if (roots.length) await claimProfileRoots(roots, runId, context.sid);
      item.hostIdentity = await owner.start({ executable: host, args: [], cwd: packageRoot, env });
      item.outerJobAssignedBeforeResume = true;
      assert.equal(canonical(item.hostIdentity.executable), canonical(host));
      await until(async () => {
        await assertRunning(owner);
        return (await fetchBounded(`http://127.0.0.1:${port}/json/version`)).ok;
      }, 'real Tauri WebView2 CDP', options.timeoutSeconds * 1000);
      const listeners = await owner.command('tcp-listener', { port });
      const cdpProcesses = await owner.snapshot();
      assert.ok(listeners.length, 'WebView2 debugging listener is missing');
      for (const listener of listeners) assert.ok(cdpProcesses.active.some((identity) => identity.pid === listener.pid), 'Refusing a CDP listener outside the captured host Job');
      item.cdpListeners = listeners;
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 15000 });
      app.browser = browser;
      app.page = await until(() => browser.contexts().flatMap((entry) => entry.pages()).find((page) => /^https?:\/\/tauri\.localhost(?:\/|$)|^tauri:\/\/localhost(?:\/|$)/.test(page.url())), 'packaged Tauri page', 20000);
      app.page.setDefaultTimeout(15000);
      await app.page.waitForLoadState('domcontentloaded');
      return app;
    } catch (error) {
      if (browser) item.webviewPagesAtFailure = browser.contexts().flatMap((entry) => entry.pages()).map((page) => page.url());
      await browser?.close().catch(() => {});
      await withCleanup(item, async () => { throw error; }, async () => {
        item.cleanup = await owner.dispose();
        if (roots.length) await removeProfilesAfterVerifiedCleanup(roots, runId, context.sid, item.cleanup);
      });
    }
  }

  async function captureReady(app) {
    const status = await until(async () => {
      await assertRunning(app.owner);
      const current = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      if (current.phase === 'failed') throw fatal(`Backend failed: ${current.error}`);
      return current.phase === 'ready' && current;
    }, 'real host backend Ready', options.timeoutSeconds * 1000);
    assert.equal(Object.hasOwn(status, 'token'), false);
    assert.equal(Object.hasOwn(status, 'port'), false);
    const snapshot = await app.owner.snapshot();
    const backends = snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend));
    assert.ok(backends.length >= 1, 'The host must run the copied frozen backend executable');
    assert.equal(snapshot.host.inOuterJob, true);
    for (const identity of backends) assert.equal(identity.inOuterJob, true);
    app.backendIdentities = backends;
    app.item.ready = { status, snapshot, innerJobEvidence: 'backend Ready follows the host mandatory backend Job assignment; outer membership queried through Win32' };
    app.item.processes = snapshot.observed;
    record(`${app.item.scenario}-ready`, { backendIdentities: backends, nestedJobRequested: Boolean(options.nestedJob), outerJobActive: true });
  }

  async function close(app, force = false) {
    await withCleanup(app, async () => {
      const before = await app.owner.snapshot();
      app.item.processes = before.observed;
      await app.browser?.close();
      if (force) await app.owner.command('kill-host');
      else {
        const windows = await app.owner.command('close-window', { title: windowTitle });
        assert.equal(windows, 1, 'Normal shutdown must close exactly the main window of the captured PID/title');
      }
      const after = await waitForEmptyJob(app.owner, force ? 'forced host tree exit' : 'normal WM_CLOSE tree exit');
      app.item.shutdown = { mode: force ? 'terminate captured host handle' : 'WM_CLOSE exact captured PID and configured title', before, after,
        outerJobStillOpenAtObservation: true, noResidueBeforeFallback: true };
      record(`${app.item.scenario}-${force ? 'forced' : 'normal'}-no-residue`, { hostPid: before.host.pid, observed: after.observed });
    }, async () => {
      await app.browser?.close().catch(() => {});
      app.item.cleanup = await app.owner.dispose();
      for (const filename of ['web_config.json', 'renderer-session.json', 'chaoxing.log']) {
        await copyFile(path.join(app.data, filename), path.join(app.scenarioEvidence, filename)).catch((error) => { if (error.code !== 'ENOENT') throw error; });
      }
      if (roots.length) await removeProfilesAfterVerifiedCleanup(roots, runId, context.sid, app.item.cleanup);
    });
    assert.equal(app.item.cleanup.fallbackUsed, false, 'Fallback tree kill cannot satisfy the normal/forced lifecycle assertion');
  }

  async function use(name, kind, action, { fault, force = false, ready = true } = {}) {
    const app = await launch(name, kind, fault);
    await withCleanup(app, async () => {
      if (ready) await captureReady(app);
      try { await bounded(() => action(app), options.timeoutSeconds * 1000, `${name} assertions`); }
      catch (error) {
        await app.page.screenshot({ path: path.join(app.scenarioEvidence, 'failure.png') }).catch(() => {});
        await writeFile(path.join(app.scenarioEvidence, 'failure.txt'), `${error.stack}\n${await app.page.locator('body').innerText().catch(() => '')}`);
        throw error;
      }
    }, () => close(app, force));
  }

  if (options.scenario !== 'Frozen') {
    await use('fake-business', 'fake', (app) => syntheticBusiness(app, record));
    await use('fake-cancel', 'fake', (app) => syntheticCancellation(app, record));
    for (const fault of ['missing', 'bad-executable']) {
      await use(`fake-${fault}`, 'fake', async (app) => {
        await app.page.getByRole('button', { name: '重新检查' }).waitFor();
        const initial = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
        assert.equal(initial.phase, 'failed');
        assert.equal(await app.page.getByLabel('手机号').count(), 0);
        await activate(app.page.getByRole('button', { name: '重新检查' }));
        const again = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
        assert.equal(again.phase, 'failed');
        const snapshot = await app.owner.snapshot();
        assert.equal(snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend)).length, 0);
        record(`fake-${fault}-failed-without-restart`, { initial, again });
      }, { fault, ready: false });
    }
    await use('fake-close-starting', 'fake', async (app) => {
      const status = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      assert.equal(status.phase, 'starting');
      record('close-before-backend-ready', { status });
    }, { fault: 'slow', ready: false });
    await use('fake-backend-death', 'fake', async (app) => {
      for (const identity of app.backendIdentities) await app.owner.command('kill-member', { pid: identity.pid, createdAtFileTime: identity.createdAtFileTime });
      await app.page.getByRole('button', { name: '重新检查' }).waitFor();
      await activate(app.page.getByRole('button', { name: '重新检查' }));
      const status = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      assert.equal(status.phase, 'failed');
      const snapshot = await app.owner.snapshot();
      assert.equal(snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend)).length, 0);
      record('ready-backend-death-does-not-restart', { status });
    });
    await use('fake-force-host', 'fake', async () => {}, { force: true });
  }
  if (options.scenario !== 'Fake') {
    await use('frozen-contracts', 'frozen', (app) => frozenContracts(app, record));
    if (options.nestedJob) await use('frozen-force-host', 'frozen', async () => {}, { force: true });
  }
}

async function activate(locator) {
  await locator.waitFor({ state: 'visible' });
  await until(() => locator.isEnabled(), 'fixture button enabled');
  // CDP DOM click is stated explicitly; no claim of physical mouse input.
  await locator.evaluate((element) => element.click());
}

async function syntheticBusiness(app, record) {
  const { page } = app;
  await page.getByLabel('手机号').fill('p2-fixture');
  await page.getByLabel('密码', { exact: true }).fill('p3-synthetic-password');
  await activate(page.getByRole('button', { name: '登录', exact: true }));
  const course = page.getByRole('button', { name: /P2 测试课程一/ });
  await course.waitFor();
  assert.equal(await course.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByRole('button', { name: /P2 测试课程二/ }).getAttribute('aria-pressed'), 'false');
  await activate(page.getByRole('button', { name: '保存当前配置' }));
  await page.getByText('配置已保存', { exact: true }).waitFor();
  await activate(page.getByRole('button', { name: '开始学习', exact: true }));
  await page.getByText('p2-existing-task', { exact: true }).waitFor();
  await page.getByRole('log').getByText('P2 终态日志二', { exact: true }).waitFor();
  assert.equal(await page.getByRole('log').getByText('P2 唯一日志一', { exact: true }).count(), 1);
  const counters = await json(path.join(app.fixture, 'counts.json'));
  assert.equal(counters.start, 1);
  assert.equal(counters.configWrites, 1);
  assert.deepEqual(counters.after.slice(0, 3), [0, 1, 1]);
  const session = await page.evaluate(() => window.__TAURI__.core.invoke('session_read'));
  assert.equal(session.activeTask.taskId, 'p2-existing-task');
  assert.equal(JSON.stringify(session).includes('password'), false);
  await page.screenshot({ path: path.join(app.scenarioEvidence, 'synthetic-progress.png'), fullPage: true });
  await page.reload();
  await page.getByText('p2-existing-task', { exact: true }).waitFor();
  assert.equal((await json(path.join(app.fixture, 'counts.json'))).start, 1);
  await writeFile(path.join(app.fixture, 'control.json'), JSON.stringify({ missing: true }));
  await page.reload();
  await page.getByText('已过期', { exact: true }).first().waitFor();
  await until(async () => (await page.evaluate(() => window.__TAURI__.core.invoke('session_read'))).activeTask === null, 'missing task clears saved task');
  assert.equal((await page.evaluate(() => window.__TAURI__.core.invoke('session_read'))).login.username, 'p2-fixture');
  record('synthetic-login-config-start409-after-once-refresh404', { counters, upstreamAccountsUsed: false, input: 'visible/enabled DOM click over CDP' });
}

async function syntheticCancellation(app, record) {
  await writeFile(path.join(app.fixture, 'control.json'), JSON.stringify({ delayMs: 1000 }));
  const cancellation = await app.page.evaluate(async () => {
    const invoke = window.__TAURI__.core.invoke;
    await invoke('api_cancel', { requestId: 980001 });
    const early = await invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 980001 } }).then(() => 'success', (error) => error.kind);
    const pending = invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 980002 } }).then(() => 'success', (error) => error.kind);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const before = performance.now();
    const status = await invoke('backend_status');
    const statusMs = performance.now() - before;
    await invoke('api_cancel', { requestId: 980002 });
    return { early, inflight: await pending, phase: status.phase, statusMs };
  });
  assert.equal(cancellation.early, 'cancelled');
  assert.equal(cancellation.inflight, 'cancelled');
  assert.equal(cancellation.phase, 'ready');
  assert.ok(cancellation.statusMs < 750, 'Backend status must remain responsive while HTTP waits');
  await writeFile(path.join(app.fixture, 'control.json'), '{}');
  record('synthetic-cancellation-before-and-during-request', cancellation);
}

async function frozenContracts(app, record) {
  const contract = await app.page.evaluate(async () => {
    const invoke = window.__TAURI__.core.invoke;
    let id = 990001;
    const request = (operation, payload = null, extra = {}) => invoke('api_request', { request: { operation, payload, requestId: id++, ...extra } });
    const read = await request('configRead');
    const write = await request('configWrite', { settings: { jobs: 2 }, selectedCoursesByAccount: { 'p3-config-fixture': ['course-1'] } });
    const reread = await request('configRead');
    const invalid = [];
    // Empty inputs fail backend validation before creating an upstream client.
    for (const operation of ['login', 'courses', 'start']) invalid.push([operation, await request(operation, {})]);
    const missing = [];
    for (const operation of ['taskStatus', 'taskDetails', 'taskLogs']) missing.push([operation, await request(operation, null, { taskId: 'p3-never-created', ...(operation === 'taskLogs' ? { after: 0 } : {}) })]);
    return { read, write, reread, invalid, missing };
  });
  assert.equal(contract.read.status, 200);
  assert.equal(contract.write.status, 200);
  assert.equal(contract.reread.body.data.settings.jobs, 2);
  for (const [operation, response] of contract.invalid) assert.equal(response.status, 400, operation);
  for (const [operation, response] of contract.missing) assert.equal(response.status, 404, operation);
  const stored = await json(path.join(app.data, 'web_config.json'));
  assert.deepEqual(stored.selectedCoursesByAccount, { 'p3-config-fixture': ['course-1'] });
  assert.ok((await lstat(path.join(app.data, 'chaoxing.log'))).isFile());
  assert.deepEqual(await Promise.all(app.installLogs.map(fingerprint)), app.installLogBaseline);
  record('frozen-config-read-write-empty400-missing404', { contract, logInDataDirectory: true, installLogsUnchanged: true, upstreamAccountsUsed: false, learningTaskCreated: false });
}

async function runPython(options, context, evidence, result, record) {
  const fixtureRoot = path.join(evidence, 'fixture');
  const data = path.join(fixtureRoot, 'data');
  const installed = path.join(fixtureRoot, 'package');
  await mkdir(data, { recursive: true });
  const executable = path.join(installed, path.basename(options.executablePath));
  if (options.mode === 'Backend') await safeCopyDirectory(path.dirname(options.executablePath), installed);
  else { await mkdir(installed); await copyFile(options.executablePath, executable); }
  const env = sanitizedEnvironment(process.env, context, { configuration: 'Release' });
  Object.assign(env, { CHAOXING_HEADLESS: '1', CHAOXING_DATA_DIR: data, PYTHONIOENCODING: 'utf-8' });
  let port;
  let token;
  let instance;
  if (options.mode === 'Backend') {
    token = randomBytes(32).toString('hex'); instance = randomUUID();
    Object.assign(env, { CHAOXING_TAURI: '1', CHAOXING_TAURI_TOKEN: token, CHAOXING_TAURI_INSTANCE_ID: instance });
  } else { port = await freePort(); env.CHAOXING_PORT = String(port); }
  const owner = new NativeSupervisor(context.powerShell, path.join(evidence, 'process'));
  const item = { executable, mode: options.mode, data, sanitizedPath: env.PATH, headless: true, stdin: 'real pipe', process: null };
  result.processes.push(item);
  try {
    item.process = await owner.start({ executable, args: [], cwd: data, env });
    if (options.mode === 'Backend') {
      const ready = await until(async () => {
        await assertRunning(owner);
        const lines = (await readable(path.join(evidence, 'process/stdout.log'))).split(/\r?\n/);
        for (const line of lines) {
          let value; try { value = JSON.parse(line); } catch { continue; }
          if (value.ready !== 'chaoxing-ready') continue;
          if (value.version !== 1 || value.instanceId !== instance || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535) throw fatal('Frozen backend emitted an invalid ready handshake');
          return value;
        }
        return false;
      }, 'frozen backend ready handshake', options.timeoutSeconds * 1000);
      port = ready.port;
      record('python-backend-validated-ready-handshake', { version: ready.version, instanceMatched: true, dynamicPort: true });
    }
    const url = `http://127.0.0.1:${port}`;
    const headers = token ? { 'X-Auth-Token': token } : {};
    await until(async () => {
      await assertRunning(owner);
      const response = await fetchBounded(`${url}/api/health`, { headers });
      if (response.status !== 200) return false;
      const body = await response.json();
      if (token) assert.equal(body.instanceId, instance);
      return body.status === true;
    }, 'frozen Python health endpoint', options.timeoutSeconds * 1000);
    const listeners = await owner.command('tcp-listener', { port });
    const listeningState = await owner.snapshot();
    assert.ok(listeners.length, 'No loopback listener belongs to the captured frozen executable');
    for (const listener of listeners) assert.ok(listeningState.active.some((identity) => identity.pid === listener.pid && canonical(identity.executable) === canonical(executable)), 'A different local process owns the health endpoint');
    item.listeners = listeners;
    const staticResponse = await fetchBounded(`${url}/`, { headers });
    assert.equal(staticResponse.status, 200, 'Packaged Python must serve its bundled web frontend');
    assert.match(await staticResponse.text(), /<html|<!doctype/i);
    if (token) assert.equal((await fetchBounded(`${url}/api/health`)).status, 401);
    const read = await fetchBounded(`${url}/api/config`, { headers });
    assert.equal(read.status, 200);
    const write = await fetchBounded(`${url}/api/config`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: { jobs: 2 } }) });
    assert.equal(write.status, 200);
    for (const route of ['login', 'courses', 'start']) {
      const response = await fetchBounded(`${url}/api/${route}`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: '{}' });
      assert.equal(response.status, 400, route);
    }
    for (const route of ['task/p3-never-created', 'task/p3-never-created/details', 'logs/p3-never-created?after=0']) assert.equal((await fetchBounded(`${url}/api/${route}`, { headers })).status, 404, route);
    assert.equal((await json(path.join(data, 'web_config.json'))).settings.jobs, 2);
    item.ready = await owner.snapshot();
    await owner.command('stdin-eof');
    item.shutdown = await waitForEmptyJob(owner, 'Python stdin EOF tree exit', 15000);
    record(`python-${options.mode.toLowerCase()}-health-static-config-validation-eof`, { upstreamAccountsUsed: false, learningTaskCreated: false, before: item.ready, after: item.shutdown });
  } finally { item.cleanup = await owner.dispose(); }
  assert.equal(item.cleanup.fallbackUsed, false, 'Python EOF must exit without the supervisor killing the tree');
}

export async function main(argv = process.argv.slice(2)) {
  // Reserve a new directory even when parsing or the release guard fails. Never
  // overwrite P2 or a previous P3 result, including on invalid scenario input.
  const evidenceIndex = argv.indexOf('--evidence-directory');
  const requestedEvidence = evidenceIndex >= 0 && argv[evidenceIndex + 1] && !argv[evidenceIndex + 1].startsWith('--')
    ? argv[evidenceIndex + 1] : path.join(repo, 'desktop/src-tauri/target/p3-smoke-evidence');
  await mkdir(path.resolve(requestedEvidence), { recursive: true });
  const evidence = await mkdtemp(path.join(path.resolve(requestedEvidence), 'smoke-'));
  const result = { runId: randomUUID(), startedAt: new Date().toISOString(), success: false, checks: [], processes: [],
    limitations: ['DOM clicks over CDP do not represent physical mouse input', 'Debug execution does not qualify as release or clean-system validation'] };
  const record = (name, details = {}) => { result.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
  let options;
  try {
    options = parseArguments(argv);
    result.selection = { kind: options.kind, configuration: options.configuration, scenario: options.scenario, mode: options.mode,
      nestedJob: Boolean(options.nestedJob), usePackagedLayout: Boolean(options.usePackagedLayout) };
    result.releasePermission = assertReleasePermission(options);
    const context = await windowsContext(options.powerShell);
    if (options.kind === 'tauri' && options.configuration === 'Release') {
      result.profileOwnership = await publishReleaseProfileOwnership(evidence, context, result.runId);
    }
    options.backendDirectory = path.resolve(options.backendDirectory || path.join(repo, 'desktop/src-tauri/resources/backend'));
    options.fakeBackendDirectory = path.resolve(options.fakeBackendDirectory || path.join(repo, 'desktop/src-tauri/target/p2-fixture/dist/p2-backend'));
    if (options.hostPath) options.hostPath = path.resolve(options.hostPath);
    if (options.executablePath) options.executablePath = path.resolve(options.executablePath);
    await validateInputs(options);
    if (options.kind === 'tauri') {
      await probeBuildProfile(options, context, evidence, result, record);
      await runTauri(options, context, evidence, result, record);
    }
    else await runPython(options, context, evidence, result, record);
    assert.ok(result.checks.length, 'A smoke run must execute at least one assertion');
    result.success = true;
  } catch (error) { result.error = error.stack; console.error(error.stack); }
  finally {
    result.endedAt = new Date().toISOString();
    await writeFile(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2), { flag: 'wx' });
    console.log(`Evidence: ${path.join(evidence, 'result.json')}`);
  }
  return result.success ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) process.exitCode = await main();
