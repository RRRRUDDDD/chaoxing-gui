import { createServer } from 'node:http';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

const core = vi.hoisted(() => ({ isTauri: vi.fn().mockReturnValue(false), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);

const credentials = { username: 'alice', password: '', use_cookies: true };
const recipes = [
  { name: 'visits', task_type: 'visits', tool_options: { count: 10, interval: 30 } },
  { name: 'video catalog', task_type: 'catalog', tool_options: { purpose: 'video_time' } },
  { name: 'reading catalog', task_type: 'catalog', tool_options: { purpose: 'reading_time' } },
  { name: 'download catalog', task_type: 'catalog', tool_options: { purpose: 'download' } },
  { name: 'video time', task_type: 'video_time', tool_options: { source_task_id: 'catalog-1', resource_ids: ['video-1'], minutes: 0.5 } },
  { name: 'reading time', task_type: 'reading_time', tool_options: { source_task_id: 'reading-catalog', resource_ids: ['reading-1'], minutes: 0.1 } },
  { name: 'download', task_type: 'download', tool_options: { source_task_id: 'catalog-2', resource_ids: ['file-1', 'video-1'] } },
];
let server;
let origin;
let fixture;

function fixtureResponse(method, path, payload, query = '') {
  fixture.calls.push({ method, path, payload, query });
  return fixture.response;
}

beforeAll(async () => {
  server = createServer(async (request, response) => {
    response.setHeader('Access-Control-Allow-Origin', '*');
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    response.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return; }
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString();
      const url = new URL(request.url, 'http://fixture.invalid');
      fixture.httpCalls += 1;
      const result = fixtureResponse(request.method, url.pathname, body ? JSON.parse(body) : null, url.search);
      response.writeHead(result.status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result.body));
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: false, msg: error.message }));
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  origin = `http://127.0.0.1:${server.address().port}`;
});

afterEach(() => {
  core.isTauri.mockReturnValue(false);
  core.invoke.mockReset();
  delete window.chaoxingSession;
  vi.restoreAllMocks();
});

afterAll(async () => { await new Promise((resolve) => server.close(resolve)); });

async function configureTransport(transport) {
  fixture = { calls: [], httpCalls: 0, response: { status: 200, body: { status: true, data: { task_id: 'tool-task' } } } };
  core.isTauri.mockReturnValue(transport === 'tauri');
  core.invoke.mockImplementation(async (command, args) => {
    if (command !== 'api_request') throw new Error(`Unexpected command: ${command}`);
    const { operation, payload, taskId } = args.request;
    const routes = {
      start: ['POST', '/api/start'],
      taskStatus: ['GET', `/api/task/${taskId}`],
      taskDetails: ['GET', `/api/task/${taskId}/details`],
      taskStop: ['POST', `/api/task/${taskId}/stop`],
      taskResume: ['POST', `/api/task/${taskId}/resume`],
      taskOpenDownloads: ['POST', `/api/task/${taskId}/open-downloads`],
    };
    if (!routes[operation]) throw new Error(`Unexpected operation: ${operation}`);
    return fixtureResponse(...routes[operation], payload ?? null);
  });
  if (transport === 'electron') {
    window.chaoxingSession = { read: vi.fn(), rememberLogin: vi.fn(), rememberTask: vi.fn(), clear: vi.fn() };
  }
  // Let the real entry point choose its adapter at startup for each runtime.
  vi.resetModules();
  const { default: api } = await import('./axios');
  if (transport !== 'tauri') api.defaults.baseURL = `${origin}/api`;
  return api;
}

function expectTransport(transport, operation, payload, taskId) {
  if (transport === 'tauri') {
    expect(fixture.httpCalls).toBe(0);
    expect(core.invoke).toHaveBeenCalledExactlyOnceWith('api_request', {
      request: { operation, payload, ...(taskId ? { taskId } : {}), requestId: expect.any(Number) },
    });
  } else {
    expect(fixture.httpCalls).toBe(1);
    expect(core.invoke).not.toHaveBeenCalled();
    if (transport === 'electron') {
      for (const method of Object.values(window.chaoxingSession)) expect(method).not.toHaveBeenCalled();
    }
  }
}

describe.each(['browser', 'electron', 'tauri'])('course tools over the %s Axios transport', (transport) => {
  it.each(recipes)('passes the $name recipe through the existing start operation', async ({ task_type, tool_options }) => {
    const api = await configureTransport(transport);
    const payload = { ...credentials, course_list: ['course-1'], task_type, tool_options };
    const response = await api.post('/start', payload);
    expect(response.status).toBe(200);
    expect(response.data).toEqual(fixture.response.body);
    expect(fixture.calls).toEqual([{ method: 'POST', path: '/api/start', payload, query: '' }]);
    expectTransport(transport, 'start', payload);
  });

  it('preserves a tool start conflict and its recovery task without retrying', async () => {
    const api = await configureTransport(transport);
    const payload = { ...credentials, course_list: ['course-1'], task_type: 'visits', tool_options: { count: 10, interval: 30 } };
    const body = { status: false, msg: '当前账号已有运行任务', data: { task_id: 'existing-tool' } };
    fixture.response = { status: 409, body };
    await expect(api.post('/start', payload)).rejects.toMatchObject({
      isAxiosError: true, response: { status: 409, data: body },
    });
    expect(fixture.calls).toEqual([{ method: 'POST', path: '/api/start', payload, query: '' }]);
    expectTransport(transport, 'start', payload);
  });

  it('carries reading progress, cancellation and retained results through the existing task operations', async () => {
    const api = await configureTransport(transport);
    const payload = { ...credentials, course_list: ['course-1'], task_type: 'reading_time', tool_options: { source_task_id: 'reading-catalog', resource_ids: ['reading-1'], minutes: 0.1 } };
    await api.post('/start', payload);
    const running = { task_type: 'reading_time', task_label: '阅读时长', status: 'running', progress: 0, total: 1 };
    fixture.response = { status: 200, body: { status: true, data: running } };
    expect((await api.get('/task/tool-task')).data.data).toEqual(running);
    const details = { courses: [], active_jobs: {}, tool: { purpose: 'reading_time', course_ids: ['course-1'], unit: '秒', completed_units: 5, total_units: 6, current: null, resources: [{ id: 'reading-1', readable: true, required_minutes: 60, read_minutes: 12.25, book_count: 3 }], results: [{ id: 'reading-1', status: 'skipped', seconds: 5, before: 12.25, after: null }] } };
    fixture.response = { status: 200, body: { status: true, data: details } };
    expect((await api.get('/task/tool-task/details')).data.data).toEqual(details);
    fixture.response = { status: 200, body: { status: true, data: { state: 'stopping' } } };
    expect((await api.post('/task/tool-task/stop', { username: 'alice' })).data.data.state).toBe('stopping');
    fixture.response = { status: 200, body: { status: true, data: { ...running, status: 'cancelled', cancel_requested: true } } };
    expect((await api.get('/task/tool-task')).data.data.status).toBe('cancelled');
    fixture.response = { status: 200, body: { status: true, data: { task_id: 'tool-task', status: 'cancelled' } } };
    expect((await api.post('/task/tool-task/resume', credentials)).data.data.status).toBe('cancelled');
    expect(fixture.calls.map(({ method, path }) => [method, path])).toEqual([
      ['POST', '/api/start'], ['GET', '/api/task/tool-task'], ['GET', '/api/task/tool-task/details'],
      ['POST', '/api/task/tool-task/stop'], ['GET', '/api/task/tool-task'], ['POST', '/api/task/tool-task/resume'],
    ]);
    expect(fixture.calls[3].payload).toEqual({ username: 'alice' });
    if (transport === 'tauri') {
      expect(fixture.httpCalls).toBe(0);
      expect(core.invoke.mock.calls.map(([, args]) => args.request.operation)).toEqual(['start', 'taskStatus', 'taskDetails', 'taskStop', 'taskStatus', 'taskResume']);
    } else {
      expect(fixture.httpCalls).toBe(6);
      expect(core.invoke).not.toHaveBeenCalled();
    }
  });

  it.each([401, 503])('preserves a reading start HTTP %s error without submitting it again', async (status) => {
    const api = await configureTransport(transport);
    const payload = { ...credentials, course_list: ['course-1'], task_type: 'reading_time', tool_options: { source_task_id: 'reading-catalog', resource_ids: ['reading-1'], minutes: 30 } };
    const body = { status: false, msg: status === 401 ? '登录已失效' : '阅读服务暂不可用' };
    fixture.response = { status, body };
    await expect(api.post('/start', payload)).rejects.toMatchObject({ response: { status, data: body } });
    expect(fixture.calls).toEqual([{ method: 'POST', path: '/api/start', payload, query: '' }]);
    expectTransport(transport, 'start', payload);
  });

  it('opens the task download folder using its task ID and account', async () => {
    const api = await configureTransport(transport);
    const payload = { username: credentials.username };
    fixture.response = { status: 200, body: { status: true, msg: '下载目录已打开' } };
    const response = await api.post('/task/download-1_A/open-downloads', payload);
    expect(response.data).toEqual(fixture.response.body);
    expect(fixture.calls).toEqual([{ method: 'POST', path: '/api/task/download-1_A/open-downloads', payload, query: '' }]);
    expectTransport(transport, 'taskOpenDownloads', payload, 'download-1_A');
  });

  it.each([403, 404, 409])('preserves an open-downloads HTTP %s error without retrying', async (status) => {
    const api = await configureTransport(transport);
    const payload = { username: credentials.username };
    const body = { status: false, msg: '下载目录暂不可用' };
    fixture.response = { status, body };
    await expect(api.post('/task/download-1_A/open-downloads', payload)).rejects.toMatchObject({
      isAxiosError: true, response: { status, data: body },
    });
    expect(fixture.calls).toEqual([{ method: 'POST', path: '/api/task/download-1_A/open-downloads', payload, query: '' }]);
    expectTransport(transport, 'taskOpenDownloads', payload, 'download-1_A');
  });
});
