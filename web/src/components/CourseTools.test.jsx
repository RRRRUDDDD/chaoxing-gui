import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import api from '../api/axios';
import { sessionStore } from '../lib/sessionStore';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';
import CourseToolProgress from './CourseToolProgress';

vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const account = { username: 'alice', password: '', use_cookies: true };
const courses = [{ courseId: 'one', title: '模拟课程一' }, { courseId: 'two', title: '模拟课程二' }];
const resources = [
  { id: 'video-one', course_id: 'one', course_title: '模拟课程一', chapter_id: 'chapter-one', chapter_title: '第一章', name: '课程视频', kind: 'video', watchable: true, downloadable: true, duration: 20 },
  { id: 'document-one', course_id: 'one', course_title: '模拟课程一', chapter_id: 'chapter-one', chapter_title: '第一章', name: '课程讲义', kind: 'document', watchable: false, downloadable: true },
  { id: 'video-two', course_id: 'two', course_title: '模拟课程二', chapter_id: 'chapter-two', chapter_title: '第二章', name: '补充视频', kind: 'video', watchable: true, downloadable: true, duration: 60 },
  { id: 'locked', course_id: 'two', course_title: '模拟课程二', chapter_id: 'chapter-two', chapter_title: '第二章', name: '不可用视频', kind: 'video', watchable: false, downloadable: false },
];
const ok = (data) => ({ data: { status: true, data } });
const logPage = () => ({ data: { status: true, data: [], next_cursor: 0, truncated: false } });
const catalogState = (status = 'completed') => ({ status, task_type: 'catalog', task_label: '资源读取', progress: 2, total: 2 });
const catalogTool = (purpose = 'download') => ({ purpose, course_ids: ['one', 'two'], resources, results: [], completed_units: 2, total_units: 2, unit: '章节', current: null });
const downloadTool = () => ({
  purpose: 'download', course_ids: ['one'], resources: [], completed_units: 1024, total_units: 2048, unit: '字节',
  current: { name: '下载课程讲义', completed: 1024, total: null, unit: '字节' },
  output_dir: 'E:\\fixture-downloads\\download-task',
  results: [{ id: 'document-one', name: '课程讲义', course_title: '模拟课程一', status: 'completed', message: '保存完成', bytes: 1024, path: 'E:\\fixture-downloads\\download-task\\讲义.pdf' }],
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function selectionServices(config = {}) {
  api.get.mockImplementation(async (url) => {
    if (url === '/config') return ok(config);
    throw new Error(`Unexpected GET ${url}`);
  });
  api.post.mockImplementation(async (url, body) => {
    if (url === '/courses') return ok(courses);
    if (url === '/login') return ok({ username: body.username });
    if (url === '/config') return ok({});
    throw new Error(`Unexpected POST ${url}`);
  });
}

function progressServices(status, tool) {
  api.get.mockImplementation(async (url) => {
    if (url.endsWith('/details')) return ok({ courses: [], active_jobs: {}, tool });
    if (url.startsWith('/logs/')) return logPage();
    return ok(status);
  });
}

async function manualLogin(username) {
  fireEvent.change(await screen.findByLabelText('手机号'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'mock-password' } });
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
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('course tool configuration', () => {
  it('defaults to automatic study, requires an explicit course choice and sends numeric visits options', async () => {
    selectionServices();
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    await screen.findByRole('button', { name: /模拟课程一/ });
    expect(screen.getByLabelText('执行功能').value).toBe('study');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('执行功能'), { target: { value: 'visits' } });
    expect(screen.getByRole('button', { name: '开始提交次数' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /模拟课程一/ }));
    fireEvent.change(screen.getByLabelText('每门课程提交次数'), { target: { value: '12' } });
    fireEvent.change(screen.getByLabelText('提交间隔（秒）'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: '开始提交次数' }));
    expect(start).toHaveBeenCalledExactlyOnceWith({ task_type: 'visits', course_list: ['one'], tool_options: { count: 12, interval: 5 } });
    fireEvent.change(screen.getByLabelText('执行功能'), { target: { value: 'study' } });
    expect(screen.getByLabelText('播放倍速')).toBeTruthy();
  });

  it('disables visits with empty, fractional or out-of-range options without coercing them to defaults', async () => {
    selectionServices({ selectedCoursesByAccount: { alice: ['one'] } });
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    fireEvent.change(await screen.findByLabelText('执行功能'), { target: { value: 'visits' } });
    for (const [label, values, valid] of [
      ['每门课程提交次数', ['', '0', '-1', '1001', '1.5'], '1000'],
      ['提交间隔（秒）', ['', '0', '3601', '2.5'], '3600'],
    ]) {
      for (const value of values) {
        fireEvent.change(screen.getByLabelText(label), { target: { value } });
        const button = screen.getByRole('button', { name: '开始提交次数' });
        expect(button.disabled).toBe(true);
        expect(screen.getByLabelText(label).getAttribute('aria-invalid')).toBe('true');
        fireEvent.click(button);
      }
      fireEvent.change(screen.getByLabelText(label), { target: { value: valid } });
    }
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '开始提交次数' }));
    expect(start.mock.lastCall[0].tool_options).toEqual({ count: 1000, interval: 3600 });
  });

  it.each([['video_time', '读取视频列表'], ['download', '读取资源列表']])('starts a %s catalog for the selected courses', async (purpose, label) => {
    selectionServices();
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    fireEvent.change(await screen.findByLabelText('执行功能'), { target: { value: purpose } });
    expect(screen.getByRole('button', { name: label }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /模拟课程二/ }));
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(start).toHaveBeenCalledExactlyOnceWith({ task_type: 'catalog', course_list: ['two'], tool_options: { purpose } });
  });

  it('resets tool configuration and courses when the account changes', async () => {
    selectionServices();
    const view = render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText('执行功能'), { target: { value: 'visits' } });
    fireEvent.change(screen.getByLabelText('每门课程提交次数'), { target: { value: '44' } });
    fireEvent.click(screen.getByRole('button', { name: /模拟课程一/ }));
    view.rerender(<CourseSelection userInfo={{ ...account, username: 'bob' }} onStartStudy={vi.fn()} />);
    await screen.findByRole('button', { name: /模拟课程一/ });
    expect(screen.getByLabelText('执行功能').value).toBe('study');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('执行功能'), { target: { value: 'visits' } });
    expect(screen.getByLabelText('每门课程提交次数').value).toBe('10');
  });
});

describe('resource selection and task results', () => {
  it('starts only explicitly selected resources and keeps hidden selections through search and filtering', async () => {
    progressServices(catalogState(), catalogTool());
    const start = vi.fn();
    render(<StudyProgress taskId="catalog-task" username="alice" onStartStudy={start} />);
    await screen.findByRole('checkbox', { name: '选择资源 课程讲义' });
    expect(screen.getAllByRole('checkbox').every((checkbox) => !checkbox.checked)).toBe(true);
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(true);
    expect(screen.getByRole('checkbox', { name: '选择资源 不可用视频' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程讲义' }));
    fireEvent.change(screen.getByLabelText('搜索资源'), { target: { value: '第二章' } });
    fireEvent.change(screen.getByLabelText('资源类型'), { target: { value: 'video' } });
    fireEvent.click(screen.getByRole('button', { name: '全选当前列表' }));
    expect(screen.getByRole('checkbox', { name: '选择资源 补充视频' }).checked).toBe(true);
    expect(screen.getByRole('checkbox', { name: '选择资源 不可用视频' }).checked).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '下载所选资源' }));
    await waitFor(() => expect(start).toHaveBeenCalledOnce());
    expect(start.mock.lastCall[0]).toEqual({ task_type: 'download', course_list: ['one', 'two'], tool_options: { source_task_id: 'catalog-task', resource_ids: ['document-one', 'video-two'] } });
    await waitFor(() => expect(screen.queryByRole('button', { name: '任务启动中' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(true);
  });

  it('validates video minutes and supports targets shorter than a minute', async () => {
    progressServices(catalogState(), catalogTool('video_time'));
    const start = vi.fn();
    render(<StudyProgress taskId="videos" username="alice" onStartStudy={start} />);
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择资源 课程视频' }));
    expect(screen.queryByText('课程讲义')).toBeNull();
    for (const value of ['', '0', '-1', '0.05', '1441']) {
      fireEvent.change(screen.getByLabelText('每个视频增加时长（分钟）'), { target: { value } });
      expect(screen.getByRole('button', { name: '开始累计时长' }).disabled).toBe(true);
      fireEvent.click(screen.getByRole('button', { name: '开始累计时长' }));
    }
    expect(start).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('每个视频增加时长（分钟）'), { target: { value: '0.1' } });
    fireEvent.click(screen.getByRole('button', { name: '开始累计时长' }));
    await waitFor(() => expect(start).toHaveBeenCalledExactlyOnceWith({ task_type: 'video_time', course_list: ['one'], tool_options: { source_task_id: 'videos', resource_ids: ['video-one'], minutes: 0.1 } }));
  });

  it('waits for final details after a catalog becomes terminal and retains the stop lifecycle', async () => {
    vi.useFakeTimers();
    const finalDetails = deferred();
    let terminal = false;
    api.get.mockImplementation(async (url) => {
      if (url.endsWith('/details')) return terminal ? finalDetails.promise : ok({ tool: catalogTool() });
      if (url.startsWith('/logs/')) return logPage();
      return ok(catalogState(terminal ? 'completed' : 'running'));
    });
    const stop = vi.fn().mockResolvedValue(undefined);
    const start = vi.fn();
    render(<StudyProgress taskId="reading" username="alice" onStartStudy={start} onStop={stop} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '停止任务' }));
    expect(stop).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认停止任务' }));
    await act(async () => {});
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: '停止任务' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(true);
    terminal = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText('正在获取最终资源列表…')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).disabled).toBe(true);
    await act(async () => finalDetails.resolve(ok({ tool: catalogTool() })));
    expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).disabled).toBe(false);
    expect(screen.queryByRole('button', { name: '停止任务' })).toBeNull();
    expect(start).not.toHaveBeenCalled();
  });

  it.each(['error', 'cancelled'])('does not launch from a %s catalog snapshot', async (status) => {
    progressServices(catalogState(status), catalogTool());
    render(<StudyProgress taskId="failed-catalog" username="alice" onStartStudy={vi.fn()} />);
    await screen.findByRole('checkbox', { name: '选择资源 课程视频' });
    expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(true);
  });

  it('allows available resources in a partial catalog and displays failed scan results', async () => {
    const tool = catalogTool();
    tool.results = [{ id: 'failed-chapter', name: '第三章', course_title: '模拟课程一', status: 'error', message: '章节读取失败，请重新读取' }];
    progressServices(catalogState('partial'), tool);
    render(<StudyProgress taskId="partial-catalog" username="alice" onStartStudy={vi.fn()} />);
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择资源 课程视频' }));
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(false);
    expect(screen.getByText('章节读取失败，请重新读取')).toBeTruthy();
  });

  it('reports submitted visits separately from platform statistics and displays interrupted results', async () => {
    const status = { status: 'partial', task_type: 'visits', task_label: '学习次数', progress: 1, total: 2, error: '任务中断，请核对已完成次数后重新开始' };
    const tool = { completed_units: 3, total_units: 10, unit: '次', current: null, results: [{ id: 'one', name: '模拟课程一', course_title: '模拟课程一', status: 'completed', submitted: 3, before: 7, after: 8 }, { id: 'two', name: '模拟课程二', status: 'skipped', submitted: 0, before: null, after: null }] };
    progressServices(status, tool);
    render(<StudyProgress taskId="visits" username="alice" />);
    const resultsList = await screen.findByRole('region', { name: '工具执行结果' });
    expect(within(resultsList).getByText('3 次')).toBeTruthy();
    expect(within(resultsList).getByText('7 次')).toBeTruthy();
    expect(within(resultsList).getByText('8 次')).toBeTruthy();
    expect(within(resultsList).getAllByText('不可用')).toHaveLength(2);
    expect(screen.getByRole('alert').textContent).toContain('核对已完成次数');
    expect(screen.queryByText('章节统计')).toBeNull();
  });

  it('opens only the task download directory, blocks repeated clicks and shows retryable failures', async () => {
    const pending = deferred();
    api.post.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(ok({ path: 'ignored-server-path' }));
    progressServices({ status: 'completed', task_type: 'download', progress: 1, total: 1 }, downloadTool());
    render(<StudyProgress taskId="download-task" username="alice" />);
    const open = await screen.findByRole('button', { name: '打开下载目录' });
    await waitFor(() => expect(open.disabled).toBe(false));
    expect(screen.getByRole('progressbar', { name: '下载课程讲义' }).getAttribute('aria-valuenow')).toBeNull();
    expect(screen.getByText('1 KB / 未知')).toBeTruthy();
    fireEvent.click(open);
    expect(screen.getByRole('button', { name: '正在打开' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '正在打开' }));
    expect(api.post).toHaveBeenCalledOnce();
    expect(api.post.mock.lastCall).toEqual(['/task/download-task/open-downloads', { username: 'alice' }, { signal: expect.any(AbortSignal) }]);
    await act(async () => pending.reject({ response: { status: 500, data: { msg: '文件管理器启动失败' } } }));
    expect((await screen.findByRole('alert')).textContent).toContain('文件管理器启动失败');
    fireEvent.click(screen.getByRole('button', { name: '打开下载目录' }));
    await screen.findByText('已打开下载目录');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('cancels folder requests on account/task changes and unmount without showing late errors', async () => {
    const old = deferred();
    const newer = deferred();
    api.post.mockReturnValueOnce(old.promise).mockReturnValueOnce(newer.promise);
    const props = { taskStatus: { status: 'completed', task_type: 'download' }, tool: downloadTool() };
    const view = render(<CourseToolProgress {...props} taskId="old" username="alice" />);
    fireEvent.click(screen.getByRole('button', { name: '打开下载目录' }));
    const oldSignal = api.post.mock.lastCall[2].signal;
    view.rerender(<CourseToolProgress {...props} taskId="new" username="bob" />);
    expect(oldSignal.aborted).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '打开下载目录' }));
    const newSignal = api.post.mock.lastCall[2].signal;
    await act(async () => old.reject(new Error('old folder failure')));
    expect(screen.getByRole('button', { name: '正在打开' }).disabled).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(api.post.mock.lastCall[1]).toEqual({ username: 'bob' });
    view.unmount();
    expect(newSignal.aborted).toBe(true);
    await act(async () => newer.resolve(ok({})));
  });

  it('discards resource selections and ignores a failed start after task change', async () => {
    const old = deferred();
    const start = vi.fn().mockReturnValueOnce(old.promise);
    const props = { taskStatus: catalogState(), tool: catalogTool(), catalogReady: true, username: 'alice', onStartStudy: start };
    const view = render(<CourseToolProgress {...props} taskId="old" />);
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程视频' }));
    fireEvent.click(screen.getByRole('button', { name: '下载所选资源' }));
    view.rerender(<CourseToolProgress {...props} taskId="new" />);
    await act(async () => old.reject(new Error('old start failure')));
    expect(screen.getAllByRole('checkbox').every((checkbox) => !checkbox.checked)).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(true);
  });
});

function appServices({ purpose = 'download', followup } = {}) {
  selectionServices({ selectedCoursesByAccount: { alice: ['one', 'two'], bob: ['one'] } });
  const selectionGet = api.get.getMockImplementation();
  const selectionPost = api.post.getMockImplementation();
  api.get.mockImplementation(async (url, options) => {
    if (url === '/config') return selectionGet(url, options);
    if (url === '/task/catalog-task') return ok(catalogState());
    if (url === '/task/catalog-task/details') return ok({ courses: [], tool: catalogTool(purpose) });
    if (url.endsWith('/details')) return ok({ courses: [], tool: { completed_units: 0, total_units: 1, unit: '秒', results: [] } });
    if (url.startsWith('/logs/')) return logPage();
    return ok({ status: 'running', task_type: purpose, progress: 0, total: 1 });
  });
  api.post.mockImplementation(async (url, body, options) => {
    if (url === '/start') {
      if (body.task_type === 'catalog') return ok({ task_id: 'catalog-task' });
      if (body.username === 'bob') return ok({ task_id: 'bob-task' });
      return followup ? followup(body, options) : ok({ task_id: 'next-task' });
    }
    if (url.endsWith('/resume')) return ok({ task_id: url.split('/')[2], status: 'running' });
    return selectionPost(url, body, options);
  });
}

async function openCatalog(purpose = 'download') {
  await sessionStore.rememberLogin('alice');
  render(<App />);
  fireEvent.change(await screen.findByLabelText('执行功能'), { target: { value: purpose } });
  fireEvent.click(screen.getByRole('button', { name: purpose === 'video_time' ? '读取视频列表' : '读取资源列表' }));
  await screen.findByRole('checkbox', { name: '选择资源 课程视频' });
  await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).disabled).toBe(false));
}

describe('shared App start lifecycle', () => {
  it.each(['video_time', 'download'])('runs catalog then %s through the same start and persistence machinery', async (purpose) => {
    appServices({ purpose });
    await openCatalog(purpose);
    expect((await sessionStore.read()).activeTask).toEqual({ username: 'alice', taskId: 'catalog-task' });
    expect(screen.getAllByRole('checkbox').every((checkbox) => !checkbox.checked)).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程视频' }));
    if (purpose === 'video_time') fireEvent.change(screen.getByLabelText('每个视频增加时长（分钟）'), { target: { value: '0.1' } });
    fireEvent.click(screen.getByRole('button', { name: purpose === 'video_time' ? '开始累计时长' : '下载所选资源' }));
    await screen.findByText('next-task');
    const starts = api.post.mock.calls.filter(([url]) => url === '/start');
    expect(starts).toHaveLength(2);
    expect(starts[1][1]).toEqual({ ...account, task_type: purpose, course_list: ['one'], tool_options: { source_task_id: 'catalog-task', resource_ids: ['video-one'], ...(purpose === 'video_time' ? { minutes: 0.1 } : {}) } });
    await waitFor(async () => expect((await sessionStore.read()).activeTask).toEqual({ username: 'alice', taskId: 'next-task' }));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(true);
  });

  it.each([404, 500])('shows a %s follow-up failure on the catalog progress page and allows retry', async (status) => {
    appServices({ followup: () => Promise.reject({ response: { status, data: { msg: status === 404 ? '来源任务已过期，请重新读取资源列表' : '启动任务失败，请重试' } } }) });
    await openCatalog();
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程视频' }));
    fireEvent.click(screen.getByRole('button', { name: '下载所选资源' }));
    expect((await screen.findByRole('alert')).textContent).toContain(status === 404 ? '来源任务已过期' : '启动任务失败');
    expect(within(screen.getByRole('region', { name: '资源选择' })).getByRole('alert')).toBeTruthy();
    expect(screen.getByText('catalog-task')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: '选择资源 课程视频' }).checked).toBe(true);
    expect(screen.getByRole('button', { name: '下载所选资源' }).disabled).toBe(false);
    expect((await sessionStore.read()).activeTask.taskId).toBe('catalog-task');
  });

  it('adopts a conflicting task and reports follow-up persistence errors in its progress page', async () => {
    appServices({ followup: () => Promise.reject({ response: { status: 409, data: { data: { task_id: 'existing-tool' } } } }) });
    await openCatalog();
    vi.spyOn(sessionStore, 'rememberTask').mockRejectedValueOnce(new Error('storage failed'));
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程视频' }));
    fireEvent.click(screen.getByRole('button', { name: '下载所选资源' }));
    await screen.findByText('existing-tool');
    expect((await screen.findByRole('alert')).textContent).toContain('恢复信息未能保存');
    expect(api.post.mock.calls.some(([url]) => url === '/task/existing-tool/resume')).toBe(true);
  });

  it.each(['success', 'error', 'conflict'])('ignores a late %s resource start after logging out and switching accounts', async (outcome) => {
    const pending = deferred();
    appServices({ followup: () => pending.promise });
    await openCatalog();
    fireEvent.click(screen.getByRole('checkbox', { name: '选择资源 课程视频' }));
    fireEvent.click(screen.getByRole('button', { name: '下载所选资源' }));
    const oldSignal = api.post.mock.calls.filter(([url]) => url === '/start').at(-1)[2].signal;
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await manualLogin('bob');
    expect(oldSignal.aborted).toBe(true);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('bob-task');
    await act(async () => {
      if (outcome === 'success') pending.resolve(ok({ task_id: 'old-resource-task' }));
      else pending.reject({ response: { status: outcome === 'conflict' ? 409 : 500, data: { msg: 'old failure', data: { task_id: 'old-resource-task' } } } });
    });
    expect(screen.getByText('bob-task')).toBeTruthy();
    expect(screen.queryByText('old-resource-task')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect((await sessionStore.read()).activeTask).toEqual({ username: 'bob', taskId: 'bob-task' });
  });
});
