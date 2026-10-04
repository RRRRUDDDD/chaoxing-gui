import React from 'react';
import { cn } from '../../lib/utils';

/** 标准按钮:品牌蓝主按钮、浅灰次按钮,悬停轻微提亮 */
const Button = React.forwardRef(({ className, variant = 'default', size = 'default', children, ...props }, ref) => {
  const variantStyles = {
    default: 'bg-brand text-white hover:bg-brand-dark shadow-sm',
    destructive: 'bg-danger text-white hover:bg-danger/90 shadow-sm',
    outline: 'border border-line bg-white text-ink hover:bg-soft',
    ghost: 'text-body hover:bg-soft hover:text-ink',
  };

  const sizeStyles = {
    default: 'h-10 px-4',
    sm: 'h-8 px-3 text-[13px]',
    lg: 'h-12 px-6 text-[15px]',
    icon: 'h-10 w-10',
  };

  return (
    <button
      className={cn(
        'inline-flex select-none items-center justify-center gap-2 rounded-lg text-sm font-medium',
        'transition-colors duration-150 ease-out',
        'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20',
        'disabled:pointer-events-none disabled:opacity-45',
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
      ref={ref}
      {...props}
    >
      {children}
    </button>
  );
});

Button.displayName = 'Button';

export default Button;
