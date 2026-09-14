# 前端与桌面约定

- 选课按账号保存，仅恢复与当前课程列表的交集；空选择或加载失败时禁用开始，不能隐式改为全部课程。
- 活动任务入口保留到终态；409 响应恢复已有任务。后端端口会改变，桌面持久化通过受控 IPC，不能依赖随机 origin 的 localStorage。
- 退出操作在等待存储之前同步锁定任务启动。异步响应必须检查账号代次、请求身份和取消信号，包含成功、409、持久化失败和 finally 分支。
- 任务启动后的恢复信息保存失败在实际进度页显示，并绑定账号和任务，避免旧失败提示污染新账号。
- 下一轮轮询等待前一轮的状态、详情和日志请求全部结束；销毁时取消请求。看到终态仍需成功取得最终详情和日志，补拉失败可重试。
- 日志按序号去重、游标递增，前端最多保留 500 条；后端截断或前端裁剪时给出可见提示。
- preload 只暴露明确命名的持久化操作；主进程校验当前主窗口、主 frame、精确后端 origin 和严格参数结构。仅保存账号、任务 ID 和状态，不保存密码。

回归入口：`npm --prefix web test`、`npm --prefix desktop test` 和 `npm --prefix web run build`。CI 中每个原生命令失败必须传播为步骤失败。

桌面测试使用 `node --test` 自动发现标准命名测试；Windows 上的 Node 20 不会展开命令行里的 `tests/*.test.js`。涉及运行命令兼容性的改动需用 CI 配置的 Node 版本验证。

Windows runner 的 `TEMP/TMP` 可能包含 `RUNNER~1` 等 8.3 短名。测试夹具调用 `mkdtemp(os.tmpdir())` 后先登记清理，再对新建目录调用 `realpath`，之后才派生预期路径与所有权标记；否则会与 Node/PowerShell 返回的长路径不一致。使用真实短名 TEMP 和 CI 的 Node 版本复现，不放宽所有权或 junction 检查。


## Tauri 2 桌面边界（P2）

- Tauri 的会话读写失败必须保留为可见错误，不能读取或回写 localStorage；普通浏览器与 Electron 兼容分支分别验证。
- Serde `deny_unknown_fields` 不代表仅接受 JSON object：派生结构也接受数组，枚举也接受对象。IPC envelope、嵌套 DTO 与磁盘 v1 会话应显式要求 object，operation 应先要求 String，再匹配白名单；可空但必填的键需要单独校验。
- 真实 Windows 关窗 smoke 仅向捕获的宿主 PID、准确主窗口标题发送 WM_CLOSE。不要关闭该 PID 的隐藏 dispatcher/COM 窗口；它们被关闭会破坏退出流程。
- GUI smoke 的浏览器、Electron、Tauri 启动与断言失败都必须关闭已创建的测试进程；未知场景名应失败，不能零检查报成功。DOM click 与物理鼠标输入要区分记录。

## Windows Tauri 打包与验收（P3）

- PyInstaller onedir 以完整目录树 staging，并用 Tauri resources 目录映射；包内容按版本、目录、长度和 SHA256 校验。保留供 Electron 使用的 Flask 内 web/dist，不只复制后端 exe。
- Tauri CLI 2.11.4 会将 NSIS 宿主的包类型标记 UNK 改为 NSS，打包后恢复未签名原文件。NSIS/portable 必须分别记录完整宿主哈希；签名回调捕获的 pre-sign 哈希要与独立计算结果一致，恢复后的 portable 宿主另行签名。打包回调不得改写已登记在后端清单中的第三方依赖字节。
- `Get-Command node/pwsh/7z -CommandType Application` 可能返回多个安装路径；调用单个程序时显式选择首项。跨进程 JSON 管道用明确的 UTF-8 编解码，中文窗口标题需由真实窗口夹具验证。
- release 宿主/安装 smoke 只允许 fresh Windows runner 或明确声明的专用 Windows 用户/VM；debug 覆写不能证明 release 隔离。检查进程树清理必须基于已捕获的身份/Job，supervisor 意外退出不等于已验证无残留。
- NSIS 路径表中的上游空目录祖先表示安装根；由根路径检查覆盖，不能作为空相对路径误拒绝。安装/卸载预检包含旧版本独有目录；该预检不保证抵抗检查后的并发路径替换。


## Windows 隔离验收续接

- 优化后的 release 二进制可能把命令参数比较拆成重叠 SIMD 常量；完整明文搜索不能证明原生参数不受支持。仅在可丢弃用户/VM、真实 known-folder 预检和 ownership claim 后，用已捕获 Job 调原生无副作用探针并核对退出码；Debug 保留执行未知程序前的拒绝边界。
- 无论 probe、GUI 启动失败还是正常/强制关窗，删除本轮 profile 之前都要有显式 verified=true 的清理报告、空 remaining、非空且全部已退出的 captured observed。supervisor 退出或缺报告不是已验证清理；应保留数据和原始/清理异常，不能把 fallback 清理记为生命周期通过。
- Windows 的 git archive 可能按 checkout 换行规则输出 CRLF。将文件按原始字节固定哈希时，要对本次导出命令明确 core.autocrlf/core.eol 并提前核对导出内容，不修改全局设置、不静默归一化哈希证据。
- NSIS 解码器需按实际包验证，不能只要求“近期7-Zip”。本轮官方7-Zip25.01遇到 BadCmd=13，原P3与本轮完整成功解码器为NanaZip6.5.1767.0；execution alias不是真实二进制。复制工具时记录真实exe/DLL来源及完整哈希，保留拒绝不完整解析的门槛。
