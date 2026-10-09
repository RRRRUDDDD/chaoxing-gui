# 代码优化计划 2026-10-10

## 来源
来自对整个代码仓库（Python 后端 `main.py` + `api/`，Rust 桌面主 `desktop/src-tauri/src/`，React 前端 `web/src/`）的全面审查。

## P1 — 高优先级（正确性 / 性能）

### 1. RateLimiter 持有锁期间调用 time.sleep  — `api/base.py:235-246`
- **问题：** `rate_limiter.limit_rate()` 在持有 `self.lock` 时调用 `time.sleep(delay)`，导致所有 worker 线程串行排队睡眠，抵销了多线程并发的优势。
- **修复：** 在锁内计算 wake 时间，释放锁后再 sleep。
  ```python
  def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
      with self.lock:
          now = time.time()
          extra = random.uniform(random_min, random_max) if random_time else 0.0
          wake = max(now + extra, self.last_call + self.call_interval)
          self.last_call = wake
          delay = wake - now
      if delay > 0:
          time.sleep(delay)
  ```

### 2. StudyResult._NON_FAILURE 猴子补丁模式  — `api/base.py:249-258`
- **问题：** `_NON_FAILURE` 在类定义外部通过 `StudyResult._NON_FAILURE = frozenset(...)` 赋值，属性在类加载后才能访问。
- **修复：** 将 `_NON_FAILURE` 移入类体。

### 3. Tiku.query() 每次调用都会写 self._cache_dao  — `api/answer.py:372`
- **问题：** `self._cache_dao = cache_dao` 在热路径上执行一次无用的实例属性写。
- **修复：** 删除该行；`close()` 方法已有 fallback 到 `CacheDAO.get_shared()`。

## P2 — 中优先级（可维护性 / 性能）

### 4. validate_jobs 对布尔值的错误提示不清晰  — `main.py:42-49`
- **修复：** 为 `bool` 输入添加专门的错误分支。

### 5. StudyResult 枚举值跳过 3  — `api/base.py:249-254`
- **动作：** 记录在文档/注释中，说明为何跳过。

### 6. PaddleOCR 重试使用全局状态无最大次数  — `api/decode.py:53-58, 282-317`
- **修复：** 添加最大重试次数或可配置退避。

### 7. answer_check.cut() 每次调用都构造分隔符列表  — `api/answer_check.py:57-79`
- **修复：** 将 `cut_char` 提升为模块级别 `frozenset`。

### 8. answer_check.split_answers() 中 regex 每次编译  — `api/answer_check.py:120`
- **修复：** 预编译为模块常量 `_CODE_PATTERN`。

### 9. backend.rs 中 stop_backend 与 stop_backend_on_exit 重复  — `backend.rs:849-920`
- **修复：** 提取共享的 reap 逻辑到私有 helper。

### 10. taskPolling.js 中 appendLogPage 每次创建 Map  — `web/src/lib/taskPolling.js:4-16`
- **评估：** 日志量通常较小，先观察再决定是否优化为数组+Set。

## P3 — 低优先级（清理 / polish） — 已完成

- [x] `api/base.py` — `study_document` 现改用 `urlencode` 构造 URL 参数，避免 f-string 转义问题。添加了 `from urllib.parse import urlencode` 导入。
- [x] `api/task_state.py` — 评估了所有 `deepcopy` 调用（lines 110, 116, 145, 156, 170, 172, 181, 450, 456）。结论：全部为防御性隔离必要， shallow copy 无法替代，因为 (1) `status`/`details` 包含嵌套可变对象（dicts/lists），(2) `sanitize_errors` 原地递归变异嵌套结构。`dataclasses.replace` 不适用，因为方法返回 dict 而非 `_Task` 实例。添加了评估结论注释于导入处。
- [x] `backend.rs:751-822` — 为 `HEALTH_INTERVAL`, `HEALTH_TIMEOUT`, `MAX_HEALTH_BODY`, `API_TIMEOUT`, `POLL_INTERVAL` 添加了文档注释，澄清各自用途及在 `POLL_INTERVAL` 用在多个 tight wait loops 中的作用。
- [x] `api/answer.py:229` — TODO 未直接改为抽象类（`Tiku()` 在 `main.py:208` 直接实例化作为工厂）。改为文档化当前的 base class/factory 模式，解释为什么 `ABCMeta` 会破坏现有 factory 实例化流程，记录未来重构建议（改为 classmethod）。
- [x] `api/base.py:142-144` — 将 `logger.debug(f"...")` 改为 `logger.debug("...", var)` 风格，与文件中其他 loguru 调用保持一致。

## 实现顺序建议
1. P1 #1（RateLimiter）— 最大性能收益，改动小。
2. P1 #3（Tiku._cache_dao）— 微优化，改动极小。
3. P1 #2（StudyResult）— 正确性硬化。
4. P2 #8（answer_check regex）— 零风险编译优化。
5. P2 #4、#7 — 可维护性提升。
6. P2 #9 — 代码重复清理。

## 文件索引
- `api/base.py` — `RateLimiter`, `StudyResult`, `Chaoxing` 类
- `api/answer.py` — `Tiku.query()`, `CacheDAO`
- `api/answer_check.py` — `cut`, `split_answers`
- `main.py` — `validate_jobs`
- `desktop/src-tauri/src/backend.rs` — `stop_backend`, `stop_backend_on_exit`
- `web/src/lib/taskPolling.js` — `appendLogPage`
- `api/decode.py` — PaddleOCR 初始化
- `api/task_state.py` — `deepcopy` 使用点
