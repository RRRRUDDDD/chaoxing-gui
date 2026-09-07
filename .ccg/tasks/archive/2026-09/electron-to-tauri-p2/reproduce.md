# P2 本地验证复现

Windows/PowerShell，仓库根目录执行；要求当前工作区保留 P1/P2 整合代码。验证只创建合成账号 profile，不能将真实账号目录传作 fixture。

本轮版本：Node 24.14.0、Python 3.11.9、Rust/Cargo 1.95.0、Tauri 2.11.5、@tauri-apps/api 2.11.1、Playwright Core 1.63.0、Electron 33.4.11。本机与 CI Node 20、后续 Python 3.13/干净 VM 的差异保持区分。

```powershell
. desktop/scripts/dev-env.ps1
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
python -m unittest discover -s tests -v
npm --prefix desktop test
cargo fmt --manifest-path desktop/src-tauri/Cargo.toml --all --check
cargo check --manifest-path desktop/src-tauri/Cargo.toml --locked --all-targets -j 1
cargo clippy --manifest-path desktop/src-tauri/Cargo.toml --locked --all-targets -j 1 -- -D warnings
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked -j 1
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked -j 1 --features custom-protocol --bin chaoxing-desktop
```

本机并行 Cargo 曾遇 Windows paging-file 1455，始终 -j1。MSVC/rustup/镜像处理见父 implementation.md 和 dev-env.ps1。本次未新增安装器或替换 Electron 默认构建。

GUI 工具安装于仓库外（可用 P2_TOOLS_DIR 覆写）：

```powershell
$p2Tools = Join-Path $env:TEMP 'chaoxing-p2-tools'
npm install --prefix $p2Tools playwright-core@1.63.0 electron@33.4.11
python -m PyInstaller --noconfirm --onedir --name p2-backend --specpath desktop/src-tauri/target/p2-fixture --workpath desktop/src-tauri/target/p2-fixture/build --distpath desktop/src-tauri/target/p2-fixture/dist desktop/tests/fixtures/p2_backend.py
python -m PyInstaller --noconfirm chaoxing-backend.spec
node desktop/scripts/p2-smoke.mjs all
```

PyInstaller fixture 的 spec/work/dist 保持和仓库同盘；跨盘 spec 曾触发 relpath 错误。浏览器默认路径为 `C:/Program Files/Google/Chrome/Application/chrome.exe`，可用 P2_CHROME 覆写。

smoke 也可分别运行 browser / electron / tauri / failures / migration / native / frozen。先模拟后端和故障注入，再 frozen；frozen 只发送空账号输入及隔离配置/不存在任务请求，不启动学习。

输出默认在 ignored `desktop/src-tauri/target/p2-smoke-evidence`；P2_EVIDENCE_DIR 可设独立证据目录。每次 profile 都是新的 `chaoxing-p2-smoke-*` 临时目录。Debug 宿主显式隔离 data/logs/webview/legacy，不隐式读取生产 Electron 数据。

DOM click 是当前桌面未投递 CDP 鼠标事件时的驱动方式。正常关闭使用主窗口 WM_CLOSE，并核对宿主和本次后端退出；不要向该进程的所有隐藏窗口发送 WM_CLOSE。
