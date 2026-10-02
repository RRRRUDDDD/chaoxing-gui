import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';
import api from '../api/axios';

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
beforeEach(() => {
  window.matchMedia = vi.fn(() => ({ matches: true }));
  api.get.mockReset().mockResolvedValue(ok({}));
  api.post.mockReset().mockImplementation(async (url) => ok(url === '/courses' ? [
    { courseId: '1', title: 'First course' }, { courseId: '2', title: 'Second course' },
  ] : {}));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('tracks real modifications and clears stale success, ignoring click order', async () => {
  api.get.mockResolvedValue(ok({ selectedCoursesByAccount: { alice: ['1', '2'] } }));
  await mountCourses();
  expect(screen.queryByText('有未保存的更改')).toBeNull();
  const first = screen.getByRole('button', { name: /First course/ });
  fireEvent.click(first);
  expect(screen.getByText('有未保存的更改')).toBeTruthy();
  fireEvent.click(first);
  expect(screen.queryByText('有未保存的更改')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  await screen.findByText('配置已保存');
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.5' } });
  expect(screen.queryByText('配置已保存')).toBeNull();
  expect(screen.getByText('有未保存的更改')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1' } });
  expect(screen.queryByText('有未保存的更改')).toBeNull();
});
it.each(['business', 'network'])('renders a %s save failure as an alert, retaining modifications', async (kind) => {
  await mountCourses();
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  if (kind === 'business') api.post.mockResolvedValueOnce({ data: { status: false, msg: '无法保存配置' } });
  else api.post.mockRejectedValueOnce(new Error('offline'));
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  const alert = await screen.findByRole('alert');
  expect(alert.className).toContain('text-danger');
  expect(screen.getByText('有未保存的更改')).toBeTruthy();
});
it('acknowledges only the request snapshot when editing continues during save', async () => {
  await mountCourses();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  expect(screen.getByRole('button', { name: '保存中' }).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1.5' } });
  await act(async () => pending.resolve(ok({})));
  expect(screen.queryByText('配置已保存')).toBeNull();
  expect(screen.getByText('有未保存的更改')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('播放倍速'), { target: { value: '1' } });
  expect(screen.queryByText('有未保存的更改')).toBeNull();
});
it.each(['success', 'failure'])('ignores a late save %s after changing accounts', async (outcome) => {
  const view = await mountCourses();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: /First course/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  const signal = api.post.mock.calls.find(([url]) => url === '/config')[2].signal;
  view.rerender(<CourseSelection userInfo={{ ...account, username: 'bob' }} onStartStudy={vi.fn()} />);
  await screen.findByRole('button', { name: /First course/ });
  expect(signal.aborted).toBe(true);
  await act(async () => outcome === 'success' ? pending.resolve(ok({})) : pending.reject(new Error('old failure')));
  expect(screen.queryByText('配置已保存')).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('有未保存的更改')).toBeNull();
});
it('keeps wide editor text and numeric drafts without saving or disrupting disclosure focus', async () => {
  await mountCourses();
  const outer = screen.getByRole('button', { name: '展开高级配置' });
  expect(screen.queryByRole('button', { name: '展开题库配置' })).toBeNull();
  fireEvent.click(outer);
  fireEvent.click(screen.getByRole('button', { name: '展开题库配置' }));
  expect(screen.getByRole('button', { name: '展开任务通知' })).toBeTruthy();
  const bankPanel = document.getElementById(screen.getByRole('button', { name: '收起题库配置' }).getAttribute('aria-controls'));
  expect(bankPanel.querySelector('button').parentElement.querySelector('p')).toBeNull();
  const delay = screen.getByRole('spinbutton', { name: '查询延迟（秒）' });
  fireEvent.change(delay, { target: { value: '1.50' } });
  delay.focus();
  fireEvent.click(outer);
  expect(document.activeElement).toBe(outer);
  expect(document.getElementById(outer.getAttribute('aria-controls')).hidden).toBe(true);
  expect(screen.queryByRole('spinbutton', { name: '查询延迟（秒）' })).toBeNull();
  fireEvent.click(outer);
  expect(screen.getByRole('spinbutton', { name: '查询延迟（秒）' }).value).toBe('1.50');
  const trigger = screen.getByRole('button', { name: '展开编辑' });
  fireEvent.click(trigger);
  expect(screen.queryByRole('button', { name: '开始学习' })).toBeNull();
  expect(screen.getByText(/编辑时不会请求订阅链接/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('题库配置'), { target: { value: 'https://example.com/subscription' } });
  fireEvent.click(screen.getByRole('button', { name: '返回配置面板' }));
  expect(document.activeElement).toBe(trigger);
  expect(delay.value).toBe('1.50');
  fireEvent.click(trigger);
  expect(screen.getByLabelText('题库配置').value).toBe('https://example.com/subscription');
  expect(api.post.mock.calls.every(([url]) => url === '/courses')).toBe(true);
});
it('preview save updates only local state and demo totals match course details', async () => {
  const view = render(<CourseSelection userInfo={account} preview />);
  fireEvent.click(await screen.findByRole('button', { name: /大学英语/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  expect(screen.getByText('演示配置已保存')).toBeTruthy();
  expect(screen.queryByText('有未保存的更改')).toBeNull();
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
  const log = screen.getByRole('log');
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
it('cancels an in-flight configuration save when unmounted', async () => {
  const view = await mountCourses();
  const pending = deferred();
  api.post.mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  const signal = api.post.mock.calls.find(([url]) => url === '/config')[2].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve(ok({})));
  expect(screen.queryByText('配置已保存')).toBeNull();
});
