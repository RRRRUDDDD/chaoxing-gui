# 修复与审查

## 根因

Actions 34589073558 的 `test-web` 在 Node 20.20.2 的桌面测试步骤失败，后续 `build-windows` 被依赖条件跳过。

`os.tmpdir()` 和 `mkdtemp()` 保留 runner 的 `RUNNER~1` 路径，而生产清理检查中的 `realpath()` 及 PowerShell 返回长路径 `runneradmin`。两份测试文件直接用短路径派生 fixture 和预期值，造成五个 junction 用例、NSIS reference 用例和两个 profile cleanup 用例失败。

本地使用 Win32 `GetShortPathName` 获取真实 8.3 路径，仅为测试进程设置 `TEMP/TMP`，在相同 Node 20.20.2 下重现完全相同的八个失败。另行核对 `windowsContext()`：其 .NET `GetTempPath()` 已返回长路径，因此无需修改实际安装/清理逻辑。

## 修改范围

- `desktop/tests/installation.test.mjs` 与 `desktop/tests/p3-smoke.test.mjs` 的临时目录 helper 在登记清理后返回 `realpath(root)`。
- 所有 fixture 派生路径及标记均来自统一的长路径。未改变任何断言、跳过条件、超时、生产所有权/junction 检查或工作流门禁。
- 已将 Windows 短路径夹具约定补充到前端/桌面 spec。
- 按用户后续指示停止已启动的双模型进程，分析、修复和审查均由主代理完成。

## 审查结论

- Critical：无。
- Warning：无与修复相关的问题。
- Info：GitHub 原运行不会因为本地修复而改变；此次没有推送远端、重跑 Actions 或发布版本，尚无新的云端完整打包结果。
- 原有迁移计划修改及 `poc-window.png` 不属于本次提交。相关桌面/前端/工作流代码在本地 HEAD 与失败远端提交之间一致，虽两者提交历史分叉。

## 验证

| 检查 | 结果 |
| --- | --- |
| Node 20.20.2 + 真实 8.3 TEMP，修复前定向复现 | 2 通过 / 8 失败 / 34 未匹配选定名称 |
| 同条件，修复后定向复现 | 10 通过 / 0 失败 / 34 未匹配选定名称 |
| 同条件，完整 `npm --prefix desktop test` | 90 通过，0 失败，0 跳过 |
| Node 20.20.2 `npm --prefix web test` | 125 通过 |
| `npm --prefix web run build` | 通过 |
| Python 3.11.15 `python -m unittest discover -s tests -v` | 160 通过 |
| Python 3.13.14 `python -m unittest discover -s tests -v` | 160 通过 |
| 工作流涉及的五份桌面 JS 与两份修改的测试 `node --check` | 通过 |
| `desktop/scripts/version.py --check` | 通过，1.1.1 |
| `git diff --check` | 通过 |

原始 Actions 日志、定向/完整测试输出和本次复现脚本保留在归档目录本地的忽略文件中；版本库保留本报告和任务元数据。
