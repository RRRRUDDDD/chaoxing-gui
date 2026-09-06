const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { SessionStore, isTrustedSender, registerSessionIpc } = require('../session-store');

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-session-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return { directory, store: new SessionStore(directory) };
}

function trustedWindow(url = 'http://127.0.0.1:42001/') {
  const mainFrame = { url };
  const window = { isDestroyed: () => false, webContents: { mainFrame } };
  return { window, event: { sender: window.webContents, senderFrame: mainFrame } };
}

test('account and task persist across process/origin changes; logout removes them', (t) => {
  const { directory, store } = fixture(t);
  store.rememberLogin('alice');
  store.rememberTask({ username: 'alice', taskId: 'task-one' });
  const restarted = new SessionStore(directory);
  assert.deepEqual(restarted.read(), { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: { username: 'alice', taskId: 'task-one' } });
  const disk = fs.readFileSync(store.filename, 'utf8');
  assert.equal(disk.includes('password'), false);
  assert.equal(disk.includes('42001'), false);
  fs.writeFileSync(`${store.filename}.tmp`, disk);
  restarted.clear();
  assert.equal(fs.existsSync(store.filename), false);
  assert.equal(fs.existsSync(`${store.filename}.tmp`), false);
  assert.equal(store.read().login, null);
});

test('payloads reject passwords, extra fields, oversized values and account mismatches', (t) => {
  const { store } = fixture(t);
  store.rememberLogin('alice');
  for (const value of ['', ' alice ', 'a'.repeat(129), 'a\nb', { username: 'alice', password: 'test-only' }, ['alice']]) {
    assert.throws(() => store.rememberLogin(value));
  }
  for (const task of [{ username: 'bob', taskId: 'one' }, { username: 'alice', taskId: '../one' }, { username: 'alice', taskId: 'x'.repeat(129) }, { username: 'alice', taskId: 'one', password: 'test-only' }]) {
    assert.throws(() => store.rememberTask(task));
  }
  store.rememberTask({ username: 'alice', taskId: 'one' });
  store.rememberLogin('bob');
  assert.equal(store.read().activeTask, null);
});

test('corrupt, oversized and credential-bearing disk records are never exposed', (t) => {
  const { store } = fixture(t);
  for (const data of ['not json', 'x'.repeat(4097), JSON.stringify({ version: 1, login: { username: 'alice', use_cookies: true, password: 'test-only' }, activeTask: null })]) {
    fs.writeFileSync(store.filename, data);
    assert.deepEqual(store.read(), { version: 1, login: null, activeTask: null });
  }
});

test('IPC trusts only the current main window main frame and exact backend origin', () => {
  const origin = 'http://127.0.0.1:42001';
  const { window, event } = trustedWindow();
  assert.equal(isTrustedSender(event, window, origin), true);
  assert.equal(isTrustedSender({ ...event, sender: {} }, window, origin), false);
  assert.equal(isTrustedSender({ ...event, senderFrame: { url: origin } }, window, origin), false);
  assert.equal(isTrustedSender(event, { ...window, isDestroyed: () => true }, origin), false);
  assert.equal(isTrustedSender(event, window, null), false);
  for (const url of ['http://127.0.0.1:42002/', 'http://localhost:42001/', 'http://127.0.0.1:42001@evil.example/', 'http://127.0.0.1:420011/', 'data:text/html,loading', 'file:///tmp/page.html']) {
    window.webContents.mainFrame.url = url;
    assert.equal(isTrustedSender(event, window, origin), false, url);
  }
});

test('all IPC methods enforce sender and argument boundaries before touching storage', (t) => {
  const { store } = fixture(t);
  const { window, event } = trustedWindow();
  const handlers = new Map();
  registerSessionIpc({ handle: (name, handler) => handlers.set(name, handler) }, { getWindow: () => window, getOrigin: () => 'http://127.0.0.1:42001', store });
  const login = handlers.get('session:remember-login');
  login(event, 'alice');
  for (const handler of handlers.values()) assert.throws(() => handler({ ...event, sender: {} }), /禁止访问/);
  assert.throws(() => handlers.get('session:read')(event, 'extra'));
  assert.throws(() => handlers.get('session:clear')(event, 'extra'));
  assert.throws(() => login(event, 'alice', 'password'));
  assert.throws(() => login(event, { username: 'alice', password: 'test-only' }));
  assert.equal(store.read().login.username, 'alice');
  handlers.get('session:clear')(event);
  assert.equal(store.read().login, null);
});

test('sandbox preload exposes four named operations, never a generic IPC API', async () => {
  let exposed;
  const calls = [];
  const electron = { contextBridge: { exposeInMainWorld: (name, methods) => { exposed = { name, methods }; } }, ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve(null); } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.js'), 'utf8'), { require: (name) => { assert.equal(name, 'electron'); return electron; } });
  assert.equal(exposed.name, 'chaoxingSession');
  assert.deepEqual(Object.keys(exposed.methods), ['read', 'rememberLogin', 'rememberTask', 'clear']);
  await exposed.methods.read();
  await exposed.methods.rememberLogin('alice');
  await exposed.methods.rememberTask(null);
  await exposed.methods.clear();
  assert.deepEqual(calls, [['session:read'], ['session:remember-login', 'alice'], ['session:remember-task', null], ['session:clear']]);
});
