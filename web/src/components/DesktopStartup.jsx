import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';
import Button from './ui/Button';

const phases = new Set(['starting', 'ready', 'stopping', 'stopped', 'failed']);
const messages = {
  starting: ['正在准备学习助手', '服务正在启动，请稍候。'],
  checking: ['正在检查服务状态', '请稍候。'],
  stopping: ['服务正在关闭', '请等待服务退出后重新打开应用。'],
  stopped: ['学习服务已停止', '请关闭应用后重新打开，或稍后重新检查。'],
  failed: ['学习服务启动失败', '请关闭应用后重新打开，或稍后重新检查。'],
  unavailable: ['无法读取服务状态', '请稍后重新检查。如果问题持续，请关闭应用后重新打开。'],
};

export default function DesktopStartup({ children, intervalMs = 1000 }) {
  const [runtime] = useState(() => {
    try { return isTauriDesktop() ? 'desktop' : 'web'; }
    catch { return 'unavailable'; }
  });
  const [phase, setPhase] = useState(runtime === 'web' ? 'ready' : runtime === 'unavailable' ? 'unavailable' : 'starting');
  const [checkVersion, setCheckVersion] = useState(0);
  const [hostError, setHostError] = useState('');
  const [notice, setNotice] = useState('');
  const requestRef = useRef(null);

  useEffect(() => {
    if (runtime === 'web' || (runtime === 'unavailable' && checkVersion === 0)) return undefined;
    let disposed = false;
    let timer;
    const poll = async () => {
      try {
        // StrictMode replays effects. Reuse the in-flight read so only the live
        // effect schedules the next poll, always after the preceding one settles.
        if (!requestRef.current) {
          const pending = Promise.resolve().then(() => desktopBridge.backendStatus()).finally(() => {
            if (requestRef.current === pending) requestRef.current = null;
          });
          requestRef.current = pending;
        }
        const status = await requestRef.current;
        if (disposed) return;
        if (!status || !phases.has(status.phase)) throw new Error('Invalid service status');
        setPhase(status.phase);
        setHostError(typeof status.error === 'string' ? status.error : '');
        setNotice(typeof status.notice === 'string' ? status.notice : '');
        if (['starting', 'ready', 'stopping'].includes(status.phase)) timer = setTimeout(poll, intervalMs);
      } catch {
        if (!disposed) setPhase('unavailable');
      }
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [runtime, intervalMs, checkVersion]);

  if (phase === 'ready') return <>
    {notice && <div role="status" className="border-b border-warning/30 bg-warning/5 px-6 py-3 text-sm text-body">{notice}</div>}
    {children}
  </>;
  const failed = ['failed', 'stopped', 'unavailable'].includes(phase);
  const [title, description] = messages[phase];
  const recheck = () => { setPhase('checking'); setCheckVersion((version) => version + 1); };

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-12">
      <div className="w-full max-w-md text-center">
        <img src="/fav.jpg" alt="超星学习通" className="mx-auto mb-4 h-12 w-12 rounded-xl object-cover shadow-focus" />
        <h1 className="mb-8 text-xl font-semibold tracking-tight">超星学习通 · 自动化学习助手</h1>
        <section role={failed ? 'alert' : 'status'} aria-live={failed ? 'assertive' : 'polite'} className="rounded-2xl border border-line bg-white p-8 shadow-lift">
          {failed ? <AlertCircle className="mx-auto mb-4 h-8 w-8 text-danger" aria-hidden="true" />
            : <Loader2 className="mx-auto mb-4 h-8 w-8 animate-spin text-brand" aria-hidden="true" />}
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-2 text-sm leading-relaxed text-body">{description}</p>
          {failed && hostError && <p className="mt-3 break-words rounded-lg bg-danger/5 px-3 py-2 text-left text-sm leading-relaxed text-danger">{hostError}</p>}
          {failed && <Button type="button" onClick={recheck} className="mt-6"><RefreshCw className="h-4 w-4" aria-hidden="true" />重新检查</Button>}
        </section>
      </div>
    </main>
  );
}
