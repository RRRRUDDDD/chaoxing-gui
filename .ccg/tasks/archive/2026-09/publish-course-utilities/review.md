# 课程工具发布记录

- 用户明确要求“全都做完之后就 push 到 GitHub”，授权提交并推送本次已验证功能。
- 沿用仓库已有的发布约定：产品代码正常快进推送 `origin/main`，CCG 规范和任务记录仅保存在本地开发历史。
- 获取最新远端后，确认本地既有产品基线与 `aac62c607cbce8d6060d117de79c22a20a4de407` 一致；仅存在既有 `.gitignore` 与本地 `.ccg/` 历史差异。
- 将 18 个本次产品、测试和 README 文件提交为 `f6946fb0b6086ddb629dcec43e8f361814a5a71a`，未混入其他工作区改动。
- 从 `origin/main` 建立隔离分支 `feat/course-utilities-20260916`，无冲突转移提交为 `fe2860ef5a798bac91bd71a2e9dfdd92393613ac`。
- 发布前检查全部产品树与已测试源码一致，提交空白检查通过，发布工作区干净；没有修改实现，因此沿用本次最终测试结果，无需重复运行相同内容的测试。
- 正常快进推送 `HEAD:refs/heads/main` 成功；`git ls-remote` 已核对远端 main 为 `fe2860ef5a798bac91bd71a2e9dfdd92393613ac`。
- GitHub Actions 已自动启动 [Build Windows Package](https://github.com/RRRRUDDDD/chaoxing-gui/actions/runs/35060820176)，记录时为 `in_progress`。
- 确认发布工作区位于本任务的 `.cache` 目录内、没有未提交改动且提交已在远端后，移除临时工作区，保留发布分支。

验证依据：Python 3.11/3.13 各 301 项、Web 225 项、Desktop Node 101 项、Rust API 11 项通过；Web 生产构建与桌面/手机尺寸 fixture UI 检查通过。完整日志、截图和实现审查边界保存在 `../port-course-utilities/`。

本次发布检查没有发现待解决的 Critical/Warning 问题。实现任务的外部审查超时情况与无真实账号验证的限制仍适用。未生成或上传本地安装包，云端构建状态没有被表述为成功。

复用了既有发布策略，没有新增需要回馈的业务规范。原有 `.ccg/spec/frontend/index.md`、迁移计划修改及本地参考仓库保留。
