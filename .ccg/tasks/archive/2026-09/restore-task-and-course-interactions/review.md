# 审查结果

用户明确要求不调用双模型；已停止最初两路外部分析，本次实现与最终审查由主代理完成。

## Critical

- 无未解决项。

## Warning

- 无未解决的阻断交付项。测试范围和旧任务兼容限制见下文。

## Info

- 后端在启动 worker 前原子保存恢复参数，不保存登录密码。进程退出后恢复同一任务 ID，校验账号并原子领取，避免重复启动；终态任务保存结果且继续沿用原 TTL。
- 恢复重新读取平台进度；创建/恢复写盘失败回滚，终态写盘失败保留内存结果并提示。任务缺失用专用 TaskNotFound 区分，不把上游 KeyError 当作任务 404。
- 前端在恢复完成后才轮询，恢复失败可重试；真正缺失的旧记录清除任务 ID 并返回选课。保留退出锁、账号代次、请求取消和迟到响应保护。
- GitHub 共用固定仓库地址。Tauri 的无参数命令保留主窗口、来源和参数校验；Electron 只允许该固定 HTTPS 地址交给系统浏览器，拒绝新 renderer，并将新增模块纳入打包清单。
- 无保存记录时选课为空；保留同账号明确保存的有效选择。Ctrl/Cmd+A 全选当前筛选结果，保留其他已选项，不拦截输入框和可编辑区域的文本全选。
- 日志更新仅滚动日志容器；用户向上翻阅时暂停跟随，不调用 focus 或 scrollIntoView。
- 用户原有 frontend spec、旧任务 plan 和 poc-window.png 保留。本任务只追加 backend spec 中的恢复约定。

## 验证

| 范围 | 结果 | 覆盖与证据 |
| --- | --- | --- |
| Python 完整测试 | 176 通过 | `python -m unittest discover -s tests -v`；见 `python-tests.log`。新增恢复回归包含真实子进程 os._exit、账号隔离、并发、终态、磁盘失败及平台进度。 |
| Web 完整测试 | 141 通过 | `npm --prefix web test`；包括恢复/退出竞争、课程选择、桌面桥接和用户交互。最后整理 JSX 缩进后，复跑 UserInteractions.test.jsx 的 6 项交互测试通过。 |
| Desktop Node 完整测试 | 97 通过 | `npm --prefix desktop test`；包含仓库外链白名单及拒绝其他地址。 |
| Rust 完整测试 | 114 通过，1 个辅助测试忽略 | subprocess helper 按设计 ignored；涵盖恢复请求白名单及仓库命令验证。 |
| Rust 编译与静态检查 | 通过 | fmt、check --all-targets、clippy --all-targets -- -D warnings；使用锁定依赖。 |
| Web 生产构建 | 通过 | `npm --prefix web run build`。 |
| Chromium 界面 smoke | 通过 | `evidence/browser/p2-smoke-browser.json`，success 为 true；附课程页和进度页截图。 |
| Electron 界面 smoke | 通过 | `evidence/electron/p2-smoke-electron.json`，success 为 true；附课程页和进度页截图。 |
| 差异检查 | 通过 | 产品改动通过 `git diff --check`；归档中的原始 Python 日志含空白测试样本及日志行末空格，不纳入源码空白检查。最后仅整理搜索框 JSX 缩进，无行为变化。 |

界面 smoke 使用真实浏览器引擎和隔离 HTTP 夹具；点击通过元素可见、可用检查后执行 DOM click，Ctrl+A 使用键盘输入。覆盖保存配置、409 恢复、刷新恢复、404 清除、账号切换、默认空选、输入框全选及页面滚动/焦点保持。普通浏览器仓库 popup 使用合成响应；Electron 在实际主进程观察 shell.openExternal 调用，不启动用户浏览器。

## 兼容与验证边界

- 升级前已经丢失、且没有保存执行参数的任务无法仅凭旧 ID 重建，需要重新开始一次；此后新建任务可以退出续接。
- 关闭软件期间不继续后台执行；再次打开并登录同一账号后恢复。恢复依赖平台实际保存的进度。
- 未使用真实账号、未执行真实学习任务，未运行 Tauri GUI，也未生成发行安装包。Tauri 变更已通过编译、测试和静态检查。
- 本次仅提交任务归档，产品代码与 backend spec 改动保留在工作区。
