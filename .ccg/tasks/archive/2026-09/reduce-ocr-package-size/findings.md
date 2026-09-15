# 优化结果

当前桌面发行已改为复用 ddddocr 1.6.1 的原始默认模型和完整字符表，由 Pillow、NumPy、ONNX Runtime 完成文字识别。删除发行包中的 beta/检测模型和 OpenCV，无需下载新的 OCR 模型。源码中的 wheel 仍作为资产来源；其构建环境传递依赖不等于最终发布内容。

## 实测体积

对比审计中的本地 2026-09-07 至 09-08 产物和本轮当前工作树构建，单位 MiB。旧产物未按当前源代码重建，因此整体差值也包含期间其他已提交代码及宿主变化。

| 内容 | 优化前 | 优化后 | 降幅 |
| --- | ---: | ---: | ---: |
| 冻结后端（解压后） | 325.15 | 142.65 | 56.13% |
| 独立 Python EXE | 172.34 | 64.87 | 62.36% |
| Tauri NSIS 安装包 | 146.63 | 51.56 | 64.84% |
| Tauri portable ZIP | 177.88 | 68.99 | 61.21% |

新 Tauri ZIP 全部展开为 152.03 MiB，仅包含 common_old.onnx，不含 OpenCV。字节数、SHA256 与主要目录分组见 size-report.json；优化前快照见 baseline-size.json。旧发布文件保留，新文件位于：

- `desktop/release/ocr-optimized/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe`
- `desktop/release/ocr-optimized/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip`
- `dist/ocr-optimized/chaoxing-gui.exe`
- `dist/ocr-optimized/chaoxing-backend/`（必须保留整个目录）

## 实现与约束

- api/captcha_ocr.py 只实现项目当前使用的默认文字识别契约，不替换或重新训练模型；不实现项目未使用的 beta、检测、滑块或概率接口。
- common_old.onnx SHA256 为 `b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68`，默认字符表 8210 项，编码后 SHA256 为 `2c097552127cb5476189e653c0d2ccfd6b6c87425c57f81a01b5770207c33360`。
- 两个 spec 用相同资产白名单、版本检查和 copy_metadata；同时排除上游 ddddocr 运行时代码、cv2 和题目 Paddle 运行时，防止 hook 回收闲置依赖。保留 console/onefile/onedir 模式与后端完整 web/dist。
- 缺少资源/依赖、资产版本或校验值错误时，ocr_init 仍返回 None 并提示手动验证；注入 OCR 实例及显式 None 的行为保留。
- 添加早于 Flask/账号初始化的离线诊断入口；CI 比较新旧实现，并检查两个真实冻结 EXE 的清单和实际推理。无控制台 EXE 使用独立新建的 JSON 报告文件，不依赖 stdout。
- 上游 MIT 许可证保留在资源及 wheel 元数据中。题目云端/HTTP OCR 和源码可选 PaddleOCR 不在本轮变更范围。

## 验证

- Python 3.11.15、3.13.14：完整 unittest 各 193 项通过，其中新增验证码回归 17 项；诊断参数调整后两环境针对性回归再次通过。
- 48 张生成图片（6 组文字 × 4 种色彩模式 × 2 种尺寸）：与 ddddocr 1.6.1 的输入张量逐元素完全相等，最终识别结果一致。独立禁止 ddddocr/cv2 导入的子进程识别通过，详见 source-comparison.json。
- 两个真实冻结 EXE：仅保留默认模型，资产 hash、CPU 推理、自检报告与空临时目录校验通过；旧大包被新增验证器拒绝。见 frozen-backend.json、frozen-independent.json 和 rejected-old-backend.json。
- 真实后台启动 smoke：后端握手、health、静态页面、安全配置校验及 stdin EOF 退出通过；独立 EXE 对应路径通过。Windows Job 的进程回收均 verified=true、remaining 为空；原始结果保留在 smoke-*-result.json。
- Web：141 项通过；Desktop：101 项通过；Python 编译、两个 spec 语法和工作流 YAML 检查通过。
- PyInstaller 6.21.0 两种构建及 Tauri release 构建成功。ZIP 819 项文件完整性校验通过；NSIS 818 项应用文件与预期资源/宿主逐字节哈希一致，见 nsis-result.json。NanaZip 6.5.1767.0 检查工具与已安装官方包的完整 EXE/DLL 哈希相同，见 archive-tool.json。

## 验证边界

本轮未使用真实账号、真实课程验证码或远程 OCR，不宣称实景准确率验收。未运行安装器/卸载器或本机正式 Tauri GUI，未运行远程 Actions 或发布；这些仍由已有隔离 Windows CI/验收流程执行。本地文件未签名，版本维持 1.1.1。

较大的 smoke 二进制夹具保留在 `build/ocr-optimized/smoke-backend/`、`build/ocr-optimized/smoke-independent/`，NSIS 检查证据在 `build/ocr-optimized/nsis-content/`；不将构建二进制提交到任务归档。原始 smoke JSON 中的路径保留测试发生时的值。
