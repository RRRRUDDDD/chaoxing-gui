#!/usr/bin/env bash
set -u
task_dir='.ccg/tasks/electron-to-tauri-migration-plan'
{
cat <<'REVIEW_A'
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
仅审查迁移计划文档。上一轮仓库扫描超时；下方提供完整新增计划的 git diff，请直接完成最终评审，不调用任何工具、不读文件、不运行命令、不创建Task、不再调用模型。角色为严格的代码/架构审查者。只找会导致错误实施的缺口，特别是 Tauri 2 权限、本地 React→Rust 代理→Flask 架构、Axios 409/404/取消/超时、旧会话兼容。不要重述计划，最多4个问题，中文800字以内，分 Critical/Warning/Info；无问题明确写无。区分已明确留待PoC的事项与真正缺失的设计，不要求现在实现代码。
已核实源码事实：当前 Electron 主窗口加载 Flask 动态127.0.0.1端口；四个窄会话IPC；renderer-session.json为纯账号/taskId的v1 JSON，不保存密码或safeStorage密文；前端全部业务请求集中Axios，依赖409、404、AbortSignal和after日志游标；TaskStore仅驻留Python内存；普通Web/独立exe必须保留。
<PLAN_DIFF>
REVIEW_A
cat "$task_dir/research/plan.diff"
cat <<'REVIEW_A_END'
</PLAN_DIFF>
</TASK>
OUTPUT: 中文 Critical/Warning/Info，引用章节或原diff行号，给出必要文档修正建议；这是规划评审，不是运行时验证。
REVIEW_A_END
} | CODEX_TIMEOUT=150000 timeout 180s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/review-retry-a.md" 2> "$task_dir/research/review-retry-a.log" &
review_a_pid=$!
{
cat <<'REVIEW_B'
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
仅审查迁移计划文档。上一轮仓库扫描超时；下方提供完整新增计划的 git diff，请直接完成最终评审，不调用任何工具、不读文件、不运行命令、不创建Task、不再调用模型。角色为严格的架构/发布审查者。只找会导致错误实施的缺口，特别是 onedir资源布局、Windows进程树/EOF/Job、握手、数据导入和回滚、NSIS/便携/WebView2、CI和阶段依赖。不要重述计划，最多4个问题，中文800字以内，分 Critical/Warning/Info；无问题明确写无。区分已明确留待PoC的事项与真正缺失的设计，不要求现在实现代码。
已核实源码事实：当前Python包是PyInstaller onedir/console=True，exe依赖同级_internal；app.py读取stdin EOF后os._exit(0)，不是优雅退出；Electron关闭会调stdin.end并安排延迟taskkill；logger和Tiku配置在模块导入时绑定cwd；旧数据目录用Electron app.getPath(userData)；现有安装包卸载保留AppData；CI还构建非Electron独立exe，必须保留。
<PLAN_DIFF>
REVIEW_B
cat "$task_dir/research/plan.diff"
cat <<'REVIEW_B_END'
</PLAN_DIFF>
</TASK>
OUTPUT: 中文 Critical/Warning/Info，引用章节或原diff行号，给出必要文档修正建议；这是规划评审，不是运行时验证。
REVIEW_B_END
} | CODEX_TIMEOUT=150000 timeout 180s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/review-retry-b.md" 2> "$task_dir/research/review-retry-b.log" &
review_b_pid=$!
wait "$review_a_pid"
review_a_status=$?
wait "$review_b_pid"
review_b_status=$?
printf '{"review_a":%s,"review_b":%s}\n' "$review_a_status" "$review_b_status" > "$task_dir/research/review-retry-status.json"
cat "$task_dir/research/review-retry-status.json"
if [ "$review_a_status" -ne 0 ] || [ "$review_b_status" -ne 0 ]; then exit 1; fi
