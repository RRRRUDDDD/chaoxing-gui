# 续接验收交付

本轮续接完成了范围内修复、Node 20 回归、真实 Windows Sandbox 原生安装补验、静态包检查、外审重试和证据归集。P3 acceptance 与父迁移仍未完成。

## 源码变更

- `12bae228` 修正 Release 优化宿主的 `--check-debug-build` 探针：Release 仅在 disposable profile 中运行原生退出码探针，保留 Debug fail-closed、权限、ownership 和 Job 清理门槛。
- `bbdbbc2` 修正 profile 清理顺序：`removeProfilesAfterVerifiedCleanup()` 只有在 `verified=true`、`remaining=[]`、`observed` 非空且所有捕获进程已死亡时才删除本轮 profile；probe、GUI 启动失败、正常关窗和强制关窗均接入。主失败和清理失败通过 `withCleanup` 同时保留。
- 受影响源码仅为 `desktop/scripts/p3-smoke.mjs` 和 `desktop/tests/p3-smoke.test.mjs`，当前 HEAD 为 `bbdbbc218416a4f458f9b03bb1a3da5f02451f45`。

## 已验证

- Node 20.20.0 Desktop 回归：`90 PASS / 0 FAIL / 0 SKIP`；清理失败、存活进程、错误 run/SID 和异常聚合 fixture 均覆盖。证据：`verification/node20-desktop-cleanup-final.log`。
- Windows Sandbox guest：真实 `WDAGUtilityAccount`、Virtual Machine、Windows build 26100、无系统 Python、初始 product profiles 为空；缺 WebView2 原生/portable 入口均 exit 3，微软签名离线 runtime 安装成功，安装后原生检查 exit 0，Release probe exit 4。
- `NativeInstallationPartial` run `run-509f5e36e42744d6813cc023ef166ba0` 成功完成 16 项原生安装范围检查：三处 junction 拒绝、portable/installed manifest 前后校验、原生 Release probe、正常/强制关闭、captured Job/PID/creation FILETIME 清理、卸载注册表清除、保留 7 个合成 Tauri/Electron 数据和旧程序哨兵。该结果的 `fullAcceptancePassed=false`、`formalReleaseSmokePassed=false`。
- RUD 主机只做了 NanaZip 解码器下的只读 NSIS/portable 内容校验，未执行产品安装器或 Release 宿主；产物保持未签名。
- Git bundle 对应 `bbdbbc2`，验证通过，未上传；远端 main 仍为 `5899b5f`，远端找不到当前提交，本轮没有 dispatch、push 或发布。

## 未通过或未完成

- 标准 Release Fake GUI smoke `run-504ac0d14d084dc19e44a7c97924cff7` 在真实 Sandbox 中 CDP 连接 300 秒超时并以 `fetch failed` 结束。fallback 清理不能算正常生命周期或业务通过；CDP 根因尚未确定，未为通过而开启 shipped devtools 或放宽 Release 边界。
- 既有 P0/P1/P2 与 P3 双路外审均无可审阅正文；历史调用为 429，最新 closeout 两路为 403 authentication_failed。任何一次都不计为通过。
- GitHub Actions 没有当前源码提交的运行；已有成功 runs 属于迁移前提交。真实代码签名成功路径、签名产物/安装验证、完整 Win10/Win11 矩阵、干净 runner nested Job 与完整 CDP/IPC/前端业务验收仍待条件具备。

## 保护边界

用户归档计划 `.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md` 未修改、未暂存，SHA256 为 `7c926423ae0a4e3643fbf54f2dd68d2e1899ebae0817ac23ea99d641151a2bab`；`poc-window.png` 保留，SHA256 为 `282e312479ea72fc33b72211ec2404fd84a5dda7bc5803cdc44920c3465dce87`。没有 push、发布、真实账号或真实学习任务；父任务继续 `in_progress`，本轮未进入 P4/P5。
