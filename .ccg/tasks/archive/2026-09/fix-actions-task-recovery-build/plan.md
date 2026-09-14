# 修复计划

## 证据

- 失败步骤为独立 PyInstaller 程序的启动 smoke；前后端测试和 exe 构建均已成功。
- CI 的固定 20 秒 start 超时覆盖 PowerShell 启动、Add-Type 编译辅助 C# 和实际 CreateProcess，失败证据没有捕获进程身份，也没有程序 stdout/stderr 文件。
- 下载该 run 上传的同一 exe，未经重建即在本机通过健康检查、静态页面、配置和 stdin EOF 进程树退出检查。云端辅助进程初始化变慢的具体外部因素尚不可从原始日志区分。

## 文件范围与实施

1. `desktop/tests/p3-smoke.test.mjs` 和新的 `desktop/tests/fixtures/p3-supervisor-initialization.mjs`：先验证慢初始化不消耗被测进程启动预算、初始化失败不发送启动命令、无效协议被拒绝及真实启动超时仍生效。
2. `desktop/tests/fixtures/p3-windows-process.ps1`：Add-Type 完成后提供明确初始化握手，记录辅助进程初始化阶段。
3. `desktop/scripts/p3-smoke.mjs`：独立等待初始化最多 60 秒，收到握手后才发送 start；实际启动保持原 20 秒预算。输出阶段耗时和错误，沿用现有进程清理和失败判定。

## 验证与交付

- 先观察新回归在旧实现上失败，再运行修复后的相关测试和 Desktop 完整测试；使用 CI 同版本 Node 20 验证。
- 用 CI 原始 exe 再跑独立程序 smoke；检查错误路径、无子进程残留和改动范围。
- 通过隔离 worktree 正常推送修复，跟踪新的 GitHub Actions 至结论。测试辅助脚本改动不能代替云端完整构建结果。
- 本地归档保留文本证据，避免将下载和复制的巨大 exe 加入 Git 历史。

## 完成记录

- 4 项新回归按预期先失败后通过；Node 20.20.2 的桌面完整测试 101/101 通过，原 CI exe 的两次本地 smoke 通过。
- 修复推送为 `057ed25df913753843ce5e8aa5ebf50595d20bb0`。
- 新 run 34857176177 四个 job 全部成功，涵盖编译、全部打包、真实 GUI 和安装卸载验收；四份 artifact 已上传。
- 文本日志、JSON 结果和二进制来源哈希归档；本地 exe 副本保持忽略，不加入归档提交。
