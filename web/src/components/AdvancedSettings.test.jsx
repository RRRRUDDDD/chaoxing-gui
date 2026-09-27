import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AdvancedSettings from './AdvancedSettings';

afterEach(cleanup);

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
