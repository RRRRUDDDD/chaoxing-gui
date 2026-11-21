import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ErrorBoundary from './ErrorBoundary';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('catches unexpected render errors and recovers after reset', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let shouldThrow = true;
  function FlakyView() {
    if (shouldThrow) throw new Error('渲染崩溃样例');
    return <div>恢复正常</div>;
  }

  render(
    <ErrorBoundary>
      <FlakyView />
    </ErrorBoundary>,
  );

  const alert = screen.getByRole('alert');
  expect(alert.textContent).toContain('界面遇到意外错误');
  expect(alert.textContent).toContain('渲染崩溃样例');

  shouldThrow = false;
  fireEvent.click(screen.getByRole('button', { name: '尝试继续' }));
  expect(screen.getByText('恢复正常')).toBeTruthy();
});
