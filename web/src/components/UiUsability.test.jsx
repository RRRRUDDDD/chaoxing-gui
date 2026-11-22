import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import CourseSelection, { AUTO_SAVE_DELAY_MS } from './CourseSelection';
import StudyProgress from './StudyProgress';
import api from '../api/axios';
import { configSavedMessage, logRole } from '../lib/uiText';

vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const account = { username: 'alice', password: '', use_cookies: true };
const ok = (data) => ({ data: { status: true, data } });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const mountCourses = async (props = {}) => {
  const view = render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} {...props} />);
  await screen.findByRole('button', { name: /First course/ });
  return view;
};
const flushAutoSave = async () => {
  await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_SAVE_DELAY_MS); });
};
beforeEach(() => {
  window.matchMedia = vi.fn(() => ({ matches: true }));
  api.get.mockReset().mockResolvedValue(ok({}));
  api.post.mockReset().mockImplementation(async (url) => ok(url === '/courses' ? [
    { courseId: '1', title: 'First course' }, { courseId: '2', title: 'Second course' },
  ] : {}));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('auto-saves real modifications after input debounce and skips saving when reverted', async () => {
  api.get.mockResolvedValue(ok({ selectedCoursesByAccount: { alice: ['1', '2'] } }));
  await mountCourses();
  vi.useFakeTimers();
  expect(screen.queryByRole('button', { name: '保存为默认配置' })).toBeNull();
  const first = screen.getByRole('button', { name: /First course/ });
  fireEvent.click(first);
  fireEvent.click(first);
  await flushAutoSave();
  expect(api.post.mock.calls.filter(([url]) => url === '/config')).toHaveLength(0);

  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.5' } });
  expect(api.post.mock.calls.filter(([url]) => url === '/config')).toHaveLength(0);
  await flushAutoSave();
  expect(api.post.mock.calls.filter(([url]) => url === '/config')).toHaveLength(1);
  expect(screen.getByText(configSavedMessage)).toBeTruthy();

  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.8' } });
  expect(screen.queryByText(configSavedMessage)).toBeNull();
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.5' } });
  await flushAutoSave();
  expect(api.post.mock.calls.filter(([url]) => url === '/config')).toHaveLength(1);
});

it.each(['business', 'network'])('renders a %s auto-save failure as an alert with retry without looping', async (kind) => {
  await mountCourses();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  if (kind === 'business') api.post.mockResolvedValueOnce({ data: { status: false, msg: '无法保存配置' } });
  else api.post.mockRejectedValueOnce(new Error('offline'));
  await flushAutoSave();
  const alert = screen.getByRole('alert');
  expect(alert.className).toContain('text-danger');
  const configCalls = () => api.post.mock.calls.filter(([url]) => url === '/config').length;
  expect(configCalls()).toBe(1);
  await flushAutoSave();
  expect(configCalls()).toBe(1);
  fireEvent.click(within(alert).getByRole('button', { name: '重试' }));
  await act(async () => {});
  expect(configCalls()).toBe(2);
  expect(screen.getByText(configSavedMessage)).toBeTruthy();
});

it('auto-saves subsequent edits after an in-flight save finishes', async () => {
  await mountCourses();
  vi.useFakeTimers();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  await flushAutoSave();
  expect(screen.getByText('正在自动保存…')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.5' } });
  await act(async () => pending.resolve(ok({})));
  expect(screen.queryByText(configSavedMessage)).toBeNull();
  await flushAutoSave();
  const configCalls = api.post.mock.calls.filter(([url]) => url === '/config');
  expect(configCalls).toHaveLength(2);
  expect(configCalls[1][1].settings.speed).toBe(1.5);
  expect(screen.getByText(configSavedMessage)).toBeTruthy();
});

it.each(['success', 'failure'])('ignores a late auto-save %s after changing accounts', async (outcome) => {
  const view = await mountCourses();
  vi.useFakeTimers();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  await flushAutoSave();
  const signal = api.post.mock.calls.find(([url]) => url === '/config')[2].signal;
  vi.useRealTimers();
  view.rerender(<CourseSelection userInfo={{ ...account, username: 'bob' }} onStartStudy={vi.fn()} />);
  await screen.findByRole('button', { name: /First course/ });
  expect(signal.aborted).toBe(true);
  await act(async () => outcome === 'success' ? pending.resolve(ok({})) : pending.reject(new Error('old failure')));
  expect(screen.queryByText(configSavedMessage)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('places AI workspace button inside app settings and renders top-bar quick tutor as icon-only', async () => {
  const openAI = vi.fn();
  const openTutor = vi.fn();
  await mountCourses({ onOpenAI: openAI, onOpenTutor: openTutor });
  const header = screen.getByRole('banner');
  expect(within(header).queryByRole('button', { name: /AI 题库/ })).toBeNull();
  const tutorButton = within(header).getByRole('button', { name: '快捷 AI 助教' });
  expect(tutorButton.textContent.trim()).toBe('');
  fireEvent.click(tutorButton);
  expect(openTutor).toHaveBeenCalledOnce();

  fireEvent.click(screen.getByRole('button', { name: '展开应用设置' }));
  fireEvent.click(screen.getByRole('button', { name: 'AI 题库与工作台' }));
  expect(openAI).toHaveBeenCalledExactlyOnceWith('dashboard');
});
it('keeps bank text and numeric drafts across disclosure changes without saving', async () => {
  await mountCourses();
  const outer = screen.getByRole('button', { name: '展开答题设置' });
  fireEvent.click(outer);
  const delay = screen.getByRole('spinbutton', { name: '查询延迟（秒）' });
  fireEvent.change(delay, { target: { value: '1.50' } });
  const bank = screen.getByLabelText('题库配置');
  fireEvent.change(bank, { target: { value: 'https://example.com/subscription' } });
  bank.focus();
  fireEvent.click(outer);
  expect(document.activeElement).toBe(outer);
  expect(document.getElementById(outer.getAttribute('aria-controls')).hidden).toBe(true);
  expect(screen.queryByRole('spinbutton', { name: '查询延迟（秒）' })).toBeNull();
  fireEvent.click(outer);
  expect(screen.getByRole('spinbutton', { name: '查询延迟（秒）' }).value).toBe('1.50');
  expect(screen.getByLabelText('题库配置').value).toBe('https://example.com/subscription');
  expect(screen.getByText(/编辑时不会请求订阅链接/)).toBeTruthy();
  expect(api.post.mock.calls.every(([url]) => url === '/courses')).toBe(true);
});
it('puts the task-type choice above the workspace, retitles the page and summarizes answer settings', async () => {
  await mountCourses();
  const heading = screen.getByRole('heading', { level: 1 });
  expect(heading.textContent).toBe('选择课程并配置学习参数');
  expect(screen.getByRole('radio', { name: '自动学习' }).checked).toBe(true);
  expect(screen.getByText('未配置')).toBeTruthy();
  fireEvent.click(screen.getByRole('radio', { name: '学习次数' }));
  expect(heading.textContent).toBe('选择课程并提交学习次数');
  expect(screen.queryByLabelText('播放倍速')).toBeNull();
  fireEvent.click(screen.getByRole('radio', { name: '自动学习' }));
  fireEvent.click(screen.getByRole('button', { name: '展开答题设置' }));
  // 摘要跟随题库配置状态：填入题库后反映提交方式。
  fireEvent.change(screen.getByLabelText('题库配置'), { target: { value: 'https://example.com/subscription' } });
  expect(screen.getByText('仅保存答案')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('自动提交答题'), { target: { value: 'true' } });
  expect(screen.getByText('自动提交')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '收起答题设置' }));
  expect(screen.getByText('自动提交')).toBeTruthy();
});
it('preview auto-save updates only local state and demo totals match course details', async () => {
  const view = render(<CourseSelection userInfo={account} preview />);
  fireEvent.click(await screen.findByRole('button', { name: /大学英语/ }));
  await screen.findByText('演示配置已保存');
  view.unmount();
  render(<StudyProgress taskId="preview" preview />);
  expect(screen.getByText('2 / 2 课程 · 100%')).toBeTruthy();
  expect(screen.getByText('章节 4 / 4')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: /演示课程/ })).toHaveLength(2);
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
});
it.each([
  ['completed', '所有任务已完成'], ['partial', '任务已结束，部分内容未完成'],
  ['error', '任务执行失败'], ['cancelled', '任务已手动停止'],
])('places %s result and exception statistics before logs', async (status, label) => {
  api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({ courses: [] }) : url.startsWith('/logs/') ?
    { data: { status: true, data: [], next_cursor: 0 } } : ok({ status, progress: 1, total: 2, stats: { failed_chapters: 1, total_chapters: 2 } }));
  render(<StudyProgress taskId="task" />);
  const summary = await screen.findByText(label);
  const log = screen.getByRole(logRole);
  expect(summary.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByText('失败章节').compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
it('does not claim completion with an unknown total', async () => {
  api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({}) : url.startsWith('/logs/') ?
    { data: { status: true, data: [], next_cursor: 0 } } : ok({ status: 'running', progress: 4, total: 0 }));
  render(<StudyProgress taskId="unknown" />);
  await screen.findByText('学习中');
  expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false);
  expect(screen.queryByText(/100%/)).toBeNull();
});

it('keeps long course and chapter names available without hiding their status', async () => {
  const title = 'LongCourseName'.repeat(30);
  const chapterTitle = 'LongChapterName'.repeat(30);
  api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({ courses: [
    { id: 'long', title, status: 'partial', chapters: [{ id: 'chapter', title: chapterTitle, status: 'error' }] },
  ] }) : url.startsWith('/logs/') ? { data: { status: true, data: [], next_cursor: 0 } } : ok({ status: 'partial', total: 1, progress: 0 }));
  render(<StudyProgress taskId="long" />);
  const course = await screen.findByRole('button', { name: new RegExp(title) });
  expect(screen.getByTitle(title).className).toContain('break-words');
  fireEvent.click(course);
  expect(screen.getByText(chapterTitle).className).toContain('break-words');
  expect(screen.getByText('失败').className).toContain('shrink-0');
});
it('cancels an in-flight configuration auto-save when unmounted', async () => {
  const view = await mountCourses();
  vi.useFakeTimers();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  await flushAutoSave();
  const signal = api.post.mock.calls.find(([url]) => url === '/config')[2].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(ok({})));
  expect(screen.queryByText(configSavedMessage)).toBeNull();
});

it('renders top-bar quick tutor as icon-only on study progress page', async () => {
  const openTutor = vi.fn();
  api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({ courses: [] }) : url.startsWith('/logs/') ?
    { data: { status: true, data: [], next_cursor: 0 } } : ok({ status: 'running', progress: 0, total: 1 }));
  render(<StudyProgress taskId="tutor-icon" onOpenTutor={openTutor} />);
  await screen.findByText('学习中');
  const header = screen.getByRole('banner');
  const tutorButton = within(header).getByRole('button', { name: '快捷 AI 助教' });
  expect(tutorButton.textContent.trim()).toBe('');
  fireEvent.click(tutorButton);
  expect(openTutor).toHaveBeenCalledOnce();
});

it('renders safe fallback values when active video job metrics are missing or NaN', async () => {
  api.get.mockImplementation(async (url) => {
    if (url.endsWith('/details')) {
      return ok({
        courses: [],
        active_jobs: {
          'c1:j1': { job_name: '视频一', course_name: '课程一', progress: undefined, current_time: null, duration: Number.NaN },
        },
      });
    }
    if (url.startsWith('/logs/')) return { data: { status: true, data: [], next_cursor: 0 } };
    return ok({ status: 'running', progress: 0, total: 1 });
  });
  render(<StudyProgress taskId="nan-safe" />);
  expect(await screen.findByText('视频一')).toBeTruthy();
  expect(screen.getByText('0%')).toBeTruthy();
  expect(screen.getAllByText('--:--')).toHaveLength(2);
  expect(screen.queryByText(/NaN/)).toBeNull();
});
