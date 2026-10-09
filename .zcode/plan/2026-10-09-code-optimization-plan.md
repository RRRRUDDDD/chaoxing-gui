# 2026-10-09 代码优化 Plan

> 基于全代码审查编写的优化计划。优先级 P3 = 低优先级清理。

## 背景

本次审查覆盖 Python 后端 (`api/`, `app.py`, `main.py`)、Rust Tauri 后端
(`desktop/src-tauri/src/`) 和 React 前端 (`web/src/`)。

审查结论：**无高优先级问题**。之前两轮优化（P1×5/P2×22/P3×3.1–3.10）已全部
执行完毕（最后一批 `ddbad49` 收尾）。待优化项为低优先级清理。

---

## P3: 低优先级清理

### 1. 清理过期的 `chaoxing/` 目录

- **文件**: `chaoxing/` 目录和 `chaoxing.spec`
- **描述**: `chaoxing/api/` 是旧版 API 代码备份（Oct 2）, 所有文件已被 `api/` 目录
  （Oct 4–9 更新）取代。确认无任何活跃代码引用。
- **建议**: 删除整个 `chaoxing/` 目录和 `chaoxing.spec`
- **步骤**:
  1. `grep -rn "from chaoxing" .` 确认无引用
  2. `rm -rf chaoxing/ chaoxing.spec`
  3. 提交 (`chore:` 类型)
- **耗时**: 5 min
- **影响**: 仓库体积减小 ~400KB，消除贡献者混淆

### 2. `StudyResult.is_failure()` 避免重复创建 Set

- **文件**: `api/base.py:72-73`
- **描述**: `is_failure()` 每次调用创建新 Set `{SUCCESS, SKIPPED}`; 在重试循环热路径中频繁调用。
- **建议**: 使用类级别 `frozenset` 常量
- **改动**:
  ```python
  class StudyResult(Enum):
      ...
      _NON_FAILURE = frozenset({SUCCESS, SKIPPED})

      def is_failure(self):
          return self not in self._NON_FAILURE
  ```
- **耗时**: 2 min
- **影响**: 微观性能，分配减少

### 3. `backend.rs` 文档注释修正

- **文件**: `desktop/src-tauri/src/backend.rs` (`read_bounded_line`)
- **描述**: 注释说 "longer lines are dropped to backend.log"，但行为是分割 chunk 并记录。
- **建议**: 更新注释以准确描述：split-into-chunks + log behavior。
- **耗时**: 2 min
- **影响**: 文档准确性

---

## 跳过的项目 (无需修改)

以下 TODOs/FIXMEs 都是有意为之的设计决策:

| 位置 | 状态 | 理由 |
|------|------|------|
| `api/base.py:817` | FIXME `get()` fallback | 保守重试行为，正确 |
| `api/base.py:896` | FIXME tenacity | 手动重试避免新增依赖 |
| `api/answer.py:229` | TODO abstract class | 重构偏好 |
| `api/answer.py:244` | TODO CONFIG_PATH | 已有解决办法 |
| `api/decode.py:hash()` lock bucket | — | CPython 进程内稳定 |
| OCR 三重循环 | 重构建议 | 功能正确 |

---

## 操作原则

- **不改变功能**: 仅涉及清晰度和微观性能
- **最小化改动**: 紧凑低风险区域
- **保持测试**: 确保现有测试通过
