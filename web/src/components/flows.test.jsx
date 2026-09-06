import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';
import Login from './Login';
import api from '../api/axios';
import { SAVED_LOGIN_KEY, SESSION_KEY, sessionStore } from '../lib/sessionStore';

vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const ok = (data) => ({ data: { status: true, data } });
const page = (data = [], next_cursor = 0) => ({ data: { status: true, data, next_cursor, truncated: false } });
const account = { username: 'alice', password: '', use_cookies: true };
const courses = [{ courseId: '1', title: 'Course one' }, { courseId: '2', title: 'Course two' }];
const taskState = (status = 'running') => ({ status, progress: 0, total: 2, stats: { completed_chapters: 0, total_chapters: 0 } });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function services({ config = {}, status = 'running', conflict = false } = {}) {
  api.get.mockImplementation(async (url) => {
    if (url === '/config') return ok(config);
    if (url.endsWith('/details')) return ok({ courses: [] });
    if (url.startsWith('/logs/')) return page();
    if (url.startsWith('/task/')) return ok(taskState(status));
    throw new Error(`Unexpected GET ${url}`);
  });
  api.post.mockImplementation(async (url, body) => {
    if (url === '/login') return ok({ username: body.username });
    if (url === '/courses') return ok(courses);
    if (url === '/config') return ok({});
    if (url === '/start') {
      if (conflict) throw { response: { status: 409, data: { status: false, data: { task_id: 'existing-task' } } } };
      return ok({ task_id: 'task-one' });
    }
    throw new Error(`Unexpected POST ${url}`);
  });
}

async function loginManually(username) {
  await screen.findByRole('button', { name: '登录' });
  fireEvent.change(screen.getByLabelText('手机号'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await screen.findByText(username);
}

beforeEach(async () => {
  delete window.chaoxingSession;
  await sessionStore.clear();
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  window.matchMedia = vi.fn(() => ({ matches: true }));
  api.get.mockReset();
  api.post.mockReset();
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('course selection', () => {
  it('shows API failures and disables starting instead of submitting an empty list', async () => {
    services();
    api.post.mockResolvedValue({ data: { status: false, msg: '课程服务暂不可用' } });
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    expect((await screen.findByRole('alert')).textContent).toContain('课程服务暂不可用');
    const button = screen.getByRole('button', { name: '开始学习' });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '重新加载' })).toBeTruthy();
  });

  it('blocks starting if saved configuration could not be loaded', async () => {
    services();
    api.get.mockRejectedValue(new Error('offline'));
    render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: /Course one/ }).getAttribute('aria-pressed')).toBe('false');
  });

  it('keeps a stale saved selection empty and saves only this account after explicit selection', async () => {
    services({ config: { selectedCourses: ['1'], selectedCoursesByAccount: { alice: ['gone'], bob: ['2'] } } });
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    const first = await screen.findByRole('button', { name: /Course one/ });
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.click(first);
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(start.mock.calls[0][0].course_list).toEqual(['1']);
    fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
    await screen.findByText('配置已保存');
    const payload = api.post.mock.calls.find(([url]) => url === '/config')[1];
    expect(payload.selectedCoursesByAccount).toEqual({ alice: ['1'] });
    expect(payload.selectedCourses).toBeUndefined();
    fireEvent.click(first);
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  });

  it('cancels a previous account load and never lets its late courses replace the new account', async () => {
    services({ config: { selectedCoursesByAccount: { bob: ['b'] } } });
    const old = deferred();
    api.post.mockImplementation((_url, body) => body.username === 'alice' ? old.promise : Promise.resolve(ok([{ courseId: 'b', title: 'Bob course' }])));
    const start = vi.fn();
    const view = render(<CourseSelection userInfo={account} onStartStudy={start} />);
    const previousSignal = api.post.mock.calls[0][2].signal;
    view.rerender(<CourseSelection userInfo={{ ...account, username: 'bob' }} onStartStudy={start} />);
    await screen.findByRole('button', { name: /Bob course/ });
    expect(previousSignal.aborted).toBe(true);
    await act(async () => old.resolve(ok(courses)));
    expect(screen.queryByText('Course one')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(start.mock.calls[0][0].course_list).toEqual(['b']);
  });

  it('does not enable starting when the course list is empty', async () => {
    services();
    api.post.mockResolvedValue(ok([]));
    render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
    await screen.findByText('暂无课程');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  });
});

describe('task navigation', () => {
  it('keeps an active task while navigating, blocks duplicate starts, restores after refresh and clears on logout', async () => {
    services();
    await sessionStore.rememberLogin('alice');
    const view = render(<React.StrictMode><App /></React.StrictMode>);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('task-one');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask?.taskId).toBe('task-one'));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    const returnButton = await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.click(returnButton);
    await screen.findByText('task-one');
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(1);
    expect(api.post.mock.calls.filter(([url]) => url === '/login')).toHaveLength(1);
    view.unmount();
    render(<App />);
    await screen.findByText('task-one');
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await screen.findByLabelText('密码');
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('adopts the running task returned by a 409 response', async () => {
    services({ conflict: true });
    await sessionStore.rememberLogin('alice');
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('existing-task');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask.taskId).toBe('existing-task'));
  });

  it.each(['idle', 'pending success', 'pending conflict'])('locks starting during logout from an %s session and ignores late responses after account changes', async (scenario) => {
    services();
    await sessionStore.rememberLogin('alice');
    const clearing = deferred();
    const clearSession = sessionStore.clear;
    vi.spyOn(sessionStore, 'clear').mockImplementationOnce(async () => {
      await clearing.promise;
      await clearSession();
    });
    const aliceStart = deferred();
    const bobStart = deferred();
    const normalPost = api.post.getMockImplementation();
    api.post.mockImplementation((url, body, options) => url === '/start'
      ? body.username === 'alice' ? aliceStart.promise : bobStart.promise
      : normalPost(url, body, options));
    render(<App />);
    const startButton = await screen.findByRole('button', { name: '开始学习' });
    const pending = scenario !== 'idle';
    if (pending) fireEvent.click(startButton);
    const oldSignal = api.post.mock.calls.find(([url]) => url === '/start')?.[2].signal;
    const logoutButton = screen.getByRole('button', { name: '退出登录' });
    fireEvent.click(logoutButton);
    expect(startButton.disabled).toBe(true);
    expect(logoutButton.disabled).toBe(true);
    if (pending) expect(oldSignal.aborted).toBe(true);
    fireEvent.click(startButton);
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(pending ? 1 : 0);
    expect(screen.queryByLabelText('密码')).toBeNull();

    await act(async () => clearing.resolve());
    await loginManually('bob');
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    await act(async () => {
      if (scenario === 'pending conflict') {
        aliceStart.reject({ response: { status: 409, data: { data: { task_id: 'alice-task' } } } });
      } else aliceStart.resolve(ok({ task_id: 'alice-task' }));
    });
    const bobStarting = screen.getByRole('button', { name: '任务启动中' });
    expect(bobStarting.disabled).toBe(true);
    fireEvent.click(bobStarting);
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(pending ? 2 : 1);
    expect(screen.queryByText('alice-task')).toBeNull();
    await act(async () => bobStart.resolve(ok({ task_id: 'bob-task' })));
    await screen.findByText('bob-task');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toEqual({ username: 'bob', taskId: 'bob-task' }));
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)).login.username).toBe('bob');
  });

  it('preserves the logged-in account and allows retrying when clearing the session fails', async () => {
    services();
    await sessionStore.rememberLogin('alice');
    const clearing = deferred();
    vi.spyOn(sessionStore, 'clear').mockReturnValueOnce(clearing.promise);
    const oldStart = deferred();
    const normalPost = api.post.getMockImplementation();
    let starts = 0;
    api.post.mockImplementation((url, body, options) => url === '/start' && starts++ === 0
      ? oldStart.promise : normalPost(url, body, options));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
    await act(async () => clearing.reject(new Error('clear failed')));
    expect((await screen.findByRole('alert')).textContent).toContain('清除保存的账号失败');
    expect(screen.getByText('alice')).toBeTruthy();
    expect(screen.queryByLabelText('密码')).toBeNull();
    expect(screen.getByRole('button', { name: '退出登录' }).disabled).toBe(false);
    const retryButton = screen.getByRole('button', { name: '开始学习' });
    expect(retryButton.disabled).toBe(false);
    fireEvent.click(retryButton);
    await screen.findByText('task-one');
    await act(async () => oldStart.resolve(ok({ task_id: 'old-task' })));
    expect(screen.queryByText('old-task')).toBeNull();
    expect(screen.getByText('task-one')).toBeTruthy();
    expect(api.post.mock.calls.filter(([url]) => url === '/login')).toHaveLength(1);
  });

  it.each([false, true])('shows the recovery storage failure on the actual progress page (conflict: %s)', async (conflict) => {
    services({ conflict });
    await sessionStore.rememberLogin('alice');
    vi.spyOn(sessionStore, 'rememberTask').mockRejectedValueOnce(new Error('storage unavailable'));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText(conflict ? 'existing-task' : 'task-one');
    expect(screen.getByText('学习进度监控')).toBeTruthy();
    const notice = await screen.findByRole('alert');
    expect(notice.textContent).toContain('恢复信息未能保存');
    expect(notice.textContent).toMatch(/请记下.*任务 ID/);
  });

  it.each(['alice', 'bob'])('does not attach a late recovery failure to a new task after logging in as %s', async (username) => {
    services();
    await sessionStore.rememberLogin('alice');
    const oldSave = deferred();
    vi.spyOn(sessionStore, 'rememberTask').mockReturnValueOnce(oldSave.promise);
    const normalPost = api.post.getMockImplementation();
    let starts = 0;
    api.post.mockImplementation((url, body, options) => url === '/start'
      ? Promise.resolve(ok({ task_id: starts++ === 0 ? 'old-task' : 'new-task' }))
      : normalPost(url, body, options));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('old-task');
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await loginManually(username);
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    await screen.findByText('new-task');
    await act(async () => oldSave.reject(new Error('old storage failure')));
    expect(screen.getByText('new-task')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toEqual({ username, taskId: 'new-task' });
  });

  it('clears expired task persistence and permits a new task after returning', async () => {
    services();
    const normalGet = api.get.getMockImplementation();
    api.get.mockImplementation((url, options) => url.startsWith('/task/') ? Promise.reject({ response: { status: 404 } }) : normalGet(url, options));
    await sessionStore.rememberLogin('alice');
    await sessionStore.rememberTask({ username: 'alice', taskId: 'expired-task' });
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('任务不存在或已过期');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(false);
  });

  it.each(['completed', 'partial', 'error'])('allows a new task only after the tracked task becomes %s', async (status) => {
    services({ status });
    await sessionStore.rememberLogin('alice');
    await sessionStore.rememberTask({ username: 'alice', taskId: 'task-one' });
    render(<App />);
    await screen.findByText('task-one');
    await waitFor(() => expect(screen.queryByText('加载中')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '查看上次任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(false);
  });
});

describe('progress details', () => {
  it('shows failed, skipped and empty chapters distinctly and renders at most 500 log entries', async () => {
    const chapters = [
      { id: 'failed', title: 'Failed chapter', status: 'error', has_finished: true },
      { id: 'skipped', title: 'Skipped chapter', status: 'skipped', has_finished: false },
      { id: 'empty', title: 'Empty chapter', status: 'empty', has_finished: true },
    ];
    const logs = Array.from({ length: 510 }, (_, index) => ({ seq: index + 1, timestamp: 1, level: 'info', message: `line-${index}` }));
    api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({ courses: [{ id: 'c', title: 'Result course', status: 'partial', chapters }] }) : url.startsWith('/logs/') ? page(logs, 510) : ok(taskState('partial')));
    render(<StudyProgress taskId="one" />);
    await screen.findByText('line-509');
    const log = screen.getByRole('log');
    expect(within(log).getAllByText(/^line-/)).toHaveLength(500);
    expect(within(log).queryByText('line-0')).toBeNull();
    expect(screen.queryByText('所有任务已完成')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Result course/ }));
    const row = (title) => screen.getByText(title).closest('li');
    expect(within(row('Failed chapter')).getByText('失败')).toBeTruthy();
    expect(within(row('Skipped chapter')).getByText('已跳过')).toBeTruthy();
    expect(within(row('Empty chapter')).getByText('无任务')).toBeTruthy();
    expect(screen.getByText('1 / 3 章节')).toBeTruthy();
  });

  it('cancels old task requests on task change, resets the cursor and ignores late old logs', async () => {
    const oldDetails = deferred();
    const oldLogs = deferred();
    api.get.mockImplementation(async (url) => {
      if (url === '/task/old/details') return oldDetails.promise;
      if (url === '/logs/old') return oldLogs.promise;
      if (url.endsWith('/details')) return ok({ courses: [] });
      if (url.startsWith('/logs/')) return page([{ seq: 1, message: 'new-log', timestamp: 1 }], 1);
      return ok(taskState(url === '/task/old' ? 'running' : 'completed'));
    });
    const view = render(<StudyProgress taskId="old" />);
    await waitFor(() => expect(api.get.mock.calls.some(([url]) => url === '/logs/old')).toBe(true));
    const oldSignal = api.get.mock.calls.find(([url]) => url === '/logs/old')[1].signal;
    view.rerender(<StudyProgress taskId="new" />);
    await screen.findByText('new-log');
    expect(oldSignal.aborted).toBe(true);
    expect(api.get.mock.calls.find(([url]) => url === '/logs/new')[1].params.after).toBe(0);
    await act(async () => { oldDetails.resolve(ok({ courses: [] })); oldLogs.resolve(page([{ seq: 99, message: 'old-log' }], 99)); });
    expect(screen.queryByText('old-log')).toBeNull();
    view.unmount();
    expect(api.get.mock.calls.find(([url]) => url === '/logs/new')[1].signal.aborted).toBe(true);
  });
});

it('migrates legacy login to cookie-only authentication and falls back to manual login when expired', async () => {
  localStorage.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'legacy-test-password' }));
  api.post.mockImplementation(async (_url, body) => {
    if (body.use_cookies) throw { response: { status: 401 } };
    return ok({ username: 'alice' });
  });
  const success = vi.fn();
  render(<React.StrictMode><Login onLoginSuccess={success} /></React.StrictMode>);
  await screen.findByText('保存的登录会话不可用，请输入密码重新登录');
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][1]).toEqual({ username: 'alice', password: '', use_cookies: true });
  expect(localStorage.getItem(SAVED_LOGIN_KEY)).toBeNull();
  expect(screen.getByLabelText('密码').value).toBe('');
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'new-test-password' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await waitFor(() => expect(success).toHaveBeenCalledOnce());
  expect(success.mock.calls[0][0]).toEqual(account);
  expect(localStorage.getItem(SESSION_KEY)).not.toContain('password');
});

it.each(['courses', 'progress'])('keeps the %s preview usable without backend requests', async (preview) => {
  window.history.replaceState({}, '', `/?preview=${preview}`);
  render(<App />);
  if (preview === 'courses') fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
  await screen.findByText('preview-task');
  fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
  await screen.findByRole('button', { name: '开始学习' });
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
});
