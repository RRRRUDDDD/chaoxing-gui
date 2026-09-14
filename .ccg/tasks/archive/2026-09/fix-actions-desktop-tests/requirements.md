# 修复 Actions 桌面测试失败

- 失败运行：https://github.com/RRRRUDDDD/chaoxing-gui/actions/runs/34589073558
- 失败提交：`9ffcf9b4aa64f6b62872c0132ba719442ab2cfa4`；Windows、Node 20.20.2。
- `test-web` 的桌面测试 90 项中 8 项失败、70 项通过、12 项跳过；Python 与前端测试已通过，打包被依赖门禁阻止。
- 报错涉及临时目录的 Windows 8.3 短路径 `RUNNER~1` 与真实长路径 `runneradmin` 比较不一致：scratch ownership、NSIS reference path、owned profile cleanup。
- 范围：核实根因，以最小改动修复测试夹具或实际路径处理，并在同版本 Node 和真实短路径临时目录下回归。
- 必须保留目录所有权、junction/reparse、进程退出验证及数据保留断言，不能通过跳过测试或放宽清理检查使 CI 变绿。
- 工作区已有修改：归档迁移计划与 `poc-window.png`，不包含在本次提交。当前 main 与 origin/main 历史分叉，先核对代码差异再决定远端验证方式。
- 用户后续指示：不需要调用双模型；停止已启动的调用，由主代理完成分析、修复和审查。
- 完成条件：本地回归与前端构建通过；记录远端验证状态；任务归档并单独提交。
