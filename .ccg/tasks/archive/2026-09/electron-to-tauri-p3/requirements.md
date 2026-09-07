# P3 范围与边界

接手 HEAD `aae12acc82c16a0ec5f5c8d8eb39c99c42ffbcc2`，main，索引为空。33 项 P2 交付哈希均匹配。P0/P1/P2 不重做实现；外部审查尚未通过。

- 将可核验的既有 P1 基础、P2 混合增量与 P3 新增分开提交；永不暂存/改写用户归档计划，保留 poc-window.png 等未纳入范围的文件。
- 完整 PyInstaller onedir staging，目录映射保留所有层级。保留 console、排除项及 Flask 内 web/dist；独立宿主/安装目录和 desktop/release/tauri 产物。
- 当前用户 NSIS、完整便携 ZIP；卸载保留业务数据、原 Electron 及回滚链。便携仍使用 AppData。
- 在线 bootstrapper 为 NSIS 默认；提供离线运行时安装说明/入口。裸宿主与便携缺 WebView2 时，必须在创建 WebView 前给出可执行提示。
- pyproject.toml 是版本来源，不升版本、不建 tag；校验 Cargo/Tauri/npm/locks/tag/artifact。只有实际可用证书才能签名，签名需验证。
- CI 保留 Python 3.11/3.13、Node 20、Electron 和独立 Python 发行，加入 Rust 1.95.0、Tauri 2.11.5、cache、fmt/check/clippy/test/release、内容校验及真实宿主 nested Job smoke。
- PowerShell 原生命令失败即失败；后台进程隐藏窗口、真实 stdin 管道、超时和 finally 清理。测试失败阻止后续发布。
- 不 push、不发布、不访问真实账号。release smoke 仅在隔离 Windows 用户/VM；本机 debug smoke 不冒充 release/干净系统。P4/P5 不在本轮范围。

四路外部调用（P3 分析两路、P0/P1/P2 补审两路）均 exit 1，无正文报告；实际错误另从对应 session 提取。继续可独立完成工作，最终还需 P3 双路审查。没有外部通过结论。
