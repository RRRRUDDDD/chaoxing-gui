# 超星学习通自动化工具

<div align="center">

[![GitHub Stars](https://img.shields.io/github/stars/RRRRUDDDD/chaoxing-gui)](https://github.com/RRRRUDDDD/chaoxing-gui)
[![License](https://img.shields.io/github/license/RRRRUDDDD/chaoxing-gui)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

**带 Web 界面的超星学习通自动学习工具**

视频学习 · 自动答题

</div>

---

## 快速开始

### 方式一：一键启动

```bash
# 1. 下载项目
git clone https://github.com/RRRRUDDDD/chaoxing-gui.git
cd chaoxing-gui

# 2. 启动
start.bat  # Windows 双击或命令行运行

# 3. 打开浏览器访问 http://localhost:3000
```

**自动完成**：检查环境 → 安装依赖 → 启动服务 → 打开浏览器

### 方式二：桌面应用

```bash
# 构建桌面版（只需一次）
build_desktop.bat

# 双击安装
desktop/release/chaoxing-gui-desktop-Setup-*.exe
```

安装后通过开始菜单或桌面快捷方式启动。

Windows 发布包可从 [GitHub Releases](https://github.com/RRRRUDDDD/chaoxing-fanya/releases/latest) 直接下载。

Tauri 2 桌面版正在独立验收，可用 `build_tauri.bat` 构建 Windows x64 NSIS 和便携 ZIP，输出在 `desktop/release/tauri/`。它使用独立程序目录，保留 Electron 与原数据；当前默认桌面构建入口仍为 Electron。安装、WebView2 离线准备、版本校验和验收边界见 [桌面版指南](desktop/README.md)。

### 方式三：命令行模式

```bash
# 使用配置文件
python main.py -c config.ini

# 命令行参数
python main.py -u 手机号 -p 密码 -l 课程ID --speed 1.5
```

---

## 使用说明

### 1. 登录

**Web UI**：
- 输入手机号和密码，点击"登录"
- 登录后可使用当前账号的本机会话自动登录；会话过期时重新输入密码

**CLI**：
- 编辑 `config.ini` 填写账号信息
- 或使用 `-u` `-p` 参数

### 2. 选择课程

**Web UI**：点击课程卡片选择课程，至少选择一门后开始。保存的选择按账号隔离，已经失效的课程不会自动替换为全部课程。

**CLI**：使用 `-l` 参数指定课程 ID，逗号分隔

### 3. 配置参数（可选）

- **播放倍速**：1.0-2.0，建议 1.5
- **并发章节**：界面提供 1-10，后端允许 1-16，建议 3-5
- **题库**：支持言溪/Like/AI 等 5 种题库
- **通知**：支持 Server酱/Telegram/Bark 等

### 4. 开始学习

**Web UI**：点击"开始学习"，查看实时进度

返回课程页后，可通过“查看运行任务”继续查看进度。同一账号的任务结束前不能重复启动。失败和跳过的任务分别统计；保存但未提交的测验也属于跳过，不会显示为全部完成。

**CLI**：自动运行，查看控制台输出

### 5. 课程工具（Web / 桌面）

在选课页右侧的“执行功能”中选择：

- **学习次数**：设置每门课程的提交次数和间隔，点击“开始提交次数”。进度页分别显示成功提交次数与平台返回的前后统计。
- **视频时长**：点击“读取视频列表”，勾选视频，设置每个视频增加的分钟数，再点击“开始累计时长”。按实际时间运行；超过视频长度后从头继续，已完成的视频也可选择。
- **资源下载**：点击“读取资源列表”，按名称、章节或类型筛选并勾选资源，点击“下载所选资源”。支持视频、音频、文档等；文档优先保存平台提供的 PDF。

三项功能共用任务进度、日志和“停止任务”操作，同一账号同时执行一个任务。下载文件保存在程序数据目录的 `downloads/<任务 ID>/` 中，完成后可点击“打开下载目录”。同名文件会另存，失败或停止时清理当前临时文件。资源列表过期或资源数量过大时，按界面提示重新读取或减少所选课程分批处理。

重新打开程序后，资源读取和下载可恢复，已确认保存的文件会保留。次数与时长上报在异常退出后保留执行记录并结束本次任务；核对平台统计后可重新开始，以避免重复累计。平台统计可能延迟或合并请求，成功提交量不等于平台实际增加量。

---

## 核心功能

- ✅ **视频自动播放**：支持倍速（1.0-2.0x）
- ✅ **题库自动答题**：内置 5 大题库，覆盖率可配置
- ✅ **OCR 识别**：本地 PaddleOCR 或云端视觉模型
- ✅ **进度推送**：Server酱/Telegram/Bark/Qmsg
- ✅ **可视化界面**：Web UI + 桌面应用
- ✅ **课程学习次数**：可设置次数与间隔，查看提交结果和平台统计
- ✅ **视频观看时长**：选择视频并按指定时长执行，支持停止
- ✅ **课程资源下载**：筛选下载视频、音频、文档，显示进度和保存位置

---

## 同类工具对比

| 特性 | 本项目 | 命令行工具 | 浏览器扩展 |
|------|--------|-----------|-----------|
| **上手难度** | ⭐⭐ 一键启动 | ⭐⭐⭐⭐ 需配置环境 | ⭐ 最简单 |
| **界面** | Web + 桌面应用 | 纯命令行 | 浏览器内 |
| **后台运行** | ✅ 支持 | ✅ 支持 | ❌ 需保持浏览器打开 |
| **题库生态** | 5 种可选 | 需自行集成 | 通常单一 |
| **自动推送** | 内置 4 种渠道 | 需自己实现 | 有限 |
| **定制性** | ⭐⭐⭐⭐⭐ 开源可改 | ⭐⭐⭐⭐⭐ 开源可改 | ⭐⭐ 扩展限制 |
| **稳定性** | 独立程序，稳定 | 独立程序，稳定 | 依赖浏览器更新 |
| **适用场景** | 长期使用、批量任务 | 自动化、定时任务 | 临时使用、轻量需求 |

**选择建议**：
- 新手 / 临时使用 → **浏览器扩展**（最快）
- 普通用户 / 长期使用 → **本项目**（功能完整）
- 技术用户 / 自动化 → **命令行工具**或本项目 CLI 模式

---

## 常见问题

### 端口被占用

```bash
# 查看占用进程
netstat -ano | findstr :5000
netstat -ano | findstr :3000

# 结束进程
taskkill /F /PID <进程ID>
```

### 题库无响应

1. 检查 token 是否正确
2. 查看 `logs/chaoxing.log` 确认错误
3. 降低 `cover_rate` 参数（如 0.6）
4. 切换其他题库提供商

### 登录失败

- 检查账号密码是否正确
- Cookie 可能已过期，重新获取
- 部分账号触发验证码（暂不支持）

---

## 开发与验证

Python 最低版本为 3.11，CI 使用 3.11 和 3.13 运行第一方离线回归测试。测试不加载 Paddle 模型，不连接学习账号、题库或通知服务。

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
npm --prefix desktop test
```

答案缓存键包含题干、题型和有序选项，旧题干缓存不再命中。缓存默认每 32 次更新原子写盘，任务正常退出时再次刷新；磁盘正常时，异常终止最多丢失最后一批未完成刷盘的 32 次更新。写盘失败会保留脏数据等待重试，此时不保证该上限。直接使用题库 API 的调用方应在结束时调用 `Tiku.close()`，多个进程不要同时写同一缓存文件。

账号会话保存在数据目录的 `.cookies/` 下，各账号独立；旧 `cookies.txt` 仅供未指定账号的 CLI Cookie 登录使用。桌面端记忆账号和任务入口，不保存密码，也不依赖每次启动分配的后端端口。

---

## 许可与免责

### 开源许可

GPL-3.0 许可证 — 允许自由使用和修改，但衍生项目必须开源

### 免责声明

**本项目仅供学习交流**

- ⚠️ 使用者自行承担所有法律责任
- ⚠️ 严禁用于作弊、刷分等违反学术诚信的行为
- ⚠️ 账号安全、学习记录等风险自负
- ⚠️ 开发者不对任何后果负责

**使用即表示同意上述条款**

---

## 致谢与支持

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 核心逻辑开发

课程学习次数、视频观看时长与资源下载的接口约定参考 [liuyunfz/chaoxing_tool](https://github.com/liuyunfz/chaoxing_tool)（GPL-3.0），已适配本项目的账号会话、任务生命周期和图形界面；运行时不依赖该仓库。

- [报告问题](https://github.com/RRRRUDDDD/chaoxing-gui/issues)
- [功能建议](https://github.com/RRRRUDDDD/chaoxing-gui/issues)
- [技术交流](https://github.com/RRRRUDDDD/chaoxing-gui/discussions)

如果有帮助，欢迎 Star ⭐

---
