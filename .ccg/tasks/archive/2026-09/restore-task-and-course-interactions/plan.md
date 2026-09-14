# 实施计划

按用户最新指示，不再调用双模型，由主代理完成全部实现与审查。

## 1. 后端任务恢复

- `api/task_state.py`：可选文件持久化，创建任务前原子保存无登录密码的执行参数；重启后活动任务为 interrupted，同账号仍只保留一个活动任务；终态持久化结果、详情和有界日志，按原 TTL 过期。
- `app.py`：数据目录中的独立任务文件；复用启动参数校验；增加具名恢复接口，验证账号登录后原子领取 interrupted 任务并复用原 ID。重新读取平台实际进度，不改变退出时清理后端的行为。
- 新增 `tests/test_task_recovery.py`：覆盖进程中断后重启、终态不重启、并发恢复、账号隔离、IO 失败、无密码落盘、恢复后平台进度核对和上游 KeyError 不误报任务 404；保留原有任务状态与接口测试。

## 2. 桌面桥接与前端恢复

- `desktop/src-tauri/src/api_proxy.rs`、`web/src/api/tauriAdapter.js`：仅增加 taskResume 白名单操作，继续验证 task ID 和请求结构。
- `web/src/App.jsx`：恢复时先调用恢复接口，再开始轮询；失败可重试；没有历史恢复数据的旧 ID 返回课程选择并解释原因；延续账号代次和取消保护。
- `web/src/components/StudyProgress.jsx`：显示恢复状态/错误与重试入口，恢复期间暂停轮询。
- 相应单元/流程测试验证真实恢复路径和迟到请求处理。

## 3. GitHub 外链

- 新的共享仓库链接组件供课程页与进度页使用，浏览器保留原生链接行为。
- Tauri 增加无 URL 参数的 `open_repository` 命令，仅打开固定 HTTPS 仓库；继续限制主窗口/来源和空参数结构。
- Electron 的新窗口处理只为固定仓库调用系统浏览器，仍拒绝新 WebView 窗口。
- 覆盖链接点击、命令参数、错误展示和拒绝其他 URL。

## 4. 课程选择与日志滚动

- `web/src/lib/courseSelection.js`：首次默认空选择，保持同账号明确保存的选择交集。
- `web/src/components/CourseSelection.jsx`：Ctrl/Cmd+A 全选当前筛选结果，保留隐藏选中项；输入框/可编辑区域保留文本全选；提供全选与清空入口。
- `web/src/components/StudyProgress.jsx`：移除 scrollIntoView，滚动仅作用于日志容器；用户向上翻阅时暂停跟随，不抢焦点。
- 更新课程默认值回归，验证快捷键、日志滚动及焦点。

## 验证与交付

1. 各模块相关测试通过后，运行完整 Python、Web、Electron 测试及 Web 构建。
2. 运行 Rust 检查/测试；可行时执行隔离 GUI 验证，不使用真实账号。
3. 审查 diff，记录验证范围和限制；仅追加本任务值得保留的规范经验。
4. 按原要求归档任务并只提交该任务归档，保留用户原有工作区修改。

## 完成记录

- 四项实现均已完成，主代理自行审查；按用户最新要求未继续调用双模型。
- Python 176 项、Web 141 项、Desktop Node 97 项、Rust 114 项测试通过；Rust 另有 1 个 subprocess helper 按设计忽略。
- Web 生产构建，以及 Rust fmt、check --all-targets、clippy --all-targets 检查通过。
- Chromium 与 Electron 隔离界面 smoke 通过；恢复、过期返回选课、默认空选、Ctrl+A、输入框文本全选、日志滚动/焦点和仓库外链均有验证。
- 未运行 Tauri GUI、未生成发行安装包；无真实账号或真实学习任务参与测试。具体证据和兼容边界见 review.md。
