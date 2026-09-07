# P3 实施计划

## 阶段与依赖

1. 来源归属：核验 entry-*、P2 patch/final snapshot，整理 P1 基础提交及 P2 增量提交。只在 Git 索引中采用历史快照，工作区保持最终整合字节。api/desktop_runtime.py 无原始快照，只能以已核验 P2 最终源码纳入基础并明确记录，不能伪造原始版本。补齐 desktop CLI lock，确保干净检出可装依赖。
2. 测试先行并行实施：先定义/运行资源完整性、缺资源/错误启动、取消、正常退出/强杀、nested Job、版本漂移与打包验证负例。外部分析无报告后采用以下独立模块方案；不称分析通过。
3. 主代理集成：Tauri 配置及资源映射、独立宿主名、NSIS 数据保留、版本/签名入口、CI 和文档。完整资源准备完再执行需 resources 的 Cargo 构建。
4. 本地验证：脚本/版本测试、Python 3.11/3.13、Node 20 的 Web/Electron、Rust fmt/check/clippy/test，均保存日志。先 fake 故障注入，后真实冻结后端；debug 证据另设 P2_EVIDENCE_DIR。
5. release 构建与包校验：本机生成 NSIS/ZIP、校验层级/manifest/hash/版本/签名状态；不在当前真实用户启动 release。CI 在 fresh runner 运行 release 安装/便携宿主及 nested Job；无远程执行则记录门槛未验证。
6. 双路最终外部审查、独立复核、修复和针对性重验。编写交付/复现/保留改动与待办，归档本轮已完成的实施交付；父任务继续未完成，未过门槛转交。

## 文件归属（fork_turns=none；子代理不得再派生）

| 所有者 | 文件范围 |
|---|---|
| packaging | desktop/scripts/prepare-backend.ps1、package-portable.ps1、verify-package.ps1、package-common.ps1；desktop/portable/；desktop/tests/packaging.test.mjs；本任务 verification/packaging-* 与 research/packaging-* |
| smoke | desktop/scripts/smoke-tauri.ps1、p3-smoke.mjs、smoke-python.ps1、smoke-installation.ps1、p3-installation.mjs；desktop/tests/p3-smoke.test.mjs、installation.test.mjs 及新 p3 fixture；本任务 verification/smoke-* 与 research/smoke-* |
| native | desktop/src-tauri/src/main.rs、webview_runtime.rs、windows_job.rs；desktop/src-tauri/tests/nested_job.rs；追加接管 desktop/src-tauri/windows/installer.nsi、installer-hooks.nsh、UPSTREAM.md 和 desktop/tests/nsis-paths.test.mjs/fixture；本任务 verification/native-* 与 research/native-*。新增模块接在 main.rs，避免与 lib.rs 集成冲突 |
| 主代理 | 其余文件：task/父任务、来源提交、config/Cargo/package/lock、版本签名/build 入口、CI、README、集成验证、审查与归档 |

任何跨归属修改先通知主代理，不恢复/覆盖别人的编辑；并发 Cargo 仅由主代理调度，全部 --locked -j 1。

## 固定接口与产物约定

- 后端源 dist/chaoxing-backend；默认 staging desktop/src-tauri/resources/backend。Tauri 映射 `resources/backend/` → `backend/`；清单在 resources/backend-manifest.json，可单独映射到 backend-manifest.json。staging 不进入 Git。
- Rust Cargo 主 bin 保持 chaoxing-desktop 供现有 debug smoke；Tauri `mainBinaryName` 为 chaoxing-gui-tauri，发行宿主名 chaoxing-gui-tauri.exe。productName 使用独立的 Chaoxing GUI Tauri；identifier 保持 com.chaoxing.gui。
- 输出 desktop/release/tauri/chaoxing-gui-tauri-setup-<version>-windows-x64.exe、chaoxing-gui-tauri-portable-<version>-windows-x64.zip。便携解包根目录直接有 host、backend/、资源与启动/安装运行时指引；不得打进测试 exe 或 Electron。
- portable 构建参数：HostPath、BackendDirectory（默认 staging）、OutputDirectory、Version。版本值必须与 pyproject 一致；可以额外支持清单路径。全包输出 hash manifest，验证 ZIP 时不需要运行宿主。
- 宿主 `--check-webview2`：仅检查运行时，exit 0 可用，exit 3 不可用；不创建 WebView、不启动后端、不触碰数据。裸宿主缺运行时时 native MessageBox 指向同目录运行时安装入口/微软说明，然后退出。测试注入只能在测试/debug，release 不增加绕过隔离变量。
- 便携 Start-Chaoxing.cmd + PowerShell 入口；Install-WebView2 入口支持线上 bootstrapper 及用户提供 MicrosoftEdgeWebView2RuntimeInstallerX64.exe 离线文件，安装前验证 Microsoft Authenticode。任何安装都只由用户主动运行入口触发，构建/测试不安装运行时。
- smoke-tauri.ps1 接受 HostPath、BackendDirectory、EvidenceDirectory、Configuration(Debug/Release)、Scenario(Fake/Frozen/All)，支持 NestedJob。Release 必须要求 fresh GitHub runner 或调用者明确声明 disposable Windows user/VM，且目标 AppData 原先不存在/带本次所有权标记；本机不传声明。host 子进程 PATH 只留 Windows 目录，并清除 Python/开发覆写，验证 backend exe 实际路径。
- smoke 复用 P2 合成后端/业务断言，按捕获 PID 和准确主窗口标题关窗；release 测试不能通过 debug 覆写后端，使用隔离包副本。CI 准备冻结 fixture，真实 backend 仅配置、空输入400、缺task404等无上游调用路径。
- CI 发布步骤须依赖所有新增测试/打包/smoke成功，Tauri artifact glob 仅限独立目录。不自动执行远程 workflow。

## 当前环境限制

本机未提升管理员权限，没有 Windows Sandbox 或运行中的 Hyper-V VM，不能据此创建隔离 release 验收环境。当前用户/本机和虚拟环境为 Python3.11.9/3.13.14、Node24（将另用 Node20 验证）、Rust1.95.0；证书初查没有可用代码签名私钥证书或签名环境变量。最后的验收记录必须保留这些边界。
