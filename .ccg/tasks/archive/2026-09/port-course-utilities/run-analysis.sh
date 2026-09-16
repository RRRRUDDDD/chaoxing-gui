#!/usr/bin/env bash
set -u
task_dir=.ccg/tasks/port-course-utilities
wrapper=/c/Users/RUD/.claude/bin/codeagent-wrapper.exe
"$wrapper" --progress --backend claude - "$PWD" < "$task_dir/analyze-ui.txt" > "$task_dir/analysis-ui.md" 2> "$task_dir/analysis-ui.stderr.log" &
ui_pid=$!
"$wrapper" --progress --backend claude - "$PWD" < "$task_dir/analyze-backend.txt" > "$task_dir/analysis-backend.md" 2> "$task_dir/analysis-backend.stderr.log" &
backend_pid=$!
ui_status=0
backend_status=0
wait "$ui_pid" || ui_status=$?
wait "$backend_pid" || backend_status=$?
printf 'ui_exit=%s\nbackend_exit=%s\n' "$ui_status" "$backend_status"
test "$ui_status" -eq 0 && test "$backend_status" -eq 0
