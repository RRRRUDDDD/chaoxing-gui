import React, { useId, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

// Keep the subtree mounted so number drafts survive disclosure changes.
const SettingsDisclosure = ({ title, icon: Icon, children, summary, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  const trigger = useRef(null);
  const panel = useRef(null);
  const toggle = () => {
    if (open && panel.current?.contains(document.activeElement)) trigger.current?.focus();
    setOpen(!open);
  };
  return (
    <section>
      <button ref={trigger} id={`${id}-trigger`} type="button" onClick={toggle}
        aria-expanded={open} aria-controls={id}
        aria-describedby={summary ? `${id}-summary` : undefined}
        className="flex w-full items-center justify-between gap-2 rounded-lg py-1 text-[13px] font-medium text-body hover:text-brand focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20">
        <span className="flex min-w-0 items-center gap-2">
          {Icon && <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />}
          <span className="truncate">{open ? '收起' : '展开'}{title}</span>
          {summary && (
            <span id={`${id}-summary`} aria-hidden="true"
              className={`ml-1 shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${summary.cls}`}>
              {summary.text}
            </span>
          )}
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      <div ref={panel} id={id} hidden={!open} aria-labelledby={`${id}-trigger`} className="pt-4">
        {children}
      </div>
    </section>
  );
};

export default SettingsDisclosure;
