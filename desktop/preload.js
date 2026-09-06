const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('chaoxingSession', Object.freeze({
  read: () => ipcRenderer.invoke('session:read'),
  rememberLogin: (username) => ipcRenderer.invoke('session:remember-login', username),
  rememberTask: (task) => ipcRenderer.invoke('session:remember-task', task),
  clear: () => ipcRenderer.invoke('session:clear'),
}));
