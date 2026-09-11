import { invoke, isTauri } from '@tauri-apps/api/core';

export const isTauriDesktop = () => isTauri();

// Keep runtime details and command envelopes out of the business components.
export const desktopBridge = Object.freeze({
  apiRequest: (request) => invoke('api_request', { request }),
  apiCancel: (requestId) => invoke('api_cancel', { requestId }),
  backendStatus: () => invoke('backend_status'),
  read: () => invoke('session_read'),
  rememberLogin: (username) => invoke('session_remember_login', { username }),
  rememberTask: (task) => invoke('session_remember_task', { task }),
  clear: () => invoke('session_clear'),
});

export function getSessionBridge() {
  if (isTauriDesktop()) return desktopBridge;
  return globalThis.window?.chaoxingSession ?? null;
}
