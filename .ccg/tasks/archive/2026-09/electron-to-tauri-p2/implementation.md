# P2 实施记录

## 续接

2026-09-07：复杂度 L、风险 high（IPC/API 契约与数据导入）。HEAD `d4ae08f44772ef68cffb227feb450c05b84a9d9b`，分支 main。未覆盖初始改动，基线见 verification/baseline。现有父 task.json 实际已为 P2-in-progress；P0/P1 的已验证事实直接续用。

接手检查发现 P1 的会话命令尚未返回会话、不能表达 rememberTask(null)，同步宿主启动/请求也会阻塞启动状态及取消；现有 migration 发布是逐文件步骤。P2 需先定义失败用例并修复这些衔接点。已有 review-p0p1-a/b.md 仅作为待核对线索，不能替代本轮带超时的两路调用。

## 分析与计划

两路独立 Claude 分析 + P0/P1 审查同时启动；每路 300 秒上限，CODEX_TIMEOUT 与外部进程树截止双重约束。PowerShell 使用两个同时存活的 Process，等价于 Bash `&` + `wait`，不串行调用。原始提示、stdout/stderr、退出码、时间留在 research/ 与 verification/。

两路调用均 exit 1，无 stdout 报告；分别 12:46:02Z / 12:46:09Z 结束。未取得外部通过结论。计划见 plan.md，继续测试先行与并行实施。

## 实现与先行验证（2026-09-07）

- 前端代理：adapter/bridge/session/startup 测试先红（新模块未存在、会话严格性等8项失败）后绿；全量 web 123项/8文件 + build 通过。App.jsx/taskPolling.js 未修改。@tauri-apps/api 精确2.11.1。
- 主代理 session：先行7项测试5失败（必填null键、漏字段、空taskId、UTF16边界、新目录）；修复后原有+新增20项通过。Windows被占用原子替换实测保留原文件；bounded read及host串行store已接。
- 生命周期代理：API先行13项10失败、生命周期7项6失败；修复后API14/14 + 生命周期16/16 + DTO6/6 +登记表3/3通过。实际stalled409 body等30s验证超时。初次并行cargo内存1455，改-j1后通过，后续统一单构建。
- 迁移代理：先行15项10失败，singleton额外1失败；完成后30项通过、0 ignored，含真实子进程三阶段exit73重试、junction、严格新数据不覆盖、源快照不变。实际旧Electron窗口检测仍须GUI验证。
- Python：真实app import隔离3项先红2项后绿；20项runtime+entry契约通过。Tauri模式不再解析旧端口/CORS，Origin请求拒绝，普通浏览器CORS保留。
- Chromium真实浏览器模拟业务通过，原始结果p2-smoke-browser.json。首轮Electron模拟登录未进入课程，作为待排查失败保留，尚未声称三宿主验收通过。
- 独立审查发现启动页丢弃host.error，已先加3项失败断言，再修正文案展示及旧数据警告；不会把可诊断失败仅显示泛化提示。
- 外部两路无报告原因已查证：对应Claude会话均为 `API Error: Request rejected (429) · Service Unavailable`，不是代码通过或主观判断；后续将带超时重试正式审查。

## 最终验收进展

- 全量 Web 125/125、Python 151/151、原 Desktop Node 6/6 通过；输出分别见 verification/*-final-tests.txt。
- 真 Chromium、原 Electron main/preload/session、Tauri native invoke 已通过同一模拟业务流程；各只提交一次 start，after 前三次为 [0,1,1]。GUI 驱动使用可见/可用断言后的 DOM click；当前 Windows 桌面未送达 CDP 原生鼠标事件，不能声称物理鼠标验证。
- 初次 Tauri 关闭失败是测试脚本向同 PID 的隐藏 dispatcher/COM 窗口也发送 WM_CLOSE。保留两次失败 JSON（包括先断开 CDP 仍失败），修正为仅应用标题匹配的主窗口后，宿主 exit 0，约 1s 完成，后端无残留。产品退出逻辑无需修改。失败清理只针对捕获的测试 PID，保证脚本出错不会留下测试宿主。
- 正式 P0/P1/P2 双路外部审查同时于 13:41:22Z 启动，240s 上限，分别 13:44:39Z/13:44:49Z exit 1，无 stdout；对应会话 da8cf851/3a1e661e 均为 429 Service Unavailable。外部双路审查仍未通过；不把独立子代理复核等同于外部通过。
- 独立审查发现的 Serde 派生结构接受 positional arrays 已修复：IPC envelope 与所有嵌套 API/session DTO 强制 object；先行6项失败，修复后7/7通过。合法对象和显式null语义不变。

## 本地验收结果（2026-09-07，全部通过）

- Rust fmt/check/all-target clippy(-D warnings)/test + custom-protocol 宿主构建通过。Rust 71 lib + 14 API 集成 + 16 生命周期集成；含真实 30s 超时、30 项迁移中断/安全单测。统一 --locked -j1，无 lint 豁免。见 rust-final-gates.json。
- 原生 operation 对象类型宽松也已修复：ApiRequest operation 必须先反序列化为 String，再复用八操作枚举。Rust 负例和真实 Tauri IPC 负例都先失败后通过；合法八操作未改变。
- 当前宿主同一模拟业务完整通过：单次 start/409恢复，after=[0,1,1] 与日志去重，终态补拉，刷新恢复，404仅清任务，退出清账号。真实 Chromium/Electron/Tauri 的结果分别见 p2-smoke-{browser,electron,tauri}.json。
- 真实 Tauri IPC 拒绝12项非法调用，含未知命令、未知字段、数组、非字符串operation、路径穿越和未授权的创建窗口命令。CSP frame-src 阻止frame加载，导航限制阻止外部页面。原生早取消/进行中取消通过，HTTP等待期间 backend_status 约2ms返回。
- Windows 实际临时文件路径被目录占用时：账号保存、任务保存及会话读取失败在界面可见，localStorage 哨兵保持不变。随后直接结束本次假后端，Ready转Failed，重新检查不spawn/restart。
- 缺后端不挂载App，重新检查保持Failed；8s延迟握手期间关闭窗口及时结束宿主与后端。正常关闭以主窗口 WM_CLOSE 实测，全部 exit0，无本次宿主/后端残留。
- 真实旧Electron启动时，Tauri导入被延迟且不spawn后端；关闭旧版后导入会话/选课成功，migration-v1.done存在。源会话SHA256未变，Node可读Rust v1，关闭Tauri后实际重新运行旧Electron成功恢复任务。首次脚本在已关闭Electron对象调用process()报错，现改为启动时捕获PID，保留失败记录。
- 最新 PyInstaller onedir 真后端通过Tauri原生转发：配置读写/落盘200、空登录/课程/start输入均400（创建账号客户端/任务前失败）、不存在任务状态/详情/日志404。后端chaoxing.log在隔离data目录，安装目录日志未改变；窗口关闭后host与真实后端退出。未访问上游账号或创建学习任务。
- 隔离data文件的Windows ACL继承当前用户私有目录，owner为当前用户，Everyone/Authenticated Users/Users无Allow规则。该验证不代替P3/P4安装目录与不同Windows用户ACL矩阵。
- 真实WebView2截图已查看：启动失败原因完整可读，任务持久化失败提示可见。驱动限制仍是DOM click而非物理鼠标；后续干净VM人工/原生输入矩阵留给P4。

- 最后脚本审查发现浏览器/Electron启动尚未返回stop时抛错会遗漏清理。使用缺失Chrome可执行文件实际注入：修复前8s不退出并需外部清理捕获测试进程树；修复后主动exit1且fixture已退出，不需要强杀（launch-failure-{red,green}/result.json）。两个launch分支现在try/catch/finally清理，正向browser/electron流程重跑通过。
- smoke入口拒绝未知selection并exit1，避免拼错测试名而零检查成功（selection-validation/result.json）。默认输出改为ignored target/p2-smoke-evidence；P2_EVIDENCE_DIR可指定任务证据目录，归档后不会意外重建活动任务。

## 归档与提交

P2 独立前端和测试工具源码提交：98c7ffe3f9ecb4cf24f2cbf2460ffabbb5d8725f（17文件）。P1基础及混合Rust/Python文件不纳入该提交；增量补丁已在隔离fixture仓库应用并与当前源码逐文件对比一致。用户plan编辑的SHA256与接手时一致。

本轮实现交付归档至 .ccg/tasks/archive/2026-09/electron-to-tauri-p2，状态 archived_review_pending：实现/本地验收通过，外部双路审查仍待恢复；父任务继续活跃。归档提交只选择本轮记录/证据/补丁，不包含用户plan源文件、其复制品或含该编辑的完整初始diff。
