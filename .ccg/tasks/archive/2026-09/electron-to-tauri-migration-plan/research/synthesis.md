# 两路分析的综合取舍

两路通过用户指定的 `codeagent-wrapper.exe --backend claude` 并行执行，角色均为 analyzer、检查视角不同。实际命令都使用 claude backend，不将其描述成两个不同模型供应商。退出码均为 0。

## 采纳

- A 核实不存在 safeStorage；会话迁移聚焦 v1 JSON 和 legacy localStorage 可见性。
- A 倾向本地 React 页面，避免动态 Flask 页面获得桌面 IPC 权限。
- B 推荐把完整 PyInstaller onedir 目录作为 resources，保留 `_internal` 邻接关系。
- B 指出 stdin EOF 实际调用 `os._exit(0)`，以及数据路径/WebView2/便携 ZIP/CI 的关键约束。

## 经源码与官方文档纠正

- A 把 stdin 关闭称为优雅退出，与 `app.py:743` 不符；计划明确已有中断语义，不保证 finally/atexit 或任务续作。
- B 建议把 Tauri identifier 改成 `chaoxing-desktop` 来匹配旧目录，不采用。identifier 保持稳定，旧数据通过独立的路径解析与一次性导入兼容。
- B 的“sidecar 仅支持单文件”表述过于绝对：externalBin 可以与依赖 resources 组合，只是不自动收集 onedir；首选完整 resources 是减少布局风险的工程选择。
- A/B 的 localhost 直连建议不是最终决定。计划采用本地页面 + 受限 Rust 代理，避免为渲染器增加通用 HTTP/CORS 权限；为新增 adapter 明确错误、取消和超时测试成本。
- 不能只添加 capabilities 就认为所有 custom commands 自动受限。已对照 Tauri 2 AppManifest 文档补充 build.rs 命令 ACL，明确负向 WebView2 测试。
- B 的“后端零改动”只适用于保持全部旧行为的方案；推荐方案需要 Tauri 专属启动/鉴权分支，并限制为少量 runtime 文件，不重写业务逻辑。
- 桌面 README 不作为 onedir/卸载/体积的唯一事实依据；当前 build spec 与 electron-builder 配置优先。

## 基线变化

最初读取工作区时有用户已暂存和未暂存变更；独立的已有工作在本任务调研期间提交为 `d384ded`，随后工作区变为干净。全量检查在该提交运行。

后续独立提交 `5899b5f` 仅把 `desktop/package.json` 的测试入口从 glob 改成 `node --test`，用于 Windows Node 20 发现测试。已核对 diff 并重跑 6 项桌面测试；计划最终基线更新为该提交。本任务未修改或提交这些业务变更；本地 Node 为 24.14.0，不声称已运行 Node 20。

## 仍需 PoC

Job Object 注册与嵌套行为、真实冻结后端的端口握手、Windows 原子替换、实际旧数据/安装路径、WebView2 capability/CSP、便携资源布局及无运行时启动提示，都不得仅凭文档宣称已验证。
