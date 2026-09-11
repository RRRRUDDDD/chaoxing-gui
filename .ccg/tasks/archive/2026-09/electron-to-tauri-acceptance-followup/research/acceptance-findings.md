# Isolated acceptance findings

本轮实际运行都在 Windows Sandbox 的 `679402FC-7175-4\WDAGUtilityAccount`，Windows 11 build 26100、Microsoft Virtual Machine。RUD 只准备输入、运行只读包检查/离线测试及启动 Sandbox；没有在 RUD 下执行产品 release 宿主或产品安装器。每次使用新 guest 工作目录、新 input manifest、新输出目录，原始 P3 安装包和 ZIP 哈希始终固定。

## 原生编译模式探针

第一次 standard run `337961b4979e4d81abe3fb42f0f90049` 被完整参数字符串检查拒绝。第二次 `dbf135f6e7ae4958a79316d35fa2bf1a` 先直接调用原生探针，实际 exit 4，之后旧检查仍拒绝。独立二进制研究见 release-probe-review.md：LLVM 将 19 字节比较拆成两个重叠的 16 字节常量，完整 ASCII/UTF-16 字符串都不在优化后的宿主中。

`12bae228` 保留 Debug 静态预检；Release 只在 disposable 权限与 fresh profile 边界内使用已捕获 Job 执行原生探针，核对 exit 4，并在 verified cleanup 后清理归属目录。第三次 standard run `504ac0d14d084dc19e44a7c97924cff7` 实际证明 probe 退出、Job 空、fallbackUsed=false、verified=true、profileCleanup.ownedRootsRemoved=true。

## 标准 release GUI 仍失败

第三次 standard run 随后报 `real Tauri WebView2 CDP timed out after 300000ms: fetch failed`。捕获 Job 中有宿主、WebView2、冻结合成后端及 conhost；超时清理后 captured observed 全部死亡、remaining 空、verified=true，但 fallbackUsed=true。不能把这次强制清理算正常退出或业务通过。

已读 Wry 0.55.1 的 Windows 实现：release 的 devtools 默认 false，调用 SetAreDevToolsEnabled；这不足以证明 remote debugging 一定被禁用。没有得到该次 WebView2 的命令行/监听器诊断，CDP 原因保持未确定。没有为通过验收而开启产品 devtools、修改 release 安全边界或重建产品。

## task-only native 补验工具的来源校验

补验副本只替换固定 SHA 的 `p3-installation.mjs` 中 smokeLayout，其余原始字节不变。副本仍运行原安装包/完整清单/junction/registry/retention 门槛，原生生命周期只证明进程存在、精确关窗与强杀退出；所有结果始终 fullAcceptancePassed=false，标准 CDP 工具不变。

首次补验 `68b899a1365f41f2b2989e4e0ca64e44` 在执行产品安装前，被准备器来源校验拒绝。工作区和 Git blob 的安装脚本为 44,234 字节、LF、SHA256 `ecabd8c7330f390e208ea6c5c2aecf5eb7f14e68ba09d19e430170865b721b1c`；默认 git archive 在这台 Windows 配置下导出成 44,890 字节、656 个 CRLF、SHA256 `9a04c6426c265551492594ef00e5e88c736e90fc8b78d9a4fcef4d5214ceb3b6`。

后续仅对导出命令加 `-c core.autocrlf=false -c core.eol=lf`，并在主机准备阶段就核对 ZIP 内原脚本的真实字节哈希。没有把不同哈希当匹配、没有修改用户 Git 配置，也没有覆盖这次拒绝证据。

## 7-Zip 25.01 与实际 P3 解码器不同

补验 `3790c00e08e64722a40cb8d1837163e9` 通过来源校验、portable 全部 826 文件检查后，NSIS 检查拒绝 `SubType = NSIS-3 Unicode BadCmd=13`。实际用的是官方 7-Zip 25.01，工具可以运行，但不能完整解析此安装包；拒绝门槛保留。

回查旧 P3 list/extract 原始输出，实际成功解码器是 Microsoft Store 的 NanaZip 6.5.1767.0（其 7z.exe 为零长度 execution alias），不是已经下载的官方 25.01。只复制当前已安装 NanaZip 的真实 console 和所需 DLL 为独立工具，逐文件验证复制前后 SHA，未安装或改变主机软件。独立副本在 RUD 下只做内容检查：portable 826 文件、NSIS 825 应用文件的全内容/哈希/未签名状态通过；没有执行产品。见 verification/nanazip-tool-provenance.json、nsis-readonly-nanazip/。

干净 runner 的解码工具仍须实测，不能把“近期 7-Zip”当作足够的兼容性证明。CI 当前来源未在远端，故该事实没有变成远程 CI 通过。

## 本地独立审查 W1

reviewer 核实既有 GUI launch/close 的异常清理缺少 verified 门槛，见 local-followup-review.md。这个发现不是 12bae228 的新增缺陷，也没有把原 smoke 总结果变绿；但它违反严格的进程清理与 profile 移除顺序，纳入本轮必要修复。最终实现、测试与复审状态以 delivery.md、review.md 和 verification/final-results.json 为准。
