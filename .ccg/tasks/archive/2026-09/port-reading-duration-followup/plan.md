# 续作实施计划

1. 核对并保留现有 `course_tool_tasks`、`task_state`、Flask、Tauri API 白名单和 Web 起停改动。
2. 实现 `api/reading_time.py`：严格识别 `type=read/module=insertread`，读取 `api/work` 与专题书籍，校验课程归属和 `zt/getcards` 内容，按成功 `{}` 回执累计真实间隔，禁止 readlog 自动重试并支持取消。
3. 运行 Python 阅读/API/课程工具回归、Web 课程工具流程和构建、Rust API proxy 测试（环境支持时）。
4. 审查 diff，更新任务状态和经验规范；仅提交阅读接入文件及任务记录，排除参考仓库、缓存和依赖目录。
5. 归档本任务。
