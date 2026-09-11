import { describe, expect, it, vi } from 'vitest';
import { createSessionStore, SAVED_LOGIN_KEY, SESSION_KEY } from './sessionStore';

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

describe('saved session', () => {
  it('migrates legacy credentials to a cookie account without keeping the password', async () => {
    const local = memoryStorage();
    local.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'legacy-test-secret' }));
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    expect((await store.read()).login).toEqual({ username: 'alice', use_cookies: true });
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).not.toContain('legacy-test-secret');
  });

  it('uses stable desktop persistence after the browser origin changes', async () => {
    let saved = { version: 1, login: null, activeTask: null };
    const bridge = {
      read: vi.fn(async () => saved),
      rememberLogin: vi.fn(async (username) => (saved = { ...saved, login: { username, use_cookies: true } })),
      rememberTask: vi.fn(async (task) => (saved = { ...saved, activeTask: task })),
      clear: vi.fn(async () => (saved = { version: 1, login: null, activeTask: null })),
    };
    const oldOrigin = memoryStorage();
    oldOrigin.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'old-test-secret' }));
    const first = createSessionStore({ getBridge: () => bridge, getStorage: () => oldOrigin });
    await first.read();
    await first.rememberTask({ username: 'alice', taskId: 'task-one' });
    const second = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    expect((await second.read()).activeTask.taskId).toBe('task-one');
    expect(bridge.rememberLogin).toHaveBeenCalledWith('alice');
    expect(JSON.stringify(bridge.rememberLogin.mock.calls)).not.toContain('secret');
    expect(oldOrigin.getItem(SAVED_LOGIN_KEY)).toBeNull();
    await second.clear();
    expect((await first.read()).login).toBeNull();
  });

  it('isolates tasks by account and clears both new and legacy storage on logout', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    await store.rememberLogin('bob');
    expect((await store.read()).activeTask).toBeNull();
    await expect(store.rememberTask({ username: 'alice', taskId: 'one' })).rejects.toThrow('账号不匹配');
    local.setItem(SAVED_LOGIN_KEY, '{}');
    await store.clear();
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).toBeNull();
  });

  it('preserves the login when only an expired task is cleared', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    expect(await store.rememberTask(null)).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null });
    expect(await store.clear()).toEqual({ version: 1, login: null, activeTask: null });
  });

  it('does not touch localStorage when desktop initialization or read fails', async () => {
    const getStorage = vi.fn(() => memoryStorage());
    const broken = createSessionStore({ getBridge: () => { throw new Error('initialization failed'); }, getStorage });
    await expect(broken.read()).rejects.toThrow('initialization failed');
    const failing = createSessionStore({ getBridge: () => ({ read: vi.fn().mockRejectedValue(new Error('disk failed')) }), getStorage });
    await expect(failing.read()).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
  });

  it.each(['rememberLogin', 'rememberTask', 'clear'])('propagates desktop %s failures without browser fallback', async (operation) => {
    const local = memoryStorage();
    local.setItem(SESSION_KEY, 'existing-browser-state');
    const bridge = { [operation]: vi.fn().mockRejectedValue(new Error('disk failed')) };
    const getStorage = vi.fn(() => local);
    const store = createSessionStore({ getBridge: () => bridge, getStorage });
    const argument = operation === 'rememberLogin' ? 'alice' : operation === 'rememberTask' ? { username: 'alice', taskId: 'one' } : undefined;
    await expect(store[operation](argument)).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
    expect(local.getItem(SESSION_KEY)).toBe('existing-browser-state');
  });

  it.each([
    undefined,
    { version: 2, login: null, activeTask: null },
    { version: 1, login: null },
    { version: 1, login: { username: 'alice', use_cookies: true, password: 'forbidden' }, activeTask: null },
    { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: { username: 'bob', taskId: 'one' } },
  ])('rejects malformed desktop sessions instead of silently erasing state (%#)', async (session) => {
    const store = createSessionStore({ getBridge: () => ({ read: vi.fn().mockResolvedValue(session) }), getStorage: () => memoryStorage() });
    await expect(store.read()).rejects.toThrow('格式错误');
  });

  it('serializes desktop writes and allows a later write after an earlier failure', async () => {
    let rejectFirst;
    const saved = { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null };
    const bridge = {
      rememberLogin: vi.fn(() => new Promise((_resolve, reject) => { rejectFirst = reject; })),
      rememberTask: vi.fn().mockResolvedValue(saved),
    };
    const store = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    const first = expect(store.rememberLogin('alice')).rejects.toThrow('disk failed');
    const second = store.rememberTask(null);
    await Promise.resolve();
    expect(bridge.rememberTask).not.toHaveBeenCalled();
    rejectFirst(new Error('disk failed'));
    await first;
    expect(await second).toEqual(saved);
    expect(bridge.rememberTask).toHaveBeenCalledExactlyOnceWith(null);
  });

  it.each([
    { username: null, taskId: 'task' },
    { username: ' alice ', taskId: 'task' },
    { username: 'alice', taskId: '../task' },
    { username: 'alice', taskId: 'task', password: 'forbidden' },
  ])('rejects invalid task arguments before desktop persistence (%#)', async (task) => {
    const bridge = { rememberTask: vi.fn().mockResolvedValue({ version: 1, login: null, activeTask: null }) };
    const store = createSessionStore({ getBridge: () => bridge });
    await expect(store.rememberTask(task)).rejects.toThrow('任务信息格式错误');
    expect(bridge.rememberTask).not.toHaveBeenCalled();
  });
});
