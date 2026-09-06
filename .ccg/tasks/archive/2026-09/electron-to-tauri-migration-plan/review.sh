#!/usr/bin/env bash
set -u
task_dir='.ccg/tasks/electron-to-tauri-migration-plan'
git diff --no-index -- /dev/null "$task_dir/plan.md" > "$task_dir/research/plan.diff"
CODEX_TIMEOUT=300000 timeout 360s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/review-a.md" 2> "$task_dir/research/review-a.log" <<'REVIEW_A' &
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
用户只要求 Electron→Tauri 的 plan，本次仅文档。只读审查 .ccg/tasks/electron-to-tauri-migration-plan/plan.md 及 research/plan.diff（新增计划的 diff），必要时核对 desktop/session-store.js、web/src/lib/sessionStore.js、web/src/api/axios.js、web/src/lib/taskPolling.js、web/src/App.jsx、app.py 与 .ccg/spec/frontend/index.md。聚焦推荐架构、API adapter 的状态/取消/超时兼容、Tauri 2 command ACL、local/remote 权限、token/启动模式、会话迁移契约是否可实施，是否误读现有功能。不要因尚未实施 PoC 而报告已发生运行故障；这是计划，明确 PoC 门槛的假设可以接受。不要无关扩展业务范围。禁止修改文件、创建 Task、运行测试或访问真实服务，禁止调用其他模型/代理。最多列 6 个有证据的问题，中文 1600 字以内。引用 plan.md 行号；若没有 Critical 明确写无；区分 Critical/Warning/Info，给可直接修正文档的建议。
</TASK>
OUTPUT: 中文 Critical/Warning/Info 审查报告，关注会导致错误实施的缺口，标明这是文档审查，不声称执行代码测试。
REVIEW_A
review_a_pid=$!
CODEX_TIMEOUT=300000 timeout 360s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/review-b.md" 2> "$task_dir/research/review-b.log" <<'REVIEW_B' &
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
用户只要求 Electron→Tauri 的 plan，本次仅文档。只读审查 .ccg/tasks/electron-to-tauri-migration-plan/plan.md 及 research/plan.diff（新增计划的 diff），必要时核对 desktop/main.js、desktop/electron-builder.yml、chaoxing-backend.spec、build_desktop.bat、.github/workflows/main.yml、app.py 的启动定义、api/logger.py、api/answer.py 的路径定义与 .ccg/spec/backend/index.md。聚焦 Python onedir/resources、端口握手、stdin/Job/宿主退出、数据目录与备份回滚、WebView2/NSIS/便携、CI/版本与签名、阶段依赖/估算是否可执行。当前 README 有过期描述，以源码为准。不要因尚未实施 PoC 而报告已发生运行故障；明确 PoC 门槛的假设可以接受。禁止修改文件、创建 Task、运行测试或访问真实服务，禁止调用其他模型/代理。最多列 6 个有证据的问题，中文 1600 字以内。引用 plan.md 行号；若没有 Critical 明确写无；区分 Critical/Warning/Info，给可直接修正文档的建议。
</TASK>
OUTPUT: 中文 Critical/Warning/Info 审查报告，关注会导致错误实施的缺口，标明这是文档审查，不声称执行代码测试。
REVIEW_B
review_b_pid=$!
wait "$review_a_pid"
review_a_status=$?
wait "$review_b_pid"
review_b_status=$?
printf '{"review_a":%s,"review_b":%s}\n' "$review_a_status" "$review_b_status" > "$task_dir/research/review-status.json"
cat "$task_dir/research/review-status.json"
if [ "$review_a_status" -ne 0 ] || [ "$review_b_status" -ne 0 ]; then exit 1; fi
