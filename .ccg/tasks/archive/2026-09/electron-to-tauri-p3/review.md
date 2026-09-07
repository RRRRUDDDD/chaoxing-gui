# P3 审查记录

## 外部调用

指定工具：`C:/Users/RUD/.claude/bin/codeagent-wrapper.exe --progress --backend claude`。每组a/b两路同时启动，真实stdin管道、重定向输出、隐藏窗口、明确截止时间、KILL_ON_JOB_CLOSE以及finally清理。仓库路径通过Git解析；历史档案未覆盖。

| 阶段 | 上限/路 | a | b | 结论 |
|---|---:|---|---|---|
| P3分析 | 300s | exit1，无报告 | exit1，无报告 | 429 Service Unavailable；未通过 |
| P0/P1/P2补审 | 240s | exit1，无报告 | exit1，无报告 | 429 Service Unavailable；未通过 |
| P3最终审查 | 300s | exit1，无报告 | exit1，无报告 | 429 Service Unavailable；未通过 |

最终两路分别运行约191s与182s后退出，均没有到达超时上限。stderr中的Session-ID对应错误从本次Claude session中定向提取；未读取无关会话。完整输出保留`research/<stage>-<lane>.prompt.md/.stdout.md/.stderr.log/.result.json`；最终源码快照含91个完整相关文件及全部未跟踪P3源码，lock原路径和哈希明确记录。空stdout文件本身也保留。

实际错误汇总：verification/external-service-errors-final.json。前轮规划超时/P2外审失败没有改写为通过，独立本地审查不替代外部报告。

## 已修复的本地发现

- 首次真实NSIS包含fake-backend.exe：以required-features控制测试bin，release关闭默认特性，实际包内容门槛拒绝测试helper。
- 旧卸载器可能删除新清单外的旧目录：增加完整旧程序树检查，未经验证的MSI迁移拒绝2；覆盖junction、深度和枚举边界。上游空根资源祖先由根检查覆盖。
- NSIS宿主与portable宿主标记不同：独立计算NSS预期字节，签名时绑定preSign/captured hash，恢复的portable宿主另签；完整哈希校验没有跳过host。
- Tauri可能原地签名资源DLL使manifest失效：签名回调按已登记清单核对并保留资源字节，含有效vendor签名。
- CI发布缺ZIP侧车、版本后缀可互换、签名可用性与既存签名状态混淆：补齐发布文件和边界校验。
- 多个Node安装路径导致wrapper选择数组、中文标题在stdin JSON中损坏：选择首个Application，严格UTF-8 reader，真实窗口/管道回归通过。
- smoke profile竞争、supervisor死亡误称清理成功、安装子smoke超时后所有权丢失：exclusive mkdir/所有权复检、已捕获Job确认和提前所有权交接；错误清单/host资源变化回归通过。

## 最终独立本地复核

`ccg-review`以fork_turns=none独立只读检查完整P3改动，未发现新增Critical或Warning。报告见research/p3-independent-review-final.md。复核采用已完成根验证结果，没有重复运行产品安装器或正式宿主。

Info继续保留：实际签名成功路径、remote CI、隔离用户release安装/便携、缺WebView2机器未验证；NSIS预检仍有后续并发替换时窗。父迁移任务保持未完成。
