#!/usr/bin/env bash
set -u
task_dir='.ccg/tasks/electron-to-tauri-migration-plan'
mkdir -p "$task_dir/research"
CODEX_TIMEOUT=300000 timeout 360s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/analysis-a.md" 2> "$task_dir/research/analysis-a.log" <<'ANALYSIS_A' &
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/analyzer.md
<TASK>
只读分析。用户仅要求制定 Electron 到 Tauri 的迁移计划，不执行迁移。你是两路独立分析中的前端与桌面安全视角。只检查 desktop/main.js、desktop/preload.js、desktop/session-store.js、desktop/tests/session-store.test.js、web/src/lib/sessionStore.js、web/src/lib/sessionStore.test.js、web/src/api/axios.js、web/src/App.jsx、web/vite.config.js、.ccg/spec/frontend/index.md 以及 app.py 的启动/CORS/配置定义。基于当前工作区（含未提交改动）输出：现有职责清单；Tauri 2 加载 Flask 动态 localhost 页面与本地打包 React 页面两方案的取舍；窄 IPC/会话存储、安全权限、旧 safeStorage 迁移的实际难点；具体文件与阶段；必要验收。区分事实与需 PoC 的假设。不要修改文件，不要创建 Task，不要运行测试，不要调用其他代理/模型，不要调用真实账号或业务服务，不要扩展研究目录。最多 1800 中文字，直接输出最终报告。
</TASK>
OUTPUT: 中文 Markdown，标明源码路径，推荐路线与备选路线，具体风险及验收，不宣称已执行迁移或测试。
ANALYSIS_A
analysis_a_pid=$!
CODEX_TIMEOUT=300000 timeout 360s C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude - "$(pwd -W)" > "$task_dir/research/analysis-b.md" 2> "$task_dir/research/analysis-b.log" <<'ANALYSIS_B' &
ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/analyzer.md
<TASK>
只读分析。用户仅要求制定 Electron 到 Tauri 的迁移计划，不执行迁移。你是两路独立分析中的后端/构建发布视角。只检查 desktop/main.js、desktop/package.json、desktop/electron-builder.yml、desktop/README.md、chaoxing-backend.spec、chaoxing.spec、build_desktop.bat、.github/workflows/main.yml、app.py 的启动/health/CORS/data dir 定义、.ccg/spec/backend/index.md。基于当前工作区（含未提交改动）输出：保留 Flask/Python 的迁移架构；Tauri 2 Windows sidecar/资源布局对 PyInstaller onedir 的约束；stdin EOF 实际退出语义、进程树与端口/健康检查；数据路径兼容；NSIS/WebView2/便携 ZIP/版本号/CI 切换；按依赖给出阶段与实际文件归属、估算和验证门槛。区分已知与 PoC。不要修改文件，不要创建 Task，不要运行测试，不要调用其他代理/模型，不要调用真实账号或业务服务，不要扩展研究目录。最多 1800 中文字，直接输出最终报告。
</TASK>
OUTPUT: 中文 Markdown，列出实际源码路径、优先决策、风险、可执行验收，不宣称已执行迁移或测试。
ANALYSIS_B
analysis_b_pid=$!
wait "$analysis_a_pid"
analysis_a_status=$?
wait "$analysis_b_pid"
analysis_b_status=$?
printf '{"analysis_a":%s,"analysis_b":%s}\n' "$analysis_a_status" "$analysis_b_status" > "$task_dir/research/analysis-status.json"
cat "$task_dir/research/analysis-status.json"
if [ "$analysis_a_status" -ne 0 ] || [ "$analysis_b_status" -ne 0 ]; then exit 1; fi
