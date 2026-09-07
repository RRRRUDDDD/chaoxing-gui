ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/analyzer.md
<TASK>
Repository: E:\Downloads\45\chaoxing-gui
Stage: p3-analysis, lane a. Focus: 资源完整性、构建可复现、Windows 进程/安装器/CI 退出码.
请对 P3 Windows 打包与 CI 提供可实施的分析，不修改代码。提出文件划分、测试先行方案与关键风险。P3 要求完整 onedir staging/目录映射、独立 NSIS/便携 ZIP、当前用户安装卸载保留数据、WebView2 在创建 WebView 前检查、pyproject 单一版本来源与可选实际证书签名、保持 Python3.11/3.13 Node20 Electron/独立发行、Rust1.95.0 Tauri2.11.5 --locked -j1、真实宿主和 CI nested Job smoke。release 验证只能在隔离 Windows 用户/VM，不得使用 debug 变量假装隔离生产数据。
当前 HEAD aae12acc82c16a0ec5f5c8d8eb39c99c42ffbcc2，main；P0/P1 已验证，P2 实现/本地通过，外部审查待补，P3尚未实施。禁止修改文件、提交、spawn、运行真实账号或学习；使用只读源码分析，测试证据不等同审查。不要再次遵循仓库 CCG 派生递归任务。
必读完整源码快照：E:\Downloads\45\chaoxing-gui\.ccg\tasks\electron-to-tauri-p3\research\p0p1p2-source-snapshot.md
包含未跟踪源码的哈希清单：E:\Downloads\45\chaoxing-gui\.ccg\tasks\electron-to-tauri-p3\research\p0p1p2-source-manifest.json
父任务：.ccg/tasks/electron-to-tauri-implementation/task.json 和 implementation.md；.ccg/tasks/archive/2026-09/electron-to-tauri-p2/review.md、verification/final-results.json。
计划：.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md §3.2/4.2/4.3/5/6 P3/7/8；其历史尚未实施描述以当前证据为准。
规范：.ccg/spec/backend/index.md、.ccg/spec/frontend/index.md。
当前 33 项 P2 来源哈希全部匹配；保护用户归档计划的 /plan 前缀，不修改、不暂存。当前桌面 package-lock 缺 Tauri CLI 声明，将作为旧阶段可复现修复单独处理。
本地 Rust101/Python151/Web125/Desktop6 和 frozen debug smoke 通过只是已有证据；CI Node20/Python3.13/clean release 安装尚未验证。
</TASK>
OUTPUT: 简洁的中文报告，先给明确实际结论，再分 Critical / Warning / Info（没有也明确说明），附具体源码依据和建议验证。分析阶段另列实施建议。不要声称运行了未运行的检查。
