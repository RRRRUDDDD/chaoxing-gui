ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
只读复核 Electron→Tauri 2 的 P0/P1/P2/P3 源码及本轮验收修复。仓库 E:/Downloads/45/chaoxing-gui，当前已提交 HEAD bbdbbc218416a4f458f9b03bb1a3da5f02451f45，续接基线37fde916。不可依赖仅含用户plan的工作区diff；完整当前源码、task helpers与规范见 research/closeout-final-source-snapshot.md 和 source-manifest.json（相对于 .ccg/tasks/electron-to-tauri-acceptance-followup/）。锁文件完整字节留在原路径，manifest固定其hash。
先审查 git diff 37fde91 bbdbbc2 -- desktop/scripts/p3-smoke.mjs desktop/tests/p3-smoke.test.mjs，再读完整函数及相关Job/profile helpers。12bae228修正Release优化二进制无法用完整参数明文判定probe的缺陷，bbdbbc2解决本地审查W1：probe、GUI launch失败及normal/forced close只有取得verified=true、remaining=[]、非空且全部死亡observed后才删除owned profiles，异常使用withCleanup保留。确认权限、ownership/SID/runId/junction、进程身份和失败传播门槛。
最终本轮真实结果：Node20.20.0 Desktop90PASS/0skip；真实Windows Sandbox WDAGUtilityAccount、VM、无系统Python、初始空product profiles。缺WebView2 native及portable入口exit3；微软官方签名offline runtime安装后exit0；Release native --check-debug-build exit4。标准Release Fake GUI smoke依然CDP连接300秒fetch failed，fallback清理不算正常生命周期通过。最后NativeInstallationPartial沙箱16检查通过，仅涵盖NSIS/ZIP manifest、三处junction拒绝、portable/installed原生probe与normal/forced退出、captured Job/PID/creation FILETIME、卸载7个合成数据/旧Electron哨兵保留及registry清除。fullAcceptancePassed和formalReleaseSmokePassed均false。
证据：verification/native-installation-result-summary.json及sandbox-runs/run-509f5e36e42744d6813cc023ef166ba0/；标准失败run-504ac0d14d084dc19e44a7c97924cff7/；verification/node20-desktop-cleanup-final.log。task-only prepare-native-installation.py以固定源SHA+唯一区段替换生成原生副本，保留原产品及正式工具，不能替代标准CDP业务/IPC检查。git archive单次LF参数固定源字节；7Zip25.01遇BadCmd13，已按原P3工具复制并核验NanaZip6.5.1767.0真实console/DLL；RUD只做只读解码检查，未执行安装器或release。
前12次外部两路调用全exit1/API429/空正文，没有任何通过报告。远端main5899b5f，不含bbdbbc2，本轮没有push/dispatch/发布；旧成功runs不能算当前CI通过。未提供真实签名私钥，实签成功路径未验证。父迁移in_progress，P3验收false，本轮不进P4/P5。
禁止编辑任何文件、运行产品/安装器/release/Cargo/测试、读真实业务AppData、使用真实业务账号、push/发布或改用户plan/poc-window.png。输出审查正文，不以旧结论替代实际审阅；不能把部分成功或本地测试称为全部验收。
侧重probe/GUI cleanup、异常传播、权限/IPC与回归测试缺口。
</TASK>
OUTPUT: Critical/Warning/Info分级，文件行号、触发条件、建议；明确实际审查范围与未验证事项。
