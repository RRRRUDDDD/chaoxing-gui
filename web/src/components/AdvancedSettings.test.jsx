import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const bridge = vi.hoisted(() => ({ readPreferences: vi.fn(), writePreferences: vi.fn(), tauri: false }));
vi.mock('../lib/desktopBridge', () => ({
  desktopBridge: bridge,
  isTauriDesktop: () => bridge.tauri,
}));
import AdvancedSettings, { CloseActionSetting } from './AdvancedSettings';

afterEach(() => { cleanup(); bridge.tauri = false; });

function Harness({ initial, onSettings = () => {} }) {
  const [settings, setSettings] = useState(initial);
  return (
    <AdvancedSettings
      settings={settings}
      onChange={(next) => { setSettings(next); onSettings(next); }}
    />
  );
}

it('checks question-bank certificates unless the user turns it off', () => {
  const changed = vi.fn();
  render(<Harness initial={{ tiku_config: { config: '[]', submit: 'true' } }} onSettings={changed} />);
  fireEvent.click(screen.getByRole('button', { name: '展开高级配置' }));
  fireEvent.click(screen.getByRole('button', { name: '展开题库配置' }));
  const box = screen.getByLabelText('校验题库 HTTPS 证书');
  expect(box.checked).toBe(true);
  expect(screen.queryByRole('alert')).toBeNull();

  fireEvent.click(box);
  expect(box.checked).toBe(false);
  expect(changed).toHaveBeenLastCalledWith({ tiku_config: { config: '[]', submit: 'true', verify_ssl: false } });
  expect(screen.getByRole('alert').textContent).toContain('已关闭证书校验');

  fireEvent.click(box);
  expect(changed.mock.calls.at(-1)[0].tiku_config.verify_ssl).toBe(true);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('keeps an old saved opt-out when another bank field changes', () => {
  const changed = vi.fn();
  render(<Harness initial={{ tiku_config: { verify_ssl: false } }} onSettings={changed} />);
  fireEvent.change(screen.getByLabelText('自动提交答题'), { target: { value: 'true' } });
  expect(changed.mock.calls.at(-1)[0].tiku_config).toEqual({ verify_ssl: false, submit: 'true' });
});

it('shows the close-window setting only on the desktop', () => {
  bridge.readPreferences.mockReset().mockReturnValue(new Promise(() => {}));
  render(<Harness initial={{}} />);
  expect(screen.queryByLabelText('关闭窗口时')).toBeNull();
  cleanup();
  bridge.tauri = true;
  render(<Harness initial={{}} />);
  expect(screen.getByLabelText('关闭窗口时')).toBeTruthy();
});

it('reads, saves and restores the close action on failure', async () => {
  bridge.readPreferences.mockReset().mockResolvedValue({ closeAction: 'ask' });
  bridge.writePreferences.mockReset().mockResolvedValueOnce({ closeAction: 'tray' })
    .mockRejectedValueOnce('io');
  render(<CloseActionSetting />);
  const select = screen.getByLabelText('关闭窗口时');
  await waitFor(() => expect(select.value).toBe('ask'));
  fireEvent.change(select, { target: { value: 'tray' } });
  await waitFor(() => expect(select.value).toBe('tray'));
  expect(bridge.writePreferences).toHaveBeenLastCalledWith('tray');
  fireEvent.change(select, { target: { value: 'exit' } });
  expect((await screen.findByRole('alert')).textContent).toContain('无法保存');
  expect(select.value).toBe('tray');
});
