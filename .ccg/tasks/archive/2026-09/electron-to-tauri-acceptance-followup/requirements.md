# P0–P3 外审与隔离验收续接

本轮从 37fde9160229e99d2fa837d2f1a850203c260d81 续接。P1/P2 基础和 P3 源码均已提交；只补审与验收及必要的范围内修复，不重做实施、不应用旧补丁。

## 必须完成的本轮工作

- 为续接先做两路外部分析，再分别补 P0/P1/P2 和 P3 两路外部代码审查。使用指定 codeagent-wrapper 的两个并行 claude 调用，保留当前源码清单、完整 prompt、stdout、stderr、退出码和时限。空正文、429、超时均不算通过。
- 独立检查能否使用已存在的可丢弃 Windows 用户/VM 或干净 Windows runner；在符合条件时执行 release fake/真实冻结后端、NSIS 安装卸载、portable、nested Job 和 CI 门槛。
- 在本机可做只读产物核验、源码/CI 静态检查及隔离的离线回归；只有新发现或当前证据不足时才重复重型测试。
- 交付实际结论、证据和具体待办；环境缺失或权限不足如实列为未验收。
- 完成当前可执行范围后归档本轮任务并提交明确选定的任务文件；父迁移任务和整体迁移保持未完成。

## 保护与运行边界

- 用户归档计划 `.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md` 不修改、不暂存。入场 SHA256 为 `7c926423ae0a4e3643fbf54f2dd68d2e1899ebae0817ac23ea99d641151a2bab`，与旧交接哈希不同，保护当前字节，不恢复旧版。
- `poc-window.png` 保留，入场 SHA256 为 `282e312479ea72fc33b72211ec2404fd84a5dda7bc5803cdc44920c3465dce87`。
- 入场索引为空，main 比本地 origin/main 引用领先 9 个提交。远端实际来源需要另行只读核实。
- 不 push、tag、发布、使用真实业务账号或运行学习任务。不读取真实 AppData 业务文件。
- 不在 RUD 用户下执行 release 宿主或产品安装器，不用 debug 变量伪装 release 隔离，不创建受信任证书。
- 不清理上轮被自动审批拒绝的临时目录，不覆盖旧档案或证据。
- 仅 P0–P3；存在的其他任务（含 P4）保持原样。
- Cargo 只串行执行，`--locked -j 1`；PyInstaller spec/work/dist 同盘；保留原 console、依赖排除及 Flask 内 web/dist。

## 参考

父任务的 task.json、implementation.md、verification/p3-final-handoff.json；P3 档案的 delivery.md、review.md、reproduce.md；.ccg/spec/backend/index.md 和 frontend/index.md 已读。
