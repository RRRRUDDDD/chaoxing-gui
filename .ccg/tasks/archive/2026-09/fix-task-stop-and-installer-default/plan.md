# 实施计划

## 现状与约束

- 工作区已有尚未提交的停止按钮、TaskStore 取消信号、调度器取消、D 盘默认目录和主页文案变更。保留并验证这些变更。
- 已确认缺陷：web/src/api/tauriAdapter.js 与 desktop/src-tauri/src/api_proxy.rs 均未允许 POST /task/{id}/stop，Tauri 停止请求无法送达后端。
- 涉及跨 Web / Python / Rust 调用链，复杂度 L；扩充受控 API 操作，按高风险审查。
- 用户明确禁止调用双模型；不运行外部模型分析/审查。由主代理实施桌面部分、一个同模型子代理独立处理后端停止验证，主代理统一审查。
- 用户明确无需本地构建；完成并验证功能后推送 GitHub，由远程工作流打包。

## 文件归属与步骤

### 主代理（桌面与前端）
1. 在 tauriAdapter 与 api_proxy 添加唯一命名的 taskStop 操作，沿用 taskId、POST object、无多余 query 等验证。
2. 先补适配器和 Rust 契约回归，再实现；补界面确认停止到终态的跨 transport 回归。
3. 核对 CourseSelection 主页提示删除与 installer.nsi 默认 D 盘逻辑，保持已有安装路径恢复与显式安装路径优先。
4. 负责 web/src/**、desktop/src-tauri/src/api_proxy.rs、desktop/src-tauri/windows/installer.nsi 及必要安装器回归。

### 独立后端子任务
- 归属：app.py、main.py、api/base.py、api/task_state.py、api/live_process.py，以及 tests/test_task_stop.py（新建）和必要的 tests/test_scheduler.py / tests/test_task_state.py / tests/test_app.py。
- 检查现有取消实现，补真实运行/重试/终态与账号释放回归，修复可复现的停止问题；测试不得访问真实账号、外网。
- 不改 Web、Rust、安装器、规范或任务记录，不提交、不再 spawn。

## 验证与交付

- Web 单测、Python 3.11/3.13 完整单测、Rust 代理契约与格式检查、desktop 现有回归；不生成本地安装包。
- 审查本次修改与已有改动的兼容性，确认非停止请求仍保持原白名单边界。
- 仅暂存本次功能涉及的产品与测试文件，保留无关修改。基于 origin/main 的隔离工作区转移实现提交，核对产品树并正常快进推送，核实远程提交与云端构建状态。
- 写 review.md，补充本地规范；推送完成后更新任务状态，安全移动到 archive/2026-09，在本地提交本任务归档。
