import { AxiosError, AxiosHeaders, CanceledError } from 'axios';
import { desktopBridge } from '../lib/desktopBridge';

const MAX_LOG_CURSOR = 0xffffffff;
const TASK_ID = '[a-zA-Z0-9_-]{1,128}';
let lastRequestId;

function nextRequestId() {
  if (lastRequestId === undefined) {
    // A fresh cryptographic prefix on every page load avoids reusing a previous
    // page's pending request IDs. A shared counter separates concurrent adapters.
    const seed = globalThis.crypto.getRandomValues(new Uint32Array(2));
    lastRequestId = (seed[0] & 0x1fffff) * 0x100000000 + seed[1];
  }
  lastRequestId = lastRequestId >= Number.MAX_SAFE_INTEGER ? 1 : lastRequestId + 1;
  return lastRequestId;
}

const invalidRequest = (config) => new AxiosError('请求参数格式错误', AxiosError.ERR_BAD_REQUEST, config);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));

function requestOperation(config) {
  if (config.baseURL != null && !['/api', '/api/'].includes(config.baseURL)) throw invalidRequest(config);
  if (typeof config.url !== 'string' || config.url.includes('#') || config.paramsSerializer) throw invalidRequest(config);
  if (config.responseType && !['json', 'text'].includes(config.responseType)) throw invalidRequest(config);
  const [rawPath, ...queryParts] = config.url.split('?');
  const path = rawPath.startsWith('/api/') ? rawPath.slice(4) : rawPath;
  const method = (config.method || 'get').toLowerCase();
  const routes = { 'post /login': 'login', 'post /courses': 'courses', 'get /config': 'configRead', 'post /config': 'configWrite', 'post /start': 'start' };
  let operation = routes[`${method} ${path}`];
  let taskId;
  if (!operation && method === 'get') {
    const task = path.match(new RegExp(`^/task/(${TASK_ID})(/details)?$`));
    const logs = path.match(new RegExp(`^/logs/(${TASK_ID})$`));
    if (task) { operation = task[2] ? 'taskDetails' : 'taskStatus'; taskId = task[1]; }
    else if (logs) { operation = 'taskLogs'; taskId = logs[1]; }
  }
  if (!operation || queryParts.length > 1) throw invalidRequest(config);

  const parameters = [...new URLSearchParams(queryParts[0] || '')];
  if (config.params != null) {
    if (config.params instanceof URLSearchParams) parameters.push(...config.params);
    else if (isRecord(config.params)) parameters.push(...Object.entries(config.params));
    else throw invalidRequest(config);
  }
  let after;
  if (operation === 'taskLogs') {
    if (parameters.length > 1 || parameters.some(([key]) => key !== 'after')) throw invalidRequest(config);
    const cursor = parameters.length ? parameters[0][1] : 0;
    if ((typeof cursor !== 'number' && typeof cursor !== 'string') || !/^\d+$/.test(String(cursor))) throw invalidRequest(config);
    after = Number(cursor);
    if (!Number.isSafeInteger(after) || after < 0 || after > MAX_LOG_CURSOR) throw invalidRequest(config);
  } else if (parameters.length || queryParts.length) throw invalidRequest(config);

  let payload = null;
  if (method === 'post') {
    try { payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data; }
    catch { throw invalidRequest(config); }
    if (!isRecord(payload)) throw invalidRequest(config);
  } else if (config.data != null) throw invalidRequest(config);
  return { operation, payload, ...(taskId ? { taskId } : {}), ...(after !== undefined ? { after } : {}) };
}

function timeoutError(config) {
  return new AxiosError(config.timeoutErrorMessage || '请求超时，请稍后重试',
    config.transitional?.clarifyTimeoutError ? AxiosError.ETIMEDOUT : AxiosError.ECONNABORTED, config);
}

function proxyError(error, config) {
  if (error?.kind === 'cancelled') return new CanceledError('请求已取消', config);
  if (error?.kind === 'timeout') return timeoutError(config);
  const message = error?.kind === 'backendNotReady' ? '服务尚未就绪，请稍后重新检查'
    : error?.kind === 'invalidRequest' ? '请求参数格式错误' : '无法连接学习服务，请稍后重试';
  const result = new AxiosError(message, error?.kind === 'invalidRequest' ? AxiosError.ERR_BAD_REQUEST : AxiosError.ERR_NETWORK, config);
  result.cause = error;
  return result;
}

export function createTauriAdapter({
  request = desktopBridge.apiRequest,
  cancel = desktopBridge.apiCancel,
  eventTarget = globalThis.window,
} = {}) {
  const pending = new Set();
  let closing = false;
  const onPageHide = () => {
    closing = true;
    for (const abort of [...pending]) abort();
  };

  return (config) => new Promise((resolve, reject) => {
    if (closing || config.signal?.aborted || config.cancelToken?.reason) {
      reject(new CanceledError('请求已取消', config));
      return;
    }
    let wire;
    let timeout;
    try {
      wire = { ...requestOperation(config), requestId: nextRequestId() };
      timeout = config.timeout ?? 30000;
      if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0) {
        throw new AxiosError('请求超时设置无效', AxiosError.ERR_BAD_OPTION_VALUE, config);
      }
    } catch (error) {
      reject(error);
      return;
    }

    let settled = false;
    let sent = false;
    let timer;
    const finish = (error, response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      config.signal?.removeEventListener('abort', onAbort);
      config.cancelToken?.unsubscribe(onAbort);
      pending.delete(onAbort);
      if (!pending.size) eventTarget?.removeEventListener('pagehide', onPageHide);
      if (error) reject(error);
      else resolve(response);
    };
    const stop = (error) => {
      if (settled) return;
      if (sent) {
        // Closing the page may also close IPC. Consume cancellation failures;
        // the caller must still stop waiting and ignore the late request result.
        try { Promise.resolve(cancel(wire.requestId)).catch(() => {}); } catch { /* Already closed. */ }
      }
      finish(error);
    };
    const onAbort = () => stop(new CanceledError('请求已取消', config));
    if (!pending.size) eventTarget?.addEventListener('pagehide', onPageHide);
    pending.add(onAbort);
    config.signal?.addEventListener('abort', onAbort, { once: true });
    config.cancelToken?.subscribe(onAbort);
    if (config.signal?.aborted || config.cancelToken?.reason) onAbort();
    if (settled) return;
    if (timeout > 0) timer = setTimeout(() => stop(timeoutError(config)), timeout);

    const onResponse = (value) => {
      if (settled) return;
      try {
        if (!value || !Number.isInteger(value.status) || value.status < 100 || value.status > 599 || !Object.hasOwn(value, 'body')) {
          throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        }
        // Axios has already transformed the request. Give its response pipeline
        // the same raw JSON text as HTTP, including for custom/error transforms.
        const data = JSON.stringify(value.body);
        if (data === undefined) throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        const response = { data, status: value.status, statusText: '', headers: new AxiosHeaders({ 'Content-Type': 'application/json' }), config, request: { requestId: wire.requestId } };
        if (!config.validateStatus || config.validateStatus(response.status)) finish(null, response);
        else finish(new AxiosError(`Request failed with status code ${response.status}`,
          response.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST, config, response.request, response));
      } catch (error) { finish(error); }
    };
    sent = true;
    try {
      Promise.resolve(request(wire)).then(onResponse, (error) => finish(proxyError(error, config)));
    } catch (error) { finish(proxyError(error, config)); }
  });
}
