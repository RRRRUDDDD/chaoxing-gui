import React from 'react';
import { createServer } from 'node:http';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import App from '../App';
import DesktopStartup from '../components/DesktopStartup';
import api from './axios';
import { createTauriAdapter } from './tauriAdapter';
import { sessionStore } from '../lib/sessionStore';

const core = vi.hoisted(() => ({ isTauri: vi.fn().mockReturnValue(false), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);

const empty = () => ({ version: 1, login: null, activeTask: null });
const clone = (value) => JSON.parse(JSON.stringify(value));
const ok = (data) => ({ status: 200, body: { status: true, data } });
const originalAdapter = api.defaults.adapter;
let server;
let origin;
let fixture;

function fixtureApi(method, path, payload, after = 0) {
  fixture.calls.push({ method, path, payload, after });
  if (path === '/api/login') return ok({ username: payload.username });
  if (path === '/api/courses') return ok([{ courseId: 'one', title: '模拟课程' }]);
  if (path === '/api/config') {
    if (method === 'POST') fixture.config = clone(payload);
    return ok(fixture.config);
  }
  if (path === '/api/start') return { status: 409, body: { status: false, data: { task_id: 'shared-task' } } };
  if (fixture.gone) return { status: 404, body: { status: false, msg: '任务不存在或已过期' } };
  if (path === '/api/task/shared-task') {
    return ok({ status: fixture.status, progress: 0, total: 1, stats: { completed_chapters: 0, total_chapters: 0 } });
  }
  if (path === '/api/task/shared-task/details') {
    if (fixture.status === 'completed' && ++fixture.finalDetails === 1) return { status: 503, body: { status: false, msg: '模拟详情暂不可用' } };
    return ok({ courses: [] });
  }
  if (path === '/api/logs/shared-task') {
    const initial = { seq: 1, timestamp: 1, level: 'info', message: '初始日志' };
    const final = { seq: 2, timestamp: 2, level: 'info', message: '最终日志' };
    const logs = fixture.status === 'completed' ? [initial, final, final] : [initial, initial];
    return { status: 200, body: { status: true, data: logs, next_cursor: fixture.status === 'completed' ? 2 : 1, truncated: false } };
  }
  throw new Error(`Unexpected fixture request: ${method} ${path}`);
}

function sessionCommand(command, args) {
  if (command === 'session_remember_login') {
    fixture.session = { version: 1, login: { username: args.username, use_cookies: true }, activeTask: fixture.session.login?.username === args.username ? fixture.session.activeTask : null };
  } else if (command === 'session_remember_task') {
    fixture.session.activeTask = args.task;
  } else if (command === 'session_clear') fixture.session = empty();
  return clone(fixture.session);
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
      const text = Buffer.concat(chunks).toString();
      const url = new URL(request.url, 'http://fixture.invalid');
      const result = fixtureApi(request.method, url.pathname, text ? JSON.parse(text) : null, Number(url.searchParams.get('after') || 0));
      response.writeHead(result.status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result.body));
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: false, msg: error.message }));
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

afterEach(async () => {
  cleanup();
  await sessionStore.clear();
  core.isTauri.mockReturnValue(false);
  core.invoke.mockReset();
  delete window.chaoxingSession;
  localStorage.clear();
  api.defaults.adapter = originalAdapter;
  api.defaults.baseURL = '/api';
  vi.restoreAllMocks();
});

afterAll(async () => { await new Promise((resolve) => server.close(resolve)); });

describe('shared task flow over real Axios transports', () => {
  it.each(['browser', 'electron', 'tauri'])('recovers 409, refreshes, retries final snapshots and clears 404 over %s', async (transport) => {
    fixture = { calls: [], config: { settings: {}, selectedCoursesByAccount: { alice: ['one'] } }, session: empty(), status: 'running', finalDetails: 0, gone: false };
    window.history.replaceState({}, '', '/');
    window.matchMedia = vi.fn(() => ({ matches: true }));
    core.isTauri.mockReturnValue(transport === 'tauri');
    if (transport === 'tauri') {
      core.invoke.mockImplementation(async (command, args) => {
        if (command === 'backend_status') return { phase: 'ready' };
        if (command.startsWith('session_')) return sessionCommand(command, args);
        if (command === 'api_cancel') return undefined;
        if (command !== 'api_request') throw new Error(`Unexpected command: ${command}`);
        const { operation, payload, taskId, after } = args.request;
        const routes = {
          login: ['POST', '/api/login'], courses: ['POST', '/api/courses'],
          configRead: ['GET', '/api/config'], configWrite: ['POST', '/api/config'], start: ['POST', '/api/start'],
          taskStatus: ['GET', `/api/task/${taskId}`], taskDetails: ['GET', `/api/task/${taskId}/details`], taskLogs: ['GET', `/api/logs/${taskId}`],
        };
        const [method, path] = routes[operation];
        return fixtureApi(method, path, payload, after);
      });
      api.defaults.adapter = createTauriAdapter();
      api.defaults.baseURL = '/api';
    } else {
      api.defaults.adapter = originalAdapter;
      api.defaults.baseURL = `${origin}/api`;
      if (transport === 'electron') {
        window.chaoxingSession = {
          read: async () => sessionCommand('session_read'),
          rememberLogin: async (username) => sessionCommand('session_remember_login', { username }),
          rememberTask: async (task) => sessionCommand('session_remember_task', { task }),
          clear: async () => sessionCommand('session_clear'),
        };
      }
    }

    await sessionStore.rememberLogin('alice');
    const mount = () => render(<React.StrictMode><DesktopStartup intervalMs={100}><App /></DesktopStartup></React.StrictMode>);
    let view = mount();
    const start = await screen.findByRole('button', { name: '开始学习' });
    expect(start.disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
    await screen.findByText('配置已保存');
    expect(fixture.calls.find((call) => call.method === 'POST' && call.path === '/api/config').payload.selectedCoursesByAccount).toEqual({ alice: ['one'] });
    fireEvent.click(start);
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    expect((await sessionStore.read()).activeTask).toEqual({ username: 'alice', taskId: 'shared-task' });
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);

    view.unmount();
    view = mount();
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    fixture.status = 'completed';
    await screen.findByText('最终日志', {}, { timeout: 4500 });
    await waitFor(() => expect(fixture.finalDetails).toBe(2), { timeout: 4500 });
    const logs = within(screen.getByRole('log'));
    expect(logs.getAllByText('初始日志')).toHaveLength(1);
    expect(logs.getAllByText('最终日志')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path.startsWith('/api/logs/')).map((call) => call.after)).toEqual(expect.arrayContaining([0, 1, 2]));

    view.unmount();
    fixture.gone = true;
    view = mount();
    expect((await screen.findByRole('alert')).textContent).toContain('任务不存在或已过期');
    await waitFor(async () => expect(await sessionStore.read()).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null }));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(false);
    expect(fixture.calls.filter((call) => call.path === '/api/start')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path === '/api/login').every((call) => call.payload.use_cookies === true && call.payload.password === '')).toBe(true);
    view.unmount();
  }, 15000);
});
