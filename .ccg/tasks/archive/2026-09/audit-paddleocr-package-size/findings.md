# 检查结论

当前桌面发行构建没有携带 PaddleOCR，程序体积主要来自验证码 ddddocr 及其推理和图像处理依赖。源码启动和旧式嵌入式 Python 便携版仍保留 PaddleOCR 的条件调用。

## 使用路径

1. 题目解析 `api/decode.py:1101` 和答题前处理 `api/answer.py:92` 调用 `_ocr_image_to_text`。
2. `api/vision_ocr.py:146` 中本地 OCR 默认关闭，可通过 `enable_local` 或 `CHAOXING_ENABLE_OCR=1` 开启。
3. `api/decode.py:488` 的优先级为已配置的云端视觉 OCR，其次是没有云端配置时启用的本地 PaddleOCR，再尝试 HTTP fallback。云端失败不会自动转入 PaddleOCR。
4. 本地分支通过 `_local_ocr_result` 延迟初始化 PaddleOCR，识别题目图片；不是程序启动时无条件运行。
5. `chaoxing.spec:43`、`chaoxing-backend.spec:45` 明确排除 PaddleOCR；`.github/workflows/main.yml:174` 安装构建依赖时也过滤掉 paddleocr。Tauri 与 Electron 共用该冻结后端。
6. 检查现有后端 EXE 的 PYZ 模块表及 Tauri portable ZIP：均没有 paddle、paddleocr、paddlex 模块或 Paddle 文件。因此对这批桌面产物移除 PaddleOCR 不会带来额外体积收益。
7. `start.bat:3` 显式启用本地 OCR，并安装 PaddlePaddle/PaddleOCR。`build_portable.bat:85` 等旧脚本也安装这些依赖，复制整个 `PaddleOCR/` 并在生成的启动器中设置开关；这条发布路径需要单独优化。仓库的源码副本不等于当前桌面安装包内容。

现有 `chaoxing.log` 在本轮测试前有 40 条 PaddleOCR 文本识别成功记录，全部包含离线测试标记或测试答案；失败记录也全部含 `offline`。这些记录不能证明真实课程中用过 PaddleOCR。本轮没有登录账号、下载 OCR 模型或调用远端识别服务。

## 本地产物测量

基准为本地 2026-09-07 至 09-08 生成的 1.1.1 产物，未重建当前 HEAD，也未核实远端最新安装包。详细字节数保存在 `size-report.json`。

| 内容 | 体积（MiB，解压后） |
| --- | ---: |
| 冻结后端合计 | 325.15 |
| OpenCV（含 FFmpeg） | 111.76 |
| ddddocr 全部数据 | 83.82 |
| ONNX Runtime | 33.65 |
| NumPy 与其 DLL | 25.89 |
| Pillow | 12.80 |

本地 Tauri portable ZIP 为 177.88 MiB，解压内容合计 334.44 MiB；NSIS 安装器为 146.63 MiB。Tauri 宿主本身约 8.95 MiB，后端仍占主要体积。这里没有把开发机上的多份 staging 目录相加当作用户安装体积。

## 优化顺序

### 1. 优先裁剪 ddddocr 未使用模型

`api/captcha.py:41` 只构造 `DdddOcr(show_ad=False)`，在 `api/captcha.py:144` 调用默认 `classification(img)`。本机构建环境为 ddddocr 1.6.1，默认参数 old=false、beta=false、det=false，实际加载 **common_old.onnx**。离线真实推理探针确认只加载此模型，使用 CPUExecutionProvider，合成文本 `1234` 的识别结果为 `1234`。

| 模型 | 大小（MiB） | 当前默认路径 |
| --- | ---: | --- |
| common_old.onnx | 12.98 | 保留，实际加载 |
| common.onnx | 51.58 | 未使用，beta 模型 |
| common_det.onnx | 19.20 | 未使用，目标检测模型 |

可先锁定经过验证的 ddddocr 版本，并将两个 spec 的数据收集改为默认识别所需文件白名单；同时检查 PyInstaller hook 和最终清单，避免再次收集闲置模型。预计节省 **70.78 MiB 解压体积**，本地后端从约 325.15 降到 **254.37 MiB**。现有 ZIP 内这两个模型的压缩数据合计约 **65.72 MiB**；这是当前 ZIP 的可裁剪数据量，不是新安装器的实测结果。

不能因文件名含 old 就删除 common_old.onnx。requirements.txt 目前没有锁定 ddddocr，未来版本默认模型可能变化，必须绑定版本和验证。

### 2. 调整验证码依赖后移除 OpenCV

默认文字识别的预处理由 Pillow/NumPy 完成，主项目没有直接调用 cv2、目标检测或滑块接口；但 ddddocr 1.6.1 导入时会连带加载颜色处理、检测、滑块模块，并强制导入 OpenCV。隔离子进程禁止导入 cv2 后，ddddocr 导入确实失败。

因此单改 PyInstaller excludes 会使 `api/captcha.py` 的可选导入失败，丢失自动验证码识别。可评估将未使用模块改为延迟导入，或做仅使用现有模型、字符表、Pillow、NumPy 和 ONNX Runtime 的文字识别适配。前后需对真实授权样本做一致性验证，并验证冻结程序的初始化和验证码路径。

如果这一适配成功，可再省约 **111.76 MiB**。与模型裁剪叠加，本地后端的算术估计约 **142.61 MiB**，总计减少约 56%。这是候选方案，不是已完成的打包结果。改用 headless OpenCV 只能作为过渡，仍包含原生库，不能按完全删除 OpenCV 计算收益。

### 3. 若需要保留源码便携版的本地题目 OCR

- 将 OCR 作为按需安装的可选组件，默认轻量主包保留现有云端/HTTP 识别路径；组件安装后仍会占本机磁盘空间。
- 停止在已安装 paddleocr wheel 的包中无差别复制整份上游仓库。本地 `PaddleOCR/` 为 140.72 MiB，其中 docs/doc 约 122.29 MiB。移除源码兜底前应保证 wheel 及其依赖可用；此收益只适用于旧源码便携版。
- 本地离线识别可评估移动版模型和 CPU 部署，或评估以 ONNX Runtime/RapidOCR 运行适配后的 PP-OCR 模型，争取与验证码识别共用推理库。需要另行验证中文、公式图片效果和部署兼容性，本轮没有安装或测试这些替代引擎。

NSIS 已使用 LZMA，portable ZIP 已使用 Optimal 压缩。进一步提高压缩主要改变下载大小，不能替代依赖和模型裁剪。

## 验证与范围

- `.venv/Scripts/python.exe -m unittest tests.test_ocr -v`：24 项全部通过。覆盖开关、配置隔离、缓存、失败重试、Paddle 初始化并发与 GPU 回退等；Paddle 模型推理由测试桩替代，不代表真实模型精度已验收。
- ddddocr 1.6.1 默认模型真实离线探针通过，见 `ddddocr-probe.json`；单个合成样本只验证加载与执行，不代表真实验证码准确率。
- 禁止 cv2 导入的隔离探针确认当前 ddddocr 无法直接去除 OpenCV，见 `opencv-import-probe.json`。
- EXE/PYZ 与 portable ZIP 内容核对及体积测量，见 `size-report.json`。
- 未改产品代码、打包配置、依赖或现有产物；未触发构建或发布。
