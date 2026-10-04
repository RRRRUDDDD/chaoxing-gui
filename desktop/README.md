# Windows 桌面版开发与打包指南

## Tauri 2 Windows x64（P3）

桌面版名称为「超星学习通·自动化学习助手」。安装目录为 `chaoxing_gui`：D: 是固定磁盘时默认 `D:\chaoxing_gui`，否则为 `%LOCALAPPDATA%\chaoxing_gui`。宿主程序仍为 `chaoxing-gui-tauri.exe`。

### 安装、便携与回滚

产物位于 `desktop/release/tauri/`，版本取自 `pyproject.toml`（当前 `1.3.0`）：

| 产物 | 用途 |
|---|---|
| `chaoxing-gui-setup-1.3.0-windows-x64.exe` | 当前用户 NSIS 安装；默认目录 `chaoxing_gui` |
| `chaoxing-gui-portable-1.3.0-windows-x64.zip` | 完整解压后运行 `Start-Chaoxing.cmd` |
| `chaoxing-gui-artifacts-1.3.0-windows-x64.json`、`SHA256SUMS.txt` | 版本、大小、SHA256 和签名可用性记录 |
| ZIP 的 `.manifest.json`、`.sha256` | ZIP 与内部逐文件清单的完整性检查 |
| NSIS 的 `.exe.manifest.json` | 绑定 NSIS/ZIP 校验值，并记录安装版宿主的独立哈希；包校验时与两个产物一起保留 |

安装器拒绝覆盖含旧版桌面程序的目录。Tauri 业务数据位于 `%APPDATA%\com.chaoxing.gui\data`，日志位于 `%LOCALAPPDATA%\com.chaoxing.gui\logs`；卸载保留 AppData 数据。便携版也使用当前 Windows 用户的 AppData，不提供随 U 盘携带账号数据的语义。不要仅复制宿主或后端 exe，必须保留完整 `backend/`、`_internal/`、启动脚本与清单。

如果还有旧版数据，首次导入前请先关闭旧程序。导入保留 `%APPDATA%\chaoxing-desktop` 原目录。Tauri 启动失败会在窗口中显示原因，“重新检查”只刷新状态；它不会重启后端或重复提交学习任务。无法保存或读取账号时会显示错误。

### WebView2 与离线安装

常规 NSIS 使用微软在线 Evergreen bootstrapper，缺少 WebView2 时需要联网下载运行时。离线设备先在联网机器从 [微软 WebView2 下载页](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) 获取 **Evergreen Standalone Installer x64**，将 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` 复制到目标设备并安装，再运行 NSIS；不要只复制 bootstrapper 到离线设备。

便携入口先检查运行时，缺少时给出安装说明，不自动安装。用户可主动运行 `Install-WebView2.cmd`；把上述独立安装器放在同目录时会优先离线安装，也可传入 `-InstallerPath`。安装脚本执行前核验 Microsoft Authenticode 签名。直接运行宿主也会在创建 WebView、读取业务数据和启动后端之前显示原生提示。`chaoxing-gui-tauri.exe --check-webview2` 仅检测，返回 `0` 为可用，`3` 为不可用。

### 构建与版本

Windows 构建需要 PowerShell 7、Python 3.11+、Node 20、MSVC C++ Build Tools 与 Windows SDK、支持 NSIS 3 的近期 7-Zip（`7z.exe` 在 PATH，GitHub Windows runner 已提供），以及 Rust **1.95.0**（rustfmt/clippy）。Tauri Rust 依赖锁定 **2.11.5**，CLI 锁定 **2.11.4**。安装 Python 构建依赖时沿用 CI 的排除 PaddleOCR 策略和 PyInstaller **6.21.0**；两个 PyInstaller spec 保留原 console 模式。

验证码使用 ddddocr **1.6.1** 的原始 `common_old.onnx` 和完整默认字符表，由 Pillow、NumPy、ONNX Runtime 执行现有文字识别路径。两个 spec 只收集这组资源和许可证/版本元数据，排除 ddddocr 运行时代码、OpenCV、beta/检测模型。构建环境仍会安装 ddddocr 的传递依赖；这些依赖不会全部进入发行包。升级 ddddocr 前必须重新核对模型与字符表 SHA256，并运行下列比较及冻结验证，不能只改版本号。

```powershell
python desktop/scripts/verify-captcha-ocr.py --source
python desktop/scripts/verify-captcha-ocr.py --executable dist/chaoxing-backend/chaoxing-backend.exe
python desktop/scripts/verify-captcha-ocr.py --executable dist/chaoxing-gui.exe
```

CI 对 48 张生成图片比较新旧预处理张量和识别结果，并在禁止导入 ddddocr/cv2 的独立进程验证识别。冻结检查读取实际 EXE 模块/资源清单，再在空临时目录运行 `--check-captcha-ocr`，不启动 Web 服务或访问账号；独立 exe 也通过报告文件验证结果。此检查证明运行与上游一致性，不代表真实课程验证码准确率已验收。题目图片的云端/HTTP OCR，以及已安装的 paddleocr 包，不受这次裁剪影响。

```powershell
# 本项目已配置的本机工具链环境；标准 rustup/MSVC 开发终端可直接使用。
. ./desktop/scripts/dev-env.ps1
python desktop/scripts/version.py --check
./build_tauri.bat
```

构建入口安装 Node 锁定依赖、执行 Web/Desktop/Python 回归、构建 Web 与冻结后端，再执行 Rust fmt/check/clippy/test、Tauri release 构建和 NSIS/ZIP 内容比对。Cargo 使用 `--locked -j 1`；所有 PyInstaller spec/work/dist 路径应和仓库处于同一磁盘。完整 `web/dist` 继续嵌在冻结后端中。CI 已预备并验证这些输入后使用 `build-tauri.ps1 -Prepared`。

Cargo 默认启用仅供测试的 `test-support` 特性，以保留原有 `cargo test --locked -j 1` 入口。正式构建额外传入 `--no-default-features`，Tauri 的 `required-features` 规则排除 `fake-backend`；最终包校验还会拒绝任何多余的测试程序。

Tauri 会在 NSIS 宿主中写入包类型标记，所以它与便携宿主的哈希不同。构建从编译输出独立计算 NSIS 预期字节；有证书时在签名回调中捕获已签 NSIS 宿主，再单独签便携宿主。资源清单中的后端和第三方依赖在打包时保持原字节。内容校验对两个宿主分别检查完整哈希，对其余全部文件使用相同资源清单。

`pyproject.toml` 是唯一版本源。手动修改该文件后，可运行 `python desktop/scripts/version.py --sync` 同步 Cargo、Tauri、Web/Desktop package 与 lock；`--check --tag v1.3.0 --artifacts desktop/release/tauri` 检查当前 tag 和产物。脚本不会自行升级版本或创建 tag。

`sign-windows.ps1 -Mode Inspect` 查找有效且含私钥的代码签名证书；多个证书时用 `CHAOXING_SIGN_CERT_THUMBPRINT` 选择。实际签名还需要 Windows SDK `signtool.exe`。CI 可提供 `WINDOWS_CERTIFICATE_PFX`（Base64）和 `WINDOWS_CERTIFICATE_PASSWORD` secrets；缺少可用证书时明确记录 unsigned，有证书时对宿主、后端和安装器签名并验证。SHA256 清单用于检测内容变化，不能替代发行者签名。

### 验证入口与边界

```powershell
python -m unittest discover -s tests -v
npm --prefix web test
npm --prefix desktop test
pwsh -NoProfile -File desktop/scripts/verify-package.ps1 -PackagePath desktop/release/tauri/chaoxing-gui-portable-1.3.0-windows-x64.zip
pwsh -NoProfile -File desktop/scripts/verify-nsis.ps1 -InstallerPath desktop/release/tauri/chaoxing-gui-setup-1.3.0-windows-x64.exe -PortablePath desktop/release/tauri/chaoxing-gui-portable-1.3.0-windows-x64.zip
```

包检查不执行安装器或宿主。`smoke-tauri.ps1` 先用合成后端验证失败、取消、409/404 和退出，再验证真实冻结后端的 health/安全 API；宿主子进程 PATH 排除系统 Python，stdin 是持有的真实管道，嵌套 Windows Job 在关窗和强杀后检查残留。GUI 操作为 CDP 下确认可见/可用后 DOM click，不等于物理鼠标测试。

`smoke-installation.ps1` 只在 fresh GitHub Windows runner，或明确传入 `-DisposableWindowsUser` 的专用隔离 Windows 用户/VM 中运行安装、卸载和 ZIP 宿主验收。不要在含真实账号的日常用户下运行或用 debug 环境变量替代隔离；release 不接受 `CHAOXING_TAURI_DEV_ROOT`、`CHAOXING_TAURI_DEV_BACKEND`、`CHAOXING_TAURI_DEV_HIDDEN`。脚本拒绝既存 Tauri 用户数据，安装与清理均限制在本次创建的路径。

CI 保留 Python 3.11/3.13、Node 20、独立 Python 发行，以及 Rust/包校验、真实宿主和嵌套 Job 验收。任一步失败阻断发行路径，验证证据通过 artifact 留存；手动 workflow 默认不发布。尚未实际运行的远程 CI、缺 WebView2 干净机、安装后运行及签名门槛必须在阶段交付记录中保留。

## 开发与构建

```bash
# 前端热重载
cd web && npm run dev

# Tauri 桌面开发
cd desktop && npm run dev:tauri

# Windows x64 安装包与便携包
build_tauri.bat
```

产物在 `desktop/release/tauri/`。后端仍用 `chaoxing-backend.spec` 冻结，由 Tauri 安装包一起分发。`CHAOXING_HEADLESS=1` 使后端绑定本机并关闭托盘和浏览器自启。
