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
