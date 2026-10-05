import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import NumberInput from './NumberInput';

afterEach(cleanup);

function Field({ initial = 1, onCommit = () => {}, ...props }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <label htmlFor="jobs">并发章节数</label>
      <NumberInput id="jobs" min={1} max={10} integer value={value}
        onValueChange={(next) => { setValue(next); onCommit(next); }} {...props} />
    </>
  );
}

it('lets the field be cleared and retyped without forcing a fallback value', () => {
  const commit = vi.fn();
  render(<Field onCommit={commit} />);
  const input = screen.getByLabelText('并发章节数');
  fireEvent.change(input, { target: { value: '' } });
  expect(input.value).toBe('');
  expect(input.getAttribute('aria-invalid')).toBe('true');
  fireEvent.change(input, { target: { value: '3' } });
  expect(input.value).toBe('3');
  expect(commit).toHaveBeenLastCalledWith(3);
  expect(input.getAttribute('aria-invalid')).toBe('false');
});

it('never commits out-of-range drafts and clamps them on blur', () => {
  const commit = vi.fn();
  render(<Field onCommit={commit} />);
  const input = screen.getByLabelText('并发章节数');
  fireEvent.change(input, { target: { value: '13' } });
  expect(commit).not.toHaveBeenCalled();
  expect(screen.getByText('请输入 1–10 之间的整数')).toBeTruthy();
  fireEvent.blur(input);
  expect(input.value).toBe('10');
  expect(commit).toHaveBeenLastCalledWith(10);
});

it('restores the last valid value when left empty', () => {
  const commit = vi.fn();
  render(<Field initial={4} onCommit={commit} />);
  const input = screen.getByLabelText('并发章节数');
  fireEvent.change(input, { target: { value: '' } });
  fireEvent.blur(input);
  expect(input.value).toBe('4');
  expect(commit).not.toHaveBeenCalled();
});

it('shows the hint while the value is valid', () => {
  render(<Field hint="建议不超过 4" />);
  expect(screen.getByText('建议不超过 4')).toBeTruthy();
});

it('reports invalid drafts and clears the flag when the field unmounts', () => {
  const onValidityChange = vi.fn();
  const view = render(<Field onValidityChange={onValidityChange} />);
  expect(onValidityChange).toHaveBeenLastCalledWith(true);
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '13' } });
  expect(onValidityChange).toHaveBeenLastCalledWith(false);
  view.unmount();
  expect(onValidityChange).toHaveBeenLastCalledWith(true);
});
