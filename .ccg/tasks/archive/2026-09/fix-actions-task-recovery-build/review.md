# 审查与验证

## 原始失败

- Run 34852907745 的前端及 Python 3.11/3.13 测试通过，独立 PyInstaller exe 构建通过，失败于随后 smoke 的 `Supervisor start timed out`。
- 原 20 秒 start 预算包含 PowerShell/.NET 初始化和 Add-Type 编译；原证据没有进程身份和程序输出文件，初始化期间也没有阶段日志，无法从旧日志进一步区分外部延迟因素。
- 原 CI exe SHA256：`ACA3C6165586245509040B1938BBCFD5B23A9562933CC434A680E9932EFC1223`。未重建，在本机旧脚本和修复后脚本下均通过独立程序 smoke，因此没有证据表明 exe 编译损坏。

## 修复审查

- Critical：无。
- Warning：无未解决项；已取得云端完整构建成功结论。
- Add-Type 完成后用 initialize / protocolVersion 1 握手确认测试监管进程就绪；独立初始化预算 60 秒，实际 CreateProcess 启动仍为原来的 20 秒。
- 初始化未成功时不发送或预排队 start；初始化失败和程序启动失败仍抛错并回收测试监管进程，未取消任何 Job、EOF、来源、权限或清理验证。
- 记录初始化阶段、耗时、程序启动耗时和错误，后续可以区分初始化延迟和被测进程启动故障。
- 实际 C# 进程所有权和终止逻辑未修改；只有 4 个 smoke 实现/测试文件进入产品提交。
- 本地 backend spec 补充打包验收中初始化与程序启动分阶段计时的约定；用户原有 frontend spec、旧任务 plan 和截图保留。

## 本地验证

- 新增 4 项初始化协议回归在旧代码上全部失败，修复后全部通过；覆盖慢初始化、初始化超时、无效协议和程序启动超时。
- Node 20.20.2 的 Desktop 完整测试：101/101 通过，见 `desktop-node20.log`。包含实际 Windows Job、stdin EOF、超时树清理以及中文窗口标题回归。
- 使用原 CI exe、Node 20.20.2 和修复后 PowerShell wrapper 完成独立程序 smoke：健康接口、打包前端、配置读写、空参数/缺失任务校验及 stdin EOF 退出全部通过；清理报告 verified=true、fallbackUsed=false、remaining=[]。
- 修复后初始化日志：初始化 1071ms，捕获程序启动 124ms；原问题的两个阶段已独立记录。
- 本地下载/复制的 exe 保留为忽略文件，归档提交仅显式暂存文本证据，避免将大型二进制加入 Git 历史。
- JavaScript 语法检查和 `git diff --check` 通过。本次不修改应用源码或 Rust，沿用已通过的应用回归，不重复无关测试。

## 云端验证

- 本地修复提交 `1be45ec078e79e77c37af3c249195936735e0307` 已在远程发布历史整合为 `057ed25df913753843ce5e8aa5ebf50595d20bb0`，所有产品文件内容一致，普通快进推送成功。
- 新工作流：https://github.com/RRRRUDDDD/chaoxing-gui/actions/runs/34857176177 ，状态 completed，结论 success，四个 job 全部通过，完整 API 记录见 `cloud-run.json`。
- 前端、Desktop、Python 3.11/3.13 测试、原失败的独立程序启动 smoke、冻结后端启动 smoke、Electron 打包、Rust fmt/check/clippy/test、Tauri 发布打包和 NSIS/portable 内容校验全部通过。
- 真实 Debug/Release 桌面故障及业务 smoke、冻结后端接口、安装卸载及便携版运行验收通过；临时 WebView2 调试策略清理通过。
- 四份 artifact 已上传：独立 Python 包、Electron 包、Tauri 包和完整验收证据，清单见 `cloud-artifacts.json`。本次普通 main push 未发布或改写 GitHub Release。
- 仅产品修复推送远端；任务归档保留本地。临时推送 worktree 清理，用户原有三个工作区改动保留。
