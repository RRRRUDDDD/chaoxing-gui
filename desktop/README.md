# Windows 桌面版开发与打包指南

## Tauri 2 Windows x64（P3）

Tauri 使用独立的 `chaoxing-gui-tauri.exe` 和 `Chaoxing GUI Tauri` 安装目录。Electron 构建入口继续保留，P4 干净系统矩阵和 P5 默认入口切换尚未完成。当前阶段交付打包、内容校验和 CI 验收脚本；本机构建成功不代表 GitHub Windows runner 或干净 Win10/Win11 已通过。

### 安装、便携与回滚

产物位于 `desktop/release/tauri/`，版本取自 `pyproject.toml`（当前 `1.1.1`）：

| 产物 | 用途 |
|---|---|
| `chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe` | 当前用户 NSIS 安装；默认 `%LOCALAPPDATA%\Chaoxing GUI Tauri` |
| `chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip` | 完整解压后运行 `Start-Chaoxing.cmd` |
| `chaoxing-gui-tauri-artifacts-1.1.1-windows-x64.json`、`SHA256SUMS.txt` | 版本、大小、SHA256 和签名可用性记录 |
| ZIP 的 `.manifest.json`、`.sha256` | ZIP 与内部逐文件清单的完整性检查 |
| NSIS 的 `.exe.manifest.json` | 绑定 NSIS/ZIP 校验值，并记录安装版宿主的独立哈希；包校验时与两个产物一起保留 |

安装器拒绝覆盖含旧 Electron 程序的目录。Tauri 业务数据位于 `%APPDATA%\com.chaoxing.gui\data`，日志位于 `%LOCALAPPDATA%\com.chaoxing.gui\logs`；卸载保留 AppData 数据。便携版也使用当前 Windows 用户的 AppData，不提供随 U 盘携带账号数据的语义。不要仅复制宿主或后端 exe，必须保留完整 `backend/`、`_internal/`、启动脚本与清单。

首次导入旧 Electron 数据前请关闭 Electron。导入保留 `%APPDATA%\chaoxing-desktop` 原目录；回滚时退出 Tauri，再运行原 Electron 程序即可。Tauri 启动失败会在窗口中显示原因，“重新检查”只刷新状态；它不会重启后端或重复提交学习任务。无法保存或读取账号时会显示错误。

### WebView2 与离线安装

常规 NSIS 使用微软在线 Evergreen bootstrapper，缺少 WebView2 时需要联网下载运行时。离线设备先在联网机器从 [微软 WebView2 下载页](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) 获取 **Evergreen Standalone Installer x64**，将 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` 复制到目标设备并安装，再运行 NSIS；不要只复制 bootstrapper 到离线设备。

便携入口先检查运行时，缺少时给出安装说明，不自动安装。用户可主动运行 `Install-WebView2.cmd`；把上述独立安装器放在同目录时会优先离线安装，也可传入 `-InstallerPath`。安装脚本执行前核验 Microsoft Authenticode 签名。直接运行宿主也会在创建 WebView、读取业务数据和启动后端之前显示原生提示。`chaoxing-gui-tauri.exe --check-webview2` 仅检测，返回 `0` 为可用，`3` 为不可用。

### 构建与版本

Windows 构建需要 PowerShell 7、Python 3.11+、Node 20、MSVC C++ Build Tools 与 Windows SDK、支持 NSIS 3 的近期 7-Zip（`7z.exe` 在 PATH，GitHub Windows runner 已提供），以及 Rust **1.95.0**（rustfmt/clippy）。Tauri Rust 依赖锁定 **2.11.5**，CLI 锁定 **2.11.4**。安装 Python 构建依赖时沿用 CI 的排除 PaddleOCR 策略和 PyInstaller **6.21.0**；不改变现有两个 PyInstaller spec 的 console 或排除项。

```powershell
# 本项目已配置的本机工具链环境；标准 rustup/MSVC 开发终端可直接使用。
. ./desktop/scripts/dev-env.ps1
python desktop/scripts/version.py --check
./build_tauri.bat
```

构建入口安装 Node 锁定依赖、执行 Web/Desktop/Python 回归、构建 Web 与冻结后端，再执行 Rust fmt/check/clippy/test、Tauri release 构建和 NSIS/ZIP 内容比对。Cargo 使用 `--locked -j 1`；所有 PyInstaller spec/work/dist 路径应和仓库处于同一磁盘。完整 `web/dist` 继续嵌在冻结后端中供 Electron 使用。CI 已预备并验证这些输入后使用 `build-tauri.ps1 -Prepared`。

Cargo 默认启用仅供测试的 `test-support` 特性，以保留原有 `cargo test --locked -j 1` 入口。正式构建额外传入 `--no-default-features`，Tauri 的 `required-features` 规则排除 `fake-backend`；最终包校验还会拒绝任何多余的测试程序。

Tauri 会在 NSIS 宿主中写入包类型标记，所以它与便携宿主的哈希不同。构建从编译输出独立计算 NSIS 预期字节；有证书时在签名回调中捕获已签 NSIS 宿主，再单独签便携宿主。资源清单中的后端和第三方依赖在打包时保持原字节。内容校验对两个宿主分别检查完整哈希，对其余全部文件使用相同资源清单。

`pyproject.toml` 是唯一版本源。手动修改该文件后，可运行 `python desktop/scripts/version.py --sync` 同步 Cargo、Tauri、Web/Desktop package 与 lock；`--check --tag v1.1.1 --artifacts desktop/release/tauri` 检查当前 tag 和产物。脚本不会自行升级版本或创建 tag。

`sign-windows.ps1 -Mode Inspect` 查找有效且含私钥的代码签名证书；多个证书时用 `CHAOXING_SIGN_CERT_THUMBPRINT` 选择。实际签名还需要 Windows SDK `signtool.exe`。CI 可提供 `WINDOWS_CERTIFICATE_PFX`（Base64）和 `WINDOWS_CERTIFICATE_PASSWORD` secrets；缺少可用证书时明确记录 unsigned，有证书时对宿主、后端和安装器签名并验证。SHA256 清单用于检测内容变化，不能替代发行者签名。

### 验证入口与边界

```powershell
python -m unittest discover -s tests -v
npm --prefix web test
npm --prefix desktop test
pwsh -NoProfile -File desktop/scripts/verify-package.ps1 -PackagePath desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip
pwsh -NoProfile -File desktop/scripts/verify-nsis.ps1 -InstallerPath desktop/release/tauri/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe -PortablePath desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip
```

包检查不执行安装器或宿主。`smoke-tauri.ps1` 先用合成后端验证失败、取消、409/404 和退出，再验证真实冻结后端的 health/安全 API；宿主子进程 PATH 排除系统 Python，stdin 是持有的真实管道，嵌套 Windows Job 在关窗和强杀后检查残留。GUI 操作为 CDP 下确认可见/可用后 DOM click，不等于物理鼠标测试。

`smoke-installation.ps1` 只在 fresh GitHub Windows runner，或明确传入 `-DisposableWindowsUser` 的专用隔离 Windows 用户/VM 中运行安装、卸载和 ZIP 宿主验收。不要在含真实账号的日常用户下运行或用 debug 环境变量替代隔离；release 不接受 `CHAOXING_TAURI_DEV_ROOT`、`CHAOXING_TAURI_DEV_BACKEND`、`CHAOXING_TAURI_DEV_HIDDEN`。脚本拒绝既存 Tauri 用户数据，安装与清理均限制在本次创建的路径。

CI 保留 Python 3.11/3.13、Node 20、Electron 测试与打包、独立 Python 发行，增加 Rust/包校验、真实宿主和嵌套 Job 验收。任一步失败阻断发行路径，验证证据通过 artifact 留存；手动 workflow 默认不发布。尚未实际运行的远程 CI、缺 WebView2 干净机、安装后运行及签名门槛必须在阶段交付记录中保留。

## Electron（现有发行与回滚入口）

## 概述

超星学习通 · 自动化学习助手现在支持三种发行形态：

1. **独立 exe**（原有）：`chaoxing.spec` → `dist/chaoxing-gui.exe`，带托盘图标，自动打开系统浏览器
2. **便携版**（原有）：`build_portable.bat` → 嵌入式 Python + 启动脚本
3. **Electron 桌面版**（新增）：`desktop/` → 独立窗口，无需浏览器

## 架构

```
Electron 主进程 (main.js)
  ↓ spawn
  ├─ 后端子进程 (chaoxing-backend.exe / python app.py)
  │   ├─ Flask API (127.0.0.1:动态端口)
  │   └─ stdin watchdog (父进程退出时自动退出)
  └─ BrowserWindow
      └─ loadURL('http://127.0.0.1:{port}')
```

### 关键特性

- **动态端口**：Electron 分配空闲端口，通过 `CHAOXING_PORT` 环境变量传递给后端
- **孤儿进程防护**：后端监听 stdin EOF，父进程退出时自动终止
- **单实例锁**：`app.requestSingleInstanceLock()` 确保只运行一个实例
- **零前端改动**：前端仍通过相对路径 `/api/*` 调用后端

## 开发模式

### 启动完整开发环境

```bash
# 1. 后端开发
python app.py
# 访问 http://localhost:5000

# 2. 前端开发（热重载）
cd web && npm run dev
# 访问 http://localhost:3000（代理到后端 5000）

# 3. Electron 桌面开发（使用开发态后端 + 构建后的前端）
cd desktop && npm run dev
# Electron 窗口加载 http://127.0.0.1:{动态端口}
```

**注意**：
- Electron `npm run dev` 会启动 `python app.py`（开发模式），不支持前端热重载
- 前端迭代用 `web/npm run dev`；桌面窗口迭代用 `desktop/npm run dev`

## 构建生产版本

### 一键构建（推荐）

```bash
build_desktop.bat
```

**输出**：
- `desktop/release/chaoxing-gui-desktop-*.exe`（NSIS 安装包）
- `desktop/release/chaoxing-gui-desktop-*-portable.exe`（绿色便携版）

### 分步构建

```bash
# 1. 构建前端
cd web && npm ci && npm run build

# 2. 构建后端 exe（无头模式专用）
pyinstaller --clean --noconfirm chaoxing-backend.spec

# 3. 保留整个 onedir 后端层级
pwsh -NoProfile -File desktop/scripts/prepare-backend.ps1 -DestinationDirectory desktop/backend/chaoxing-backend

# 4. 构建 Electron 应用
cd desktop && npm ci && npx electron-builder --win
```

## 环境变量说明

后端 `app.py` 识别以下环境变量：

| 变量 | 值 | 作用 |
|------|---|------|
| `CHAOXING_HEADLESS` | `1` | 启用无头模式：绑定 127.0.0.1、禁用托盘、禁用 webbrowser.open、启用 stdin 守护 |
| `CHAOXING_PORT` | 整数 | Flask 监听端口，默认 `5000` |

**向后兼容**：
- 不设置环境变量 → 行为与原有完全一致（独立 exe 带托盘 + 浏览器）
- 开发模式 `python app.py` → 默认 5000 端口，无托盘，不自动开浏览器

## 文件清单

### 新增文件

```
desktop/
├── main.js                    # Electron 主进程
├── package.json               # Electron 依赖
├── electron-builder.yml       # 打包配置
├── build/icon.png             # 应用图标（512x512）
└── .gitignore

chaoxing-backend.spec          # 无头后端构建配置（console=True）
build_desktop.bat              # 一键构建脚本
```

### 修改文件

```
app.py                         # 新增 HEADLESS 模式支持（43-46, 617-627, 647-678 行）
.gitignore                     # 排除 desktop/node_modules, desktop/backend, desktop/release
```

## 常见问题

### Q1: 为什么需要两个 spec 文件？

- `chaoxing.spec`（原有）：独立 exe，`console=False`，带托盘图标
- `chaoxing-backend.spec`（新增）：Electron 后端，`console=True`，确保 stdin/stdout 可用

### Q2: 为什么后端要 `console=True`？

`console=True` 确保 `sys.stdin` 是真实管道句柄，而非 `None`。Electron 通过 `stdin.end()` 通知后端退出。

### Q3: 安装包体积多大？

- NSIS 安装包：~140 MB（压缩）
- 安装后体积：~250 MB（Electron 运行时 ~100 MB + PyInstaller 后端 ~150 MB）

### Q4: 如何调试后端日志？

**开发模式**：直接查看终端输出
**生产模式**：日志写入 `%APPDATA%\chaoxing-desktop\backend.log`

```powershell
Get-Content $env:APPDATA\chaoxing-desktop\backend.log -Tail 50 -Wait
```

### Q5: 端口冲突怎么办？

Electron 自动分配空闲端口，不会冲突。原有独立 exe 仍使用 5000，但支持实例复用（检测到 5000 占用时直接打开浏览器）。

## 测试清单

- [ ] 开发模式：`cd desktop && npm run dev` 窗口正常显示
- [ ] 生产构建：`build_desktop.bat` 无错误
- [ ] 安装：双击安装包，默认安装到 `C:\Users\<用户>\AppData\Local\Programs\超星泛雅刷课助手`
- [ ] 启动：桌面快捷方式启动，窗口显示登录界面
- [ ] 功能：登录 → 选课 → 开始学习，后台正常运行
- [ ] 关闭窗口：进程全部退出（Task Manager 检查无残留）
- [ ] 重复启动：二次启动聚焦第一个窗口，不创建新实例
- [ ] 配置持久化：`%APPDATA%\chaoxing-desktop\web_config.json` 保存用户配置
- [ ] 卸载：开始菜单卸载，程序文件清理，业务数据保留

## 许可证

与主项目一致
