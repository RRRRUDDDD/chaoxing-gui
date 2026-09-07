# P3 复现与继续验收

从仓库根目录执行。完整迁移源码已由P1基础`9219fe2`、P2整合`0d224f3`和P3`6b2c5a2`组成；不能仅检出原P2前端提交。准备Windows、PowerShell7、MSVC/SDK、Rust1.95.0、Python3.11构建依赖、PyInstaller6.21.0、Node20和近期7-Zip。Python3.13回归另用独立环境。

## 构建与基础验证

```powershell
. ./desktop/scripts/dev-env.ps1
function Invoke-P3 {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
Invoke-P3 python @('desktop/scripts/version.py', '--check')
Invoke-P3 npm @('--prefix', 'web', 'ci')
Invoke-P3 npm @('--prefix', 'web', 'test')
Invoke-P3 npm @('--prefix', 'web', 'run', 'build')
Invoke-P3 npm @('--prefix', 'desktop', 'ci')
Invoke-P3 npm @('--prefix', 'desktop', 'test')
Invoke-P3 python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
Invoke-P3 python @('-m', 'PyInstaller', '--clean', '--noconfirm', 'chaoxing-backend.spec')
Invoke-P3 pwsh @('-NoProfile', '-File', 'desktop/scripts/build-tauri.ps1', '-Prepared')
```

最后一个入口重新staging整个后端，运行Rust fmt/check/clippy/test/release、强制真实NSIS路径夹具以及NSIS/ZIP完整内容、版本、签名状态检查。Cargo命令均`--locked -j 1`；不要并发Cargo，本机曾触发paging-file1455。所有PyInstaller spec/work/dist必须与仓库同盘。不要改掉原spec的console、排除项或Flask内web/dist。

本地最终完整Desktop用Node20.20.0执行`node --test --test-concurrency=1`（cwd=desktop），并设置`CHAOXING_REQUIRE_NSIS_PATH_TESTS=1`，86项全部通过。CI直接使用Node20；本轮统一release构建入口实际使用系统Node24.14.0，见toolchain-final.json，不宣称本机等于CI。

## 实际debug宿主与冻结后端

```powershell
$p3Evidence = Join-Path $PWD ('desktop/src-tauri/target/p3-rerun-' + [Guid]::NewGuid().ToString('N'))
$env:P2_EVIDENCE_DIR = $p3Evidence
Invoke-P3 python @('-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'p2-backend',
    '--specpath', 'desktop/src-tauri/target/p3-fixture', '--workpath', 'desktop/src-tauri/target/p3-fixture/build',
    '--distpath', 'desktop/src-tauri/target/p3-fixture/dist', 'desktop/tests/fixtures/p2_backend.py')
Invoke-P3 cargo @('build', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '-j', '1',
    '--features', 'custom-protocol', '--bin', 'chaoxing-desktop')
Invoke-P3 pwsh @('-NoProfile', '-File', 'desktop/scripts/smoke-tauri.ps1',
    '-HostPath', 'desktop/src-tauri/target/debug/chaoxing-desktop.exe',
    '-BackendDirectory', 'desktop/src-tauri/resources/backend',
    '-FakeBackendDirectory', 'desktop/src-tauri/target/p3-fixture/dist/p2-backend',
    '-EvidenceDirectory', $p3Evidence, '-Configuration', 'Debug', '-Scenario', 'All', '-NestedJob')
```

顺序是fake业务/缺资源/坏exe/取消/启动中退出/后端死亡/强杀，再真实冻结后端的隔离配置、空输入400、缺任务404；不访问真实账号或上游学习任务。普通cargo gates可能重建不嵌Web的debug主程序，GUI前需显式重建custom-protocol版本。不要把当前用户release宿主当作可由debug环境变量隔离。

本轮实际通过的debug宿主副本与完整原始报告留在ignored大型fixture目录中；便于Git复查的结果副本是verification/debug-host-final.json。复用P2工具前读旧P2/reproduce.md，并始终设置新的P2_EVIDENCE_DIR；不要覆盖旧归档。

## 只读包检查

```powershell
Invoke-P3 pwsh @('-NoProfile', '-File', 'desktop/scripts/verify-package.ps1',
    '-PackagePath', 'desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip')
Invoke-P3 pwsh @('-NoProfile', '-File', 'desktop/scripts/verify-nsis.ps1',
    '-InstallerPath', 'desktop/release/tauri/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe',
    '-PortablePath', 'desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip')
```

三个sidecar必须与产物一起保留。检查使用近期7z；Electron所带7-Zip16.04不能完整解析NSIS3，本轮已实测拒绝。`verify-nsis.ps1 -SevenZipPath`可指定解码器。只检查内容，不执行产品安装器/宿主。

## 未执行的release验收

以下只在fresh GitHub Windows runner或确认可丢弃的Windows用户/VM中运行；本轮没有在RUD用户执行。标准入口为workflow中的release fake smoke和`smoke-installation.ps1`。后者验证junction拒绝、解压后与安装后完整清单、真实冻结host、nested Job、卸载保留合成业务数据以及原Electron哨兵。专用VM/用户在命令上显式传`-DisposableWindowsUser`；GitHub-hosted Windows runner自动识别，仍要求profile与安装基线为空。

缺WebView2需在干净系统验证原生前置提示和在线bootstrapper；离线机器预装Microsoft Evergreen Standalone x64，便携Install-WebView2入口验证Microsoft签名后才执行。详细使用说明见desktop/README.md与desktop/portable/README.txt。

有真实代码签名私钥后运行sign-windows Inspect并选择证书，再完整重建/验签/安装验收。不要为了令门槛变绿自行创建信任证书。

## 外部审查重试

归档的verification/run-claude-pair.ps1通过Git寻找仓库根，归档后仍有效。为重试建立新的活动任务及research输出目录，复制runner与capture-review.py；重新生成当前源码快照和两份prompt，以新的Stage名称启动。不得覆盖本轮或旧P2的prompt/stdout/stderr/result证据。两路都必须产生可审阅正文并处理实际发现，exit1/429/空输出不是通过。

原始证据内部绝对路径记录的是归档前位置`.ccg/tasks/electron-to-tauri-p3/`。归档移动为`.ccg/tasks/archive/2026-09/electron-to-tauri-p3/`，内容保持原样；复查时按此映射定位。大型fixture、二进制和WebView profiles不进入Git，明确提交的证据清单见archive-selection.json。
