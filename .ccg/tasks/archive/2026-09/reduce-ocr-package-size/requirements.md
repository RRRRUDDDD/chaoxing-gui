# OCR 发布体积优化

- 根据 audit-paddleocr-package-size/findings.md 优化当前 PyInstaller 桌面发行；Tauri、Electron 共用后端，独立 exe 同步优化。
- 锁定 ddddocr 1.6.1，保留已验证的 common_old.onnx 和完整默认字符表。保留原 CPU 推理、先缩放后灰度、float32 / 255、CTC 解码行为。
- 使用只覆盖现有 bytes -> classification -> str 契约的轻量适配，去掉冻结程序对 ddddocr 运行时代码和 OpenCV 的依赖；ddddocr wheel 继续作为构建/源码资产来源。
- 不修改题目 PaddleOCR/云端 OCR、账号数据、网络协议、现有用户改动，不扩大到旧式嵌入式 Python 便携版。
- 增加模型/字符表版本与哈希检查、真实上游对比、无 cv2 导入检查、冻结可执行文件离线识别自检和包清单检查。
- 本轮不声称真实课程验证码准确率已验收；合成图片的上游一致性、真实 CPU 推理和冻结可用性分别记录。
- 用户明确要求不调用双模型。已取消两个外部分析进程；主代理直接实施和审查，不再调用外部模型。

现有用户改动：.ccg/spec/frontend/index.md、.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md、poc-window.png，均保留。
