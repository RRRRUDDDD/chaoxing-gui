import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const core = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);
import { desktopBridge, getSessionBridge, isTauriDesktop } from './desktopBridge';

const empty = () => ({ version: 1, login: null, activeTask: null });

beforeEach(() => { core.isTauri.mockReset().mockReturnValue(false); core.invoke.mockReset().mockResolvedValue(empty()); });
afterEach(() => { delete window.chaoxingSession; vi.restoreAllMocks(); });

describe('desktop bridge', () => {
  it('uses the official runtime detection and preserves the Electron session bridge', () => {
    expect(isTauriDesktop()).toBe(false);
    expect(getSessionBridge()).toBeNull();
    const electron = { read: vi.fn() };
    window.chaoxingSession = electron;
    expect(getSessionBridge()).toBe(electron);
    core.isTauri.mockReturnValue(true);
    expect(getSessionBridge()).toBe(desktopBridge);
    expect(core.isTauri).toHaveBeenCalled();
  });

  it('invokes only the named commands with their exact argument envelopes', async () => {
    const request = { operation: 'taskLogs', payload: null, requestId: 42, taskId: 'task', after: 8 };
    const task = { username: 'alice', taskId: 'task' };
    await desktopBridge.apiRequest(request);
    await desktopBridge.apiCancel(42);
    await desktopBridge.backendStatus();
    await desktopBridge.read();
    await desktopBridge.rememberLogin('alice');
    await desktopBridge.rememberTask(task);
    await desktopBridge.rememberTask(null);
    expect(await desktopBridge.clear()).toEqual(empty());
    expect(core.invoke.mock.calls).toEqual([
      ['api_request', { request }], ['api_cancel', { requestId: 42 }], ['backend_status'],
      ['session_read'], ['session_remember_login', { username: 'alice' }],
      ['session_remember_task', { task }], ['session_remember_task', { task: null }], ['session_clear'],
    ]);
  });

  it('propagates initialization and command errors instead of selecting browser persistence', async () => {
    core.isTauri.mockImplementation(() => { throw new Error('initialization failed'); });
    expect(() => getSessionBridge()).toThrow('initialization failed');
    core.invoke.mockRejectedValue(new Error('disk failure'));
    await expect(desktopBridge.read()).rejects.toThrow('disk failure');
  });

  it.each([false, true])('selects the API transport using official detection (Tauri: %s)', async (tauri) => {
    core.isTauri.mockReturnValue(tauri);
    core.invoke.mockResolvedValue({ status: 200, body: { status: true } });
    vi.resetModules();
    const { default: api } = await import('../api/axios');
    expect(api.defaults.timeout).toBe(30000);
    if (tauri) {
      expect((await api.get('/config')).data).toEqual({ status: true });
      expect(core.invoke).toHaveBeenCalledWith('api_request', {
        request: { operation: 'configRead', payload: null, requestId: expect.any(Number) },
      });
    } else {
      expect(api.defaults.baseURL).toBe('/api');
      expect(api.defaults.adapter).toEqual(expect.arrayContaining(['xhr', 'http']));
      expect(core.invoke).not.toHaveBeenCalled();
    }
  });
});
