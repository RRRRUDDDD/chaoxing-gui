import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ExecutionLog from './ExecutionLog';
import { logRole } from '../lib/uiText';

const logs = [
  { seq: 1, timestamp: 1, level: 'info', message: 'Course loaded' },
  { seq: 2, timestamp: 2, level: 'warning', message: 'Course skipped' },
  { seq: 3, timestamp: 3, level: 'error', message: 'Network failed token=private-value' },
];
const clipboard = (value) => Object.defineProperty(navigator, 'clipboard', { configurable: true, value });
afterEach(() => { cleanup(); clipboard(undefined); vi.restoreAllMocks(); });

it('combines filters, renders messages as text, and reports empty results', () => {
  render(<ExecutionLog logs={[...logs, { seq: 4, message: '<img src=x onerror=alert(1)>' }]} truncated />);
  expect(screen.getByText(/较早的日志已省略/)).toBeTruthy();
  expect(screen.getByRole(logRole).querySelector('img')).toBeNull();
  fireEvent.change(screen.getByLabelText('日志级别'), { target: { value: 'warnings' } });
  fireEvent.change(screen.getByLabelText('搜索日志'), { target: { value: 'course' } });
  const log = screen.getByRole(logRole);
  expect(within(log).getByText('Course skipped')).toBeTruthy();
  expect(within(log).queryByText('Course loaded')).toBeNull();
  expect(screen.getByText('匹配 1 / 4 条')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('搜索日志'), { target: { value: 'absent' } });
  expect(within(log).getByText(/没有匹配的日志/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '复制当前结果' }).disabled).toBe(true);
});
it('copies only filtered logs, with time, level, and masked secrets', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  clipboard({ writeText });
  render(<ExecutionLog logs={logs} />);
  fireEvent.change(screen.getByLabelText('日志级别'), { target: { value: 'errors' } });
  fireEvent.click(screen.getByRole('button', { name: '复制当前结果' }));
  await screen.findByText(/已复制当前结果/);
  expect(writeText).toHaveBeenCalledOnce();
  const text = writeText.mock.calls[0][0];
  expect(text).toContain('[error] Network failed token=[REDACTED]');
  expect(text).not.toContain('private-value');
  expect(text).not.toContain('Course');
  expect(screen.getByRole(logRole).textContent).toContain('private-value');
});
it.each(['missing', 'denied'])('offers a manual copy fallback when clipboard is %s', async (mode) => {
  clipboard(mode === 'missing' ? undefined : { writeText: vi.fn().mockRejectedValue(new Error('denied')) });
  render(<ExecutionLog logs={logs} />);
  fireEvent.change(screen.getByLabelText('日志级别'), { target: { value: 'errors' } });
  fireEvent.click(screen.getByRole('button', { name: '复制当前结果' }));
  const text = await screen.findByRole('textbox', { name: '手动复制日志' });
  expect(text.readOnly).toBe(true);
  expect(text.value).toContain('[REDACTED]');
  expect(text.value).not.toContain('private-value');
  expect(screen.getByLabelText('日志级别').value).toBe('errors');
  expect(screen.getByText(/分享前仍需检查/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '关闭手动复制' }));
  expect(screen.queryByRole('textbox', { name: '手动复制日志' })).toBeNull();
});
it('keeps an explicit pause across new logs and resumes without moving focus', () => {
  const view = render(<ExecutionLog logs={logs} />);
  const log = screen.getByRole(logRole);
  Object.defineProperties(log, {
    scrollHeight: { configurable: true, value: 1200 },
    clientHeight: { configurable: true, value: 400 },
  });
  const toggle = screen.getByRole('checkbox', { name: '自动跟随' });
  fireEvent.click(toggle);
  fireEvent.scroll(log, { target: { scrollTop: 800 } });
  view.rerender(<ExecutionLog logs={[...logs, { seq: 4, message: 'next' }]} />);
  expect(log.scrollTop).toBe(800);
  expect(toggle.checked).toBe(false);
  const latest = screen.getByRole('button', { name: '回到最新' });
  latest.focus();
  fireEvent.click(latest);
  expect(log.scrollTop).toBe(1200);
  expect(toggle.checked).toBe(true);
  expect(document.activeElement).toBe(latest);
});
it('resets tools on a new task key and ignores an old clipboard completion', async () => {
  let resolve;
  clipboard({ writeText: vi.fn(() => new Promise((yes) => { resolve = yes; })) });
  const view = render(<ExecutionLog key="old" logs={logs} />);
  fireEvent.change(screen.getByLabelText('搜索日志'), { target: { value: 'failed' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '自动跟随' }));
  fireEvent.click(screen.getByRole('button', { name: '复制当前结果' }));
  view.rerender(<ExecutionLog key="new" logs={logs} />);
  await act(async () => resolve());
  expect(screen.getByLabelText('搜索日志').value).toBe('');
  expect(screen.getByRole('checkbox', { name: '自动跟随' }).checked).toBe(true);
  expect(screen.queryByText(/已复制当前结果/)).toBeNull();
});
