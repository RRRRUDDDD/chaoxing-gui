import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Minimize2, PanelBottomClose, Power } from 'lucide-react';
import Button from './ui/Button';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';

const CHOICES = [
  { action: 'minimize', label: '最小化', icon: Minimize2 },
  { action: 'tray', label: '最小化到托盘', icon: PanelBottomClose },
  { action: 'exit', label: '退出程序', icon: Power },
];

export function CloseChoiceDialog() {
  const [promptId, setPromptId] = useState(null);
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialogRef = useRef(null);
  const titleId = useId();

  useEffect(() => {
    let disposed = false;
    let unlisten = null;
    desktopBridge.onCloseRequested(async (id) => {
      try {
        // The host exits by itself unless the prompt is acknowledged in time.
        if (!disposed && await desktopBridge.closePromptShown(id) && !disposed) {
          setRemember(false);
          setError('');
          setPromptId(id);
        }
      } catch { /* The host falls back to exiting. */ }
    }).then((stop) => {
      if (disposed) stop();
      else unlisten = stop;
    }).catch(() => {});
    return () => { disposed = true; unlisten?.(); };
  }, []);

  useEffect(() => {
    if (promptId === null) return;
    dialogRef.current?.querySelector('button')?.focus();
  }, [promptId]);

  const choose = useCallback(async (action) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await desktopBridge.closeChoice(action, action === 'cancel' ? false : remember);
      setPromptId(null);
    } catch (reason) {
      setError(typeof reason === 'string' ? reason : '操作失败，请重试');
      if (action === 'cancel') setPromptId(null);
    } finally {
      setBusy(false);
    }
  }, [busy, remember]);

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      void choose('cancel');
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...dialogRef.current.querySelectorAll('button, input')].filter((node) => !node.disabled);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (promptId === null) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4" onMouseDown={(event) => {
      if (event.target === event.currentTarget) void choose('cancel');
    }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKeyDown}
        className="w-full max-w-sm rounded-2xl border border-line bg-white p-6 shadow-lift">
        <h2 id={titleId} className="text-base font-semibold">关闭窗口</h2>
        <p className="mt-1 text-sm text-body">选择关闭窗口后的操作。</p>
        <div className="mt-5 space-y-2">
          {CHOICES.map(({ action, label, icon: Icon }) => (
            <div key={action}>
              <Button type="button" variant={action === 'minimize' ? 'default' : 'outline'} disabled={busy}
                className="w-full justify-start" onClick={() => void choose(action)}>
                <Icon className="h-4 w-4" aria-hidden="true" />{label}
              </Button>
            </div>
          ))}
        </div>
        <label className="mt-4 flex items-center gap-2 text-[13px] text-body">
          <input type="checkbox" className="h-4 w-4 accent-brand" checked={remember} disabled={busy}
            onChange={(event) => setRemember(event.target.checked)} />
          记住我的选择
        </label>
        {error && <p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
        <div className="mt-4 border-t border-line pt-3">
          <Button type="button" variant="ghost" disabled={busy} className="w-full"
            onClick={() => void choose('cancel')} aria-label="取消关闭窗口">
            取消，继续使用
          </Button>
        </div>
      </div>
    </div>
  );
}

// Mounted outside the startup screen so a close during startup is still asked.
export default function CloseChoice({ children }) {
  return (
    <>
      {children}
      {isTauriDesktop() && <CloseChoiceDialog />}
    </>
  );
}
