# 实施计划与所有权

L+ / high / fullstack。自主本地协议分析已完成；不调用外部双模型分析。仅按以下互不重叠范围并行实施，之后独立复审。

## 服务代理

仅 api/reading_time.py、tests/test_reading_time.py。

ReadingTools(CourseTools)：继承只读章节扫描、账号会话、请求/取消等基础设施；覆盖 _resource，仅返回 type=read/module=insertread 的阅读任务，public_resource 白名单包含 readable、required_minutes、read_minutes、book_count。单个资源对应阅读任务，后台选择该任务第一本可读专题书籍；ID 绑定课程/班级/章节/job。

watch_reading(course, resource, seconds, on_progress=None) 返回 seconds、before、after（后两者为平台分钟）。按真实 5 秒间隔执行原网页 multimedia/readlog，首个请求建立基线；只有后续成功响应才累计相应已等待时长，不计未确认区间，最后不足 5 秒也等待后提交。取消可中断等待，网络有截止，睡眠/停机长间隔不虚增本地进度。{} 是已观测的唯一空成功回执，仅在该端点接受；HTML、空串、业务失败等拒绝。无自动网络重试。

访问 api/work 的参数来自刷新后的 read 附件 enc 和 course/chapter/job（utenc 经真实验证无需提供）；跟随有限次同平台、预期路径重定向取得 readTips/readList。只读展示统计和链接，绝不调用 readv2 完成接口。书籍链接保留 _from_/_fromV2_，绑定原课程/章节；只跟随受信任 mooc 主机和 course/zt 路径，解析书籍真实 courseid 和初始章节，取 zt/getcards 验证内容存在后上报。所有私有数据只在内存。

## UI 代理

仅 web/src/components/CourseToolSettings.jsx、CourseSelection.jsx、CourseToolProgress.jsx、StudyProgress.jsx、CourseTools.test.jsx、可新增 ReadingTime.test.jsx、web/src/api/courseToolsFlow.test.jsx。

添加 reading_time（阅读时长）；选课后 catalog purpose=reading_time，列表仅 readable===true 可选；显示已记录/要求分钟及 book_count。每任务分钟 0.1–1440，默认30；启动 reading_time 带 source_task_id/resource_ids/minutes 与相应 course_list。进度 tool.unit=秒、completed_units/total_units，结果 seconds 为已上报时长，before/after 为平台分钟。文案说明 GUI 后台运行、无需新窗口，平台统计可能次日更新；不声称已达标。复用停止与状态，不破坏账号代次/已有任务/退出锁。为三种传输和 UI 起停添加有意义测试。

## 主代理

仅 api/course_tool_tasks.py、api/task_state.py、app.py、tests/test_course_tool_tasks.py、tests/test_course_tools_api.py 或新 tests/test_reading_task_api.py、README.md；必要时 desktop/src-tauri/src/api_proxy.rs 测试区。负责 reading 类型/选项/初始 DTO/进度结果、创建 ReadingTools、catalog及运行路由、停止和禁止重放。保持现有三项功能兼容。

## 验证与发布

1. 集成并独立审查真实协议、等待/取消、公开 DTO、恢复和 UI。
2. 运行适用完整检查；真实账号执行有界阅读任务，对照原网页请求，分别记录已上报和平台统计，不把 HTTP 200 说成计分增加。
3. 重建 Web、冻结后端和嵌入新 UI 的 Tauri 宿主；校验备份，更新已授权本地安装，保持原任务历史并验证 GUI 起停。
4. 产品提交经最新 origin/main 隔离 worktree 验证后正常推送。保留用户修改和参考仓库；task/spec 本地归档，真实账号 HTML/Cookie/链接不进入 Git。
