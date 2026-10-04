export const DEFAULT_DOWNLOAD_DIR = 'D:\\Download';
const STORAGE_KEY = 'chaoxing_download_dir';

// 下载目录是本机偏好，保存在 WebView 的 localStorage 里，与账号无关。
const getStorage = () => (typeof window === 'undefined' ? null : window.localStorage);

export function loadDownloadDir(storage = getStorage()) {
  try {
    const saved = storage?.getItem(STORAGE_KEY);
    return typeof saved === 'string' && saved.trim() ? saved.trim() : DEFAULT_DOWNLOAD_DIR;
  } catch {
    return DEFAULT_DOWNLOAD_DIR;
  }
}

export function saveDownloadDir(value, storage = getStorage()) {
  const trimmed = value.trim();
  try {
    storage?.setItem(STORAGE_KEY, trimmed);
  } catch {
    // 存储不可用时仅保留本次会话内的输入。
  }
  return trimmed;
}
