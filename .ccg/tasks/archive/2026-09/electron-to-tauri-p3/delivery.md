# P3 本地交付

本地实施、构建、内容检查和回归已完成并归档；**外部审查、远程 CI 和真实隔离用户 release 验收未通过/未执行，整体迁移仍为 in_progress**。

## 提交与来源

| 提交 | 归属 |
|---|---|
| `9219fe24c25151af12c6c79a986b86018cd6a2ef` | 经P2历史快照核验的既有P1基础 |
| `0d224f38f352f5822dfc31639df13b7b8d073675` | 保留的P2 Rust/Python整合与依赖测试 |
| `6b2c5a2d93936cfeb9df0c44e9c7f4b1af5e24db` | P3 Windows打包、CI及验收工具，51个明确源码文件 |

原P2前端/工具提交`98c7ffe`和归档`ed1a6e2`继续保留。入场33项交付哈希匹配；没有再次应用已验证补丁。`api/desktop_runtime.py`仅有最终快照，归属记录没有虚构原始P1副本。详见verification/entry-provenance.json、foundation-commits.json、p3-source-commit.json。

## 可复查产物

目录：`desktop/release/tauri/`，版本均为**1.1.1**，本机没有可用签名私钥，实际均为**NotSigned**。不自行升版本或创建tag。

| 文件 | 字节 | SHA256 |
|---|---:|---|
| `chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe` | 153754871 | `70c18ac432d6e05dff8eea4680b1a3f3c9c1b840370c4460202865d8446ddc09` |
| `chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip` | 186516281 | `3318ca14cf4c20818f69fedded0f52b479ab542003cc12625e19398f7b993e76` |

同时保留setup的`.exe.manifest.json`、ZIP的`.manifest.json`/`.sha256`、总artifact JSON、SHA256SUMS.txt与签名状态报告。它们的副本在verification/artifacts/，最终包内容报告在verification/nsis-content-final/result.json。二进制本体和大型fixture不进入Git。

完整backend staging为815文件、116目录。NSIS含825个应用文件，ZIP含826文件（额外包含package-manifest.json）。两个宿主有Tauri包类型标记差异，分别校验全部字节；每个依赖仍与完整资源清单匹配。首轮发现的fake-backend.exe误入包与错误共用宿主哈希均已修复，失败证据保留。

## 实现行为

- 完整PyInstaller onedir通过目录映射进入Tauri包；保留原console、排除项及Flask内web/dist。Electron测试、打包和独立Python发行入口保留。
- 当前用户NSIS、独立宿主/安装目录与发布目录；卸载保留AppData业务数据，旧Electron程序/数据/回滚链保留。便携继续使用Windows用户AppData。
- WebView2在创建WebView/React、读取业务数据和启动后端前检测；NSIS为在线bootstrapper，提供离线Standalone Installer说明与便携主动安装入口。
- NSIS原生预检拒绝junction等不安全路径，检查资源祖先、旧版本独有目录和旧卸载器，拒绝未经验证的MSI自动迁移（exit2）。预检不能消除检查后的并发路径替换时窗。
- pyproject版本同步/校验覆盖Cargo/Tauri/npm/locks/artifacts与存在的tag。签名回调绑定NSIS编译来源及签后字节，便携宿主另签，第三方后端依赖保留已登记的字节。实际证书签名成功分支仍未验证。
- CI保留Python3.11/3.13与Node20，加入固定Rust/cache、质量门、NSIS/ZIP校验和真正host/安装smoke；原生命令失败传播，发行依赖所有门槛成功。

## 实际验证

| 项目 | 结果与证据 |
|---|---|
| Python 3.11 / 3.13 | 各160 PASS；verification/python311-complete-final.txt、python313-complete-final.txt |
| Node20 Web | 125 PASS及生产build；verification/node20-web-final.txt |
| Node20 Desktop | 86 PASS、0skip；verification/node20-desktop-complete-final.txt |
| Rust | fmt/check/clippy/test/release PASS；114 PASS及1个故意ignored的子进程入口；verification/build-tauri-marker-final.txt |
| 实际debug宿主 | Node20下23检查通过，fake业务/失败/取消先行，再真实冻结后端；verification/tauri-debug-node20-green.txt、debug-host-final.json |
| 冻结Python / backend | 实际health、安全API、stdin与Job退出通过；verification/frozen-python/ |
| Electron打包 | 成功；verification/electron-package-local.txt |
| NSIS/ZIP | 完整文件/层级/hash、版本、未签名状态通过；verification/nsis-content-final/与artifacts/ |
| CI静态检查 | YAML、23个run step、40个PowerShell源文件通过；verification/workflow-final-validation.json |
| 独立本地审查 | 无新增Critical/Warning；research/p3-independent-review-final.md |

Rust计数为71 lib+11 main+14 API+16 lifecycle+2 nested；故意ignored的nested子进程入口由其父测试调用。Node20版本20.20.0；本地统一release构建入口使用默认Node24.14.0。Python3.11.9/3.13.14、Windows build26100、PowerShell7.5.4，见verification/toolchain-final.json。所有Cargo执行`--locked -j 1`（fmt除外）。

GUI证据为CDP+可见/可用断言后的DOM click及精确PID/标题的WM_CLOSE。它不证明物理鼠标、干净Win10/11、无系统Python的完整机器或release安装验收。Debug宿主/冻结进程的PATH隔离与真实嵌套Job有本地证据；GitHub runner自带Job之下的表现尚未实测。

## 外部审查实际结果

P3分析两路（300s）、P0/P1/P2补审两路（240s）、P3最终审查两路（300s）均使用指定wrapper同时启动，持有stdin管道、隐藏窗口、有截止时间和Job进程树清理。**6次均exit1、没有正文报告，实际错误均为429 Service Unavailable。没有外部通过结论。** 完整prompt、stdout/stderr、退出码、源码快照与解释见review.md及research/；错误汇总为verification/external-service-errors-final.json。

## 保留改动与剩余门槛

- 用户`.ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md`全部字节未变、永未暂存/提交；SHA256 `f7ec970826f014e9975a056b2e73ee9bee5dd7323f75b06db79fb7b78cdb5167`。保留未跟踪的`poc-window.png`。
- 本机ignored `.ccg/spec/frontend/index.md`追加P3经验，新增内容归档spec-feedback.md。保留本机大型构建/fixture证据；归档只提交明确列出的任务证据文件。
- 自动审批拒绝清理`%TEMP%\chaoxing-p3-test-uxNS2N`与`%TEMP%\chaoxing-p3-test-Ktr9xp`，仅给出`blocked by policy`。它们是已核实的测试Node硬链接目录；未执行清理、未绕过重试，没有相关测试进程残留。
- 服务恢复后补做P0/P1/P2与P3双路外审。
- 将这些本地提交安排到干净Windows runner后，执行CI全链路，验证真实release宿主/NSIS安装卸载/ZIP、无系统Python条件、runner嵌套Job以及失败阻断发行。没有push或远程CI执行。
- 专用Windows用户/VM验证缺WebView2、联网/离线安装、真实release数据保留和回滚；本轮没有在RUD下运行产品安装器/正式宿主，没有访问真实账号或启动真实学习任务。
- 实际证书可用后完成签名及签名产物验收。P4完整干净系统矩阵和P5默认入口切换/删除Electron继续后置。
