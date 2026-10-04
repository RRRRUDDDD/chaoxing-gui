import React from 'react';
import { cn } from '../../lib/utils';

/** 输入框:浅灰描边、聚焦品牌蓝 */
const Input = React.forwardRef(({ className, type = 'text', ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        'flex h-10 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink',
        'placeholder:text-faint',
        'transition-shadow duration-150',
        'hover:border-faint/50',
        'focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15',
        'disabled:cursor-not-allowed disabled:bg-soft disabled:opacity-60',
        className
      )}
      ref={ref}
      {...props}
    />
  );
});

Input.displayName = 'Input';

export default Input;
