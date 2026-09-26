import React from 'react';
import { cn } from '../../lib/utils';

/** 下拉选择:与 Input 同高同描边 */
const Select = React.forwardRef(({ className, ...props }, ref) => (
  <select
    ref={ref}
    className={cn(
      'h-10 w-full cursor-pointer rounded-lg border border-line bg-white px-3 text-sm text-ink',
      'transition-shadow duration-150 hover:border-faint/50',
      'focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15',
      'disabled:cursor-not-allowed disabled:bg-soft disabled:opacity-60',
      className
    )}
    {...props}
  />
));
Select.displayName = 'Select';

export default Select;
