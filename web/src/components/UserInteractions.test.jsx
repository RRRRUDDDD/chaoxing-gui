import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';
import RepositoryLink, { REPOSITORY_URL } from './RepositoryLink';
import AdvancedSettings from './AdvancedSettings';
import api from '../api/axios';

const core = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);
vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const account = { username: 'alice', password: '', use_cookies: true };
const courses = [{ courseId: '1', title: 'First course' }, { courseId: '2', title: 'Second course' }];
const ok = (data) => ({ data: { status: true, data } });

beforeEach(() => {
  core.isTauri.mockReset().mockReturnValue(false);
  core.invoke.mockReset().mockResolvedValue(undefined);
  api.get.mockReset().mockResolvedValue(ok({}));
  api.post.mockReset().mockResolvedValue(ok(courses));
  window.matchMedia = vi.fn(() => ({ matches: true }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('starts with no selected courses and selects the current list with Ctrl+A or Cmd+A', async () => {
  const start = vi.fn();
  await act(async () => { render(<CourseSelection userInfo={account} onStartStudy={start} />); });
  const first = screen.getByRole('button', { name: /First course/ });
  expect(first.getAttribute('aria-pressed')).toBe('false');
  expect(screen.getByRole('button', { name: /Second course/ }).getAttribute('aria-pressed')).toBe('false');
  expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  for (const modifier of ['ctrlKey', 'metaKey']) {
    expect(fireEvent.keyDown(document.body, { key: 'a', [modifier]: true })).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(start.mock.lastCall[0].course_list).toEqual(['1', '2']);
    fireEvent.click(screen.getByRole('button', { name: '清空' }));
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  }
});

it('preserves text selection in the search field and hidden course choices when selecting filtered results', async () => {
  api.get.mockResolvedValue(ok({ selectedCoursesByAccount: { alice: ['2'] } }));
  const start = vi.fn();
  const view = render(<CourseSelection userInfo={account} onStartStudy={start} />);
  await screen.findByRole('button', { name: /First course/ });
  const search = screen.getByRole('searchbox');
  fireEvent.change(search, { target: { value: 'First' } });
  search.focus();
  expect(fireEvent.keyDown(search, { key: 'a', ctrlKey: true })).toBe(true);
  expect(screen.getByRole('button', { name: /First course/ }).getAttribute('aria-pressed')).toBe('false');
  fireEvent.keyDown(document.body, { key: 'A', ctrlKey: true });
  fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
  expect(start.mock.lastCall[0].course_list).toEqual(['2', '1']);
  view.unmount();
  expect(fireEvent.keyDown(document.body, { key: 'a', ctrlKey: true })).toBe(true);
});

it('does not select courses while their configuration is unavailable', async () => {
  api.get.mockRejectedValue(new Error('offline'));
  render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
  await screen.findByRole('alert');
  expect(fireEvent.keyDown(document.body, { key: 'a', ctrlKey: true })).toBe(true);
  expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  expect(screen.getByRole('button', { name: /First course/ }).getAttribute('aria-pressed')).toBe('false');
});

const externalLinks = [
  { kind: 'repository', name: '在 GitHub 上查看项目', url: REPOSITORY_URL, command: 'open_repository' },
  { kind: 'OCS docs', name: 'OCS 题库配置', url: 'https://docs.ocsjs.com/docs/work', command: 'open_ocs_docs' },
];

async function renderExternalLink({ kind, name }) {
  core.invoke.mockResolvedValue({ closeAction: 'ask' });
  await act(async () => {
    render(kind === 'repository' ? <RepositoryLink /> : <AdvancedSettings settings={{}} onChange={vi.fn()} />);
  });
  if (kind !== 'repository') fireEvent.click(screen.getByRole('button', { name: '展开高级配置' }));
  core.invoke.mockClear();
  return screen.getByRole('link', { name });
}

it.each(externalLinks)('opens $kind through its fixed Tauri command and recovers from browser errors', async (target) => {
  core.isTauri.mockReturnValue(true);
  const link = await renderExternalLink(target);
  expect(link.getAttribute('href')).toBe(target.url);
  expect(fireEvent.click(link)).toBe(false);
  expect(core.invoke).toHaveBeenCalledExactlyOnceWith(target.command);
  core.invoke.mockRejectedValueOnce(new Error('browser unavailable'));
  fireEvent.click(link);
  expect((await screen.findByRole('alert')).textContent).toContain('无法打开浏览器');
  await act(async () => { fireEvent.click(link); });
  expect(screen.queryByRole('alert')).toBeNull();
});

it.each(externalLinks)('opens $kind on middle click but not on right click in Tauri', async (target) => {
  core.isTauri.mockReturnValue(true);
  const link = await renderExternalLink(target);
  const middle = new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 });
  expect(fireEvent(link, middle)).toBe(false);
  expect(core.invoke).toHaveBeenCalledExactlyOnceWith(target.command);
  core.invoke.mockClear();
  const right = new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 2 });
  expect(fireEvent(link, right)).toBe(true);
  expect(core.invoke).not.toHaveBeenCalled();
});

it.each(externalLinks)('keeps native browser navigation for $kind', async (target) => {
  const link = await renderExternalLink(target);
  // Observe the component decision, then prevent jsdom from navigating.
  let prevented;
  const observe = (event) => { prevented = event.defaultPrevented; event.preventDefault(); };
  document.addEventListener('click', observe, { once: true });
  fireEvent.click(link);
  expect(prevented).toBe(false);
  expect(link.getAttribute('href')).toBe(target.url);
  expect(link.target).toBe('_blank');
  expect(link.rel).toBe('noopener noreferrer');
  expect(core.invoke).not.toHaveBeenCalled();
});

it('follows logs inside their own container without moving focus, and pauses when scrolled up', async () => {
  vi.useFakeTimers();
  let sequence = 0;
  api.get.mockImplementation(async (url) => {
    if (url.endsWith('/details')) return ok({ courses: [] });
    if (url.startsWith('/logs/')) {
      sequence += 1;
      return { data: { status: true, data: [{ seq: sequence, timestamp: sequence, message: `line-${sequence}` }], next_cursor: sequence } };
    }
    return ok({ status: 'running', progress: 0, total: 1 });
  });
  const oldScroll = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView');
  const scrollIntoView = vi.fn();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });
  const windowScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  try {
    render(<StudyProgress taskId="task" />);
    const log = screen.getByRole('log');
    Object.defineProperties(log, {
      scrollHeight: { configurable: true, writable: true, value: 1200 },
      clientHeight: { configurable: true, value: 400 },
    });
    const back = screen.getByRole('button', { name: '返回课程选择' });
    back.focus();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('line-1')).toBeTruthy();
    expect(log.scrollTop).toBe(1200);
    expect(document.activeElement).toBe(back);
    fireEvent.scroll(log, { target: { scrollTop: 100 } });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText('line-2')).toBeTruthy();
    expect(log.scrollTop).toBe(100);
    fireEvent.scroll(log, { target: { scrollTop: 800 } });
    log.scrollHeight = 1500;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(log.scrollTop).toBe(1500);
    expect(document.activeElement).toBe(back);
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(windowScroll).not.toHaveBeenCalled();
  } finally {
    if (oldScroll) Object.defineProperty(Element.prototype, 'scrollIntoView', oldScroll);
    else delete Element.prototype.scrollIntoView;
  }
});
