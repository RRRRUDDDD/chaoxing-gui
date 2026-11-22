import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import api from '../api/axios';
import { sessionStore } from '../lib/sessionStore';
import { loginLabels, startStudyLabel } from '../lib/uiText';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';

vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const account = { username: 'alice', password: '', use_cookies: true };
const courses = [{ courseId: 'one', title: '模拟课程一' }];
const ok = (data) => ({ data: { status: true, data } });
const logPage = () => ({ data: { status: true, data: [], next_cursor: 0, truncated: false } });

beforeEach(async () => {
  await sessionStore.clear();
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  window.matchMedia = vi.fn(() => ({ matches: true }));
  api.get.mockReset();
  api.post.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('shutdown on complete', () => {
  it('passes shutdown_on_complete for study and visits but never for catalog', async () => {
    api.get.mockImplementation(async (url) => {
      if (url === '/config') return ok({ selectedCoursesByAccount: { alice: ['one'] } });
      throw new Error(`Unexpected GET ${url}`);
    });
    api.post.mockImplementation(async (url) => {
      if (url === '/courses') return ok(courses);
      if (url === '/config') return ok({});
      throw new Error(`Unexpected POST ${url}`);
    });

    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    await screen.findByRole('button', { name: /模拟课程一/ });

    fireEvent.click(screen.getByRole('button', { name: '展开应用设置' }));
    fireEvent.click(screen.getByLabelText('刷取任务完成后自动关机'));

    fireEvent.click(screen.getByRole('button', { name: startStudyLabel }));
    expect(start.mock.lastCall[0].shutdown_on_complete).toBe(true);

    fireEvent.click(screen.getByRole('radio', { name: '学习次数' }));
    fireEvent.click(screen.getByRole('button', { name: '开始提交次数' }));
    expect(start.mock.lastCall[0]).toEqual({
      task_type: 'visits',
      course_list: ['one'],
      tool_options: { count: 10, interval: 30 },
      shutdown_on_complete: true,
    });

    fireEvent.click(screen.getByRole('radio', { name: '视频时长' }));
    fireEvent.click(screen.getByRole('button', { name: '读取视频列表' }));
    expect(start.mock.lastCall[0]).toEqual({
      task_type: 'catalog',
      course_list: ['one'],
      tool_options: { purpose: 'video_time' },
    });
  });

  it('forwards shutdown_on_complete from App settings into the second-stage video_time task', async () => {
    const startPayloads = [];
    api.get.mockImplementation(async (url) => {
      if (url === '/config') return ok({ selectedCoursesByAccount: { alice: ['one'] } });
      if (url === '/task/catalog-1') return ok({ status: 'completed', task_type: 'catalog', task_label: '读取视频列表', progress: 1, total: 1 });
      if (url === '/task/catalog-1/details') {
        return ok({
          courses: [],
          active_jobs: {},
          tool: {
            purpose: 'video_time',
            course_ids: ['one'],
            resources: [{
              id: 'video-1', course_id: 'one', course_title: '模拟课程一',
              chapter_id: 'ch-1', chapter_title: '第一章', name: '课程视频',
              kind: 'video', watchable: true, downloadable: false, readable: false, duration: 60,
            }],
            results: [],
            completed_units: 1,
            total_units: 1,
            unit: '章节',
            current: null,
          },
        });
      }
      if (url === '/task/video-1') return ok({ status: 'running', task_type: 'video_time', task_label: '视频观看时长', progress: 0, total: 1, shutdown_on_complete: true });
      if (url === '/task/video-1/details') return ok({ courses: [], active_jobs: {}, tool: { purpose: 'video_time', course_ids: ['one'], resources: [], results: [], completed_units: 0, total_units: 1800, unit: '秒', current: null } });
      if (url.startsWith('/logs/')) return logPage();
      throw new Error(`Unexpected GET ${url}`);
    });
    api.post.mockImplementation(async (url, body) => {
      if (url === '/login') return ok({ username: body.username });
      if (url === '/courses') return ok(courses);
      if (url === '/config') return ok({});
      if (url === '/start') {
        startPayloads.push(body);
        return ok({ task_id: body.task_type === 'catalog' ? 'catalog-1' : 'video-1' });
      }
      throw new Error(`Unexpected POST ${url}`);
    });

    render(<App />);
    fireEvent.change(await screen.findByLabelText(loginLabels.phone), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText(loginLabels.password), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: loginLabels.submit }));
    await screen.findByRole('button', { name: /模拟课程一/ });

    fireEvent.click(screen.getByRole('button', { name: '展开应用设置' }));
    fireEvent.click(screen.getByLabelText('刷取任务完成后自动关机'));
    fireEvent.click(screen.getByRole('radio', { name: '视频时长' }));
    fireEvent.click(screen.getByRole('button', { name: '读取视频列表' }));

    const videoBox = await screen.findByRole('checkbox', { name: '选择资源 课程视频' });
    await waitFor(() => expect(videoBox.disabled).toBe(false));
    fireEvent.click(videoBox);
    fireEvent.click(screen.getByRole('button', { name: '开始累计时长' }));

    await waitFor(() => expect(startPayloads).toHaveLength(2));
    expect(startPayloads[0].task_type).toBe('catalog');
    expect(startPayloads[0].shutdown_on_complete).toBeUndefined();
    expect(startPayloads[1].task_type).toBe('video_time');
    expect(startPayloads[1].shutdown_on_complete).toBe(true);
  });

  it('shows armed notice while running and allows cancelling auto-shutdown before completion', async () => {
    api.get.mockImplementation(async (url) => {
      if (url.endsWith('/details')) return ok({ courses: [], active_jobs: {} });
      if (url.startsWith('/logs/')) return logPage();
      return ok({ status: 'running', progress: 0, total: 1, shutdown_on_complete: true });
    });
    api.post.mockResolvedValueOnce(ok({ task_id: 'task-1', state: 'cancelled' }));

    render(<StudyProgress taskId="task-1" username="alice" />);
    expect(await screen.findByText(/已开启完成后自动关机/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '取消关机' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/task/task-1/cancel-shutdown',
      { username: 'alice' },
      { signal: expect.any(AbortSignal) },
    ));
    expect(await screen.findByText(/已取消自动关机/)).toBeTruthy();
  });

  it('displays live countdown when shutdown is scheduled and cancels on button click', async () => {
    vi.useFakeTimers();
    const nowSec = 1_700_000_000;
    vi.setSystemTime(nowSec * 1000);

    api.get.mockImplementation(async (url) => {
      if (url.endsWith('/details')) return ok({ courses: [], active_jobs: {} });
      if (url.startsWith('/logs/')) return logPage();
      return ok({
        status: 'completed',
        progress: 1,
        total: 1,
        shutdown_on_complete: true,
        shutdown: { state: 'scheduled', delay_seconds: 60, scheduled_at: nowSec, shutdown_at: nowSec + 60 },
      });
    });
    api.post.mockResolvedValueOnce(ok({ task_id: 'task-2', state: 'cancelled' }));

    render(<StudyProgress taskId="task-2" username="alice" />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('系统将在 60 秒后自动关机')).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText('系统将在 57 秒后自动关机')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '取消关机' }));
    await act(async () => { await Promise.resolve(); });
    expect(api.post).toHaveBeenCalledWith(
      '/task/task-2/cancel-shutdown',
      { username: 'alice' },
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByText(/已取消自动关机/)).toBeTruthy();
  });

  it('shows failure alert when OS shutdown scheduling fails', async () => {
    api.get.mockImplementation(async (url) => {
      if (url.endsWith('/details')) return ok({ courses: [], active_jobs: {} });
      if (url.startsWith('/logs/')) return logPage();
      return ok({
        status: 'completed',
        progress: 1,
        total: 1,
        shutdown_on_complete: true,
        shutdown: { state: 'failed', error: '权限不足' },
      });
    });

    render(<StudyProgress taskId="task-3" username="alice" />);
    expect((await screen.findByText(/自动关机启动失败/)).textContent).toContain('权限不足');
  });

  it('surfaces scheduled shutdown countdown on the course page and allows cancelling it there', async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    let taskPayload = { status: 'running', progress: 0, total: 1, shutdown_on_complete: true };
    api.get.mockImplementation(async (url) => {
      if (url === '/config') return ok({ selectedCoursesByAccount: { alice: ['one'] } });
      if (url === '/task/bg-task/details') return ok({ courses: [], active_jobs: {} });
      if (url.startsWith('/logs/')) return logPage();
      if (url === '/task/bg-task') return ok(taskPayload);
      throw new Error(`Unexpected GET ${url}`);
    });
    api.post.mockImplementation(async (url, body) => {
      if (url === '/login') return ok({ username: body.username });
      if (url === '/courses') return ok(courses);
      if (url === '/start') return ok({ task_id: 'bg-task' });
      if (url === '/task/bg-task/cancel-shutdown') return ok({ task_id: 'bg-task', state: 'cancelled' });
      throw new Error(`Unexpected POST ${url}`);
    });

    await sessionStore.rememberLogin('alice');
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: startStudyLabel }));
    await screen.findByText('bg-task');

    // Return to course selection while running; background poll observes completion + scheduled shutdown.
    taskPayload = {
      status: 'completed',
      progress: 1,
      total: 1,
      shutdown_on_complete: true,
      shutdown: { state: 'scheduled', delay_seconds: 60, scheduled_at: nowSec, shutdown_at: nowSec + 60 },
    };
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));

    expect(await screen.findByText(/系统将在 \d+ 秒后自动关机/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '取消关机' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/task/bg-task/cancel-shutdown',
      { username: 'alice' },
      { signal: expect.any(AbortSignal) },
    ));
    await waitFor(() => expect(screen.queryByText(/系统将在 \d+ 秒后自动关机/)).toBeNull());
  });
});
