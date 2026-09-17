# Review

## Critical

- 专题书籍真实链接会从 `/mooc-ans/course/<id>.html` 重定向到 `/mooc-ans/zt/<id>.html`；阅读任务页也可能把 `api/work` 重定向到 `coursedata/job/preview-show`。已在 `api/reading_time.py` 增加显式白名单和逐跳课程 ID 校验。

## Warning

- 平台最终分钟统计依赖次日刷新，服务只展示 before/after，不能据此声称达标。
- 本地环境没有 `cargo` 命令，Rust 测试未能直接运行；已有 Tauri 代理改动仅通过现有 Python/Web 流程及源码检查验证。

## Validation

- `python -m unittest discover -s tests -q`: 323 passed。
- `python -m unittest tests.test_reading_time tests.test_reading_tasks tests.test_course_tool_api tests.test_course_tools -q`: 76 passed。
- `npm --prefix web test -- --run src/components/CourseTools.test.jsx src/api/courseToolsFlow.test.jsx src/lib/taskPolling.test.js`: 76 passed。
- `npm --prefix web run build`: passed。
- `git diff --check`: passed。
