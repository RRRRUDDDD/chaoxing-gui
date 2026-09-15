# 实施计划

复杂度 L，风险 high（验证码路径及冻结入口）；按用户最新要求由主代理执行，不调用双模型或子代理。

1. 保存现有发布体积基线，核对 ddddocr 1.6.1 默认模型、字符表与数值处理（已完成）。
2. 先增加验证码适配的测试：预处理、CTC、初始化失败、禁用与注入、资源缺失/版本漂移；然后实施 api/captcha_ocr.py 和 api/captcha.py。
3. 两个 spec 只收集原模型/字符表/许可证元数据，排除 ddddocr 代码和 cv2；requirements.txt 锁定数据版本并声明直接使用的依赖。
4. 增加不初始化 Web/业务数据的离线 --check-captcha-ocr 入口和验证脚本，CI 在两个实际冻结 exe 上执行模型/导入/清单检查，并比较源码适配与原库。
5. 运行 Python 3.11/3.13 回归、真实推理对比、两种冻结构建与启动 smoke；生成优化后的桌面产物并测量下载/解压大小，保留未经执行的验证边界。
6. 主代理检查 diff、依赖/版权和回归；记录结果、沉淀后端规范，归档任务并仅提交本轮预期文件。

文件范围：api/captcha_ocr.py、api/captcha.py、app.py（仅提前处理自检参数）、requirements.txt、requirements-test.txt、chaoxing.spec、chaoxing-backend.spec、tests/test_captcha_ocr.py、desktop/scripts/verify-captcha-ocr.py、.github/workflows/main.yml、desktop/README.md、resource/licenses/ddddocr-LICENSE.txt（如实现适配需要保留上游版权）、.ccg/spec/backend/index.md 和本任务记录。

验收：默认模型/字符表哈希和上游相同；合成样本输入张量和最终结果一致；新程序在禁止导入 ddddocr/cv2 的子进程可推理；两个实际 exe 的自检通过；产物无 common.onnx/common_det.onnx/cv2；不把旧基线与当前源代码的差值全部归因于本次改动。
