# Electron → Tauri 2 迁移实施

依据：`.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md`（基线 5899b5f，规划审查见同目录 review.md）。
本任务是实施任务，规划任务已归档，不重做已验证工作。

## 阶段

- P0 基线与可行性验证：本地已验证，外部审查待补
- P1 宿主与后端协议：本地已验证，外部审查待补
- P2 前端兼容与数据导入：本地已验证并归档，外部审查待补
- P3 Windows 打包与 CI：本地实施/构建已交付，外部审查与干净 runner 验收待补
- P4 候选版与回归：未实施
- P5 切换默认与清理：未实施

## P0 进展记录

### 环境核查（2026-09-07）

| 项目 | 结果 |
|---|---|
| Git HEAD | d4ae08f（工作区仅 plan.md 中用户加的 `/plan` 前缀改动，保留不提交） |
| rustc / cargo | 1.95.0（stable-x86_64-pc-windows-msvc），位于 `~/.rustup/toolchains/`；**rustup 代理与 `~/.cargo/bin` 缺失**，需用工具链内绝对路径或重建 rustup |
| MSVC | BuildTools 2022，MSVC 14.44.35207 + Windows SDK 10.0.26100 |
| **MSVC 检测问题** | `vswhere.exe` 缺失且 VS 注册表键不存在，cargo 无法自动定位 link.exe。需先 `vcvars64.bat` 或用 cargo `[env]`/`linker` 配置。项目级 `.cargo/config.toml` + `[env] LIB` 方案已实测可行（无需管理员权限） |
| WebView2 | 已安装 152.0.4191.66（x86 程序目录） |
| Node | 24.14.0（CI 为 20，差异保持区分） |
| Python | 3.11.9（系统）；PyInstaller 6.21.0；Flask 3.1.3 + Werkzeug 3.1.8（`make_server` 可用，可做端口握手线程化 WSGI） |
| 旧 Electron 数据目录 | `%APPDATA%\chaoxing-desktop` 存在：含 `cookies.txt`、日志、Electron profile；**无 renderer-session.json / web_config.json / .cookies**（该机器样本无会话文件） |
| Tauri CLI | 未安装（将通过 npm devDependency `@tauri-apps/cli` 引入） |

### Tauri 2 API 核对（官方文档，2026-09-07）

- `tauri_build::Attributes::app_manifest(AppManifest::new().commands(&[...]))`：autogenerate `allow-$command`/`deny-$command` 权限，配合 capabilities 授予；app 有 ACL manifest 时本地命令也需权限条目（webview/mod.rs 源码核实）。
- resources：`"dir/"` 递归复制保留结构；运行时 `app.path().resolve("...", BaseDirectory::Resource)`。
- single-instance 插件：`tauri_plugin_single_instance::init` 回调中 `set_focus()`。
- NSIS webviewInstallMode：默认 downloadBootstrapper；offlineInstaller 约 +127MB。

### PoC 结论（2026-09-07，证据 verification/poc-results.json）PoC 沙箱：`E:\Downloads\45\tauri-poc`（仓库外）。Tauri 2.11.5 + tauri-cli 2.11.4。

| 验证项 | 结果 |
|---|---|
| 最小窗口 + ACL 受限 command + invoke | 通过（AppManifest::commands + capabilities，本地命令无权限条目即拒绝的 ACL 机制生效） |
| 单实例（second instance 退出 + 聚焦） | 通过（tauri-plugin-single-instance） |
| 端口 0 握手 + token 鉴权 health | 通过（werkzeug make_server(threaded) 绑定 127.0.0.1:0；chaoxing-ready v1 单行 JSON；instanceId 核对；无 token/伪造 Host 均 401） |
| stdin 管道 + EOF 关停 | 通过（drop stdin → python EOF → os._exit(0)，2 秒内进程消失端口解绑） |
| 窗口关闭无孤儿 | 通过（WM_CLOSE → 宿主退出 → 后端消失） |
| 宿主强杀 Job Object 收割 | 通过（**关键修复：Job 句柄必须存活于应用状态中**，setup 结束即 drop 会立即杀死后端——已实测踩坑并修复） |
| CI 嵌套 Job 行为 | 未验证（本机无法模拟，需 CI runner 实测） |
| 真实冻结 onedir 资源加载 | **通过**（NSIS 安装包 146MB，安装后 host exe + backend/{chaoxing-backend.exe,_internal}；安装版启动 → resource_dir 解析 → 冻结后端启动 → health 200；宿主强杀 → Job Object 收割后端；卸载 /S 正常，仅残留运行时产生的 chaoxing.log） |

新增关键发现（影响 P1 设计）：
- **Job 句柄生命周期**：Job 句柄 drop（KILL_ON_JOB_CLOSE）会立即杀死后端。必须把句柄存入应用状态保存至进程退出（已实测踩坑）。
- **后端 cwd 写入安装目录**：当前 `app.py` 冻结态把 `chaoxing.log` 等运行时文件写到 cwd（即资源目录）。卸载后残留运行时文件。P1 实现必须为 Tauri 模式设置 `CHAOXING_DATA_DIR` 并让运行时文件落 AppData，不能沿用"cwd=安装目录"。
- 冻结后端 stdin 无管道时立即 EOF 退出（watchdog 行为正确）；宿主必须持有真实 stdin 管道。
- Git Bash 后台任务给子进程 /dev/null stdin，无法用于后端 smoke；必须用 .NET Process / PowerShell 持管道。

工程环境修复（本机专用，记录于 dev-env.ps1）：
- rustup 代理缺失 → 直接使用工具链 bin（PATH 注入）。
- MSVC 检测缺失（无 vswhere/注册表键）→ vcvars64.bat 提供 LIB/INCLUDE/PATH。
- crates.io 被墙（SNI 层 SSL 失败）→ aliyun sparse mirror（~/.cargo/config.toml）。

### P0 基线回归（2026-09-07，verification/p0-regressions.json）

| 检查 | 结果 |
|---|---|
| Python 3.11 unittest | 131 项通过 |
| `npm --prefix web test` | 4 文件 38 项通过 |
| `npm --prefix desktop test` | 6 项通过 |
| `npm --prefix web run build` | 通过（2.04s） |

### 量化基线（开发机，温缓存）

- 宿主 exe 8.3MB；NSIS 安装包 146.2MB（327MB onedir 压缩后）。
- 3 次温启动到后端 health 200：2.19s / 2.19s / 2.20s。冷启动与 p95 需 P4 干净 VM 实测。
- 宿主 WS 约 25MB；冻结后端 WS 约 91–94MB。

### P0 结论

PoC 门槛通过（CI 嵌套 Job 一项留待 P3 在 GitHub Actions runner 验证）。可以进入 P1。
P0 产出物在仓库外沙箱 `E:\Downloads\45\tauri-poc`，仓库内仅新增 `desktop/scripts/dev-env.ps1`（本机环境初始化）与任务记录。

### 下一步（P1）

1. Rust crate 正式骨架（desktop/src-tauri/）：backend.rs / windows_job.rs / api_proxy.rs / session_store.rs / migration.rs。
2. Python `CHAOXING_TAURI=1` 分支：make_server 端口 0 + chaoxing-ready v1 握手 + token 鉴权（PoC 已验证协议形态）；`api/logger.py` cwd 依赖在导入前处理。
3. 按计划第 7 节文件归属实施；测试先行（成功/错误/取消/退出路径）。

## P1 进展记录（2026-09-07）

实施计划：`C:\Users\RUD\.claude\plans\abstract-gathering-adleman.md`（用户已批准）。

### 完成内容

**Rust crate（desktop/src-tauri/，tauri 2.11.5 锁定于 Cargo.lock）**：
- `windows_job.rs`：Job Object 封装（KILL_ON_JOB_CLOSE、幂等 terminate、Drop 收割），PoC 代码移植 + 单测（树杀/幂等）。
- `session_store.rs`：desktop/session-store.js 1:1 移植——deny_unknown_fields、4096 上限、schema 严格（trim、控制字符、taskId 白名单、账号匹配）、tmp+rename 原子写、clear 删 tmp；26 项 lib 单测中 12 项来自此模块（Node 测试组 1-3 全部翻译）。
- `migration.rs`：旧数据一次性导入——6 项白名单、staging + rename、migration-v1.done 标记、junction/symlink 拒绝（reparse point 检测）、legacy lockfile 占用检测（推迟完善）、新数据不覆盖、损坏会话不导入。
- `api_proxy.rs`：8 operation 白名单枚举、taskId 白名单 `^[A-Za-z0-9_-]{1,128}$`、日志游标 u32 上限、请求 1MB/响应 2MB 上限、ProxyResponse `{status, body}` / ProxyError 形状。
- `backend.rs`：状态机 Starting/Ready/Stopping/Stopped/Failed；握手读取线程（>8192 行当日志，持续排空至 EOF）；stderr 排空写 backend.log；health 探测（token+instanceId 双重核对）；停止 stdin EOF → 5s 宽限 → TerminateJobObject；Job 存入状态至进程退出；缺 exe 显式报"后端程序缺失"不 panic。
- `lib.rs`：single-instance 先注册、setup 同步启动后端、7 命令注册（ACL 见 build.rs）、ExitRequested → 幂等 stop、host.log/backend.log。
- `bin/fake-backend.rs`：免 Python 假后端（FAKE_MODE 故障注入）。

**Python（计划第 7 步）**：
- `api/desktop_runtime.py`：parse_ready_env（缺失即 TauriEnvError，不回退）、register_token_guard（before_request 全路径 token+Host 校验；/api/health 原地增强回显 instanceId）、run_tauri_server（make_server 127.0.0.1:0 threaded + 握手行）。
- `app.py`：__main__ 新增 CHAOXING_TAURI=1 分支（置于 frozen/HEADLESS 之前）；普通模式零改动。
- `tests/test_desktop_runtime.py`：17 项（env 矩阵、ready 行、token guard 矩阵、无 CORS 头、普通模式无 guard、端口 0）。

**前端最小改动（计划第 9 步）**：
- `web/vite.config.js`：`strictPort: true`。
- `web/src/main.jsx`：Tauri 环境下非 ready phase 渲染 P1 占位状态页（仅 `__TAURI_INTERNALS__` 存在时生效）。
- `desktop/package.json`：`@tauri-apps/cli` + `dev:tauri`/`build:tauri`（Electron 脚本保留）。

### 检查结果（证据 verification/p1-rust-backend.json）

| 检查 | 结果 |
|---|---|
| cargo check / fmt --check / clippy -D warnings | 全部 PASS |
| cargo test（26 lib + 9 集成） | PASS |
| python unittest discover | 148 项 PASS（131 存量 + 17 新增） |
| npm --prefix web test | 38 项 PASS |
| npm --prefix desktop test | 6 项 PASS |
| npm --prefix web run build | PASS |
| 冻结后端 E2E（PyInstaller 重建 + TAURI 模式） | PASS：握手/health token/无 token 401/stdin EOF 退出码 0/**chaoxing.log 落数据目录而非安装目录（P0 cwd 问题修复实测）** |

### 集成测试覆盖的故障注入

错 instanceId → Failed；启动前退出 → Failed；缺 exe → 明确错误不 panic；stop 三连幂等；非 Ready 业务请求拒绝；非法 taskId 宿主级拒绝；404 状态码透传（token 注入生效）；孙进程树杀（stop 后无残留 ping）。

### P1 关键踩坑

1. **集成测试进程环境竞态**：FAKE_MODE 经进程环境传给子进程，cargo 默认并行跑测试导致模式串台（表现为握手超时）→ FAKE_MODE_LOCK 串行化。migration 单测同理（CHAOXING_LEGACY_DATA_DIR）→ ENV_LOCK。
2. **tauri-build 无 2.11.4 版本**（阿里云镜像只有 2.6.3）→ build-dependencies 回 `version = "2"`，由 tauri 2.11.5 的依赖图解析；Cargo.lock 提交保证可重现。
3. ureq 2.12.1 的 AgentBuilder 无 `no_proxy()` 方法（PoC 中实际用的是 redirects(0)；内网 127.0.0.1 不走代理由 no_proxy 环境兜底）——与计划记录的 API 略有出入，实际验证以代码为准。

### 下一步（P2）

1. 前端 adapter：Axios→invoke 桥接（保留 409/404、30s 超时、AbortSignal、日志游标语义），替换 main.jsx 占位页为正式启动状态 UI。
2. 两路并行审查（P0+P1 合并审查，调用 codeagent-wrapper 带超时）。

## 证据

见 `verification/`。

## P2 续接（2026-09-07）
独立阶段任务：../electron-to-tauri-p2。已保存 d4ae08f 未提交工作区基线与用户 plan.md 改动。P0/P1 两路带 300s 截止的 Claude 调用均 exit 1 无报告，未视为通过；P2 按该任务 plan.md 测试先行并行实施，整体迁移仍在进行。

## P2 本地交付（2026-09-07）

阶段档案：`.ccg/tasks/archive/2026-09/electron-to-tauri-p2/`，见 delivery.md、review.md 与 verification/final-results.json。P2 实现和本机验收已通过，外部审查仍待补，父任务与整体迁移继续 in_progress，P3–P5 未实施。

- Axios/invoke adapter、四会话命令、正式启动状态页已接好。保留409/404、30s/取消、单次JSON transform、after去重；不自动重试start或重启后端。
- 必要P1衔接修复包括非阻塞启动/HTTP、取消登记/退出竞态、严格对象及operation字符串契约、会话IO可见、Windows整目录事务导入/旧Electron运行检测，以及Python Tauri Origin/CORS边界。
- Rust 71 lib+30集成、Python151、Web125、Desktop6、fmt/check/clippy/build均通过。实际Chromium/Electron/Tauri模拟业务、旧Electron导入与真实回滚、原生权限/存储失败/取消/后端死亡、真实冻结后端隔离配置与400/404/关闭均通过；无本次后端残留。
- 当前桌面使用CDP加DOM click，不等同于物理鼠标或干净VM测试。P3 CI嵌套Job及P4权限/安装/原生交互矩阵仍待验证。
- 两轮双路Claude调用均有超时、均exit1无报告，正式两路明确429 Service Unavailable。独立复核问题均修复，不能代替所需外部审查。
- 保留初始P0/P1未提交基础及用户plan前缀；P2独立前端/工具直接提交，混合Rust/Python和依赖测试保留工作区，归档含已校验的增量补丁与来源哈希。不要clean/reset丢失这些工作。

## P3 接手（2026-09-07）

实际 HEAD aae12acc82c16a0ec5f5c8d8eb39c99c42ffbcc2，main；工作区与 P2 交接清单一致，索引为空。阶段任务 `.ccg/tasks/electron-to-tauri-p3/` 已建立。先核验交付哈希及混合基础来源，再整理原阶段提交；用户归档计划不暂存、不修改。P0/P1/P2 外部审查待补，P3 分析/审查均双路带超时。整体迁移继续 in_progress。

## P3 本地实施交付（2026-09-08）

本轮档案：`.ccg/tasks/archive/2026-09/electron-to-tauri-p3/`，以 delivery.md、review.md、reproduce.md、delivery-manifest.json 与 verification/final-results.json 为准。归档表示本地交付完成，P3 全部验收与整体迁移均未完成。

- 入场33项P2交付哈希全部匹配，没有重复应用补丁。既有P1基础独立提交 `9219fe24c25151af12c6c79a986b86018cd6a2ef`，P2保留的Rust/Python整合独立提交 `0d224f38f352f5822dfc31639df13b7b8d073675`；`api/desktop_runtime.py`仅有P2最终快照，没有伪造P1前像。P3增量51个源码文件提交 `6b2c5a2d93936cfeb9df0c44e9c7f4b1af5e24db`。
- 完整后端815文件/116目录staging，保留console/排除项/Flask内web/dist。独立NSIS和portable ZIP输出至desktop/release/tauri，版本保持1.1.1。NSIS825应用文件、ZIP826文件完整校验通过；宿主根据Tauri包类型标记与签名分别记录精确哈希。安装与卸载保留业务数据，保留Electron与回滚入口。
- WebView2原生前置检测、便携主动安装入口/离线说明、当前用户安装、NSIS路径与旧树预检完成。便携仍使用用户AppData。实际缺WebView2机器与真实产品安装/卸载未在本用户运行。
- 本地最终门槛：Rust114通过（71 lib+11 main+14 API+16 lifecycle+2 nested；1个专用子进程入口ignored）、Python3.11/3.13各160、Node20 Web125及构建、Node20 Desktop86（0skip）、fmt/check/clippy/test/release构建、完整包校验通过。Node20真实debug宿主23检查、冻结Python/后端smoke和Electron打包通过。CI YAML23个run step与40个PowerShell文件解析通过。
- 双路P3分析（300s）、P0/P1/P2补审（240s）、最终P3审查（300s）共6次调用均exit1、无正文，实际错误均429 Service Unavailable。独立本地最终审查无新增Critical/Warning，不替代外部通过。
- 本地构建工具为Node24，Web/Desktop和真实debug smoke另在Node20执行；Python3.11与3.13均已验证。没有可用签名私钥，产物实际NotSigned；签名成功路径未实签。GitHub CI、干净用户release host与安装/卸载、缺WebView2矩阵仍待实测；没有push/tag/Release或真实账号操作。
- 用户归档plan.md的`/plan`前缀与全部字节保持不变（SHA256 f7ec970826f014e9975a056b2e73ee9bee5dd7323f75b06db79fb7b78cdb5167），永未暂存；poc-window.png保留。自动审批拒绝清理两个本轮临时硬链接目录，仅返回blocked by policy，路径列于P3交付记录；没有绕过重试。

下一步：服务恢复后补做P0/P1/P2与P3双路外审；经用户另行安排将这些本地提交带入远程Windows runner，验证真实release安装/便携及nested Job。P4完整干净系统矩阵与P5默认入口切换继续后置。
