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
      clear: vi.fn(async () => { saved = { version: 1, login: null, activeTask: null }; }),
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
});
