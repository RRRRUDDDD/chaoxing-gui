# 发布记录

产品修复本地提交：`e8664910176064e00cbd3a5e5728bf89fd7c214a`。

从 `origin/main` 的 `fe2860e` 创建隔离工作树，cherry-pick 产品提交并验证除 `.ccg/` 与既有 `.gitignore` 外的完整产品树相同。普通快进推送成功，远端 main 核对为 `3c3fb27b437a17502e0195789555f7a62663c5f5`。

GitHub Actions 已启动：[35067750420](https://github.com/RRRRUDDDD/chaoxing-gui/actions/runs/35067750420)，归档时仍在运行。本地 Python 3.11/3.13 全量各 312 项通过，实际 frozen 与安装启动验证结果见 review.md。

本轮 CCG 档案只保留在本地开发历史，不推入 GitHub 产品历史。保留旧安装备份和原工作区无关改动。新增阅读时长需求独立跟踪。
