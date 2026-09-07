# P2 实施计划

## 分析结论

P0/P1 的既有验证不重做。本轮两路外部分析兼 P0/P1 审查于 2026-09-07 12:42:57Z 并行启动，每路 300s 上限；分别运行约 186s/192s 后 exit 1，无正文，不能视为通过。原始结果见 research/analysis-p0p1-{a,b}.*。依用户授权继续可独立完成的实现，交付前再双路审查并如实保留限制。

本地核对确定的 P2 衔接修复：API 注册名与 ACL 不同（_cmd）；同步启动和同步网络请求阻塞事件循环；cancel-before-register 被丢失且读取响应后未复查取消；session 返回 void、缺 clear-task、缺必填 null 键、吞 IO；migration 非整目录原子发布、只检查会话而非所有新数据、真实 Electron 运行检测不足。Python Tauri 的 CORS 声明与注册不符，纳入小范围协议修复。

## 固定 IPC 契约

- `api_request({request: {operation, payload, requestId, taskId?, after?}})`；operation 保留 Rust camelCase (`configRead/configWrite/taskStatus/taskDetails/taskLogs`)，八操作；requestId 为 JS safe positive integer、页面内唯一。
- `api_cancel({requestId})`；取消只终止等待，不能撤回已执行 start。host 处理早取消、重复 ID、有界登记、停止与迟到结果；网络读移到 blocking worker，事件循环仍能处理 cancel/status。
- `backend_status()` 返回 phase/error（不向 UI 暴露 token；无需 port）；status 查询及重新检查不会 spawn/restart 后端。
- `session_read()`、`session_remember_login({username})`、`session_remember_task({task: {username,taskId} | null})`、`session_clear()` 均返回完整 v1 会话，所有必需键显式存在，IO/校验失败 reject。
- 自定义命令仅 main 本地页面；顶层参数与嵌套 DTO 严格校验，现有七项 ACL 名称保持。CSP 禁止 frame/object，导航限制 local main URL。

## 文件归属与并行步骤

所有实施者 `fork_turns=none`，不再 spawn、不提交、不覆盖他人；阅读完整文件后测试先行。改动范围外先联系主代理。

| 所有者 | 唯一文件范围 | 成功/错误/取消/退出验证 |
|---|---|---|
| 前端代理 | web/src/api/tauriAdapter.js 与 tests、axios.js、web/src/lib/desktopBridge.js 与 tests、sessionStore.js/tests、web/src/components/DesktopStartup.jsx/tests、web/src/main.jsx | 所有 operation、409/404、transform、30s/AbortSignal/迟到、日志游标、存储失败无 fallback、启动/Ready/Failed/重新检查/卸载；原有流程测试保持 |
| Rust 生命周期代理 | desktop/src-tauri/src/backend.rs、api_proxy.rs、src/bin/fake-backend.rs、tests/backend_lifecycle.rs 和专属新测试 | 早取消/进行中/迟到/重复 ID/响应上限、启动中关闭、就绪后异常退出、stop 幂等与进程树；保持无 start 重试 |
| 导入代理 | desktop/src-tauri/src/migration.rs 与独立 migration 测试 | 整目录发布、保留所有新数据、源/目标 reparse point、损坏/超大会话、复制与发布中断后重试、真实旧 Electron singleton 检测、原目录哈希不变 |
| 主代理 | session_store.rs、lib.rs、Cargo/config/capabilities（必要范围）、web/package*.json、Python CORS 小修复与相关测试、desktop/tests/p2-* 测试工具/fixtures、阶段记录 | Node/Rust v1 双向兼容、锁/原子替换/IO、IPC ACL 与非阻塞接线；隔离 profile 真 Chromium/Electron/Tauri 全流程、真实冻结后端；统一全量质量门 |

1. 先运行新增失败测试并保存输出，随后实现对应代码。代理之间仅依赖上述契约；主代理在集成层适配。
2. 基础通过后运行模拟后端三宿主场景：登录→按账号选课/保存→start 409恢复→终态日志补拉与去重→刷新恢复→404清任务→退出；存储失败提示单独验证。
3. Tauri 开发 profile/backend 路径通过 **仅 debug 编译启用** 的显式环境参数注入；同时隔离 WebView2、业务数据、日志、legacy 路径；生产默认仍为稳定 identifier 数据路径。真实账号目录不可用作 fixture。
4. 假后端故障注入先通过，再运行真实冻结后端 config 读写/404/输入错误/关闭无残留；不调用真实上游、不启动学习。
5. Cargo fmt/check/clippy/test、Python unittest、Web/Electron tests、web build 统一运行；Windows GUI/IPC 不能以单测代替。
6. P2 双路外部审查有界调用，核对并修复发现；无报告时记录未审查，不能称整体发版门槛通过。
7. 更新父任务 P2 实测状态与 P3 下一步；必要经验回写 spec；阶段独立归档，仅提交本轮所有权清晰的变更。初始 P1 未提交文件如被衔接修复，保留基线与差异记录，不能整包提交冒充 P2。

## 完成边界

本轮目标是 P2 实现与本地验收。P3 打包/CI、P4 干净 Win10/11 与权限矩阵、P5 默认切换仍待实施；如任何 P2 实测或双路审查未过，记录具体未通过项而不标记全部完成。
