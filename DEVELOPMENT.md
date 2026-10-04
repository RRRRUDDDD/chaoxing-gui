# 开发指南

本文面向从源码运行、调试或参与维护的开发者。普通用户请先阅读 [README](README.md)；Windows 桌面应用的工具链、构建、签名和安装验证见 [桌面版开发与打包指南](desktop/README.md)。

## 环境与源码运行

- Python **3.11 或更高版本**；后端 CI 使用 Python 3.11 和 3.13。
- Node.js **20** 与 npm；仅运行 CLI 时不需要 Node.js。
- Windows 桌面构建还需要 Rust、MSVC、Windows SDK 等，具体版本以桌面版指南为准。

以下命令在 Windows PowerShell 中执行。依赖安装需要联网，建议为项目使用独立 Python 虚拟环境。

### 安装依赖

```powershell
git clone https://github.com/RRRRUDDDD/chaoxing-gui.git
cd chaoxing-gui
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix web ci
```

`requirements.txt` 包含本地 OCR 相关依赖，安装可能耗时较长。PaddlePaddle 的 CPU/GPU 包需按运行平台选择；它与题库查询、验证码运行时不是同一条依赖路径。

### 启动 Web 界面

在项目根目录打开两个终端，分别启动后端和前端：

```powershell
# 终端一：后端，默认端口 5000
.\.venv\Scripts\python.exe app.py
```

```powershell
# 终端二：前端，固定端口 3000
npm --prefix web run dev
```

访问 `http://localhost:3000`。前端开发服务器将 `/api` 请求代理到 `http://localhost:5000`，使用期间保持两个终端运行。

也可以在已准备好 Python、Node.js 和 npm 的 Windows 环境中运行 `start.bat`。该脚本会检查并安装依赖，包括 PaddlePaddle / PaddleOCR；它不是无需开发环境的桌面发行包。

### 命令行模式

将 [config.ini.example](config.ini.example) 复制为 `config.ini`，填写账号、课程 ID 和可选题库配置，再运行：

```powershell
.\.venv\Scripts\python.exe main.py -c config.ini
```

查看参数帮助：

```powershell
.\.venv\Scripts\python.exe main.py --help
```

CLI 的 `-l` 参数接受逗号分隔的课程 ID。**CLI 未指定课程列表时可处理全部课程，与 Web / 桌面界面必须先选课的行为不同**，执行前请核对范围。

配置文件可能含明文密码、题库凭证和通知参数，不要提交到仓库。尽量避免在命令行直接输入密码，以免留在终端历史中。

## 测试与构建

### 离线回归

在独立测试环境中安装轻量依赖：

```powershell
python -m venv .venv-test
.\.venv-test\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv-test\Scripts\python.exe -m unittest discover -s tests -v
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
npm --prefix desktop ci
npm --prefix desktop test
```

第一方离线回归不连接真实学习账号、题库或通知服务，也不加载 Paddle 模型。离线测试通过不代表真实课程、验证码和平台统计流程已经验收。

Rust 回归与 Windows 打包测试需要桌面版指南中规定的工具链。安装、卸载验收应在干净的隔离 Windows 用户或虚拟机中运行，不要对日常账号及个人数据目录执行安装测试。

### 版本与发行

`pyproject.toml` 是唯一版本源。修改版本后执行：

```powershell
python desktop/scripts/version.py --sync
python desktop/scripts/version.py --check
```

同步脚本更新 Web/Desktop package、lock、Cargo 和 Tauri 版本，不升级依赖，不创建 Git 标签。

推送 `main` 会触发 Windows CI 测试与构建，但不会自动发布 Release。发布入口为匹配版本的 `v*` 标签，或显式启用发布选项的手动 workflow。安装包格式、签名要求和产物校验详见 [桌面版指南](desktop/README.md)。

### 源码便携包

源码便携包入口为 `clean_and_build_portable.bat`，输出 `chaoxing_portable/`。它不同于 PyInstaller 独立 EXE，也不同于 Tauri 桌面便携 ZIP。

**仅在不含个人数据的独立源码副本中运行。** 脚本会清理日志、`cache.json`、`cookies.txt`、`config.ini` 和旧构建产物，再安装嵌入式 Python 与依赖。不要在日常运行目录中使用，也不要将清理脚本视作对所有敏感文件的完整脱敏措施。

## 数据与兼容性约定

### 账号与会话

账号会话保存在数据目录的 `.cookies/` 下，各账号独立。旧 `cookies.txt` 仅供未指定账号的 CLI Cookie 登录使用。桌面端记忆账号和任务入口，不保存密码，也不依赖每次启动分配的后端端口。

Tauri 正式版数据与日志位置见 [README](README.md#数据与升级)。源码运行应留意当前工作目录和 `CHAOXING_DATA_DIR`；不要将生产账号数据混入测试或打包目录。

### 答案缓存

缓存键包含题干、题型和有序选项，旧的仅题干缓存不再命中。

默认每 32 次实际更新原子写盘，任务正常退出时再次刷新。磁盘正常时，异常终止最多丢失最后一批尚未刷盘的 32 次更新；写盘失败时会保留脏数据等待重试，此时不保证该上限。

直接使用题库 API 的调用方应在任务线程结束后调用 `Tiku.close()`，多个进程不要同时写同一个缓存文件。

### OCS 兼容范围

题库适配层位于 `api/ocs_tiku.py`，回归位于 `tests/test_ocs_tiku.py`。

- handler 使用受限表达式解析器，支持数组、对象字面量（标识符/字符串键、嵌套、尾随逗号）、三元表达式、属性访问及已有白名单方法。不支持任意语句、展开、计算属性或方法定义；不提供浏览器/系统 API。长度与嵌套限制用于控制解析资源。
- 解析错误包含题库序号、名称、字段路径和字符位置，不回显 handler 或凭据。单个配置错误隔离；顶层格式错误、订阅失败、数量超限或全部配置无效仍禁用题库。
- `contentType` 仅控制响应 JSON/text 解析。请求 `type` 默认 fetch；GM 模式下 `Content-Type: application/x-www-form-urlencoded` 使用表单，否则 POST 使用 JSON。fetch 模式 POST 使用 JSON，不因响应类型切换为表单。不自动补写用户未设置的 JSON Content-Type 请求头。
- GET 的 data 覆盖 URL 同名查询参数；URL 占位符值编码；嵌套 data 支持递归替换且不修改原配置。只有 data 第一层 handler 被编译。
- 结构化结果由 `normalize_results()` 统一保留 `question/answer/extra_data`；`select_answer()` 仍返回字符串或 None，缓存/回填接口不变。元数据不写入答案缓存，也不改变 AI 答案选择策略。
- 基础环境为 `title/options/type`，缺省图片环境为 `images=[]`、`suggestion_title=""`、`suggestion_options=""`，未知题型为 unknown。调用方可提供通过校验的扩展值。
- `api/question_images.py` 负责原图文本提取、受限下载、真实 PNG 转码及 `[图片N]` 映射。采集层在 OCR 前保留原图位置；仅引用图片扩展的 POST 配置才按需转换，同题多题库复用。失败图片不占编号，原文保留；相同 URL 去重、不同 URL 即使内容相同也保留位置。答案中的图片编号按本题映射还原。
- 图片下载仅接受 `https://p.ananas.chaoxing.com`（443），拒绝用户信息、其他端口及重定向。单图上限 5 MiB、每题 20 张、总请求体 16 MiB、16MP 像素、下载超时 8 秒；小图按比例放大至短边至少 14px。data URL 同样验证大小与真实像素，不将账号凭据发送到未知图片主机。其他图片主机目前按失败回退，不静默扩大允许范围。
- `Tiku.query_diagnostics` 是当前题的有界诊断快照（每题库最多 20 条候选摘要），保留来源、阶段、状态、耗时、选中结果和 ai/tags；缓存命中明确标记 cache，不借用上一题来源。异常不打印完整 URL/请求/响应，摘要脱敏且截长，不记录 base64。此快照不新增磁盘存储或 UI 面板。
- `no_answer` 可附带 `message`：取前 20 条 handler 结果中首条无答案且非空的字符串说明（`[说明, undefined]`），经 HTML 转纯文本、控制字符清理、凭证/URL/base64 脱敏后限制为单行 256 字符，并追加到现有任务日志供界面展示。没有说明时保留状态提示，不从原始响应猜测原因，也不硬编码供应商业务码。提示不参与答案选择或缓存；失败后仍继续查询后续题库。
- 候选筛选与回填共同使用 `api/answer_check.py` 的 `match_answer()`：完整文本精确命中优先、严格字母格式其次；长文本模糊匹配门槛 0.85、领先差值 0.10，短字符串/公式/图片 URL 必须精确匹配。数字、否定词与运算符差异阻止模糊匹配；重复/歧义选项和不完整多选不计入覆盖率。该策略是保守匹配，不是答案正确性证明。
- 选择题选项按标签与行解析，答案按题型拆分；简答/程序正文保持多行及语法符号。多空题按页面现有的 `answer{id}_N` 数量分别回填，数量不匹配走既有无答案流程。保存/提交开关、随机兜底和覆盖率阈值保持不变。
- 含图片的缓存键增加原图 URL 身份，文字题原键不变；不缓存 base64 或元数据，已有图片缓存不保证命中。

稳定协议参考 OCS `4.0` 提交 `890686a5e54f9a6d52d1169bae9ea5971e0863c7`；图片扩展参考 **dev** 提交 `419541ee6eea89cfac334717f24bed56cc63fe66` 的 `answerer-env.ts`、`work.ts`、`answer.wrapper.handler.ts`，不是稳定版 4.15.3 的保证。仍只支持受限 handler 表达式，不执行任意 JavaScript。

专项测试：`tests/test_ocs_tiku.py`、`tests/test_question_images.py`、`tests/test_answer_matching.py`，另由 OCR/缓存/实际 study_work Mock 测试保护调用链。离线测试不验证真实题库 token、额度、线上准确率或供应方服务。

### 上游兼容移植：隐私与只读结果确认

- 参考 Samueli924/chaoxing 的 [#637](https://github.com/Samueli924/chaoxing/pull/637)、[#625](https://github.com/Samueli924/chaoxing/pull/625)、[#626](https://github.com/Samueli924/chaoxing/pull/626)、[#630](https://github.com/Samueli924/chaoxing/pull/630) 和 [#631](https://github.com/Samueli924/chaoxing/pull/631)，按本地 OCS、会话与任务架构移植，不依赖被忽略的 `chaoxing/` 参考目录。
- `api/privacy.py` 在 Loguru 的共享 core 安装 patcher，覆盖直接导入 logger 和动态任务 sink。认证配置及 Cookie 值只登记在内存；`task_id` 作为内部路由键不得改写。通知目标与恢复配置不脱敏改写，正文、公开错误和日志脱敏；异常只保留清洗后的类型、说明和栈帧位置，不传递原始 traceback。
- `api/work_result.py` 只读提交前记录基线与提交后新增记录，按记录逐行配对次数/分数，缺失和并发多条新记录不猜测。最多三次结果列表读取，间隔约 4/8 秒以覆盖平台异步生成成绩，等待可取消，GET 有超时，不重放提交 POST；只读页面可能存在平台变体，未验证关联的成绩一律未知，并按未见新增记录/成绩尚未生成/并发多条区分原因。成绩与逐题解析能力不改变提交结果，不假设 100 是通用满分，不回写缓存或恢复旧 AI feedback。
- `CourseTools.iter_card_pages` 供资源扫描与复核共享原始页遍历；`read_only=True` 只允许三个课程/卡片读取端点，不自动处理验证码。严格模式遇到锁定、解析异常或无明确结束证据时失败，扫描器原有非严格模式保持兼容。
- `api/verification.py` 使用读取前后快照、明确平台总进度和原始附件交叉核对；不调用会写空页面进度的 `get_job_list`。缺失/不一致/不支持的任务保持 unknown，未完成为 pending，全量证据一致为 confirmed，取消为 cancelled。复核仅表示观测时的平台任务状态，不证明答题正确性或后续统计稳定。
- `CourseResult.verification` 独立于章节执行结果。Web 未确认时记 partial，但保留成功任务计数，不伪造任务失败、不进入重试队列；CLI 同样不通知全部完成。旧的无 verification 历史详情仍可查看，不重跑历史任务。公开详情沿用现有 JSON 传输，无新路由或 IPC 操作。
- 定向回归：`test_privacy`、`test_work_result`、`test_verification`、`test_study_results`、`test_answer_matching`、`test_ocs_tiku`，并覆盖任务日志、恢复、资源扫描及前端状态展示。所有夹具使用合成 HTML、mock 请求与临时文件，不代表真实平台在线验收。

### 任务记录迁移

任务恢复记录保存在 `study_tasks/<任务 ID>.json`，检查点只写入当前任务。

启动时发现旧 `study_tasks.json` 后，会先校验并拆分全部有效任务；成功后将原文件改名为 `study_tasks.json.migrated` 并原样保留。迁移失败不会删除原文件。新格式下，一个任务文件损坏只会跳过该任务并记录错误，不影响其他任务，损坏文件不会被自动覆盖。

**旧版本无法读取新格式，降级不兼容。** `.migrated` 只是迁移时快照，不包含之后的进度，不能当作最新状态恢复。需要降级时应使用升级前的完整数据备份。

### HTTPS 与验证码边界

题库请求默认验证 HTTPS 证书。关闭单个题库的证书校验不会全局屏蔽安全警告；Python 3.11/3.13 保留 urllib3 默认提示，避免临时过滤器影响并发请求。

验证码自动处理的框架与离线测试已接入，但真实自动提交仍保持关闭，尚无真实限流/通过样本作为验收依据。打包 OCR 自检通过只说明模型、资源与运行路径通过相应检查，不代表平台流程或识别准确率已经验证。

### 阅读与统计边界

专题阅读使用独立 Chrome / Edge 窗口与临时浏览器配置，在原阅读页面中滚动、通过页面的“加载更多”展开章节，由页面脚本上报时长。无需操作用户日常浏览器配置，也不依赖鼠标自动化工具。

仅接受具有阅读上报脚本且正文可滚动的页面；连接失败或已开始计时后的错误不会换书重跑。章节加载等待不计入阅读进度，短时任务不保证遍历整本书。上报成功、程序计时与平台最终统计是不同指标，不能互相替代作为验收结论。

## 反馈与贡献

提交问题时请说明版本、运行方式、复现步骤和脱敏日志。提交代码前运行相关回归，保持前端、桌面命令、权限声明及测试同步。

不要提交配置文件、账号会话、通知密钥、个人下载文件或本地运行日志。对外分发时请保留 [GPL-3.0 许可证](LICENSE) 及依赖的许可证信息。
