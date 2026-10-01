import React, { useState } from 'react';
import { isTauriDesktop } from '../lib/desktopBridge';

export default function ExternalLink({ href, openDesktop, children, errorMessage = '无法打开浏览器，请复制链接后手动访问', ...props }) {
  const [error, setError] = useState('');
  const open = async (event) => {
    if (!isTauriDesktop() || (event.type === 'auxclick' && event.button !== 1)) return;
    event.preventDefault();
    setError('');
    try { await openDesktop(); }
    catch { setError(errorMessage); }
  };

  return (
    <>
      <a {...props} href={href} target="_blank" rel="noopener noreferrer" onClick={open} onAuxClick={open}>
        {children}
      </a>
      {error && <span role="alert" className="max-w-48 text-xs text-danger">{error}</span>}
    </>
  );
}
