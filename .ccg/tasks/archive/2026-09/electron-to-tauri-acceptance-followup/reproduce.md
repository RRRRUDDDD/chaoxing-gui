# 续接验收复现

本轮归档只表示可执行的补审、隔离调查、必要修复与证据交付已完成。父迁移和 P3 全部门槛保持未完成；以 verification/final-results.json 为最终索引。不要重新应用 P2 旧补丁或重做 P1/P2/P3。

## 保护与环境

- 保留用户归档迁移 plan.md 的当前字节（SHA256 `7c926423ae0a4e3643fbf54f2dd68d2e1899ebae0817ac23ea99d641151a2bab`）和 poc-window.png（`282e312479ea72fc33b72211ec2404fd84a5dda7bc5803cdc44920c3465dce87`）。不要恢复旧 P3 交接里的 plan 哈希，不暂存这两个文件。
- 所有 release 产品执行必须在实际 fresh Sandbox/VM/专用用户或干净 hosted Windows runner。`-DisposableWindowsUser` 是调用者声明，不是“RUD 永远被程序硬拒绝”的保证。本任务 guest 外层会核对真实 WDAGUtilityAccount、VM 身份、映射与空 known-folder profiles。
- 不 push、tag、发布、访问真实业务账号或运行学习任务。不修改既存 ignored P4 任务，不清理 P3 被审批拒绝的旧临时目录。
- 构建依赖若需重建按旧 P3 reproduce.md；Cargo 串行且 `--locked -j 1`，PyInstaller spec/work/dist 同盘。此轮原始产品未重建，包哈希列于交付。

## 本机可运行的离线回归

在源码根目录，使用 Node 20.20.0，Desktop 测试要求真实 NSIS 路径夹具启用；测试本身不执行产品安装包。不要把主机 Node24 的结果冒充 CI Node20。

```powershell
$env:CHAOXING_REQUIRE_NSIS_PATH_TESTS='1'
Push-Location desktop
try {
    & '<Node 20.20.0 的 node.exe>' --test --test-concurrency=1
    if ($LASTEXITCODE -ne 0) { throw 'Desktop tests failed' }
} finally { Pop-Location }
```

最终对应测试日志见 verification/final-results.json。新源修复之后没有重新跑不受影响的 Rust/Python/Web 全套；旧 P3 结果保留为历史。

## 在本机重建新的 Sandbox 输入

先核对 HEAD 和工作区，所需源码必须已提交。helpers 通过 Git 解析仓库根，归档后仍可用。当前机器已准备的工具/原包位于 ignored target/release；它们不是 Git 归档中的二进制。prepare-sandbox-kit.ps1 明确需要：

- Node20.20.0 的既有缓存路径、当前 PowerShell7、desktop/node_modules/playwright-core。
- 官方 Microsoft WebView2 offline installer，SHA256 `e7fa35755196ad9223596ef021a1ce6799509142eaa40ba35f634026be50b831`，有效 Microsoft Corporation 签名。
- 已按 verification/nanazip-tool-provenance.json 准备的 NanaZip 6.5.1767.0 独立副本。官方 7-Zip25.01 对此 NSIS 实际 BadCmd=13，不能替代它，也不能放松解析门槛。
- 原 P3 NSIS/portable 与全部 sidecar；合成冻结后端在 target/acceptance-followup-fixture/dist/p2-backend。

以下命令在主机只准备输入/启动隔离 VM；产品由 guest 在通过身份与哈希校验后运行：

```powershell
$taskRoot='.ccg/tasks/archive/2026-09/electron-to-tauri-acceptance-followup'
pwsh -NoProfile -File "$taskRoot/verification/prepare-sandbox-kit.ps1" -AcceptanceMode NativeInstallationPartial -ArchiveDecoder NanaZip
if ($LASTEXITCODE -ne 0) { throw 'Kit preparation failed' }
pwsh -NoProfile -File "$taskRoot/verification/launch-sandbox-acceptance.ps1" -TimeoutSeconds 1800
if ($LASTEXITCODE -ne 0) { throw 'Guest selected acceptance scope failed; preserve result/logs' }
```

每次产生新的 kit/run/manifest，不复用已有 Sandbox 或覆盖旧证据。输入映射只读，单独输出映射可写；无 host repo、业务 AppData 或凭据映射；禁用剪贴板/音视频/打印重定向。网络只用于 Microsoft 信任/运行时准备，没有业务账号。guest 自动关机，失败仍导出结果。

`NativeInstallationPartial` 的 success 只代表原生补验范围，fullAcceptancePassed 始终 false。标准 GUI 入口可另选 `-AcceptanceMode Standard`；已知标准 CDP smoke 曾 300 秒超时，必须取得实际完整新结果才能提升该门槛。不要编辑正式工具跳过 CDP 或打开产品 devtools 来替代验证。

准备器为保持字节来源，对单次 git archive 固定 `core.autocrlf=false`、`core.eol=lf`；不会改用户 Git 配置。native 副本只针对原 p3-installation.mjs 的固定 SHA 与唯一完整区段；输入变了会拒绝，需先审查新输入，不能删哈希校验。

将新 run 收集到新的续接任务；collect-sandbox-evidence.py 要求确切 run-ID、匹配 launch/manifest 和真实 guest 身份，复制字节不覆盖已有 collected run。当前归档的原始绝对路径可能指向归档前活动目录；可按同名 research/verification 相对路径映射。

## 外审重试

本轮所有完成调用的真实状态见 review.md 与 external-errors*.json，空正文/429/exit1 不通过。服务恢复后，在新的续接任务中复制 runner 与 capture-review.py，使用新的 stage 前缀捕获当前 P0–P3 完整源码、必要工具和锁文件哈希，生成两份 prompt。必须同时启动两路：

```powershell
pwsh -NoProfile -File '<新任务>/verification/run-claude-pair.ps1' -Stage '<新stage>' -TimeoutSeconds 300
```

runner 通过两进程同时启动、真实 stdin、隐藏窗口、截止时间与 Job 清理执行指定 wrapper。按各 stderr 的确切 Session-ID 定向取 API 错误；不读取其他会话或导出原始会话。得到可读报告后处理实际发现并复审，不能仅凭 exit0 判定通过。本地独立审查也不替代外部两路。

## 将当前源码带到干净 runner

verification/source-bundle*.json 记录本地 Git bundle 的 commit、SHA256、大小、前置 baseline 与 verify 结果。bundle 留在 ignored target，未上传；包含提交而非当前用户未提交计划/图片。到已具备 baseline `5899b5fe0f7d507aa65102b3a80b7e69d8aa4565` 的全新 clone 后，核对 bundle 哈希，git bundle verify，再从 bundle 的 HEAD fetch/checkout 到记录的确切 commit。不要对当前用户工作区做 reset/clean。

GitHub 上实际查询当前本地源码曾返回422，远端 main仍为5899b5f，旧成功 runs不包含迁移。不 dispatch 旧来源工作流。将来安排当前提交的 CI 时，使用不发布的触发条件，先验证测试→构建→完整解码→Debug/Fake→Release/Fake→安装卸载的失败传播、runner原有 Job 下的嵌套行为，并保存完整日志/产物哈希。真实证书成功路径、缺WebView2在线安装仍需相应条件；本轮没有上传、签名、触发CI或发布。

P4 全 Win10/Win11 矩阵与 P5 默认入口切换/Electron删除继续后置，本轮没有扩大到这些阶段。
