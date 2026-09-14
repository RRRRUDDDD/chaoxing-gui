# 自查结果

按用户明确要求，未调用双模型或子代理。

- Critical：无。本次为检查与建议，未修改产品行为。
- Warning：当前 ddddocr 必须保留 common_old.onnx；默认识别模型不能凭文件名判断。已通过真实离线推理确认。
- Warning：直接排除 OpenCV 会使 ddddocr 导入失败。已用隔离探针确认，优化建议要求先调整依赖。
- Warning：本地体积基准是 2026-09-07/08 产物，不能宣称为最新远端发行包或优化后的实测结果。
- Info：当前桌面构建已经排除 PaddleOCR；旧源码便携版脚本具有另一套依赖安装和启用策略。
- Info：24 项现有 OCR 测试通过；Paddle 初始化与推理测试使用桩，未据此宣称真实 PaddleOCR 已启用。
- Info：保留用户原有 frontend spec、迁移计划及 poc-window.png 修改。

沉淀经验：将默认 ddddocr 模型和 OpenCV 导入约束追加到后端规范，避免后续体积裁剪破坏验证码功能。
