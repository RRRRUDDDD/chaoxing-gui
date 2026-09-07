// Real Chromium / original Electron / Tauri P2 smoke with synthetic accounts.
// Prerequisites: web build; cargo build -j1 --features custom-protocol; the
// p2_backend.py fixture frozen as target/p2-fixture/dist/p2-backend/; and
// playwright-core + Electron in P2_TOOLS_DIR (kept outside the repository).
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, readFile, writeFile, copyFile, stat } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireTools = createRequire(path.join(process.env.P2_TOOLS_DIR || path.join(os.tmpdir(), 'chaoxing-p2-tools'), 'package.json'));
const { chromium, _electron } = requireTools('playwright-core');
const evidence = path.resolve(process.env.P2_EVIDENCE_DIR || path.join(repo, 'desktop/src-tauri/target/p2-smoke-evidence'));
const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p2-smoke-'));
const fixture = path.join(repo, 'desktop/tests/fixtures/p2_backend.py');
const fakeExe = path.join(repo, 'desktop/src-tauri/target/p2-fixture/dist/p2-backend/p2-backend.exe');
const hostExe = path.join(repo, 'desktop/src-tauri/target/debug/chaoxing-desktop.exe');
await mkdir(evidence, { recursive: true });
const results = { startedAt: new Date().toISOString(), profileRoot: root,
  driver: 'Real browser engines over CDP; DOM click after visible/enabled checks (native mouse events are not delivered in this Windows desktop session)', checks: [] };
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const exited = (child) => child.exitCode !== null || child.signalCode !== null;

async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  let error;
  while (Date.now() < deadline) {
    try { const result = await check(); if (result) return result; } catch (err) { error = err; }
    await pause(100);
  }
  throw new Error(`${label} timed out${error ? `: ${error.message}` : ''}`);
}
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
async function activate(locator) {
  await locator.waitFor({ state: 'visible' });
  await until(() => locator.isEnabled(), 'button enabled');
  await locator.evaluate((element) => element.click());
}
const record = (name, details) => { results.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
async function freePort() {
  const { createServer } = await import('node:net');
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
function fixtureEnv(profile, extra = {}) {
  const env = { ...process.env, P2_FIXTURE_ROOT: profile, P2_WEB_DIST: path.join(repo, 'web/dist'),
    PYTHONIOENCODING: 'utf-8', CHAOXING_PORT: '0', ...extra };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}
async function stopFixture(child, profile) {
  if (!Number.isInteger(child.pid)) return; // The executable itself did not spawn.
  child.stdin?.end();
  try { await until(() => exited(child), 'fixture exit', 5000); }
  finally {
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'fixture cleanup', 5000);
  }
  const runtime = await json(path.join(profile, 'runtime.json')).catch(() => null);
  if (runtime) assert.equal(alive(runtime.pid), false, `orphan fixture ${runtime.pid}`);
}

async function launch(kind, options = {}) {
  const profile = options.profile || path.join(root, `${kind}-${Date.now()}`);
  const backendProfile = path.join(profile, 'fixture');
  await mkdir(backendProfile, { recursive: true });
  const env = fixtureEnv(backendProfile, options.env);
  if (kind === 'browser') {
    const child = spawn(process.env.P2_PYTHON || 'python', [fixture], { env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    child.stdout.resume(); child.stderr.resume();
    child.stdin.on('error', () => {}); // EPIPE is expected if startup already exited.
    let browser;
    const stop = async () => {
      try { await browser?.close(); }
      finally { await stopFixture(child, backendProfile); }
    };
    try {
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      const runtime = await until(() => json(path.join(backendProfile, 'runtime.json')), 'browser fixture ready');
      browser = await chromium.launch({ executablePath: process.env.P2_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
      const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
      await page.goto(`http://127.0.0.1:${runtime.port}`);
      return { kind, profile, backendProfile, page, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; browser cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  if (kind === 'electron') {
    env.P2_ELECTRON_PROFILE = path.join(profile, 'electron-data');
    let electron;
    let child;
    const stop = async () => {
      // The original Electron shell owns the stdin pipe. Even if Playwright
      // setup fails, closing its captured process releases the fixture watchdog.
      try { await electron?.close(); }
      finally {
        try {
          if (child) await until(() => exited(child), 'Electron host exit', 5000);
        } finally {
          if (child && !exited(child)) child.kill();
          if (child) await until(() => !alive(child.pid), 'Electron host cleanup', 5000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          if (runtime) await until(() => !alive(runtime.pid), 'Electron backend exit', 6000);
        }
      }
    };
    try {
      electron = await _electron.launch({ executablePath: requireTools('electron'),
        args: [path.join(repo, 'desktop/tests/fixtures/p2-electron.cjs')], env, timeout: 30000 });
      child = electron.process();
      const page = await electron.firstWindow();
      await page.waitForURL((url) => url.hostname === '127.0.0.1', { timeout: 30000 });
      await page.waitForLoadState('domcontentloaded');
      return { kind, profile, backendProfile, page, data: env.P2_ELECTRON_PROFILE, electron, pid: child.pid, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; Electron cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  const port = await freePort();
  Object.assign(env, { CHAOXING_TAURI_DEV_ROOT: profile, CHAOXING_TAURI_DEV_HIDDEN: process.env.P2_HIDE_WINDOW || '0',
    CHAOXING_TAURI_DEV_BACKEND: options.backend || fakeExe,
    CHAOXING_LEGACY_DATA_DIR: options.legacy || path.join(profile, 'no-legacy'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --force-device-scale-factor=1` });
  const child = spawn(hostExe, [], { cwd: path.join(repo, 'desktop/src-tauri'), env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  const backendPids = [];
  child.stdout.on('data', (data) => { diagnostics += data; });
  child.stderr.on('data', (data) => { diagnostics += data; });
  let browser;
  try {
    await until(async () => {
      if (exited(child)) throw new Error(`host exited ${child.exitCode ?? child.signalCode}: ${diagnostics}`);
      return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok;
    }, 'Tauri CDP', 30000);
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const page = await until(() => browser.contexts()[0]?.pages().find((p) => p.url().includes('tauri.localhost')), 'Tauri page');
    await page.waitForLoadState('domcontentloaded');
    return { kind, profile, backendProfile, data: path.join(profile, 'data'), page, child, backendPids,
      stop: async ({ force = false } = {}) => {
        const started = Date.now();
        try {
          // Detach the test debugger before exercising normal window teardown.
          await browser.close();
          if (!exited(child)) {
            if (force) child.kill();
            else await exec('pwsh', ['-NoProfile', '-File', path.join(repo, 'desktop/scripts/p2-close-window.ps1'), '-HostProcessId', String(child.pid)], { windowsHide: true, timeout: 8000 });
          }
          await until(() => exited(child) && !alive(child.pid), 'Tauri host exit', 8000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          const observedPids = [...new Set([...backendPids, ...(runtime ? [runtime.pid] : [])])];
          for (const pid of observedPids) await until(() => !alive(pid), 'Tauri backend exit', 6000);
          record(force ? 'tauri-forced-host-cleanup' : 'tauri-normal-window-close', {
            hostPid: child.pid, backendPids: observedPids, elapsedMs: Date.now() - started,
            exitCode: child.exitCode, signal: child.signalCode,
          });
        } finally {
          await browser.close().catch(() => {});
          // A failed assertion must not strand the captured test host/CDP client.
          // This fallback cannot turn a failed normal-close assertion into PASS.
          if (!exited(child)) child.kill();
          await until(() => !alive(child.pid), 'Tauri failed-test cleanup', 5000);
          await writeFile(path.join(evidence, `host-${path.basename(profile)}.txt`), diagnostics);
        }
      } };
  } catch (error) {
    await browser?.close().catch(() => {});
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'Tauri launch cleanup', 5000);
    throw new Error(`${error.message}; host diagnostics=${diagnostics}`);
  }
}

async function savedSession(app) {
  return app.page.evaluate(async (kind) => {
    if (kind === 'tauri') return window.__TAURI__.core.invoke('session_read');
    if (kind === 'electron') return window.chaoxingSession.read();
    return JSON.parse(localStorage.getItem('chaoxing_session_v1')) || { version: 1, login: null, activeTask: null };
  }, app.kind);
}

async function business(kind) {
  const app = await launch(kind);
  const { page } = app;
  page.setDefaultTimeout(15000);
  const requests = [];
  page.on('request', (request) => { if (request.url().includes('/api/')) requests.push({ method: request.method(), url: request.url() }); });
  try {
    await page.evaluate(() => {
      window.p2ClickEvents = [];
      document.addEventListener('click', (event) => {
        window.p2ClickEvents.push({ tag: event.target.tagName, id: event.target.id, x: event.clientX, y: event.clientY });
      }, true);
    });
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    const first = page.getByRole('button', { name: /P2 测试课程一/ });
    await first.waitFor();
    assert.equal(await first.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', { name: /P2 测试课程二/ }).getAttribute('aria-pressed'), 'false');
    await activate(page.getByRole('button', { name: '保存当前配置' }));
    await page.getByText('配置已保存', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    await page.getByRole('log').getByText('P2 终态日志二', { exact: true }).waitFor();
    assert.equal(await page.getByRole('log').getByText('P2 唯一日志一', { exact: true }).count(), 1);
    const beforeRefresh = await json(path.join(app.backendProfile, 'counts.json'));
    assert.equal(beforeRefresh.start, 1);
    assert.equal(beforeRefresh.configWrites, 1);
    assert.deepEqual(beforeRefresh.after.slice(0, 3), [0, 1, 1]);
    const session = await savedSession(app);
    assert.equal(session.activeTask.taskId, 'p2-existing-task');
    assert.equal(JSON.stringify(session).includes('password'), false);
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-progress.png`), fullPage: true });
    await page.reload();
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    assert.equal((await json(path.join(app.backendProfile, 'counts.json'))).start, 1);
    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ missing: true }));
    await page.reload();
    await page.getByText('已过期', { exact: true }).first().waitFor();
    await until(async () => (await savedSession(app)).activeTask === null, '404 clears only task');
    assert.equal((await savedSession(app)).login.username, 'p2-fixture');
    await activate(page.getByRole('button', { name: '返回课程选择', exact: true }).last());
    await activate(page.getByRole('button', { name: '退出登录', exact: true }));
    await page.getByRole('button', { name: '登录', exact: true }).waitFor();
    assert.equal((await savedSession(app)).login, null);
    record(`${kind}-business`, { transport: kind === 'tauri' ? 'native invoke -> HTTP fixture' : 'HTTP fixture',
      cases: ['account selection', 'config save', '409 restore without repeat start', 'terminal log retry', 'after cursor dedup', 'refresh restore', '404 clears task', 'logout clears account'], counters: beforeRefresh });
  } catch (error) {
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-error.png`), fullPage: true }).catch(() => {});
    await writeFile(path.join(evidence, `p2-${kind}-error.json`), JSON.stringify({
      url: page.url(), body: await page.locator('body').innerText().catch(() => ''),
      session: await savedSession(app).catch((err) => err.message),
      requests, clicks: await page.evaluate(() => ({ events: window.p2ClickEvents, width: innerWidth, height: innerHeight, scale: devicePixelRatio })),
    }, null, 2));
    throw error;
  } finally { await app.stop(); }
}

async function startupFailures() {
  const missing = await launch('tauri', { backend: path.join(root, 'missing-backend.exe') });
  try {
    await missing.page.getByRole('button', { name: '重新检查' }).waitFor();
    assert.equal(await missing.page.getByLabel('手机号').count(), 0);
    await activate(missing.page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await missing.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'failed remains failed');
    await missing.page.screenshot({ path: path.join(evidence, 'p2-tauri-failed.png') });
    record('tauri-missing-backend-recheck', { restarted: false });
  } finally { await missing.stop(); }
  const slow = await launch('tauri', { env: { P2_READY_DELAY_MS: '8000' } });
  try {
    const status = await slow.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'starting');
    assert.equal(await slow.page.getByLabel('手机号').count(), 0);
    await slow.page.screenshot({ path: path.join(evidence, 'p2-tauri-starting.png') });
  } finally { await slow.stop(); }
  record('tauri-close-during-startup', { closedBeforeReady: true });
}

async function migrationRollback() {
  const legacy = await launch('electron');
  let target;
  try {
    await legacy.page.evaluate(async () => {
      await window.chaoxingSession.rememberLogin('p2-fixture');
      await window.chaoxingSession.rememberTask({ username: 'p2-fixture', taskId: 'p2-existing-task' });
    });
    await writeFile(path.join(legacy.data, 'web_config.json'), JSON.stringify({ selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } }));
    const before = await readFile(path.join(legacy.data, 'renderer-session.json'));
    target = await launch('tauri', { legacy: legacy.data });
    await target.page.getByRole('button', { name: '重新检查' }).waitFor();
    const status = await target.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'failed');
    assert.match(status.error, /关闭旧版/);
    await assert.rejects(stat(path.join(target.backendProfile, 'pid.json')), { code: 'ENOENT' });
    await target.stop(); target = null;
    await legacy.stop();
    const imported = await launch('tauri', { legacy: legacy.data });
    try {
      await imported.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(imported)).login.username, 'p2-fixture');
      assert.deepEqual(await json(path.join(imported.data, 'web_config.json')), { selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } });
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      const nodeStore = createRequire(path.join(repo, 'desktop/package.json'))('./session-store.js');
      assert.equal(new nodeStore.SessionStore(imported.data).read().login.username, 'p2-fixture');
      assert.equal(new nodeStore.SessionStore(legacy.data).read().activeTask.taskId, 'p2-existing-task');
      assert.ok((await stat(path.join(imported.data, 'migration-v1.done'))).isFile());
    } finally { await imported.stop(); }
    // Exercise the original shell again against the preserved original profile.
    const rollback = await launch('electron', { profile: legacy.profile });
    try {
      await rollback.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(rollback)).login.username, 'p2-fixture');
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      record('old-electron-import-and-rollback', { runningLegacyDeferred: true, sourceUnchanged: true,
        nodeReadsRustSession: true, originalElectronRelaunched: true,
        sourceSha256: createHash('sha256').update(before).digest('hex') });
    } finally { await rollback.stop(); }
  } finally {
    if (target) await target.stop();
    if (alive(legacy.pid)) await legacy.stop();
  }
}

async function nativeContracts() {
  const app = await launch('tauri');
  const { page } = app;
  try {
    await page.getByLabel('手机号').waitFor();
    const rejected = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      const cases = [
        ['unknown command', 'p2_unknown_command', {}],
        ['unknown status field', 'backend_status', { url: 'http://example.invalid' }],
        ['positional envelope', 'session_remember_login', ['p2-fixture']],
        ['credentials in session', 'session_remember_login', { username: 'p2-fixture', password: 'synthetic' }],
        ['missing nullable key', 'session_remember_task', {}],
        ['positional task', 'session_remember_task', { task: ['p2-fixture', 'task'] }],
        ['positional request', 'api_request', { request: ['configRead', null, 701] }],
        ['object operation', 'api_request', { request: { operation: { configRead: null }, payload: null, requestId: 702 } }],
        ['unknown URL field', 'api_request', { request: { operation: 'configRead', payload: null, requestId: 703, url: 'http://example.invalid' } }],
        ['path traversal', 'api_request', { request: { operation: 'taskStatus', payload: null, requestId: 704, taskId: '../task' } }],
        ['invalid cancel ID', 'api_cancel', { requestId: 0 }],
        ['ungranted window command', 'plugin:webview|create_webview_window', { options: { label: 'p2-untrusted', url: 'about:blank', visible: false } }],
      ];
      const results = [];
      for (const [name, command, args] of cases) {
        try { await invoke(command, args); results.push({ name, rejected: false }); }
        catch { results.push({ name, rejected: true }); }
      }
      return results;
    });
    for (const result of rejected) assert.equal(result.rejected, true, result.name);
    assert.deepEqual(await savedSession(app), { version: 1, login: null, activeTask: null });
    const frameDirective = await page.evaluate(() => new Promise((resolve) => {
      const frame = document.createElement('iframe');
      let timer;
      const finish = (value) => {
        clearTimeout(timer);
        window.removeEventListener('securitypolicyviolation', onViolation);
        frame.remove();
        resolve(value);
      };
      const onViolation = (event) => { if (event.effectiveDirective === 'frame-src') finish(event.effectiveDirective); };
      window.addEventListener('securitypolicyviolation', onViolation);
      timer = setTimeout(() => finish('not blocked'), 1500);
      frame.src = location.href;
      document.body.append(frame);
    }));
    assert.equal(frameDirective, 'frame-src');
    const originalUrl = page.url();
    await page.evaluate(() => { location.href = 'https://example.invalid/p2-blocked'; });
    await pause(250);
    assert.equal(page.url(), originalUrl);
    record('tauri-native-ipc-boundaries', { rejected, frameDirective, foreignNavigationBlocked: true });

    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ delayMs: 1000 }));
    const cancellations = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      await invoke('api_cancel', { requestId: 801 });
      const early = await invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 801 } }).then(() => 'success', (error) => error.kind);
      const pending = invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 802 } }).then(() => 'success', (error) => error.kind);
      await new Promise((resolve) => setTimeout(resolve, 100));
      const started = performance.now();
      const status = await invoke('backend_status');
      const statusMs = performance.now() - started;
      await invoke('api_cancel', { requestId: 802 });
      return { early, inflight: await pending, status: status.phase, statusMs };
    });
    assert.equal(cancellations.early, 'cancelled');
    assert.equal(cancellations.inflight, 'cancelled');
    assert.equal(cancellations.status, 'ready');
    assert.ok(cancellations.statusMs < 750, 'status must remain responsive during HTTP');
    await writeFile(path.join(app.backendProfile, 'control.json'), '{}');
    record('tauri-native-cancellation', cancellations);

    await page.evaluate(() => localStorage.setItem('chaoxing_session_v1', 'p2-unchanged-local-sentinel'));
    // An existing directory at the temporary file path forces a real Windows IO
    // failure without changing permissions or touching any user account profile.
    await mkdir(path.join(app.data, 'renderer-session.json.tmp'));
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    await page.getByText('账号记忆未能保存，刷新后可能需要重新登录', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    assert.equal((await savedSession(app)).login, null);
    await page.screenshot({ path: path.join(evidence, 'p2-tauri-storage-failure.png') });
    await mkdir(path.join(app.data, 'renderer-session.json'));
    await page.reload();
    await page.getByText('读取保存的账号失败，请手动登录', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    record('tauri-native-storage-failure', { readFailureVisible: true, loginFailureVisible: true, taskFailureVisible: true, localStorageUnchanged: true });

    const runtime = await json(path.join(app.backendProfile, 'pid.json'));
    process.kill(runtime.pid);
    await page.getByRole('button', { name: '重新检查' }).waitFor();
    await activate(page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'backend death remains failed');
    assert.equal(alive(runtime.pid), false);
    assert.equal((await json(path.join(app.backendProfile, 'pid.json'))).pid, runtime.pid);
    assert.equal(await page.getByLabel('手机号').count(), 0);
    record('tauri-ready-backend-death', { restarted: false, failedStateVisible: true });
  } finally { await app.stop(); }
}

async function realFrozenBackend() {
  const backend = path.join(repo, 'dist/chaoxing-backend/chaoxing-backend.exe');
  const fingerprint = async (file) => readFile(file).then((bytes) => createHash('sha256').update(bytes).digest('hex')).catch((error) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  const installLogs = [path.join(path.dirname(backend), 'chaoxing.log'), path.join(path.dirname(backend), '_internal/chaoxing.log')];
  const before = await Promise.all(installLogs.map(fingerprint));
  assert.ok((await stat(path.join(path.dirname(backend), '_internal'))).isDirectory());
  const app = await launch('tauri', { backend });
  try {
    await app.page.getByLabel('手机号').waitFor({ timeout: 120000 });
    const { stdout } = await exec('pwsh', ['-NoProfile', '-Command',
      `@(Get-CimInstance Win32_Process -Filter 'ParentProcessId=${app.child.pid}' | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress -AsArray`], { windowsHide: true, timeout: 10000 });
    const children = JSON.parse(stdout);
    const actualBackend = children.filter((child) => path.resolve(child.ExecutablePath).toLowerCase() === backend.toLowerCase());
    assert.equal(actualBackend.length, 1, 'host must spawn the actual frozen backend');
    app.backendPids.push(actualBackend[0].ProcessId);
    const contract = await app.page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      let id = 1001;
      const request = (operation, payload = null, extra = {}) => invoke('api_request', { request: { operation, payload, requestId: id++, ...extra } });
      const status = await invoke('backend_status');
      const read = await request('configRead');
      const write = await request('configWrite', { settings: { jobs: 2 }, selectedCoursesByAccount: { 'p2-config-fixture': ['course-1'] } });
      const reread = await request('configRead');
      const invalid = [];
      // All three fail local validation before any account client/task is created.
      for (const operation of ['login', 'courses', 'start']) invalid.push([operation, await request(operation, {})]);
      const missing = [];
      for (const operation of ['taskStatus', 'taskDetails', 'taskLogs']) missing.push([operation, await request(operation, null, { taskId: 'p2-never-created', ...(operation === 'taskLogs' ? { after: 0 } : {}) })]);
      return { status, read, write, reread, invalid, missing };
    });
    assert.equal(contract.status.phase, 'ready');
    assert.equal(Object.hasOwn(contract.status, 'port'), false);
    assert.equal(Object.hasOwn(contract.status, 'token'), false);
    assert.equal(contract.read.status, 200);
    assert.equal(contract.write.status, 200);
    assert.equal(contract.reread.body.data.settings.jobs, 2);
    for (const [name, response] of contract.invalid) assert.equal(response.status, 400, name);
    for (const [name, response] of contract.missing) assert.equal(response.status, 404, name);
    const stored = await json(path.join(app.data, 'web_config.json'));
    assert.deepEqual(stored.selectedCoursesByAccount, { 'p2-config-fixture': ['course-1'] });
    const dataLog = path.join(app.data, 'chaoxing.log');
    assert.ok((await stat(dataLog)).isFile());
    assert.deepEqual(await Promise.all(installLogs.map(fingerprint)), before);
    await copyFile(dataLog, path.join(evidence, 'p2-frozen-chaoxing.log'));
    record('tauri-real-frozen-backend', { backend, backendPid: actualBackend[0].ProcessId, contract,
      logInDataDirectory: true, installLogsUnchanged: true, upstreamAccountsUsed: false, learningTaskCreated: false });
  } finally { await app.stop(); }
}

const selection = process.argv[2] || 'all';
try {
  assert.ok(['all', 'browser', 'electron', 'tauri', 'failures', 'migration', 'native', 'frozen'].includes(selection), `Unknown smoke selection: ${selection}`);
  for (const kind of ['browser', 'electron', 'tauri']) {
    if (selection === 'all' || selection === kind) await business(kind);
  }
  if (selection === 'all' || selection === 'failures') await startupFailures();
  if (selection === 'all' || selection === 'migration') await migrationRollback();
  if (selection === 'all' || selection === 'native') await nativeContracts();
  if (selection === 'all' || selection === 'frozen') await realFrozenBackend();
  results.success = true;
} catch (error) {
  results.success = false;
  results.error = error.stack;
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  results.endedAt = new Date().toISOString();
  await writeFile(path.join(evidence, `p2-smoke-${selection}.json`), JSON.stringify(results, null, 2));
  await writeFile(path.join(evidence, `p2-smoke-${selection}-${results.startedAt.replace(/[^0-9]/g, '')}.json`), JSON.stringify(results, null, 2));
  console.log(`Evidence: ${path.join(evidence, `p2-smoke-${selection}.json`)}`);
}
