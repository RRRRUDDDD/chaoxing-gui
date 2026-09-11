# Supplemental native installation preparation

本交付只准备一个给 Windows Sandbox guest 使用的安装验收副本。它不修改产品代码、原始 `p3-installation.mjs` 或 `p3-smoke.mjs`，也不把原来的 CDP 超时改成通过。本子任务没有执行产品、安装器、卸载器、WebView2 安装器或真实业务操作；没有访问 RUD 的业务 AppData。

准备器：`verification/prepare-native-installation.py`。源参考提交为 `12bae2286bec71bf184f19c8be67e6431199e134`；Git 提交、原始 P3 产物与输入包的整体来源仍由根代理的 kit/guest 校验负责。

## 固定输入与替换边界

| 项目 | 值 |
| --- | --- |
| 原始文件 | `desktop/scripts/p3-installation.mjs` |
| 原始 SHA256 | `ecabd8c7330f390e208ea6c5c2aecf5eb7f14e68ba09d19e430170865b721b1c` |
| 唯一替换区段 | `installationMain.smokeLayout` |
| 原始字节区间 | `[31103, 32716)`，UTF-8/LF 原始字节 |
| 原始区段 SHA256 | `3ca15a2a5bcafdabd4547db476cbed52f1cab420a8042acb4e69e6c7e72ec35f` |
| 替换区段 SHA256 | `7a073ce524580e5fd26b063e88fe24c476726410ea2431c343c1b05e885c5799` |
| 生成模块 SHA256 | `7b29159c05fc3c29c7af805690b557def2fd5a6294046486ea83b19c135d0458` |
| 生成模块长度 | 58,207 字节 |

准备器同时要求原始全文件哈希、唯一完整旧函数体、前后精确锚点匹配；文件其他位置有任何改动也拒绝。前缀和后缀原始字节原样保留，包含原有 import、权限预检、注册表检查、安装路径验证、manifest/hash/signature 验证入口、junction 拒绝、保留数据断言、失败传播与最终清理。生成模块保留 `./p3-smoke.mjs` import 和原有仓库相对路径计算。

准备器只进行本地文件读取、纯字节转换与指定输出文件的独占创建，不导入或运行产品。它拒绝输入/输出父路径中的 symlink/reparse point，要求输入名为 `p3-installation.mjs`、输出名为 `p3-installation-native-partial.mjs`，要求输出父目录已存在，并以 `xb` 拒绝覆盖任何现有输出。写入前后重新核对原始内容，输出元数据由 stdout JSON 返回。失败时 exit 2；不会覆盖旧证据。

## 集成方法

在主机的新 kit 输入目录预建 `native` 子目录，然后只运行准备器：

```powershell
python .ccg/tasks/electron-to-tauri-acceptance-followup/verification/prepare-native-installation.py --source desktop/scripts/p3-installation.mjs --output '<fresh-kit>/input/native/p3-installation-native-partial.mjs'
```

将生成模块和准备元数据纳入该次输入清单。guest 解压固定 `source.zip` 后，将已核验的模块以不覆盖方式复制到 `$scripts/p3-installation-native-partial.mjs`，使它与原始 `p3-installation.mjs`、`p3-smoke.mjs` 位于同一 `desktop/scripts` 目录。不要覆盖原脚本，也不要把这个副本放到主机产品源码目录作为标准入口。

只有 `sandbox-guest.ps1` 确认真实 `WDAGUtilityAccount`、虚拟机身份、预期映射、fresh profiles、无 Python、输入哈希、受信任的离线 WebView2 runtime 及原生 Release probe 后，才允许在 guest 的独立 `NativeInstallationPartial` 模式中调用：

```powershell
& $node (Join-Path $scripts 'p3-installation-native-partial.mjs') --installer-path $installer --portable-path $portable --powershell-path $pwsh --evidence-directory $installationEvidence --timeout-seconds 480 --disposable-windows-user
```

以上产品执行命令只属于已经通过真实隔离检查的 guest。生成模块保留原有 Release caller-assertion/fresh-profile guards；实际 Sandbox 身份判断由外层 `sandbox-guest.ps1` 实施。准备器本身不会判断、创建或启动 VM，也不授予在 RUD 用户下运行产品的权限。

## 原生检查与清理

每个 portable/installed layout 都在真实包目录就地执行，并先读取 `tauri.conf.json` 中唯一 main window 的精确标题（未写 label 时按 Tauri 默认 `main` 解释）。每次原生启动都有单独的 UUID profile owner 和手递交文件：

1. **编译模式探针**：`--check-debug-build` 必须原生 exit 4；在外层 Job 仍保留时观察宿主及子进程全部退出，然后验证 cleanup。
2. **正常窗口关闭**：捕获真实宿主、精确路径的冻结 backend 及 `msedgewebview2.exe`。要求它们在保留的外层 Job 中活跃；按捕获的宿主 PID 与配置标题发送 WM_CLOSE，必须恰好找到一个窗口，宿主 exit 0，全部已观察进程退出。
3. **强制宿主退出**：重新启动、重新声明独立 profile ownership，等待同样的 backend/WebView2 原生活跃证据，仅通过保留的宿主 handle 终止宿主。要求宿主 exit 197，且外层 Job 尚未关闭时整个已捕获树已退出。

所有场景的 `owner.dispose()` 必须有 `verified=true`、空 remaining、包含原宿主 PID/精确创建 FILETIME/可执行文件的全部死亡身份。只有此后才调用原来的 `cleanupChildProfileInvocation`；该原函数进一步验证 handoff、SID、当前 child runId、真实 known-folder 路径、ownership marker 和链接，再删除本次拥有的 profile。启动失败但清理未验证时保留 profile 并报失败。外层原有 finally 仍可以使用同一 handoff 重试验证后的清理。

若 fallback Job kill 才完成清理，可以在验证安全后删除所属 profile，但该场景仍失败；`fallbackUsed=true` 不能满足原生生命周期断言。每个场景的 PASS 都写在 cleanup 与 profile 移除完成之后。每个 layout 的最终名称严格为 `portable-native-release-layout` 或 `installed-native-release-layout`，不会生成原有 `*-real-release-layout` CDP 通过记录。

可选 PowerShell 诊断只请求捕获 Job 中的活跃 PID；读取前后持有进程 handle 并匹配精确创建 FILETIME 和可执行文件。返回的进程还须同时存在于诊断前后的 captured Job snapshots，才保存其命令行和所属 `127.0.0.1`/`::1` TCP 监听器。它不读取进程环境、账号或无关进程；不执行 CDP 请求，也不以诊断失败为生命周期通过依据。这个副本不添加 CDP 调试参数，因此不能用其监听器结果判定原 CDP 超时原因。

## 结果含义

替换区段在原始 try/preflight 之前设置以下字段，所以后续参数、权限或预检失败的结果也具有范围标签：

```json
{
  "scope": "native-installation-partial",
  "acceptanceScope": "supplemental-native-installation",
  "fullAcceptancePassed": false,
  "formalReleaseSmokePassed": false
}
```

原 `success` 字段保持原始失败传播逻辑，最多表示这个补充安装序列全部完成。外层 guest 汇总也必须保留 `fullAcceptancePassed=false`。安装/卸载、portable 前后 manifest、junction 拒绝、HKCU 注册表及合成 Tauri/Electron 数据保留断言继续按原始代码执行，但本子任务尚未运行这些真实断言。

本副本不验证前端显示或可用性、Tauri IPC/backend Ready、业务流程、取消、HTTP 400/404、CDP、物理鼠标、完整 Win10/Win11 矩阵或远程 CI。backend/WebView2 进程活跃只证明进程存在，不能证明 API 或 UI 就绪。原正式 CDP 失败必须单独保留；这个副本和它的 success 不能提升正式 P3/CDP/CI 门槛状态。

## 本子任务已完成的离线验证

- Python 3.11.9 使用 `compile()` 检查准备器，无 `__pycache__` 写入。
- 对原始字节进行纯内存转换；验证替换区段外字节一致、原始源文件前后哈希不变。权限 guard 改动、重复旧函数、旧函数体改动、后边界改动四类输入全部拒绝。
- 生成模块通过 Node 24.14.0 和 kit 内固定 Node 20.20.0 的 `--input-type=module --check`；未把生成模块写入主机文件。Node 20 工具哈希与已有 kit 清单一致：`fda2b2a8857735f7958ba9771a2b24734ff36434e85ae606bcf7744d41d7adc0`。
- 内嵌诊断通过 PowerShell 7.5.4 的 AST parser。检查只解析字符串，没有执行诊断脚本或 CIM 查询。
- 使用纯内存文件/NativeSupervisor/进程快照 mock 在 Node 24.14.0 验证以下 11 条路径，没有运行真实应用或操作系统进程树。

| Mock 场景 | 结果 |
| --- | --- |
| 三场景正常完成 | 独立 owner UUID、三次验证清理、仅 native layout PASS |
| Release probe 返回错误退出码 | 拒绝 layout PASS |
| backend 不出现 | 拒绝；fallback 清理不能满足生命周期 |
| 无精确标题窗口 | 拒绝；不伪造 WM_CLOSE 成功 |
| cleanup 未验证 | 拒绝；不删除 profile |
| cleanup 缺少原宿主身份 | 拒绝；不删除 profile |
| cleanup 使用 fallback | 拒绝 layout PASS，即使清理已安全完成 |
| 强杀宿主后 backend 残留 | 拒绝 force-host / layout PASS |
| 外层 Job handle 丢失 | 拒绝原生观察 |
| 正常关窗退出码非 0 | 拒绝正常关窗 / layout PASS |
| 诊断混入无关 PID | 无关记录被过滤，不进入证据 |

这些是准备器、生成语法和失败传播的离线检查，不是真实 native installation 验收。实际 guest 安装结果由根代理后续保存和审查。
