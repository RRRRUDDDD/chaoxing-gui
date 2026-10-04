## 模块说明

- `__init__.py`: 提供格式化输出辅助函数
- `base.py`: 提供核心功能，包含主要的Chaoxing类和学习功能
- `answer.py`: 提供多种题库接口和答题功能
- `answer_check.py`: 答案检查和验证
- `cipher.py`: AES加密解密功能
- `config.py`: 全局配置常量
- `cookies.py`: Cookie管理
- `cxsecret_font.py`: 超星字体解析
- `decode.py`: 解析超星页面数据
- `exceptions.py`: 自定义异常类
- `font_decoder.py`: 字体解码器
- `logger.py`: 日志功能
- `notification.py`: 通知功能
- `process.py`: 进度显示工具
- `captcha.py`: 验证码识别模块
- `live.py`: 直播任务处理
- `live_process.py`: 直播任务处理逻辑

## 隐私与结果复核

- `privacy.py`：统一日志、通知与公开错误脱敏，保留任务路由标识。
- `work_result.py`：提交前基线及提交后成绩的只读检查，不重做、不改缓存。
- `verification.py`：课程总进度与原始任务附件的保守复核，结果独立于执行计数。
- `course_tools.py` 的 `iter_card_pages`：共享原始卡片遍历；严格只读模式不会上报学习进度或自动处理验证码。

以上能力均使用现有 SessionManager、取消信号及任务日志出口。边界和回归入口见 [开发说明](../DEVELOPMENT.md#上游兼容移植隐私与只读结果确认)。
