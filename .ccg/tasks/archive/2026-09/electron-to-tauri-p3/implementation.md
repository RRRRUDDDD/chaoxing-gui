# P3 Windows 打包与 CI

复杂度 L、风险 high。接手核查和来源证据位于 verification/entry-*；旧阶段保持既有验收结论，不重做实现。当前进行来源核对、双路外部分析与 P0/P1/P2 审查补做。

## 归属整理和并行实施

P1 基础 9219fe2，P2 混合增量 0d224f3。工作区原整合字节未修改，未重复应用补丁。api/desktop_runtime.py 仅最终快照，明确归在 P2 整合，不伪造 P1 前像。补齐原缺失 CLI lock。三个 fork_turns=none 实施者按 plan 文件归属推进，主代理负责版本/CI/配置。外部四路均 exit1 无报告；实际服务错误见 verification/external-service-errors.json。版本测试8项先红后绿；原生 WebView preflight8项红例已捕获。

## Windows 构建集成进度

本轮续接再次核对 HEAD=0d224f38f352f5822dfc31639df13b7b8d073675、main、暂存区为空，用户计划文件 SHA256 仍为 f7ec970826f014e9975a056b2e73ee9bee5dd7323f75b06db79fb7b78cdb5167。仅 P3 增量与用户保留改动处于工作区。

已实现原生 WebView2 前置检测、构建 profile 无副作用探针、嵌套 Job 测试、完整 backend staging/ZIP 清单、版本/签名/build 入口、独立 NSIS 模板和 CI 增量。安装验收脚本现分配给 smoke 实施者，归属表同步更新。真实 staging 遭遇短暂 Win32 ACCESS_DENIED；新增针对性临时锁恢复与持久锁回滚测试后，通过有界重试成功准备 815 文件、116 目录，源文件哈希保持一致；未识别持锁进程，不归因于杀毒软件。

后续为调试宿主重建、NSIS 内容校验、完整回归、实际 release 产物、文档与最终双路审查。本机仍未运行 release GUI/安装器，远程 CI 未执行，代码签名证书不可用；这些门槛不能由本地 debug 成功代替。

## 集成验证与审查修复

Python3.11/3.13各159项、Node20 Web125及构建、Node20既有Electron6+包装11+NSIS3通过；版本后缀新红绿回归增至9项，需纳入最终总数。Debug实际宿主23项通过，包含fake业务/取消/缺资源/错误exe/启动中关窗/后端死亡/强杀和真实冻结后端安全API；全部退出检查通过。首轮supervisor中文标题JSON失败由严格UTF8标准输入reader修复，保留失败记录并按捕获PID/创建时间独立确认退出。

第一轮完整release编译成功（8m54s），但NSIS内容检查发现fake-backend被Tauri按Cargo bin列表收录并失败。核对CLI2.11.4上游required-features逻辑后，为fake-backend引入test-support特性；默认开发/测试保持原接口，P3 release明确--no-default-features。相关失败构建不能视为通过。

独立审查修复：Release追加portable sidecar，版本校验绑定setup/exe和portable/zip，签名报告区分本机私钥能力与文件已有有效签名，bootstrapper路径加引号。NSIS路径junction防护正在native归属实施，安装脚本增加fresh runner拒绝用例。根运行编译和内容检查，仍不在真实用户启动正式宿主或安装器。

## NSIS 内容门槛与最终收敛

续接确认 HEAD 与保护文件哈希未变。Native 路径防护最终13项夹具通过，补齐旧目录全树检查、MSI迁移拒绝和上游空根资源祖先兼容。真实包哈希门槛又发现 NSIS host 与 portable host 的3字节包类型标记差异；只读提取和锁定上游源码确认原因，详见 research/tauri-bundle-host.md。新增先红后绿的7项NSIS内容/记录测试，按包分别保存完整宿主预期哈希，资源仍全部精确匹配。签名回调必须捕获NSIS真正签名字节、保护已登记backend资源，并单独签名恢复后的portable宿主；该路径本机没有证书不能声称已实签。

最终本地build入口exit0（verification/build-tauri-marker-final.txt），Rust114通过+1个仅供子进程调用的ignored入口、native NSIS夹具13通过、NSIS825应用文件/ZIP826文件全量校验通过。Node20完整Desktop86通过、0skip，含Electron6、打包/路径/安装/进程/签名回调；Python3.11/3.13各160、Web125及构建沿用通过证据。最终YAML23个run step及40个PowerShell源文件语法通过。实际产物1.1.1、无签名证书/均NotSigned；不在RUD下执行正式安装器/宿主，远程CI尚未执行。进入最终审查和交付归档。

最终外部双路各在约191/182秒exit1，无正文，实际429 Service Unavailable；本轮6次外部调用均未通过。独立只读终审无新增Critical/Warning。P3源码51文件已提交6b2c5a2，Git树具备89个受审构建/运行/测试输入。准备归档本地交付，acceptancePassed=false，父迁移任务保持in_progress。
