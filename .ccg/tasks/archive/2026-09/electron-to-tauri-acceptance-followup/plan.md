# 续接计划

复杂度 L，风险 high，范围是 P0–P3 审查与验收。父任务不标记完成。

1. 固定入场 HEAD、索引、保护文件和 P3 产物/源码来源。保存本轮证据，不覆盖旧档案。
2. 启动两路外部分析；并行委派两个边界明确的只读研究：CI/隔离验收入口，P0–P3 审查范围和既有证据。研究不能替代外部审查。
3. 依据分析核实已存在的 Windows Sandbox/VM/专用用户和远程 runner 条件。优先只读调查，不创建账号、不更改系统虚拟化、不上传源码、不触发发布。
4. 重新捕获当前源码，分别执行 P0/P1/P2 与 P3 两路外审；对可复现发现先核实再做范围内修复，必要时双路复审。
5. 并行交付相互独立的验证工作：产物完整性和当前源码/CI 门槛研究；本地只执行允许的只读包核验和离线测试。满足实际隔离条件才执行 release/安装验收。
6. 若干净环境不具备，完成可复查的离线转交材料、准确命令和证据归集方案；记录实际阻塞，不把静态检查当 CI 或 release 通过。
7. 进行独立本地复核；核对保护文件和索引。更新父任务中的待补审/待验收状态，保存本轮实际结果与待办。
8. 检查是否有需要沉淀的项目经验，归档本轮并只提交明确选定的文件，不 push。

## 文件归属

必要修复已纳入范围：`desktop/scripts/p3-smoke.mjs` 的 release 编译模式探针不能依赖完整参数明文。真实沙箱已复现旧检查失败，同时原生探针 exit 4；独立本地反汇编确认 LLVM 把 19 字节比较拆成重叠 16 字节常量。只修复验收工具，保留 Debug 前置拒绝、release 权限/新 profile 检查，并在 probe 前声明目录归属、在已验证进程树退出后清理。产品二进制与安装包继续复用 P3 原始哈希。

实际决策：双路分析均 exit 1 且无报告，P0/P1/P2 正式补审同样没有报告；不能作为通过。已启用的 Windows Sandbox 实际启动探针通过，独立 WDAGUtilityAccount、无 Python、无业务目录、无 WebView2。使用专用只读输入映射和证据输出映射推进真实隔离验收；不改变主机用户/系统功能。远端 HEAD 缺失，本轮不 dispatch 旧来源的 CI。委派分析/实施因服务传输错误或模型限流中断，根代理接手；不将失败委派写成独立审查通过。

- 根代理：任务元数据、外审 runner/快照/prompt/调用结果、环境调查、实际验证、必要修复集成与最终归档。
- 子代理 ci_gate_research：只允许写本轮 research/ci-gates.md，调查工作流与隔离验收入口。
- 子代理 review_scope_research：只允许写本轮 research/review-scope.md，检查当前源码审查范围、证据可复查性及遗漏。
- 需要其他实施/复核子任务时再次明确文件所有权。所有代理不修改用户归档计划、不运行 release、不提交、不再 spawn。

## 隔离验收后的必要补充

- 使用 task-only 原生安装副本补验 NSIS/portable 的进程生命周期、卸载及数据保留；标准 CDP 失败仍保留，partial success 不提升 P3/CI 门槛。
- git archive 固定本次命令 core.autocrlf=false、core.eol=lf 并提前核对导出字节；不改用户 Git 配置。7-Zip 25.01 实际 BadCmd=13，按旧 P3 证据准备 NanaZip 6.5 独立解码器，保留拒绝不完整解析的门槛。
- 独立本地审查发现既有 GUI smoke 清理路径 W1：supervisor 未验证退出时仍移除 profile。将这一必要验收工具修复委派 native_installation_probe，仅可写 desktop/scripts/p3-smoke.mjs、desktop/tests/p3-smoke.test.mjs；保留原始失败、要求 verified cleanup 后才删除，测试只用临时合成目录。根代理负责最后完整 Desktop 回归、独立复审及双路外审尝试。
