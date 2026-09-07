import axios from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTauriAdapter } from './tauriAdapter';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const ok = (body = { status: true }) => ({ status: 200, body });

function setup(response = ok()) {
  const request = vi.fn().mockResolvedValue(response);
  const cancel = vi.fn().mockResolvedValue(undefined);
  const eventTarget = new EventTarget();
  const adapter = createTauriAdapter({ request, cancel, eventTarget });
  const api = axios.create({ baseURL: '/api', timeout: 30000, adapter, headers: { 'Content-Type': 'application/json' } });
  return { api, adapter, request, cancel, eventTarget };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('Tauri API contract', () => {
  it.each([
    ['post', '/login', { username: 'alice', password: '', use_cookies: true }, {}, { operation: 'login' }],
    ['post', '/courses', { username: 'alice' }, {}, { operation: 'courses' }],
    ['get', '/config', undefined, {}, { operation: 'configRead' }],
    ['post', '/config', { selectedCoursesByAccount: { alice: ['one'] } }, {}, { operation: 'configWrite' }],
    ['post', '/start', { username: 'alice', course_list: ['one'] }, {}, { operation: 'start' }],
    ['get', '/task/task-1_A', undefined, {}, { operation: 'taskStatus', taskId: 'task-1_A' }],
    ['get', '/task/task-1_A/details', undefined, {}, { operation: 'taskDetails', taskId: 'task-1_A' }],
    ['get', '/logs/task-1_A', undefined, { params: { after: 17 } }, { operation: 'taskLogs', taskId: 'task-1_A', after: 17 }],
  ])('maps %s %s to one explicit operation', async (method, url, data, config, operation) => {
    const { api, request, cancel } = setup();
    const response = await api.request({ method, url, data, ...config });
    expect(response.data).toEqual({ status: true });
    expect(response.status).toBe(200);
    expect(request).toHaveBeenCalledOnce();
    const wire = request.mock.calls[0][0];
    expect(wire).toEqual({ ...operation, payload: data ?? null, requestId: expect.any(Number) });
    expect(Number.isSafeInteger(wire.requestId)).toBe(true);
    expect(wire.requestId).toBeGreaterThan(0);
    expect(cancel).not.toHaveBeenCalled();
  });

  it('keeps the log cursor including zero and accepts only its bounded query form', async () => {
    const { api, request } = setup();
    await api.get('/logs/task');
    await api.get('/logs/task?after=42');
    await api.get('/logs/task', { params: new URLSearchParams({ after: '4294967295' }) });
    expect(request.mock.calls.map(([value]) => value.after)).toEqual([0, 42, 4294967295]);
  });

  it.each([
    { url: 'https://example.com/api/config' },
    { url: '//example.com/api/config' },
    { url: '/config', baseURL: 'https://example.com/api' },
    { url: '/config', method: 'delete' },
    { url: '/unknown' },
    { url: '/task/../config' },
    { url: '/task/%2e%2e' },
    { url: '/task/a%2fb' },
    { url: '/task/a/extra' },
    { url: '/config#fragment' },
    { url: '/config?after=1' },
    { url: '/logs/task?after=1&after=2' },
    { url: '/logs/task?other=1' },
    { url: '/logs/task?after=1', params: { after: 2 } },
    { url: '/logs/task', params: { after: -1 } },
    { url: '/logs/task', params: { after: 1.5 } },
    { url: '/logs/task', params: { after: 4294967296 } },
    { url: '/logs/task', params: { after: ['1'] } },
    { url: '/logs/task', params: { operation: 'start' } },
    { url: '/config', params: { unknown: undefined } },
    { url: '/config', data: { unexpected: true } },
    { url: '/login', method: 'post', data: 'not JSON', transformRequest: [(value) => value] },
  ])('fails closed without invoking for unsupported request %#', async (config) => {
    const { api, request } = setup();
    await expect(api.request(config)).rejects.toMatchObject({ code: 'ERR_BAD_REQUEST' });
    expect(request).not.toHaveBeenCalled();
  });

  it.each([409, 404])('preserves HTTP %s status and transformed error data', async (status) => {
    const body = { status: false, msg: '任务不可用', data: { task_id: 'existing-task' } };
    const { api, request } = setup({ status, body });
    await expect(api.post('/start', { course_list: ['one'] })).rejects.toMatchObject({
      isAxiosError: true, response: { status, data: body },
    });
    expect(request).toHaveBeenCalledOnce();
  });

  it('honors validateStatus including null without retrying start', async () => {
    const { api, request } = setup({ status: 409, body: { status: false } });
    expect((await api.post('/start', {}, { validateStatus: (status) => status === 409 })).status).toBe(409);
    expect((await api.post('/start', {}, { validateStatus: null })).status).toBe(409);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([200, 409])('runs each custom transform once with raw response JSON (%s)', async (status) => {
    const body = { status: status === 200, data: { value: 1 } };
    const { api, request } = setup({ status, body });
    const transformRequest = vi.fn((value) => JSON.stringify({ ...value, transformed: true }));
    const transformResponse = vi.fn((value) => {
      expect(typeof value).toBe('string');
      return { ...JSON.parse(value), transformed: true };
    });
    const outcome = await api.post('/start', { course_list: ['one'] }, { transformRequest, transformResponse }).catch((error) => error.response);
    expect(request.mock.calls[0][0].payload).toEqual({ course_list: ['one'], transformed: true });
    expect(outcome.data).toEqual({ ...body, transformed: true });
    expect(transformRequest).toHaveBeenCalledOnce();
    expect(transformResponse).toHaveBeenCalledOnce();
  });

  it('honors text responseType without a second JSON parse', async () => {
    const { api } = setup(ok({ nested: '{"value":1}' }));
    expect((await api.get('/config', { responseType: 'text' })).data).toBe('{"nested":"{\\"value\\":1}"}');
  });

  it.each([
    ['cancelled', 'ERR_CANCELED'], ['backendNotReady', 'ERR_NETWORK'],
    ['invalidRequest', 'ERR_BAD_REQUEST'], ['network', 'ERR_NETWORK'], ['timeout', 'ECONNABORTED'],
  ])('maps host %s errors into Axios errors', async (kind, code) => {
    const { api, request } = setup();
    request.mockRejectedValue({ kind, reason: 'fixture', phase: 'failed' });
    await expect(api.post('/start', {})).rejects.toMatchObject({ code });
    expect(request).toHaveBeenCalledOnce();
  });

  it('does not reuse request IDs for parallel requests or a reloaded adapter module', async () => {
    const first = setup();
    await Promise.all(Array.from({ length: 40 }, () => first.api.get('/config')));
    vi.resetModules();
    const reloaded = await import('./tauriAdapter');
    const secondRequest = vi.fn().mockResolvedValue(ok());
    const second = axios.create({ adapter: reloaded.createTauriAdapter({ request: secondRequest, cancel: vi.fn(), eventTarget: new EventTarget() }) });
    await Promise.all(Array.from({ length: 40 }, () => second.get('/config')));
    const ids = [...first.request.mock.calls, ...secondRequest.mock.calls].map(([request]) => request.requestId);
    expect(new Set(ids).size).toBe(80);
    expect(ids.every((id) => Number.isSafeInteger(id) && id > 0)).toBe(true);
  });
});

describe('Tauri request lifetime', () => {
  it('rejects an early abort before sending an operation', async () => {
    const { api, adapter, request, cancel } = setup();
    const controller = new AbortController();
    controller.abort();
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    await expect(adapter({ method: 'post', url: '/start', data: '{}', signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(request).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('cancels in flight, consumes a late %s and never repeats start', async (completion) => {
    const { api, request, cancel } = setup();
    const pending = deferred();
    request.mockReturnValue(pending.promise);
    cancel.mockRejectedValue(new Error('window already closing'));
    const controller = new AbortController();
    const outcome = api.post('/start', {}, { signal: controller.signal });
    const rejected = expect(outcome).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    controller.abort();
    await rejected;
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    pending[completion](completion === 'resolve' ? ok() : { kind: 'network' });
    await Promise.resolve();
    await Promise.resolve();
    expect(request).toHaveBeenCalledOnce();
  });

  it('sends cancellation when abort arrives while invoke is registering', async () => {
    const { api, request, cancel } = setup();
    const controller = new AbortController();
    request.mockImplementation(() => { controller.abort(); return Promise.resolve(ok()); });
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    expect(request).toHaveBeenCalledOnce();
  });

  it('applies the default 30 second timeout and honors an explicit timeout', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    request.mockReturnValue(new Promise(() => {}));
    const timeout = expect(api.post('/start', {})).rejects.toMatchObject({ code: 'ECONNABORTED' });
    await vi.advanceTimersByTimeAsync(29999);
    expect(cancel).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await timeout;
    const short = expect(api.get('/config', { timeout: 25, transitional: { clarifyTimeoutError: true } })).rejects.toMatchObject({ code: 'ETIMEDOUT' });
    await vi.advanceTimersByTimeAsync(25);
    await short;
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('allows timeout zero and ignores abort after a completed response', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    const pending = deferred();
    const controller = new AbortController();
    request.mockReturnValue(pending.promise);
    const result = api.get('/config', { timeout: 0, signal: controller.signal });
    await vi.advanceTimersByTimeAsync(120000);
    expect(cancel).not.toHaveBeenCalled();
    pending.resolve(ok());
    await result;
    controller.abort();
    expect(cancel).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels every pending request on pagehide and removes lifetime listeners', async () => {
    vi.useFakeTimers();
    const { api, request, cancel, eventTarget } = setup();
    const remove = vi.spyOn(eventTarget, 'removeEventListener');
    request.mockReturnValue(new Promise(() => {}));
    const results = Promise.allSettled([api.get('/config'), api.post('/start', {})]);
    eventTarget.dispatchEvent(new Event('pagehide'));
    expect((await results).map((result) => result.reason.code)).toEqual(['ERR_CANCELED', 'ERR_CANCELED']);
    expect(cancel.mock.calls.map(([id]) => id).sort()).toEqual(request.mock.calls.map(([value]) => value.requestId).sort());
    expect(remove).toHaveBeenCalledWith('pagehide', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });
});
