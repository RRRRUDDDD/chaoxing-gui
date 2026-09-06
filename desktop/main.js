const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, exec } = require('child_process');
const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const { SessionStore, registerSessionIpc } = require('./session-store');

let mainWindow = null;
let backend = null;
let backendPid = 0;
let backendExited = false;
let port = 0;
let quitting = false;

const isDev = !app.isPackaged;

// 数据目录：后端配置、cookies、日志都落在这里
let dataDir;
try {
  dataDir = app.getPath('userData');
} catch (e) {
  dataDir = path.join(os.tmpdir(), 'chaoxing-desktop');
}
const MAIN_LOG = path.join(dataDir, 'main.log');
const BACKEND_LOG = path.join(dataDir, 'backend.log');
registerSessionIpc(ipcMain, {
  getWindow: () => mainWindow,
  getOrigin: () => port ? `http://127.0.0.1:${port}` : null,
  store: new SessionStore(dataDir),
});

/**
 * 主进程日志：启动失败时用于定位问题
 */
function trace(msg) {
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.appendFileSync(MAIN_LOG, `${new Date().toISOString()} ${msg}\n`);
  } catch (e) {}
}

process.on('uncaughtException', (err) => trace('uncaughtException: ' + (err && err.stack ? err.stack : err)));
process.on('unhandledRejection', (err) => trace('unhandledRejection: ' + (err && err.stack ? err.stack : err)));

/**
 * 获取空闲端口（由系统分配，避开固定端口冲突）
 */
function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const p = server.address().port;
      server.close(() => resolve(p));
    });
    server.on('error', reject);
  });
}

/**
 * 启动后端进程
 */
async function startBackend() {
  port = await getFreePort();
  fs.mkdirSync(dataDir, { recursive: true });

  let cmd, args, cwd;
  if (isDev) {
    // 开发模式：运行仓库根目录的 app.py
    cmd = 'python';
    args = ['app.py'];
    cwd = path.join(__dirname, '..');
  } else {
    // 生产模式：运行 resources/backend 下的 PyInstaller 产物
    cmd = path.join(process.resourcesPath, 'backend', 'chaoxing-backend.exe');
    args = [];
    cwd = dataDir;
    if (!fs.existsSync(cmd)) {
      throw new Error(`后端程序缺失：${cmd}`);
    }
  }

  // 注意：stdio 必须是真实 fd，WriteStream 在 open 事件前 fd 为 null，spawn 会直接抛错
  const logFd = fs.openSync(BACKEND_LOG, 'a');
  fs.writeSync(logFd, `\n===== ${new Date().toISOString()} 启动 (port=${port}) =====\n`);

  try {
    backend = spawn(cmd, args, {
      cwd,
      env: {
        ...process.env,
        CHAOXING_HEADLESS: '1',
        CHAOXING_ELECTRON: '1',
        CHAOXING_PORT: String(port),
        CHAOXING_DATA_DIR: dataDir,
        PYTHONIOENCODING: 'utf-8',
      },
      stdio: ['pipe', logFd, logFd],
      windowsHide: true,
    });
  } finally {
    // 句柄已复制给子进程，父进程这份可以关掉
    try { fs.closeSync(logFd); } catch (e) {}
  }

  backendPid = backend.pid || 0;
  backendExited = false;
  trace(`后端已启动 pid=${backendPid} port=${port} cmd=${cmd}`);

  backend.on('exit', (code, signal) => {
    backend = null;
    backendExited = true;
    trace(`后端退出 code=${code} signal=${signal}`);
    if (quitting) return;
    // 非退出流程中后端意外死亡
    quitting = true;
    dialog.showErrorBox('后端异常', `后端进程已退出 (code=${code})\n日志：${BACKEND_LOG}`);
    app.quit();
  });

  backend.on('error', (err) => trace('后端进程错误: ' + err));
}

/**
 * 轮询健康检查，等待后端就绪
 */
function waitForBackend(timeoutMs = 120000) {
  const url = `http://127.0.0.1:${port}/api/health`;
  const start = Date.now();

  return new Promise((resolve, reject) => {
    const check = async () => {
      if (backendExited) {
        return reject(new Error(`后端进程已退出，请查看日志：\n${BACKEND_LOG}`));
      }
      try {
        const res = await fetch(url);
        if (res.ok) return resolve();
      } catch (err) {
        // 后端尚未监听，继续重试
      }
      if (Date.now() - start > timeoutMs) {
        return reject(new Error(`后端启动超时，请查看日志：\n${BACKEND_LOG}`));
      }
      setTimeout(check, 300);
    };
    check();
  });
}

function htmlPage(body) {
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>超星学习通 · 自动化学习助手</title><style>
html,body{height:100%;margin:0}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;
font-family:"Microsoft YaHei",system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:32px;
box-sizing:border-box;text-align:center}
.spinner{width:38px;height:38px;border:3px solid #334155;border-top-color:#38bdf8;border-radius:50%;
animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
p{margin:0;font-size:14px;color:#94a3b8}
h1{margin:0;font-size:18px;color:#f87171;font-weight:600}
pre{margin:0;max-width:90%;white-space:pre-wrap;word-break:break-all;font-size:12px;color:#94a3b8;
background:#1e293b;border-radius:8px;padding:14px;text-align:left}
</style></head><body>${body}</body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

const LOADING_PAGE = htmlPage('<div class="spinner"></div><p>正在启动服务，请稍候…</p>');

function errorPage(message) {
  return htmlPage(`<h1>启动失败</h1><pre>${String(message).replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</pre>`);
}

/**
 * 创建主窗口
 */
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: '超星学习通 · 自动化学习助手',
    autoHideMenuBar: true,
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  Menu.setApplicationMenu(null);

  // 安全限制：外链一律不新开窗口，导航限制在本地后端
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  const restrictNavigation = (e, url) => {
    try {
      if (!port || new URL(url).origin !== `http://127.0.0.1:${port}`) e.preventDefault();
    } catch { e.preventDefault(); }
  };
  mainWindow.webContents.on('will-navigate', restrictNavigation);
  mainWindow.webContents.on('will-redirect', restrictNavigation);

  // Programmatic loadURL may display the loading/error data pages; their IPC is denied.
  mainWindow.loadURL(LOADING_PAGE);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * 停止后端：先关 stdin 触发优雅退出，超时后强杀进程树
 */
function stopBackend() {
  const pid = backendPid;
  if (backend) {
    try {
      backend.stdin.end();
    } catch (err) {
      trace('关闭后端 stdin 失败: ' + err);
    }
  }
  backend = null;
  backendPid = 0;

  if (pid) {
    setTimeout(() => {
      exec(`taskkill /pid ${pid} /T /F`, () => {});
    }, 2000);
  }
}

/**
 * 应用启动
 */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    createWindow();
    try {
      await startBackend();
      await waitForBackend();
      trace('后端就绪，加载前端');
      if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${port}`);
    } catch (err) {
      const message = err && err.message ? err.message : String(err);
      trace('启动失败: ' + (err && err.stack ? err.stack : message));
      if (quitting) return;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(errorPage(message));
      } else {
        dialog.showErrorBox('启动失败', message);
        app.exit(1);
      }
    }
  });

  app.on('before-quit', () => {
    quitting = true;
    stopBackend();
  });

  app.on('window-all-closed', () => {
    app.quit();
  });
}
