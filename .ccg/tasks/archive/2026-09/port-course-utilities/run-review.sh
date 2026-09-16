#!/usr/bin/env bash
set -u
export CODEX_TIMEOUT=300000
task_dir=.ccg/tasks/port-course-utilities
wrapper=/c/Users/RUD/.claude/bin/codeagent-wrapper.exe
"$wrapper" --progress --backend claude - "$PWD" < "$task_dir/review-backend.txt" > "$task_dir/review-backend.md" 2> "$task_dir/review-backend.stderr.log" &
backend_pid=$!
"$wrapper" --progress --backend claude - "$PWD" < "$task_dir/review-ui.txt" > "$task_dir/review-ui.md" 2> "$task_dir/review-ui.stderr.log" &
ui_pid=$!
backend_status=0
ui_status=0
wait "$backend_pid" || backend_status=$?
wait "$ui_pid" || ui_status=$?
printf 'backend_exit=%s\nui_exit=%s\n' "$backend_status" "$ui_status"
test "$backend_status" -eq 0 && test "$ui_status" -eq 0
