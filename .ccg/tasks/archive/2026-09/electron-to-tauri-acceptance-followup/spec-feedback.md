# 本轮规范反馈

以下经验追加到本机 ignored `.ccg/spec/frontend/index.md`，本文件保存本轮新增文字，不把既有 ignored 规范整体强行入库。

## Windows 隔离验收续接

- 优化后的 release 二进制可能把命令参数比较拆成重叠 SIMD 常量；完整明文搜索不能证明原生参数不受支持。仅在可丢弃用户/VM、真实 known-folder 预检和 ownership claim 后，用已捕获 Job 调原生无副作用探针并核对退出码；Debug 保留执行未知程序前的拒绝边界。
- 无论 probe、GUI 启动失败还是正常/强制关窗，删除本轮 profile 之前都要有显式 verified=true 的清理报告、空 remaining、非空且全部已退出的 captured observed。supervisor 退出或缺报告不是已验证清理；应保留数据和原始/清理异常，不能把 fallback 清理记为生命周期通过。
- Windows 的 git archive 可能按 checkout 换行规则输出 CRLF。将文件按原始字节固定哈希时，要对本次导出命令明确 core.autocrlf/core.eol 并提前核对导出内容，不修改全局设置、不静默归一化哈希证据。
- NSIS 解码器需按实际包验证，不能只要求“近期7-Zip”。本轮官方7-Zip25.01遇到 BadCmd=13，原P3与本轮完整成功解码器为NanaZip6.5.1767.0；execution alias不是真实二进制。复制工具时记录真实exe/DLL来源及完整哈希，保留拒绝不完整解析的门槛。
