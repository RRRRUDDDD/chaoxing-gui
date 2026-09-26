import React, { useEffect, useRef, useState } from 'react';
import Input from './Input';
import { cn } from '../../lib/utils';

const parse = (raw) => (raw.trim() === '' ? NaN : Number(raw));

/**
 * 数字输入:编辑中保留原始文本,只把范围内的值提交给上层;
 * 失焦时把越界值收回到范围内,空值或非数字恢复为上次的有效值。
 */
const NumberInput = ({ id, value, onValueChange, onValidityChange, min, max, step, integer = false, hint = '', ...props }) => {
  const [text, setText] = useState(String(value));

  // Keep the draft while it still represents the committed value (e.g. "0.50").
  useEffect(() => {
    setText((current) => (parse(current) === value ? current : String(value)));
  }, [value]);

  const inRange = (n) => Number.isFinite(n) && (!integer || Number.isInteger(n))
    && (min === undefined || n >= min) && (max === undefined || n <= max);
  const bounds = min !== undefined && max !== undefined ? ` ${min}–${max} 之间` : min !== undefined ? `不小于 ${min} ` : `不大于 ${max} `;
  const error = inRange(parse(text)) ? '' : `请输入${bounds}的${integer ? '整数' : '数值'}`;
  const hintId = `${id}-hint`;

  const onValidityChangeRef = useRef(onValidityChange);
  onValidityChangeRef.current = onValidityChange;
  useEffect(() => {
    onValidityChangeRef.current?.(!error);
    return () => onValidityChangeRef.current?.(true);
  }, [error]);


  const handleChange = (event) => {
    const raw = event.target.value;
    setText(raw);
    const n = parse(raw);
    if (inRange(n)) onValueChange(n);
  };

  const handleBlur = () => {
    if (!error) return;
    let n = parse(text);
    if (!Number.isFinite(n)) {
      setText(String(value));
      return;
    }
    if (integer) n = Math.round(n);
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    setText(String(n));
    onValueChange(n);
  };

  return (
    <>
      <Input
        id={id}
        type="number"
        inputMode={integer ? 'numeric' : 'decimal'}
        min={min}
        max={max}
        step={step}
        value={text}
        onChange={handleChange}
        onBlur={handleBlur}
        aria-invalid={!!error}
        aria-describedby={error || hint ? hintId : undefined}
        className={cn(
          '[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none',
          error && 'border-danger focus:border-danger focus:ring-danger/15',
        )}
        {...props}
      />
      {(error || hint) && (
        <p id={hintId} className={`text-xs ${error ? 'text-danger' : 'text-faint'}`}>
          {error || hint}
        </p>
      )}
    </>
  );
};

export default NumberInput;
