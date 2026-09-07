/plan
请接手 chaoxing-gui 桌面版 Electron→Tauri 2 迁移的 **P3 阶段实施：Windows 打包与 CI**，并补做 P0/P1/P2 尚未通过的外部审查。

仓库：`E:\Downloads\45\chaoxing-gui`，Windows / PowerShell，分支 `main`。请先核对实际 `git status`、HEAD 和任务记录，再续接；不要重做已完成阶段。

## 1. 已完成状态与证据

- 父任务：`.ccg/tasks/electron-to-tauri-implementation/`，仍为 `in_progress`，`currentPhase=P2-local-verification-complete-review-pending`。P0/P1 已验证；P2 实现和本地验收通过，外部审查未通过，整体迁移未完成。
- P2 参考提交：`98c7ffe3f9ecb4cf24f2cbf2460ffabbb5d8725f`（前端兼容与 smoke 源码）、`ed1a6e271823a17253b3d77f248515eba67fb807`（P2 归档）。之后另有交接文档提交，不要求 HEAD 仍等于这两个提交。
- P2 已完成：Axios→`api_request/api_cancel` adapter、四个会话命令桥接、正式启动状态 UI、旧数据事务导入，以及必要的 Rust/Python 边界与生命周期修复。继续保持 409/404、30 秒超时、AbortSignal、JSON transform 仅一次、日志 after 去重；绝不自动重试 `/start`。会话失败可见且不回退 localStorage；启动页的重新检查不重启后端。
- 最近本地质量门：Rust **101**（71 lib + 14 API + 16 lifecycle）、Python **151**、Web **125**、Desktop **6**；cargo fmt/check/clippy/test、web build、嵌入 Web 的 debug Tauri 宿主构建通过。
- 真实 Chromium、原 Electron、Tauri 的合成业务流程，以及旧 Electron 运行时阻止导入、关闭后导入、源文件不变、原 Electron 重启回滚通过；原生 IPC/取消/存储失败/后端死亡和真实冻结后端隔离 smoke 通过。
- 边界：GUI 使用 CDP + 可见/可用断言后的 DOM click，不能当作物理鼠标或干净 Win10/Win11 验收。本次 P2 本地使用 Node 24 / Python 3.11，不等于 CI Node 20 / Python 3.13 已验证；现有 smoke 也不等于 release 安装验证。

## 2. 必读资料

1. 父任务的 `task.json`、`implementation.md`、`verification/p2-final-handoff.json`，包含 P0/P1/P2 进展、环境问题和后续待办。
2. `.ccg/tasks/archive/2026-09/electron-to-tauri-p2/` 中的 `delivery.md`、`review.md`、`reproduce.md`、`delivery-manifest.json`、`verification/final-results.json`、`verification/rust-final-gates.json`。
3. `.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md` 的 §3.2、§4.2、§4.3、§5、§6 P3、§7、§8；原计划中的历史“尚未实施”描述以当前任务和证据为准。
4. `.ccg/spec/backend/index.md`、`.ccg/spec/frontend/index.md`；检查并遵守适用的 `AGENTS.md`（上一轮仓库内未找到）。按 CCG 管理任务、规划、并行实施、审查与归档。

## 3. 工作区保护与可复现前提

**当前工作区才是经过验证的 P1/P2 整合结果；干净检出仍缺少未提交的 P1 基础。** P2 源码提交只包含 17 个明确归属的前端与 smoke 文件，不能直接拿它当完整迁移代码。

交接时仍有以下改动，以进场检查为准：
- 已跟踪但未提交：用户的归档 `plan.md`、`.gitignore`、`app.py`、`desktop/package.json`、`web/vite.config.js`。
- 未跟踪：`api/desktop_runtime.py`、`desktop/rust-toolchain.toml`、`desktop/scripts/dev-env.ps1`、`desktop/src-tauri/`、`tests/test_desktop_runtime.py`、`tests/test_tauri_entry_contract.py`、`poc-window.png`。

先按 P2 `delivery-manifest.json`、`patches/p2-on-preexisting-p1.patch` 和 `patches/retained-final-snapshots/` 核对来源，保留混合文件的当前整合结果。补丁已在隔离 fixture 仓库验证，不要盲目重复应用；`api/desktop_runtime.py` 仅有最终快照，没有接手时的逐字节原始副本。

为使 P3 的干净检出 CI 可复现，应将核实属于迁移的既有 P1/P2 基础按原阶段整理为独立、可审查的提交，再提交 P3 增量。不得 `git add .`、reset/clean、覆盖其他实施者改动，或将旧阶段代码冒充 P3。用户在归档计划里加的 `/plan` 前缀必须逐字节保留，**永不暂存/提交该文件**。`.ccg/` 当前被忽略，必要时只对本任务明确路径使用 `git add -f`。

## 4. 尚未通过的外部审查

必须使用 `C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude` **同时启动两路**，调用带明确超时和进程树清理，记录 prompt、stdout/stderr、退出码与实际结论。

- P2 初始两路上限 300 秒、最终 P0/P1/P2 两路上限 240 秒；均 exit 1、无正文审查报告，实际错误为 **429 Service Unavailable**。更早规划阶段超时也不算通过。本地独立审查发现均已修复，但不能替代外部审查。
- 本轮补做 P0/P1/P2 两路审查，并完成 P3 自身的双路分析与最终审查；审查材料要包含未跟踪源码，不能只提供 `git diff`。
- 可参考 P2 `verification/run-claude-pair.ps1`，但要修正归档后失效的相对仓库路径，将输出写入本轮任务目录，不能覆盖历史证据。
- 若仍失败，如实保留证据，继续可独立完成的工作；不得写成已审查通过，也不得因此跳过可完成的实施与验证。

## 5. 本轮 P3 交付范围

1. **完整后端 staging**：实现 `desktop/scripts/prepare-backend.ps1`，保留 PyInstaller onedir 的 `chaoxing-backend.exe`、完整 `_internal` 和其他依赖层级；Tauri resources 使用目录映射，不只复制 exe，不使用平铺 glob。保留现有 PyInstaller console/排除项和 Electron 所需的 Flask 内 `web/dist`。
2. **NSIS 与便携 ZIP**：完善 Tauri 配置、图标、`package-portable.ps1`、`build_tauri.bat` 等构建入口。使用独立宿主 exe、安装目录及 `desktop/release/tauri/` 产物路径，明确 setup/portable/version/windows-x64 命名，避免混入 Electron 发布 glob。当前用户安装、卸载保留业务数据；保留旧 Electron 程序、数据与回滚链路。便携默认仍使用用户 AppData，不新增 USB 自带数据语义。
3. **WebView2 策略**：常规 NSIS 在线 bootstrapper，明确离线变体或配套离线安装说明；便携必须包含完整资源并有可执行的运行时安装指引/启动器或已验证提示。缺失 WebView2 的处理必须在创建 WebView/React 之前生效。
4. **版本与签名**：以 `pyproject.toml` 为单一版本来源，同步/校验 Cargo、Tauri、web/desktop package 与 lock、artifact，以及存在时的 tag。不要自行升版本或创建 tag。核实签名证书是否实际可用；有证书才签名验证，无证书如实记录。
5. **CI 与打包验证**：增量修改 `.github/workflows/main.yml`，加入固定 Rust/cache、fmt/clippy/test/release build、包内容检查、真实宿主 smoke，验证 Windows runner 的**嵌套 Job Object**。保留 Python 3.11/3.13、Node 20 前端测试与构建、Electron 测试与打包、独立 Python 发行。每条 PowerShell 原生命令都传播非零退出码；后台进程必须隐藏窗口、持有真实 stdin 管道，并有超时和 finally 清理。同步必要的 README/使用说明。

目标验收：干净 Windows runner 生成版本一致、内容可校验的 NSIS/ZIP；真实打包宿主在无系统 Python 的条件下运行 health/合成业务，退出无残留；测试失败阻断后续发行路径。P4 的完整干净系统矩阵和 P5 的默认入口切换/删除 Electron 不纳入本轮。

## 6. 实施与验收约束

- 先核对基线、更新父任务并建立 P3 阶段记录，完成分析、计划与文件归属，再按 CCG 使用 `fork_turns="none"` 并行实施；子代理不再派生代理，不覆盖彼此文件。每步更新 `task.json`/`implementation.md`，证据放本轮 `verification/`。
- 测试先行：先定义成功、缺资源/启动失败、取消与退出路径，再实现；先假后端故障注入，再真实冻结后端。复用 P2 工具时读 `reproduce.md`，证据改用新的 `P2_EVIDENCE_DIR`，避免覆盖 P2 归档。
- 本机先执行 `. desktop/scripts/dev-env.ps1`。Rust 1.95.0、Tauri 2.11.5 已锁定；Cargo build/check/test/clippy 使用 `--locked -j 1`，并行曾触发 Windows paging-file 1455。rustup 代理/MSVC 检测/阿里云镜像细节见父任务记录。PyInstaller spec/work/dist 与仓库保持同在 E:，避免跨盘 relpath 错误。
- **隔离变量仅对 debug 有效**：`CHAOXING_TAURI_DEV_ROOT`、`CHAOXING_TAURI_DEV_BACKEND`、`CHAOXING_TAURI_DEV_HIDDEN` 不能用于假定 release/安装宿主已隔离。真实 release 验证使用隔离 Windows 用户/VM；条件不足就记录未验证项，绝不让测试导入真实账号数据。正常关窗只针对捕获的 PID 和准确主窗口标题，不能关闭隐藏 dispatcher/COM 窗口。
- 不自动发布 Release、push 仓库、访问真实账号或启动真实学习任务。若当前权限/环境无法运行远程 CI，完成本地代码、构建和可审查产物，明确记录 CI 未执行，不能宣称 P3 全部验收通过。
- 只提交核实属于迁移的变更，区分既有基础与 P3 新增。按 CCG 归档已完成的阶段交付，未通过的门槛继续保留待办；父迁移任务保持未完成，不把局部实现、归档或本机通过等同于整体迁移完成。

请直接推进实施，交付可复查的代码/产物、验证证据、外部审查实际结果、保留改动清单和下一阶段待办。
