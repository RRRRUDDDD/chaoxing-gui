import React, { useState } from 'react';
import { Github } from 'lucide-react';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';

export const REPOSITORY_URL = 'https://github.com/RRRRUDDDD/chaoxing-gui';

export default function RepositoryLink() {
  const [error, setError] = useState('');
  const open = async (event) => {
    if (!isTauriDesktop() || (event.type === 'auxclick' && event.button !== 1)) return;
    event.preventDefault();
    setError('');
    try { await desktopBridge.openRepository(); }
    catch { setError('无法打开浏览器，请复制仓库链接后手动访问'); }
  };

  return (
    <>
      <a
        href={REPOSITORY_URL}
        target="_blank"
        rel="noopener noreferrer"
        onClick={open}
        onAuxClick={open}
        aria-label="在 GitHub 上查看项目"
        title="在 GitHub 上查看项目"
        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-colors duration-150 hover:bg-soft hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
      >
        <Github className="h-4 w-4" aria-hidden="true" />
      </a>
      {error && <span role="alert" className="max-w-48 text-xs text-danger">{error}</span>}
    </>
  );
}
