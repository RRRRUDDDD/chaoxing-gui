import { Github } from 'lucide-react';
import { desktopBridge } from '../lib/desktopBridge';
import ExternalLink from './ExternalLink';

export const REPOSITORY_URL = 'https://github.com/RRRRUDDDD/chaoxing-gui';

export default function RepositoryLink() {
  return (
    <ExternalLink
      href={REPOSITORY_URL}
      openDesktop={desktopBridge.openRepository}
      errorMessage="无法打开浏览器，请复制仓库链接后手动访问"
      aria-label="在 GitHub 上查看项目"
      title="在 GitHub 上查看项目"
      className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-colors duration-150 hover:bg-soft hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
    >
      <Github className="h-4 w-4" aria-hidden="true" />
    </ExternalLink>
  );
}
