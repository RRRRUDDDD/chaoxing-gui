ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
请对 chaoxing-gui Electron→Tauri2 迁移 P3 最终实现做独立、完整的只读审查。仓库 E:/Downloads/45/chaoxing-gui，main，HEAD 0d224f38f352f5822dfc31639df13b7b8d073675 是已分别整理提交的 P1/P2 基础；P3 此刻尚未提交。不得只看 git diff：91个当前完整文件（含未跟踪源码）已冻结快照。
必读：
1. .ccg/tasks/electron-to-tauri-p3/requirements.md、plan.md、implementation.md
2. .ccg/tasks/electron-to-tauri-p3/research/p3-final-review-source-snapshot.md
3. .ccg/tasks/electron-to-tauri-p3/research/p3-final-review-source-manifest.json（精确原路径/字节/SHA256；lock全文在所列磁盘路径）
4. .ccg/spec/backend/index.md、.ccg/spec/frontend/index.md
5. .ccg/tasks/electron-to-tauri-p3/research/tauri-bundle-host.md、tauri-binary-selection.md
完整源快照 SHA256：380ba5580fd9d56c8b9adce030bcfc97b8a79a9ec6e1498094a2296cd975de31

实现范围：完整PyInstaller onedir staging；独立current-user NSIS与portable ZIP；WebView2在WebView/React之前检查，online bootstrapper与离线主动安装入口；pyproject版本单一源；Rust1.95.0、Tauri Rust2.11.5、CLI2.11.4；逐产物文件hash与版本/签名门槛；CI保留Python3.11/3.13、Node20、Electron、独立Python并新增真实host/installer nestedJob smoke。
重点修复事实：Tauri NSIS host包类型标记UNK→NSS、bundle后恢复unsigned host，故NSIS需独立完整host hash；signed callback记录preSign+signed hash，必须与独立计算的NSS编译输出相等，再单独签portable。对已登记backend资源原字节保留，避免打包阶段再签DLL使manifest失效。安装守卫拒绝reparse路径，检查旧版本独有目录和所有祖先，MSI自动迁移拒绝2；上游空根目录祖先已兼容。完整内容校验不得漏过host或忽略差异。

已实际通过：Python3.11/3.13各160；Node20 Web125及build；Node20 Desktop86、0skip；Rust114（另1个特意ignored子进程入口，主测试实际调用它）；fmt/check/clippy/test；完整build-tauri.ps1 -Prepared，NSIS825应用文件、ZIP826文件hash校验；Node20真实debug host23检查；冻结独立Python与backend真实smoke；Electron打包。
日志：verification/build-tauri-marker-final.txt、node20-desktop-complete-final.txt、python311-complete-final.txt、python313-complete-final.txt、node20-web-final.txt、tauri-debug-node20-green.txt、nsis-content-final/result.json、workflow-final-validation.json。
产品artifact存在desktop/release/tauri/，版本1.1.1；无可用代码签名证书，实际全为NotSigned。签名成功分支未实签。远程CI未执行；真实release宿主/安装器从未在当前RUD用户运行。不得把debug DOM-click或fixture测试当成clean Win10/11、缺WebView2机器、物理鼠标或release安装验证。P0/P1/P2补审和P3分析双路已尝试，均429失败无报告；不得改称通过。

只读审查；不要修改任何源文件、任务记录、Git索引或受保护的用户归档plan.md，不要运行真实installer/host、访问真实账号/AppData、启动学习任务、创建证书、push/tag/Release。必要时读全源码和已有证据。无需复跑重型测试。
本路重点：完整包资源、NSIS安装/卸载/旧树防护、WebView前置、版本和签名前后内容绑定；仍审查全部P3实现。
</TASK>
OUTPUT: Critical/Warning/Info 分级、可复现的具体发现（文件与行号、触发、影响、修复建议）；没有问题时明确说明实际审查范围与源码审查结论，保留CI/clean-system/签名未验证边界。不要把外部调用无正文或服务错误写成通过。
