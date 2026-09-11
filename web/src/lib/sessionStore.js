import { desktopBridge, getSessionBridge } from './desktopBridge';

export const SAVED_LOGIN_KEY = 'chaoxing_saved_login';
export const SESSION_KEY = 'chaoxing_session_v1';
const emptySession = () => ({ version: 1, login: null, activeTask: null });
const usernameValue = (value) => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 128 && !/[\u0000-\u001f\u007f]/.test(value) ? value.trim() : null;
export const validTaskId = (value) => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const validTask = (value) => exactKeys(value, ['username', 'taskId']) && usernameValue(value.username) !== null
  && usernameValue(value.username) === value.username && validTaskId(value.taskId);

function desktopSession(value) {
  if (!exactKeys(value, ['version', 'login', 'activeTask']) || value.version !== 1
    || !(value.login === null || (exactKeys(value.login, ['username', 'use_cookies'])
      && usernameValue(value.login.username) !== null && usernameValue(value.login.username) === value.login.username && value.login.use_cookies === true))
    || !(value.activeTask === null || (validTask(value.activeTask) && value.activeTask.username === value.login?.username))) {
    throw new Error('保存的账号格式错误');
  }
  return sanitizeSession(value);
}

function sanitizeSession(value) {
  const session = emptySession();
  const username = usernameValue(value?.login?.username);
  if (username) session.login = { username, use_cookies: true };
  if (username && value?.activeTask?.username === username && validTaskId(value.activeTask.taskId)) {
    session.activeTask = { username, taskId: value.activeTask.taskId };
  }
  return session;
}

function parseStored(raw) {
  try { return raw && raw.length <= 16384 ? JSON.parse(raw) : null; } catch { return null; }
}

export function createSessionStore({
  getBridge = getSessionBridge,
  getStorage = () => window.localStorage,
} = {}) {
  let queue = Promise.resolve();
  const serialize = (operation) => {
    const next = queue.then(operation);
    queue = next.catch(() => {});
    return next;
  };
  const storage = () => { try { return getStorage(); } catch { return null; } };

  const read = async () => {
    const bridge = getBridge();
    let session = bridge ? desktopSession(await bridge.read()) : null;
    // Tauri imports legacy data in the host before readiness. Its random page
    // origin must never become a second persistence source or an IO fallback.
    if (bridge === desktopBridge) return session;
    const local = storage();
    let legacy;
    let browserState;
    if (local) {
      // Remove legacy plaintext credentials before migration; only the account is used.
      const old = local.getItem(SAVED_LOGIN_KEY);
      local.removeItem(SAVED_LOGIN_KEY);
      legacy = usernameValue(parseStored(old)?.username);
      browserState = sanitizeSession(parseStored(local.getItem(SESSION_KEY)));
    }
    session ||= browserState || emptySession();
    const migratedUsername = browserState?.login?.username || legacy;
    if (!session.login && migratedUsername) {
      session.login = { username: migratedUsername, use_cookies: true };
      if (bridge) {
        session = desktopSession(await bridge.rememberLogin(migratedUsername));
      } else if (local) {
        local.setItem(SESSION_KEY, JSON.stringify(session));
      }
    }
    if (bridge && local) local.removeItem(SESSION_KEY);
    return session;
  };

  return {
    read: () => serialize(read),
    rememberLogin: (value) => serialize(async () => {
      const username = usernameValue(value);
      if (!username) throw new Error('账号格式错误');
      const bridge = getBridge();
      if (bridge) return desktopSession(await bridge.rememberLogin(username));
      const previous = await read();
      const session = { ...emptySession(), login: { username, use_cookies: true }, activeTask: previous.login?.username === username ? previous.activeTask : null };
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    rememberTask: (task) => serialize(async () => {
      const bridge = getBridge();
      if (task !== null && !validTask(task)) throw new Error('任务信息格式错误');
      if (bridge) return desktopSession(await bridge.rememberTask(task));
      const session = await read();
      if (task && session.login?.username !== task.username) throw new Error('任务账号不匹配');
      session.activeTask = task ? { username: task.username, taskId: task.taskId } : null;
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    clear: () => serialize(async () => {
      const bridge = getBridge();
      const session = bridge ? desktopSession(await bridge.clear()) : emptySession();
      if (bridge === desktopBridge) return session;
      const local = storage();
      local?.removeItem(SAVED_LOGIN_KEY);
      local?.removeItem(SESSION_KEY);
      return session;
    }),
  };
}

export const sessionStore = createSessionStore();
