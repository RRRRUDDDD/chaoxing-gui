import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const bridge = vi.hoisted(() => ({
  handler: null,
  unlisten: vi.fn(),
  onCloseRequested: vi.fn(),
  closePromptShown: vi.fn(),
  closeChoice: vi.fn(),
}));
vi.mock('../lib/desktopBridge', () => ({
  desktopBridge: bridge,
  isTauriDesktop: () => true,
}));
import CloseChoice, { CloseChoiceDialog, useReportTaskRunning } from './CloseChoiceDialog';

beforeEach(() => {
  bridge.handler = null;
  bridge.unlisten.mockReset();
  bridge.onCloseRequested.mockReset().mockImplementation(async (handler) => {
    bridge.handler = handler;
    return bridge.unlisten;
  });
  bridge.closePromptShown.mockReset().mockResolvedValue(true);
  bridge.closeChoice.mockReset().mockResolvedValue(undefined);
});
afterEach(cleanup);

async function prompt(id = 7) {
  await waitFor(() => expect(bridge.handler).toBeTypeOf('function'));
  await act(async () => { await bridge.handler(id); });
}

it('acknowledges the prompt and sends each choice with the remember flag', async () => {
  for (const [label, action] of [['最小化', 'minimize'], ['最小化到托盘', 'tray'], ['退出程序', 'exit']]) {
    render(<CloseChoiceDialog />);
    await prompt(3);
    expect(bridge.closePromptShown).toHaveBeenLastCalledWith(3);
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '最小化' }));
    if (action === 'tray') fireEvent.click(screen.getByLabelText('记住我的选择'));
    fireEvent.click(screen.getByRole('button', { name: label }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(bridge.closeChoice).toHaveBeenLastCalledWith(action, action === 'tray');
    cleanup();
  }
});

it('escape and the backdrop cancel without remembering', async () => {
  render(<CloseChoiceDialog />);
  await prompt();
  fireEvent.click(screen.getByLabelText('记住我的选择'));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(bridge.closeChoice).toHaveBeenLastCalledWith('cancel', false);

  await prompt(8);
  const backdrop = screen.getByRole('dialog').parentElement;
  fireEvent.mouseDown(backdrop);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(bridge.closeChoice).toHaveBeenCalledTimes(2);
});

it('does not show an expired prompt and keeps focus inside', async () => {
  bridge.closePromptShown.mockResolvedValueOnce(false);
  render(<CloseChoiceDialog />);
  await prompt(1);
  expect(screen.queryByRole('dialog')).toBeNull();

  await prompt(2);
  const buttons = screen.getAllByRole('button');
  const cancel = screen.getByRole('button', { name: '取消关闭窗口' });
  cancel.focus();
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab' });
  expect(document.activeElement).toBe(buttons[0]);
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(cancel);
});

it('warns next to exit only while a task runs and shows a failed save', async () => {
  function Reporter({ running }) {
    useReportTaskRunning(running);
    return null;
  }
  const view = render(<CloseChoice><Reporter running={false} /></CloseChoice>);
  await prompt();
  expect(screen.queryByText(/正在运行的任务会中断/)).toBeNull();
  view.rerender(<CloseChoice><Reporter running /></CloseChoice>);
  expect(screen.getByText('正在运行的任务会中断，下次启动可恢复。')).toBeTruthy();

  bridge.closeChoice.mockRejectedValueOnce('无法保存关闭方式，请重试');
  fireEvent.click(screen.getByLabelText('记住我的选择'));
  fireEvent.click(screen.getByRole('button', { name: '最小化' }));
  expect((await screen.findByRole('alert')).textContent).toBe('无法保存关闭方式，请重试');
  expect(screen.getByRole('dialog')).toBeTruthy();
});

it('stops listening when unmounted', async () => {
  const view = render(<CloseChoiceDialog />);
  await waitFor(() => expect(bridge.handler).toBeTypeOf('function'));
  view.unmount();
  expect(bridge.unlisten).toHaveBeenCalledTimes(1);
});
