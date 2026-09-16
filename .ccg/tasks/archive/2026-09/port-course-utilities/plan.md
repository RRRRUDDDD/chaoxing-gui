# 课程辅助功能接入计划

## 行为与契约

保留现有自动学习默认行为。选课页增加“执行功能”：自动学习、学习次数、视频时长、资源下载。后两项先启动可停止的资源读取任务，在原有进度页展示资源，勾选后执行下一步。课程工具统一使用 `/api/start`、既有轮询/日志/停止/恢复接口；同账号仅运行一个任务。

`POST /api/start` 仍接受现有学习请求；新增：

- `task_type: "visits"`, `course_list`, `tool_options: {count: 10, interval: 30}`（次数 1..1000，间隔 1..3600 秒）。
- `task_type: "catalog"`, `course_list`, `tool_options: {purpose: "video_time" | "download"}`。
- `task_type: "video_time"`, `course_list`, `tool_options: {source_task_id, resource_ids: [...], minutes: 30}`（每个选中视频增加指定分钟，0.1..1440；实时运行，单视频越界后从头继续）。
- `task_type: "download"`, `course_list`, `tool_options: {source_task_id, resource_ids: [...]}`。

所有新请求包含现有 `username/password/use_cookies`。后端验证 source_task_id 同账号、来自已结束的 catalog、选中资源和课程属于该快照；执行前重新获取课程与资源参数。客户端不提供上游 URL 或本地文件路径。

工具任务 status 增加 `task_type`, `task_label`, `progress`, `total`, `current_course`, `current_chapter`, `current_task`；`progress/total` 对工具表示处理条目数。details 保留 `courses: []`, `active_jobs: {}`，增加：

```
tool: {
  purpose, course_ids: [...], resources: [...], results: [...],
  completed_units: 0, total_units: 0, unit: "次" | "秒" | "字节" | "章节",
  current: {name, completed, total, unit}, output_dir: "..."
}
```

公开资源字段：`id, course_id, course_title, chapter_id, chapter_title, name, kind`（video/audio/document/file）, `downloadable: bool`, `watchable: bool`。可选 `duration` 秒；对外详情不返回 Cookie、dtoken、签名链接和私有附件。

results 条目包含 `id`, `name`, `course_title`, `status`（completed/error/skipped）, `message`，可选 `before`, `after`, `submitted`, `seconds`, `path`, `bytes`。文件保存到 `DATA_DIR/downloads/<task_id>/`；`POST /api/task/<task_id>/open-downloads`，JSON `{username}`，只打开属于该任务且经校验的下载目录。此操作同步新增 JS/Rust 白名单，支持本地浏览器、Electron、Tauri。

工具任务独立持久化配方 `{task_type, course_list, tool_options}`，禁止密码/会话。保存每项完成状态；重启后次数/时长等非幂等上报保留已有结果并终止为 partial，明确告知核对后重新开始，防止自动重复。catalog/download 可以从新鲜上游快照重试，下载保留已完成文件并避免覆盖。终态始终在资源/日志关闭后发布。

## 文件归属与并行实施

1. **service agent**：仅 `api/course_tools.py`, `tests/test_course_tools.py`。实现独立 CourseTools 服务及离线测试。参考本地上游，不修改参考仓库及现有 study 解码路径。
2. **ui agent**：仅 `web/src/App.jsx`, `web/src/components/CourseSelection.jsx`, `web/src/components/StudyProgress.jsx`, 新 `web/src/components/CourseToolSettings.jsx`, `web/src/components/CourseToolProgress.jsx`, `web/src/components/CourseTools.test.jsx`。实现功能选择、资源列表/筛选/勾选、时长输入、工具进度/结果和打开目录交互，复用现有账户/任务状态与样式。旧改动必须保留。
3. **desktop agent**：仅 `web/src/api/tauriAdapter.js`, `web/src/api/tauriAdapter.test.js`, 新 `web/src/api/courseToolsFlow.test.jsx`, `desktop/src-tauri/src/api_proxy.rs`。增加 taskOpenDownloads 到固定 POST 路径并验证所有传输模式、拒绝无效参数。旧 stop 改动必须保留。
4. **lead**：`app.py`, `api/task_state.py`, 新 `api/course_tool_tasks.py`, `tests/test_course_tool_tasks.py`, `tests/test_course_tool_api.py`, 文档和任务记录。实现校验、worker/进度、快照与恢复、安全打开目录和回归验证。

## 服务接口（供 worker 使用）

`CourseTools(chaoxing, cancel_check=None)`，从 chaoxing.session_manager 借用当前账号的线程 Session。

- `scan_course(course, on_chapter=None) -> list[dict]`：返回公开字段并带 `_` 前缀私有元数据（runner 用 `public_resource` 过滤）；callback `(chapter_title, completed, total)`。含已完成和非任务点的资源，读取实际 cardcount，不调用会产生学习副作用的 get_job_list。锁定章节可跳过并记录；网络/解析失败不得伪装为空。
- `public_resource(resource) -> dict`：返回上述公开 DTO。
- `get_statistics(course) -> dict`：`visits`, `watched_minutes`, `total_minutes`（不可用为 None）, `warnings: []`。使用当前年月，不采用上游 2023 常量。
- `add_visits(course, count, interval, on_progress=None) -> dict`：成功请求后 callback `(completed, total)`，返回 `before/after/submitted`。HTTP/业务错误抛出异常；平台实计与请求数区别显示。
- `watch_video(course, resource, seconds, on_progress=None) -> dict`：callback `(completed_seconds, total_seconds)`；返回 `seconds`。保持真实间隔及可取消，不因 isPassed 直接跳过已完成视频；时长不超过媒体边界，跨长度重放。
- `download_resource(course, resource, directory, on_progress=None) -> dict`：callback `(received_bytes, total_bytes_or_None)`，返回 `path/name/bytes`。流式、超时、关闭 response、校验状态/长度、安全文件名、重名不覆盖、part 清理，不信任上游任意主机重定向。
- `ToolCancelled`：协作取消异常，所有长循环/等待/下载检查 cancel_check。

## 验证

- Python 3.11/3.13 离线测试；上游 HTML/JSON、签名、分页、已完成资源、统计、视频边界、下载失败与停止。
- API 参数、账号/来源快照、同账号冲突、启动失败、非幂等恢复、持久化及停止收尾。
- Web 新功能交互与既有账号切换/迟到响应/恢复/停止测试，生产 build。
- Desktop Node 测试，Rust ApiOperation/契约测试。
- 本地 fixture 浏览器 smoke 检查选课、资源读取/选择和进度界面；不使用真实平台账号。
- 合并审查意见并修复，记录局限，归档本任务记录；按用户后续要求提交并推送本次产品变更，保留原有无关工作区改动。

## 完成情况

- [x] 独立课程工具服务、GUI、桌面传输契约按文件归属完成。
- [x] 接入既有账号隔离、任务启动/停止、日志、进度与退出恢复。
- [x] 完成本地交叉审查并修复发现的问题；外部双模型审查均超时，未取得有效报告。
- [x] Python 3.11/3.13 各 301 项、Web 225 项、Desktop Node 101 项、Rust API 11 项通过。
- [x] Web 生产构建和桌面/手机尺寸的 fixture 浏览器流程通过。
- [x] 回馈后端规范，归档需求、计划、审查和验证证据；功能提交 `f6946fb` 通过发布任务转移为 `fe2860e`，已推送 GitHub main。

测试详情和验证边界见 `review.md`。本次未使用真实超星账号进行上报或下载。
