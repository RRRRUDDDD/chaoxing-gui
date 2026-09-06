# 分析：Electron → Tauri 2 迁移计划（前端/桌面安全视角）

## 现有职责清单（事实，基于当前工作区含未提交改动）

- `desktop/main.js`：随机空闲端口 → spawn Flask（dev: `python app.py`；打包: PyInstaller exe）→ 轮询 `/api/health` → 加载 `http://127.0.0.1:{port}`；导航/新窗口限制在后端 origin；data: 占位/错误页；stdin 优雅关闭 + `taskkill /T /F` 清进程树；日志落 userData。
- `desktop/preload.js` + `desktop/session-store.js`：仅 4 个命名 IPC（read/rememberLogin/rememberTask/clear）；主进程校验“主窗口主 frame + 精确 origin + 参数个数 + 严格 schema”（只存用户名/taskId，0600 原子写 `renderer-session.json`，4096 上限）。
- `web/src/lib/sessionStore.js`：`getBridge` 抽象（有 bridge 用 IPC，无则 localStorage 兜底）；旧明文 `chaoxing_saved_login` 迁移（去密码只留账号）。`web/src/api/axios.js` 相对 `/api`；`web/vite.config.js` 代理到 5000。
- `app.py`：`CHAOXING_PORT/HEADLESS/DATA_DIR` 环境变量；CORS 仅 `localhost/127.0.0.1:{PORT}`；stdin 守护防孤儿；打包态另有静态服务与托盘分支。

**事实澄清**：全仓库 grep 无 `safeStorage`。任务所称“旧 safeStorage 迁移”在当前代码不存在；真实旧存储是 localStorage 明文账号（web 层已迁移）与 Electron 的 `renderer-session.json`——Tauri 侧需迁移的是后者。

## 方案取舍

| 方案 | 优点 | 缺点 | 改动量 |
|------|------|------|--------|
| A：WebView 加载 Flask 动态 localhost 页 | 对齐现状，axios/vite/preview 不动 | 窗口 URL 随端口动态，需就绪后建窗/导航；页面出自 Flask，后端被攻破即特权页 XSS；Tauri CSP 不注入远程页，Flask 现无任何安全头 | 低 |
| B：打包 React 静态页（tauri.localhost）+ HTTP 调后端 | 页面静态可信，IPC 仅授固定 origin；可配 CSP；后端无法注入页面内容 | axios 改绝对地址 + 动态端口下发；CORS 需加 tauri 源；WebView2 自定义源→127.0.0.1 的 PNA 行为待验证；Flask 静态服务在桌面包内冗余 | 中 |

**推荐 B**（信任边界更清晰，符合 spec 第 9 条“窄持久化”精神）；**备选 A**（PoC 失败或工期紧张时）。

## 实际难点

1. **窄 IPC/会话存储**：`window.chaoxingSession` 消失，改 `invoke` 四命令；serde 类型化 + `deny_unknown_fields` 可平替 strict schema；`isTrustedSender` 无直接等价物，改由 capability（窗口 label + origin 模式）承担。**需 PoC**：远程/动态端口 origin 的 capability 通配写法；`isTauri()` 探测与四命令封装。`desktop/tests/session-store.test.js` 五组用例需 Rust 重写，`sessionStore.test.js` 需 mock tauri bridge。
2. **安全权限**：默认 capability 仅留 core + 4 命令，禁 shell/dialog；方案 B 设 CSP `connect-src http://127.0.0.1:*`；`on_navigation` 等价导航限制（API 名需 PoC 核实）。
3. **旧存储迁移**：Tauri 数据目录不同，需显式读旧 Electron userData 下的 `renderer-session.json`，经现有 `validSession` 校验后落新目录、成功前不删旧文件；更早纯 localStorage 账号在 WebView2 中不可读，仅丢失用户名+taskId（可接受）。
4. **进程管理**：sidecar kill 不保证杀进程树，需 `taskkill /T /F` 等价或 Job Object；`app.py` stdin 守护保留即有兜底。

## 阶段与文件

- **阶段 0（PoC）**：Tauri 骨架 + sidecar 随机端口/健康检查；验证 A 的 remote capability 与导航 API、B 的 CORS+PNA，据结果二选一。
- **阶段 1**：Rust 核心——`src/session_store.rs`（移植 `desktop/session-store.js` 全部校验与测试）、四命令、进程管理、日志。
- **阶段 2**：前端——`web/src/lib/sessionStore.js` 桥检测换 Tauri、`web/src/lib/sessionStore.test.js`；方案 B 另改 `web/src/api/axios.js`（绝对地址）与 `vite.config.js`；`web/src/App.jsx` 预期零改动。
- **阶段 3**：打包——`tauri.conf.json`/capabilities、PyInstaller externalBin、旧 `renderer-session.json` 一次性迁移；`app.py` CORS 增加 tauri 源。
- **阶段 4**：下线 `desktop/`；CI 加 `cargo test` + `tauri build`，保留 `.ccg/spec/frontend/index.md` 既有回归入口。

## 验收标准

- Rust 测试覆盖现有等价用例：跨重启持久、拒密码/多余字段/超长/账号不匹配、损坏与超限文件回空、四通道参数计数。
- 负向：非授予源 invoke 被拒；桌面路径不读写 localStorage（spec 第 4、9 条）。
- 迁移后磁盘仅含 version/login/activeTask，无 password；失败时旧文件完好。
- 退出无孤儿后端；PoC 结论（PNA/CORS、capability、导航限制）书面记录后再进入阶段 1。

以上为分析与计划，未执行任何迁移、测试或文件改动。

---
SESSION_ID: 1e9016b6-a6c6-44eb-9d17-f6e041c3dcbb
