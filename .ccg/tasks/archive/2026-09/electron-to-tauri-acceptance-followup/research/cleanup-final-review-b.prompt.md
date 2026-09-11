ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
对当前 Electron→Tauri 2 P0–P3 续接工作做只读审查，禁止编辑任何文件、禁止运行产品/安装器/release/Cargo、禁止读真实业务 AppData、禁止 push/发布/真实账号。
仓库 E:/Downloads/45/chaoxing-gui，HEAD 12bae2286bec71bf184f19c8be67e6431199e134，基线37fde916。
完整相关源码和本轮验证 helper 快照：.ccg/tasks/electron-to-tauri-acceptance-followup/research/cleanup-final-source-snapshot.md，manifest同名前缀。请审查快照实际内容与当前文件；不依赖只有用户计划的工作区 git diff。先读 git show 12bae228 -- desktop/scripts/p3-smoke.mjs，再读当前未提交W1修复的 git diff -- desktop/scripts/p3-smoke.mjs desktop/tests/p3-smoke.test.mjs；snapshot已包含这些新字节。
背景：真实Sandbox WDAG用户无Python/无WebView2，已验证原生/portable缺runtime exit3、官方微软签名离线runtime安装exit0、release --check-debug-build exit4。LLVM优化导致原完整参数明文搜索误拒绝，12bae228只修验收探针。随后标准release fake smoke CDP连接300s超时，仍是失败。既有10次两路外调均429/exit1/空正文，没有通过结论。
这次新增task-only native installation partial：verification/prepare-native-installation.py只替换固定SHA原p3-installation.mjs里的smokeLayout，保留其他字节；guest将副本放在独立解压源码中调用，产品原文件不变。新的Sandbox NativeInstallationPartial模式必须仍验证真实WDAG/VM、fresh profile、所有input hash、原产物SHA，结果始终fullAcceptancePassed=false。它检查NSIS/ZIP/registry/junction/retention和原生window/进程树，不能冒充CDP业务/IPC/CI通过。
重点文件：desktop/scripts/p3-smoke.mjs、desktop/scripts/p3-installation.mjs、task verification的prepare-native-installation.py、prepare-sandbox-kit.ps1、sandbox-guest.ps1、sandbox-bootstrap.ps1、launch-sandbox-acceptance.ps1、collect-sandbox-evidence.py；辅助research/native-installation-scope.md。所有产品执行只允许新的真实Sandbox，不能在RUD运行release。不动用户归档plan.md与poc-window.png。父任务in_progress，本轮不进入P4/P5。
Node20最终Desktop全套86PASS/0skip，probe针对性18PASS，task PowerShell/Python语法通过。不要把历史Rust/Python/Web测试写成这轮重跑。隔离补验执行结果尚未完成，审查可指出未验证处。
侧重点：kit输入来源/清单/隔离映射、generated模块固定源边界、NSIS路径/junction/注册表/卸载数据保留；捕获PID+创建时间的诊断和关窗限制；证据与归档可复查性。

本次增量：独立本地审查W1发现旧GUI launch/close在dispose失败后仍删profile。现已新增共享removeProfilesAfterVerifiedCleanup，显式verified=true、严格remaining=[]、observed非空且每项alive===false后，再调用原ownership/SID/runId/link守卫。probe和两GUI路径接入；GUI使用withCleanup保留primary+cleanup错误。新增四个fixture regression，worker用Node20的p3-smoke单测22PASS，根代理完整Desktop当前正在跑；不要声称完整回归已结束。请重点检查这次修复是否真的堵住W1、异常传播和profile保留是否正确。
验收工具另有已验证修正：git archive按Windows换行配置变CRLF导致原生副本来源校验失败，现单次命令固定core.autocrlf=false/core.eol=lf并预先核对ZIP脚本SHA，不改全局配置；7Zip25.01对原NSIS显示BadCmd=13，未放松拒绝门槛。旧P3原工具实为NanaZip6.5.1767.0，独立复制真实console+DLL按哈希验证，已完成RUD只读NSIS825/ZIP826全内容检查（未执行产品）。新Sandbox kit默认使用这份已校验工具，实际安装执行仍待完成，不能先写通过。
</TASK>
OUTPUT: Critical/Warning/Info，具体文件与行号、触发条件和建议；没有发现也明确审查范围与未验证事项。输出真实报告，失败不可写成通过。
