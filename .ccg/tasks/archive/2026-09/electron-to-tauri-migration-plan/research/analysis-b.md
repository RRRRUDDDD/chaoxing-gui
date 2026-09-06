# 分析：Electron → Tauri 2 迁移计划（后端/构建发布视角）

## 现状（已核实源码）

- `desktop/main.js:63-122` 生产态 spawn `resources/backend/chaoxing-backend.exe`（onedir 平铺进 `resources/backend/`，`_internal` 同级），注入 `CHAOXING_PORT/HEADLESS/ELECTRON/DATA_DIR`；`waitForBackend` 轮询 `/api/health`（300ms/120s，`main.js:127-149`）；退出语义 = `stdin.end()` → 2 秒宽限 → `taskkill /T /F`（`main.js:217-234`）。
- `app.py:46-54` 环境契约、CORS 限定 `localhost/127.0.0.1:{PORT}`（`app.py:52`）、`os.chdir(DATA_DIR)`（`app.py:767-768`）、`/api/health`（`app.py:637`）。
- `app.py:734-743` stdin 守护线程读到 EOF 后 **`os._exit(0)`**：跳过 atexit/线程 join，`Tiku` 答案缓存与 worker 队列的 `finally` 清理不执行（与 `.ccg/spec/backend/index.md` 的关闭协议冲突）——此语义 Electron 现状已存在，迁移需对等而非恶化。
- `chaoxing-backend.spec` 为 onedir（`exclude_binaries=True`+COLLECT）、`console=True`；`chaoxing.spec` 为独立 onefile 产物，与桌面版共存于 CI（`.github/workflows/main.yml:124-192`），**两者互不影响，迁移只动桌面链路**。
- 工作区干净，无未提交改动。

## 迁移架构（保留 Flask/Python）

```
Tauri 2 (Rust 主进程)
  ├─ 自定义 command：TcpListener bind 127.0.0.1:0 取空闲端口
  ├─ spawn onedir 后端（resource_dir()/backend/chaoxing-backend.exe）
  │   env: CHAOXING_PORT / HEADLESS=1 / ELECTRON=1 / DATA_DIR=app_data_dir
  ├─ 轮询 /api/health 后导航 WebView → http://127.0.0.1:{port}
  └─ 关闭时：stdin drop → 等待 → taskkill /PID x /T /F
```

**关键决策：不用 sidecar（`bundle.externalBin`），用 `bundle.resources` 装载整个 onedir 目录。** 依据：Tauri sidecar 仅支持单文件并为 exe 追加 target-triple 后缀，不随带 `_internal/`；PyInstaller onedir 要求 exe 与 `_internal` 同级。用 resources 保留目录结构，Rust 侧拼 `resource_dir()` 绝对路径 spawn（自写进程管理，避开 plugin-shell 的 sidecar/scope 限制）。后端 `app.py` 零改动。

## 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| WebView2 为新系统依赖（Electron 自带运行时） | 高 | `windows.webviewInstallMode: downloadBootstrapper`（离线场景用 offlineInstaller）；Win10 裸机验证 |
| `os._exit(0)` 丢缓存（答案键/任务态） | 中 | 迁移先对等；PoC 阶段评估新增 `/api/shutdown` 优雅关闭端点（需改 `app.py`，另立议题） |
| Tauri 默认 `app_data_dir` 用 identifier，指向 `%APPDATA%\com.chaoxing.gui`，与现 `%APPDATA%\chaoxing-desktop`（Electron `name`）断裂 | 高 | identifier 定为 `chaoxing-desktop` 保路径兼容（web_config.json/cookies.txt/chaoxing.log 无缝沿用）；放弃 `com.chaoxing.gui` 风格 |
| 前端 IPC：`main.js:27-31` 注册的 SessionStore/窗口控制 IPC 无法平移 | 中 | 改为 Tauri command；`preload.js/session-store.js` 的前端调用面**未在本次检查范围，标注 PoC**；前端需保留能力探测 |
| 杀软误报/长路径/onedir 在用户机首次启动慢 | 中 | 干净 Win10/11 VM 验证；保留 120s 健康超时 |
| `desktop/package.json` tests（`node --test tests/*.test.js`）随 Electron 移除 | 低 | 进程管理逻辑下沉 Rust 侧补测试 |

## 阶段计划（按依赖排序）

1. **PoC（2–4 天）**：新建 `desktop-tauri/`（`tauri.conf.json`、`src-tauri` spawn/health/退出逻辑）。门槛：干净 VM 上启动→就绪→关闭无残留进程→二次启动聚焦。
2. **打包（2–3 天）**：`bundle.resources` 装载 onedir；NSIS（对应 `electron-builder.yml` 的 `oneClick:false`、`allowToChangeInstallationDirectory`、`deleteAppDataOnUninstall:false`）；`version: 1.1.1`；便携版改 ZIP（Tauri 无 portable exe target，沿用 `main.yml:154-160` 的 Compress-Archive 模式）；`build_desktop.bat` 换 `build_tauri.bat`（步骤 1/2/3 前端+PyInstaller 不变，仅替换第 4 步）。门槛：安装/卸载/数据沿用/便携 ZIP 解压即用。
3. **CI（1–2 天）**：仅改 `.github/workflows/main.yml:194-216`：加 rustup/cargo 缓存，`tauri build` 替代 electron-builder，artifact 路径改 `target/release/bundle/`；`test-backend`、两处 PyInstaller smoke、发布逻辑（`main.yml:218-228`）不动。门槛：tag 触发全绿、三产物（NSIS/ZIP/onefile）齐发。
4. **IPC 对齐（2–4 天，PoC 后）**：session-store 能力探测迁移；验证门槛：登录→学习全流程在 WebView2 下回归（`unittest discover` 后端回归不变）。
5. **清理**：删 `desktop/`、更新 `desktop/README.md` → 新 README。

## 已知 vs PoC

- **已知**（源码可证）：环境契约、health/stdin 语义、onedir 布局、CI 步骤归属、CORS 兼容（WebView2 加载同源 `127.0.0.1`，`app.py:52` 无需改）。
- **PoC**（需实测）：resource_dir spawn onedir 实机表现、WebView2 引导、Tauri command IPC 面、便携 ZIP 布局、`os._exit` 在 Tauri 关闭时序下的缓存丢失程度。

**结论**：架构上 Flask 后端原样保留，改动集中在壳层进程管理、数据目录 identifier、打包与 CI 四处；PoC 通过前不启动阶段 2。本报告为只读分析，未执行迁移、未运行测试。

---
SESSION_ID: ad74bb8e-f36e-4bd1-a276-a0406dda3597
