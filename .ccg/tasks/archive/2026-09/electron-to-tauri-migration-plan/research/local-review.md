# 主代理文档核对

## 已核对

- 现有源码与计划引用：18 项 context 引用均存在；不把规划中的新文件视作已交付实现。
- 会话：v1 JSON、4096 上限、账号与 taskId、原子写入、清除和错误提示；没有 safeStorage 迁移前提。
- API：列出所有现有业务路由，并要求保留 409/404、JSON transform、AbortSignal、超时与日志游标。
- 权限：选择本地页面及受限 Rust 转发；纳入 custom command ACL，不使用远程通配 capability 或通用 shell/HTTP。
- 生命周期：明确真实 stdin EOF → os._exit；Job/句柄/退出等待为待实施验收，不宣称已实现优雅关闭。
- 资源：按现有 onedir 打包保留 _internal；开发/生产 cwd 在 Python 导入之前确定。
- 升级/回退：独立目录、白名单导入、备份保留、事务发布、无效 taskId 清理；不把 appId 相同等同于安装器原位升级。
- 发布：NSIS、完整便携 ZIP、WebView2 缺失路径、签名条件、独立 exe 保留、退出码传播和版本一致性。
- 范围：仅新建任务规划和证据文件，未进行迁移实现、业务改动、数据迁移或发行发布。
- 文档结构：代码围栏成对；JSON/JSONL 可解析；调用脚本通过 Bash 语法检查。

## 验证结果

Python 3.11/3.13 各 131 项、Web 38 项、Desktop 6 项、Web 构建均成功。后续只改变桌面测试入口，已针对该项重跑。完整执行记录在 verification/，本机 Node 24.14.0，与 CI Node 20 区分说明。

## Spec Evolution

读取并遵守 backend/frontend spec。此次只有规划，没有新完成的 Tauri 实现经验；待验证的 API/打包约定保留在 plan 的 PoC 门槛中，没有把设计假设加入项目 spec。
