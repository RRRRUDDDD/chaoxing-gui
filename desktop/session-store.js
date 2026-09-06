const fs = require('node:fs');
const path = require('node:path');

const emptySession = () => ({ version: 1, login: null, activeTask: null });
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const validUsername = (value) => typeof value === 'string' && value.length > 0 && value.length <= 128 && value === value.trim() && !/[\u0000-\u001f\u007f]/.test(value);
const validTask = (value) => exactKeys(value, ['username', 'taskId']) && validUsername(value.username) && typeof value.taskId === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value.taskId);

function validSession(value) {
  return exactKeys(value, ['version', 'login', 'activeTask']) && value.version === 1 &&
    (value.login === null || (exactKeys(value.login, ['username', 'use_cookies']) && validUsername(value.login.username) && value.login.use_cookies === true)) &&
    (value.activeTask === null || (validTask(value.activeTask) && value.activeTask.username === value.login?.username));
}

class SessionStore {
  constructor(directory) {
    this.directory = directory;
    this.filename = path.join(directory, 'renderer-session.json');
  }

  read() {
    try {
      if (fs.statSync(this.filename).size > 4096) return emptySession();
      const value = JSON.parse(fs.readFileSync(this.filename, 'utf8'));
      return validSession(value) ? value : emptySession();
    } catch (error) {
      if (error.code === 'ENOENT' || error instanceof SyntaxError) return emptySession();
      throw new Error('无法读取保存的账号');
    }
  }

  write(value) {
    if (!validSession(value)) throw new Error('保存的账号格式错误');
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    const temporary = `${this.filename}.tmp`;
    try {
      fs.writeFileSync(temporary, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 });
      fs.renameSync(temporary, this.filename);
    } catch {
      try { fs.unlinkSync(temporary); } catch {}
      throw new Error('无法保存账号，请重试');
    }
    return value;
  }

  rememberLogin(username) {
    if (!validUsername(username)) throw new Error('账号格式错误');
    const previous = this.read();
    return this.write({ version: 1, login: { username, use_cookies: true }, activeTask: previous.login?.username === username ? previous.activeTask : null });
  }

  rememberTask(task) {
    if (task !== null && !validTask(task)) throw new Error('任务信息格式错误');
    const previous = this.read();
    if (task && previous.login?.username !== task.username) throw new Error('任务账号不匹配');
    return this.write({ ...previous, activeTask: task });
  }

  clear() {
    for (const filename of [this.filename, `${this.filename}.tmp`]) {
      try { fs.unlinkSync(filename); } catch (error) {
        if (error.code !== 'ENOENT') throw new Error('无法清除保存的账号，请重试');
      }
    }
    return emptySession();
  }
}

function isTrustedSender(event, window, expectedOrigin) {
  try {
    return !!expectedOrigin && !!window && !window.isDestroyed() &&
      event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame &&
      new URL(event.senderFrame.url).origin === expectedOrigin;
  } catch { return false; }
}

function registerSessionIpc(ipcMain, { getWindow, getOrigin, store }) {
  const methods = {
    'session:read': { count: 0, call: () => store.read() },
    'session:remember-login': { count: 1, call: (username) => store.rememberLogin(username) },
    'session:remember-task': { count: 1, call: (task) => store.rememberTask(task) },
    'session:clear': { count: 0, call: () => store.clear() },
  };
  for (const [channel, method] of Object.entries(methods)) {
    ipcMain.handle(channel, (event, ...args) => {
      if (!isTrustedSender(event, getWindow(), getOrigin())) throw new Error('禁止访问保存的账号');
      if (args.length !== method.count) throw new Error('请求参数格式错误');
      return method.call(...args);
    });
  }
}

module.exports = { SessionStore, isTrustedSender, registerSessionIpc };
