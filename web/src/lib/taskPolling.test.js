import { afterEach, describe, expect, it, vi } from 'vitest';
import { appendLogPage, startTaskPolling } from './taskPolling';

const ok = (data) => ({ data: { status: true, data } });
const logPage = (data = [], cursor = 0) => ({ data: { status: true, data, next_cursor: cursor, truncated: false } });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

afterEach(() => vi.useRealTimers());

describe('task polling', () => {
  it('waits for every request before scheduling another round', async () => {
    vi.useFakeTimers();
    const status = deferred();
    const details = deferred();
    const api = { get: vi.fn((url) => url.endsWith('/details') ? details.promise : url.startsWith('/logs') ? Promise.resolve(logPage()) : status.promise) };
    const stop = startTaskPolling({ api, taskId: 'one' });
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(1);
    status.resolve(ok({ status: 'running' }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(3);
    details.resolve(ok({ courses: [] }));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.get).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.get).toHaveBeenCalledTimes(6);
    stop();
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it('aborts pending requests and ignores a late result after disposal', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const onStatus = vi.fn();
    const api = { get: vi.fn(() => pending.promise) };
    const stop = startTaskPolling({ api, taskId: 'old', onStatus });
    stop();
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
    pending.resolve(ok({ status: 'completed' }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(onStatus).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['completed', 'error', 'partial'])('pulls final details and logs after observing %s, then stops', async (status) => {
    vi.useFakeTimers();
    const pending = deferred();
    const onDetails = vi.fn();
    const onLogs = vi.fn();
    const api = { get: vi.fn((url) => url.endsWith('/details') ? Promise.resolve(ok({ courses: [{ status }] })) : url.startsWith('/logs') ? Promise.resolve(logPage([{ seq: 7, message: 'final' }], 7)) : pending.promise) };
    startTaskPolling({ api, taskId: 'one', onDetails, onLogs });
    expect(api.get).toHaveBeenCalledTimes(1);
    pending.resolve(ok({ status }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(3);
    expect(onDetails).toHaveBeenCalledWith({ courses: [{ status }] });
    expect(onLogs.mock.calls[0][0].logs[0].message).toBe('final');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries failed final detail pulls and advances the log cursor without duplicates', async () => {
    vi.useFakeTimers();
    let detailsCalls = 0;
    const onLogs = vi.fn();
    const api = { get: vi.fn((url) => {
      if (url.endsWith('/details')) return ++detailsCalls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(ok({ courses: [] }));
      if (url.startsWith('/logs')) return Promise.resolve(logPage([{ seq: 3, message: 'last' }], 3));
      return Promise.resolve(ok({ status: 'completed' }));
    }) };
    startTaskPolling({ api, taskId: 'one', onLogs });
    await vi.advanceTimersByTimeAsync(2000);
    expect(detailsCalls).toBe(2);
    const logCalls = api.get.mock.calls.filter(([url]) => url.startsWith('/logs'));
    expect(logCalls.map(([, options]) => options.params.after)).toEqual([0, 3]);
    expect(onLogs.mock.calls.at(-1)[0].logs).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops and reports an expired task on 404', async () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    const api = { get: vi.fn().mockRejectedValue({ response: { status: 404 } }) };
    startTaskPolling({ api, taskId: 'gone', onMissing });
    await vi.advanceTimersByTimeAsync(10000);
    expect(onMissing).toHaveBeenCalledOnce();
    expect(api.get).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});

it('bounds logs, deduplicates sequence numbers and never moves the cursor backward', () => {
  const data = Array.from({ length: 510 }, (_, index) => ({ seq: index + 1, message: String(index) }));
  const first = appendLogPage({ logs: [], cursor: 0, truncated: false }, { data, next_cursor: 510 });
  expect(first.logs).toHaveLength(500);
  expect(first.logs[0].seq).toBe(11);
  expect(first.truncated).toBe(true);
  const next = appendLogPage(first, { data: [{ seq: 510 }, { seq: 512 }, { seq: 511 }, { seq: 512 }], next_cursor: 1 });
  expect(next.logs.slice(-3).map((log) => log.seq)).toEqual([510, 511, 512]);
  expect(next.cursor).toBe(512);
  expect(next.logs).toHaveLength(500);
  expect(appendLogPage(next, { data: [], next_cursor: 520, truncated: true }).cursor).toBe(520);
});
