# P2 范围与验收

续接 HEAD d4ae08f 的未提交 P0/P1 实现，仅实施 P2。主任务为 `../electron-to-tauri-implementation`，不得标记 P3–P5 或整体迁移完成。用户在归档 plan.md 添加的 `/plan` 保留且不得提交。初始文件副本、SHA256 与 diff 见 verification/baseline。

## 契约

- 浏览器与 Electron 保持 HTTP；Tauri 使用官方 `isTauri()` 检测、有限 operation 的 invoke adapter。保留 409/404、30s、AbortSignal、Axios 单次 JSON transform；无 `/start` 自动重试。请求取消须覆盖早于登记、进行中、迟到响应、窗口关闭。
- 四会话命令返回 v1 `version/login/activeTask` 数据；`rememberTask(null)` 仅清任务；IO 错误可见，桌面 bridge 错误不得落入 localStorage。保持严格 schema、账号匹配、串行写入和 Windows 原子替换。
- 启动页轮询 backend_status，只有 Ready 才挂载业务 App；Failed/Stopped/状态查询失败显示可理解错误。重试只查询状态，不启动后端或学习任务。启动中关窗和就绪后后端异常退出均可观测。
- 旧数据仅使用人工构造的隔离 fixture 验证；白名单复制、拒绝 reparse point、有效新数据不覆盖、旧版运行时推迟、复制/发布中断可重试、旧目录不变、无密码新增；不操作真实账号。
- P0/P1 两路 Claude 审查重新调用且有截止时间，旧报告不是本轮通过依据；本轮 P2 也须双路审查。失败保留原始结果并继续独立工作。

## 测试顺序

先列成功/错误/取消/退出验收，再写失败用例；先假后端与模拟业务，再真实冻结后端（无上游账号）。单测覆盖转换、409 恢复、404 清理、日志 after 去重/终态补拉、持久化失败与退出竞态。真实 Chromium/Electron/Tauri 窗口执行同一模拟流程，另测隔离数据导入/回滚。最后运行 Web、Electron、Python、Rust fmt/check/clippy/test 与 web build。

## 已读上下文

- 父任务 task.json、implementation.md 与 P0/P1 verification（根目录仅这两份记录，未发现 plan.md 或第三份顶层文档）。
- 归档迁移计划 3.2/4.2/5/6(P2)/7/8/9；P1 批准计划 `C:/Users/RUD/.claude/plans/abstract-gathering-adleman.md`。
- `.ccg/spec/backend/index.md`、`.ccg/spec/frontend/index.md`；仓库没有 AGENTS.md 文件，遵循用户提供的 CCG 指令。

## 提交边界

本阶段新增代码可直接提交；接手前已有改动/未跟踪 P1 文件必须保留，不能把它们冒充本轮改动整包提交。必要的 P1 修复保留逐文件基线差异证据，归档提交仅包含本阶段拥有的变更和阶段记录。父实施任务继续记录 P2 结果与后续阶段，不归档整体迁移。
