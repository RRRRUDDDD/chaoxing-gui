# Independent local review of 12bae228

审查代理：`/root/followup_local_review`（ccg-review，fork_turns=none）。首次因传输错误中断，没有报告；一次恢复后形成以下报告。代理角色禁止写任务目录，本文件由根代理根据完整最终回复落盘。审查没有运行产品、安装器、release、Cargo、测试或访问业务 AppData；没有修改、暂存或提交源文件。

审查对象：`12bae2286bec71bf184f19c8be67e6431199e134` 的 `desktop/scripts/p3-smoke.mjs`，结合同文件全部上下文、原生 Job 实现与安全测试源码。

## Critical

未发现探针补丁新增的 Critical。新增 Release 探针保留权限检查、profile 预检、捕获进程树验证和失败传播。该结论不是整个 P3 通过。

## Warning W1 — 既有 GUI 清理路径缺少进程验证门槛

`runTauri.launch()` 在 owner.dispose() 失败后将异常转换为 `{ error }`，随后仍调用 removeOwnedProfiles；`close()` 也会在 dispose 失败后继续删除 profile（此提交约 475、519–524 行）。若 supervisor 在最终状态报告前退出，未取得经过验证的进程树退出证据。所有权检查能限制删除对象，但不能证明目录已无人使用。

已核对父提交：W1 不是 12bae228 引入，新探针已经有相应门槛。上述路径仍会使整次 smoke 失败，不是整次结果假阳性。建议 GUI 删除路径同样要求 cleanup 验证成功。本次只读审查没有修复。

## Info

- RUD 禁止执行是操作约束，assertReleasePermission 没有用户名硬拒绝；它接受声明的 disposable Windows 用户或完整的 hosted Windows runner 条件。windowsContext 获取实际 SID/known folders。不能描述为任何参数下都拒绝 RUD。外层本任务 Sandbox guard 才硬性检查 WDAG/VM。
- 部分 profile claim 失败或启动没有捕获身份时，目录可能保留；失败路径不能宣称清理成功，需经验证的外层交接或销毁一次性环境。
- Debug 保留完整字符串预检；Release 使用原生退出码并重新校验权限。main 在 probe 前发布 ownership handoff；probe 在 start 前 claim。原生源码在 runtime/UI/backend/data 初始化前返回 Debug 0 / Release 4。本审查未独立反汇编；LLVM 常量拆分来自已有研究。
- Profile guard 覆盖两个 Tauri 根及三个 legacy 根。删除仍需 runId、SID、路径匹配并只处理 claim 根；拒绝已有非本轮数据与链接。
- 原生实现先挂入禁止 breakaway 的 Job，再恢复宿主。探针等待空 active/全部 observed 已退出，核对退出码；清理 profile 前要求 verified=true。withCleanup 保留主失败与清理失败，fallback 不能满足成功条件。
- compiled-host-profile-verified 是清理前的单项记录，最终须以 result.success 和退出码判断；CDP/cleanup 失败不能变成整次通过。

## Verification and limits

提交 diff --check 通过；Desktop 无 lint 脚本，本次 .mjs 无独立类型检查要求。引用根代理 Node20.20.0 的 `verification/node20-desktop-final.log`：86 PASS、0 FAIL、0 SKIP，没有重跑。现有测试涵盖权限、退出码、ownership、junction、超时传播和真实 Job 夹具，但未直接涵盖新 probe 的全部失败组合。

真实 Sandbox probe 退出与 profile 清理成功；完整 release GUI smoke 仍 CDP 300s 超时。审查时提供的外调八次均 429/空正文；之后新增外调结果由最终 review.md 单独统计。独立本地报告不替代外部审查或未完成的验收。
