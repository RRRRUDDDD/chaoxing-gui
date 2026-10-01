import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

export const isTauriDesktop = () => isTauri();

// Keep runtime details and command envelopes out of the business components.
export const desktopBridge = Object.freeze({
  apiRequest: (request) => invoke('api_request', { request }),
  apiCancel: (requestId) => invoke('api_cancel', { requestId }),
  backendStatus: () => invoke('backend_status'),
  openRepository: () => invoke('open_repository'),
  openOcsDocs: () => invoke('open_ocs_docs'),
  read: () => invoke('session_read'),
  rememberLogin: (username) => invoke('session_remember_login', { username }),
  rememberTask: (task) => invoke('session_remember_task', { task }),
  clear: () => invoke('session_clear'),
  // Resolves to an unlisten function.
  onCloseRequested: (handler) => listen('close-requested', (event) => handler(event.payload)),
  closePromptShown: (promptId) => invoke('close_prompt_shown', { promptId }),
  closeChoice: (action, remember) => invoke('close_choice', { action, remember }),
  readPreferences: () => invoke('preferences_read'),
  writePreferences: (closeAction) => invoke('preferences_write', { closeAction }),
});

export function getSessionBridge() {
  if (isTauriDesktop()) return desktopBridge;
  return null;
}
