import { useEffect, useRef } from 'react';
import { cn } from '../lib/utils';

/**
 * 数字滚动:值变化时从旧值滚到新值,ease-out-expo。
 * 仅 textContent 变更;tnum 保证对齐。
 */
const CountUp = ({ value = 0, duration = 800, className, suffix = '' }) => {
  const ref = useRef(null);
  const prevRef = useRef(0);
  const rafRef = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = prevRef.current;
    const to = Number(value) || 0;
    prevRef.current = to;

    if (from === to) {
      el.textContent = `${to}${suffix}`;
      return;
    }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      el.textContent = `${to}${suffix}`;
      return;
    }

    const start = performance.now();
    const easeOutExpo = (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

    const tick = (now) => {
      const t = Math.min((now - start) / duration, 1);
      const current = Math.round(from + (to - from) * easeOutExpo(t));
      el.textContent = `${current}${suffix}`;
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value, duration, suffix]);

  return (
    <span ref={ref} className={cn('tnum', className)}>
      {value}
      {suffix}
    </span>
  );
};

export default CountUp;
