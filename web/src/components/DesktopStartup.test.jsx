import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DesktopStartup from './DesktopStartup';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';
import { recheckLabel, updateLabels } from '../lib/uiText';

vi.mock('../lib/desktopBridge', () => ({ isTauriDesktop: vi.fn(), desktopBridge: { backendStatus: vi.fn(), checkUpdate: vi.fn() } }));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = (time = 0) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
const show = (strict = false, extra = {}) => {
  const content = <DesktopStartup intervalMs={100} readyIntervalMs={100} {...extra}><div>业务界面</div></DesktopStartup>;
  return render(strict ? <React.StrictMode>{content}</React.StrictMode> : content);
};

beforeEach(() => { vi.useFakeTimers(); isTauriDesktop.mockReset().mockReturnValue(true); desktopBridge.backendStatus.mockReset(); desktopBridge.checkUpdate.mockReset().mockResolvedValue(null); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('desktop startup', () => {
  it('renders the browser app immediately without querying desktop status', async () => {
    isTauriDesktop.mockReturnValue(false);
    show();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(1000);
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it('mounts the app only after readiness and continues watching for service death', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'starting' }).mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValue({ phase: 'failed', error: 'exit code fixture' });
    show();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('正在');
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('服务');
    expect(screen.getByRole('alert').textContent).toContain('exit code fixture');
    expect(screen.getByRole('button', { name: recheckLabel })).toBeTruthy();
  });

  it('slows the readiness watch down once the service is ready', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready' });
    show(false, { readyIntervalMs: 1000 });
    await tick();
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    await tick(100);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    await tick(900);
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
  });

  it('serializes slow status queries, including StrictMode effect replay', async () => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValueOnce(pending.promise).mockResolvedValue({ phase: 'ready' });
    show(true);
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(screen.queryByText('业务界面')).toBeNull();
    await act(async () => pending.resolve({ phase: 'starting' }));
    await tick(99);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    await tick(1);
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each(['failed', 'stopped'])('shows an actionable Chinese %s state and only rereads on recheck', async (phase) => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase, error: 'raw runtime diagnostics' }).mockResolvedValue({ phase: 'ready' });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(phase === 'failed' ? '失败' : '停止');
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: recheckLabel }));
    await tick();
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each([new Error('invoke error'), { phase: 'unknown' }, undefined])('retries on a transient status read failure instead of tearing down the app (%s)', async (value) => {
    if (value instanceof Error) desktopBridge.backendStatus.mockRejectedValue(value);
    else desktopBridge.backendStatus.mockResolvedValue(value);
    show();
    await tick();
    // First failure: a retry is scheduled, not the unavailable state.
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.queryByRole('button', { name: recheckLabel })).toBeNull();
    expect(vi.getTimerCount()).toBe(1);
  });

  it.each([new Error('invoke error'), { phase: 'unknown' }, undefined])('goes unavailable only after two consecutive failures (%s)', async (value) => {
    if (value instanceof Error) desktopBridge.backendStatus.mockRejectedValue(value);
    else desktopBridge.backendStatus.mockResolvedValue(value);
    show();
    await tick();
    expect(vi.getTimerCount()).toBe(1);
    await tick(100);
    expect(screen.getByRole('alert').textContent).toContain('无法读取服务状态');
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('button', { name: recheckLabel })).toBeTruthy();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('never opens the app when runtime initialization throws', async () => {
    isTauriDesktop.mockImplementation(() => { throw new Error('initialization'); });
    show();
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('ignores a late status %s after unmount and cancels timers', async (completion) => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValue(pending.promise);
    const view = show();
    await tick();
    view.unmount();
    await act(async () => pending[completion](completion === 'resolve' ? { phase: 'ready' } : new Error('late')));
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.queryByText('业务界面')).toBeNull();
  });

  it('unmounts the app while the service stops, then reports the stopped state', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValueOnce({ phase: 'stopping' }).mockResolvedValue({ phase: 'stopped' });
    const view = show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByRole('alert').textContent).toContain('停止');
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('shows a host failure reason as text, including a host failure instruction', async () => {
    const error = '请先关闭旧版桌面应用，再重新打开。<img src=x onerror=alert(1)>';
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'failed', error });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(error);
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it('keeps an import warning visible after the business app becomes ready', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready', notice: '旧账号记录无法导入，请重新登录。' });
    show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('旧账号记录无法导入');
  });

  it('checks for updates once the backend is ready', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready' });
    show();
    await tick();
    await tick(10);
    expect(desktopBridge.checkUpdate).toHaveBeenCalledOnce();
    expect(desktopBridge.checkUpdate).toHaveResolvedWith(null);
  });

  it('does not check for updates in the browser runtime', async () => {
    isTauriDesktop.mockReturnValue(false);
    show();
    await tick(1000);
    expect(desktopBridge.checkUpdate).not.toHaveBeenCalled();
  });

  it('shows an update banner when a newer version is available', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready' });
    desktopBridge.checkUpdate.mockResolvedValue({
      version: '1.4.0',
      notes: '## 更新内容',
      downloadUrl: 'https://github.com/RRRRUDDDD/chaoxing-gui/releases/download/v1.4.0/chaoxing-gui-setup-1.4.0-windows-x64.exe',
      size: 44977885,
    });
    show();
    await tick();
    await tick(10);
    expect(screen.getByText((content, element) => element.textContent === `${updateLabels.title} 1.4.0`)).toBeTruthy();
    expect(screen.getByRole('button', { name: updateLabels.action })).toBeTruthy();
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it('hides the update banner when the current version is up-to-date', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready' });
    desktopBridge.checkUpdate.mockResolvedValue(null);
    show();
    await tick();
    await tick(10);
    expect(screen.queryByText(updateLabels.title)).toBeNull();
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it('shows a checking state while the update check is in flight', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready' });
    const pending = deferred();
    desktopBridge.checkUpdate.mockReturnValue(pending.promise);
    show();
    await tick();
    expect(screen.getByText(updateLabels.checking)).toBeTruthy();
    await act(async () => pending.resolve(null));
    await tick(10);
    expect(screen.queryByText(updateLabels.checking)).toBeNull();
  });
});
