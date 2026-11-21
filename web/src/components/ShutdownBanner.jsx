import { memo, useEffect, useState } from 'react';
import { Loader2, Power } from 'lucide-react';
import Button from './ui/Button';

export const computeShutdownRemaining = (shutdownAt, delaySeconds) => {
  const delay = Number.isFinite(delaySeconds) ? Math.max(0, Math.ceil(delaySeconds)) : 60;
  if (!Number.isFinite(shutdownAt)) return delay;
  return Math.min(delay, Math.max(0, Math.ceil(shutdownAt - Date.now() / 1000)));
};

function ShutdownCountdownInner({ shutdownAt, delaySeconds, format }) {
  const [remaining, setRemaining] = useState(() => computeShutdownRemaining(shutdownAt, delaySeconds));
  useEffect(() => {
    const timer = setInterval(() => {
      setRemaining(computeShutdownRemaining(shutdownAt, delaySeconds));
    }, 1000);
    return () => clearInterval(timer);
  }, [shutdownAt, delaySeconds]);
  return format(remaining);
}

// 隔离 1 秒倒计时定时器，避免每秒触发整个父页面重渲染。
export function ShutdownCountdown({ shutdownAt, delaySeconds, format }) {
  return (
    <ShutdownCountdownInner
      key={`${shutdownAt ?? ''}:${delaySeconds ?? ''}`}
      shutdownAt={shutdownAt}
      delaySeconds={delaySeconds}
      format={format}
    />
  );
}

const formatBannerCountdown = (remaining) => (
  remaining === 0 ? '正在执行自动关机…' : `系统将在 ${remaining} 秒后自动关机`
);

const formatSidebarCountdown = (remaining) => (
  remaining === 0 ? '正在关机' : `${remaining} 秒后关机`
);

export const ScheduledShutdownBanner = memo(function ScheduledShutdownBanner({
  shutdownInfo,
  cancelling = false,
  cancelError = '',
  disabled = false,
  onCancel,
  description = '刷取任务已结束，如仍需使用电脑请点击右侧按钮取消关机。',
}) {
  return (
    <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/40 bg-warning/10 px-5 py-4 animate-fade-in">
      <div className="flex min-w-0 items-start gap-2.5">
        <Power className="mt-0.5 h-4.5 w-4.5 shrink-0 text-warning-ink" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-semibold text-warning-ink tnum">
            <ShutdownCountdown
              shutdownAt={shutdownInfo?.shutdown_at}
              delaySeconds={shutdownInfo?.delay_seconds}
              format={formatBannerCountdown}
            />
          </p>
          <p className="mt-0.5 text-[13px] text-body">{description}</p>
          {cancelError && <p className="mt-1 text-xs text-danger">{cancelError}</p>}
        </div>
      </div>
      {onCancel && (
        <Button variant="destructive" size="sm" onClick={onCancel} disabled={disabled || cancelling}>
          {cancelling && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          {cancelling ? '正在取消' : '取消关机'}
        </Button>
      )}
    </div>
  );
});

export const ShutdownStatusText = memo(function ShutdownStatusText({
  shutdownScheduled,
  shutdownArmed,
  shutdownCancelled,
  shutdownInfo,
}) {
  if (shutdownScheduled) {
    return (
      <ShutdownCountdown
        shutdownAt={shutdownInfo?.shutdown_at}
        delaySeconds={shutdownInfo?.delay_seconds}
        format={formatSidebarCountdown}
      />
    );
  }
  if (shutdownArmed) return '已开启';
  if (shutdownCancelled) return '已取消';
  return '启动失败';
});
