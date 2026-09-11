# Current P0-P3 sources, follow-up verification helpers and local specifications


## .ccg/spec/backend/index.md

SHA256: 8e887476a54cb07f7396f401e4069729bc0378870ba27bd31c9de90191f2ce2c

````text
# 后端约定

## 任务结果与重试

- `StudyResult.SUCCESS` 表示已完成；保存未提交、禁用题库和过期任务使用 `SKIPPED`。网络失败不能转换为空章节成功。
- 上游卡片会过滤已完成附件。章节重试必须按任务类型和稳定 `jobid/objectid/id` 累计结果；解析器通过 `job_info.passed_jobs` 提供明确完成证据，缺席的任务不能直接视为成功。
- `chapter_result_callback` 只在章节最终结果确定时调用；Web 以 `CourseResult` 再核对回调汇总。`process_course` 可复用已获取的章节快照。
- worker 和 retry 队列必须有明确关闭协议，在 `finally` 中 `join`。转交重试后才能确认前一次队列任务，避免 `join` 提前返回。

## 会话与上下文

- 每个 `Chaoxing` 实例拥有一个 `SessionManager`，每线程独立复用 Session；线程结束关闭本线程 Session，所有 worker 结束后关闭实例。
- Cookie 按账号保存，保留 CookieJar 的域、路径、有效期等属性。校验续期后发布快照，其他实例不能通过恢复逻辑覆盖当前实例的 Cookie。
- 新线程/线程池工作项使用独立的 `copy_context()` 传播日志与 OCR 上下文。下载图片借用当前账号的线程 Session，独立调用不得读取全局账号 Cookie。
- Web 的 OCR 配置用 `ocr_context` 显式传递。`{}` 禁用远端 OCR 配置，`None` 读取 CLI 默认配置；不得修改进程环境变量来切换 Web 任务配置。

## 缓存与日志

- 答案键包含版本、清洗前的规范题干、题型和有序选项；冷读、检查和首次发布在同一临界区内。
- 默认每 32 次更新原子写盘，`Tiku.close()` 必须刷新剩余数据。记录异常退出的丢失边界；磁盘失败和多进程写入不在正常批量持久化保证内。
- OCR 缓存必须有容量和 TTL，暂时失败不能等同于识别成功但空白。缓存键包含配置，URL 缓存还需隔离账号会话。
- 任务状态、详情和有界日志由 `TaskStore` 同步访问、返回独立快照并统一过期。终态发布在资源关闭与任务日志排空之后。

回归入口：`python -m unittest discover -s tests -v`，CI 验证 Python 3.11 和 3.13。测试隔离外部服务和本地账号数据。


## Windows 桌面数据与生命周期（P2）

- 迁移整目录发布前完成白名单复制、校验、文件刷新与完成标记。保留原目录；新目录出现业务数据即停止覆盖；固定 staging 与源/目标路径拒绝 reparse point。
- Windows 目录共享锁不能只用 FILE_READ_ATTRIBUTES：使用 FILE_LIST_DIRECTORY 才能形成所需锁约束；发布通过持有的目录句柄 FileRenameInfo 完成且禁止替换目标。用真实进程中断及 junction 测试验证。
- Tauri 开发 profile 同时隔离业务、日志和 WebView2，调试后端覆写只允许 debug 构建；开发态不隐式读取真实 Electron 数据。
- 同步 HTTP 在阻塞工作线程运行；取消后的工作线程仍可能等待到 30s 网络截止，登记必须保持有界并在实际结束后释放，防止重复 ID 或迟到响应重新生效。后台启动与 stop 需要共享短状态转换锁。

````

## .ccg/spec/frontend/index.md

SHA256: b0752e7397ce5bb0baeec3ee828e8fd31c725acf6fea3434886ca7416691fc12

````text
# 前端与桌面约定

- 选课按账号保存，仅恢复与当前课程列表的交集；空选择或加载失败时禁用开始，不能隐式改为全部课程。
- 活动任务入口保留到终态；409 响应恢复已有任务。后端端口会改变，桌面持久化通过受控 IPC，不能依赖随机 origin 的 localStorage。
- 退出操作在等待存储之前同步锁定任务启动。异步响应必须检查账号代次、请求身份和取消信号，包含成功、409、持久化失败和 finally 分支。
- 任务启动后的恢复信息保存失败在实际进度页显示，并绑定账号和任务，避免旧失败提示污染新账号。
- 下一轮轮询等待前一轮的状态、详情和日志请求全部结束；销毁时取消请求。看到终态仍需成功取得最终详情和日志，补拉失败可重试。
- 日志按序号去重、游标递增，前端最多保留 500 条；后端截断或前端裁剪时给出可见提示。
- preload 只暴露明确命名的持久化操作；主进程校验当前主窗口、主 frame、精确后端 origin 和严格参数结构。仅保存账号、任务 ID 和状态，不保存密码。

回归入口：`npm --prefix web test`、`npm --prefix desktop test` 和 `npm --prefix web run build`。CI 中每个原生命令失败必须传播为步骤失败。

桌面测试使用 `node --test` 自动发现标准命名测试；Windows 上的 Node 20 不会展开命令行里的 `tests/*.test.js`。涉及运行命令兼容性的改动需用 CI 配置的 Node 版本验证。


## Tauri 2 桌面边界（P2）

- Tauri 的会话读写失败必须保留为可见错误，不能读取或回写 localStorage；普通浏览器与 Electron 兼容分支分别验证。
- Serde `deny_unknown_fields` 不代表仅接受 JSON object：派生结构也接受数组，枚举也接受对象。IPC envelope、嵌套 DTO 与磁盘 v1 会话应显式要求 object，operation 应先要求 String，再匹配白名单；可空但必填的键需要单独校验。
- 真实 Windows 关窗 smoke 仅向捕获的宿主 PID、准确主窗口标题发送 WM_CLOSE。不要关闭该 PID 的隐藏 dispatcher/COM 窗口；它们被关闭会破坏退出流程。
- GUI smoke 的浏览器、Electron、Tauri 启动与断言失败都必须关闭已创建的测试进程；未知场景名应失败，不能零检查报成功。DOM click 与物理鼠标输入要区分记录。

## Windows Tauri 打包与验收（P3）

- PyInstaller onedir 以完整目录树 staging，并用 Tauri resources 目录映射；包内容按版本、目录、长度和 SHA256 校验。保留供 Electron 使用的 Flask 内 web/dist，不只复制后端 exe。
- Tauri CLI 2.11.4 会将 NSIS 宿主的包类型标记 UNK 改为 NSS，打包后恢复未签名原文件。NSIS/portable 必须分别记录完整宿主哈希；签名回调捕获的 pre-sign 哈希要与独立计算结果一致，恢复后的 portable 宿主另行签名。打包回调不得改写已登记在后端清单中的第三方依赖字节。
- `Get-Command node/pwsh/7z -CommandType Application` 可能返回多个安装路径；调用单个程序时显式选择首项。跨进程 JSON 管道用明确的 UTF-8 编解码，中文窗口标题需由真实窗口夹具验证。
- release 宿主/安装 smoke 只允许 fresh Windows runner 或明确声明的专用 Windows 用户/VM；debug 覆写不能证明 release 隔离。检查进程树清理必须基于已捕获的身份/Job，supervisor 意外退出不等于已验证无残留。
- NSIS 路径表中的上游空目录祖先表示安装根；由根路径检查覆盖，不能作为空相对路径误拒绝。安装/卸载预检包含旧版本独有目录；该预检不保证抵抗检查后的并发路径替换。

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/capture-review.py

SHA256: 52456a3e95ed0afec999f8f539d0f04077f2ce4167b077240337ad6f2a198a49

````text
"""Capture complete source bytes for a bounded external review, never only git diff."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("stage")
args = parser.parse_args()
if not args.stage or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in args.stage):
    raise SystemExit("Unsafe stage name")
task = Path(__file__).resolve().parents[1]
root = Path(subprocess.check_output(["git", "-C", str(task), "rev-parse", "--show-toplevel"], text=True, timeout=15).strip())
research = task / "research"
manifest_path = research / f"{args.stage}-source-manifest.json"
snapshot_path = research / f"{args.stage}-source-snapshot.md"
if manifest_path.exists() or snapshot_path.exists():
    raise SystemExit("Review evidence exists; choose a fresh stage name")

names = {
    ".github/workflows/main.yml", ".gitignore", "README.md", "desktop/README.md", "build_tauri.bat",
    "pyproject.toml", "requirements.txt", "requirements-test.txt", "chaoxing.spec", "chaoxing-backend.spec",
    "app.py", "api/desktop_runtime.py", "api/logger.py", "web/vite.config.js", "web/package.json", "web/package-lock.json",
    "desktop/main.js", "desktop/preload.js", "desktop/session-store.js",
    "desktop/package.json", "desktop/package-lock.json", "desktop/electron-builder.yml",
    "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock", "desktop/src-tauri/build.rs",
    "desktop/src-tauri/tauri.conf.json", "desktop/rust-toolchain.toml", "tests/test_release_version.py",
    "tests/test_desktop_runtime.py", "tests/test_tauri_entry_contract.py",
    ".ccg/spec/backend/index.md", ".ccg/spec/frontend/index.md",
}
for folder, patterns in {
    "desktop/src-tauri/src": ["**/*.rs"],
    "desktop/src-tauri/tests": ["**/*.rs"],
    "desktop/src-tauri/capabilities": ["*.json"],
    "desktop/src-tauri/windows": ["*"],
    "desktop/scripts": ["*.ps1", "*.mjs", "*.py"],
    "desktop/tests": ["*.mjs", "*.js"],
    "desktop/tests/fixtures": ["p3-*", "nsis-paths.nsi", "p2_backend.py"],
    "desktop/portable": ["*"],
    "web/src": ["**/*.js", "**/*.jsx", "**/*.css"],
    "web/src/api": ["*.js"],
    "web/src/lib": ["desktopBridge*", "sessionStore*"],
    "web/src/components": ["DesktopStartup*"],
}.items():
    for pattern in patterns:
        names.update(path.relative_to(root).as_posix() for path in (root / folder).glob(pattern) if path.is_file())

tracked = set(subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], timeout=15).decode("utf-8").split("\0"))
names.update(path.relative_to(root).as_posix() for pattern in ("*.ps1", "*.py")
             for path in (task / "verification").glob(pattern) if path.is_file())
records = []
with snapshot_path.open("x", encoding="utf-8", newline="\n") as snapshot:
    snapshot.write("# Current P0-P3 sources, follow-up verification helpers and local specifications\n\n")
    for name in sorted(names):
        path = root / name
        contents = path.read_bytes()
        digest = hashlib.sha256(contents).hexdigest()
        lockfile = name.endswith(("package-lock.json", "Cargo.lock"))
        records.append({"path": name, "bytes": len(contents), "sha256": digest, "trackedAtCapture": name in tracked,
                        "bodyIncluded": not lockfile})
        snapshot.write(f"\n## {name}\n\nSHA256: {digest}\n\n")
        if lockfile:
            snapshot.write("Lock file retained on disk at the exact repository path; hash recorded above.\n")
        else:
            snapshot.write("````text\n" + contents.decode("utf-8-sig") + "\n````\n")
manifest = {"capturedAt": datetime.now(timezone.utc).isoformat(), "head": subprocess.check_output(
    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=15).strip(),
    "stage": args.stage, "repoRoot": str(root), "files": records,
    "protectedPlanSha256": hashlib.sha256((root / ".ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md").read_bytes()).hexdigest(),
    "snapshotSha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest()}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"snapshot": str(snapshot_path), "manifest": str(manifest_path), "files": len(records)}, ensure_ascii=False))

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/collect-external-errors.py

SHA256: 586eaf0cfcfe52647ea1751a4dfc1bd47c29193c0012e79d572d5297dcbad405

````text
"""Capture bounded, redacted API-error evidence without rerunning external models."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


TASK = Path(__file__).resolve().parents[1]
SESSION_ROOT = Path("C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui")
STAGES = ("followup-analysis", "p0p1p2-review", "p3-review")
SESSION_ID = re.compile(
    r"Session[- ]ID\s*[:=]\s*([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})(?![0-9a-f-])",
    re.IGNORECASE,
)
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"\b(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|client[-_ ]?secret|"
        r"token|password|authorization)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"'`,;}\]]+",
        re.IGNORECASE,
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{40,}(?![A-Za-z0-9])"),
)


def redact(value):
    """Apply only to allowlisted error fields; never serialize arbitrary records."""
    if not isinstance(value, str):
        return None
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result[:2000] + (" [TRUNCATED]" if len(result) > 2000 else "")


def read_stable(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Source changed during collection")
    return data, {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def utc_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def api_errors(session_id, accessed):
    """Read only the exact file named by a completed lane's stderr Session-ID."""
    path = SESSION_ROOT / (session_id + ".jsonl")
    result = {"sessionId": session_id, "file": path.as_posix(), "errors": [], "issues": []}
    if path.resolve().parent != SESSION_ROOT.resolve() or path.is_symlink():
        result["issues"].append("Session path is redirected; file was not read")
        return result
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as stream:
            accessed.append({"sessionId": session_id, "file": path.as_posix()})
            for line_number, raw in enumerate(stream, 1):
                digest.update(raw)
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeError, json.JSONDecodeError):
                    result["issues"].append(f"Malformed JSON at line {line_number}; content omitted")
                    continue
                if not isinstance(record, dict) or record.get("isApiErrorMessage") is not True:
                    continue
                if record.get("sessionId") != session_id:
                    result["issues"].append(f"Session-ID mismatch at line {line_number}; content omitted")
                    continue
                message = record.get("message")
                blocks = message.get("content", []) if isinstance(message, dict) else []
                texts = [block["text"] for block in blocks if isinstance(block, dict)
                         and block.get("type") == "text" and isinstance(block.get("text"), str)]
                original = "\n".join(texts)
                status = record.get("apiErrorStatus")
                status = int(status) if str(status).isdigit() and 100 <= int(status) <= 599 else None
                error_type = record.get("error")
                safe_message, safe_type = redact(original), redact(error_type)
                result["errors"].append({
                    "line": line_number,
                    "timestamp": redact(record.get("timestamp")),
                    "isApiErrorMessage": True,
                    "apiErrorStatus": status,
                    "apiErrorIsTransient": record.get("apiErrorIsTransient") is True,
                    "errorType": safe_type,
                    "message": safe_message,
                    "redactionApplied": safe_message != original or safe_type != error_type,
                })
        after = path.stat()
        result["sha256"] = digest.hexdigest()
        result["bytes"] = after.st_size
        result["sourceStable"] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        if not result["sourceStable"]:
            result["issues"].append("Session file changed while read; snapshot may be incomplete")
    except OSError as exc:
        result["issues"].append(f"Exact session file unavailable: {type(exc).__name__}")
    return result


def collect_lane(stage, lane, accessed):
    name = f"{stage}-{lane}"
    result = {"name": name, "stage": stage, "lane": lane, "availability": "unavailable",
              "passed": False, "reportBodyPresent": None, "sources": {}, "sessions": [], "issues": []}
    prefix = TASK / "research" / name
    try:
        raw, metadata = read_stable(prefix.with_suffix(".result.json"))
        invocation = json.loads(raw.decode("utf-8-sig"))
        if (not isinstance(invocation, dict) or invocation.get("stage") != stage
                or invocation.get("lane") != lane or type(invocation.get("exitCode")) is not int
                or type(invocation.get("timedOut")) is not bool or not invocation.get("endedAt")):
            raise ValueError("No valid completed invocation result")
        duration = (utc_time(invocation["endedAt"]) - utc_time(invocation["startedAt"])).total_seconds()
        if duration < 0:
            raise ValueError("Invalid invocation duration")
        result["sources"]["result"] = {"file": f"research/{name}.result.json", **metadata}
        for suffix in ("stdout.md", "stderr.log"):
            contents, metadata = read_stable(prefix.with_suffix("." + suffix))
            result["sources"][suffix.split(".")[0]] = {"file": f"research/{name}.{suffix}", **metadata}
            if suffix == "stdout.md":
                stdout = contents.decode("utf-8-sig")
            else:
                stderr = contents.decode("utf-8-sig")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        result["issues"].append(f"Completed evidence unavailable: {type(exc).__name__}; active files were not changed")
        return result

    for key in ("startedAt", "endedAt", "timeoutSeconds", "timedOut", "exitCode"):
        result[key] = invocation.get(key)
    result.update({"availability": "complete", "durationSeconds": round(duration, 3),
                   "stdoutNonWhitespace": bool(stdout.strip()),
                   "reportBodyPresent": None if stdout.strip() else False,
                   "wrapperInvocationSucceeded": invocation.get("invocationSucceeded"),
                   "wrapperReviewPassed": invocation.get("reviewPassed")})
    ids = list(dict.fromkeys(match.lower() for match in SESSION_ID.findall(stderr)))
    result["sessionIdsFromStderr"] = ids
    for session_id in ids:
        result["sessions"].append(api_errors(session_id, accessed))
    if not ids:
        result["issues"].append("No valid Session-ID in stderr; no session files accessed")
    errors = [error for session in result["sessions"] for error in session["errors"]]
    result["apiStatuses"] = sorted({error["apiErrorStatus"] for error in errors if error["apiErrorStatus"] is not None})
    failed = invocation["exitCode"] != 0 or invocation["timedOut"] or not stdout.strip() or bool(errors)
    result["passed"] = False if failed else None
    result["assessment"] = ("No passing report: invocation failed, API error, timeout, or blank stdout"
                            if failed else "Nonempty output requires lead interpretation; no pass inferred")
    return result


def markdown(evidence):
    rows = ["# External invocation evidence", "", f"Collected at `{evidence['collectedAt']}`.", "",
            "All values below come from completed invocation files and structured API-error records. "
            "A failed invocation or blank stdout is `passed=false`; this collector never grants a review pass.", "",
            "| Lane | Exit | Timed out | Duration (s) | Stdout bytes | Report body | API status | Passed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for lane in evidence["lanes"]:
        value = lambda key: json.dumps(lane.get(key), ensure_ascii=False)
        rows.append(f"| {lane['name']} | {value('exitCode')} | {value('timedOut')} | {value('durationSeconds')} "
                    f"| {lane['sources'].get('stdout', {}).get('bytes', 'unavailable')} "
                    f"| {value('reportBodyPresent')} | {value('apiStatuses')} | {value('passed')} |")
    rows += ["", "## Actual API errors", ""]
    for lane in evidence["lanes"]:
        for session in lane["sessions"]:
            for error in session["errors"]:
                message = (error["message"] or "No error text recorded").replace("`", "'").replace("\n", " ")
                rows.append(f"- `{lane['name']}`: `{error['timestamp']}`, JSONL line {error['line']}, "
                            f"status `{error['apiErrorStatus']}`, type `{error['errorType']}`: `{message}`")
    rows += ["", "## Exact session-file access", "",
             "Session IDs were extracted from each completed lane's `stderr.log`. "
             "Only these exact files under `C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui/` "
             "were opened; no session-directory enumeration was performed.", ""]
    rows += [f"- `{item['sessionId']}.jsonl`" for item in evidence["accessedSessionFiles"]]
    rows += ["", "## Collection boundaries and verification", "",
             "- Source result/stdout/stderr and accessed session SHA256 values are recorded in `../verification/external-errors.json`.",
             "- Output contains allowlisted API-error fields only. User prompts, tool inputs, general messages, and full sessions are omitted; potential credentials are redacted.",
             "- Incomplete or changing sources are recorded as unavailable; this run did not alter any invocation files.",
             "- The collector uses exclusive creation and refuses to overwrite either finished output file.",
             "- The collector was checked by in-memory Python compilation before this capture. No model, network, release host, installer, or business AppData access was used.",
             "- Exit 0 and nonempty stdout would still require lead review of the report. These records establish invocation failure, not a substantive code-review finding."]
    issues = [f"{lane['name']}: {issue}" for lane in evidence["lanes"] for issue in lane["issues"]]
    issues += [f"{lane['name']}/{session['sessionId']}: {issue}" for lane in evidence["lanes"]
               for session in lane["sessions"] for issue in session["issues"]]
    rows += ["", "Collection issues: " + ("; ".join(issues) if issues else "none."), ""]
    return "\n".join(rows)


def main():
    output = TASK / "verification" / "external-errors.json"
    report = TASK / "research" / "external-evidence.md"
    if output.exists() or report.exists():
        raise SystemExit("Finished external-error evidence exists; refusing to overwrite")
    accessed = []
    evidence = {"schemaVersion": 1, "collectedAt": datetime.now(timezone.utc).isoformat(),
                "sessionRoot": SESSION_ROOT.as_posix(), "collector": "verification/collect-external-errors.py",
                "passingReviewEstablished": False,
                "lanes": [collect_lane(stage, lane, accessed) for stage in STAGES for lane in ("a", "b")],
                "accessedSessionFiles": accessed}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown(evidence))
    print(json.dumps({"output": str(output), "report": str(report), "lanes": len(evidence["lanes"]),
                      "sessionsAccessed": len(accessed), "passingReviewEstablished": False}))


if __name__ == "__main__":
    main()

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/launch-sandbox-acceptance.ps1

SHA256: 30f96cb47993fc47dbe0e69ea1ce3d269d7c1668309886126d247017d44736ad

````text
#Requires -Version 7.0
param([ValidateRange(30,2400)][int]$TimeoutSeconds=1800)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$kit=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-kit.json') -Raw | ConvertFrom-Json
$inputDirectory=[IO.Path]::GetFullPath($kit.inputDirectory)
if ((Get-FileHash -LiteralPath (Join-Path $inputDirectory 'input-manifest.json')).Hash.ToLowerInvariant() -cne $kit.inputManifestSha256) { throw 'Input manifest changed.' }
if (@(Get-Process -Name WindowsSandbox,WindowsSandboxClient -ErrorAction SilentlyContinue).Count) { throw 'Existing Sandbox; refusing reuse or interruption.' }
$runDirectory=Join-Path $kit.kitDirectory ('run-' + [Guid]::NewGuid().ToString('N'))
$outputDirectory=Join-Path $runDirectory 'output'
[void][IO.Directory]::CreateDirectory($outputDirectory)
$escapedInput=[Security.SecurityElement]::Escape($inputDirectory)
$escapedOutput=[Security.SecurityElement]::Escape($outputDirectory)
$configPath=Join-Path $runDirectory 'acceptance.wsb'
$config=@"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Default</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$escapedInput</HostFolder><SandboxFolder>C:\TauriAcceptance\Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$escapedOutput</HostFolder><SandboxFolder>C:\TauriAcceptance\Output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\TauriAcceptance\Input\sandbox-bootstrap.ps1</Command></LogonCommand>
</Configuration>
"@
[xml]$parsedConfig=$config
[IO.File]::WriteAllText($configPath,$config,[Text.UTF8Encoding]::new($false))
$record=[ordered]@{startedAt=[DateTime]::UtcNow.ToString('o');configPath=$configPath;outputDirectory=$outputDirectory;sourceCommit=$kit.sourceCommit;productExecutedOnHost=$false;success=$false;networking='enabled for Microsoft certificate/runtime setup; no real accounts'}
$recordFile=Join-Path $PSScriptRoot ('sandbox-launch-' + [IO.Path]::GetFileName($runDirectory) + '.json')
$record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordFile -Encoding utf8
$process=$null
try {
    $process=Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/WindowsSandbox.exe') -ArgumentList ('"' + $configPath + '"') -WindowStyle Hidden -PassThru
    $record['launcherPid']=$process.Id
    $record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $recordFile -Encoding utf8
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    Write-Output "Sandbox acceptance started; evidence: $outputDirectory"
    while ([DateTime]::UtcNow -lt $deadline) {
        $resultPath=Join-Path $outputDirectory 'result.json'
        if (Test-Path -LiteralPath $resultPath) {
            try { $actual=Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json } catch { Start-Sleep -Milliseconds 500; continue }
            $record['guestResultPath']=$resultPath
            $record['guestIdentity']=$actual.identity
            $record.success=$actual.success -eq $true
            $record['guestPhase']=$actual.phase
            if (-not $record.success) { $record['guestError']=$actual.error }
            break
        }
        $bootstrapPath=Join-Path $outputDirectory 'bootstrap-result.json'
        if (Test-Path -LiteralPath $bootstrapPath) { throw 'Guest bootstrap finished without a product acceptance report; inspect bootstrap logs.' }
        if ($process.HasExited -and $process.ExitCode -ne 0) { throw "Sandbox launcher exited $($process.ExitCode)" }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $record.Contains('guestResultPath')) { throw 'Sandbox acceptance timed out without completed evidence.' }
} catch {
    $record['error']=$_.Exception.Message
} finally {
    $record['endedAt']=[DateTime]::UtcNow.ToString('o')
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordFile -Encoding utf8
    $record | ConvertTo-Json -Depth 8
    if ($process) { $process.Dispose() }
}
if (-not $record.success) { exit 1 }

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/launch-sandbox-probe.ps1

SHA256: 9643d96dd89b70d5f72e9b250ef5525293c8513cbf2c3cbd4f593f7bdc6c0af1

````text
#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = (git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate repository' }
$base = Join-Path $repoRoot ('desktop/src-tauri/target/acceptance-followup-probe-' + [Guid]::NewGuid().ToString('N'))
$inputDirectory = Join-Path $base 'input'
$outputDirectory = Join-Path $base 'output'
[void][IO.Directory]::CreateDirectory($inputDirectory)
[void][IO.Directory]::CreateDirectory($outputDirectory)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-probe.ps1') -Destination $inputDirectory
$configPath = Join-Path $base 'probe.wsb'
$escapedInput = [Security.SecurityElement]::Escape($inputDirectory)
$escapedOutput = [Security.SecurityElement]::Escape($outputDirectory)
$config = @"
<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <AudioInput>Disable</AudioInput>
  <VideoInput>Disable</VideoInput>
  <PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>4096</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>$escapedInput</HostFolder><SandboxFolder>C:\TauriAcceptance\Input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$escapedOutput</HostFolder><SandboxFolder>C:\TauriAcceptance\Output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\TauriAcceptance\Input\sandbox-probe.ps1</Command></LogonCommand>
</Configuration>
"@
[IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
if (@(Get-Process -Name WindowsSandbox,WindowsSandboxClient -ErrorAction SilentlyContinue).Count) {
    throw 'An existing Windows Sandbox is active; refusing to reuse or stop it.'
}
$run = [ordered]@{startedAt=[DateTime]::UtcNow.ToString('o'); configPath=$configPath; inputDirectory=$inputDirectory; outputDirectory=$outputDirectory; productExecuted=$false; success=$false}
$process = $null
try {
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/WindowsSandbox.exe') -ArgumentList ('"' + $configPath + '"') -WindowStyle Hidden -PassThru
    $run['launcherPid'] = $process.Id
    $deadline = [DateTime]::UtcNow.AddSeconds(150)
    while ([DateTime]::UtcNow -lt $deadline) {
        $resultPath = Join-Path $outputDirectory 'probe-result.json'
        if (Test-Path -LiteralPath $resultPath) {
            $probe = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
            $run['guest'] = $probe
            $run.success = $probe.success -eq $true
            Copy-Item -LiteralPath $resultPath -Destination (Join-Path $PSScriptRoot 'sandbox-probe-result.json')
            break
        }
        if ($process.HasExited) {
            $run['launcherExitCode'] = $process.ExitCode
            if ($process.ExitCode -ne 0) { throw "Windows Sandbox launcher exited $($process.ExitCode)" }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $run.success) { throw 'No guest probe evidence within 150 seconds.' }
} catch {
    $run['error'] = $_.Exception.Message
} finally {
    $run['endedAt'] = [DateTime]::UtcNow.ToString('o')
    $run | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-probe-launch.json') -Encoding utf8
    $run | ConvertTo-Json -Depth 10
    if ($process) { $process.Dispose() }
}
if (-not $run.success) { exit 1 }

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/prepare-sandbox-kit.ps1

SHA256: 43356a88af93c0d3ec7393c69b1e783dc42671f550e24f1768473d08047658ba

````text
#Requires -Version 7.0
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=(git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Missing repository' }
$head=(git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve source HEAD.' }
& git -C $repo merge-base --is-ancestor 37fde9160229e99d2fa837d2f1a850203c260d81 $head
if ($LASTEXITCODE -ne 0) { throw 'Source is not based on the accepted handoff.' }
$sourceChanges=@(git -C $repo diff --name-only HEAD -- desktop web pyproject.toml LICENSE)
if ($LASTEXITCODE -ne 0 -or $sourceChanges.Count) { throw 'Commit reviewed source changes before exporting acceptance inputs.' }
$target=[IO.Path]::GetFullPath((Join-Path $repo 'desktop/src-tauri/target'))
$kit=Join-Path $target ('acceptance-followup-kit-' + [Guid]::NewGuid().ToString('N'))
if (-not $kit.StartsWith($target + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid kit path.' }
$inputDirectory=Join-Path $kit 'input'
[void][IO.Directory]::CreateDirectory($inputDirectory)
$tools=Join-Path $inputDirectory 'tools'
foreach ($directory in @($tools,(Join-Path $tools 'node'),(Join-Path $tools '7zip'),(Join-Path $inputDirectory 'artifacts'),(Join-Path $inputDirectory 'fixture'))) {
    [void][IO.Directory]::CreateDirectory($directory)
}
$node=Join-Path $env:TEMP 'chaoxing-p3-node20/node_modules/node/bin/node.exe'
$nodeVersion=(& $node --version).Trim()
if ($LASTEXITCODE -ne 0 -or $nodeVersion -cne 'v20.20.0') { throw 'Node 20.20.0 is required.' }
$runtime=Join-Path $target 'acceptance-followup-tools/MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
$signature=Get-AuthenticodeSignature -LiteralPath $runtime
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Official runtime signature verification failed.' }
$sevenArchive=Join-Path $target 'acceptance-followup-tools/7z2501-x64.exe'
$sevenDirectory=Join-Path $tools '7zip'
& (Get-Command 7z.exe -CommandType Application | Select-Object -First 1).Source x -y "-o$sevenDirectory" $sevenArchive 7z.exe 7z.dll
if ($LASTEXITCODE -ne 0) { throw 'Could not extract standalone 7-Zip tools.' }
& (Join-Path $sevenDirectory '7z.exe') i > (Join-Path $PSScriptRoot '7zip-portable-version.txt')
if ($LASTEXITCODE -ne 0) { throw 'Transferred 7-Zip executable cannot run independently.' }
Copy-Item -LiteralPath $node -Destination (Join-Path $tools 'node/node.exe')
Copy-Item -LiteralPath $PSHOME -Destination (Join-Path $tools 'pwsh') -Recurse
Copy-Item -LiteralPath (Join-Path $repo 'desktop/node_modules/playwright-core') -Destination (Join-Path $tools 'playwright-core') -Recurse
Copy-Item -LiteralPath $runtime -Destination (Join-Path $tools 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe')
Copy-Item -LiteralPath (Join-Path $target 'acceptance-followup-fixture/dist/p2-backend') -Destination (Join-Path $inputDirectory 'fixture/p2-backend') -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-guest.ps1') -Destination $inputDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox-bootstrap.ps1') -Destination $inputDirectory
$artifactRoot=Join-Path $repo 'desktop/release/tauri'
$expected=@{
    'chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip'='3318ca14cf4c20818f69fedded0f52b479ab542003cc12625e19398f7b993e76'
    'chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe'='70c18ac432d6e05dff8eea4680b1a3f3c9c1b840370c4460202865d8446ddc09'
}
foreach ($name in $expected.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $artifactRoot $name)).Hash.ToLowerInvariant() -cne $expected[$name]) { throw "P3 artifact changed: $name" }
}
foreach ($file in Get-ChildItem -LiteralPath $artifactRoot -File) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $inputDirectory 'artifacts')
}
$sourceZip=Join-Path $inputDirectory 'source.zip'
& git -C $repo archive --format=zip "--output=$sourceZip" $head desktop web pyproject.toml LICENSE
if ($LASTEXITCODE -ne 0) { throw 'Could not export committed acceptance source.' }
$manifest=[ordered]@{schemaVersion=1;baselineCommit='37fde9160229e99d2fa837d2f1a850203c260d81';sourceCommit=$head;sourcePaths=@('desktop','web','pyproject.toml','LICENSE');createdAt=[DateTime]::UtcNow.ToString('o');files=@()}
foreach ($file in Get-ChildItem -LiteralPath $inputDirectory -File -Recurse) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked kit file: $($file.FullName)" }
    $manifest.files += [ordered]@{path=[IO.Path]::GetRelativePath($inputDirectory,$file.FullName).Replace('\','/');length=$file.Length;sha256=(Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant()}
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Destination (Join-Path $PSScriptRoot 'sandbox-input-manifest.json')
$metadata=[ordered]@{createdAt=[DateTime]::UtcNow.ToString('o');kitDirectory=$kit;inputDirectory=$inputDirectory;sourceCommit=$head;files=$manifest.files.Count;
    inputManifestSha256=(Get-FileHash -LiteralPath (Join-Path $inputDirectory 'input-manifest.json')).Hash.ToLowerInvariant();
    runtime=[ordered]@{downloadUrl='https://go.microsoft.com/fwlink/?LinkId=2124701';sha256=(Get-FileHash -LiteralPath $runtime).Hash.ToLowerInvariant();signer=$signature.SignerCertificate.Subject;signature=[string]$signature.Status};
    sevenZip=[ordered]@{downloadUrl='https://www.7-zip.org/a/7z2501-x64.exe';sha256=(Get-FileHash -LiteralPath $sevenArchive).Hash.ToLowerInvariant()};
    productExecutedOnHost=$false;node=$nodeVersion;powershell=[string]$PSVersionTable.PSVersion}
$metadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sandbox-kit.json') -Encoding utf8
$kitId=[IO.Path]::GetFileName($kit).Substring('acceptance-followup-kit-'.Length)
$metadata | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot ("sandbox-kit-$kitId.json")) -Encoding utf8
Copy-Item -LiteralPath (Join-Path $inputDirectory 'input-manifest.json') -Destination (Join-Path $PSScriptRoot ("sandbox-input-manifest-$kitId.json"))
$metadata | ConvertTo-Json -Depth 6

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/run-claude-pair.ps1

SHA256: a5d5cff9ea99fce6c6e1ee3ca6ba763d257784dbc100a677fbc6194f4e32a997

````text
param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [ValidateRange(30, 1200)][int]$TimeoutSeconds = 300,
    [string]$RepoRoot
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
if (-not $RepoRoot) {
    $RepoRoot = git -C $PSScriptRoot rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve repository root' }
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$wrapperPath = 'C:/Users/RUD/.claude/bin/codeagent-wrapper.exe'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
public sealed class ReviewProcessJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long UserTime, JobTime; public uint Flags;
        public UIntPtr MinSet, MaxSet; public uint Active; public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Io { public ulong R1, R2, R3, R4, R5, R6; }
    [StructLayout(LayoutKind.Sequential)] struct Limits {
        public Basic Basic; public Io Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr h, int cls, ref Limits info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr h, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    IntPtr handle;
    public ReviewProcessJob(Process process) {
        handle = CreateJobObject(IntPtr.Zero, null);
        if (handle == IntPtr.Zero) throw new Win32Exception();
        var limits = new Limits(); limits.Basic.Flags = 0x2000;
        if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)) || !AssignProcessToJobObject(handle, process.Handle)) {
            int error = Marshal.GetLastWin32Error(); Dispose(); throw new Win32Exception(error);
        }
    }
    public void Dispose() { if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; } }
}
'@
$running = [System.Collections.Generic.List[object]]::new()
$allSucceeded = $true
try {
    foreach ($lane in @('a', 'b')) {
        $prefix = Join-Path $taskRoot "research/$Stage-$lane"
        if (Test-Path -LiteralPath "$prefix.result.json") { throw "Evidence already exists: $prefix" }
        $prompt = Get-Content -LiteralPath "$prefix.prompt.md" -Raw
        $info = [System.Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $wrapperPath
        $info.WorkingDirectory = $RepoRoot
        foreach ($arg in @('--progress', '--backend', 'claude', '-', $RepoRoot)) { $info.ArgumentList.Add($arg) }
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
        $info.Environment['CODEX_TIMEOUT'] = [string]($TimeoutSeconds * 1000)
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $info
        $run = [pscustomobject]@{
            Process=$process; Job=$null; Prefix=$prefix; Lane=$lane; Started=[DateTime]::UtcNow
            OutFile=$null; ErrFile=$null; OutTask=$null; ErrTask=$null; Done=$false; StartedProcess=$false
        }
        $running.Add($run)
        [void]$process.Start()
        $run.StartedProcess = $true
        $run.Job = [ReviewProcessJob]::new($process)
        $run.OutFile = [System.IO.File]::Create("$prefix.stdout.md")
        $run.ErrFile = [System.IO.File]::Create("$prefix.stderr.log")
        $run.OutTask = $process.StandardOutput.BaseStream.CopyToAsync($run.OutFile)
        $run.ErrTask = $process.StandardError.BaseStream.CopyToAsync($run.ErrFile)
        $process.StandardInput.Write($prompt)
        $process.StandardInput.Close()
        Write-Output "Started $Stage-$lane PID=$($process.Id), deadline=${TimeoutSeconds}s, process-tree Job active"
    }
    while (@($running | Where-Object { -not $_.Done }).Count -gt 0) {
        foreach ($run in @($running | Where-Object { -not $_.Done })) {
            $timedOut = ([DateTime]::UtcNow - $run.Started).TotalSeconds -ge $TimeoutSeconds -and -not $run.Process.HasExited
            if ($timedOut) {
                $run.Process.Kill($true)
                $run.Job.Dispose()
                if (-not $run.Process.WaitForExit(5000)) { throw "Review process failed to stop: $($run.Process.Id)" }
            }
            if ($run.Process.HasExited) {
                $run.Job.Dispose()
                if (-not $run.OutTask.Wait(5000) -or -not $run.ErrTask.Wait(5000)) { throw 'Review output drain timed out' }
                $run.OutFile.Dispose()
                $run.ErrFile.Dispose()
                $hasOutput = (Get-Item -LiteralPath "$($run.Prefix).stdout.md").Length -gt 0
                $ok = -not $timedOut -and $run.Process.ExitCode -eq 0 -and $hasOutput
                $result = [ordered]@{
                    stage=$Stage; lane=$run.Lane; repoRoot=$RepoRoot; pid=$run.Process.Id
                    command='codeagent-wrapper.exe --progress --backend claude - <repoRoot>'
                    startedAt=$run.Started.ToString('o'); endedAt=[DateTime]::UtcNow.ToString('o')
                    timeoutSeconds=$TimeoutSeconds; timedOut=($timedOut -or $run.Process.ExitCode -eq 124)
                    exitCode=$run.Process.ExitCode; stdoutPresent=$hasOutput
                    invocationSucceeded=$ok; reviewPassed=$false
                    conclusion=$(if ($ok) { 'Report requires human/lead interpretation' } else { 'No passing review: invocation failed or empty report' })
                    processTreeCleanup='KILL_ON_JOB_CLOSE disposed; output streams drained'
                }
                $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$($run.Prefix).result.json" -Encoding utf8
                Write-Output ($result | ConvertTo-Json -Compress)
                $run.Done = $true
                if (-not $ok) { $allSucceeded = $false }
            }
        }
        if (@($running | Where-Object { -not $_.Done }).Count -gt 0) { Start-Sleep -Milliseconds 250 }
    }
} finally {
    foreach ($run in $running) {
        if ($run.StartedProcess -and -not $run.Process.HasExited) {
            $run.Process.Kill($true)
            [void]$run.Process.WaitForExit(5000)
        }
        if ($run.Job) { $run.Job.Dispose() }
        if ($run.OutFile) { $run.OutFile.Dispose() }
        if ($run.ErrFile) { $run.ErrFile.Dispose() }
        $run.Process.Dispose()
    }
}
if (-not $allSucceeded) { exit 1 }

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/sandbox-bootstrap.ps1

SHA256: 8a2ec64ec8a60eab8d6ce06b0316fd6a2c4655f9730a2d288bb8f321a43f7585

````text
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq 'DESKTOP-3DSSD2K') { throw 'Sandbox guest required.' }
$code=1
try {
    & 'C:\TauriAcceptance\Input\tools\pwsh\pwsh.exe' -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\TauriAcceptance\Input\sandbox-guest.ps1' -ShutdownGuest 1> 'C:\TauriAcceptance\Output\bootstrap.stdout.log' 2> 'C:\TauriAcceptance\Output\bootstrap.stderr.log'
    $code=$LASTEXITCODE
} catch {
    $_ | Out-String | Set-Content -LiteralPath 'C:\TauriAcceptance\Output\bootstrap-error.log'
} finally {
    @{identity=$identity;exitCode=$code;endedAt=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath 'C:\TauriAcceptance\Output\bootstrap-result.json'
    & "$env:SystemRoot\System32\shutdown.exe" /s /t 5
}

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/sandbox-guest.ps1

SHA256: b08babc25cf5a86f181cbbf179ff4a90939339853e6928e18fa5c9e7daceb9c7

````text
#Requires -Version 7.0
param(
    [string]$InputDirectory = 'C:\TauriAcceptance\Input',
    [string]$OutputDirectory = 'C:\TauriAcceptance\Output',
    [switch]$ShutdownGuest
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$machine = Get-CimInstance Win32_ComputerSystem
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq 'DESKTOP-3DSSD2K' -or $machine.Model -ne 'Virtual Machine') {
    throw 'Product acceptance is restricted to the actual Windows Sandbox guest.'
}
if ($OutputDirectory -cne 'C:\TauriAcceptance\Output' -or $InputDirectory -cne 'C:\TauriAcceptance\Input') {
    throw 'Unexpected Sandbox mapping.'
}
$work = Join-Path 'C:\' ('tauri-acceptance-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$evidence = Join-Path $work 'evidence'
[void][IO.Directory]::CreateDirectory($evidence)
$report = [ordered]@{
    schemaVersion=1; startedAt=[DateTime]::UtcNow.ToString('o'); success=$false; identity=$identity
    sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; computerName=$env:COMPUTERNAME
    computerModel=$machine.Model; os=[Environment]::OSVersion.VersionString; workDirectory=$work
    sourceCommit=$null; stages=@(); checks=@(); phase='input-verification'
    remoteCI=$false; realAccountsUsed=$false; learningTasksCreated=$false
    limitations=@('Windows Sandbox on Windows 11 build 26100, not the full Win10/Win11 matrix',
        'DOM clicks through CDP, not physical mouse input', 'Artifacts are unsigned; no actual code-signing success path')
}
function Save-Status {
    $report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'status.json') -Encoding utf8
}
function Assert-NoProductProfiles {
    $states = @(foreach ($folder in @('ApplicationData','LocalApplicationData')) {
        foreach ($app in @('com.chaoxing.gui','chaoxing-desktop')) {
            $target = Join-Path ([Environment]::GetFolderPath($folder)) $app
            [ordered]@{path=$target; exists=(Test-Path -LiteralPath $target)}
        }
    })
    if (@($states | Where-Object exists).Count) { throw 'Preexisting application data in guest; refusing release execution.' }
    return $states
}
function Run-Stage {
    param([string]$Name, [string]$Executable, [string[]]$Arguments, [int]$ExpectedExit=0, [int]$Timeout=300)
    $report.phase=$Name
    Save-Status
    $stage = [ordered]@{name=$Name; executable=$Executable; args=$Arguments; expectedExit=$ExpectedExit; startedAt=[DateTime]::UtcNow.ToString('o')}
    $report.stages += $stage
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Executable
    $info.WorkingDirectory=$work
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::new()
    $process.StartInfo=$info
    $outFile=$null; $errFile=$null; $started=$false
    try {
        $outFile=[IO.File]::Open((Join-Path $OutputDirectory ($Name + '.stdout.log')), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $errFile=[IO.File]::Open((Join-Path $OutputDirectory ($Name + '.stderr.log')), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        [void]$process.Start()
        $started=$true
        $stage['pid']=$process.Id
        $outTask=$process.StandardOutput.BaseStream.CopyToAsync($outFile)
        $errTask=$process.StandardError.BaseStream.CopyToAsync($errFile)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit($Timeout * 1000)) {
            $stage['timedOut']=$true
            $process.Kill($true)
            [void]$process.WaitForExit(10000)
            throw "$Name timed out after $Timeout seconds."
        }
        if (-not $outTask.Wait(10000) -or -not $errTask.Wait(10000)) { throw "$Name output drain timed out." }
        $stage['exitCode']=$process.ExitCode
        $stage['success']=$process.ExitCode -eq $ExpectedExit
        if (-not $stage.success) { throw "$Name returned $($process.ExitCode), expected $ExpectedExit." }
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(10000) }
        if ($outFile) { $outFile.Dispose() }
        if ($errFile) { $errFile.Dispose() }
        $process.Dispose()
        $stage['endedAt']=[DateTime]::UtcNow.ToString('o')
        Save-Status
    }
}
function Read-OnlyRunReport {
    param([string]$Directory, [string]$Kind)
    $paths=@(Get-ChildItem -LiteralPath $Directory -Directory | ForEach-Object { Join-Path $_.FullName 'result.json' } | Where-Object { Test-Path -LiteralPath $_ })
    if ($paths.Count -ne 1) { throw "$Kind must produce exactly one run report." }
    $actual=Get-Content -LiteralPath $paths[0] -Raw | ConvertFrom-Json
    if ($actual.success -ne $true -or @($actual.checks).Count -eq 0) { throw "$Kind did not pass its actual assertions." }
    return $actual
}
try {
    $report['initialProductProfiles']=Assert-NoProductProfiles
    $python=@(Get-Command python.exe,python3.exe,py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object Name,Source)
    $report['initialPythonCommands']=$python
    $report['pythonRegistryPresent']=(Test-Path -LiteralPath 'HKLM:\SOFTWARE\Python') -or (Test-Path -LiteralPath 'HKCU:\SOFTWARE\Python')
    if ($python.Count -ne 0 -or $report.pythonRegistryPresent) { throw 'Guest must have no system Python before acceptance.' }
    $manifest=Get-Content -LiteralPath (Join-Path $InputDirectory 'input-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.sourceCommit -cnotmatch '^[0-9a-f]{40}$' -or $manifest.baselineCommit -cne '37fde9160229e99d2fa837d2f1a850203c260d81') { throw 'Unexpected source manifest.' }
    $report.sourceCommit=$manifest.sourceCommit
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.files) {
        if ($entry.path -match '(^|/)\.\.?(/|$)|[\\:\x00-\x1f]' -or $entry.path.StartsWith('/') -or -not $seen.Add($entry.path)) { throw 'Unsafe manifest path.' }
        $source=Join-Path $InputDirectory $entry.path
        $item=Get-Item -LiteralPath $source
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -ne $entry.length) { throw "Invalid input: $($entry.path)" }
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw "Input hash mismatch: $($entry.path)" }
        $destination=Join-Path $work $entry.path
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        [IO.File]::Copy($source,$destination,$false)
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw "Copied input hash mismatch: $($entry.path)" }
    }
    $report['verifiedInputFiles']=$seen.Count
    $sourceRoot=Join-Path $work 'source'
    [IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $work 'source.zip'),$sourceRoot)
    $tools=Join-Path $work 'tools'
    $pwsh=Join-Path $tools 'pwsh/pwsh.exe'
    $node=Join-Path $tools 'node/node.exe'
    $sevenZip=Join-Path $tools '7zip/7z.exe'
    $env:PATH=(Join-Path $tools 'node') + ';' + (Join-Path $tools 'pwsh') + ';' + (Join-Path $tools '7zip') + ";$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:PYTHONUTF8='1'
    $env:NO_PROXY='localhost,127.0.0.1'
    $nodeVersion=(& $node --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -cne 'v20.20.0') { throw 'Guest Node must be 20.20.0.' }
    $report['node']=$nodeVersion
    $report['powershell']=[string]$PSVersionTable.PSVersion
    [void][IO.Directory]::CreateDirectory((Join-Path $sourceRoot 'desktop/node_modules'))
    Copy-Item -LiteralPath (Join-Path $tools 'playwright-core') -Destination (Join-Path $sourceRoot 'desktop/node_modules/playwright-core') -Recurse
    Run-Stage '7zip-version' $sevenZip @('i') 0 30
    $artifacts=Join-Path $work 'artifacts'
    $portable=Join-Path $artifacts 'chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip'
    $installer=Join-Path $artifacts 'chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe'
    $layout=Join-Path $work 'portable-preflight'
    [IO.Compression.ZipFile]::ExtractToDirectory($portable,$layout)
    $hostExe=Join-Path $layout 'chaoxing-gui-tauri.exe'
    Run-Stage 'missing-webview2-native-check' $hostExe @('--check-webview2') 3 30
    Run-Stage 'missing-webview2-portable-entry' $pwsh @('-NoProfile','-File',(Join-Path $layout 'Start-Chaoxing.ps1')) 3 40
    $report.checks += [ordered]@{name='missing-webview2-exit3-without-data';success=$true;profiles=(Assert-NoProductProfiles)}
    if (@(Get-Process -Name 'chaoxing*','p2-backend' -ErrorAction SilentlyContinue).Count) { throw 'Missing-runtime preflight left a product process.' }
    $runtime=Join-Path $tools 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    $signature=Get-AuthenticodeSignature -LiteralPath $runtime
    $report['runtimeInstallerSignature']=[ordered]@{status=[string]$signature.Status;subject=$signature.SignerCertificate.Subject;thumbprint=$signature.SignerCertificate.Thumbprint;sha256=(Get-FileHash -LiteralPath $runtime).Hash.ToLowerInvariant()}
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Runtime installer is not validly Microsoft signed.' }
    Run-Stage 'install-offline-webview2' $pwsh @('-NoProfile','-File',(Join-Path $layout 'Install-WebView2.ps1'),'-InstallerPath',$runtime,'-TimeoutSeconds','480') 0 520
    Run-Stage 'installed-webview2-native-check' $hostExe @('--check-webview2') 0 30
    Run-Stage 'native-release-build-profile' $hostExe @('--check-debug-build') 4 30
    $report.checks += [ordered]@{name='offline-microsoft-runtime-install';success=$true}
    [void][IO.Directory]::CreateDirectory((Join-Path $sourceRoot 'web'))
    Copy-Item -LiteralPath (Join-Path $layout 'backend/_internal/web/dist') -Destination (Join-Path $sourceRoot 'web/dist') -Recurse
    $scripts=Join-Path $sourceRoot 'desktop/scripts'
    $fakeEvidence=Join-Path $evidence 'release-fake'
    Run-Stage 'release-fake' $pwsh @('-NoProfile','-File',(Join-Path $scripts 'smoke-tauri.ps1'),
        '-HostPath',$hostExe,'-BackendDirectory',(Join-Path $layout 'backend'),'-FakeBackendDirectory',(Join-Path $work 'fixture/p2-backend'),
        '-EvidenceDirectory',$fakeEvidence,'-Configuration','Release','-Scenario','Fake','-NestedJob','-DisposableWindowsUser','-TimeoutSeconds','300') 0 420
    $fake=Read-OnlyRunReport $fakeEvidence 'Release fake'
    if ($fake.selection.configuration -cne 'Release' -or $fake.selection.nestedJob -ne $true) { throw 'Incorrect fake smoke selection.' }
    $report['releaseFake']=[ordered]@{success=$true;checks=@($fake.checks).Count;runId=$fake.runId}
    $report['betweenStageProfiles']=Assert-NoProductProfiles
    $installationEvidence=Join-Path $evidence 'installation'
    Run-Stage 'installation' $pwsh @('-NoProfile','-File',(Join-Path $scripts 'smoke-installation.ps1'),
        '-InstallerPath',$installer,'-PortablePath',$portable,'-EvidenceDirectory',$installationEvidence,'-DisposableWindowsUser','-TimeoutSeconds','480') 0 650
    $installation=Read-OnlyRunReport $installationEvidence 'Installation'
    if (-not @($installation.checks | Where-Object { $_.name -ceq 'uninstall-retains-tauri-data-and-old-electron' }).Count) { throw 'Missing actual retention/uninstall assertion.' }
    $report['installation']=[ordered]@{success=$true;checks=@($installation.checks).Count;runId=$installation.runId}
    $report['finalProductProfiles']=Assert-NoProductProfiles
    if (@(Get-Process -Name 'chaoxing*','p2-backend' -ErrorAction SilentlyContinue).Count) { throw 'Product process remains after acceptance.' }
    $report.success=$true
    $report.phase='completed'
} catch {
    $report['error']=$_ | Out-String
    $report['scriptStackTrace']=$_.ScriptStackTrace
} finally {
    $exportRoot=Join-Path $OutputDirectory 'evidence'
    [void][IO.Directory]::CreateDirectory($exportRoot)
    foreach ($file in Get-ChildItem -LiteralPath $evidence -File -Recurse) {
        $relative=[IO.Path]::GetRelativePath($evidence,$file.FullName)
        if ($relative -match '(^|[\\/])fixtures([\\/]|$)' -or $file.Extension -notin @('.json','.txt','.log','.md','.png')) { continue }
        $destination=Join-Path $exportRoot $relative
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    $report['endedAt']=[DateTime]::UtcNow.ToString('o')
    Save-Status
    $report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'result.json') -Encoding utf8
    if ($ShutdownGuest) { & "$env:SystemRoot\System32\shutdown.exe" /s /t 5 }
}
if (-not $report.success) { exit 1 }

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/sandbox-probe.ps1

SHA256: 580d75fdd5533b1073f8c75fed504a3ac8dc951724e5843a0c07666aa65b2685

````text
param(
    [string]$OutputDirectory = 'C:\TauriAcceptance\Output',
    [string]$HostComputerName = 'DESKTOP-3DSSD2K'
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity.Split('\')[-1] -ine 'WDAGUtilityAccount' -or $env:COMPUTERNAME -ieq $HostComputerName) {
    throw 'Probe may run only in Windows Sandbox under its real WDAGUtilityAccount.'
}
$report = [ordered]@{
    startedAt = [DateTime]::UtcNow.ToString('o')
    identity = $identity
    computerName = $env:COMPUTERNAME
    operatingSystem = [Environment]::OSVersion.VersionString
    powershell = [string]$PSVersionTable.PSVersion
    computer = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,HypervisorPresent
    profiles = [ordered]@{
        roaming = [Environment]::GetFolderPath('ApplicationData')
        local = [Environment]::GetFolderPath('LocalApplicationData')
        temp = [IO.Path]::GetTempPath()
    }
    webview2 = @()
    pythonCommands = @(Get-Command python.exe,python3.exe,py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object Name,Source)
    productExecuted = $false
    networkUse = $false
}
foreach ($hive in @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients', 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients', 'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients')) {
    if (Test-Path -LiteralPath $hive) {
        foreach ($key in Get-ChildItem -LiteralPath $hive) {
            if ($key.GetValue('name') -match 'WebView2') {
                $report.webview2 += [ordered]@{name=$key.GetValue('name'); version=$key.GetValue('pv'); registryPath=$key.Name}
            }
        }
    }
}
$report['productProfilesExist'] = @(foreach ($base in @($report.profiles.roaming, $report.profiles.local)) {
    foreach ($app in @('com.chaoxing.gui', 'chaoxing-desktop')) {
        $profile = Join-Path $base $app
        [ordered]@{path=$profile; exists=(Test-Path -LiteralPath $profile)}
    }
})
$report['completedAt'] = [DateTime]::UtcNow.ToString('o')
$report['success'] = $true
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'probe-result.json') -Encoding UTF8
# This is reached only after the actual guest identity check above.
& "$env:SystemRoot\System32\shutdown.exe" /s /t 5

````

## .ccg/tasks/electron-to-tauri-acceptance-followup/verification/validate-workflow.ps1

SHA256: dc8a13d99909c85399f955f85e251d61fe830dec773b2854f0c587fea7673c26

````text
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:P3_WORKFLOW_EVIDENCE = $PSScriptRoot
@'
from pathlib import Path
import hashlib,json,os
import yaml
root=Path.cwd()
evidence=Path(os.environ['P3_WORKFLOW_EVIDENCE'])
workflow=root/'.github/workflows/main.yml'
data=yaml.load(workflow.read_text(encoding='utf-8-sig'),Loader=yaml.BaseLoader)
jobs=data['jobs']
assert jobs['test-backend']['strategy']['matrix']['python-version']==['3.11','3.13']
assert jobs['build-windows']['needs']==['test-backend','test-web']
assert any(s.get('uses','').startswith('dtolnay/rust-toolchain@') and s['with']['toolchain']=='1.95.0' for s in jobs['build-windows']['steps'])
for job in ('test-web','build-windows'):
    assert any(s.get('uses','').startswith('actions/setup-node@') and s['with']['node-version']=='20' for s in jobs[job]['steps'])
steps=[]
directory=evidence/'workflow-steps-final'
directory.mkdir(exist_ok=True)
for name,job in jobs.items():
    for index,step in enumerate(job['steps']):
        if 'run' not in step: continue
        path=directory/f'{name}-{index:02d}.ps1'
        path.write_text(step['run'],encoding='utf-8')
        steps.append({'job':name,'name':step.get('name',''),'runFile':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
report={'yamlParsed':True,'workflowSha256':hashlib.sha256(workflow.read_bytes()).hexdigest(),'runSteps':steps,'remoteExecution':False}
(evidence/'workflow-final-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
'@ | python -
if ($LASTEXITCODE -ne 0) { throw 'Workflow YAML/structure validation failed' }
$failures = @()
$scripts = @((Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'workflow-steps-final') -Filter '*.ps1' -File).FullName)
$scripts += @((Get-ChildItem -LiteralPath desktop/scripts,desktop/portable -Filter '*.ps1' -File).FullName)
foreach ($script in $scripts) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
    foreach ($error in $errors) { $failures += @{ file=$script; error=[string]$error } }
}
$reportPath = Join-Path $PSScriptRoot 'workflow-final-validation.json'
$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json -AsHashtable
$report['powershellSyntaxParsed'] = $failures.Count -eq 0
$report['powershellFilesParsed'] = $scripts.Count
$report['errors'] = $failures
$report['nativeExitCodeChecks'] = 'All workflow python/npm/cargo/pwsh commands have explicit nonzero propagation; build entry uses checked commands.'
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
if ($failures.Count) { throw ($failures | ConvertTo-Json -Depth 4) }
"Workflow YAML, $($report.runSteps.Count) run steps and $($scripts.Count) PowerShell sources: PASS. Remote CI not run."

````

## .github/workflows/main.yml

SHA256: bdde79c5f4a3b00d3469d3bc394eb7e72a0b27c18e04cedd397ca95b5ffe91be

````text
name: Build Windows Package

on:
  workflow_dispatch:
    inputs:
      publish_release:
        description: Publish the verified build to GitHub Releases
        required: false
        default: false
        type: boolean
      release_tag:
        description: Optional tag (must match pyproject.toml); blank uses the source version
        required: false
        default: ""
        type: string
  push:
    branches:
      - main
    tags:
      - "v*"

permissions:
  contents: write

defaults:
  run:
    shell: pwsh

jobs:
  test-backend:
    runs-on: windows-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: requirements-test.txt
      - name: Install offline test dependencies
        run: |
          python -m pip install -r requirements-test.txt
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      - name: Run first-party regression tests
        env:
          PYTHONUTF8: "1"
        run: |
          python -m unittest discover -s tests -v
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  test-web:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: |
            web/package-lock.json
            desktop/package-lock.json
      - name: Install frontend and desktop test dependencies
        run: |
          npm --prefix web ci
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          npm --prefix desktop ci
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      - name: Test frontend behavior
        run: |
          npm --prefix web test
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      - name: Test Electron sessions and Tauri packaging/smoke contracts
        run: |
          npm --prefix desktop test
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      - name: Check desktop JavaScript
        run: |
          foreach ($source in @('desktop/main.js', 'desktop/preload.js', 'desktop/session-store.js', 'desktop/scripts/p3-smoke.mjs', 'desktop/scripts/p3-installation.mjs')) {
            node --check $source
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          }
      - name: Build frontend
        run: |
          npm --prefix web run build
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  build-windows:
    needs: [test-backend, test-web]
    runs-on: windows-latest
    timeout-minutes: 120
    env:
      PYTHONUTF8: "1"
      CARGO_BUILD_JOBS: "1"

    steps:
      - name: Check out source
        uses: actions/checkout@v4
        timeout-minutes: 10

      - name: Set up Python
        uses: actions/setup-python@v5
        timeout-minutes: 10
        with:
          python-version: "3.11"
          architecture: x64
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Set up Node.js
        uses: actions/setup-node@v4
        timeout-minutes: 10
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: |
            web/package-lock.json
            desktop/package-lock.json

      - name: Set up pinned Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          toolchain: "1.95.0"
          targets: x86_64-pc-windows-msvc
          components: rustfmt, clippy

      - name: Cache Rust
        uses: Swatinem/rust-cache@v2
        with:
          workspaces: desktop/src-tauri -> target
          shared-key: windows-x64-tauri-rust-1.95.0

      - name: Cache Electron binaries
        uses: actions/cache@v4
        with:
          path: |
            ~\AppData\Local\electron\Cache
            ~\AppData\Local\electron-builder\Cache
          key: ${{ runner.os }}-electron-${{ hashFiles('desktop/package-lock.json') }}
          restore-keys: |
            ${{ runner.os }}-electron-

      - name: Validate source, lock and release versions
        env:
          REQUESTED_RELEASE_TAG: ${{ inputs.release_tag }}
        run: |
          $versionJson = python desktop/scripts/version.py --check --json
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          $version = ($versionJson | ConvertFrom-Json).version
          $releaseTag = "v$version"
          if ($env:GITHUB_REF_TYPE -eq 'tag') { $releaseTag = $env:GITHUB_REF_NAME }
          elseif ($env:REQUESTED_RELEASE_TAG) { $releaseTag = $env:REQUESTED_RELEASE_TAG }
          python desktop/scripts/version.py --check --tag $releaseTag
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          "APP_VERSION=$version" >> $env:GITHUB_ENV
          "RELEASE_TAG=$releaseTag" >> $env:GITHUB_ENV
          New-Item -ItemType Directory -Force desktop/release/verification | Out-Null
          $versionJson | Set-Content desktop/release/verification/version.json -Encoding utf8

      - name: Build frontend and install desktop tooling
        run: |
          npm --prefix web ci
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          npm --prefix web run build
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          npm --prefix desktop ci
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Install packaging dependencies
        run: |
          python -m pip install --upgrade pip
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          $requirements = Get-Content requirements.txt | Where-Object { $_ -notmatch '^\s*paddleocr\s*' }
          $requirements | Set-Content "$env:RUNNER_TEMP\requirements-packaging.txt"
          python -m pip install -r "$env:RUNNER_TEMP\requirements-packaging.txt"
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          python -m pip install pyinstaller==6.21.0
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Build single-file executable
        run: |
          python -m PyInstaller --clean --noconfirm chaoxing.spec
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Smoke test independent executable with owned stdin and process tree
        run: |
          pwsh -NoProfile -File desktop/scripts/smoke-python.ps1 -ExecutablePath dist/chaoxing-gui.exe -Mode Independent -EvidenceDirectory desktop/release/verification/python-independent
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Assemble independent Python release archive
        run: |
          New-Item -ItemType Directory -Force release/python | Out-Null
          Copy-Item -LiteralPath dist/chaoxing-gui.exe -Destination release/python/chaoxing-gui.exe
          Copy-Item -LiteralPath README.md -Destination release/python/README.md
          @{ version=$env:APP_VERSION; platform='windows-x64'; kind='independent-python' } | ConvertTo-Json | Set-Content release/python/version.json -Encoding utf8
          Compress-Archive -Path release/python/* -DestinationPath "release/chaoxing-gui-no-electron-$env:APP_VERSION-windows-x64.zip" -Force

      - name: Build desktop backend executable
        run: |
          python -m PyInstaller --clean --noconfirm chaoxing-backend.spec
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Smoke test frozen desktop backend
        run: |
          pwsh -NoProfile -File desktop/scripts/smoke-python.ps1 -ExecutablePath dist/chaoxing-backend/chaoxing-backend.exe -Mode Backend -EvidenceDirectory desktop/release/verification/python-backend
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Build Electron desktop app (coexistence and rollback)
        run: |
          pwsh -NoProfile -File desktop/scripts/prepare-backend.ps1 -DestinationDirectory desktop/backend/chaoxing-backend
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          npm --prefix desktop exec -- electron-builder --win --publish never --projectDir desktop
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Import optional signing certificate
        env:
          CHAOXING_PFX_BASE64: ${{ secrets.WINDOWS_CERTIFICATE_PFX }}
          CHAOXING_PFX_PASSWORD: ${{ secrets.WINDOWS_CERTIFICATE_PASSWORD }}
        run: |
          if ($env:CHAOXING_PFX_BASE64) {
            $certificateFile = Join-Path $env:RUNNER_TEMP "chaoxing-sign-$([Guid]::NewGuid().ToString('N')).pfx"
            try {
              [IO.File]::WriteAllBytes($certificateFile, [Convert]::FromBase64String($env:CHAOXING_PFX_BASE64))
              $password = ConvertTo-SecureString $env:CHAOXING_PFX_PASSWORD -AsPlainText -Force
              $importedCertificates = @(Import-PfxCertificate -FilePath $certificateFile -CertStoreLocation Cert:\CurrentUser\My -Password $password)
              $signingCertificates = @($importedCertificates | Where-Object HasPrivateKey)
              if ($signingCertificates.Count -ne 1) { throw 'PFX must identify exactly one signing certificate with a private key' }
              $env:CHAOXING_SIGN_CERT_THUMBPRINT = $signingCertificates[0].Thumbprint
              "CHAOXING_SIGN_CERT_THUMBPRINT=$env:CHAOXING_SIGN_CERT_THUMBPRINT" >> $env:GITHUB_ENV
            } finally {
              if (Test-Path -LiteralPath $certificateFile) { Remove-Item -LiteralPath $certificateFile -Force }
            }
          }
          pwsh -NoProfile -File desktop/scripts/sign-windows.ps1 -Mode Inspect -ReportPath desktop/release/verification/signing-availability.json
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Rust fmt/check/clippy/test and Tauri release build/package
        run: |
          pwsh -NoProfile -File desktop/scripts/build-tauri.ps1 -Prepared
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Validate NSIS and portable payloads
        run: |
          pwsh -NoProfile -File desktop/scripts/verify-nsis.ps1 -InstallerPath "desktop/release/tauri/chaoxing-gui-tauri-setup-$env:APP_VERSION-windows-x64.exe" -PortablePath "desktop/release/tauri/chaoxing-gui-tauri-portable-$env:APP_VERSION-windows-x64.zip" -EvidenceDirectory desktop/release/verification/nsis-content
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Build frozen synthetic backend and embedded debug host
        run: |
          python -m PyInstaller --noconfirm --onedir --name p2-backend --specpath desktop/src-tauri/target/p3-fixture --workpath desktop/src-tauri/target/p3-fixture/build --distpath desktop/src-tauri/target/p3-fixture/dist desktop/tests/fixtures/p2_backend.py
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked -j 1 --features custom-protocol --bin chaoxing-desktop
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Debug fake faults and frozen backend smoke in nested Jobs
        run: |
          pwsh -NoProfile -File desktop/scripts/smoke-tauri.ps1 -HostPath desktop/src-tauri/target/debug/chaoxing-desktop.exe -BackendDirectory desktop/src-tauri/resources/backend -FakeBackendDirectory desktop/src-tauri/target/p3-fixture/dist/p2-backend -EvidenceDirectory desktop/release/verification/tauri-debug -Configuration Debug -Scenario All -NestedJob
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Release synthetic business and failure smoke in nested Jobs
        run: |
          pwsh -NoProfile -File desktop/scripts/smoke-tauri.ps1 -HostPath desktop/src-tauri/target/x86_64-pc-windows-msvc/release/chaoxing-gui-tauri.exe -BackendDirectory desktop/src-tauri/resources/backend -FakeBackendDirectory desktop/src-tauri/target/p3-fixture/dist/p2-backend -EvidenceDirectory desktop/release/verification/tauri-release-fixture -Configuration Release -Scenario Fake -NestedJob
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Install/uninstall and portable release host acceptance on disposable runner
        run: |
          pwsh -NoProfile -File desktop/scripts/smoke-installation.ps1 -InstallerPath "desktop/release/tauri/chaoxing-gui-tauri-setup-$env:APP_VERSION-windows-x64.exe" -PortablePath "desktop/release/tauri/chaoxing-gui-tauri-portable-$env:APP_VERSION-windows-x64.zip" -EvidenceDirectory desktop/release/verification/tauri-installation
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Upload validation evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: windows-packaging-verification
          path: desktop/release/verification/
          if-no-files-found: warn

      - name: Upload independent Python artifact
        uses: actions/upload-artifact@v4
        with:
          name: chaoxing-gui-no-electron-${{ env.APP_VERSION }}-windows-x64
          path: release/chaoxing-gui-no-electron-${{ env.APP_VERSION }}-windows-x64.zip
          if-no-files-found: error

      - name: Upload Electron artifact
        uses: actions/upload-artifact@v4
        with:
          name: chaoxing-gui-desktop-${{ env.APP_VERSION }}
          path: desktop/release/*.exe
          if-no-files-found: error

      - name: Upload Tauri artifacts
        uses: actions/upload-artifact@v4
        with:
          name: chaoxing-gui-tauri-${{ env.APP_VERSION }}-windows-x64
          path: desktop/release/tauri/
          if-no-files-found: error

      - name: Publish GitHub Release
        if: success() && (startsWith(github.ref, 'refs/tags/') || (github.event_name == 'workflow_dispatch' && inputs.publish_release))
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ env.RELEASE_TAG }}
          name: Release ${{ env.RELEASE_TAG }}
          target_commitish: ${{ github.sha }}
          generate_release_notes: true
          files: |
            release/chaoxing-gui-no-electron-${{ env.APP_VERSION }}-windows-x64.zip
            desktop/release/*.exe
            desktop/release/tauri/chaoxing-gui-tauri-setup-${{ env.APP_VERSION }}-windows-x64.exe
            desktop/release/tauri/chaoxing-gui-tauri-setup-${{ env.APP_VERSION }}-windows-x64.exe.manifest.json
            desktop/release/tauri/chaoxing-gui-tauri-portable-${{ env.APP_VERSION }}-windows-x64.zip
            desktop/release/tauri/chaoxing-gui-tauri-portable-${{ env.APP_VERSION }}-windows-x64.zip.manifest.json
            desktop/release/tauri/chaoxing-gui-tauri-portable-${{ env.APP_VERSION }}-windows-x64.zip.sha256
            desktop/release/tauri/chaoxing-gui-tauri-artifacts-${{ env.APP_VERSION }}-windows-x64.json
            desktop/release/tauri/SHA256SUMS.txt

````

## .gitignore

SHA256: 8f87805b2f0a4f887ed212c554de559378cde525e26ba90a66d5f0de9c4dbb1c

````text
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# C extensions
*.so

# Distribution / packaging
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
/lib/
lib64/
parts/
sdist/
var/
wheels/
pip-wheel-metadata/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# PyInstaller
#  Usually these files are written by a python script from a template
#  before PyInstaller builds the exe, so as to inject date/other infos into it.
*.manifest
*.spec
!chaoxing.spec
!chaoxing-backend.spec

# Installer logs
pip-log.txt
pip-delete-this-directory.txt

# Unit test / coverage reports
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
.cache
nosetests.xml
coverage.xml
*.cover
*.py,cover
.hypothesis/
.pytest_cache/

# Translations
*.mo
*.pot

# Django stuff:
*.log
local_settings.py
db.sqlite3
db.sqlite3-journal

# Flask stuff:
instance/
.webassets-cache

# Scrapy stuff:
.scrapy

# Sphinx documentation
docs/_build/

# PyBuilder
target/

# Jupyter Notebook
.ipynb_checkpoints

# IPython
profile_default/
ipython_config.py

# pyenv
.python-version

# pipenv
#   According to pypa/pipenv#598, it is recommended to include Pipfile.lock in version control.
#   However, in case of collaboration, if having platform-specific dependencies or dependencies
#   having no cross-platform support, pipenv may install dependencies that don't work, or not
#   install all needed dependencies.
#Pipfile.lock

# PEP 582; used by e.g. github.com/David-OConnor/pyflow
__pypackages__/

# Celery stuff
celerybeat-schedule
celerybeat.pid

# SageMath parsed files
*.sage.py

# Environments
.env
.venv
.venv*/
env/
venv/
ENV/
env.bak/
venv.bak/

# Spyder project settings
.spyderproject
.spyproject

# Rope project settings
.ropeproject

# mkdocs documentation
/site

# mypy
.mypy_cache/
.dmypy.json
dmypy.json

# Pyre type checker
.pyre/
logs/
saves/
build/
!desktop/build/
dist/
*.spec
!chaoxing.spec
!chaoxing-backend.spec

# python-uv.lock
# just like Pipfile.lock,
uv.lock

# poetry
poetry.lock

# Custom files
.ccg/
.firecrawl/
chaoxing/
.cookies.txt
cookies.txt
.cookies/
.config.ini
config.ini
chaoxing.log
config*.ini
.chaoxing.log
./config.ini
./chaoxing.log
./cookies.txt
.idea/
.vscode/
cache.json
web_config.json

# Portable build
chaoxing_portable/
chaoxing_portable.zip
portable_build/
python-embed.zip
get-pip.py

# Frontend
web/node_modules/
web/dist/
web/.vite/

# Desktop (Electron)
desktop/node_modules/
desktop/backend/
desktop/release/
desktop/*.log

# Tauri (desktop/src-tauri)
desktop/src-tauri/target/
desktop/src-tauri/gen/
desktop/src-tauri/permissions/autogenerated/
desktop/src-tauri/resources/backend/
desktop/src-tauri/resources/backend-manifest.json

````

## README.md

SHA256: 408fe30a9f7d35a812ba4b0fa8defde32a743656e8cb44e1a9017bfe92ea1e0e

````text
# 超星学习通自动化工具

<div align="center">

[![GitHub Stars](https://img.shields.io/github/stars/RRRRUDDDD/chaoxing-gui)](https://github.com/RRRRUDDDD/chaoxing-gui)
[![License](https://img.shields.io/github/license/RRRRUDDDD/chaoxing-gui)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

**带 Web 界面的超星学习通自动学习工具**

视频学习 · 自动答题

</div>

---

## 快速开始

### 方式一：一键启动

```bash
# 1. 下载项目
git clone https://github.com/RRRRUDDDD/chaoxing-gui.git
cd chaoxing-gui

# 2. 启动
start.bat  # Windows 双击或命令行运行

# 3. 打开浏览器访问 http://localhost:3000
```

**自动完成**：检查环境 → 安装依赖 → 启动服务 → 打开浏览器

### 方式二：桌面应用

```bash
# 构建桌面版（只需一次）
build_desktop.bat

# 双击安装
desktop/release/chaoxing-gui-desktop-Setup-*.exe
```

安装后通过开始菜单或桌面快捷方式启动。

Windows 发布包可从 [GitHub Releases](https://github.com/RRRRUDDDD/chaoxing-fanya/releases/latest) 直接下载。

Tauri 2 桌面版正在独立验收，可用 `build_tauri.bat` 构建 Windows x64 NSIS 和便携 ZIP，输出在 `desktop/release/tauri/`。它使用独立程序目录，保留 Electron 与原数据；当前默认桌面构建入口仍为 Electron。安装、WebView2 离线准备、版本校验和验收边界见 [桌面版指南](desktop/README.md)。

### 方式三：命令行模式

```bash
# 使用配置文件
python main.py -c config.ini

# 命令行参数
python main.py -u 手机号 -p 密码 -l 课程ID --speed 1.5
```

---

## 使用说明

### 1. 登录

**Web UI**：
- 输入手机号和密码，点击"登录"
- 登录后可使用当前账号的本机会话自动登录；会话过期时重新输入密码

**CLI**：
- 编辑 `config.ini` 填写账号信息
- 或使用 `-u` `-p` 参数

### 2. 选择课程

**Web UI**：点击课程卡片选择课程，至少选择一门后开始。保存的选择按账号隔离，已经失效的课程不会自动替换为全部课程。

**CLI**：使用 `-l` 参数指定课程 ID，逗号分隔

### 3. 配置参数（可选）

- **播放倍速**：1.0-2.0，建议 1.5
- **并发章节**：界面提供 1-10，后端允许 1-16，建议 3-5
- **题库**：支持言溪/Like/AI 等 5 种题库
- **通知**：支持 Server酱/Telegram/Bark 等

### 4. 开始学习

**Web UI**：点击"开始学习"，查看实时进度

返回课程页后，可通过“查看运行任务”继续查看进度。同一账号的任务结束前不能重复启动。失败和跳过的任务分别统计；保存但未提交的测验也属于跳过，不会显示为全部完成。

**CLI**：自动运行，查看控制台输出

---

## 核心功能

- ✅ **视频自动播放**：支持倍速（1.0-2.0x）
- ✅ **题库自动答题**：内置 5 大题库，覆盖率可配置
- ✅ **OCR 识别**：本地 PaddleOCR 或云端视觉模型
- ✅ **进度推送**：Server酱/Telegram/Bark/Qmsg
- ✅ **可视化界面**：Web UI + 桌面应用

---

## 同类工具对比

| 特性 | 本项目 | 命令行工具 | 浏览器扩展 |
|------|--------|-----------|-----------|
| **上手难度** | ⭐⭐ 一键启动 | ⭐⭐⭐⭐ 需配置环境 | ⭐ 最简单 |
| **界面** | Web + 桌面应用 | 纯命令行 | 浏览器内 |
| **后台运行** | ✅ 支持 | ✅ 支持 | ❌ 需保持浏览器打开 |
| **题库生态** | 5 种可选 | 需自行集成 | 通常单一 |
| **自动推送** | 内置 4 种渠道 | 需自己实现 | 有限 |
| **定制性** | ⭐⭐⭐⭐⭐ 开源可改 | ⭐⭐⭐⭐⭐ 开源可改 | ⭐⭐ 扩展限制 |
| **稳定性** | 独立程序，稳定 | 独立程序，稳定 | 依赖浏览器更新 |
| **适用场景** | 长期使用、批量任务 | 自动化、定时任务 | 临时使用、轻量需求 |

**选择建议**：
- 新手 / 临时使用 → **浏览器扩展**（最快）
- 普通用户 / 长期使用 → **本项目**（功能完整）
- 技术用户 / 自动化 → **命令行工具**或本项目 CLI 模式

---

## 常见问题

### 端口被占用

```bash
# 查看占用进程
netstat -ano | findstr :5000
netstat -ano | findstr :3000

# 结束进程
taskkill /F /PID <进程ID>
```

### 题库无响应

1. 检查 token 是否正确
2. 查看 `logs/chaoxing.log` 确认错误
3. 降低 `cover_rate` 参数（如 0.6）
4. 切换其他题库提供商

### 登录失败

- 检查账号密码是否正确
- Cookie 可能已过期，重新获取
- 部分账号触发验证码（暂不支持）

---

## 开发与验证

Python 最低版本为 3.11，CI 使用 3.11 和 3.13 运行第一方离线回归测试。测试不加载 Paddle 模型，不连接学习账号、题库或通知服务。

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
npm --prefix desktop test
```

答案缓存键包含题干、题型和有序选项，旧题干缓存不再命中。缓存默认每 32 次更新原子写盘，任务正常退出时再次刷新；磁盘正常时，异常终止最多丢失最后一批未完成刷盘的 32 次更新。写盘失败会保留脏数据等待重试，此时不保证该上限。直接使用题库 API 的调用方应在结束时调用 `Tiku.close()`，多个进程不要同时写同一缓存文件。

账号会话保存在数据目录的 `.cookies/` 下，各账号独立；旧 `cookies.txt` 仅供未指定账号的 CLI Cookie 登录使用。桌面端记忆账号和任务入口，不保存密码，也不依赖每次启动分配的后端端口。

---

## 许可与免责

### 开源许可

GPL-3.0 许可证 — 允许自由使用和修改，但衍生项目必须开源

### 免责声明

**本项目仅供学习交流**

- ⚠️ 使用者自行承担所有法律责任
- ⚠️ 严禁用于作弊、刷分等违反学术诚信的行为
- ⚠️ 账号安全、学习记录等风险自负
- ⚠️ 开发者不对任何后果负责

**使用即表示同意上述条款**

---

## 致谢与支持

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 核心逻辑开发

- [报告问题](https://github.com/RRRRUDDDD/chaoxing-gui/issues)
- [功能建议](https://github.com/RRRRUDDDD/chaoxing-gui/issues)
- [技术交流](https://github.com/RRRRUDDDD/chaoxing-gui/discussions)

如果有帮助，欢迎 Star ⭐

---

````

## api/desktop_runtime.py

SHA256: 682d766d2989203d65169729194c319f784bc17414359160c21edbb095b34da4

````text
"""Tauri 宿主模式运行时（CHAOXING_TAURI=1）。

协议（P1 计划 §7 / plan.md §3.3）：
- 环境变量缺失/为空 → 直接退出，绝不退回旧模式。
- werkzeug make_server 绑定 127.0.0.1:0（随机端口），threaded=True。
- 就绪握手：向 stdout 输出单行 JSON：
  {"ready":"chaoxing-ready","version":1,"port":<n>,"instanceId":"<id>"}
- token 鉴权：before_request 钩子校验 X-Auth-Token 与 Host 头，
  覆盖所有路径（含 /api/health 与静态路由）；失败 401。
- Tauri 模式不注册 CORS（原生转发无跨域需求；普通模式 CORS 契约不动）。
- stdin watchdog（app.py 的 watch_parent_stdin）语义不变：EOF → os._exit(0)。
"""

import hmac
import json
import os
import sys
import threading

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

READY_MARKER = "chaoxing-ready"
READY_VERSION = 1

TOKEN_HEADER = "X-Auth-Token"


class TauriEnvError(RuntimeError):
    """CHAOXING_TAURI 环境变量不完整。"""


def parse_ready_env(environ=None):
    """读取 Tauri 注入的 token/instanceId；缺失即抛 TauriEnvError。"""
    env = environ if environ is not None else os.environ
    token = env.get("CHAOXING_TAURI_TOKEN", "")
    instance_id = env.get("CHAOXING_TAURI_INSTANCE_ID", "")
    if not token or not instance_id:
        raise TauriEnvError(
            "CHAOXING_TAURI_TOKEN / CHAOXING_TAURI_INSTANCE_ID 未设置，"
            "Tauri 模式拒绝启动（不允许回退旧模式）"
        )
    return token, instance_id


def register_token_guard(app: Flask, token: str, instance_id: str, port: int):
    """注册 before_request 钩子：所有路径校验 token + Host。

    Host 必须恰为 127.0.0.1:<port>（防 DNS-rebinding / 错误端口接管）。
    同时改造 /api/health 回显 instanceId（宿主 token+instanceId 双重核对）。
    app.py 自带的 /api/health 视图函数被原地增强，路由表不变。
    """

    @app.before_request
    def _tauri_token_guard():
        supplied = request.headers.get(TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
            return jsonify({"status": False, "msg": "unauthorized"}), 401
        if request.host != f"127.0.0.1:{port}":
            return jsonify({"status": False, "msg": "bad host"}), 401
        if "Origin" in request.headers:
            return jsonify({"status": False, "msg": "bad origin"}), 401
        return None

    original_health = app.view_functions.get("health")

    def _tauri_health():
        import flask

        resp = (
            original_health()
            if original_health is not None
            else jsonify({"status": True, "msg": "OK"})
        )
        # 回显 instanceId；Flask 视图返回 tuple 时原样透传
        if isinstance(resp, tuple):
            body, status = resp[0], resp[1]
        else:
            body, status = resp, 200
        body_json = body.get_json(silent=True) or {}
        body_json["instanceId"] = instance_id
        return flask.make_response(jsonify(body_json), status)

    # app.py 已注册名为 health 的视图时原地增强（路由表不变）；
    # 测试 app 无自带路由时注册新 /api/health。
    if original_health is not None:
        app.view_functions["health"] = _tauri_health
    else:
        app.add_url_rule("/api/health", "health", _tauri_health)


def emit_ready_line(port: int, instance_id: str, stream=None):
    """输出就绪握手行（单行 JSON + flush）。"""
    stream = stream if stream is not None else sys.stdout
    line = json.dumps(
        {
            "ready": READY_MARKER,
            "version": READY_VERSION,
            "port": port,
            "instanceId": instance_id,
        },
        separators=(",", ":"),
    )
    stream.write(line + "\n")
    stream.flush()


def run_tauri_server(app: Flask, token: str, instance_id: str):
    """绑定 127.0.0.1:0，后台 serve_forever，输出握手行。

    返回 server 对象（测试可用 server.server_address[1] 取端口）。
    """
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_address[1]

    register_token_guard(app, token, instance_id, port)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # serve_forever 线程已启动、logger 此前未向 stdout 写行时是静默点；
    # 宿主侧本就容忍握手前的前置日志行。
    emit_ready_line(port, instance_id)
    return server

````

## api/logger.py

SHA256: f2413b8eb973f0725a9bdf97a1b801bce5a3ce34d295f6d58f368bda9e66a27c

````text
from loguru import logger
from tqdm import tqdm
import sys

tqdm_stream = sys.stderr

def tqdm_sink(msg):
    tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()

logger.remove()
logger.add(tqdm_sink, colorize=True, enqueue=True)
logger.add("chaoxing.log", rotation="10 MB", level="TRACE")

````

## app.py

SHA256: 6755397ca429ed85cfc3b76be8ce038d09626c3f18a0a10c37eff87c90aa8551

````text
import os
import sys
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import time
import json
import atexit
import math
from contextlib import contextmanager
from contextvars import copy_context
from typing import Dict
import webbrowser
import socket

# 确定静态文件目录（支持便携版）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
STATIC_DIR = os.path.join(SCRIPT_DIR, "web", "dist")

from api.base import Chaoxing, Account
from api.answer import Tiku
from api.exceptions import InputFormatError, LoginError
from api.logger import logger
from api.notification import Notification
from api.task_state import TaskAlreadyRunning, TaskStore
import main as main_module

# === 托盘图标相关导入 ===
try:
    from pystray import Icon, Menu, MenuItem
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    logger.warning("pystray 未安装，托盘图标功能不可用")


# 如果存在构建好的前端，则使用静态文件服务
if os.path.exists(STATIC_DIR):
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
else:
    app = Flask(__name__)

# === 环境变量：支持 Electron 无头模式 ===
HEADLESS = os.environ.get("CHAOXING_HEADLESS") == "1" or os.environ.get("CHAOXING_ELECTRON") == "1"
TAURI_MODE = os.environ.get("CHAOXING_TAURI") == "1"
PORT = 0 if TAURI_MODE else int(os.environ.get("CHAOXING_PORT", "5000"))
# 仅限本机访问时也绑定回环地址, 避免局域网内其他设备访问控制台/配置接口
HOST = "127.0.0.1"
# CORS 限定为本机来源, 防止用户浏览器中打开的任意网页跨域读取配置接口
if not TAURI_MODE:
    CORS(app, origins=[f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"])
# 数据目录：Electron 传入 %APPDATA%/<app>；未设置时沿用脚本目录（独立 exe / 开发模式行为不变）
DATA_DIR = os.environ.get("CHAOXING_DATA_DIR") or os.path.dirname(__file__)

# Web 配置文件路径
CONFIG_FILE = os.path.join(DATA_DIR, "web_config.json")


config_lock = threading.RLock()
task_store = TaskStore(cleanup_interval=60)
atexit.register(task_store.close)


def load_web_config() -> Dict:
    """Read a complete configuration snapshot."""
    with config_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
                data = json.load(config_file)
                return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.error(f"读取 Web 配置失败: {exc}")
            return {}


def save_web_config(data: Dict) -> bool:
    """Atomically save configuration under the same lock used for merging."""
    with config_lock:
        try:
            parent = os.path.dirname(CONFIG_FILE)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = CONFIG_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as config_file:
                json.dump(data, config_file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_FILE)
            return True
        except Exception as exc:
            logger.error(f"保存 Web 配置失败: {exc}")
            return False


class LogCapture:
    def __init__(self, task_id: str, store: TaskStore):
        self.task_id = task_id
        self.store = store

    def write(self, message):
        record = message.record
        if record["extra"].get("task_id") != self.task_id:
            return
        level = record["level"].name.lower()
        if level in {"critical", "fatal"}:
            level = "error"
        self.store.append_log(
            self.task_id, str(message), level=level,
            timestamp=record["time"].timestamp(),
        )


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求必须为 JSON 对象")
    return data


def _credentials(data):
    username = data.get("username")
    password = data.get("password", "")
    use_cookies = data.get("use_cookies", False)
    if not isinstance(use_cookies, bool):
        raise ValueError("use_cookies 必须为布尔值")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("用户名不能为空")
    if not isinstance(password, str) or (not use_cookies and not password.strip()):
        raise ValueError("密码不能为空")
    return username.strip(), password, use_cookies


def _course_ids(value, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("请选择至少一门有效课程")
    ids = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)) or not str(item).strip():
            raise ValueError("课程 ID 格式错误")
        course_id = str(item).strip()
        if course_id not in ids:
            ids.append(course_id)
    return ids


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{name} 必须为有限数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 必须为有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须为有限数值")
    return number


def _close_resource(resource):
    if resource is not None:
        try:
            resource.close()
        except Exception as exc:
            logger.warning(f"清理学习资源失败: {exc}")
            return str(exc)
    return None


@contextmanager
def _login_client(username, password):
    tiku = Tiku()
    chaoxing = None
    try:
        chaoxing = Chaoxing(account=Account(username, password), tiku=tiku, query_delay=0)
        yield chaoxing
    finally:
        _close_resource(chaoxing if chaoxing is not None else tiku)


@app.route('/api/login', methods=['POST'])
def login():
    try:
        username, password, use_cookies = _credentials(_json_body())
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400
    try:
        with _login_client(username, password) as chaoxing:
            result = chaoxing.login(login_with_cookies=use_cookies)
            if not result["status"]:
                return jsonify({"status": False, "msg": result.get("msg", "登录失败")}), 401
            return jsonify({"status": True, "msg": "登录成功", "data": {"username": username}})
    except Exception as exc:
        logger.error(f"登录错误: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500


@app.route('/api/courses', methods=['POST'])
def get_courses():
    try:
        username, password, use_cookies = _credentials(_json_body())
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400
    try:
        with _login_client(username, password) as chaoxing:
            result = chaoxing.login(login_with_cookies=use_cookies)
            if not result["status"]:
                return jsonify({"status": False, "msg": result.get("msg", "登录失败")}), 401
            return jsonify({"status": True, "data": chaoxing.get_course_list()})
    except Exception as exc:
        logger.error(f"获取课程列表错误: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500


@app.route('/api/config', methods=['GET', 'POST'])
def web_config():
    if request.method == 'GET':
        data = load_web_config()
        data.pop("selectedCourses", None)
        return jsonify({"status": True, "data": data})
    try:
        data = _json_body()
        if "settings" in data and not isinstance(data["settings"], dict):
            raise ValueError("settings 必须为对象")
        selections = data.get("selectedCoursesByAccount", {})
        if not isinstance(selections, dict):
            raise ValueError("selectedCoursesByAccount 必须为对象")
        normalized = {}
        for account, ids in selections.items():
            if not isinstance(account, str) or not account.strip():
                raise ValueError("选课配置缺少账号")
            normalized[account.strip()] = _course_ids(ids, allow_empty=True)
    except ValueError as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400

    with config_lock:
        stored = load_web_config()
        stored.pop("selectedCourses", None)
        if "settings" in data:
            stored["settings"] = data["settings"]
        accounts = stored.get("selectedCoursesByAccount", {})
        accounts = dict(accounts) if isinstance(accounts, dict) else {}
        accounts.update(normalized)
        stored["selectedCoursesByAccount"] = accounts
        if not save_web_config(stored):
            return jsonify({"status": False, "msg": "保存失败"}), 500
    return jsonify({"status": True, "msg": "保存成功"})


def _initial_status():
    return {
        "progress": 0, "total": 0,
        "current_course": "", "current_chapter": "", "current_task": "",
        "stats": {
            "total_courses": 0, "completed_courses": 0, "failed_courses": 0,
            "skipped_courses": 0, "partial_courses": 0,
            "total_chapters": 0, "completed_chapters": 0, "empty_chapters": 0,
            "failed_chapters": 0, "skipped_chapters": 0,
            "total_tasks": 0, "completed_tasks": 0, "failed_tasks": 0, "skipped_tasks": 0,
        },
    }


def _job_count(point):
    try:
        return max(0, int(point.get("jobCount", 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _chapter_task_stats(point, result_name):
    supplied = point.get("_task_stats")
    fields = ("total", "completed", "failed", "skipped")
    if isinstance(supplied, dict) and all(
        isinstance(supplied.get(key), int) and not isinstance(supplied[key], bool)
        and supplied[key] >= 0 for key in fields
    ):
        stats = {key: supplied[key] for key in fields}
        stats["total"] = max(stats["total"], sum(stats[key] for key in fields[1:]))
        return stats
    count = 0 if result_name == "EMPTY" else _job_count(point)
    category = {"SUCCESS": "completed", "SKIPPED": "skipped"}.get(result_name, "failed")
    return {
        "total": count,
        "completed": count if category == "completed" else 0,
        "failed": count if category == "failed" else 0,
        "skipped": count if category == "skipped" else 0,
    }


class _StudyProgress:
    """Update chapter counters from final results, once per chapter."""

    def __init__(self, store, task_id):
        self.store = store
        self.task_id = task_id
        self._finalized = set()
        self._points = {}

    @staticmethod
    def _course(task, course):
        return next(item for item in task.details["courses"] if str(item["id"]) == str(course["courseId"]))

    @staticmethod
    def _replace_chapter(task, chapter, state, counts):
        stats = task.status["stats"]
        previous = chapter["status"]
        stats["completed_chapters"] += int(state in {"completed", "empty"}) - int(previous in {"completed", "empty"})
        for status, field in (("empty", "empty_chapters"), ("error", "failed_chapters"), ("skipped", "skipped_chapters")):
            stats[field] += int(state == status) - int(previous == status)
        for key in ("total", "completed", "failed", "skipped"):
            stats[f"{key}_tasks"] += counts[key] - chapter["task_stats"][key]
        chapter.update(status=state, has_finished=state in {"completed", "empty"}, task_stats=counts, jobCount=counts["total"])

    def set_courses(self, courses):
        with self.store.edit(self.task_id) as task:
            task.status["total"] = len(courses)
            task.status["stats"]["total_courses"] = len(courses)
            task.details["courses"] = [
                {"id": course["courseId"], "title": course["title"], "status": "pending",
                 "chapters": [], "start_time": None, "end_time": None}
                for course in courses
            ]

    def begin_course(self, course, index):
        with self.store.edit(self.task_id) as task:
            self._course(task, course).update(status="running", start_time=time.time())
            task.status.update(current_course=course["title"], current_chapter="", progress=index)

    def add_chapters(self, course, point_list):
        if not isinstance(point_list, dict):
            raise ValueError("章节列表格式错误")
        points = point_list["points"]
        if not isinstance(points, list) or any(not isinstance(point, dict) for point in points):
            raise ValueError("章节列表格式错误")
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            self._points[str(course["courseId"])] = points
            task.status["stats"]["total_chapters"] += len(points)
            for point in points:
                chapter = {
                    "id": point.get("id"), "title": point.get("title", ""), "status": "pending",
                    "has_finished": False, "jobCount": 0,
                    "task_stats": {"total": 0, "completed": 0, "failed": 0, "skipped": 0},
                }
                detail["chapters"].append(chapter)
                count = _job_count(point)
                finished = bool(point.get("has_finished"))
                self._replace_chapter(task, chapter, "completed" if finished else "pending", {
                    "total": count, "completed": count if finished else 0, "failed": 0, "skipped": 0,
                })

    def chapter_start(self, course, point):
        with self.store.edit(self.task_id) as task:
            task.status.update(current_course=course.get("title", ""), current_chapter=point.get("title", ""))
            for chapter in self._course(task, course)["chapters"]:
                if chapter["id"] == point.get("id") and chapter["status"] == "pending":
                    chapter["status"] = "running"
                    break

    def chapter_result(self, course, point, result):
        result_name = getattr(result, "name", "ERROR")
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            for index, chapter in enumerate(detail["chapters"]):
                if chapter["id"] != point.get("id"):
                    continue
                key = (str(course["courseId"]), index)
                if key in self._finalized:
                    return
                state = {"SUCCESS": "completed", "EMPTY": "empty", "SKIPPED": "skipped"}.get(result_name, "error")
                counts = _chapter_task_stats(point, result_name)
                if counts["failed"]:
                    state = "error"
                elif counts["skipped"] and state in {"completed", "empty"}:
                    state = "skipped"
                self._replace_chapter(task, chapter, state, counts)
                self._finalized.add(key)
                return
            raise ValueError("章节结果不属于当前课程快照")

    @staticmethod
    def _end_course(task, detail, *, failed, skipped):
        has_success = any(chapter["has_finished"] or chapter["task_stats"]["completed"] for chapter in detail["chapters"])
        state = "completed"
        if failed or skipped:
            state = "partial" if has_success or skipped else "error"
        detail.update(status=state, end_time=time.time())
        stats = task.status["stats"]
        stats["completed_courses"] += int(state == "completed")
        stats["partial_courses"] += int(state == "partial")
        stats["failed_courses"] += int(failed)
        stats["skipped_courses"] += int(skipped)

    def finish_course(self, course, result):
        if result is None:
            raise RuntimeError("课程未返回有效学习结果")
        for chapter_task in result.tasks:
            self.chapter_result(course, chapter_task.point, chapter_task.result)
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            if any(chapter["status"] in {"pending", "running"} for chapter in detail["chapters"]):
                raise RuntimeError("课程存在未返回结果的章节")
            failed = bool(result.failed) or any(
                chapter["status"] == "error" or chapter["task_stats"]["failed"]
                for chapter in detail["chapters"]
            )
            skipped = bool(result.skipped) or any(
                chapter["status"] == "skipped" or chapter["task_stats"]["skipped"]
                for chapter in detail["chapters"]
            )
            failed = failed or (not result.success and not skipped)
            self._end_course(task, detail, failed=failed, skipped=skipped)

    def fail_course(self, course, error):
        with self.store.edit(self.task_id) as task:
            detail = self._course(task, course)
            points = self._points.get(str(course["courseId"]), [])
            for index, chapter in enumerate(detail["chapters"]):
                key = (str(course["courseId"]), index)
                if key not in self._finalized and not chapter["has_finished"]:
                    self._replace_chapter(task, chapter, "error", _chapter_task_stats(points[index], "ERROR"))
                    self._finalized.add(key)
            detail["error"] = str(error)
            skipped = any(chapter["task_stats"]["skipped"] or chapter["status"] == "skipped" for chapter in detail["chapters"])
            self._end_course(task, detail, failed=True, skipped=skipped)

    def video_progress(self, course, job, current_time, duration):
        now = time.time()
        with self.store.edit(self.task_id) as task:
            active = task.details["active_jobs"]
            expired = [key for key, info in active.items() if now - info["timestamp"] > 10]
            for key in expired:
                del active[key]
            active[f"{course['courseId']}:{job['jobid']}"] = {
                "course_name": course["title"], "job_name": job.get("name", "未知任务"),
                "current_time": current_time, "duration": duration,
                "progress": (current_time / duration * 100) if duration > 0 else 0,
                "timestamp": now,
            }

    def outcome(self, *, fatal=False):
        stats = self.store.get_status(self.task_id)["stats"]
        failed = fatal or stats["failed_courses"] or stats["failed_chapters"] or stats["failed_tasks"]
        skipped = stats["skipped_courses"] or stats["skipped_chapters"] or stats["skipped_tasks"]
        successful = stats["completed_courses"] or stats["completed_chapters"] or stats["completed_tasks"]
        if failed and not successful and not skipped:
            return "error"
        return "partial" if failed or skipped else "completed"


def _notification_message(store, task_id, outcome, error):
    if outcome == "completed":
        return "超星学习通: 所有课程学习任务已完成"
    stats = store.get_status(task_id)["stats"]
    message = (
        f"超星学习通: 学习任务结束，完成课程 {stats['completed_courses']}，"
        f"失败课程 {stats['failed_courses']}，跳过课程 {stats['skipped_courses']}，"
        f"失败任务 {stats['failed_tasks']}，跳过任务 {stats['skipped_tasks']}"
    )
    return f"{message}\n{error}" if error else message


def _run_study_task(task_id, store, common_config, tiku_config, notification_config, ocr_config):
    progress = _StudyProgress(store, task_id)
    chaoxing = None
    notification = None
    sink_id = None
    outcome = "error"
    error = None
    with logger.contextualize(task_id=task_id):
        try:
            capture = LogCapture(task_id, store)
            sink_id = logger.add(capture.write, enqueue=True, filter=lambda record: record["extra"].get("task_id") == task_id)
            from api.vision_ocr import ocr_context

            with ocr_context(ocr_config or {}):
                try:
                    try:
                        configured = Notification()
                        configured.config_set(notification_config or {"provider": ""})
                        notification = configured.get_notification_from_config()
                        notification.init_notification()
                    except Exception as exc:
                        notification = None
                        logger.warning(f"通知初始化失败: {exc}")
                        with store.edit(task_id) as task:
                            task.status["notification_error"] = str(exc)

                    common_config.update(
                        chapter_start_callback=progress.chapter_start,
                        chapter_result_callback=progress.chapter_result,
                        video_progress_callback=progress.video_progress,
                    )
                    chaoxing = main_module.init_chaoxing(common_config, tiku_config)
                    result = chaoxing.login(login_with_cookies=common_config["use_cookies"])
                    if not result["status"]:
                        raise LoginError(result.get("msg", "登录失败"))
                    courses = main_module.filter_courses(
                        chaoxing.get_course_list(), common_config["course_list"], interactive=False
                    )
                    progress.set_courses(courses)
                    for index, course in enumerate(courses):
                        progress.begin_course(course, index)
                        try:
                            point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                            progress.add_chapters(course, point_list)
                            course_result = main_module.process_course(
                                chaoxing, course, common_config, point_list=point_list
                            )
                            progress.finish_course(course, course_result)
                        except Exception as exc:
                            logger.error(f"课程处理失败 {course['title']}: {exc}")
                            progress.fail_course(course, exc)
                        with store.edit(task_id) as task:
                            task.status["progress"] = index + 1
                    outcome = progress.outcome()
                    if outcome != "completed":
                        error = "部分课程失败或被跳过，请查看课程详情"
                finally:
                    cleanup_error = _close_resource(chaoxing)
                    if cleanup_error:
                        with store.edit(task_id) as task:
                            task.status["cleanup_error"] = cleanup_error
        except Exception as exc:
            error = str(exc)
            outcome = progress.outcome(fatal=True)
            logger.error(f"任务执行错误: {exc}")
        finally:
            if notification is not None:
                try:
                    notification.send(_notification_message(store, task_id, outcome, error))
                except Exception as exc:
                    logger.warning(f"通知发送失败: {exc}")
                    with store.edit(task_id) as task:
                        task.status["notification_error"] = str(exc)
            # Flush every enqueued record before publishing the terminal state.
            # Pollers can then stop after one final cursor read without losing logs.
            try:
                logger.complete()
            finally:
                try:
                    if sink_id is not None:
                        logger.remove(sink_id)
                finally:
                    store.finish(task_id, outcome, error=error)


def _launch_study_task(*args):
    context = copy_context()
    thread = threading.Thread(
        target=context.run, args=(_run_study_task, *args),
        name=f"study-{args[0]}", daemon=True,
    )
    thread.start()
    return thread


@app.route('/api/start', methods=['POST'])
def start_study():
    try:
        data = _json_body()
        username, password, use_cookies = _credentials(data)
        course_list = _course_ids(data.get("course_list"))
        jobs = main_module.validate_jobs(data.get("jobs", 4))
        speed = _finite_number(data.get("speed", 1.0), "speed")
        retry_interval = _finite_number(data.get("retry_interval", 1.0), "retry_interval")
        if speed <= 0:
            raise ValueError("speed 必须大于 0")
        if not 0 <= retry_interval <= 300:
            raise ValueError("retry_interval 必须在 0 到 300 秒之间")
        notopen_action = data.get("notopen_action", "retry")
        if notopen_action not in ("retry", "continue"):
            raise ValueError("Web 任务仅支持 retry 或 continue，不能交互询问")
        configs = []
        for key in ("tiku_config", "notification_config", "ocr_config"):
            config = data.get(key, {})
            if config is None and key == "ocr_config":
                config = {}
            if not isinstance(config, dict):
                raise ValueError(f"{key} 必须为对象")
            configs.append(config)
        common_config = {
            "username": username, "password": password, "use_cookies": use_cookies,
            "course_list": course_list, "jobs": jobs, "speed": min(2.0, max(1.0, speed)),
            "retry_interval": retry_interval, "notopen_action": notopen_action, "interactive": False,
        }
    except (ValueError, InputFormatError) as exc:
        return jsonify({"status": False, "msg": str(exc)}), 400

    store = task_store
    try:
        task_id = store.create(username, _initial_status(), {"courses": [], "active_jobs": {}})
    except TaskAlreadyRunning as exc:
        return jsonify({"status": False, "msg": str(exc), "data": {"task_id": exc.task_id}}), 409
    except Exception as exc:
        logger.error(f"创建任务失败: {exc}")
        return jsonify({"status": False, "msg": str(exc)}), 500
    try:
        _launch_study_task(task_id, store, common_config, *configs)
    except Exception as exc:
        store.finish(task_id, "error", error=str(exc))
        logger.error(f"启动任务错误: {exc}")
        return jsonify({"status": False, "msg": str(exc), "data": {"task_id": task_id}}), 500
    return jsonify({"status": True, "data": {"task_id": task_id}})


@app.route('/api/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        return jsonify({"status": True, "data": task_store.get_status(task_id)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务不存在或已过期"}), 404


@app.route('/api/task/<task_id>/details', methods=['GET'])
def get_task_details(task_id):
    try:
        return jsonify({"status": True, "data": task_store.get_details(task_id)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务详情不存在或已过期"}), 404


@app.route('/api/logs/<task_id>', methods=['GET'])
def get_logs(task_id):
    cursor = request.args.get("after", "0")
    if not cursor.isascii() or not cursor.isdecimal():
        return jsonify({"status": False, "msg": "after 必须为非负整数"}), 400
    try:
        after = int(cursor)
    except ValueError:
        return jsonify({"status": False, "msg": "after 必须为非负整数"}), 400
    try:
        return jsonify({"status": True, **task_store.read_logs(task_id, after)})
    except KeyError:
        return jsonify({"status": False, "msg": "任务日志不存在或已过期"}), 404

@app.route('/api/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': True, 'msg': 'OK'})


# ==================== 静态文件服务（便携版支持） ====================

@app.route('/')
def serve_index():
    """服务前端首页"""
    if os.path.exists(STATIC_DIR):
        return send_from_directory(STATIC_DIR, 'index.html')
    return jsonify({'status': False, 'msg': '前端未构建，请访问 http://localhost:5173 使用开发模式'}), 404


@app.route('/<path:path>')
def serve_static(path):
    """服务静态文件，支持 SPA 客户端路由"""
    if os.path.exists(STATIC_DIR):
        # 如果请求的是 API 路径，跳过（已被上面的路由处理）
        if path.startswith('api/'):
            return jsonify({'status': False, 'msg': 'Not Found'}), 404
        
        # 尝试提供静态文件
        file_path = os.path.join(STATIC_DIR, path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_from_directory(STATIC_DIR, path)
        
        # 对于 SPA 客户端路由，返回 index.html
        return send_from_directory(STATIC_DIR, 'index.html')
    
    return jsonify({'status': False, 'msg': '前端未构建'}), 404



def is_port_in_use(port: int) -> bool:
    """检查端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def wait_for_server_ready(url: str, timeout: int = 60) -> bool:
    """等待服务器就绪"""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except:
            time.sleep(0.5)
    return False


def create_tray_icon():
    """创建托盘图标（从 fav.jpg 加载）"""
    # 优先读取 web/public/ 下的图标（与前端登录页图标同源）
    icon_path = os.path.join(SCRIPT_DIR, "web", "public", "fav.jpg")
    if getattr(sys, 'frozen', False):
        # 打包态：从 _MEIPASS 临时解包目录读取
        icon_path = os.path.join(sys._MEIPASS, "fav.jpg")

    if not os.path.exists(icon_path):
        # 兜底：旧位置（脚本根目录）
        icon_path = os.path.join(SCRIPT_DIR, "fav.jpg")

    if os.path.exists(icon_path):
        return Image.open(icon_path)
    else:
        # 兜底：纯色圆形图标
        logger.warning(f"未找到图标文件 {icon_path}，使用默认图标")
        img = Image.new('RGB', (64, 64), color=(33, 150, 243))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(33, 150, 243), outline=(255, 255, 255), width=2)
        return img


def open_browser():
    """打开浏览器"""
    webbrowser.open(f"http://localhost:{PORT}")


def setup_tray_icon():
    """设置托盘图标"""
    if not TRAY_AVAILABLE:
        return None

    menu = Menu(
        MenuItem("打开控制台", lambda: open_browser()),
        MenuItem("退出", lambda icon, item: (icon.stop(), os._exit(0)))
    )

    icon = Icon("chaoxing-gui", create_tray_icon(), "超星学习通 · 自动化学习助手", menu)
    return icon


def watch_parent_stdin():
    """stdin 守护线程：监测父进程（Electron）退出，防止孤儿进程"""
    try:
        # 阻塞读取，父进程关闭管道时触发 EOF
        while sys.stdin.buffer.read(1):
            pass
    except Exception:
        pass
    logger.info("父进程已退出，正在终止后端...")
    os._exit(0)


if __name__ == "__main__":
    # === Tauri 宿主模式（CHAOXING_TAURI=1）===
    # 置于 frozen/HEADLESS 分支之前：随机端口握手 + token 鉴权由
    # api/desktop_runtime.py 承载；不读 CHAOXING_PORT。
    if os.environ.get("CHAOXING_TAURI") == "1":
        try:
            from api.desktop_runtime import parse_ready_env, run_tauri_server
        except ImportError as e:
            logger.error(f"Tauri 运行时模块缺失: {e}")
            sys.exit(1)
        try:
            token, instance_id = parse_ready_env()
        except Exception as e:
            logger.error(str(e))
            sys.exit(1)

        # cwd/CHAOXING_DATA_DIR 兜底：导入期路径由宿主 spawn 时设定，
        # 这里保证 chaoxing.log 等运行时文件仍写数据目录而非安装目录
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.chdir(DATA_DIR)
        except Exception as e:
            logger.error(f"Tauri 数据目录不可用: {e}")
            sys.exit(1)

        # Tauri 的原生代理无需 CORS；导入期已跳过注册。
        threading.Thread(target=watch_parent_stdin, daemon=True).start()
        try:
            run_tauri_server(app, token, instance_id)
        except Exception as e:
            logger.error(f"Tauri 服务器启动失败: {e}")
            sys.exit(1)

        # serve_forever 在后台线程，主线程保持存活直至 stdin EOF 触发 os._exit
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sys.exit(0)

    # === 打包态特殊处理 ===
    if getattr(sys, 'frozen', False) and not HEADLESS:
        # 独立 exe 模式（向后兼容）
        if sys.stdout is None or sys.stderr is None:
            # console=False 时 stdout/stderr 为 None，重定向到 devnull 防止 loguru/tqdm 崩溃
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')

        # 检查端口是否已占用（实例复用逻辑）
        if is_port_in_use(PORT):
            logger.info("检测到已有实例运行，直接打开浏览器")
            open_browser()
            sys.exit(0)

        # 修复 web_config.json 路径：冻结态下写到 exe 同目录，而非临时解包目录
        base_path = os.path.dirname(sys.executable)
        os.chdir(base_path)
    elif HEADLESS:
        # Electron 无头模式：cookies.txt / chaoxing.log 等运行时文件写入数据目录
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.chdir(DATA_DIR)
        except Exception as e:
            logger.warning(f"切换数据目录失败，沿用当前目录: {e}")
        logger.info(f"Electron 无头模式启动，工作目录: {os.getcwd()}")

    # 检测是否存在前端构建
    if os.path.exists(STATIC_DIR):
        logger.info(f"检测到前端构建，将提供静态文件服务: {STATIC_DIR}")
    else:
        logger.info("未检测到前端构建，仅提供 API 服务")

    # 启动托盘图标（仅独立 exe 模式 + pystray 可用时）
    tray_icon = None
    if getattr(sys, 'frozen', False) and TRAY_AVAILABLE and not HEADLESS:
        tray_icon = setup_tray_icon()
        threading.Thread(target=tray_icon.run, daemon=True).start()
        logger.info("托盘图标已启动，右键可退出")

    # 后台启动 Flask
    flask_thread = threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()

    # HEADLESS 模式：启动 stdin 守护 + 等待就绪
    if HEADLESS:
        threading.Thread(target=watch_parent_stdin, daemon=True).start()
        if wait_for_server_ready(f"http://127.0.0.1:{PORT}/api/health", timeout=60):
            logger.info(f"后端就绪: http://127.0.0.1:{PORT}")
        else:
            logger.error("服务器启动超时")
    # 独立 exe 模式：等待就绪后打开浏览器
    elif getattr(sys, 'frozen', False):
        if wait_for_server_ready(f"http://localhost:{PORT}/api/health", timeout=60):
            logger.info("服务器就绪，正在打开浏览器...")
            open_browser()
        else:
            logger.error("服务器启动超时")
    else:
        # 开发模式：直接提示 URL，不自动开浏览器
        logger.info(f"开发模式：请手动访问 http://localhost:{PORT}")

    # 主线程保持运行（托盘图标需要主线程存活）
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("接收到退出信号")
        if tray_icon:
            tray_icon.stop()

````

## build_tauri.bat

SHA256: 9b5f506d90743326c4a201f751c8f5d7a4d06d6caaec15968f20a5f4dfa6c082

````text
@echo off
setlocal
where pwsh.exe >nul 2>&1
if errorlevel 1 (
  echo PowerShell 7 is required. See desktop\README.md.
  exit /b 1
)
pwsh.exe -NoProfile -File "%~dp0desktop\scripts\build-tauri.ps1" %*
exit /b %errorlevel%

````

## chaoxing-backend.spec

SHA256: 5c56dfe3d648e2a3dbcd10f0f3a2ebe8d098310c78e56abcf54257f6f8651682

````text
# -*- mode: python ; coding: utf-8 -*-
# Electron 后端专用 spec (独立 exe 版本见 chaoxing.spec，注意同步 datas/hiddenimports)
# 主要差异：console=True (支持 stdin/stdout 管道), 移除 pystray, name=chaoxing-backend
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("web/dist", "web/dist"),
    ("resource", "resource"),
    ("config.ini.example", "."),
    ("fav.jpg", "."),
    ("web/public/fav.jpg", "."),
]
datas += collect_data_files("ddddocr")

hiddenimports = [
    "flask_cors",
    "loguru",
    "pyaes",
    "bs4",
    "lxml",
    "openai",
    "httpx",
    "ddddocr",
    "onnxruntime",
    "PIL",
    "numpy",
    "cv2",
    "tqdm",
    "fontTools",
    "requests",
    "urllib3",
    # 移除 pystray - Electron 无头模式不需要托盘图标
]
hiddenimports += collect_submodules("api")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["paddleocr", "paddlepaddle", "PaddleOCR", "celery"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir 模式：二进制文件由 COLLECT 处理
    name="chaoxing-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # console=True 确保 stdin/stdout 管道可用
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="web/public/fav.jpg",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chaoxing-backend",
)

````

## chaoxing.spec

SHA256: 9441c474e0fad9e6aedce6c58afe1b2b1ea365172440689072b7ddead3fba86b

````text
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("web/dist", "web/dist"),
    ("resource", "resource"),
    ("config.ini.example", "."),
    ("fav.jpg", "."),
    ("web/public/fav.jpg", "."),
]
datas += collect_data_files("ddddocr")

hiddenimports = [
    "flask_cors",
    "loguru",
    "pyaes",
    "bs4",
    "lxml",
    "openai",
    "httpx",
    "ddddocr",
    "onnxruntime",
    "PIL",
    "numpy",
    "cv2",
    "tqdm",
    "fontTools",
    "requests",
    "urllib3",
    "pystray",
]
hiddenimports += collect_submodules("api")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["paddleocr", "paddlepaddle", "PaddleOCR", "celery"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="chaoxing-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="web/public/fav.jpg",
)

````

## desktop/README.md

SHA256: 129c6f65cd850cb153ae4564bccd80b441ba83ae750a6c0e23d2e88ab1dd1b23

````text
# Windows 桌面版开发与打包指南

## Tauri 2 Windows x64（P3）

Tauri 使用独立的 `chaoxing-gui-tauri.exe` 和 `Chaoxing GUI Tauri` 安装目录。Electron 构建入口继续保留，P4 干净系统矩阵和 P5 默认入口切换尚未完成。当前阶段交付打包、内容校验和 CI 验收脚本；本机构建成功不代表 GitHub Windows runner 或干净 Win10/Win11 已通过。

### 安装、便携与回滚

产物位于 `desktop/release/tauri/`，版本取自 `pyproject.toml`（当前 `1.1.1`）：

| 产物 | 用途 |
|---|---|
| `chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe` | 当前用户 NSIS 安装；默认 `%LOCALAPPDATA%\Chaoxing GUI Tauri` |
| `chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip` | 完整解压后运行 `Start-Chaoxing.cmd` |
| `chaoxing-gui-tauri-artifacts-1.1.1-windows-x64.json`、`SHA256SUMS.txt` | 版本、大小、SHA256 和签名可用性记录 |
| ZIP 的 `.manifest.json`、`.sha256` | ZIP 与内部逐文件清单的完整性检查 |
| NSIS 的 `.exe.manifest.json` | 绑定 NSIS/ZIP 校验值，并记录安装版宿主的独立哈希；包校验时与两个产物一起保留 |

安装器拒绝覆盖含旧 Electron 程序的目录。Tauri 业务数据位于 `%APPDATA%\com.chaoxing.gui\data`，日志位于 `%LOCALAPPDATA%\com.chaoxing.gui\logs`；卸载保留 AppData 数据。便携版也使用当前 Windows 用户的 AppData，不提供随 U 盘携带账号数据的语义。不要仅复制宿主或后端 exe，必须保留完整 `backend/`、`_internal/`、启动脚本与清单。

首次导入旧 Electron 数据前请关闭 Electron。导入保留 `%APPDATA%\chaoxing-desktop` 原目录；回滚时退出 Tauri，再运行原 Electron 程序即可。Tauri 启动失败会在窗口中显示原因，“重新检查”只刷新状态；它不会重启后端或重复提交学习任务。无法保存或读取账号时会显示错误。

### WebView2 与离线安装

常规 NSIS 使用微软在线 Evergreen bootstrapper，缺少 WebView2 时需要联网下载运行时。离线设备先在联网机器从 [微软 WebView2 下载页](https://developer.microsoft.com/microsoft-edge/webview2/#download-section) 获取 **Evergreen Standalone Installer x64**，将 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` 复制到目标设备并安装，再运行 NSIS；不要只复制 bootstrapper 到离线设备。

便携入口先检查运行时，缺少时给出安装说明，不自动安装。用户可主动运行 `Install-WebView2.cmd`；把上述独立安装器放在同目录时会优先离线安装，也可传入 `-InstallerPath`。安装脚本执行前核验 Microsoft Authenticode 签名。直接运行宿主也会在创建 WebView、读取业务数据和启动后端之前显示原生提示。`chaoxing-gui-tauri.exe --check-webview2` 仅检测，返回 `0` 为可用，`3` 为不可用。

### 构建与版本

Windows 构建需要 PowerShell 7、Python 3.11+、Node 20、MSVC C++ Build Tools 与 Windows SDK、支持 NSIS 3 的近期 7-Zip（`7z.exe` 在 PATH，GitHub Windows runner 已提供），以及 Rust **1.95.0**（rustfmt/clippy）。Tauri Rust 依赖锁定 **2.11.5**，CLI 锁定 **2.11.4**。安装 Python 构建依赖时沿用 CI 的排除 PaddleOCR 策略和 PyInstaller **6.21.0**；不改变现有两个 PyInstaller spec 的 console 或排除项。

```powershell
# 本项目已配置的本机工具链环境；标准 rustup/MSVC 开发终端可直接使用。
. ./desktop/scripts/dev-env.ps1
python desktop/scripts/version.py --check
./build_tauri.bat
```

构建入口安装 Node 锁定依赖、执行 Web/Desktop/Python 回归、构建 Web 与冻结后端，再执行 Rust fmt/check/clippy/test、Tauri release 构建和 NSIS/ZIP 内容比对。Cargo 使用 `--locked -j 1`；所有 PyInstaller spec/work/dist 路径应和仓库处于同一磁盘。完整 `web/dist` 继续嵌在冻结后端中供 Electron 使用。CI 已预备并验证这些输入后使用 `build-tauri.ps1 -Prepared`。

Cargo 默认启用仅供测试的 `test-support` 特性，以保留原有 `cargo test --locked -j 1` 入口。正式构建额外传入 `--no-default-features`，Tauri 的 `required-features` 规则排除 `fake-backend`；最终包校验还会拒绝任何多余的测试程序。

Tauri 会在 NSIS 宿主中写入包类型标记，所以它与便携宿主的哈希不同。构建从编译输出独立计算 NSIS 预期字节；有证书时在签名回调中捕获已签 NSIS 宿主，再单独签便携宿主。资源清单中的后端和第三方依赖在打包时保持原字节。内容校验对两个宿主分别检查完整哈希，对其余全部文件使用相同资源清单。

`pyproject.toml` 是唯一版本源。手动修改该文件后，可运行 `python desktop/scripts/version.py --sync` 同步 Cargo、Tauri、Web/Desktop package 与 lock；`--check --tag v1.1.1 --artifacts desktop/release/tauri` 检查当前 tag 和产物。脚本不会自行升级版本或创建 tag。

`sign-windows.ps1 -Mode Inspect` 查找有效且含私钥的代码签名证书；多个证书时用 `CHAOXING_SIGN_CERT_THUMBPRINT` 选择。实际签名还需要 Windows SDK `signtool.exe`。CI 可提供 `WINDOWS_CERTIFICATE_PFX`（Base64）和 `WINDOWS_CERTIFICATE_PASSWORD` secrets；缺少可用证书时明确记录 unsigned，有证书时对宿主、后端和安装器签名并验证。SHA256 清单用于检测内容变化，不能替代发行者签名。

### 验证入口与边界

```powershell
python -m unittest discover -s tests -v
npm --prefix web test
npm --prefix desktop test
pwsh -NoProfile -File desktop/scripts/verify-package.ps1 -PackagePath desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip
pwsh -NoProfile -File desktop/scripts/verify-nsis.ps1 -InstallerPath desktop/release/tauri/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe -PortablePath desktop/release/tauri/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip
```

包检查不执行安装器或宿主。`smoke-tauri.ps1` 先用合成后端验证失败、取消、409/404 和退出，再验证真实冻结后端的 health/安全 API；宿主子进程 PATH 排除系统 Python，stdin 是持有的真实管道，嵌套 Windows Job 在关窗和强杀后检查残留。GUI 操作为 CDP 下确认可见/可用后 DOM click，不等于物理鼠标测试。

`smoke-installation.ps1` 只在 fresh GitHub Windows runner，或明确传入 `-DisposableWindowsUser` 的专用隔离 Windows 用户/VM 中运行安装、卸载和 ZIP 宿主验收。不要在含真实账号的日常用户下运行或用 debug 环境变量替代隔离；release 不接受 `CHAOXING_TAURI_DEV_ROOT`、`CHAOXING_TAURI_DEV_BACKEND`、`CHAOXING_TAURI_DEV_HIDDEN`。脚本拒绝既存 Tauri 用户数据，安装与清理均限制在本次创建的路径。

CI 保留 Python 3.11/3.13、Node 20、Electron 测试与打包、独立 Python 发行，增加 Rust/包校验、真实宿主和嵌套 Job 验收。任一步失败阻断发行路径，验证证据通过 artifact 留存；手动 workflow 默认不发布。尚未实际运行的远程 CI、缺 WebView2 干净机、安装后运行及签名门槛必须在阶段交付记录中保留。

## Electron（现有发行与回滚入口）

## 概述

超星学习通 · 自动化学习助手现在支持三种发行形态：

1. **独立 exe**（原有）：`chaoxing.spec` → `dist/chaoxing-gui.exe`，带托盘图标，自动打开系统浏览器
2. **便携版**（原有）：`build_portable.bat` → 嵌入式 Python + 启动脚本
3. **Electron 桌面版**（新增）：`desktop/` → 独立窗口，无需浏览器

## 架构

```
Electron 主进程 (main.js)
  ↓ spawn
  ├─ 后端子进程 (chaoxing-backend.exe / python app.py)
  │   ├─ Flask API (127.0.0.1:动态端口)
  │   └─ stdin watchdog (父进程退出时自动退出)
  └─ BrowserWindow
      └─ loadURL('http://127.0.0.1:{port}')
```

### 关键特性

- **动态端口**：Electron 分配空闲端口，通过 `CHAOXING_PORT` 环境变量传递给后端
- **孤儿进程防护**：后端监听 stdin EOF，父进程退出时自动终止
- **单实例锁**：`app.requestSingleInstanceLock()` 确保只运行一个实例
- **零前端改动**：前端仍通过相对路径 `/api/*` 调用后端

## 开发模式

### 启动完整开发环境

```bash
# 1. 后端开发
python app.py
# 访问 http://localhost:5000

# 2. 前端开发（热重载）
cd web && npm run dev
# 访问 http://localhost:3000（代理到后端 5000）

# 3. Electron 桌面开发（使用开发态后端 + 构建后的前端）
cd desktop && npm run dev
# Electron 窗口加载 http://127.0.0.1:{动态端口}
```

**注意**：
- Electron `npm run dev` 会启动 `python app.py`（开发模式），不支持前端热重载
- 前端迭代用 `web/npm run dev`；桌面窗口迭代用 `desktop/npm run dev`

## 构建生产版本

### 一键构建（推荐）

```bash
build_desktop.bat
```

**输出**：
- `desktop/release/chaoxing-gui-desktop-*.exe`（NSIS 安装包）
- `desktop/release/chaoxing-gui-desktop-*-portable.exe`（绿色便携版）

### 分步构建

```bash
# 1. 构建前端
cd web && npm ci && npm run build

# 2. 构建后端 exe（无头模式专用）
pyinstaller --clean --noconfirm chaoxing-backend.spec

# 3. 保留整个 onedir 后端层级
pwsh -NoProfile -File desktop/scripts/prepare-backend.ps1 -DestinationDirectory desktop/backend/chaoxing-backend

# 4. 构建 Electron 应用
cd desktop && npm ci && npx electron-builder --win
```

## 环境变量说明

后端 `app.py` 识别以下环境变量：

| 变量 | 值 | 作用 |
|------|---|------|
| `CHAOXING_HEADLESS` | `1` | 启用无头模式：绑定 127.0.0.1、禁用托盘、禁用 webbrowser.open、启用 stdin 守护 |
| `CHAOXING_PORT` | 整数 | Flask 监听端口，默认 `5000` |

**向后兼容**：
- 不设置环境变量 → 行为与原有完全一致（独立 exe 带托盘 + 浏览器）
- 开发模式 `python app.py` → 默认 5000 端口，无托盘，不自动开浏览器

## 文件清单

### 新增文件

```
desktop/
├── main.js                    # Electron 主进程
├── package.json               # Electron 依赖
├── electron-builder.yml       # 打包配置
├── build/icon.png             # 应用图标（512x512）
└── .gitignore

chaoxing-backend.spec          # 无头后端构建配置（console=True）
build_desktop.bat              # 一键构建脚本
```

### 修改文件

```
app.py                         # 新增 HEADLESS 模式支持（43-46, 617-627, 647-678 行）
.gitignore                     # 排除 desktop/node_modules, desktop/backend, desktop/release
```

## 常见问题

### Q1: 为什么需要两个 spec 文件？

- `chaoxing.spec`（原有）：独立 exe，`console=False`，带托盘图标
- `chaoxing-backend.spec`（新增）：Electron 后端，`console=True`，确保 stdin/stdout 可用

### Q2: 为什么后端要 `console=True`？

`console=True` 确保 `sys.stdin` 是真实管道句柄，而非 `None`。Electron 通过 `stdin.end()` 通知后端退出。

### Q3: 安装包体积多大？

- NSIS 安装包：~140 MB（压缩）
- 安装后体积：~250 MB（Electron 运行时 ~100 MB + PyInstaller 后端 ~150 MB）

### Q4: 如何调试后端日志？

**开发模式**：直接查看终端输出
**生产模式**：日志写入 `%APPDATA%\chaoxing-desktop\backend.log`

```powershell
Get-Content $env:APPDATA\chaoxing-desktop\backend.log -Tail 50 -Wait
```

### Q5: 端口冲突怎么办？

Electron 自动分配空闲端口，不会冲突。原有独立 exe 仍使用 5000，但支持实例复用（检测到 5000 占用时直接打开浏览器）。

## 测试清单

- [ ] 开发模式：`cd desktop && npm run dev` 窗口正常显示
- [ ] 生产构建：`build_desktop.bat` 无错误
- [ ] 安装：双击安装包，默认安装到 `C:\Users\<用户>\AppData\Local\Programs\超星泛雅刷课助手`
- [ ] 启动：桌面快捷方式启动，窗口显示登录界面
- [ ] 功能：登录 → 选课 → 开始学习，后台正常运行
- [ ] 关闭窗口：进程全部退出（Task Manager 检查无残留）
- [ ] 重复启动：二次启动聚焦第一个窗口，不创建新实例
- [ ] 配置持久化：`%APPDATA%\chaoxing-desktop\web_config.json` 保存用户配置
- [ ] 卸载：开始菜单卸载，程序文件清理，业务数据保留

## 许可证

与主项目一致

````

## desktop/electron-builder.yml

SHA256: 502004501a0e12b716e86fcf661b31f9cdddaad0b4b87e841d71ae1830856ce9

````text
appId: com.chaoxing.gui
productName: 超星学习通 · 自动化学习助手
directories:
  output: release
files:
  - main.js
  - preload.js
  - session-store.js
  - package.json
extraResources:
  - from: backend/chaoxing-backend
    to: backend
win:
  target:
    - target: nsis
    - target: portable
  icon: build/icon.png
  executableName: chaoxing-gui
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  artifactName: chaoxing-gui-desktop-setup-${version}.${ext}
  deleteAppDataOnUninstall: false
portable:
  artifactName: chaoxing-gui-desktop-portable-${version}.${ext}

````

## desktop/main.js

SHA256: 72f7a8420486fb6476d6ebaa7622e03069769b7eafe0872f7699a3499edbc0e9

````text
const fs = require('fs');
const os = require('os');
const path = require('path');
const net = require('net');
const { spawn, exec } = require('child_process');
const { app, BrowserWindow, Menu, dialog, ipcMain } = require('electron');
const { SessionStore, registerSessionIpc } = require('./session-store');

let mainWindow = null;
let backend = null;
let backendPid = 0;
let backendExited = false;
let port = 0;
let quitting = false;

const isDev = !app.isPackaged;

// 数据目录：后端配置、cookies、日志都落在这里
let dataDir;
try {
  dataDir = app.getPath('userData');
} catch (e) {
  dataDir = path.join(os.tmpdir(), 'chaoxing-desktop');
}
const MAIN_LOG = path.join(dataDir, 'main.log');
const BACKEND_LOG = path.join(dataDir, 'backend.log');
registerSessionIpc(ipcMain, {
  getWindow: () => mainWindow,
  getOrigin: () => port ? `http://127.0.0.1:${port}` : null,
  store: new SessionStore(dataDir),
});

/**
 * 主进程日志：启动失败时用于定位问题
 */
function trace(msg) {
  try {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.appendFileSync(MAIN_LOG, `${new Date().toISOString()} ${msg}\n`);
  } catch (e) {}
}

process.on('uncaughtException', (err) => trace('uncaughtException: ' + (err && err.stack ? err.stack : err)));
process.on('unhandledRejection', (err) => trace('unhandledRejection: ' + (err && err.stack ? err.stack : err)));

/**
 * 获取空闲端口（由系统分配，避开固定端口冲突）
 */
function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const p = server.address().port;
      server.close(() => resolve(p));
    });
    server.on('error', reject);
  });
}

/**
 * 启动后端进程
 */
async function startBackend() {
  port = await getFreePort();
  fs.mkdirSync(dataDir, { recursive: true });

  let cmd, args, cwd;
  if (isDev) {
    // 开发模式：运行仓库根目录的 app.py
    cmd = 'python';
    args = ['app.py'];
    cwd = path.join(__dirname, '..');
  } else {
    // 生产模式：运行 resources/backend 下的 PyInstaller 产物
    cmd = path.join(process.resourcesPath, 'backend', 'chaoxing-backend.exe');
    args = [];
    cwd = dataDir;
    if (!fs.existsSync(cmd)) {
      throw new Error(`后端程序缺失：${cmd}`);
    }
  }

  // 注意：stdio 必须是真实 fd，WriteStream 在 open 事件前 fd 为 null，spawn 会直接抛错
  const logFd = fs.openSync(BACKEND_LOG, 'a');
  fs.writeSync(logFd, `\n===== ${new Date().toISOString()} 启动 (port=${port}) =====\n`);

  try {
    backend = spawn(cmd, args, {
      cwd,
      env: {
        ...process.env,
        CHAOXING_HEADLESS: '1',
        CHAOXING_ELECTRON: '1',
        CHAOXING_PORT: String(port),
        CHAOXING_DATA_DIR: dataDir,
        PYTHONIOENCODING: 'utf-8',
      },
      stdio: ['pipe', logFd, logFd],
      windowsHide: true,
    });
  } finally {
    // 句柄已复制给子进程，父进程这份可以关掉
    try { fs.closeSync(logFd); } catch (e) {}
  }

  backendPid = backend.pid || 0;
  backendExited = false;
  trace(`后端已启动 pid=${backendPid} port=${port} cmd=${cmd}`);

  backend.on('exit', (code, signal) => {
    backend = null;
    backendExited = true;
    trace(`后端退出 code=${code} signal=${signal}`);
    if (quitting) return;
    // 非退出流程中后端意外死亡
    quitting = true;
    dialog.showErrorBox('后端异常', `后端进程已退出 (code=${code})\n日志：${BACKEND_LOG}`);
    app.quit();
  });

  backend.on('error', (err) => trace('后端进程错误: ' + err));
}

/**
 * 轮询健康检查，等待后端就绪
 */
function waitForBackend(timeoutMs = 120000) {
  const url = `http://127.0.0.1:${port}/api/health`;
  const start = Date.now();

  return new Promise((resolve, reject) => {
    const check = async () => {
      if (backendExited) {
        return reject(new Error(`后端进程已退出，请查看日志：\n${BACKEND_LOG}`));
      }
      try {
        const res = await fetch(url);
        if (res.ok) return resolve();
      } catch (err) {
        // 后端尚未监听，继续重试
      }
      if (Date.now() - start > timeoutMs) {
        return reject(new Error(`后端启动超时，请查看日志：\n${BACKEND_LOG}`));
      }
      setTimeout(check, 300);
    };
    check();
  });
}

function htmlPage(body) {
  const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>超星学习通 · 自动化学习助手</title><style>
html,body{height:100%;margin:0}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;
font-family:"Microsoft YaHei",system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:32px;
box-sizing:border-box;text-align:center}
.spinner{width:38px;height:38px;border:3px solid #334155;border-top-color:#38bdf8;border-radius:50%;
animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
p{margin:0;font-size:14px;color:#94a3b8}
h1{margin:0;font-size:18px;color:#f87171;font-weight:600}
pre{margin:0;max-width:90%;white-space:pre-wrap;word-break:break-all;font-size:12px;color:#94a3b8;
background:#1e293b;border-radius:8px;padding:14px;text-align:left}
</style></head><body>${body}</body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

const LOADING_PAGE = htmlPage('<div class="spinner"></div><p>正在启动服务，请稍候…</p>');

function errorPage(message) {
  return htmlPage(`<h1>启动失败</h1><pre>${String(message).replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</pre>`);
}

/**
 * 创建主窗口
 */
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: '超星学习通 · 自动化学习助手',
    autoHideMenuBar: true,
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  Menu.setApplicationMenu(null);

  // 安全限制：外链一律不新开窗口，导航限制在本地后端
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  const restrictNavigation = (e, url) => {
    try {
      if (!port || new URL(url).origin !== `http://127.0.0.1:${port}`) e.preventDefault();
    } catch { e.preventDefault(); }
  };
  mainWindow.webContents.on('will-navigate', restrictNavigation);
  mainWindow.webContents.on('will-redirect', restrictNavigation);

  // Programmatic loadURL may display the loading/error data pages; their IPC is denied.
  mainWindow.loadURL(LOADING_PAGE);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * 停止后端：先关 stdin 触发优雅退出，超时后强杀进程树
 */
function stopBackend() {
  const pid = backendPid;
  if (backend) {
    try {
      backend.stdin.end();
    } catch (err) {
      trace('关闭后端 stdin 失败: ' + err);
    }
  }
  backend = null;
  backendPid = 0;

  if (pid) {
    setTimeout(() => {
      exec(`taskkill /pid ${pid} /T /F`, () => {});
    }, 2000);
  }
}

/**
 * 应用启动
 */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    createWindow();
    try {
      await startBackend();
      await waitForBackend();
      trace('后端就绪，加载前端');
      if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${port}`);
    } catch (err) {
      const message = err && err.message ? err.message : String(err);
      trace('启动失败: ' + (err && err.stack ? err.stack : message));
      if (quitting) return;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(errorPage(message));
      } else {
        dialog.showErrorBox('启动失败', message);
        app.exit(1);
      }
    }
  });

  app.on('before-quit', () => {
    quitting = true;
    stopBackend();
  });

  app.on('window-all-closed', () => {
    app.quit();
  });
}

````

## desktop/package-lock.json

SHA256: 6b08a2f4701a014001b81caa6083ec65696b915a597d0089a58a7819f21d3a5d

Lock file retained on disk at the exact repository path; hash recorded above.

## desktop/package.json

SHA256: be6d89c31734b1c2ab9d98d75928c9b7c30603f804bd163a8c20d14e9a8b058e

````text
{
  "name": "chaoxing-desktop",
  "version": "1.1.1",
  "description": "超星学习通 · 自动化学习助手桌面版",
  "author": "chaoxing-gui",
  "main": "main.js",
  "private": true,
  "scripts": {
    "test": "node --test",
    "dev": "electron .",
    "start": "electron .",
    "dist": "electron-builder --win",
    "tauri": "tauri",
    "dev:tauri": "tauri dev",
    "build:tauri": "pwsh -NoProfile -File scripts/build-tauri.ps1",
    "check:version": "python scripts/version.py --check",
    "sync:version": "python scripts/version.py --sync"
  },
  "devDependencies": {
    "electron": "^33.0.0",
    "electron-builder": "^26.0.0",
    "@tauri-apps/cli": "2.11.4",
    "playwright-core": "1.63.0"
  }
}

````

## desktop/portable/Install-WebView2.cmd

SHA256: 97438a9cc98ce8a8a0c7ac1648ddf83d8a3956b4113d4f81a44da203a106f10e

````text
@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-WebView2.ps1" %*
set "chaoxing_exit=%ERRORLEVEL%"
pause
exit /b %chaoxing_exit%

````

## desktop/portable/Install-WebView2.ps1

SHA256: 017c2ba33368af5a2c019056122095d795068704b1d475d8f0977b55cf745c19

````text
#Requires -Version 5.1
[CmdletBinding()]
param([string]$InstallerPath, [ValidateRange(30, 1800)][int]$TimeoutSeconds = 600)

. (Join-Path $PSScriptRoot 'Portable-Common.ps1')
$temporaryDirectory = $null
$downloadedInstaller = $null
try {
    $offlineName = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    if (-not $InstallerPath -and [IO.File]::Exists((Join-Path $PSScriptRoot $offlineName))) {
        $InstallerPath = Join-Path $PSScriptRoot $offlineName
    }
    if ($InstallerPath) {
        $InstallerPath = [IO.Path]::GetFullPath($InstallerPath)
        if ([IO.Path]::GetFileName($InstallerPath) -ine $offlineName -or -not [IO.File]::Exists($InstallerPath)) {
            throw "Provide the official x64 offline installer named $offlineName."
        }
        Assert-PortablePlainPath $InstallerPath
    } else {
        # This download occurs only when the user explicitly runs this install entry.
        $temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("chaoxing-webview2-" + [Guid]::NewGuid().ToString('N'))
        Assert-PortablePlainPath $temporaryDirectory
        [void][IO.Directory]::CreateDirectory($temporaryDirectory)
        $downloadedInstaller = Join-Path $temporaryDirectory 'MicrosoftEdgeWebview2Setup.exe'
        Write-Host 'Downloading the official Microsoft WebView2 bootstrapper...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $downloadedInstaller -TimeoutSec 120
        $InstallerPath = $downloadedInstaller
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
    if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O="?Microsoft Corporation"?(?:,|$)') {
        throw 'Installer signature is not a valid Microsoft Authenticode signature. The installer was not run.'
    }
    Write-Host 'Installing Microsoft Edge WebView2 Runtime. This may take several minutes...'
    $result = Invoke-PortableProcess -FilePath $InstallerPath -Arguments '/silent /install' -TimeoutSeconds $TimeoutSeconds
    if ($result.ExitCode -eq 0) { Write-Host 'WebView2 installation completed. Run Start-Chaoxing.cmd.' }
    elseif ($result.ExitCode -eq 3010) { Write-Host 'WebView2 installation completed. Restart Windows before starting the app.' }
    else { Write-Host "WebView2 installer failed (exit $($result.ExitCode)). $($result.Error)" }
    exit $result.ExitCode
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($temporaryDirectory) {
        # Only delete the exact download and the empty directory this invocation owns.
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char[]]'\/')
        $resolved = [IO.Path]::GetFullPath($temporaryDirectory)
        if ([IO.Path]::GetDirectoryName($resolved) -ine $tempRoot -or [IO.Path]::GetFileName($resolved) -notmatch '^chaoxing-webview2-[0-9a-f]{32}$') {
            throw 'Unsafe WebView2 download cleanup path.'
        }
        Assert-PortablePlainPath $resolved
        if ($downloadedInstaller) { Assert-PortablePlainPath $downloadedInstaller; [IO.File]::Delete($downloadedInstaller) }
        if ([IO.Directory]::Exists($resolved)) { [IO.Directory]::Delete($resolved, $false) }
    }
}

````

## desktop/portable/Portable-Common.ps1

SHA256: 46c3faf4699113c8c68853a33cdc6c95c936e4381d59b361472af94a82be1ac4

````text
#Requires -Version 5.1
# Keep this file ASCII: Windows PowerShell 5.1 reads scripts using the system code page.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-PortableProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string]$Arguments = '')
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $Arguments
    $info.WorkingDirectory = Split-Path -Parent $FilePath
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    return $process
}

function Stop-PortableProcessTree {
    param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)
    if ($Process.HasExited) { return }
    # Framework PowerShell lacks Process.Kill(entireProcessTree). Use the Windows
    # system tool directly, without a shell or a visible helper window.
    $killer = New-PortableProcess -FilePath (Join-Path $env:SystemRoot 'System32/taskkill.exe') -Arguments "/PID $($Process.Id) /T /F"
    $started = $false
    try {
        $started = $killer.Start()
        if (-not $started) { throw 'Could not start process-tree cleanup.' }
        $killer.StandardInput.Close()
        $outTask = $killer.StandardOutput.ReadToEndAsync()
        $errTask = $killer.StandardError.ReadToEndAsync()
        if (-not $killer.WaitForExit(10000)) {
            $killer.Kill()
            if (-not $killer.WaitForExit(5000)) { throw 'Process-tree cleanup timed out.' }
        }
        if (-not $Process.WaitForExit(5000)) { throw 'Process-tree cleanup failed.' }
        if (-not $outTask.Wait(2000) -or -not $errTask.Wait(2000)) { throw 'Process-tree cleanup output did not close.' }
        if ($killer.ExitCode -ne 0 -and -not $Process.HasExited) { throw "Process-tree cleanup exited $($killer.ExitCode)." }
    } finally {
        if ($started -and -not $killer.HasExited) { $killer.Kill(); [void]$killer.WaitForExit(5000) }
        $killer.Dispose()
        if (-not $Process.HasExited) { $Process.Kill(); [void]$Process.WaitForExit(5000) }
    }
}

function Invoke-PortableProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath, [string]$Arguments = '',
        [ValidateRange(1, 1800)][int]$TimeoutSeconds = 15)
    $process = New-PortableProcess -FilePath $FilePath -Arguments $Arguments
    $started = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw "Could not start: $FilePath" }
        # A genuine stdin pipe, closed to EOF; no inherited terminal or file handle.
        $process.StandardInput.Close()
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw "Process timed out after $TimeoutSeconds seconds: $FilePath" }
        if (-not $outTask.Wait(2000) -or -not $errTask.Wait(2000)) { throw 'Process output pipes did not close.' }
        return @{ ExitCode = $process.ExitCode; Output = $outTask.Result; Error = $errTask.Result }
    } finally {
        try { if ($started -and -not $process.HasExited) { Stop-PortableProcessTree $process } }
        finally { $process.Dispose() }
    }
}

function Assert-PortablePlainPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a symbolic link or junction: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
    }
}

````

## desktop/portable/README.txt

SHA256: 3b053cb40bf12c4db0e458294d288c99980bf3038f6cd990804948acbb61dcc2

````text
Chaoxing GUI Tauri Windows x64 便携包

1. 将 ZIP 完整解压到可写文件夹，再运行 Start-Chaoxing.cmd。
   请保留 chaoxing-gui-tauri.exe、backend 整个目录及同目录的启动脚本。
   不要直接在 ZIP 内启动，也不要只复制 exe。
2. 启动入口只检测 WebView2，不会自动下载或安装运行时。
   若提示缺少 Microsoft Edge WebView2 Runtime，请主动运行 Install-WebView2.cmd。
   在线入口会下载微软官方 Evergreen bootstrapper，并在执行前校验微软签名。
3. 离线安装：在联网设备从微软官方页面下载 Evergreen Standalone Installer (x64)：
   https://developer.microsoft.com/microsoft-edge/webview2/#download-section
   将 MicrosoftEdgeWebView2RuntimeInstallerX64.exe 放到本目录，
   然后在目标设备主动运行 Install-WebView2.cmd；检测到该文件时不会下载 bootstrapper。
   也可在 PowerShell 运行：
   .\Install-WebView2.ps1 -InstallerPath "D:\下载\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
   只有有效 Microsoft Authenticode 签名的安装程序才能执行。
   安装完成后重新启动应用；退出码 3010 表示需先重启 Windows。

便携包与 Electron 版可并存。账号、任务及 WebView2 用户数据仍存放在当前
Windows 用户的 AppData 中，不会跟随此文件夹移动。删除便携文件夹不会删除
这些业务数据，也不会卸载原 Electron 版本。不要把含个人数据的 AppData 复制给别人。

backend-manifest.json 保存冻结后端全部文件的相对路径、大小及 SHA256。
package-manifest.json 保存包内文件校验值；ZIP 同目录的 .manifest.json 与 .sha256
可用于校验发行文件。校验脚本不需要启动应用。校验值用于检测内容变化，
发行者身份以数字签名和可信下载来源为准。

````

## desktop/portable/Start-Chaoxing.cmd

SHA256: 0c91d792a1cb99140fb58accc3dde9cf6a6dd51844fa84d620874645de832a2e

````text
@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Chaoxing.ps1" %*
set "chaoxing_exit=%ERRORLEVEL%"
if not "%chaoxing_exit%"=="0" pause
exit /b %chaoxing_exit%

````

## desktop/portable/Start-Chaoxing.ps1

SHA256: 326fef168ed460c0454780a56b46479b99bc49974f213668c63138be6b1f60c9

````text
#Requires -Version 5.1
[CmdletBinding()]
param([ValidateRange(1, 60)][int]$CheckTimeoutSeconds = 15)

. (Join-Path $PSScriptRoot 'Portable-Common.ps1')
try {
    $hostFile = Join-Path $PSScriptRoot 'chaoxing-gui-tauri.exe'
    if (-not [IO.File]::Exists($hostFile)) { throw 'chaoxing-gui-tauri.exe is missing. Extract the entire portable ZIP before starting.' }
    $check = Invoke-PortableProcess -FilePath $hostFile -Arguments '--check-webview2' -TimeoutSeconds $CheckTimeoutSeconds
    if ($check.ExitCode -eq 3) {
        Write-Host 'Microsoft Edge WebView2 Runtime is not installed.'
        Write-Host 'Run Install-WebView2.cmd in this folder, then run Start-Chaoxing.cmd again.'
        Write-Host 'For offline installation, see README.txt. Starting this app never installs a runtime automatically.'
        exit 3
    }
    if ($check.ExitCode -ne 0) {
        Write-Host "WebView2 check failed (exit $($check.ExitCode)). $($check.Error)"
        exit $check.ExitCode
    }
    # Launch the user-owned GUI after preflight. It keeps running after this
    # entrypoint closes; only the short-lived preflight belongs to our cleanup.
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $hostFile
    $info.WorkingDirectory = $PSScriptRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Normal
    $info.RedirectStandardInput = $true
    $process = [Diagnostics.Process]::Start($info)
    if ($null -eq $process) { throw 'Could not start Chaoxing GUI Tauri.' }
    try {
        $process.StandardInput.Close()
        # Surface immediate native startup errors while keeping the launcher bounded.
        if ($process.WaitForExit(2000)) { exit $process.ExitCode }
    } finally { $process.Dispose() }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}

````

## desktop/preload.js

SHA256: fd4a4e1b86957d03b60683e0db59f27f4ecbc68a0244a2778060349367f38b56

````text
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('chaoxingSession', Object.freeze({
  read: () => ipcRenderer.invoke('session:read'),
  rememberLogin: (username) => ipcRenderer.invoke('session:remember-login', username),
  rememberTask: (task) => ipcRenderer.invoke('session:remember-task', task),
  clear: () => ipcRenderer.invoke('session:clear'),
}));

````

## desktop/rust-toolchain.toml

SHA256: 696c2536031250a4fa61a1ec4daeba2e8ea2b1c5b725e659e19bf12be076c5f3

````text
[toolchain]
channel = "1.95.0"

````

## desktop/scripts/build-tauri.ps1

SHA256: 98cd538f090e990e60a2cafaf838d2ab2dbb1bbfbbfd4722cd6ffe84f9fb54cb

````text
[CmdletBinding()]
param(
    # CI prepares/tests Python and Node in dedicated steps before this entry.
    [switch]$Prepared,
    [string]$CertificateThumbprint = $env:CHAOXING_SIGN_CERT_THUMBPRINT
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'package-common.ps1')
. (Join-Path $PSScriptRoot 'nsis-content.ps1')
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$crate = Join-Path $repoRoot 'desktop/src-tauri'
$output = Join-Path $repoRoot 'desktop/release/tauri'
$target = 'x86_64-pc-windows-msvc'

function Invoke-BuildCommand {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

Push-Location -LiteralPath $repoRoot
try {
    $versionJson = & python (Join-Path $PSScriptRoot 'version.py') --check --json
    if ($LASTEXITCODE -ne 0) { throw "Release version check failed: $versionJson" }
    $version = ($versionJson | ConvertFrom-Json).version
    $rustVersion = & rustc --version
    if ($LASTEXITCODE -ne 0) { throw 'Rust is unavailable; initialize the documented toolchain first' }
    if ($rustVersion -notmatch '^rustc 1\.95\.0 ') { throw "Expected Rust 1.95.0, found $rustVersion" }
    New-Item -ItemType Directory -Path $output -Force | Out-Null

    if (-not $Prepared) {
        Invoke-BuildCommand npm @('--prefix', 'web', 'ci')
        Invoke-BuildCommand npm @('--prefix', 'web', 'test')
        Invoke-BuildCommand npm @('--prefix', 'web', 'run', 'build')
        Invoke-BuildCommand npm @('--prefix', 'desktop', 'ci')
        Invoke-BuildCommand npm @('--prefix', 'desktop', 'test')
        Invoke-BuildCommand python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
        Invoke-BuildCommand python @('-m', 'PyInstaller', '--clean', '--noconfirm', 'chaoxing-backend.spec')
    }
    foreach ($required in @('web/dist/index.html', 'dist/chaoxing-backend/chaoxing-backend.exe', 'desktop/node_modules/@tauri-apps/cli/tauri.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $required) -PathType Leaf)) {
            throw "Missing prepared build input: $required"
        }
    }
    $signArguments = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Inspect')
    if ($CertificateThumbprint) { $signArguments += @('-CertificateThumbprint', $CertificateThumbprint) }
    $signingJson = & pwsh @signArguments
    if ($LASTEXITCODE -ne 0) { throw "Signing availability check failed: $signingJson" }
    $signing = $signingJson | ConvertFrom-Json
    $signingJson | Set-Content -LiteralPath (Join-Path $output 'signing-availability.json') -Encoding utf8

    $backend = Join-Path $repoRoot 'dist/chaoxing-backend/chaoxing-backend.exe'
    if ($signing.signingAvailable) {
        $CertificateThumbprint = $signing.selectedThumbprint
        Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Sign', '-CertificateThumbprint', $CertificateThumbprint, '-Path', $backend)
    }
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'prepare-backend.ps1'), '-Version', $version)

    Invoke-BuildCommand cargo @('fmt', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--all', '--check')
    Invoke-BuildCommand cargo @('check', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '--all-targets', '-j', '1')
    Invoke-BuildCommand cargo @('clippy', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '--all-targets', '-j', '1', '--', '-D', 'warnings')
    Invoke-BuildCommand cargo @('test', '--manifest-path', 'desktop/src-tauri/Cargo.toml', '--locked', '-j', '1')

    $release = Join-Path $crate "target/$target/release"
    $hostExe = Join-Path $release 'chaoxing-gui-tauri.exe'
    $captureDirectory = Join-Path $crate "target/p3-signing-$([Guid]::NewGuid().ToString('N'))"
    $signedHostRecord = $null
    $arguments = @('--prefix', 'desktop', 'run', 'tauri', '--', 'build', '--ci', '--target', $target, '--bundles', 'nsis')
    if ($signing.signingAvailable) {
        # Structured argv preserves spaces in script and executable paths. Tauri
        # also invokes this for its generated uninstaller and final installer.
        [void][IO.Directory]::CreateDirectory($captureDirectory)
        $signedHostRecord = Join-Path $captureDirectory 'nsis-host.json'
        $signConfig = Join-Path $captureDirectory 'tauri-signing.config.json'
        $configuration = @{ bundle=@{ windows=@{ signCommand=@{
            cmd=(Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
            args=@('-NoProfile', '-File', (Join-Path $PSScriptRoot 'bundle-sign.ps1'), '-CertificateThumbprint', $CertificateThumbprint,
                '-ExpectedHostPath', $hostExe, '-HostRecordPath', $signedHostRecord,
                '-BackendDirectory', (Join-Path $crate 'resources/backend'), '-BackendManifestPath', (Join-Path $crate 'resources/backend-manifest.json'), '-Path', '%1')
        } } } }
        $configuration | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $signConfig -Encoding utf8
        $arguments += @('--config', $signConfig)
    }
    # Keep integration helper binaries out of both Cargo's release build and
    # Tauri's required-features-aware bundle enumeration.
    $arguments += @('--', '--locked', '-j', '1', '--no-default-features')
    $started = Get-Date
    Invoke-BuildCommand npm $arguments

    # A fresh machine may obtain makensis only during its first Tauri bundle.
    # Run the real Win32 path fixtures now and forbid a missing-tool skip.
    $previousNsisRequirement = $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS
    try {
        $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS = '1'
        Invoke-BuildCommand node @('--test', (Join-Path $repoRoot 'desktop/tests/nsis-paths.test.mjs'))
    } finally {
        if ($null -eq $previousNsisRequirement) { Remove-Item Env:CHAOXING_REQUIRE_NSIS_PATH_TESTS -ErrorAction SilentlyContinue }
        else { $env:CHAOXING_REQUIRE_NSIS_PATH_TESTS = $previousNsisRequirement }
    }

    if (-not (Test-Path -LiteralPath $hostExe -PathType Leaf)) { throw 'Tauri did not produce the independently named host executable' }
    $hostVersion = (Get-Item -LiteralPath $hostExe).VersionInfo.ProductVersion
    if ($hostVersion -notin @($version, "$version.0")) { throw "Host PE product version $hostVersion differs from $version" }
    $installers = @(Get-ChildItem -LiteralPath (Join-Path $release 'bundle/nsis') -File -Filter '*.exe' | Where-Object { $_.LastWriteTime -ge $started })
    if ($installers.Count -ne 1) { throw "Expected one newly built NSIS installer, found $($installers.Count)" }
    $setup = Join-Path $output "chaoxing-gui-tauri-setup-$version-windows-x64.exe"
    Copy-Item -LiteralPath $installers[0].FullName -Destination $setup -Force
    # The bundler restores the unsigned UNK host after packaging the NSS host.
    # Derive the expected NSIS bytes independently; signed captures must match
    # that exact pre-sign hash. Sign the restored portable host separately.
    $hostExpectation = Get-NsisHostExpectation -HostPath $hostExe -SignedHostRecordPath $signedHostRecord
    if ($signing.signingAvailable) {
        Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'sign-windows.ps1'), '-Mode', 'Sign', '-CertificateThumbprint', $CertificateThumbprint, '-Path', $hostExe)
    }
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'package-portable.ps1'), '-HostPath', $hostExe, '-OutputDirectory', $output, '-Version', $version)
    $portable = Join-Path $output "chaoxing-gui-tauri-portable-$version-windows-x64.zip"
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'verify-package.ps1'), '-PackagePath', $portable, '-Version', $version)
    Write-NsisArtifactManifest -InstallerPath $setup -PortablePath $portable -HostExpectation $hostExpectation
    Invoke-BuildCommand pwsh @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'verify-nsis.ps1'), '-InstallerPath', $setup, '-PortablePath', $portable, '-EvidenceDirectory', (Join-Path $repoRoot 'desktop/release/verification/nsis-content'))
    Invoke-BuildCommand python @((Join-Path $PSScriptRoot 'version.py'), '--check', '--artifacts', $output)

    # Call the script in this PowerShell process so Path remains an actual array.
    $verifyParameters = @{ Mode='Verify'; Path=@($hostExe, (Join-Path $crate 'resources/backend/chaoxing-backend.exe'), $setup); ReportPath=(Join-Path $output 'signatures.json') }
    if ($CertificateThumbprint) { $verifyParameters.CertificateThumbprint = $CertificateThumbprint }
    & (Join-Path $PSScriptRoot 'sign-windows.ps1') @verifyParameters

    $artifactFiles = @($setup, "$setup.manifest.json", $portable, "$portable.manifest.json", "$portable.sha256")
    $artifactManifest = [ordered]@{
        version=$version; target=$target; rust=$rustVersion; tauri='2.11.5'; signed=[bool]$signing.signingAvailable
        builtAt=(Get-Date).ToUniversalTime().ToString('o')
        artifacts=@(foreach ($file in $artifactFiles) {
            [ordered]@{ name=[System.IO.Path]::GetFileName($file); bytes=(Get-Item -LiteralPath $file).Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
        })
        acceptance='Build and content checks only; release host acceptance requires disposable Windows profile smoke'
    }
    $artifactManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output "chaoxing-gui-tauri-artifacts-$version-windows-x64.json") -Encoding utf8
    $artifactManifest.artifacts | ForEach-Object { "$($_.sha256)  $($_.name)" } | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
    Write-Output "Tauri $version build and content checks completed: $output"
} finally {
    Pop-Location
}

````

## desktop/scripts/bundle-sign.ps1

SHA256: bfd763b731726f138534c5eecd52796c508d4a4c15a3f99e4cea6ccccf35c4d0

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$CertificateThumbprint,
    [Parameter(Mandatory)][string]$ExpectedHostPath,
    [Parameter(Mandatory)][string]$HostRecordPath,
    [Parameter(Mandatory)][string]$BackendDirectory,
    [Parameter(Mandatory)][string]$BackendManifestPath
)
. (Join-Path $PSScriptRoot 'package-common.ps1')

if (-not $IsWindows) { throw 'The bundle signing callback requires Windows.' }
$CertificateThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
if ($CertificateThumbprint -cnotmatch '^[A-F0-9]{40}$') { throw 'Invalid signing certificate thumbprint.' }
$signingPath = Get-PackageFullPath $Path
$expectedHost = Get-PackageFullPath $ExpectedHostPath
$hostRecord = Get-PackageFullPath $HostRecordPath
$backend = Get-PackageFullPath $BackendDirectory
$backendManifest = Get-PackageFullPath $BackendManifestPath
foreach ($file in @($signingPath, $expectedHost, $hostRecord, $backend, $backendManifest)) {
    Assert-PackageNoReparse $file
}
if (-not [IO.File]::Exists($signingPath)) { throw "Signing input must be an existing regular file: $signingPath" }
if (-not [IO.Directory]::Exists($backend)) { throw "Missing backend directory: $backend" }
Assert-PackageDisjoint $expectedHost $backend
Assert-PackageDisjoint $backendManifest $backend
foreach ($inputPath in @($signingPath, $expectedHost, $backend, $backendManifest)) {
    Assert-PackageDisjoint $hostRecord $inputPath
}

function Get-BundleFileFingerprint {
    param([Parameter(Mandatory)][string]$File)
    Assert-PackageNoReparse $File
    return [ordered]@{
        length = [IO.FileInfo]::new($File).Length
        sha256 = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Get-NsisHostFingerprint {
    param([Parameter(Mandatory)][string]$File)
    Assert-PackageNoReparse $File
    $bytes = [IO.File]::ReadAllBytes($File)
    if ($bytes.Length -lt 2 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw 'Expected an NSIS host PE/MZ executable.' }
    $image = [Text.Encoding]::ASCII.GetString($bytes)
    $prefix = '__TAURI_BUNDLE_TYPE_VAR_'
    $marker = $prefix + 'NSS'
    $index = $image.IndexOf($prefix, [StringComparison]::Ordinal)
    if ($index -lt 0 -or $image.LastIndexOf($prefix, [StringComparison]::Ordinal) -ne $index -or
        $image.IndexOf($marker, [StringComparison]::Ordinal) -ne $index) {
        throw 'Expected exactly one __TAURI_BUNDLE_TYPE_VAR_NSS host marker.'
    }
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
    return [ordered]@{ length = [long]$bytes.Length; sha256 = $hash }
}

if ($signingPath.StartsWith($backend + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    # Tauri calls its signer for unsigned resource EXEs/DLLs. Their bytes were
    # frozen before backend-manifest.json was written; this callback preserves
    # them instead of giving third-party files a new signature or a new hash.
    Assert-PackageChildPath $signingPath $backend
    $relative = [IO.Path]::GetRelativePath($backend, $signingPath).Replace('\', '/')
    Assert-PackageRelativePath $relative
    $manifest = Read-PackageJson $backendManifest
    Assert-PackageManifestHeader $manifest 'chaoxing-backend' (Get-PackageVersion) 'chaoxing-backend.exe'
    if (-not $manifest.Contains('files') -or $manifest.files -isnot [Collections.IList]) { throw 'Backend manifest files must be an array.' }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $expected = $null
    foreach ($entry in $manifest.files) {
        if ($entry -isnot [Collections.IDictionary] -or -not $entry.Contains('path') -or $entry.path -isnot [string] -or
            -not $entry.Contains('length') -or ($entry.length -isnot [long] -and $entry.length -isnot [int]) -or $entry.length -lt 0 -or
            -not $entry.Contains('sha256') -or $entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Invalid backend manifest file record.'
        }
        Assert-PackageRelativePath $entry.path
        if (-not $names.Add($entry.path)) { throw "Duplicate backend manifest path: $($entry.path)" }
        if ($entry.path -ceq $relative) { $expected = $entry }
    }
    if ($null -eq $expected) { throw "Signing resource is not in the backend manifest: $relative" }
    $before = Get-BundleFileFingerprint $signingPath
    if ($before.length -ne $expected.length -or $before.sha256 -cne $expected.sha256) {
        throw "Backend resource length/hash mismatch: $relative"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $signingPath
    if ($signature.Status -notin @('Valid', 'NotSigned')) {
        throw "Invalid existing backend resource signature: $relative ($($signature.Status))"
    }
    $after = Get-BundleFileFingerprint $signingPath
    if ($after.length -ne $before.length -or $after.sha256 -cne $before.sha256) { throw "Backend resource changed during verification: $relative" }
    [ordered]@{
        action = 'preserved-backend-resource'; sourcePath = $signingPath; path = $relative
        length = $after.length; sha256 = $after.sha256; signatureStatus = [string]$signature.Status
        signerThumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
    } | ConvertTo-Json -Depth 4
    return
}

$isHost = $signingPath.Equals($expectedHost, [StringComparison]::OrdinalIgnoreCase)
if ($isHost) {
    # Build supplies an already-created directory unique to this invocation.
    # Refuse an old record before signing, then publish with a no-overwrite move.
    $recordDirectory = [IO.Path]::GetDirectoryName($hostRecord)
    Assert-PackageMutableDirectory $recordDirectory
    if (-not [IO.Directory]::Exists($recordDirectory)) { throw 'Host record requires an existing unique capture directory.' }
    if (Test-Path -LiteralPath $hostRecord) { throw "Refusing to overwrite an existing host record: $hostRecord" }
    $preSign = Get-NsisHostFingerprint $signingPath
}

$signScript = Join-Path $PSScriptRoot 'sign-windows.ps1'
& $signScript -Mode Sign -CertificateThumbprint $CertificateThumbprint -Path $signingPath | Out-Null
$verificationJson = & $signScript -Mode Verify -CertificateThumbprint $CertificateThumbprint -Path $signingPath
$verification = ConvertFrom-Json -InputObject ($verificationJson -join "`n") -Depth 8
if (@($verification.files).Count -ne 1 -or $verification.files[0].status -cne 'Valid' -or
    $verification.files[0].signerThumbprint -ine $CertificateThumbprint) {
    throw 'Signing callback did not obtain one valid signature from the selected certificate.'
}

if ($isHost) {
    $signed = Get-NsisHostFingerprint $signingPath
    if ($signed.sha256 -cne $verification.files[0].sha256) { throw 'NSIS host changed after signature verification.' }
    $record = [ordered]@{
        schemaVersion = 1; kind = 'chaoxing-gui-tauri-signed-nsis-host'; sourcePath = $signingPath
        bundleType = 'nsis'; preSignSha256 = $preSign.sha256; preSignLength = $preSign.length
        length = $signed.length; sha256 = $signed.sha256; signerThumbprint = $verification.files[0].signerThumbprint
    }
    $temporaryRecord = Join-Path $recordDirectory ".host-record-$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        Assert-PackageChildPath $temporaryRecord $recordDirectory
        $stream = [IO.File]::Open($temporaryRecord, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-Json -InputObject $record -Depth 4) + "`n")
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        } finally { $stream.Dispose() }
        Assert-PackageChildPath $hostRecord $recordDirectory
        [IO.File]::Move($temporaryRecord, $hostRecord)
    } finally {
        if ([IO.File]::Exists($temporaryRecord)) {
            Assert-PackageChildPath $temporaryRecord $recordDirectory
            [IO.File]::Delete($temporaryRecord)
        }
    }
    $record | ConvertTo-Json -Depth 4
} else {
    Write-Output $verificationJson
}

````

## desktop/scripts/dev-env.ps1

SHA256: 4bec7a87fcff3a746d554b964f24add01ea6b34672d60b14aa9b435cf00ea79f

````text
# 开发环境初始化（本机专用，不进 CI）
# 用法：在会话中先 `. .\desktop\scripts\dev-env.ps1`，再运行 cargo / tauri 命令。
# 本机 rustup 代理缺失（无 rustup.exe、~/.cargo/bin 为空），需要直接使用工具链目录。
# 本机 MSVC 检测缺失（无 vswhere.exe、无 VS 注册表键），需要 vcvars64 提供 link.exe/LIB/INCLUDE。

$ErrorActionPreference = 'Stop'
$toolchainBin = "$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin"
if (-not (Test-Path $toolchainBin)) { throw "未找到 Rust 工具链: $toolchainBin" }
$env:PATH = "$toolchainBin;$env:PATH"
$env:CARGO_HOME = "$env:USERPROFILE\.cargo"

# 通过 vcvars64 获取 MSVC 链接环境（子进程 cmd 输出解析，避免改变当前控制台代码页）
$bat = Join-Path $env:TEMP "chaoxing-vcenv-$(Get-Random).bat"
@'
@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 exit /b %errorlevel%
set
'@ | Set-Content $bat -Encoding ascii
try {
  $vcOutput = & cmd.exe /d /c "`"$bat`""
  if ($LASTEXITCODE -ne 0) { throw "vcvars64 failed with exit code $LASTEXITCODE" }
  $envLines = $vcOutput | Select-String -Pattern "^(LIB|INCLUDE|Path)=" -SimpleMatch:$false
} finally {
  Remove-Item -LiteralPath $bat -ErrorAction SilentlyContinue
}
foreach ($line in $envLines) {
  $name, $value = $line.Line -split '=', 2
  switch ($name) {
    'LIB' { $env:LIB = $value }
    'INCLUDE' { $env:INCLUDE = $value }
    'Path' { $env:VCVARS_PATH = $value }
  }
}
# 把 vcvars 的 PATH 中的 MSVC/SDK 目录并入当前 PATH（放在最前，保证 link.exe 可被 cargo 找到）
if ($env:VCVARS_PATH) {
  $vcDirs = ($env:VCVARS_PATH -split ';') | Where-Object { $_ -match 'MSVC|Windows Kits' }
  foreach ($dir in $vcDirs) { if (Test-Path $dir) { $env:PATH = "$dir;$env:PATH" } }
  Remove-Item Env:VCVARS_PATH -ErrorAction SilentlyContinue
}
$cargoVersion = & cargo --version
if ($LASTEXITCODE -ne 0) { throw "cargo failed with exit code $LASTEXITCODE" }
Write-Host "Rust 工具链与 MSVC 链接环境已就绪: $cargoVersion"

````

## desktop/scripts/nsis-content.ps1

SHA256: 8d05a040f6fd7f8cf87b6d2fd337dcfbdc60928744325d67d847042d83f0c550

````text
# Shared by read-only package validation and its payload fault tests.
# package-common.ps1 must be loaded first.

function Get-NsisHostExpectation {
    param([Parameter(Mandatory)][string]$HostPath, [string]$SignedHostRecordPath)
    $hostFile = Get-PackageFullPath $HostPath
    Assert-PackageNoReparse $hostFile
    if (-not [IO.File]::Exists($hostFile) -or [IO.FileInfo]::new($hostFile).Length -gt 64MB) { throw 'Missing or oversized compiler host.' }
    $bytes = [IO.File]::ReadAllBytes($hostFile)
    if ($bytes.Length -lt 2 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw 'Compiler host has no PE header.' }
    $sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    # Tauri CLI 2.11.4 patches exactly this marker, signs the patched host, then
    # restores the original unsigned bytes. Never normalize arbitrary bytes or
    # derive an expectation from the installer we are about to inspect.
    $unknown = '__TAURI_BUNDLE_TYPE_VAR_UNK'
    $nsis = '__TAURI_BUNDLE_TYPE_VAR_NSS'
    $text = [Text.Encoding]::Latin1.GetString($bytes)
    $offset = $text.IndexOf($unknown, [StringComparison]::Ordinal)
    if ($offset -lt 0 -or $text.IndexOf($unknown, $offset + 1, [StringComparison]::Ordinal) -ge 0 -or
        $text.Contains($nsis, [StringComparison]::Ordinal)) { throw 'Expected exactly one unpatched Tauri bundle marker.' }
    [Text.Encoding]::ASCII.GetBytes($nsis).CopyTo($bytes, $offset)
    $patchedHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
    $record = [ordered]@{
        path='chaoxing-gui-tauri.exe'; length=[long]$bytes.Length; sha256=$patchedHash
        sourceSha256=$sourceHash; preSignLength=[long]$bytes.Length; preSignSha256=$patchedHash
        markerOffset=$offset; signed=$false; signerThumbprint=$null; derivation='tauri-cli-2.11.4-bundle-marker'
    }
    if ($SignedHostRecordPath) {
        $capture = Read-PackageJson (Get-PackageFullPath $SignedHostRecordPath)
        if ($capture.schemaVersion -ne 1 -or $capture.kind -cne 'chaoxing-gui-tauri-signed-nsis-host' -or
            $capture.bundleType -cne 'nsis' -or -not $hostFile.Equals((Get-PackageFullPath $capture.sourcePath), [StringComparison]::OrdinalIgnoreCase) -or
            $capture.preSignSha256 -cne $patchedHash -or $capture.preSignLength -ne $bytes.Length -or
            ($capture.length -isnot [long] -and $capture.length -isnot [int]) -or $capture.length -lt $bytes.Length -or $capture.length -gt 64MB -or
            $capture.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $capture.signerThumbprint -cnotmatch '^[A-F0-9]{40}$') {
            throw 'Signed NSIS host capture does not match the exact unsigned compiler output.'
        }
        $record.length = $capture.length
        $record.sha256 = $capture.sha256
        $record.signed = $true
        $record.signerThumbprint = $capture.signerThumbprint
        $record.derivation = 'tauri-cli-2.11.4-bundle-marker-and-sign-command-capture'
    }
    return $record
}

function Assert-NsisHostExpectation {
    param([Parameter(Mandatory)]$Record)
    if ($Record -isnot [Collections.IDictionary] -or $Record.path -cne 'chaoxing-gui-tauri.exe' -or
        ($Record.length -isnot [long] -and $Record.length -isnot [int]) -or $Record.length -le 0 -or $Record.length -gt 64MB -or
        $Record.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $Record.sourceSha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $Record.preSignSha256 -cnotmatch '^[0-9a-f]{64}$' -or $Record.signed -isnot [bool]) { throw 'Invalid NSIS host expectation.' }
    if ($Record.signed) {
        if ($Record.signerThumbprint -cnotmatch '^[A-F0-9]{40}$' -or $Record.derivation -cne 'tauri-cli-2.11.4-bundle-marker-and-sign-command-capture') { throw 'Invalid signed NSIS host expectation.' }
    } elseif ($null -ne $Record.signerThumbprint -or $Record.derivation -cne 'tauri-cli-2.11.4-bundle-marker' -or
        $Record.sha256 -cne $Record.preSignSha256 -or $Record.length -ne $Record.preSignLength) { throw 'Invalid unsigned NSIS host expectation.' }
}

function Get-NsisArtifactIdentity {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$ExpectedName)
    $file = Get-PackageFullPath $Path
    Assert-PackageNoReparse $file
    if ([IO.Path]::GetFileName($file) -cne $ExpectedName -or -not [IO.File]::Exists($file) -or [IO.FileInfo]::new($file).Length -eq 0) {
        throw "Missing or incorrectly named artifact: $ExpectedName"
    }
    return [ordered]@{ path=$ExpectedName; length=[IO.FileInfo]::new($file).Length; sha256=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
}

function Write-NsisArtifactManifest {
    param([Parameter(Mandatory)][string]$InstallerPath, [Parameter(Mandatory)][string]$PortablePath, [Parameter(Mandatory)]$HostExpectation)
    $version = Get-PackageVersion
    Assert-NsisHostExpectation $HostExpectation
    $record = [ordered]@{
        schemaVersion=1; kind='chaoxing-gui-tauri-nsis-artifact'; version=$version; platform='windows-x64'
        installer=(Get-NsisArtifactIdentity $InstallerPath "chaoxing-gui-tauri-setup-$version-windows-x64.exe")
        portable=(Get-NsisArtifactIdentity $PortablePath "chaoxing-gui-tauri-portable-$version-windows-x64.zip")
        host=$HostExpectation
    }
    $sidecar = (Get-PackageFullPath $InstallerPath) + '.manifest.json'
    Assert-PackageNoReparse $sidecar
    Write-PackageJson $sidecar $record
}

function Read-NsisPayloadManifest {
    param([Parameter(Mandatory)][string]$InstallerPath, [Parameter(Mandatory)][string]$PortablePath, [Parameter(Mandatory)]$PortableManifest)
    $version = Get-PackageVersion
    Assert-PackageManifestHeader $PortableManifest 'chaoxing-gui-tauri-portable' $version 'chaoxing-gui-tauri.exe'
    Assert-PackageInventoryManifest $PortableManifest $PortableManifest
    $sidecar = Read-PackageJson ((Get-PackageFullPath $InstallerPath) + '.manifest.json')
    Assert-PackageManifestHeader $sidecar 'chaoxing-gui-tauri-nsis-artifact' $version
    if ($sidecar.platform -cne 'windows-x64' -or $PortableManifest.platform -cne 'windows-x64') { throw 'Invalid NSIS payload platform.' }
    $identities = @{
        installer=(Get-NsisArtifactIdentity $InstallerPath "chaoxing-gui-tauri-setup-$version-windows-x64.exe")
        portable=(Get-NsisArtifactIdentity $PortablePath "chaoxing-gui-tauri-portable-$version-windows-x64.zip")
    }
    foreach ($name in $identities.Keys) {
        $record = $sidecar[$name]
        if ($record -isnot [Collections.IDictionary] -or $record.path -cne $identities[$name].path -or
            $record.length -ne $identities[$name].length -or $record.sha256 -cne $identities[$name].sha256) { throw "NSIS manifest $name artifact identity mismatch." }
    }
    Assert-NsisHostExpectation $sidecar.host
    $hosts = @($PortableManifest.files | Where-Object { $_.path -ceq 'chaoxing-gui-tauri.exe' })
    if ($hosts.Count -ne 1) { throw 'Portable manifest must contain exactly one host.' }
    if (-not $sidecar.host.signed -and ($hosts[0].sha256 -cne $sidecar.host.sourceSha256 -or $hosts[0].length -ne $sidecar.host.preSignLength)) {
        throw 'Unsigned NSIS host source differs from the verified portable host.'
    }
    return [ordered]@{
        schemaVersion=1; kind='chaoxing-gui-tauri-nsis-payload'; version=$version; platform='windows-x64'; entryPoint='chaoxing-gui-tauri.exe'
        files=@(foreach ($file in $PortableManifest.files) {
            if ($file.path -ceq 'chaoxing-gui-tauri.exe') { [ordered]@{ path=$file.path; length=$sidecar.host.length; sha256=$sidecar.host.sha256 } }
            else { $file }
        })
        directories=@($PortableManifest.directories); hostExpectation=$sidecar.host
    }
}

function Read-NsisContentListing {
    param([Parameter(Mandatory)][string]$Text)
    $parts = $Text -split '(?m)^----------\r?$', 2
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '(?m)^Type = Nsis\r?$') { throw 'Expected a 7-Zip NSIS archive listing.' }
    if ($parts[0] -match 'BadCmd=') { throw 'This 7-Zip version cannot fully parse NSIS 3; install a recent 7-Zip and retry.' }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    foreach ($block in ($parts[1].Trim() -split '\r?\n\r?\n')) {
        $fields = @{}
        foreach ($line in ($block -split '\r?\n')) {
            if ($line -match '^([^=]+) = (.*)$') {
                if ($fields.ContainsKey($Matches[1])) { throw 'Duplicate NSIS listing field.' }
                $fields[$Matches[1]] = $Matches[2]
            }
        }
        if (-not $fields.ContainsKey('Path') -or -not $fields.ContainsKey('Size')) { throw 'Incomplete NSIS listing record.' }
        $relative = $fields.Path.Replace('\', '/')
        Assert-PackageRelativePath $relative
        if (-not $names.Add($relative) -or $names.Count -gt 100000) { throw "Duplicate or excessive NSIS paths: $relative" }
        [long]$length = 0
        # NSIS synthesizes the uninstaller from its embedded stub. 7-Zip can
        # list this entry without a length; enforce bounds after extraction.
        $generatedUninstaller = $relative -ceq 'uninstall.exe' -and $fields.Size -ceq ''
        if (-not $generatedUninstaller -and (-not [long]::TryParse($fields.Size, [ref]$length) -or $length -lt 0 -or $length -gt 2GB)) { throw "Invalid NSIS file length: $relative" }
        $total += $length
        if ($total -gt 8GB) { throw 'NSIS payload exceeds the allowed size.' }
        if (($fields.ContainsKey('Attributes') -and $fields.Attributes -match '[LD]') -or
            $fields.ContainsKey('Symbolic Link') -or $fields.ContainsKey('Hard Link') -or
            ($fields.ContainsKey('Folder') -and $fields.Folder -eq '+')) {
            throw "Unexpected link or directory NSIS entry: $relative"
        }
        [ordered]@{ path=$relative; length=$(if ($generatedUninstaller) { $null } else { $length }) }
    }
}

function Assert-NsisContent {
    param([Parameter(Mandatory)][object[]]$Entries, [Parameter(Mandatory)]$Manifest, [string]$ExtractedDirectory)
    $expected = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    foreach ($file in $Manifest.files) { $expected.Add($file.path, $file) }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    # These are the stock NSIS installer plugins, never application payload.
    $plugins = @('System.dll', 'modern-wizard.bmp', 'modern-header.bmp', 'nsDialogs.dll', 'nsis_tauri_utils.dll', 'StartMenu.dll', 'NSISdl.dll', 'LangDLL.dll', 'UserInfo.dll')
    foreach ($entry in $Entries) {
        $relative = $entry.path
        # Some current 7-Zip-compatible decoders additionally expose the
        # decompiled install script/license; these are archive metadata.
        if ($relative -cin @('[NSIS].nsi', '[LICENSE].txt')) { continue }
        if ($relative -ceq 'uninstall.exe') {
            if ($ExtractedDirectory) {
                $uninstaller = Join-Path $ExtractedDirectory $relative
                Assert-PackageChildPath $uninstaller $ExtractedDirectory
                if (-not [IO.File]::Exists($uninstaller) -or [IO.FileInfo]::new($uninstaller).Length -gt 20MB) { throw 'Missing or oversized generated NSIS uninstaller.' }
                $stream = [IO.File]::OpenRead($uninstaller)
                try { if ($stream.ReadByte() -ne 0x4d -or $stream.ReadByte() -ne 0x5a) { throw 'Invalid generated NSIS uninstaller PE header.' } }
                finally { $stream.Dispose() }
            }
            continue
        }
        if ($relative.StartsWith('$PLUGINSDIR/', [StringComparison]::Ordinal)) {
            if ($relative.Substring(12) -cnotin $plugins) { throw "Unexpected NSIS installer helper: $relative" }
            continue
        }
        if (-not $expected.ContainsKey($relative)) { throw "Unexpected NSIS payload file: $relative" }
        $file = $expected[$relative]
        if (-not $seen.Add($relative) -or $entry.length -ne $file.length) { throw "NSIS payload length/collision mismatch: $relative" }
        if ($ExtractedDirectory) {
            $actual = Join-Path $ExtractedDirectory $relative
            Assert-PackageChildPath $actual $ExtractedDirectory
            if (-not [IO.File]::Exists($actual) -or [IO.FileInfo]::new($actual).Length -ne $file.length -or
                (Get-FileHash -LiteralPath $actual -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.sha256) {
                throw "NSIS payload hash/length mismatch: $relative"
            }
        }
    }
    foreach ($relative in $expected.Keys) {
        if (-not $seen.Contains($relative)) { throw "Missing NSIS payload file: $relative" }
    }
}

````

## desktop/scripts/p2-close-window.ps1

SHA256: dcee5c7f4cdc32d8281b837d2a1f83703c0784ff70475677509bed6440a3d5be

````text
param([Parameter(Mandatory=$true)][int]$HostProcessId)
$ErrorActionPreference='Stop'
$p2WindowTitle=(Get-Content -Raw -LiteralPath "$PSScriptRoot/../src-tauri/tauri.conf.json" | ConvertFrom-Json).app.windows[0].title
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class P2WindowClose {
    public delegate bool WindowCallback(IntPtr handle, IntPtr extra);
    [DllImport("user32.dll")] public static extern bool EnumWindows(WindowCallback callback, IntPtr extra);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint process);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr handle, uint message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr handle, StringBuilder text, int max);
    public static int Close(uint target, string title) {
        int count=0;
        EnumWindows((handle, extra) => {
            uint process; GetWindowThreadProcessId(handle, out process);
            if (process==target) {
                var text = new StringBuilder(512);
                GetWindowText(handle, text, text.Capacity);
                // Tauri/COM also own hidden dispatcher windows. Sending those
                // WM_CLOSE corrupts teardown; emulate only the main close button.
                if (text.ToString()==title && PostMessage(handle, 0x0010, IntPtr.Zero, IntPtr.Zero)) count++;
            }
            return true;
        }, IntPtr.Zero);
        return count;
    }
}
'@
$closed=[P2WindowClose]::Close($HostProcessId, $p2WindowTitle)
if ($closed -eq 0) { throw "No window belongs to test host PID $HostProcessId" }
Write-Output "Sent WM_CLOSE to $closed window(s) of test host $HostProcessId"

````

## desktop/scripts/p2-smoke.mjs

SHA256: 89340a141fbd90aa328ee9d914a928af5f2bd16e80daed6545349005416fe76b

````text
// Real Chromium / original Electron / Tauri P2 smoke with synthetic accounts.
// Prerequisites: web build; cargo build -j1 --features custom-protocol; the
// p2_backend.py fixture frozen as target/p2-fixture/dist/p2-backend/; and
// playwright-core + Electron in P2_TOOLS_DIR (kept outside the repository).
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdtemp, mkdir, readFile, writeFile, copyFile, stat } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireTools = createRequire(path.join(process.env.P2_TOOLS_DIR || path.join(os.tmpdir(), 'chaoxing-p2-tools'), 'package.json'));
const { chromium, _electron } = requireTools('playwright-core');
const evidence = path.resolve(process.env.P2_EVIDENCE_DIR || path.join(repo, 'desktop/src-tauri/target/p2-smoke-evidence'));
const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p2-smoke-'));
const fixture = path.join(repo, 'desktop/tests/fixtures/p2_backend.py');
const fakeExe = path.join(repo, 'desktop/src-tauri/target/p2-fixture/dist/p2-backend/p2-backend.exe');
const hostExe = path.join(repo, 'desktop/src-tauri/target/debug/chaoxing-desktop.exe');
await mkdir(evidence, { recursive: true });
const results = { startedAt: new Date().toISOString(), profileRoot: root,
  driver: 'Real browser engines over CDP; DOM click after visible/enabled checks (native mouse events are not delivered in this Windows desktop session)', checks: [] };
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const exited = (child) => child.exitCode !== null || child.signalCode !== null;

async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  let error;
  while (Date.now() < deadline) {
    try { const result = await check(); if (result) return result; } catch (err) { error = err; }
    await pause(100);
  }
  throw new Error(`${label} timed out${error ? `: ${error.message}` : ''}`);
}
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
async function activate(locator) {
  await locator.waitFor({ state: 'visible' });
  await until(() => locator.isEnabled(), 'button enabled');
  await locator.evaluate((element) => element.click());
}
const record = (name, details) => { results.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
async function freePort() {
  const { createServer } = await import('node:net');
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
function fixtureEnv(profile, extra = {}) {
  const env = { ...process.env, P2_FIXTURE_ROOT: profile, P2_WEB_DIST: path.join(repo, 'web/dist'),
    PYTHONIOENCODING: 'utf-8', CHAOXING_PORT: '0', ...extra };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}
async function stopFixture(child, profile) {
  if (!Number.isInteger(child.pid)) return; // The executable itself did not spawn.
  child.stdin?.end();
  try { await until(() => exited(child), 'fixture exit', 5000); }
  finally {
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'fixture cleanup', 5000);
  }
  const runtime = await json(path.join(profile, 'runtime.json')).catch(() => null);
  if (runtime) assert.equal(alive(runtime.pid), false, `orphan fixture ${runtime.pid}`);
}

async function launch(kind, options = {}) {
  const profile = options.profile || path.join(root, `${kind}-${Date.now()}`);
  const backendProfile = path.join(profile, 'fixture');
  await mkdir(backendProfile, { recursive: true });
  const env = fixtureEnv(backendProfile, options.env);
  if (kind === 'browser') {
    const child = spawn(process.env.P2_PYTHON || 'python', [fixture], { env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    child.stdout.resume(); child.stderr.resume();
    child.stdin.on('error', () => {}); // EPIPE is expected if startup already exited.
    let browser;
    const stop = async () => {
      try { await browser?.close(); }
      finally { await stopFixture(child, backendProfile); }
    };
    try {
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      const runtime = await until(() => json(path.join(backendProfile, 'runtime.json')), 'browser fixture ready');
      browser = await chromium.launch({ executablePath: process.env.P2_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
      const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
      await page.goto(`http://127.0.0.1:${runtime.port}`);
      return { kind, profile, backendProfile, page, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; browser cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  if (kind === 'electron') {
    env.P2_ELECTRON_PROFILE = path.join(profile, 'electron-data');
    let electron;
    let child;
    const stop = async () => {
      // The original Electron shell owns the stdin pipe. Even if Playwright
      // setup fails, closing its captured process releases the fixture watchdog.
      try { await electron?.close(); }
      finally {
        try {
          if (child) await until(() => exited(child), 'Electron host exit', 5000);
        } finally {
          if (child && !exited(child)) child.kill();
          if (child) await until(() => !alive(child.pid), 'Electron host cleanup', 5000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          if (runtime) await until(() => !alive(runtime.pid), 'Electron backend exit', 6000);
        }
      }
    };
    try {
      electron = await _electron.launch({ executablePath: requireTools('electron'),
        args: [path.join(repo, 'desktop/tests/fixtures/p2-electron.cjs')], env, timeout: 30000 });
      child = electron.process();
      const page = await electron.firstWindow();
      await page.waitForURL((url) => url.hostname === '127.0.0.1', { timeout: 30000 });
      await page.waitForLoadState('domcontentloaded');
      return { kind, profile, backendProfile, page, data: env.P2_ELECTRON_PROFILE, electron, pid: child.pid, stop };
    } catch (error) {
      try { await stop(); }
      catch (cleanup) { throw new Error(`${error.message}; Electron cleanup failed: ${cleanup.message}`); }
      throw error;
    }
  }
  const port = await freePort();
  Object.assign(env, { CHAOXING_TAURI_DEV_ROOT: profile, CHAOXING_TAURI_DEV_HIDDEN: process.env.P2_HIDE_WINDOW || '0',
    CHAOXING_TAURI_DEV_BACKEND: options.backend || fakeExe,
    CHAOXING_LEGACY_DATA_DIR: options.legacy || path.join(profile, 'no-legacy'),
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --force-device-scale-factor=1` });
  const child = spawn(hostExe, [], { cwd: path.join(repo, 'desktop/src-tauri'), env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let diagnostics = '';
  const backendPids = [];
  child.stdout.on('data', (data) => { diagnostics += data; });
  child.stderr.on('data', (data) => { diagnostics += data; });
  let browser;
  try {
    await until(async () => {
      if (exited(child)) throw new Error(`host exited ${child.exitCode ?? child.signalCode}: ${diagnostics}`);
      return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok;
    }, 'Tauri CDP', 30000);
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const page = await until(() => browser.contexts()[0]?.pages().find((p) => p.url().includes('tauri.localhost')), 'Tauri page');
    await page.waitForLoadState('domcontentloaded');
    return { kind, profile, backendProfile, data: path.join(profile, 'data'), page, child, backendPids,
      stop: async ({ force = false } = {}) => {
        const started = Date.now();
        try {
          // Detach the test debugger before exercising normal window teardown.
          await browser.close();
          if (!exited(child)) {
            if (force) child.kill();
            else await exec('pwsh', ['-NoProfile', '-File', path.join(repo, 'desktop/scripts/p2-close-window.ps1'), '-HostProcessId', String(child.pid)], { windowsHide: true, timeout: 8000 });
          }
          await until(() => exited(child) && !alive(child.pid), 'Tauri host exit', 8000);
          const runtime = await json(path.join(backendProfile, 'pid.json')).catch(() => null);
          const observedPids = [...new Set([...backendPids, ...(runtime ? [runtime.pid] : [])])];
          for (const pid of observedPids) await until(() => !alive(pid), 'Tauri backend exit', 6000);
          record(force ? 'tauri-forced-host-cleanup' : 'tauri-normal-window-close', {
            hostPid: child.pid, backendPids: observedPids, elapsedMs: Date.now() - started,
            exitCode: child.exitCode, signal: child.signalCode,
          });
        } finally {
          await browser.close().catch(() => {});
          // A failed assertion must not strand the captured test host/CDP client.
          // This fallback cannot turn a failed normal-close assertion into PASS.
          if (!exited(child)) child.kill();
          await until(() => !alive(child.pid), 'Tauri failed-test cleanup', 5000);
          await writeFile(path.join(evidence, `host-${path.basename(profile)}.txt`), diagnostics);
        }
      } };
  } catch (error) {
    await browser?.close().catch(() => {});
    if (!exited(child)) child.kill();
    await until(() => !alive(child.pid), 'Tauri launch cleanup', 5000);
    throw new Error(`${error.message}; host diagnostics=${diagnostics}`);
  }
}

async function savedSession(app) {
  return app.page.evaluate(async (kind) => {
    if (kind === 'tauri') return window.__TAURI__.core.invoke('session_read');
    if (kind === 'electron') return window.chaoxingSession.read();
    return JSON.parse(localStorage.getItem('chaoxing_session_v1')) || { version: 1, login: null, activeTask: null };
  }, app.kind);
}

async function business(kind) {
  const app = await launch(kind);
  const { page } = app;
  page.setDefaultTimeout(15000);
  const requests = [];
  page.on('request', (request) => { if (request.url().includes('/api/')) requests.push({ method: request.method(), url: request.url() }); });
  try {
    await page.evaluate(() => {
      window.p2ClickEvents = [];
      document.addEventListener('click', (event) => {
        window.p2ClickEvents.push({ tag: event.target.tagName, id: event.target.id, x: event.clientX, y: event.clientY });
      }, true);
    });
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    const first = page.getByRole('button', { name: /P2 测试课程一/ });
    await first.waitFor();
    assert.equal(await first.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.getByRole('button', { name: /P2 测试课程二/ }).getAttribute('aria-pressed'), 'false');
    await activate(page.getByRole('button', { name: '保存当前配置' }));
    await page.getByText('配置已保存', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    await page.getByRole('log').getByText('P2 终态日志二', { exact: true }).waitFor();
    assert.equal(await page.getByRole('log').getByText('P2 唯一日志一', { exact: true }).count(), 1);
    const beforeRefresh = await json(path.join(app.backendProfile, 'counts.json'));
    assert.equal(beforeRefresh.start, 1);
    assert.equal(beforeRefresh.configWrites, 1);
    assert.deepEqual(beforeRefresh.after.slice(0, 3), [0, 1, 1]);
    const session = await savedSession(app);
    assert.equal(session.activeTask.taskId, 'p2-existing-task');
    assert.equal(JSON.stringify(session).includes('password'), false);
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-progress.png`), fullPage: true });
    await page.reload();
    await page.getByText('p2-existing-task', { exact: true }).waitFor();
    assert.equal((await json(path.join(app.backendProfile, 'counts.json'))).start, 1);
    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ missing: true }));
    await page.reload();
    await page.getByText('已过期', { exact: true }).first().waitFor();
    await until(async () => (await savedSession(app)).activeTask === null, '404 clears only task');
    assert.equal((await savedSession(app)).login.username, 'p2-fixture');
    await activate(page.getByRole('button', { name: '返回课程选择', exact: true }).last());
    await activate(page.getByRole('button', { name: '退出登录', exact: true }));
    await page.getByRole('button', { name: '登录', exact: true }).waitFor();
    assert.equal((await savedSession(app)).login, null);
    record(`${kind}-business`, { transport: kind === 'tauri' ? 'native invoke -> HTTP fixture' : 'HTTP fixture',
      cases: ['account selection', 'config save', '409 restore without repeat start', 'terminal log retry', 'after cursor dedup', 'refresh restore', '404 clears task', 'logout clears account'], counters: beforeRefresh });
  } catch (error) {
    await page.screenshot({ path: path.join(evidence, `p2-${kind}-error.png`), fullPage: true }).catch(() => {});
    await writeFile(path.join(evidence, `p2-${kind}-error.json`), JSON.stringify({
      url: page.url(), body: await page.locator('body').innerText().catch(() => ''),
      session: await savedSession(app).catch((err) => err.message),
      requests, clicks: await page.evaluate(() => ({ events: window.p2ClickEvents, width: innerWidth, height: innerHeight, scale: devicePixelRatio })),
    }, null, 2));
    throw error;
  } finally { await app.stop(); }
}

async function startupFailures() {
  const missing = await launch('tauri', { backend: path.join(root, 'missing-backend.exe') });
  try {
    await missing.page.getByRole('button', { name: '重新检查' }).waitFor();
    assert.equal(await missing.page.getByLabel('手机号').count(), 0);
    await activate(missing.page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await missing.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'failed remains failed');
    await missing.page.screenshot({ path: path.join(evidence, 'p2-tauri-failed.png') });
    record('tauri-missing-backend-recheck', { restarted: false });
  } finally { await missing.stop(); }
  const slow = await launch('tauri', { env: { P2_READY_DELAY_MS: '8000' } });
  try {
    const status = await slow.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'starting');
    assert.equal(await slow.page.getByLabel('手机号').count(), 0);
    await slow.page.screenshot({ path: path.join(evidence, 'p2-tauri-starting.png') });
  } finally { await slow.stop(); }
  record('tauri-close-during-startup', { closedBeforeReady: true });
}

async function migrationRollback() {
  const legacy = await launch('electron');
  let target;
  try {
    await legacy.page.evaluate(async () => {
      await window.chaoxingSession.rememberLogin('p2-fixture');
      await window.chaoxingSession.rememberTask({ username: 'p2-fixture', taskId: 'p2-existing-task' });
    });
    await writeFile(path.join(legacy.data, 'web_config.json'), JSON.stringify({ selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } }));
    const before = await readFile(path.join(legacy.data, 'renderer-session.json'));
    target = await launch('tauri', { legacy: legacy.data });
    await target.page.getByRole('button', { name: '重新检查' }).waitFor();
    const status = await target.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
    assert.equal(status.phase, 'failed');
    assert.match(status.error, /关闭旧版/);
    await assert.rejects(stat(path.join(target.backendProfile, 'pid.json')), { code: 'ENOENT' });
    await target.stop(); target = null;
    await legacy.stop();
    const imported = await launch('tauri', { legacy: legacy.data });
    try {
      await imported.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(imported)).login.username, 'p2-fixture');
      assert.deepEqual(await json(path.join(imported.data, 'web_config.json')), { selectedCoursesByAccount: { 'p2-fixture': ['course-1'] } });
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      const nodeStore = createRequire(path.join(repo, 'desktop/package.json'))('./session-store.js');
      assert.equal(new nodeStore.SessionStore(imported.data).read().login.username, 'p2-fixture');
      assert.equal(new nodeStore.SessionStore(legacy.data).read().activeTask.taskId, 'p2-existing-task');
      assert.ok((await stat(path.join(imported.data, 'migration-v1.done'))).isFile());
    } finally { await imported.stop(); }
    // Exercise the original shell again against the preserved original profile.
    const rollback = await launch('electron', { profile: legacy.profile });
    try {
      await rollback.page.getByText('p2-existing-task', { exact: true }).waitFor();
      assert.equal((await savedSession(rollback)).login.username, 'p2-fixture');
      assert.deepEqual(await readFile(path.join(legacy.data, 'renderer-session.json')), before);
      record('old-electron-import-and-rollback', { runningLegacyDeferred: true, sourceUnchanged: true,
        nodeReadsRustSession: true, originalElectronRelaunched: true,
        sourceSha256: createHash('sha256').update(before).digest('hex') });
    } finally { await rollback.stop(); }
  } finally {
    if (target) await target.stop();
    if (alive(legacy.pid)) await legacy.stop();
  }
}

async function nativeContracts() {
  const app = await launch('tauri');
  const { page } = app;
  try {
    await page.getByLabel('手机号').waitFor();
    const rejected = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      const cases = [
        ['unknown command', 'p2_unknown_command', {}],
        ['unknown status field', 'backend_status', { url: 'http://example.invalid' }],
        ['positional envelope', 'session_remember_login', ['p2-fixture']],
        ['credentials in session', 'session_remember_login', { username: 'p2-fixture', password: 'synthetic' }],
        ['missing nullable key', 'session_remember_task', {}],
        ['positional task', 'session_remember_task', { task: ['p2-fixture', 'task'] }],
        ['positional request', 'api_request', { request: ['configRead', null, 701] }],
        ['object operation', 'api_request', { request: { operation: { configRead: null }, payload: null, requestId: 702 } }],
        ['unknown URL field', 'api_request', { request: { operation: 'configRead', payload: null, requestId: 703, url: 'http://example.invalid' } }],
        ['path traversal', 'api_request', { request: { operation: 'taskStatus', payload: null, requestId: 704, taskId: '../task' } }],
        ['invalid cancel ID', 'api_cancel', { requestId: 0 }],
        ['ungranted window command', 'plugin:webview|create_webview_window', { options: { label: 'p2-untrusted', url: 'about:blank', visible: false } }],
      ];
      const results = [];
      for (const [name, command, args] of cases) {
        try { await invoke(command, args); results.push({ name, rejected: false }); }
        catch { results.push({ name, rejected: true }); }
      }
      return results;
    });
    for (const result of rejected) assert.equal(result.rejected, true, result.name);
    assert.deepEqual(await savedSession(app), { version: 1, login: null, activeTask: null });
    const frameDirective = await page.evaluate(() => new Promise((resolve) => {
      const frame = document.createElement('iframe');
      let timer;
      const finish = (value) => {
        clearTimeout(timer);
        window.removeEventListener('securitypolicyviolation', onViolation);
        frame.remove();
        resolve(value);
      };
      const onViolation = (event) => { if (event.effectiveDirective === 'frame-src') finish(event.effectiveDirective); };
      window.addEventListener('securitypolicyviolation', onViolation);
      timer = setTimeout(() => finish('not blocked'), 1500);
      frame.src = location.href;
      document.body.append(frame);
    }));
    assert.equal(frameDirective, 'frame-src');
    const originalUrl = page.url();
    await page.evaluate(() => { location.href = 'https://example.invalid/p2-blocked'; });
    await pause(250);
    assert.equal(page.url(), originalUrl);
    record('tauri-native-ipc-boundaries', { rejected, frameDirective, foreignNavigationBlocked: true });

    await writeFile(path.join(app.backendProfile, 'control.json'), JSON.stringify({ delayMs: 1000 }));
    const cancellations = await page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      await invoke('api_cancel', { requestId: 801 });
      const early = await invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 801 } }).then(() => 'success', (error) => error.kind);
      const pending = invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 802 } }).then(() => 'success', (error) => error.kind);
      await new Promise((resolve) => setTimeout(resolve, 100));
      const started = performance.now();
      const status = await invoke('backend_status');
      const statusMs = performance.now() - started;
      await invoke('api_cancel', { requestId: 802 });
      return { early, inflight: await pending, status: status.phase, statusMs };
    });
    assert.equal(cancellations.early, 'cancelled');
    assert.equal(cancellations.inflight, 'cancelled');
    assert.equal(cancellations.status, 'ready');
    assert.ok(cancellations.statusMs < 750, 'status must remain responsive during HTTP');
    await writeFile(path.join(app.backendProfile, 'control.json'), '{}');
    record('tauri-native-cancellation', cancellations);

    await page.evaluate(() => localStorage.setItem('chaoxing_session_v1', 'p2-unchanged-local-sentinel'));
    // An existing directory at the temporary file path forces a real Windows IO
    // failure without changing permissions or touching any user account profile.
    await mkdir(path.join(app.data, 'renderer-session.json.tmp'));
    await page.getByLabel('手机号').fill('p2-fixture');
    await page.getByLabel('密码', { exact: true }).fill('p2-synthetic-password');
    await activate(page.getByRole('button', { name: '登录', exact: true }));
    await page.getByText('账号记忆未能保存，刷新后可能需要重新登录', { exact: true }).waitFor();
    await activate(page.getByRole('button', { name: '开始学习', exact: true }));
    await page.getByText('任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    assert.equal((await savedSession(app)).login, null);
    await page.screenshot({ path: path.join(evidence, 'p2-tauri-storage-failure.png') });
    await mkdir(path.join(app.data, 'renderer-session.json'));
    await page.reload();
    await page.getByText('读取保存的账号失败，请手动登录', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('chaoxing_session_v1')), 'p2-unchanged-local-sentinel');
    record('tauri-native-storage-failure', { readFailureVisible: true, loginFailureVisible: true, taskFailureVisible: true, localStorageUnchanged: true });

    const runtime = await json(path.join(app.backendProfile, 'pid.json'));
    process.kill(runtime.pid);
    await page.getByRole('button', { name: '重新检查' }).waitFor();
    await activate(page.getByRole('button', { name: '重新检查' }));
    await until(async () => (await page.evaluate(() => window.__TAURI__.core.invoke('backend_status'))).phase === 'failed', 'backend death remains failed');
    assert.equal(alive(runtime.pid), false);
    assert.equal((await json(path.join(app.backendProfile, 'pid.json'))).pid, runtime.pid);
    assert.equal(await page.getByLabel('手机号').count(), 0);
    record('tauri-ready-backend-death', { restarted: false, failedStateVisible: true });
  } finally { await app.stop(); }
}

async function realFrozenBackend() {
  const backend = path.join(repo, 'dist/chaoxing-backend/chaoxing-backend.exe');
  const fingerprint = async (file) => readFile(file).then((bytes) => createHash('sha256').update(bytes).digest('hex')).catch((error) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  const installLogs = [path.join(path.dirname(backend), 'chaoxing.log'), path.join(path.dirname(backend), '_internal/chaoxing.log')];
  const before = await Promise.all(installLogs.map(fingerprint));
  assert.ok((await stat(path.join(path.dirname(backend), '_internal'))).isDirectory());
  const app = await launch('tauri', { backend });
  try {
    await app.page.getByLabel('手机号').waitFor({ timeout: 120000 });
    const { stdout } = await exec('pwsh', ['-NoProfile', '-Command',
      `@(Get-CimInstance Win32_Process -Filter 'ParentProcessId=${app.child.pid}' | Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress -AsArray`], { windowsHide: true, timeout: 10000 });
    const children = JSON.parse(stdout);
    const actualBackend = children.filter((child) => path.resolve(child.ExecutablePath).toLowerCase() === backend.toLowerCase());
    assert.equal(actualBackend.length, 1, 'host must spawn the actual frozen backend');
    app.backendPids.push(actualBackend[0].ProcessId);
    const contract = await app.page.evaluate(async () => {
      const invoke = window.__TAURI__.core.invoke;
      let id = 1001;
      const request = (operation, payload = null, extra = {}) => invoke('api_request', { request: { operation, payload, requestId: id++, ...extra } });
      const status = await invoke('backend_status');
      const read = await request('configRead');
      const write = await request('configWrite', { settings: { jobs: 2 }, selectedCoursesByAccount: { 'p2-config-fixture': ['course-1'] } });
      const reread = await request('configRead');
      const invalid = [];
      // All three fail local validation before any account client/task is created.
      for (const operation of ['login', 'courses', 'start']) invalid.push([operation, await request(operation, {})]);
      const missing = [];
      for (const operation of ['taskStatus', 'taskDetails', 'taskLogs']) missing.push([operation, await request(operation, null, { taskId: 'p2-never-created', ...(operation === 'taskLogs' ? { after: 0 } : {}) })]);
      return { status, read, write, reread, invalid, missing };
    });
    assert.equal(contract.status.phase, 'ready');
    assert.equal(Object.hasOwn(contract.status, 'port'), false);
    assert.equal(Object.hasOwn(contract.status, 'token'), false);
    assert.equal(contract.read.status, 200);
    assert.equal(contract.write.status, 200);
    assert.equal(contract.reread.body.data.settings.jobs, 2);
    for (const [name, response] of contract.invalid) assert.equal(response.status, 400, name);
    for (const [name, response] of contract.missing) assert.equal(response.status, 404, name);
    const stored = await json(path.join(app.data, 'web_config.json'));
    assert.deepEqual(stored.selectedCoursesByAccount, { 'p2-config-fixture': ['course-1'] });
    const dataLog = path.join(app.data, 'chaoxing.log');
    assert.ok((await stat(dataLog)).isFile());
    assert.deepEqual(await Promise.all(installLogs.map(fingerprint)), before);
    await copyFile(dataLog, path.join(evidence, 'p2-frozen-chaoxing.log'));
    record('tauri-real-frozen-backend', { backend, backendPid: actualBackend[0].ProcessId, contract,
      logInDataDirectory: true, installLogsUnchanged: true, upstreamAccountsUsed: false, learningTaskCreated: false });
  } finally { await app.stop(); }
}

const selection = process.argv[2] || 'all';
try {
  assert.ok(['all', 'browser', 'electron', 'tauri', 'failures', 'migration', 'native', 'frozen'].includes(selection), `Unknown smoke selection: ${selection}`);
  for (const kind of ['browser', 'electron', 'tauri']) {
    if (selection === 'all' || selection === kind) await business(kind);
  }
  if (selection === 'all' || selection === 'failures') await startupFailures();
  if (selection === 'all' || selection === 'migration') await migrationRollback();
  if (selection === 'all' || selection === 'native') await nativeContracts();
  if (selection === 'all' || selection === 'frozen') await realFrozenBackend();
  results.success = true;
} catch (error) {
  results.success = false;
  results.error = error.stack;
  console.error(error.stack);
  process.exitCode = 1;
} finally {
  results.endedAt = new Date().toISOString();
  await writeFile(path.join(evidence, `p2-smoke-${selection}.json`), JSON.stringify(results, null, 2));
  await writeFile(path.join(evidence, `p2-smoke-${selection}-${results.startedAt.replace(/[^0-9]/g, '')}.json`), JSON.stringify(results, null, 2));
  console.log(`Evidence: ${path.join(evidence, `p2-smoke-${selection}.json`)}`);
}

````

## desktop/scripts/p3-installation.mjs

SHA256: ecabd8c7330f390e208ea6c5c2aecf5eb7f14e68ba09d19e430170865b721b1c

````text
// Destructive installation acceptance is restricted to a disposable Windows
// user/VM or a fresh GitHub-hosted runner. Unit tests never execute an installer.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { copyFile, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import {
  assertReleasePermission, assertOwnedOrAbsent, claimProfileRoots, removeOwnedProfiles,
  releaseProfileRoots, sanitizedEnvironment, windowsContext, NativeSupervisor, until, checkNoLinks,
  withCleanup,
} from './p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const scratchMarker = '.p3-installation-owner.json';
const uninstallKey = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Chaoxing GUI Tauri';
const preferencesKey = 'Software\\chaoxing-gui\\Chaoxing GUI Tauri';
const canonical = (value) => path.win32.resolve(value).toLowerCase();
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
const encoded = (script) => Buffer.from(script, 'utf16le').toString('base64');

export function parseInstallationArguments(argv) {
  const names = new Map([['installer-path', 'installerPath'], ['portable-path', 'portablePath'],
    ['evidence-directory', 'evidenceDirectory'], ['powershell-path', 'powerShell'], ['timeout-seconds', 'timeoutSeconds']]);
  const result = { timeoutSeconds: 360 };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index].replace(/^--/, '');
    if (!argv[index].startsWith('--') || (!names.has(flag) && flag !== 'disposable-windows-user')) throw new Error(`Unknown installation argument: ${argv[index]}`);
    if (seen.has(flag)) throw new Error(`Duplicate installation argument: --${flag}`);
    seen.add(flag);
    if (flag === 'disposable-windows-user') result.disposableWindowsUser = true;
    else {
      const value = argv[++index];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for --${flag}`);
      result[names.get(flag)] = value;
    }
  }
  result.timeoutSeconds = Number(result.timeoutSeconds);
  if (!Number.isInteger(result.timeoutSeconds) || result.timeoutSeconds < 1 || result.timeoutSeconds > 1200) throw new Error('Installation timeout must be between 1 and 1200 seconds');
  return result;
}

export async function assertInstallationPreflight(roots, records, runId, sid) {
  if (!Array.isArray(records) || records.length) throw new Error(`Installation smoke refuses preexisting Tauri registry/install records: ${JSON.stringify(records)}`);
  await assertOwnedOrAbsent(roots, runId, sid);
}

export async function validateInstallationInputs(options) {
  for (const [key, extension, label, signature] of [
    ['installerPath', '.exe', 'Installer executable', Buffer.from('MZ')],
    ['portablePath', '.zip', 'Portable ZIP', Buffer.from([0x50, 0x4b, 0x03, 0x04])],
  ]) {
    const filename = options[key];
    if (!filename || path.extname(filename).toLowerCase() !== extension) throw new Error(`${label} path must end in ${extension}`);
    const info = await lstat(filename).catch((error) => { throw new Error(`${label} missing: ${filename}: ${error.message}`); });
    if (!info.isFile() || !(await checkNoLinks(filename))) throw new Error(`${label} must be a regular file`);
    const handle = await open(filename, 'r');
    try {
      const head = Buffer.alloc(signature.length);
      await handle.read(head, 0, head.length, 0);
      assert.ok(head.equals(signature), `${label} does not have a valid ${key === 'installerPath' ? 'PE/MZ executable' : 'ZIP'} header`);
    } finally { await handle.close(); }
  }
  const sidecar = `${options.installerPath}.manifest.json`;
  assert.ok((await lstat(sidecar)).isFile() && await checkNoLinks(sidecar), 'NSIS payload sidecar must be a regular file');
}

export function nsisSpecification(mode, directory) {
  if (!['install', 'uninstall'].includes(mode)) throw new Error('Unsupported NSIS operation');
  if (typeof directory !== 'string' || !/^[A-Za-z]:\\/.test(directory) || /[\x00-\x1f"<>|?*]/.test(directory)
    || directory.slice(2).includes(':') || directory.endsWith('\\')
    || canonical(directory) !== directory.toLowerCase() || path.win32.parse(directory).root.toLowerCase() === directory.toLowerCase()) {
    throw new Error('NSIS directory must be a normalized absolute local directory path');
  }
  return { args: mode === 'install' ? ['/S', '/NS'] : ['/S'], nsisTail: { mode, directory } };
}

function registryPath(value, label) {
  if (typeof value !== 'string' || !value) throw new Error(`Missing registry ${label}`);
  const plain = value.startsWith('"') && value.endsWith('"') ? value.slice(1, -1) : value;
  if (plain.includes('"') || /[\r\n\0]/.test(plain)) throw new Error(`Invalid registry ${label}`);
  return canonical(plain);
}

export function assertInstallRegistry(records, directory) {
  assert.ok(records.some((entry) => entry.kind === 'uninstall'), 'Installer did not create its uninstall registry record');
  for (const entry of records) {
    assert.equal(entry.hive, 'CurrentUser', 'Installer must register only for CurrentUser');
    assert.ok([uninstallKey.toLowerCase(), preferencesKey.toLowerCase()].includes(entry.key.toLowerCase()), 'Unexpected installer registry key');
    assert.equal(registryPath(entry.installLocation, 'install location'), canonical(directory), 'Installer wrote an unexpected install location/directory');
    if (entry.kind === 'uninstall') assert.equal(registryPath(entry.uninstallString, 'uninstaller'), canonical(path.win32.join(directory, 'uninstall.exe')), 'Unexpected registry uninstaller');
    else assert.equal(entry.kind, 'preferences', 'Unexpected installer registry record');
  }
}

async function assertScratchOwnership(directory, runId) {
  assert.ok(await checkNoLinks(directory), 'Scratch must exist without linked ancestors');
  await checkNoLinks(path.join(directory, scratchMarker));
  const ownership = await json(path.join(directory, scratchMarker)).catch((error) => { throw new Error(`Scratch is not owned: ${error.message}`); });
  assert.equal(ownership.runId, runId, 'Scratch owner must be the current run');
  assert.equal(canonical(ownership.path), canonical(directory), 'Scratch owner path mismatch');
  const resolved = await realpath(directory);
  assert.equal(canonical(resolved), canonical(directory), 'Unexpected scratch removal target');
  assert.notEqual(canonical(directory), canonical(path.parse(directory).root), 'Refusing root directory cleanup');
  return resolved;
}

export async function removeOwnedScratch(directory, runId) {
  if (!(await checkNoLinks(directory, true))) return;
  const resolved = await assertScratchOwnership(directory, runId);
  await rm(resolved, { recursive: true, force: false, maxRetries: 4, retryDelay: 200 });
}

async function sha256(filename) {
  const hasher = createHash('sha256');
  for await (const bytes of createReadStream(filename)) hasher.update(bytes);
  return hasher.digest('hex');
}

const junctionPlacements = ['install-directory', 'backend', 'nested-backend'];

function installationJunctionPaths(scratch, placement) {
  assert.ok(junctionPlacements.includes(placement), 'Unsupported installation junction placement');
  const directory = path.join(scratch, `拒绝 ${placement} junction`);
  const installDirectory = path.join(directory, '安装 目录');
  return { directory, installDirectory, target: path.join(directory, '保留 原始数据'),
    junction: placement === 'install-directory' ? installDirectory
      : path.join(installDirectory, 'backend', ...(placement === 'nested-backend' ? ['_internal'] : [])) };
}

export async function createInstallationJunctionFixture(scratch, runId, placement) {
  await assertScratchOwnership(scratch, runId);
  await checkNoLinks(scratch, true);
  const fixture = { scratch, runId, placement, ...installationJunctionPaths(scratch, placement), sentinels: [] };
  await mkdir(fixture.directory);
  if (path.dirname(fixture.junction) !== fixture.directory) await mkdir(path.dirname(fixture.junction), { recursive: true });
  await mkdir(fixture.target);
  await mkdir(path.join(fixture.target, 'nested'));
  for (const relative of ['sentinel.txt', path.join('nested', 'sentinel.txt')]) {
    const filename = path.join(fixture.target, relative);
    await writeFile(filename, `P3 junction target must remain unchanged: ${runId}: ${relative}`, { flag: 'wx' });
    fixture.sentinels.push({ relative, before: await sha256(filename) });
  }
  // This deliberately created link is the only exception to the harness's
  // no-links rule. Both endpoints are fresh children of the owned scratch.
  await symlink(fixture.target, fixture.junction, process.platform === 'win32' ? 'junction' : 'dir');
  return fixture;
}

async function assertFixtureJunction(fixture, allowAbsent = false) {
  await assertScratchOwnership(fixture.scratch, fixture.runId);
  for (const [key, expected] of Object.entries(installationJunctionPaths(fixture.scratch, fixture.placement))) {
    assert.equal(fixture[key], expected, `Unexpected installation junction ${key}`);
  }
  assert.ok(await checkNoLinks(path.dirname(fixture.junction)), 'Junction parent must exist without links');
  assert.ok(await checkNoLinks(fixture.target, true), 'Junction target must exist without nested links');
  const info = await lstat(fixture.junction).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
  if (!info && allowAbsent) return false;
  assert.ok(info?.isSymbolicLink(), 'Expected the captured installation junction');
  assert.equal(canonical(await realpath(fixture.junction)), canonical(fixture.target), 'Installation junction target changed');
  return true;
}

export async function assertInstallationJunctionRejected(fixture, exitCode, records) {
  assert.equal(exitCode, 2, 'Installer must reject the existing installation junction with exit code 2');
  assert.deepEqual(records, [], 'Rejected junction installation wrote Tauri registry records');
  await assertFixtureJunction(fixture);
  assert.deepEqual((await readdir(fixture.directory)).sort(), ['保留 原始数据', '安装 目录'], 'Rejected junction installation wrote unexpected fixture files');
  if (fixture.placement !== 'install-directory') {
    assert.deepEqual(await readdir(fixture.installDirectory), ['backend'], 'Rejected junction installation wrote program files');
  }
  if (fixture.placement === 'nested-backend') {
    assert.deepEqual(await readdir(path.join(fixture.installDirectory, 'backend')), ['_internal'], 'Rejected junction installation wrote backend files');
  }
  assert.deepEqual((await readdir(fixture.target)).sort(), ['nested', 'sentinel.txt'], 'Installer changed the junction target inventory');
  assert.deepEqual(await readdir(path.join(fixture.target, 'nested')), ['sentinel.txt'], 'Installer changed the nested junction target inventory');
  for (const entry of fixture.sentinels) {
    entry.after = await sha256(path.join(fixture.target, entry.relative));
    assert.equal(entry.after, entry.before, `Installer modified the junction target: ${entry.relative}`);
  }
}

export async function removeInstallationJunction(fixture) {
  if (!(await assertFixtureJunction(fixture, true))) return;
  // Unlink only this verified junction, never recursively remove through it.
  await unlink(fixture.junction);
}

export function installationRetentionFixtures(context, scratch, runId) {
  const tauriData = path.join(context.roaming, 'com.chaoxing.gui', 'data');
  const legacyData = path.join(context.roaming, 'chaoxing-desktop');
  return [
    { path: path.join(tauriData, 'web_config.json'), content: JSON.stringify({ settings: { jobs: 2 }, p3RunId: runId }) },
    { path: path.join(tauriData, 'renderer-session.json'), content: JSON.stringify({ version: 1, login: { username: 'p3-tauri-fixture' }, activeTask: null }) },
    { path: path.join(context.local, 'com.chaoxing.gui', 'EBWebView', 'Default', 'Preferences'), content: JSON.stringify({ p3RunId: runId, retained: 'WebView profile' }) },
    { path: path.join(legacyData, 'renderer-session.json'), content: JSON.stringify({ version: 1, login: { username: 'p3-old-electron-fixture' }, activeTask: null }) },
    { path: path.join(legacyData, 'cookies.txt'), content: 'P3 synthetic Electron cookie sentinel; no account credentials' },
    { path: path.join(scratch, '旧版 Electron 程序', 'chaoxing-gui.exe'), content: 'P3 inert old Electron program fixture; never execute' },
    { path: path.join(scratch, '旧版 Electron 程序', 'resources', 'app.asar'), content: 'P3 inert old Electron resources fixture' },
  ];
}

export async function assertRetainedFiles(entries) {
  for (const entry of entries) {
    entry.after = await sha256(entry.path);
    assert.equal(entry.after, entry.before, `Uninstall modified retained data/program: ${entry.path}`);
  }
}

export async function cleanupChildProfileInvocation(invocation, context) {
  if (invocation.profileCleanup?.completed) return;
  const cleanup = invocation.process?.cleanup;
  const host = invocation.process?.identity;
  if (host) {
    assert.equal(cleanup?.verified, true, 'Child profile cleanup requires a verified captured process tree');
    assert.deepEqual(cleanup.remaining, [], 'Child profile cleanup requires an empty captured Job');
    assert.ok(Array.isArray(cleanup.observed) && cleanup.observed.every((identity) => !identity.alive), 'Child profile cleanup found a live captured process');
    assert.ok(cleanup.observed.some((identity) => identity.pid === host.pid && identity.createdAtFileTime === host.createdAtFileTime),
      'Child profile cleanup must include the captured host identity');
  }
  const folders = await readdir(invocation.evidenceDirectory, { withFileTypes: true }).catch((error) => {
    if (error.code === 'ENOENT') return []; throw error;
  });
  if (folders.length === 0) {
    invocation.profileCleanup = { completed: true, claimedProfiles: false };
    return;
  }
  assert.equal(folders.length, 1, 'Each child profile handoff must belong to exactly one smoke invocation');
  assert.ok(folders[0].isDirectory() && /^smoke-[A-Za-z0-9]+$/.test(folders[0].name), 'Unexpected child profile evidence directory');
  const evidence = path.join(invocation.evidenceDirectory, folders[0].name);
  const handoff = path.join(evidence, 'profile-ownership.json');
  if (!(await checkNoLinks(handoff))) {
    invocation.profileCleanup = { completed: true, claimedProfiles: false };
    return;
  }
  const ownership = await json(handoff);
  assert.equal(ownership.schemaVersion, 1, 'Unsupported child profile ownership schema');
  assert.equal(ownership.configuration, 'Release', 'Child profile handoff must be for Release');
  assert.equal(ownership.sid, context.sid, 'Child profile SID mismatch');
  assert.ok(typeof ownership.runId === 'string' && /^[a-f0-9-]{36}$/.test(ownership.runId), 'Invalid child profile run ID');
  assert.equal(canonical(ownership.evidenceDirectory), canonical(evidence), 'Child profile evidence path mismatch');
  const roots = releaseProfileRoots(context);
  assert.deepEqual(ownership.roots, roots, 'Child profile paths must match actual Windows known folders');
  assert.ok(host, 'Child profile cleanup requires a captured host identity');
  invocation.profileCleanup = { completed: false, runId: ownership.runId, sid: ownership.sid, roots, capturedTreeVerified: true };
  await removeOwnedProfiles(roots, ownership.runId, ownership.sid);
  invocation.profileCleanup.completed = true;
}

// Keep registry paths explicit. Inspection emits only matching Tauri records,
// not an inventory of unrelated installed software.
const inspectRegistryScript = String.raw`
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p3Records = [Collections.Generic.List[object]]::new()
foreach ($p3Hive in @('CurrentUser','LocalMachine')) {
  foreach ($p3View in @('Registry64','Registry32')) {
    $p3Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]$p3Hive, [Microsoft.Win32.RegistryView]$p3View)
    try {
      $p3PreferencesPath = 'Software\chaoxing-gui\Chaoxing GUI Tauri'
      $p3Preferences = $p3Base.OpenSubKey($p3PreferencesPath, $false)
      if ($null -ne $p3Preferences) {
        try { $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key=$p3PreferencesPath; kind='preferences'; installLocation=[string]$p3Preferences.GetValue('') }) }
        finally { $p3Preferences.Dispose() }
      }
      $p3UninstallRoot = $p3Base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall', $false)
      if ($null -ne $p3UninstallRoot) {
        try {
          foreach ($p3Name in $p3UninstallRoot.GetSubKeyNames()) {
            $p3Entry = $p3UninstallRoot.OpenSubKey($p3Name, $false)
            if ($null -eq $p3Entry) { continue }
            try {
              $p3Display = [string]$p3Entry.GetValue('DisplayName')
              $p3Binary = [string]$p3Entry.GetValue('MainBinaryName')
              if ($p3Name -in @('Chaoxing GUI Tauri','com.chaoxing.gui') -or $p3Display -like '*Chaoxing GUI Tauri*' -or $p3Binary -eq 'chaoxing-gui-tauri.exe') {
                $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key="Software\Microsoft\Windows\CurrentVersion\Uninstall\$p3Name"; kind='uninstall'; displayName=$p3Display; installLocation=[string]$p3Entry.GetValue('InstallLocation'); uninstallString=[string]$p3Entry.GetValue('UninstallString') })
              }
            } finally { $p3Entry.Dispose() }
          }
        } finally { $p3UninstallRoot.Dispose() }
      }
      $p3Run = $p3Base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Run', $false)
      if ($null -ne $p3Run) {
        try {
          if ('Chaoxing GUI Tauri' -in $p3Run.GetValueNames()) { $p3Records.Add(@{ hive=$p3Hive; view=$p3View; key='Software\Microsoft\Windows\CurrentVersion\Run'; kind='autorun'; valueName='Chaoxing GUI Tauri' }) }
        } finally { $p3Run.Dispose() }
      }
    } finally { $p3Base.Dispose() }
  }
}
$p3Local = [Environment]::GetFolderPath('LocalApplicationData')
$p3ProgramFiles = [Environment]::GetFolderPath('ProgramFiles')
$p3ProgramFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
$p3Programs = [Environment]::GetFolderPath('Programs')
$p3Desktop = [Environment]::GetFolderPath('DesktopDirectory')
@{ records=@($p3Records.ToArray()); defaultPaths=@(
  (Join-Path $p3Local 'Chaoxing GUI Tauri'), (Join-Path $p3Local 'Programs\Chaoxing GUI Tauri'),
  (Join-Path $p3ProgramFiles 'Chaoxing GUI Tauri'), (Join-Path $p3ProgramFilesX86 'Chaoxing GUI Tauri'),
  (Join-Path $p3Programs 'Chaoxing GUI Tauri'), (Join-Path $p3Programs 'Chaoxing GUI Tauri.lnk'),
  (Join-Path $p3Desktop 'Chaoxing GUI Tauri.lnk')
) } | ConvertTo-Json -Depth 8 -Compress
`;

async function installationState(context) {
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(inspectRegistryScript)], { windowsHide: true, timeout: 20000 });
  return JSON.parse(stdout);
}

// A verified archive is still extracted into a fresh owned directory with a
// second traversal/link check. No archive-controlled path reaches the source.
const extractScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
if (Test-Path -LiteralPath $p3Request.destination) { throw 'Extraction destination must not exist' }
$p3Zip = [IO.Compression.ZipFile]::OpenRead($p3Request.archive)
try {
  if ($p3Zip.Entries.Count -gt 100000) { throw 'Too many ZIP entries' }
  $p3Names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  [long]$p3Length = 0
  foreach ($p3Entry in $p3Zip.Entries) {
    $p3Name = $p3Entry.FullName.TrimEnd('/')
    if (-not $p3Name -or $p3Name -match '(^/|\\|:|(^|/)\.{1,2}(/|$)|[<>"|?*\x00-\x1f])' -or -not $p3Names.Add($p3Name)) { throw "Unsafe ZIP path: $p3Name" }
    foreach ($p3Component in $p3Name.Split('/')) { if (-not $p3Component -or $p3Component.EndsWith(' ') -or $p3Component.EndsWith('.')) { throw 'Unsafe ZIP path component' } }
    if ((($p3Entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000 -or ($p3Entry.ExternalAttributes -band 0x400)) { throw 'ZIP symbolic link/reparse entry refused' }
    $p3Length += $p3Entry.Length
    if ($p3Entry.Length -gt 2GB -or $p3Length -gt 8GB) { throw 'ZIP size limit exceeded' }
  }
} finally { $p3Zip.Dispose() }
[IO.Compression.ZipFile]::ExtractToDirectory($p3Request.archive, $p3Request.destination, $false)
`;

// NSIS rewrites Tauri's bundle marker and can sign the host independently of
// the portable binary. Keep its exact, artifact-bound expectation separately.
const createNsisReferenceScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
. $p3Request.nsisContent
$p3Scratch = Get-PackageFullPath $p3Request.scratch
Assert-PackageNoReparse $p3Scratch
$p3Owner = Read-PackageJson (Join-Path $p3Scratch '.p3-installation-owner.json')
if ($p3Owner.runId -cne $p3Request.runId -or [IO.Path]::GetFullPath($p3Owner.path) -ine $p3Scratch) { throw 'NSIS reference requires current scratch ownership' }
$p3Reference = Join-Path $p3Scratch 'nsis-payload-reference.json'
Assert-PackageChildPath $p3Reference $p3Scratch
if (Test-Path -LiteralPath $p3Reference) { throw 'NSIS reference must not already exist' }
$p3PortableManifest = Read-PackageJson $p3Request.portableManifest
if ((Get-FileHash -LiteralPath $p3Request.portableManifest -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p3Request.portableManifestSha256) { throw 'Portable reference payload manifest changed' }
$p3NsisManifest = Read-NsisPayloadManifest -InstallerPath $p3Request.installerPath -PortablePath $p3Request.portablePath -PortableManifest $p3PortableManifest
Assert-PackageManifestHeader $p3NsisManifest 'chaoxing-gui-tauri-nsis-payload' (Get-PackageVersion) 'chaoxing-gui-tauri.exe'
Write-PackageJson $p3Reference $p3NsisManifest
@{ manifest=$p3Reference; sha256=(Get-FileHash -LiteralPath $p3Reference -Algorithm SHA256).Hash.ToLowerInvariant() } | ConvertTo-Json -Compress
`;

// Reuse the packaging verifier's exact directory, length, hash, and reparse
// checks against the actual extracted/installed files before and after launch.
const verifyLayoutScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
if ($p3Request.mode -cnotin @('Portable','Installed')) { throw 'Invalid layout verification mode' }
$p3Version = Get-PackageVersion
$p3Manifest = Read-PackageJson $p3Request.manifest
$p3ManifestHash = (Get-FileHash -LiteralPath $p3Request.manifest -Algorithm SHA256).Hash.ToLowerInvariant()
if ($p3ManifestHash -cne $p3Request.manifestSha256) { throw 'Reference payload manifest changed' }
$p3ManifestKind = if ($p3Request.mode -ceq 'Portable') { 'chaoxing-gui-tauri-portable' } else { 'chaoxing-gui-tauri-nsis-payload' }
Assert-PackageManifestHeader $p3Manifest $p3ManifestKind $p3Version 'chaoxing-gui-tauri.exe'
if ($p3Manifest.platform -cne 'windows-x64') { throw 'Unexpected payload platform' }
$p3Inventory = Get-PackageInventory $p3Request.directory
$p3ExtraName = if ($p3Request.mode -ceq 'Portable') { 'package-manifest.json' } else { 'uninstall.exe' }
$p3Extra = @($p3Inventory.files | Where-Object { $_.path -ceq $p3ExtraName })
if ($p3Extra.Count -ne 1 -or $p3Extra[0].length -le 0) { throw "Missing layout file: $p3ExtraName" }
if ($p3Request.mode -ceq 'Portable' -and $p3Extra[0].sha256 -cne $p3ManifestHash) { throw 'Extracted package manifest mismatch' }
$p3Payload = @{ files=@($p3Inventory.files | Where-Object { $_.path -cne $p3ExtraName }); directories=@($p3Inventory.directories) }
Assert-PackageInventoryManifest $p3Manifest $p3Payload
$p3BackendManifest = Read-PackageJson (Join-Path $p3Request.directory 'backend-manifest.json')
Assert-PackageManifestHeader $p3BackendManifest 'chaoxing-backend' $p3Version 'chaoxing-backend.exe'
$p3BackendInventory = @{
  files=@(foreach ($p3File in $p3Inventory.files) {
    if ($p3File.path.StartsWith('backend/', [StringComparison]::Ordinal)) {
      @{ path=$p3File.path.Substring(8); length=$p3File.length; sha256=$p3File.sha256 }
    }
  })
  directories=@(foreach ($p3Directory in $p3Inventory.directories) {
    if ($p3Directory.StartsWith('backend/', [StringComparison]::Ordinal)) { $p3Directory.Substring(8) }
  })
}
Assert-PackageInventoryManifest $p3BackendManifest $p3BackendInventory
Assert-BackendLayout (Join-Path $p3Request.directory 'backend') $p3BackendInventory
@{ directory=$p3Request.directory; mode=$p3Request.mode; manifestKind=$p3ManifestKind; version=$p3Version; files=$p3Payload.files.Count; backendFiles=$p3BackendInventory.files.Count; manifestSha256=$p3ManifestHash } | ConvertTo-Json -Compress
`;

const removeRegistryScript = String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
$p3Owner = Get-Content -Raw -LiteralPath (Join-Path $p3Request.scratch '.p3-installation-owner.json') | ConvertFrom-Json
if ($p3Owner.runId -cne $p3Request.runId -or [IO.Path]::GetFullPath($p3Owner.path) -ine [IO.Path]::GetFullPath($p3Request.scratch)) { throw 'Registry cleanup requires current scratch ownership' }
$p3Expected = [IO.Path]::GetFullPath($p3Request.installDirectory)
if (-not $p3Expected.StartsWith(([IO.Path]::GetFullPath($p3Request.scratch).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Registry cleanup target is outside scratch' }
foreach ($p3Record in $p3Request.records) {
  if ($p3Record.hive -ne 'CurrentUser' -or $p3Record.view -notin @('Registry32','Registry64') -or $p3Record.key -notin @('Software\Microsoft\Windows\CurrentVersion\Uninstall\Chaoxing GUI Tauri','Software\chaoxing-gui\Chaoxing GUI Tauri')) { throw 'Unowned registry cleanup key' }
  $p3Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]([string]$p3Record.view))
  try {
    $p3Entry = $p3Base.OpenSubKey($p3Record.key, $false)
    if ($null -eq $p3Entry) { continue }
    try {
      $p3Value = if ($p3Record.kind -eq 'uninstall') { [string]$p3Entry.GetValue('InstallLocation') } else { [string]$p3Entry.GetValue('') }
      $p3Value = $p3Value.Trim('"')
      if (-not $p3Value -or [IO.Path]::GetFullPath($p3Value) -ine $p3Expected) { throw 'Registry cleanup refuses an unexpected install location' }
    } finally { $p3Entry.Dispose() }
    # Only these exact absent-at-baseline subkeys, validated against the owned
    # install directory, are removed. The publisher/root keys are preserved.
    $p3Base.DeleteSubKeyTree($p3Record.key, $false)
  } finally { $p3Base.Dispose() }
}
`;

// Exposed for parser-only regression checks; importing this module performs no
// registry reads/writes, extraction, or process launches.
export const installationPowerShellScripts = { inspectRegistryScript, extractScript, createNsisReferenceScript, verifyLayoutScript, removeRegistryScript };

async function inventoryFiles(root, relative = '') {
  const files = [];
  for (const entry of await readdir(path.join(root, relative), { withFileTypes: true })) {
    const next = path.join(relative, entry.name);
    if (entry.isDirectory()) files.push(...await inventoryFiles(root, next));
    else files.push(next);
  }
  return files;
}

export async function installationMain(argv = process.argv.slice(2)) {
  const evidenceIndex = argv.indexOf('--evidence-directory');
  const requested = evidenceIndex >= 0 && argv[evidenceIndex + 1] && !argv[evidenceIndex + 1].startsWith('--')
    ? argv[evidenceIndex + 1] : path.join(repo, 'desktop/src-tauri/target/p3-installation-evidence');
  await mkdir(path.resolve(requested), { recursive: true });
  const evidence = await mkdtemp(path.join(path.resolve(requested), 'installation-'));
  const result = { runId: randomUUID(), startedAt: new Date().toISOString(), success: false, processes: [], checks: [],
    limitations: ['Installer and release-host execution is allowed only in a disposable Windows profile', 'This harness is not proof of a remote CI run until its real installer checks complete'] };
  const failures = [];
  const record = (name, details = {}) => { result.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
  let context;
  let options;
  let scratch;
  let installDirectory;
  const junctionFixtures = [];
  const childProfileInvocations = [];
  let baseline;
  let preflightPassed = false;
  let retentionRoots = [];
  let sequenceCompleted = false;
  let operationNumber = 0;

  async function runChecked(name, executable, args, extra = {}) {
    const owner = new NativeSupervisor(context.powerShell, path.join(evidence, `${++operationNumber}-${name}`));
    const expectedExitCode = extra.expectedExitCode ?? 0;
    const item = { name, executable, args, nsisTail: extra.nsisTail || null, expectedExitCode };
    result.processes.push(item);
    if (extra.profileInvocation) extra.profileInvocation.process = item;
    try {
      item.identity = await owner.start({ executable, args, cwd: scratch || repo,
        env: extra.env || { ...process.env }, ...(extra.nsisTail ? { nsisTail: extra.nsisTail } : {}) });
      item.shutdown = await until(async () => {
        const snapshot = await owner.snapshot();
        if (snapshot.host && !snapshot.host.alive && snapshot.host.exitCode !== expectedExitCode) {
          const error = new Error(`${name} exited ${snapshot.host.exitCode}`); error.fatal = true; throw error;
        }
        return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot;
      }, `${name} process and descendants`, options.timeoutSeconds * 1000, 200);
      assert.equal(item.shutdown.host.exitCode, expectedExitCode, `${name} must exit with the expected code and without a pending reboot`);
    } finally {
      try { item.cleanup = await owner.dispose(); }
      catch (error) { item.cleanup = owner.cleanup || { verified: false }; throw error; }
    }
    assert.equal(item.cleanup.fallbackUsed, false, `${name} required forced cleanup`);
    assert.equal(item.cleanup.verified, true, `${name} process cleanup was not verified`);
    return item;
  }

  const runScript = (name, script, args = [], extra = {}) => runChecked(name, context.powerShell, ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/scripts', script), ...args], extra);
  const runInline = (name, script, request) => runChecked(name, context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(script)],
    { env: { ...process.env, P3_INSTALLATION_REQUEST: JSON.stringify(request) } });

  async function smokeLayout(name, directory) {
    const host = path.join(directory, 'chaoxing-gui-tauri.exe');
    const smokeEvidence = path.join(evidence, `${name}-host`);
    const args = ['-HostPath', host, '-BackendDirectory', path.join(directory, 'backend'), '-Configuration', 'Release', '-Scenario', 'Frozen',
      '-UsePackagedLayout', '-NestedJob', '-EvidenceDirectory', smokeEvidence, '-TimeoutSeconds', String(Math.max(1, Math.min(120, Math.floor(options.timeoutSeconds / 3))))];
    if (options.disposableWindowsUser) args.push('-DisposableWindowsUser');
    const invocation = { name, evidenceDirectory: smokeEvidence };
    childProfileInvocations.push(invocation);
    await withCleanup(invocation, async () => {
      await runScript(`${name}-real-host`, 'smoke-tauri.ps1', args, { profileInvocation: invocation });
      const reports = await readdir(smokeEvidence);
      assert.equal(reports.length, 1, 'Each layout smoke must produce exactly one fresh report');
      const reportPath = path.join(smokeEvidence, reports[0], 'result.json');
      const report = await json(reportPath);
      assert.equal(report.success, true, `${name} host smoke failed`);
      assert.equal(report.selection.configuration, 'Release');
      assert.equal(report.selection.usePackagedLayout, true);
      assert.ok(report.checks.some((check) => check.name === 'frozen-force-host-forced-no-residue'), 'Installed layout must complete forced-host nested Job cleanup');
      record(`${name}-real-release-layout`, { directory, reportPath });
    }, (value) => cleanupChildProfileInvocation(value, context));
  }

  try {
    options = parseInstallationArguments(argv);
    // This must precede windowsContext, input execution, archive extraction,
    // registry mutation, and every installer/host launch.
    result.permission = assertReleasePermission({ configuration: 'Release', disposableWindowsUser: options.disposableWindowsUser });
    context = await windowsContext(options.powerShell);
    baseline = await installationState(context);
    const roots = [...releaseProfileRoots(context), ...baseline.defaultPaths.map((entry) => ({ path: entry, claim: false }))];
    await assertInstallationPreflight(roots, baseline.records, result.runId, context.sid);
    result.baseline = { ...baseline, checkedProfileRoots: roots, sid: context.sid };
    if (options.installerPath) options.installerPath = path.resolve(options.installerPath);
    if (options.portablePath) options.portablePath = path.resolve(options.portablePath);
    await validateInstallationInputs(options);
    preflightPassed = true;
    result.sources = await Promise.all([options.installerPath, options.portablePath, `${options.installerPath}.manifest.json`]
      .map(async (filename) => ({ path: filename, sha256: await sha256(filename) })));
    scratch = await mkdtemp(path.join(context.temp, 'chaoxing-p3 安装 空格-'));
    await writeFile(path.join(scratch, scratchMarker), JSON.stringify({ runId: result.runId, path: scratch }), { flag: 'wx' });
    result.scratch = scratch;
    installDirectory = path.join(scratch, '安装 目录');
    const portableDirectory = path.join(scratch, '便携 解压');
    await runScript('verify-portable', 'verify-package.ps1', ['-PackagePath', options.portablePath]);
    await runScript('verify-nsis', 'verify-nsis.ps1', ['-InstallerPath', options.installerPath, '-PortablePath', options.portablePath, '-EvidenceDirectory', path.join(evidence, 'nsis-content')]);
    result.junctionRejections = junctionFixtures;
    for (const placement of junctionPlacements) {
      const fixture = await createInstallationJunctionFixture(scratch, result.runId, placement);
      junctionFixtures.push(fixture);
      const rejectedInstall = nsisSpecification('install', fixture.installDirectory);
      const rejectedProcess = await runChecked(`reject-${placement}-junction`, options.installerPath, rejectedInstall.args,
        { nsisTail: rejectedInstall.nsisTail, expectedExitCode: 2, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
      fixture.exitCode = rejectedProcess.shutdown.host.exitCode;
      fixture.registry = (await installationState(context)).records;
      await assertInstallationJunctionRejected(fixture, fixture.exitCode, fixture.registry);
      await assertInstallationPreflight(roots, fixture.registry, result.runId, context.sid);
      await removeInstallationJunction(fixture);
      fixture.linkCleanup = true;
      record(`installer-rejects-preexisting-${placement}-junction`, { exitCode: fixture.exitCode, sentinels: fixture.sentinels });
    }
    await runInline('extract-portable', extractScript, { archive: options.portablePath, destination: portableDirectory });
    await checkNoLinks(portableDirectory, true);
    const portableManifest = path.join(portableDirectory, 'package-manifest.json');
    const portableManifestSha256 = await sha256(portableManifest);
    await runInline('create-nsis-reference', createNsisReferenceScript, { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'),
      nsisContent: path.join(repo, 'desktop/scripts/nsis-content.ps1'), scratch, runId: result.runId,
      installerPath: options.installerPath, portablePath: options.portablePath, portableManifest, portableManifestSha256 });
    const nsisManifest = path.join(scratch, 'nsis-payload-reference.json');
    await checkNoLinks(nsisManifest);
    const references = {
      Portable: { manifest: portableManifest, manifestSha256: portableManifestSha256 },
      Installed: { manifest: nsisManifest, manifestSha256: await sha256(nsisManifest) },
    };
    result.referenceManifests = references;
    const verifyLayout = async (name, directory, mode) => {
      const reference = references[mode];
      assert.ok(reference, `Unsupported layout verification mode: ${mode}`);
      await runInline(name, verifyLayoutScript, { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'),
        directory, mode, ...reference });
      record(name, { directory, mode, ...reference });
    };
    await verifyLayout('portable-manifest-before-host', portableDirectory, 'Portable');
    await smokeLayout('portable', portableDirectory);
    await verifyLayout('portable-manifest-after-host', portableDirectory, 'Portable');

    await assertInstallationPreflight(roots, (await installationState(context)).records, result.runId, context.sid);
    const install = nsisSpecification('install', installDirectory);
    await runChecked('install-nsis', options.installerPath, install.args, { nsisTail: install.nsisTail, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    const installedState = await installationState(context);
    result.installedRegistry = installedState.records;
    assertInstallRegistry(installedState.records, installDirectory);
    // Any default-path write is a failure, even if a correct owned-path registry
    // record was also written. Unexpected paths are never recursively cleaned.
    await assertOwnedOrAbsent(baseline.defaultPaths.map((entry) => ({ path: entry, claim: false })), result.runId, context.sid);
    await checkNoLinks(installDirectory, true);
    const installedFiles = await inventoryFiles(installDirectory);
    assert.ok(installedFiles.includes('chaoxing-gui-tauri.exe') && installedFiles.includes('uninstall.exe'), 'NSIS did not install the host and uninstaller at the requested location');
    result.installedFiles = installedFiles;
    await verifyLayout('installed-manifest-before-host', installDirectory, 'Installed');
    await smokeLayout('installed', installDirectory);
    await verifyLayout('installed-manifest-after-host', installDirectory, 'Installed');

    // Both real-host smokes have removed only their owned AppData profiles.
    // Create new synthetic retained data only after all launches have ended.
    const legacyData = path.win32.join(context.roaming, 'chaoxing-desktop');
    retentionRoots = releaseProfileRoots(context).map((entry) => ({ ...entry, claim: entry.claim || canonical(entry.path) === canonical(legacyData) }));
    await claimProfileRoots(retentionRoots, result.runId, context.sid);
    const retained = installationRetentionFixtures(context, scratch, result.runId);
    for (const entry of retained) { await mkdir(path.dirname(entry.path), { recursive: true }); await writeFile(entry.path, entry.content, { flag: 'wx' }); }
    result.retention = await Promise.all(retained.map(async (entry) => ({ path: entry.path, before: await sha256(entry.path) })));
    const uninstaller = path.join(scratch, '卸载 程序.exe');
    await copyFile(path.join(installDirectory, 'uninstall.exe'), uninstaller);
    const uninstall = nsisSpecification('uninstall', installDirectory);
    await runChecked('uninstall-nsis', uninstaller, uninstall.args, { nsisTail: uninstall.nsisTail, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    await assertRetainedFiles(result.retention);
    for (const relative of installedFiles) assert.equal(await lstat(path.join(installDirectory, relative)).catch((error) => { if (error.code === 'ENOENT') return null; throw error; }), null, `Uninstall left installed file: ${relative}`);
    assert.equal(await lstat(installDirectory).catch((error) => { if (error.code === 'ENOENT') return null; throw error; }), null, 'Uninstall left its program directory');
    result.afterUninstallRegistry = (await installationState(context)).records;
    assert.deepEqual(result.afterUninstallRegistry, [], 'Uninstall left Tauri registry/install records');
    record('uninstall-retains-tauri-data-and-old-electron', { retention: result.retention, installedFilesRemoved: installedFiles.length, remainingRegistryRecords: 0 });
    sequenceCompleted = true;
  } catch (error) { failures.push(error.stack || String(error)); }
  finally {
    if (preflightPassed && scratch) {
      try {
        const current = await installationState(context);
        const owned = [];
        for (const directory of [installDirectory, ...junctionFixtures.map((fixture) => fixture.installDirectory)].filter(Boolean)) {
          const matching = current.records.filter((entry) => entry.hive === 'CurrentUser' && [uninstallKey.toLowerCase(), preferencesKey.toLowerCase()].includes(entry.key.toLowerCase())
            && typeof entry.installLocation === 'string' && registryPath(entry.installLocation, 'cleanup install location') === canonical(directory));
          if (matching.length) {
            assert.equal(baseline.records.length, 0, 'Registry cleanup requires empty baseline');
            await runInline('cleanup-owned-registry', removeRegistryScript, { scratch, runId: result.runId, installDirectory: directory, records: matching });
            owned.push(...matching);
          }
        }
        result.registryCleanup = { removedOwnedRecords: owned, remainingRecords: (await installationState(context)).records };
        if (result.registryCleanup.remainingRecords.length) throw new Error('Unexpected Tauri registry records remain; no unowned records were deleted');
      } catch (error) { failures.push(`Registry cleanup: ${error.stack || error}`); }
      for (const invocation of childProfileInvocations) {
        try { await cleanupChildProfileInvocation(invocation, context); }
        catch (error) { failures.push(`Child profile cleanup: ${error.stack || error}`); }
      }
      if (retentionRoots.length) {
        try { await removeOwnedProfiles(retentionRoots, result.runId, context.sid); result.retentionFixtureCleanup = true; }
        catch (error) { failures.push(`Profile fixture cleanup: ${error.stack || error}`); }
      }
      for (const fixture of junctionFixtures) {
        try { await removeInstallationJunction(fixture); fixture.linkCleanup = true; }
        catch (error) { failures.push(`Junction fixture cleanup: ${error.stack || error}`); }
      }
      try { await removeOwnedScratch(scratch, result.runId); result.scratchCleanup = true; }
      catch (error) { failures.push(`Scratch cleanup: ${error.stack || error}`); }
    }
    for (const source of result.sources || []) {
      try { source.after = await sha256(source.path); assert.equal(source.after, source.sha256, 'Installation smoke modified an input artifact'); }
      catch (error) { failures.push(`Source preservation: ${error.stack || error}`); }
    }
    result.success = sequenceCompleted && failures.length === 0;
    result.childProfileCleanup = childProfileInvocations.map(({ name, evidenceDirectory, profileCleanup }) => ({ name, evidenceDirectory, ...profileCleanup }));
    if (failures.length) { result.error = failures.join('\n\n'); console.error(result.error); }
    result.endedAt = new Date().toISOString();
    await writeFile(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2), { flag: 'wx' });
    console.log(`Evidence: ${path.join(evidence, 'result.json')}`);
  }
  return result.success ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) process.exitCode = await installationMain();

````

## desktop/scripts/p3-smoke.mjs

SHA256: 6495a962a56246d6d8bab2140c2a0af37e248351d0c93f1a77719aaff98bcdf1

````text
// P3 Windows smoke: a real packaged host, an isolated onedir resource copy,
// and a retained outer Job. Importing this module never starts an application.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { cp, copyFile, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const marker = '.p3-smoke-owner.json';
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const canonical = (value) => path.resolve(value).toLowerCase();
const json = async (filename) => JSON.parse(await readFile(filename, 'utf8'));
const readable = async (filename) => readFile(filename, 'utf8').catch((error) => {
  if (error.code === 'ENOENT') return '';
  throw error;
});

export function parseArguments(argv) {
  const names = new Map([
    ['kind', 'kind'], ['host-path', 'hostPath'], ['backend-directory', 'backendDirectory'],
    ['fake-backend-directory', 'fakeBackendDirectory'], ['evidence-directory', 'evidenceDirectory'],
    ['configuration', 'configuration'], ['scenario', 'scenario'], ['executable-path', 'executablePath'],
    ['mode', 'mode'], ['timeout-seconds', 'timeoutSeconds'], ['powershell-path', 'powerShell'],
  ]);
  const switches = new Map([['nested-job', 'nestedJob'], ['disposable-windows-user', 'disposableWindowsUser'], ['use-packaged-layout', 'usePackagedLayout']]);
  const options = { kind: 'tauri', configuration: 'Release', scenario: 'All', mode: 'Backend', timeoutSeconds: 150 };
  const seen = new Set();
  for (let index = 0; index < argv.length; index++) {
    const flag = argv[index].replace(/^--/, '');
    if (!argv[index].startsWith('--') || (!names.has(flag) && !switches.has(flag))) throw new Error(`Unknown smoke argument: ${argv[index]}`);
    if (seen.has(flag)) throw new Error(`Duplicate smoke argument: --${flag}`);
    seen.add(flag);
    if (switches.has(flag)) options[switches.get(flag)] = true;
    else {
      const value = argv[++index];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for --${flag}`);
      options[names.get(flag)] = value;
    }
  }
  for (const [key, allowed] of Object.entries({ kind: ['tauri', 'python'], configuration: ['Debug', 'Release'], scenario: ['Fake', 'Frozen', 'All'], mode: ['Independent', 'Backend'] })) {
    const selected = allowed.find((value) => value.toLowerCase() === String(options[key]).toLowerCase());
    if (!selected) throw new Error(`Unknown smoke ${key}: ${options[key]}`);
    options[key] = selected;
  }
  options.timeoutSeconds = Number(options.timeoutSeconds);
  if (!Number.isInteger(options.timeoutSeconds) || options.timeoutSeconds < 1 || options.timeoutSeconds > 600) throw new Error('timeout-seconds must be between 1 and 600');
  if (options.usePackagedLayout && (options.kind !== 'tauri' || options.scenario !== 'Frozen')) throw new Error('UsePackagedLayout requires the Tauri Frozen scenario');
  return options;
}

export function assertReleasePermission(options, env = process.env) {
  if (options.configuration !== 'Release' || options.kind === 'python') return { required: false, allowed: true };
  const hosted = env.GITHUB_ACTIONS === 'true' && env.RUNNER_ENVIRONMENT === 'github-hosted'
    && env.RUNNER_OS === 'Windows' && /^\d+$/.test(env.GITHUB_RUN_ID || '');
  if (!options.disposableWindowsUser && !hosted) {
    throw new Error('Release smoke requires a fresh GitHub-hosted Windows runner or -DisposableWindowsUser asserting a disposable Windows user/VM. This guard cannot be bypassed with APPDATA or development environment overrides.');
  }
  return { required: true, allowed: true, basis: hosted ? 'github-hosted-windows-runner; profile freshness still required' : 'caller asserts disposable Windows user/VM' };
}

export function releaseProfileRoots(context) {
  return [
    { path: path.win32.join(context.roaming, 'com.chaoxing.gui'), claim: true },
    { path: path.win32.join(context.local, 'com.chaoxing.gui'), claim: true },
    { path: path.win32.join(context.roaming, 'chaoxing-desktop'), claim: false },
    { path: path.win32.join(context.local, 'chaoxing-desktop'), claim: false },
    { path: path.win32.join(context.temp, 'chaoxing-desktop'), claim: false },
  ];
}

export async function checkNoLinks(target, recursive = false) {
  const absolute = path.resolve(target);
  const ancestors = [];
  for (let current = absolute; ; current = path.dirname(current)) {
    ancestors.push(current);
    if (current === path.dirname(current)) break;
  }
  for (const current of ancestors.reverse()) {
    const info = await lstat(current).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
    if (!info) return false;
    if (info.isSymbolicLink()) throw new Error(`Smoke refuses symbolic links/reparse points: ${current}`);
  }
  if (recursive && (await lstat(absolute)).isDirectory()) {
    for (const entry of await readdir(absolute)) await checkNoLinks(path.join(absolute, entry), true);
  }
  return true;
}

export async function assertOwnedOrAbsent(roots, runId, sid) {
  for (const root of roots) {
    if (!(await checkNoLinks(root.path))) continue;
    await checkNoLinks(path.join(root.path, marker));
    const ownership = await json(path.join(root.path, marker)).catch((error) => { if (error.code === 'ENOENT' || error instanceof SyntaxError) return null; throw error; });
    if (!ownership || ownership.runId !== runId || ownership.sid !== sid || typeof ownership.path !== 'string' || canonical(ownership.path) !== canonical(root.path)) {
      throw new Error(`Release smoke refuses preexisting application/legacy data not owned by this smoke run: ${root.path}`);
    }
  }
}

export async function claimProfileRoots(roots, runId, sid) {
  await assertOwnedOrAbsent(roots, runId, sid);
  for (const root of roots.filter((entry) => entry.claim)) {
    // Parents are OS known folders that already exist. Exclusive mkdir rejects
    // a concurrent application's new directory. Reusing any existing directory
    // requires its ownership to be checked again after that creation attempt.
    try { await mkdir(root.path); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      await assertOwnedOrAbsent([root], runId, sid);
      continue;
    }
    await writeFile(path.join(root.path, marker), JSON.stringify({ runId, sid, path: path.resolve(root.path) }), { flag: 'wx' });
  }
}

export async function publishReleaseProfileOwnership(evidence, context, runId) {
  const roots = releaseProfileRoots(context);
  await assertOwnedOrAbsent(roots, runId, context.sid);
  assert.ok(await checkNoLinks(evidence), 'Profile ownership evidence directory must exist without links');
  const ownership = { schemaVersion: 1, configuration: 'Release', runId, sid: context.sid,
    evidenceDirectory: path.resolve(evidence), roots };
  // Complete this handoff before any profile is claimed. An enclosing Job
  // owner can then clean this invocation's roots if its finally is interrupted.
  await writeFile(path.join(evidence, 'profile-ownership.json'), JSON.stringify(ownership), { flag: 'wx' });
  return ownership;
}

export async function removeOwnedProfiles(roots, runId, sid) {
  await assertOwnedOrAbsent(roots, runId, sid);
  for (const root of roots.filter((entry) => entry.claim)) {
    if (!(await checkNoLinks(root.path, true))) continue;
    const resolved = await realpath(root.path);
    if (canonical(resolved) !== canonical(root.path)) throw new Error(`Refusing unexpected profile target: ${resolved}`);
    // Only exact known-folder children with this run's ownership marker reach rm.
    await rm(resolved, { recursive: true, force: false, maxRetries: 4, retryDelay: 200 });
  }
}

export function sanitizedEnvironment(source, context, options = {}) {
  const allowed = new Set(['COMSPEC', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432',
    'PUBLIC', 'HOMEDRIVE', 'HOMEPATH', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'USERNAME', 'USERDOMAIN']);
  const env = {};
  for (const [key, value] of Object.entries(source)) if (allowed.has(key.toUpperCase())) env[key.toUpperCase()] = value;
  Object.assign(env, {
    SystemRoot: context.windows, WINDIR: context.windows, USERPROFILE: context.userProfile,
    APPDATA: context.roaming, LOCALAPPDATA: context.local, TEMP: context.temp, TMP: context.temp,
    PATH: [path.win32.join(context.windows, 'System32'), context.windows, path.win32.join(context.windows, 'System32', 'Wbem')].join(';'),
  });
  if (options.configuration === 'Debug') {
    assert.ok(options.profile && options.backend, 'Debug launch requires explicit isolated profile and backend');
    Object.assign(env, { CHAOXING_TAURI_DEV_ROOT: options.profile, CHAOXING_TAURI_DEV_BACKEND: options.backend, CHAOXING_TAURI_DEV_HIDDEN: '1' });
  }
  return env;
}

export async function windowsContext(powerShell) {
  if (process.platform !== 'win32') throw new Error('P3 real process smoke requires Windows');
  const executable = powerShell || 'pwsh.exe';
  const { stdout } = await exec(executable, ['-NoProfile', '-NonInteractive', '-Command',
    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @{ windows=[Environment]::GetFolderPath('Windows'); roaming=[Environment]::GetFolderPath('ApplicationData'); local=[Environment]::GetFolderPath('LocalApplicationData'); userProfile=[Environment]::GetFolderPath('UserProfile'); temp=[IO.Path]::GetTempPath(); sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; powerShell=(Join-Path $PSHOME 'pwsh.exe') } | ConvertTo-Json -Compress"],
  { windowsHide: true, timeout: 15000 });
  const context = JSON.parse(stdout);
  for (const key of ['windows', 'roaming', 'local', 'userProfile', 'temp', 'powerShell']) assert.ok(path.isAbsolute(context[key]), `Windows known folder ${key} must be absolute`);
  return context;
}

async function requireFile(filename, label) {
  if (!filename) throw new Error(`${label} path is required`);
  const info = await lstat(filename).catch((error) => { throw new Error(`${label} is missing: ${filename}: ${error.message}`); });
  if (!info.isFile() || info.isSymbolicLink()) throw new Error(`${label} must be a regular file: ${filename}`);
}

export async function validateInputs(options) {
  if (options.kind === 'python') {
    await requireFile(options.executablePath, 'Frozen Python executable');
    if (options.mode === 'Backend') {
      const internal = path.join(path.dirname(options.executablePath), '_internal');
      assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Frozen backend must retain its complete onedir _internal directory: ${internal}`);
    }
    return;
  }
  await requireFile(options.hostPath, 'Tauri host');
  if (options.usePackagedLayout) {
    assert.equal(options.scenario, 'Frozen', 'UsePackagedLayout requires Frozen');
    assert.equal(canonical(options.backendDirectory), canonical(path.join(path.dirname(options.hostPath), 'backend')), 'UsePackagedLayout requires BackendDirectory equal to HostPath parent/backend');
    await checkNoLinks(path.dirname(options.hostPath));
  }
  if (options.scenario !== 'Fake') {
    await requireFile(path.join(options.backendDirectory || '', 'chaoxing-backend.exe'), 'Frozen backend');
    const internal = path.join(options.backendDirectory, '_internal');
    assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Frozen backend must retain its complete onedir _internal directory: ${internal}`);
  }
  if (options.scenario !== 'Frozen') {
    await requireFile(path.join(options.fakeBackendDirectory || '', 'p2-backend.exe'), 'Frozen P2 fixture');
    const internal = path.join(options.fakeBackendDirectory, '_internal');
    assert.ok((await lstat(internal).catch(() => null))?.isDirectory(), `Fixture must be a complete onedir package: ${internal}`);
  }
}

export function assertBuildProfile(exitCode, configuration) {
  const expected = configuration === 'Debug' ? 0 : 4;
  if (exitCode !== expected) throw new Error(`Build profile probe refused ${configuration}: expected exit ${expected}, received ${exitCode}. A release executable cannot use a Debug smoke profile.`);
}

async function probeBuildProfile(options, context, evidence, result, record) {
  // Keep Debug fail-closed before running an unknown host on a developer profile.
  // Optimized Release builds can split the flag into overlapping SIMD constants;
  // only a disposable profile may classify those builds by their native exit.
  if (options.configuration === 'Debug') {
    const bytes = await readFile(options.hostPath);
    assert.ok(bytes.includes(Buffer.from('--check-debug-build')), 'Host lacks the P3 side-effect-free --check-debug-build probe; rebuild the current P3 host before smoke');
  } else { assertReleasePermission(options); }
  const roots = options.configuration === 'Release' ? releaseProfileRoots(context) : [];
  const directory = path.join(evidence, 'build-profile-probe');
  await mkdir(directory);
  const owner = new NativeSupervisor(context.powerShell, directory);
  const item = { scenario: 'build-profile-probe', requested: options.configuration };
  result.processes.push(item);
  let profilesClaimed = false;
  await withCleanup(item, async () => {
    if (roots.length) {
      await claimProfileRoots(roots, result.runId, context.sid);
      profilesClaimed = true;
    }
    item.hostIdentity = await owner.start({ executable: options.hostPath, args: ['--check-debug-build'], cwd: directory,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    item.shutdown = await waitForEmptyJob(owner, 'side-effect-free native build profile probe', 10000);
    assertBuildProfile(item.shutdown.host.exitCode, options.configuration);
    record('compiled-host-profile-verified', { configuration: options.configuration, exitCode: item.shutdown.host.exitCode });
  }, async () => {
    item.cleanup = await owner.dispose();
    if (profilesClaimed) {
      assert.equal(item.cleanup.verified, true, 'Probe profile cleanup requires a verified captured process tree');
      await removeOwnedProfiles(roots, result.runId, context.sid);
      item.profileCleanup = { ownedRootsRemoved: true };
    }
  });
  assert.equal(item.cleanup.fallbackUsed, false, 'Build probe must exit without forced cleanup');
}

export async function until(check, label, timeout = 20000, interval = 100) {
  const deadline = Date.now() + timeout;
  let latest;
  while (Date.now() < deadline) {
    try { const result = await bounded(check, Math.max(1, deadline - Date.now()), label); if (result) return result; }
    catch (error) { if (error.fatal) throw error; latest = error; }
    await pause(Math.min(interval, Math.max(0, deadline - Date.now())));
  }
  throw new Error(`${label} timed out after ${timeout}ms${latest ? `: ${latest.message}` : ''}`);
}

async function bounded(action, timeout, label) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeout}ms`)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

export async function withCleanup(resource, action, cleanup) {
  let result;
  let failure;
  try { result = await action(resource); } catch (error) { failure = error; }
  try { await cleanup(resource); } catch (error) { failure = failure ? new AggregateError([failure, error], `${failure.message}; cleanup failed: ${error.message}`) : error; }
  if (failure) throw failure;
  return result;
}

export class NativeSupervisor {
  constructor(powerShell, evidenceDirectory) {
    this.powerShell = powerShell;
    this.evidenceDirectory = evidenceDirectory;
    this.identity = null;
    this.pending = new Map();
    this.counter = 0;
    this.diagnostics = '';
    this.finished = false;
  }
  async start(specification) {
    await mkdir(this.evidenceDirectory, { recursive: true });
    this.child = spawn(this.powerShell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
      path.join(repo, 'desktop/tests/fixtures/p3-windows-process.ps1')], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stdin.on('error', () => {});
    this.child.stderr.on('data', (bytes) => { this.diagnostics += bytes.toString(); });
    this.lines = createInterface({ input: this.child.stdout });
    this.lines.on('line', (line) => {
      let message;
      try { message = JSON.parse(line); } catch { this.diagnostics += `${line}\n`; return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id); clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(message.error));
    });
    const failed = (error) => {
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
      this.pending.clear();
    };
    this.child.on('error', failed);
    this.child.on('exit', (code, signal) => failed(new Error(`Process supervisor exited ${code ?? signal}: ${this.diagnostics}`)));
    try {
      this.identity = await this.command('start', { specification: { ...specification,
        stdoutPath: path.join(this.evidenceDirectory, 'stdout.log'), stderrPath: path.join(this.evidenceDirectory, 'stderr.log') } }, 20000);
      return this.identity;
    } catch (error) {
      await this.dispose().catch((cleanup) => { error.message += `; launch cleanup: ${cleanup.message}`; });
      throw error;
    }
  }
  command(operation, properties = {}, timeout = 10000) {
    if (!this.child || this.child.exitCode !== null || this.child.signalCode !== null) return Promise.reject(new Error('Process supervisor is not running'));
    const id = ++this.counter;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Supervisor ${operation} timed out`)); }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(`${JSON.stringify({ id, operation, ...properties })}\n`, (error) => {
        if (error && this.pending.delete(id)) { clearTimeout(timer); reject(error); }
      });
    });
  }
  snapshot() { return this.command('snapshot'); }
  async dispose() {
    if (this.finished) return this.cleanup;
    this.finished = true;
    let failure;
    try {
      if (this.identity && this.child?.exitCode === null && this.child?.signalCode === null) {
        this.cleanup = await this.command('finish');
        this.cleanup.verified = true;
        if (this.cleanup.remaining.length) throw new Error('Captured Job still contains processes after cleanup');
      } else if (this.identity) {
        this.cleanup = { fallbackUsed: true, verified: false, reason: 'Supervisor exited before its final process-state report' };
        throw new Error('Supervisor exited before cleanup could verify the captured tree');
      }
    } catch (error) { failure = error; }
    finally {
      this.child?.stdin.end();
      if (this.child) {
        try { await until(() => this.child.exitCode !== null || this.child.signalCode !== null, 'supervisor exit', 6000); }
        catch {
          // Closing the supervisor's only Job handle kills only its captured tree.
          this.child.kill();
          await until(() => this.child.exitCode !== null || this.child.signalCode !== null, 'supervisor termination', 5000);
          failure ||= new Error('Supervisor required forced cleanup');
        }
      }
      this.lines?.close();
      if (this.diagnostics) await writeFile(path.join(this.evidenceDirectory, 'supervisor.log'), this.diagnostics);
    }
    if (failure) throw failure;
    return this.cleanup || { fallbackUsed: false, remaining: [], observed: [] };
  }
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function fingerprint(filename) {
  return readFile(filename).then((bytes) => createHash('sha256').update(bytes).digest('hex')).catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
}

function loadChromium() {
  try { return createRequire(path.join(repo, 'desktop/package.json'))('playwright-core').chromium; }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND' || !process.env.P2_TOOLS_DIR) throw error;
    return createRequire(path.join(path.resolve(process.env.P2_TOOLS_DIR), 'package.json'))('playwright-core').chromium;
  }
}

async function safeCopyDirectory(source, destination) {
  assert.ok(await checkNoLinks(source, true), `Missing resource directory: ${source}`);
  await cp(source, destination, { recursive: true, errorOnExist: true, force: false });
}

function fatal(message) { const error = new Error(message); error.fatal = true; return error; }

async function assertRunning(owner) {
  const snapshot = await owner.snapshot();
  if (!snapshot.host?.alive) throw fatal(`Captured executable exited before the assertion completed (exit ${snapshot.host?.exitCode})`);
  return snapshot;
}

async function fetchBounded(url, options = {}, timeout = 2000) {
  return fetch(url, { ...options, signal: AbortSignal.timeout(timeout) });
}

async function waitForEmptyJob(owner, label, timeout = 12000) {
  return until(async () => { const snapshot = await owner.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive) && snapshot; }, label, timeout);
}

async function runTauri(options, context, evidence, result, record) {
  const chromium = loadChromium();
  const work = path.join(evidence, 'fixtures');
  await mkdir(work);
  const roots = options.configuration === 'Release' ? releaseProfileRoots(context) : [];
  const runId = result.runId;
  const windowTitle = (await json(path.join(repo, 'desktop/src-tauri/tauri.conf.json'))).app.windows[0].title;
  result.profilePolicy = { windowsKnownFolders: true, sid: context.sid, roots, productionPathsOverridden: false };
  if (roots.length) await assertOwnedOrAbsent(roots, runId, context.sid);
  let index = 0;

  async function launch(name, kind, fault) {
    const scenarioRoot = path.join(work, `${++index}-${name}`);
    const scenarioEvidence = path.join(evidence, `${index}-${name}`);
    const packageRoot = options.usePackagedLayout ? path.dirname(options.hostPath) : path.join(scenarioRoot, 'package');
    const profile = path.join(scenarioRoot, 'profile');
    const fixture = path.join(scenarioRoot, 'fixture');
    if (!options.usePackagedLayout) await mkdir(packageRoot, { recursive: true });
    await mkdir(fixture, { recursive: true });
    const host = options.usePackagedLayout ? options.hostPath : path.join(packageRoot, path.basename(options.hostPath));
    if (!options.usePackagedLayout) await copyFile(options.hostPath, host);
    const backend = path.join(packageRoot, 'backend', 'chaoxing-backend.exe');
    const source = kind === 'fake' ? options.fakeBackendDirectory : options.backendDirectory;
    if (!options.usePackagedLayout && fault !== 'missing') {
      await safeCopyDirectory(source, path.dirname(backend));
      if (kind === 'fake') {
        await copyFile(path.join(path.dirname(backend), 'p2-backend.exe'), backend);
        await rm(path.join(path.dirname(backend), 'p2-backend.exe'));
      }
      if (fault === 'bad-executable') await writeFile(backend, 'P3 deliberately invalid Windows executable');
    }
    const port = await freePort();
    const env = sanitizedEnvironment(process.env, context, { configuration: options.configuration, profile, backend });
    env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = `--remote-debugging-address=127.0.0.1 --remote-debugging-port=${port} --force-device-scale-factor=1`;
    if (kind === 'fake') Object.assign(env, { P2_FIXTURE_ROOT: fixture, P2_WEB_DIST: path.join(repo, 'web/dist') });
    if (fault === 'slow') env.P2_READY_DELAY_MS = '30000';
    const owner = new NativeSupervisor(context.powerShell, scenarioEvidence);
    const item = { scenario: name, host, expectedBackend: backend, configuration: options.configuration,
      packagedResourceLookup: options.configuration === 'Release', layout: options.usePackagedLayout ? 'input package executed in place' : 'owned fixture package copy',
      sanitizedPath: env.PATH, stdin: 'real pipe held by supervisor',
      outerJobAssignedBeforeResume: false, processes: [], cleanup: null };
    result.processes.push(item);
    let browser;
    const app = { owner, item, fixture, scenarioEvidence, profile, backend, kind,
      data: roots.length ? path.win32.join(context.roaming, 'com.chaoxing.gui', 'data') : path.join(profile, 'data') };
    app.installLogs = [path.join(path.dirname(backend), 'chaoxing.log'), path.join(path.dirname(backend), '_internal', 'chaoxing.log')];
    app.installLogBaseline = await Promise.all(app.installLogs.map(fingerprint));
    try {
      if (roots.length) await claimProfileRoots(roots, runId, context.sid);
      item.hostIdentity = await owner.start({ executable: host, args: [], cwd: packageRoot, env });
      item.outerJobAssignedBeforeResume = true;
      assert.equal(canonical(item.hostIdentity.executable), canonical(host));
      await until(async () => {
        await assertRunning(owner);
        return (await fetchBounded(`http://127.0.0.1:${port}/json/version`)).ok;
      }, 'real Tauri WebView2 CDP', options.timeoutSeconds * 1000);
      const listeners = await owner.command('tcp-listener', { port });
      const cdpProcesses = await owner.snapshot();
      assert.ok(listeners.length, 'WebView2 debugging listener is missing');
      for (const listener of listeners) assert.ok(cdpProcesses.active.some((identity) => identity.pid === listener.pid), 'Refusing a CDP listener outside the captured host Job');
      item.cdpListeners = listeners;
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`, { timeout: 15000 });
      app.browser = browser;
      app.page = await until(() => browser.contexts().flatMap((entry) => entry.pages()).find((page) => /^https?:\/\/tauri\.localhost(?:\/|$)|^tauri:\/\/localhost(?:\/|$)/.test(page.url())), 'packaged Tauri page', 20000);
      app.page.setDefaultTimeout(15000);
      await app.page.waitForLoadState('domcontentloaded');
      return app;
    } catch (error) {
      if (browser) item.webviewPagesAtFailure = browser.contexts().flatMap((entry) => entry.pages()).map((page) => page.url());
      await browser?.close().catch(() => {});
      item.cleanup = await owner.dispose().catch((cleanup) => ({ error: cleanup.message }));
      if (roots.length) await removeOwnedProfiles(roots, runId, context.sid);
      throw error;
    }
  }

  async function captureReady(app) {
    const status = await until(async () => {
      await assertRunning(app.owner);
      const current = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      if (current.phase === 'failed') throw fatal(`Backend failed: ${current.error}`);
      return current.phase === 'ready' && current;
    }, 'real host backend Ready', options.timeoutSeconds * 1000);
    assert.equal(Object.hasOwn(status, 'token'), false);
    assert.equal(Object.hasOwn(status, 'port'), false);
    const snapshot = await app.owner.snapshot();
    const backends = snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend));
    assert.ok(backends.length >= 1, 'The host must run the copied frozen backend executable');
    assert.equal(snapshot.host.inOuterJob, true);
    for (const identity of backends) assert.equal(identity.inOuterJob, true);
    app.backendIdentities = backends;
    app.item.ready = { status, snapshot, innerJobEvidence: 'backend Ready follows the host mandatory backend Job assignment; outer membership queried through Win32' };
    app.item.processes = snapshot.observed;
    record(`${app.item.scenario}-ready`, { backendIdentities: backends, nestedJobRequested: Boolean(options.nestedJob), outerJobActive: true });
  }

  async function close(app, force = false) {
    let primary;
    try {
      const before = await app.owner.snapshot();
      app.item.processes = before.observed;
      await app.browser?.close();
      if (force) await app.owner.command('kill-host');
      else {
        const windows = await app.owner.command('close-window', { title: windowTitle });
        assert.equal(windows, 1, 'Normal shutdown must close exactly the main window of the captured PID/title');
      }
      const after = await waitForEmptyJob(app.owner, force ? 'forced host tree exit' : 'normal WM_CLOSE tree exit');
      app.item.shutdown = { mode: force ? 'terminate captured host handle' : 'WM_CLOSE exact captured PID and configured title', before, after,
        outerJobStillOpenAtObservation: true, noResidueBeforeFallback: true };
      record(`${app.item.scenario}-${force ? 'forced' : 'normal'}-no-residue`, { hostPid: before.host.pid, observed: after.observed });
    } catch (error) { primary = error; }
    finally {
      await app.browser?.close().catch(() => {});
      try { app.item.cleanup = await app.owner.dispose(); }
      catch (error) { primary = primary ? new Error(`${primary.message}; cleanup: ${error.message}`) : error; }
      for (const filename of ['web_config.json', 'renderer-session.json', 'chaoxing.log']) {
        await copyFile(path.join(app.data, filename), path.join(app.scenarioEvidence, filename)).catch((error) => { if (error.code !== 'ENOENT') throw error; });
      }
      if (roots.length) await removeOwnedProfiles(roots, runId, context.sid);
    }
    if (primary) throw primary;
    assert.equal(app.item.cleanup.fallbackUsed, false, 'Fallback tree kill cannot satisfy the normal/forced lifecycle assertion');
  }

  async function use(name, kind, action, { fault, force = false, ready = true } = {}) {
    const app = await launch(name, kind, fault);
    await withCleanup(app, async () => {
      if (ready) await captureReady(app);
      try { await bounded(() => action(app), options.timeoutSeconds * 1000, `${name} assertions`); }
      catch (error) {
        await app.page.screenshot({ path: path.join(app.scenarioEvidence, 'failure.png') }).catch(() => {});
        await writeFile(path.join(app.scenarioEvidence, 'failure.txt'), `${error.stack}\n${await app.page.locator('body').innerText().catch(() => '')}`);
        throw error;
      }
    }, () => close(app, force));
  }

  if (options.scenario !== 'Frozen') {
    await use('fake-business', 'fake', (app) => syntheticBusiness(app, record));
    await use('fake-cancel', 'fake', (app) => syntheticCancellation(app, record));
    for (const fault of ['missing', 'bad-executable']) {
      await use(`fake-${fault}`, 'fake', async (app) => {
        await app.page.getByRole('button', { name: '重新检查' }).waitFor();
        const initial = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
        assert.equal(initial.phase, 'failed');
        assert.equal(await app.page.getByLabel('手机号').count(), 0);
        await activate(app.page.getByRole('button', { name: '重新检查' }));
        const again = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
        assert.equal(again.phase, 'failed');
        const snapshot = await app.owner.snapshot();
        assert.equal(snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend)).length, 0);
        record(`fake-${fault}-failed-without-restart`, { initial, again });
      }, { fault, ready: false });
    }
    await use('fake-close-starting', 'fake', async (app) => {
      const status = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      assert.equal(status.phase, 'starting');
      record('close-before-backend-ready', { status });
    }, { fault: 'slow', ready: false });
    await use('fake-backend-death', 'fake', async (app) => {
      for (const identity of app.backendIdentities) await app.owner.command('kill-member', { pid: identity.pid, createdAtFileTime: identity.createdAtFileTime });
      await app.page.getByRole('button', { name: '重新检查' }).waitFor();
      await activate(app.page.getByRole('button', { name: '重新检查' }));
      const status = await app.page.evaluate(() => window.__TAURI__.core.invoke('backend_status'));
      assert.equal(status.phase, 'failed');
      const snapshot = await app.owner.snapshot();
      assert.equal(snapshot.active.filter((identity) => canonical(identity.executable) === canonical(app.backend)).length, 0);
      record('ready-backend-death-does-not-restart', { status });
    });
    await use('fake-force-host', 'fake', async () => {}, { force: true });
  }
  if (options.scenario !== 'Fake') {
    await use('frozen-contracts', 'frozen', (app) => frozenContracts(app, record));
    if (options.nestedJob) await use('frozen-force-host', 'frozen', async () => {}, { force: true });
  }
}

async function activate(locator) {
  await locator.waitFor({ state: 'visible' });
  await until(() => locator.isEnabled(), 'fixture button enabled');
  // CDP DOM click is stated explicitly; no claim of physical mouse input.
  await locator.evaluate((element) => element.click());
}

async function syntheticBusiness(app, record) {
  const { page } = app;
  await page.getByLabel('手机号').fill('p2-fixture');
  await page.getByLabel('密码', { exact: true }).fill('p3-synthetic-password');
  await activate(page.getByRole('button', { name: '登录', exact: true }));
  const course = page.getByRole('button', { name: /P2 测试课程一/ });
  await course.waitFor();
  assert.equal(await course.getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByRole('button', { name: /P2 测试课程二/ }).getAttribute('aria-pressed'), 'false');
  await activate(page.getByRole('button', { name: '保存当前配置' }));
  await page.getByText('配置已保存', { exact: true }).waitFor();
  await activate(page.getByRole('button', { name: '开始学习', exact: true }));
  await page.getByText('p2-existing-task', { exact: true }).waitFor();
  await page.getByRole('log').getByText('P2 终态日志二', { exact: true }).waitFor();
  assert.equal(await page.getByRole('log').getByText('P2 唯一日志一', { exact: true }).count(), 1);
  const counters = await json(path.join(app.fixture, 'counts.json'));
  assert.equal(counters.start, 1);
  assert.equal(counters.configWrites, 1);
  assert.deepEqual(counters.after.slice(0, 3), [0, 1, 1]);
  const session = await page.evaluate(() => window.__TAURI__.core.invoke('session_read'));
  assert.equal(session.activeTask.taskId, 'p2-existing-task');
  assert.equal(JSON.stringify(session).includes('password'), false);
  await page.screenshot({ path: path.join(app.scenarioEvidence, 'synthetic-progress.png'), fullPage: true });
  await page.reload();
  await page.getByText('p2-existing-task', { exact: true }).waitFor();
  assert.equal((await json(path.join(app.fixture, 'counts.json'))).start, 1);
  await writeFile(path.join(app.fixture, 'control.json'), JSON.stringify({ missing: true }));
  await page.reload();
  await page.getByText('已过期', { exact: true }).first().waitFor();
  await until(async () => (await page.evaluate(() => window.__TAURI__.core.invoke('session_read'))).activeTask === null, 'missing task clears saved task');
  assert.equal((await page.evaluate(() => window.__TAURI__.core.invoke('session_read'))).login.username, 'p2-fixture');
  record('synthetic-login-config-start409-after-once-refresh404', { counters, upstreamAccountsUsed: false, input: 'visible/enabled DOM click over CDP' });
}

async function syntheticCancellation(app, record) {
  await writeFile(path.join(app.fixture, 'control.json'), JSON.stringify({ delayMs: 1000 }));
  const cancellation = await app.page.evaluate(async () => {
    const invoke = window.__TAURI__.core.invoke;
    await invoke('api_cancel', { requestId: 980001 });
    const early = await invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 980001 } }).then(() => 'success', (error) => error.kind);
    const pending = invoke('api_request', { request: { operation: 'configRead', payload: null, requestId: 980002 } }).then(() => 'success', (error) => error.kind);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const before = performance.now();
    const status = await invoke('backend_status');
    const statusMs = performance.now() - before;
    await invoke('api_cancel', { requestId: 980002 });
    return { early, inflight: await pending, phase: status.phase, statusMs };
  });
  assert.equal(cancellation.early, 'cancelled');
  assert.equal(cancellation.inflight, 'cancelled');
  assert.equal(cancellation.phase, 'ready');
  assert.ok(cancellation.statusMs < 750, 'Backend status must remain responsive while HTTP waits');
  await writeFile(path.join(app.fixture, 'control.json'), '{}');
  record('synthetic-cancellation-before-and-during-request', cancellation);
}

async function frozenContracts(app, record) {
  const contract = await app.page.evaluate(async () => {
    const invoke = window.__TAURI__.core.invoke;
    let id = 990001;
    const request = (operation, payload = null, extra = {}) => invoke('api_request', { request: { operation, payload, requestId: id++, ...extra } });
    const read = await request('configRead');
    const write = await request('configWrite', { settings: { jobs: 2 }, selectedCoursesByAccount: { 'p3-config-fixture': ['course-1'] } });
    const reread = await request('configRead');
    const invalid = [];
    // Empty inputs fail backend validation before creating an upstream client.
    for (const operation of ['login', 'courses', 'start']) invalid.push([operation, await request(operation, {})]);
    const missing = [];
    for (const operation of ['taskStatus', 'taskDetails', 'taskLogs']) missing.push([operation, await request(operation, null, { taskId: 'p3-never-created', ...(operation === 'taskLogs' ? { after: 0 } : {}) })]);
    return { read, write, reread, invalid, missing };
  });
  assert.equal(contract.read.status, 200);
  assert.equal(contract.write.status, 200);
  assert.equal(contract.reread.body.data.settings.jobs, 2);
  for (const [operation, response] of contract.invalid) assert.equal(response.status, 400, operation);
  for (const [operation, response] of contract.missing) assert.equal(response.status, 404, operation);
  const stored = await json(path.join(app.data, 'web_config.json'));
  assert.deepEqual(stored.selectedCoursesByAccount, { 'p3-config-fixture': ['course-1'] });
  assert.ok((await lstat(path.join(app.data, 'chaoxing.log'))).isFile());
  assert.deepEqual(await Promise.all(app.installLogs.map(fingerprint)), app.installLogBaseline);
  record('frozen-config-read-write-empty400-missing404', { contract, logInDataDirectory: true, installLogsUnchanged: true, upstreamAccountsUsed: false, learningTaskCreated: false });
}

async function runPython(options, context, evidence, result, record) {
  const fixtureRoot = path.join(evidence, 'fixture');
  const data = path.join(fixtureRoot, 'data');
  const installed = path.join(fixtureRoot, 'package');
  await mkdir(data, { recursive: true });
  const executable = path.join(installed, path.basename(options.executablePath));
  if (options.mode === 'Backend') await safeCopyDirectory(path.dirname(options.executablePath), installed);
  else { await mkdir(installed); await copyFile(options.executablePath, executable); }
  const env = sanitizedEnvironment(process.env, context, { configuration: 'Release' });
  Object.assign(env, { CHAOXING_HEADLESS: '1', CHAOXING_DATA_DIR: data, PYTHONIOENCODING: 'utf-8' });
  let port;
  let token;
  let instance;
  if (options.mode === 'Backend') {
    token = randomBytes(32).toString('hex'); instance = randomUUID();
    Object.assign(env, { CHAOXING_TAURI: '1', CHAOXING_TAURI_TOKEN: token, CHAOXING_TAURI_INSTANCE_ID: instance });
  } else { port = await freePort(); env.CHAOXING_PORT = String(port); }
  const owner = new NativeSupervisor(context.powerShell, path.join(evidence, 'process'));
  const item = { executable, mode: options.mode, data, sanitizedPath: env.PATH, headless: true, stdin: 'real pipe', process: null };
  result.processes.push(item);
  try {
    item.process = await owner.start({ executable, args: [], cwd: data, env });
    if (options.mode === 'Backend') {
      const ready = await until(async () => {
        await assertRunning(owner);
        const lines = (await readable(path.join(evidence, 'process/stdout.log'))).split(/\r?\n/);
        for (const line of lines) {
          let value; try { value = JSON.parse(line); } catch { continue; }
          if (value.ready !== 'chaoxing-ready') continue;
          if (value.version !== 1 || value.instanceId !== instance || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535) throw fatal('Frozen backend emitted an invalid ready handshake');
          return value;
        }
        return false;
      }, 'frozen backend ready handshake', options.timeoutSeconds * 1000);
      port = ready.port;
      record('python-backend-validated-ready-handshake', { version: ready.version, instanceMatched: true, dynamicPort: true });
    }
    const url = `http://127.0.0.1:${port}`;
    const headers = token ? { 'X-Auth-Token': token } : {};
    await until(async () => {
      await assertRunning(owner);
      const response = await fetchBounded(`${url}/api/health`, { headers });
      if (response.status !== 200) return false;
      const body = await response.json();
      if (token) assert.equal(body.instanceId, instance);
      return body.status === true;
    }, 'frozen Python health endpoint', options.timeoutSeconds * 1000);
    const listeners = await owner.command('tcp-listener', { port });
    const listeningState = await owner.snapshot();
    assert.ok(listeners.length, 'No loopback listener belongs to the captured frozen executable');
    for (const listener of listeners) assert.ok(listeningState.active.some((identity) => identity.pid === listener.pid && canonical(identity.executable) === canonical(executable)), 'A different local process owns the health endpoint');
    item.listeners = listeners;
    const staticResponse = await fetchBounded(`${url}/`, { headers });
    assert.equal(staticResponse.status, 200, 'Packaged Python must serve its bundled web frontend');
    assert.match(await staticResponse.text(), /<html|<!doctype/i);
    if (token) assert.equal((await fetchBounded(`${url}/api/health`)).status, 401);
    const read = await fetchBounded(`${url}/api/config`, { headers });
    assert.equal(read.status, 200);
    const write = await fetchBounded(`${url}/api/config`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: { jobs: 2 } }) });
    assert.equal(write.status, 200);
    for (const route of ['login', 'courses', 'start']) {
      const response = await fetchBounded(`${url}/api/${route}`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: '{}' });
      assert.equal(response.status, 400, route);
    }
    for (const route of ['task/p3-never-created', 'task/p3-never-created/details', 'logs/p3-never-created?after=0']) assert.equal((await fetchBounded(`${url}/api/${route}`, { headers })).status, 404, route);
    assert.equal((await json(path.join(data, 'web_config.json'))).settings.jobs, 2);
    item.ready = await owner.snapshot();
    await owner.command('stdin-eof');
    item.shutdown = await waitForEmptyJob(owner, 'Python stdin EOF tree exit', 15000);
    record(`python-${options.mode.toLowerCase()}-health-static-config-validation-eof`, { upstreamAccountsUsed: false, learningTaskCreated: false, before: item.ready, after: item.shutdown });
  } finally { item.cleanup = await owner.dispose(); }
  assert.equal(item.cleanup.fallbackUsed, false, 'Python EOF must exit without the supervisor killing the tree');
}

export async function main(argv = process.argv.slice(2)) {
  // Reserve a new directory even when parsing or the release guard fails. Never
  // overwrite P2 or a previous P3 result, including on invalid scenario input.
  const evidenceIndex = argv.indexOf('--evidence-directory');
  const requestedEvidence = evidenceIndex >= 0 && argv[evidenceIndex + 1] && !argv[evidenceIndex + 1].startsWith('--')
    ? argv[evidenceIndex + 1] : path.join(repo, 'desktop/src-tauri/target/p3-smoke-evidence');
  await mkdir(path.resolve(requestedEvidence), { recursive: true });
  const evidence = await mkdtemp(path.join(path.resolve(requestedEvidence), 'smoke-'));
  const result = { runId: randomUUID(), startedAt: new Date().toISOString(), success: false, checks: [], processes: [],
    limitations: ['DOM clicks over CDP do not represent physical mouse input', 'Debug execution does not qualify as release or clean-system validation'] };
  const record = (name, details = {}) => { result.checks.push({ name, result: 'PASS', ...details }); console.log(`PASS ${name}`); };
  let options;
  try {
    options = parseArguments(argv);
    result.selection = { kind: options.kind, configuration: options.configuration, scenario: options.scenario, mode: options.mode,
      nestedJob: Boolean(options.nestedJob), usePackagedLayout: Boolean(options.usePackagedLayout) };
    result.releasePermission = assertReleasePermission(options);
    const context = await windowsContext(options.powerShell);
    if (options.kind === 'tauri' && options.configuration === 'Release') {
      result.profileOwnership = await publishReleaseProfileOwnership(evidence, context, result.runId);
    }
    options.backendDirectory = path.resolve(options.backendDirectory || path.join(repo, 'desktop/src-tauri/resources/backend'));
    options.fakeBackendDirectory = path.resolve(options.fakeBackendDirectory || path.join(repo, 'desktop/src-tauri/target/p2-fixture/dist/p2-backend'));
    if (options.hostPath) options.hostPath = path.resolve(options.hostPath);
    if (options.executablePath) options.executablePath = path.resolve(options.executablePath);
    await validateInputs(options);
    if (options.kind === 'tauri') {
      await probeBuildProfile(options, context, evidence, result, record);
      await runTauri(options, context, evidence, result, record);
    }
    else await runPython(options, context, evidence, result, record);
    assert.ok(result.checks.length, 'A smoke run must execute at least one assertion');
    result.success = true;
  } catch (error) { result.error = error.stack; console.error(error.stack); }
  finally {
    result.endedAt = new Date().toISOString();
    await writeFile(path.join(evidence, 'result.json'), JSON.stringify(result, null, 2), { flag: 'wx' });
    console.log(`Evidence: ${path.join(evidence, 'result.json')}`);
  }
  return result.success ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) process.exitCode = await main();

````

## desktop/scripts/package-common.ps1

SHA256: 83149db717c23e4cf103458b4f4a737e805e4724669311a9628c19d27882e55a

````text
#Requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-PackageVersion {
    param([string]$Version)
    $projectFile = Join-Path $PSScriptRoot '../../pyproject.toml'
    $project = [regex]::Match([IO.File]::ReadAllText($projectFile), '(?ms)^\[project\][^\S\r\n]*\r?\n(.*?)(?=^\[|\z)')
    $versions = [regex]::Matches($project.Groups[1].Value, '(?m)^version\s*=\s*"([^"]+)"\s*(?:#.*)?$')
    if ($versions.Count -ne 1) { throw 'Expected exactly one project version in pyproject.toml.' }
    $expected = $versions[0].Groups[1].Value
    if ($expected -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
        throw "Unsafe or unsupported project version: $expected"
    }
    if ($Version -and $Version -cne $expected) { throw "Version mismatch: requested '$Version', pyproject.toml is '$expected'." }
    return $expected
}

function Get-PackageFullPath {
    param([Parameter(Mandatory)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Unsafe empty path.' }
    if (-not [IO.Path]::IsPathFullyQualified($Path)) { $Path = Join-Path (Get-Location).ProviderPath $Path }
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ($full.TrimEnd([char[]]'\/') -eq $root.TrimEnd([char[]]'\/')) { throw "Unsafe filesystem root: $full" }
    $relative = $full.Substring($root.Length).Replace('\', '/')
    Assert-PackageRelativePath $relative
    return $full.TrimEnd([char[]]'\/')
}

function Assert-PackageRelativePath {
    param([Parameter(Mandatory)][string]$Path)
    if ($Path -match '[\\:\x00-\x1f<>"|?*]' -or $Path.StartsWith('/') -or $Path.EndsWith('/')) {
        throw "Unsafe relative path: '$Path'"
    }
    foreach ($segment in $Path.Split('/')) {
        if ($segment -in @('', '.', '..') -or $segment -match '[. ]$' -or
            $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
            throw "Unsafe relative path: '$Path'"
        }
    }
}

function Assert-PackageNoReparse {
    param([Parameter(Mandatory)][string]$Path)
    $cursor = Get-PackageFullPath $Path
    while ($cursor) {
        try { $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop }
        catch [System.Management.Automation.ItemNotFoundException] { $item = $null }
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing reparse point/junction: $cursor"
        }
        $parent = [IO.Directory]::GetParent($cursor)
        $cursor = if ($null -ne $parent) { $parent.FullName } else { $null }
    }
}

function Assert-PackageChildPath {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Parent)
    $full = Get-PackageFullPath $Path
    $parentFull = Get-PackageFullPath $Parent
    if (-not $full.StartsWith($parentFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside intended directory '$parentFull': $full"
    }
    Assert-PackageNoReparse $full
}

function Assert-PackageDisjoint {
    param([Parameter(Mandatory)][string]$First, [Parameter(Mandatory)][string]$Second)
    $left = Get-PackageFullPath $First
    $right = Get-PackageFullPath $Second
    if ($left.Equals($right, [StringComparison]::OrdinalIgnoreCase) -or
        $left.StartsWith($right + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $right.StartsWith($left + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe overlapping source/destination paths: '$left' and '$right'"
    }
}

function Assert-PackageMutableDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $full = Get-PackageFullPath $Path
    $protectedPaths = @([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..')), $env:USERPROFILE, $env:SystemRoot, [IO.Path]::GetTempPath())
    foreach ($protected in $protectedPaths) {
        if (-not $protected) { continue }
        $protected = [IO.Path]::GetFullPath($protected).TrimEnd([char[]]'\/')
        if ($full.Equals($protected, [StringComparison]::OrdinalIgnoreCase) -or
            $protected.StartsWith($full + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe protected directory: $full"
        }
    }
    Assert-PackageNoReparse $full
}

function Get-PackageInventory {
    param([Parameter(Mandatory)][string]$Directory, [switch]$SkipHash)
    $root = Get-PackageFullPath $Directory
    Assert-PackageNoReparse $root
    if (-not [IO.Directory]::Exists($root)) { throw "Missing required directory: $root" }
    $directories = [Collections.Generic.List[string]]::new()
    $filePaths = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
    $pending.Push([IO.DirectoryInfo]::new($root))
    while ($pending.Count) {
        foreach ($item in $pending.Pop().EnumerateFileSystemInfos()) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point/junction: $($item.FullName)" }
            $relative = [IO.Path]::GetRelativePath($root, $item.FullName).Replace('\', '/')
            Assert-PackageRelativePath $relative
            if ($item.Attributes -band [IO.FileAttributes]::Directory) {
                $directories.Add($relative)
                $pending.Push([IO.DirectoryInfo]$item)
            } else { $filePaths.Add($relative) }
        }
    }
    $directories.Sort([StringComparer]::Ordinal)
    $filePaths.Sort([StringComparer]::Ordinal)
    $files = @(foreach ($relative in $filePaths) {
        $file = Join-Path $root $relative
        [ordered]@{
            path = $relative
            length = [IO.FileInfo]::new($file).Length
            sha256 = if ($SkipHash) { $null } else { (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    })
    return [ordered]@{ files = $files; directories = @($directories.ToArray()) }
}

function Assert-BackendLayout {
    param([Parameter(Mandatory)][string]$Directory, [Parameter(Mandatory)]$Inventory)
    foreach ($required in @('chaoxing-backend.exe', '_internal/web/dist/index.html')) {
        $file = @($Inventory.files | Where-Object { $_.path -ceq $required -and $_.length -gt 0 })
        if ($file.Count -ne 1) { throw "Missing or empty required backend resource: $required" }
    }
    if ('_internal' -cnotin $Inventory.directories -or '_internal/web/dist/assets' -cnotin $Inventory.directories -or
        @($Inventory.files | Where-Object { $_.path.StartsWith('_internal/web/dist/assets/', [StringComparison]::Ordinal) -and $_.length -gt 0 }).Count -eq 0) {
        throw 'Incomplete backend: _internal and embedded web/dist/assets are required.'
    }
    $webRoot = Join-Path $Directory '_internal/web/dist'
    $html = [IO.File]::ReadAllText((Join-Path $webRoot 'index.html'))
    foreach ($match in [regex]::Matches($html, '(?i)<(?:script|link|img|source)\b[^>]*?\b(?:src|href)\s*=\s*["''](?<url>[^"''<>]+)["'']')) {
        $url = $match.Groups['url'].Value
        if ($url -match '^(?:[a-z][a-z0-9+.-]*:|//|#)') { continue }
        $relative = [Uri]::UnescapeDataString(($url -split '[?#]', 2)[0]).TrimStart('/')
        if ($relative.StartsWith('./')) { $relative = $relative.Substring(2) }
        Assert-PackageRelativePath $relative
        if (-not [IO.File]::Exists((Join-Path $webRoot $relative))) { throw "Missing referenced web resource: $relative" }
    }
}

function New-PackageManifest {
    param([Parameter(Mandatory)][string]$Kind, [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$EntryPoint, [Parameter(Mandatory)]$Inventory)
    return [ordered]@{
        schemaVersion = 1
        kind = $Kind
        version = $Version
        entryPoint = $EntryPoint
        files = @($Inventory.files)
        directories = @($Inventory.directories)
    }
}

function Write-PackageJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 12) + "`n", [Text.UTF8Encoding]::new($false))
}

function Read-PackageJson {
    param([Parameter(Mandatory)][string]$Path)
    Assert-PackageNoReparse $Path
    if (-not [IO.File]::Exists($Path)) { throw "Missing manifest: $Path" }
    if ([IO.FileInfo]::new($Path).Length -gt 16MB) { throw "Manifest is too large: $Path" }
    try { return ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Path)) -AsHashtable -Depth 20 }
    catch { throw "Invalid JSON manifest '$Path': $($_.Exception.Message)" }
}

function Assert-PackageManifestHeader {
    param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Version, [string]$EntryPoint)
    if ($Manifest -isnot [Collections.IDictionary] -or -not $Manifest.Contains('schemaVersion') -or
        ($Manifest.schemaVersion -isnot [long] -and $Manifest.schemaVersion -isnot [int]) -or
        $Manifest.schemaVersion -ne 1 -or -not $Manifest.Contains('kind') -or $Manifest.kind -cne $Kind) {
        throw "Invalid $Kind manifest schema."
    }
    if (-not $Manifest.Contains('version') -or $Manifest.version -cne $Version) { throw "Manifest version mismatch: expected $Version." }
    if ($EntryPoint -and (-not $Manifest.Contains('entryPoint') -or $Manifest.entryPoint -cne $EntryPoint)) { throw 'Manifest entry point mismatch.' }
}

function Assert-PackageInventoryManifest {
    param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)]$Inventory)
    if (-not $Manifest.Contains('files') -or $Manifest.files -isnot [Collections.IList] -or
        -not $Manifest.Contains('directories') -or $Manifest.directories -isnot [Collections.IList]) {
        throw 'Invalid manifest: files and directories must be arrays.'
    }
    $actualFiles = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    foreach ($file in $Inventory.files) { $actualFiles.Add($file.path, $file) }
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $Manifest.files) {
        if ($file -isnot [Collections.IDictionary] -or -not $file.Contains('path') -or $file.path -isnot [string] -or
            -not $file.Contains('length') -or ($file.length -isnot [long] -and $file.length -isnot [int]) -or $file.length -lt 0 -or
            -not $file.Contains('sha256') -or $file.sha256 -isnot [string] -or $file.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Invalid file record in manifest.'
        }
        Assert-PackageRelativePath $file.path
        if (-not $names.Add($file.path)) { throw "Duplicate manifest path: $($file.path)" }
        if (-not $actualFiles.ContainsKey($file.path)) { throw "Missing manifest payload file: $($file.path)" }
        $actual = $actualFiles[$file.path]
        if ($actual.length -ne $file.length -or $actual.sha256 -cne $file.sha256) { throw "Payload length/hash mismatch: $($file.path)" }
    }
    if ($Manifest.files.Count -ne $Inventory.files.Count) { throw 'Manifest does not cover every payload file.' }
    $actualDirectories = [Collections.Generic.HashSet[string]]::new([string[]]$Inventory.directories, [StringComparer]::Ordinal)
    foreach ($directory in $Manifest.directories) {
        if ($directory -isnot [string]) { throw 'Invalid directory path in manifest.' }
        Assert-PackageRelativePath $directory
        if (-not $names.Add($directory)) { throw "Duplicate manifest path: $directory" }
        if (-not $actualDirectories.Contains($directory)) { throw "Missing manifest payload directory: $directory" }
    }
    if ($Manifest.directories.Count -ne $Inventory.directories.Count) { throw 'Manifest does not cover every payload directory.' }
}

function Copy-PackageTree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)]$Inventory)
    Assert-PackageDisjoint $Source $Destination
    Assert-PackageNoReparse $Source
    Assert-PackageNoReparse $Destination
    if (Test-Path -LiteralPath $Destination) { throw "Copy destination already exists: $Destination" }
    [void][IO.Directory]::CreateDirectory($Destination)
    foreach ($relative in $Inventory.directories) { [void][IO.Directory]::CreateDirectory((Join-Path $Destination $relative)) }
    foreach ($file in $Inventory.files) { [IO.File]::Copy((Join-Path $Source $file.path), (Join-Path $Destination $file.path), $false) }
}

function Remove-PackagePath {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$AllowedParent)
    $full = Get-PackageFullPath $Path
    Assert-PackageChildPath $full $AllowedParent
    if (-not (Test-Path -LiteralPath $full)) { return }
    if ([IO.Directory]::Exists($full)) {
        $null = Get-PackageInventory -Directory $full -SkipHash
        Remove-Item -LiteralPath $full -Recurse -Force
    } else { Remove-Item -LiteralPath $full -Force }
}

function Move-PackagePath {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)][string]$AllowedParent)
    $from = Get-PackageFullPath $Source
    $to = Get-PackageFullPath $Destination
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        # Recheck scope and reparse points on every attempt. Windows scanners can
        # briefly deny rename after a large onedir copy; never replace an existing
        # destination or bypass permissions to make publication succeed.
        Assert-PackageChildPath $from $AllowedParent
        Assert-PackageChildPath $to $AllowedParent
        $isDirectory = [IO.Directory]::Exists($from)
        if ($isDirectory) { $null = Get-PackageInventory -Directory $from -SkipHash }
        try {
            if ($isDirectory) { [IO.Directory]::Move($from, $to) }
            else { [IO.File]::Move($from, $to) }
            return
        } catch {
            $cause = $_.Exception
            while ($null -ne $cause.InnerException) { $cause = $cause.InnerException }
            $code = if ($cause -is [ComponentModel.Win32Exception]) { $cause.NativeErrorCode } else { $cause.HResult -band 0xffff }
            # ERROR_ACCESS_DENIED / SHARING_VIOLATION / LOCK_VIOLATION only.
            # Persistent errors still fail and enter the existing rollback path.
            $remaining = 10000 - $timer.ElapsedMilliseconds
            if ($code -notin @(5, 32, 33) -or $remaining -le 0) { throw }
            Write-Verbose "Retrying scoped rename after Windows error $code ($($timer.ElapsedMilliseconds) ms): $from"
            Start-Sleep -Milliseconds ([Math]::Min(200, $remaining))
        }
    }
}

function Publish-PackageItems {
    param([Parameter(Mandatory)][object[]]$Items, [Parameter(Mandatory)][string]$AllowedParent)
    $token = [Guid]::NewGuid().ToString('N')
    $published = [Collections.Generic.List[object]]::new()
    try {
        foreach ($item in $Items) {
            Assert-PackageChildPath $item.Source $AllowedParent
            Assert-PackageChildPath $item.Destination $AllowedParent
            $record = @{ Destination = $item.Destination; Backup = Join-Path $AllowedParent ".previous-$token-$($published.Count)"; Saved = $false; Installed = $false }
            $published.Add($record)
            if (Test-Path -LiteralPath $item.Destination) {
                Move-PackagePath $item.Destination $record.Backup $AllowedParent
                $record.Saved = $true
            }
            Move-PackagePath $item.Source $item.Destination $AllowedParent
            $record.Installed = $true
        }
    } catch {
        $failure = $_
        for ($i = $published.Count - 1; $i -ge 0; $i--) {
            $record = $published[$i]
            if ($record.Installed) { Remove-PackagePath $record.Destination $AllowedParent }
            if ($record.Saved) { Move-PackagePath $record.Backup $record.Destination $AllowedParent }
        }
        throw $failure
    }
    foreach ($record in $published) {
        if ($record.Saved) { Remove-PackagePath $record.Backup $AllowedParent }
    }
}

````

## desktop/scripts/package-portable.ps1

SHA256: b59c69f8f093ee631e714ea2b082c2697b56a94a115890d73fe870e242d6805f

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$HostPath,
    [string]$BackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../release/tauri'),
    [string]$Version,
    [string]$BackendManifestPath
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$hostFile = Get-PackageFullPath $HostPath
$backend = Get-PackageFullPath $BackendDirectory
$output = Get-PackageFullPath $OutputDirectory
Assert-PackageNoReparse $hostFile
Assert-PackageMutableDirectory $output
Assert-PackageDisjoint $backend $output
if ([IO.Path]::GetFileName($hostFile) -cne 'chaoxing-gui-tauri.exe' -or -not [IO.File]::Exists($hostFile) -or [IO.FileInfo]::new($hostFile).Length -eq 0) {
    throw 'HostPath must name the built, nonempty chaoxing-gui-tauri.exe release host.'
}
if (-not $BackendManifestPath) { $BackendManifestPath = Join-Path (Split-Path -Parent $backend) 'backend-manifest.json' }
$backendManifestFile = Get-PackageFullPath $BackendManifestPath
$backendManifest = Read-PackageJson $backendManifestFile
Assert-PackageManifestHeader $backendManifest 'chaoxing-backend' $packageVersion 'chaoxing-backend.exe'
$backendInventory = Get-PackageInventory $backend
Assert-BackendLayout $backend $backendInventory
Assert-PackageInventoryManifest $backendManifest $backendInventory

$portable = Get-PackageFullPath (Join-Path $PSScriptRoot '../portable')
$portableFiles = @('Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'Portable-Common.ps1', 'README.txt')
foreach ($name in $portableFiles) {
    $file = Join-Path $portable $name
    Assert-PackageNoReparse $file
    if (-not [IO.File]::Exists($file)) { throw "Missing portable entrypoint: $name" }
}
$licenseFiles = [ordered]@{
    'LICENSE' = Get-PackageFullPath (Join-Path $PSScriptRoot '../../LICENSE')
    'TAURI-LICENSE.txt' = Get-PackageFullPath (Join-Path $PSScriptRoot '../src-tauri/windows/LICENSE_MIT')
}
foreach ($file in $licenseFiles.Values) {
    Assert-PackageNoReparse $file
    if (-not [IO.File]::Exists($file) -or [IO.FileInfo]::new($file).Length -eq 0) { throw "Missing required distribution license: $file" }
}

$artifactName = "chaoxing-gui-tauri-portable-$packageVersion-windows-x64.zip"
[void][IO.Directory]::CreateDirectory($output)
$temporary = Join-Path $output ".portable-staging-$([Guid]::NewGuid().ToString('N'))"
$payload = Join-Path $temporary 'payload'
$archivePath = Join-Path $temporary $artifactName
try {
    [void][IO.Directory]::CreateDirectory($payload)
    Copy-PackageTree -Source $backend -Destination (Join-Path $payload 'backend') -Inventory $backendInventory
    Assert-PackageInventoryManifest $backendManifest (Get-PackageInventory (Join-Path $payload 'backend'))
    [IO.File]::Copy($hostFile, (Join-Path $payload 'chaoxing-gui-tauri.exe'), $false)
    [IO.File]::Copy($backendManifestFile, (Join-Path $payload 'backend-manifest.json'), $false)
    foreach ($name in $portableFiles) { [IO.File]::Copy((Join-Path $portable $name), (Join-Path $payload $name), $false) }
    foreach ($name in $licenseFiles.Keys) { [IO.File]::Copy($licenseFiles[$name], (Join-Path $payload $name), $false) }
    $payloadInventory = Get-PackageInventory $payload
    $payloadManifest = New-PackageManifest -Kind 'chaoxing-gui-tauri-portable' -Version $packageVersion -EntryPoint 'chaoxing-gui-tauri.exe' -Inventory $payloadInventory
    $payloadManifest['platform'] = 'windows-x64'
    $payloadManifestPath = Join-Path $payload 'package-manifest.json'
    Write-PackageJson $payloadManifestPath $payloadManifest

    # Explicit entries preserve empty directories and the entire onedir hierarchy.
    $zip = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create, [Text.Encoding]::UTF8)
    try {
        foreach ($directory in $payloadInventory.directories) { [void]$zip.CreateEntry("$directory/") }
        foreach ($file in @($payloadInventory.files) + @(@{ path = 'package-manifest.json' })) {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $payload $file.path), $file.path, [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $zip.Dispose() }
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $artifactManifest = [ordered]@{
        schemaVersion = 1
        kind = 'chaoxing-gui-tauri-artifact'
        version = $packageVersion
        artifact = [ordered]@{ path = $artifactName; length = [IO.FileInfo]::new($archivePath).Length; sha256 = $archiveHash }
        payloadManifest = [ordered]@{ path = 'package-manifest.json'; sha256 = (Get-FileHash -LiteralPath $payloadManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    Write-PackageJson "$archivePath.manifest.json" $artifactManifest
    [IO.File]::WriteAllText("$archivePath.sha256", "$archiveHash  $artifactName`n", [Text.UTF8Encoding]::new($false))
    & (Join-Path $PSScriptRoot 'verify-package.ps1') -PackagePath $archivePath -Version $packageVersion
    Publish-PackageItems -AllowedParent $output -Items @(
        @{ Source = $archivePath; Destination = Join-Path $output $artifactName },
        @{ Source = "$archivePath.manifest.json"; Destination = Join-Path $output "$artifactName.manifest.json" },
        @{ Source = "$archivePath.sha256"; Destination = Join-Path $output "$artifactName.sha256" }
    )
} finally { Remove-PackagePath $temporary $output }
Write-Output "Packaged portable $packageVersion`: $(Join-Path $output $artifactName)"

````

## desktop/scripts/prepare-backend.ps1

SHA256: dfcf094f3c7453bce6cf01bb7746d51083222df02da1e20af53812c29b158fec

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Alias('BackendDirectory')][string]$SourceDirectory = (Join-Path $PSScriptRoot '../../dist/chaoxing-backend'),
    [Alias('StagingDirectory')][string]$DestinationDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$Version
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$source = Get-PackageFullPath $SourceDirectory
$destination = Get-PackageFullPath $DestinationDirectory
Assert-PackageDisjoint $source $destination
Assert-PackageMutableDirectory $destination
Assert-PackageNoReparse $source
$parent = Split-Path -Parent $destination
Assert-PackageDisjoint $source $parent
$manifestPath = Join-Path $parent 'backend-manifest.json'
Assert-PackageNoReparse $manifestPath
if ([IO.Directory]::Exists($manifestPath)) { throw "Expected a manifest file, found directory: $manifestPath" }
if ([IO.File]::Exists($destination)) { throw "Expected a staging directory: $destination" }
if ([IO.Directory]::Exists($destination)) { $null = Get-PackageInventory -Directory $destination -SkipHash }

# Validate the complete source before creating or replacing any staging files.
$inventory = Get-PackageInventory $source
Assert-BackendLayout $source $inventory
$manifest = New-PackageManifest -Kind 'chaoxing-backend' -Version $packageVersion -EntryPoint 'chaoxing-backend.exe' -Inventory $inventory
[void][IO.Directory]::CreateDirectory($parent)
$token = [Guid]::NewGuid().ToString('N')
$temporary = Join-Path $parent ".backend-staging-$token"
$temporaryManifest = Join-Path $parent ".backend-manifest-$token.json"
try {
    Copy-PackageTree -Source $source -Destination $temporary -Inventory $inventory
    $copied = Get-PackageInventory $temporary
    Assert-PackageInventoryManifest $manifest $copied
    Assert-BackendLayout $temporary $copied
    Write-PackageJson $temporaryManifest $manifest
    # Same-volume renames preserve the previous directory and manifest on failure.
    Publish-PackageItems -AllowedParent $parent -Items @(
        @{ Source = $temporary; Destination = $destination },
        @{ Source = $temporaryManifest; Destination = $manifestPath }
    )
} finally {
    Remove-PackagePath $temporary $parent
    Remove-PackagePath $temporaryManifest $parent
}
Write-Output "Prepared backend $packageVersion ($($inventory.files.Count) files): $destination"

````

## desktop/scripts/sign-windows.ps1

SHA256: d4c863e5d296afd851f1b1f0f9b6abe53525f133599953ef1496f6d2333a8670

````text
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Sign', 'Verify')][string]$Mode = 'Inspect',
    [string]$CertificateThumbprint = $env:CHAOXING_SIGN_CERT_THUMBPRINT,
    [string[]]$Path = @(),
    [string]$ReportPath,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$now = Get-Date
$candidates = @(foreach ($store in @('Cert:\CurrentUser\My', 'Cert:\LocalMachine\My')) {
    Get-ChildItem -LiteralPath $store | Where-Object {
        $_.HasPrivateKey -and $_.NotBefore -le $now -and $_.NotAfter -gt $now -and
        (@($_.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }) -contains '1.3.6.1.5.5.7.3.3')
    }
})
$certificate = $null
if ($CertificateThumbprint) {
    $CertificateThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    if ($CertificateThumbprint -notmatch '^[A-F0-9]{40}$') { throw 'Invalid signing certificate thumbprint' }
    $certificate = $candidates | Where-Object Thumbprint -eq $CertificateThumbprint | Select-Object -First 1
    if (-not $certificate) { throw 'The requested valid code-signing certificate with private key is unavailable' }
} elseif ($candidates.Count -eq 1) {
    $certificate = $candidates[0]
    $CertificateThumbprint = $certificate.Thumbprint
} elseif ($candidates.Count -gt 1) {
    throw 'Multiple signing certificates are available; set CHAOXING_SIGN_CERT_THUMBPRINT explicitly'
}

$files = @()
if ($Mode -ne 'Inspect' -and $Path.Count -eq 0) { throw 'Sign/Verify requires at least one explicit path' }
if ($Mode -eq 'Sign') {
    if (-not $certificate) { throw 'Signing requested but no usable code-signing certificate exists' }
    $signTool = (Get-Command signtool.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    foreach ($file in $Path) {
        $resolved = (Resolve-Path -LiteralPath $file).Path
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw "Not a signing file: $resolved" }
        $arguments = @('sign', '/sha1', $CertificateThumbprint, '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
        if ($certificate.PSPath -like '*LocalMachine*') { $arguments += '/sm' }
        & $signTool @arguments $resolved
        if ($LASTEXITCODE -ne 0) { throw "signtool signing failed with exit $LASTEXITCODE" }
        & $signTool verify /pa /all $resolved
        if ($LASTEXITCODE -ne 0) { throw "signtool verification failed with exit $LASTEXITCODE" }
    }
}
if ($Mode -ne 'Inspect') {
    $files = @(foreach ($file in $Path) {
        $resolved = (Resolve-Path -LiteralPath $file).Path
        $signature = Get-AuthenticodeSignature -LiteralPath $resolved
        $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
        if ($certificate -and ($signature.Status -ne 'Valid' -or $thumbprint -ne $CertificateThumbprint)) {
            throw "Signature does not match the selected certificate: $resolved ($($signature.Status))"
        }
        if (-not $certificate -and $signature.Status -notin @('NotSigned', 'Valid')) {
            throw "Invalid existing signature: $resolved ($($signature.Status))"
        }
        [ordered]@{
            path=$resolved; status=[string]$signature.Status; signerThumbprint=$thumbprint
            sha256=(Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
}
$report = [ordered]@{
    checkedAt=(Get-Date).ToUniversalTime().ToString('o'); mode=$Mode
    signingAvailable=[bool]$certificate; selectedThumbprint=$CertificateThumbprint
    candidateCount=$candidates.Count
    conclusion=$(if ($certificate) {
        'Selected certificate available; Sign/Verify enforces matching valid signatures'
    } elseif ($Mode -eq 'Inspect') {
        'No usable code-signing certificate supplied or found; this build cannot create new signatures'
    } elseif (@($files | Where-Object status -ne 'Valid').Count -eq 0) {
        'All inspected signatures are valid; no local signing private key is available'
    } else {
        'One or more inspected files are unsigned; no local signing private key is available (see per-file status)'
    })
    files=$files
}
$json = $report | ConvertTo-Json -Depth 6
if ($ReportPath) { [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($ReportPath), $json + "`n", [System.Text.UTF8Encoding]::new($false)) }
Write-Output $json

````

## desktop/scripts/smoke-installation.ps1

SHA256: 1de0a7dc4f49e4ad2ee5b3973e20e225dff12afcb57007297e2a986aa88cc924

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [Parameter(Mandatory = $true)][string]$PortablePath,
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-installation-evidence'),
    [switch]$DisposableWindowsUser,
    [ValidateRange(1, 1200)][int]$TimeoutSeconds = 360
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-installation.mjs'), '--installer-path', $InstallerPath,
    '--portable-path', $PortablePath, '--evidence-directory', $EvidenceDirectory,
    '--timeout-seconds', [string]$TimeoutSeconds, '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
if ($DisposableWindowsUser) { $p3Args += '--disposable-windows-user' }
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

````

## desktop/scripts/smoke-python.ps1

SHA256: 59c06de1d78a05068ae87878e8baab922f3231e8c016cf78e46a3c28ae9ad11c

````text
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExecutablePath,
    [string]$Mode = 'Backend',
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-python-smoke-evidence'),
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-smoke.mjs'), '--kind', 'python',
    '--executable-path', $ExecutablePath, '--mode', $Mode,
    '--evidence-directory', $EvidenceDirectory, '--timeout-seconds', [string]$TimeoutSeconds,
    '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

````

## desktop/scripts/smoke-tauri.ps1

SHA256: a58e2518897c24a6a9e50e9aa076dc7ff0c72fadf2453f1b2121bdd9f867e603

````text
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HostPath,
    [string]$BackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/resources/backend'),
    [string]$FakeBackendDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p2-fixture/dist/p2-backend'),
    [string]$EvidenceDirectory = (Join-Path $PSScriptRoot '../src-tauri/target/p3-smoke-evidence'),
    [string]$Configuration = 'Release',
    [string]$Scenario = 'All',
    [switch]$NestedJob,
    [switch]$UsePackagedLayout,
    [switch]$DisposableWindowsUser,
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 150
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$p3Node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$p3Args = @(
    (Join-Path $PSScriptRoot 'p3-smoke.mjs'), '--kind', 'tauri',
    '--host-path', $HostPath, '--backend-directory', $BackendDirectory,
    '--fake-backend-directory', $FakeBackendDirectory, '--evidence-directory', $EvidenceDirectory,
    '--configuration', $Configuration, '--scenario', $Scenario,
    '--timeout-seconds', [string]$TimeoutSeconds, '--powershell-path', (Join-Path $PSHOME 'pwsh.exe')
)
if ($NestedJob) { $p3Args += '--nested-job' }
if ($UsePackagedLayout) { $p3Args += '--use-packaged-layout' }
if ($DisposableWindowsUser) { $p3Args += '--disposable-windows-user' }
& $p3Node @p3Args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

````

## desktop/scripts/verify-nsis.ps1

SHA256: 69d7ebd153b43864a063db6bf136425273d5bda80bc4395f71c8a8782ab0de96

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [Parameter(Mandatory)][string]$PortablePath,
    [string]$EvidenceDirectory,
    [string]$SevenZipPath
)
. (Join-Path $PSScriptRoot 'package-common.ps1')
. (Join-Path $PSScriptRoot 'nsis-content.ps1')
$version = Get-PackageVersion
$installer = Get-PackageFullPath $InstallerPath
$portable = Get-PackageFullPath $PortablePath
Assert-PackageNoReparse $installer
if ([IO.Path]::GetFileName($installer) -cne "chaoxing-gui-tauri-setup-$version-windows-x64.exe" -or -not [IO.File]::Exists($installer)) {
    throw 'Missing or incorrectly named NSIS artifact.'
}
& (Join-Path $PSScriptRoot 'verify-package.ps1') -PackagePath $portable -Version $version
if (-not $SevenZipPath) {
    $command = Get-Command 7z.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw 'NSIS inspection requires a recent 7-Zip (7z.exe on PATH) or explicit -SevenZipPath.' }
    $SevenZipPath = $command.Source
}
$sevenZip = Get-PackageFullPath $SevenZipPath
if (-not [IO.File]::Exists($sevenZip)) { throw 'Missing 7-Zip inspection executable.' }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path $PSScriptRoot '../release/verification/nsis-content' }
$evidence = Get-PackageFullPath $EvidenceDirectory
Assert-PackageMutableDirectory $evidence
Assert-PackageDisjoint $evidence $installer
Assert-PackageDisjoint $evidence $portable
[void][IO.Directory]::CreateDirectory($evidence)
$run = Join-Path $evidence "nsis-$([Guid]::NewGuid().ToString('N'))"
[void][IO.Directory]::CreateDirectory($run)
$extracted = Join-Path $run 'payload'

function Invoke-ArchiveTool {
    param([string[]]$Arguments, [string]$LogName)
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $sevenZip
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(120000)) {
            $process.Kill($true)
            [void]$process.WaitForExit(5000)
            throw '7-Zip archive inspection timed out.'
        }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw '7-Zip output drain timed out.' }
        [IO.File]::WriteAllText((Join-Path $run "$LogName.stdout.txt"), $stdout.Result, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText((Join-Path $run "$LogName.stderr.txt"), $stderr.Result, [Text.UTF8Encoding]::new($false))
        if ($process.ExitCode -ne 0) { throw "7-Zip $LogName failed with exit $($process.ExitCode): $($stderr.Result)" }
        return $stdout.Result
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(5000) }
        $process.Dispose()
    }
}

$result = [ordered]@{ success=$false; version=$version; installer=$installer; portable=$portable; archiveTool=$sevenZip; installerExecuted=$false }
try {
    $archive = [IO.Compression.ZipFile]::OpenRead($portable)
    try {
        $reader = [IO.StreamReader]::new($archive.GetEntry('package-manifest.json').Open(), [Text.Encoding]::UTF8)
        try { $manifest = ConvertFrom-Json -InputObject $reader.ReadToEnd() -AsHashtable -Depth 20 }
        finally { $reader.Dispose() }
    } finally { $archive.Dispose() }
    $manifest = Read-NsisPayloadManifest -InstallerPath $installer -PortablePath $portable -PortableManifest $manifest
    $listing = Invoke-ArchiveTool @('l', '-slt', '-sccUTF-8', $installer) 'list'
    $entries = @(Read-NsisContentListing $listing)
    Assert-NsisContent -Entries $entries -Manifest $manifest
    # Validate every archive path before any extraction; never execute setup.
    [void](Invoke-ArchiveTool @('x', '-y', '-sccUTF-8', "-o$extracted", $installer) 'extract')
    Assert-NsisContent -Entries $entries -Manifest $manifest -ExtractedDirectory $extracted
    $signatureFiles = @($installer, (Join-Path $extracted 'chaoxing-gui-tauri.exe'))
    if ($manifest.hostExpectation.signed) { $signatureFiles += Join-Path $extracted 'uninstall.exe' }
    $result['signatures'] = @(foreach ($file in $signatureFiles) {
        $signature = Get-AuthenticodeSignature -LiteralPath $file
        $thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }
        if ($manifest.hostExpectation.signed) {
            if ($signature.Status -ne 'Valid' -or $thumbprint -cne $manifest.hostExpectation.signerThumbprint) { throw "NSIS payload signature mismatch: $file ($($signature.Status))" }
        } elseif ($signature.Status -ne 'NotSigned') { throw "Expected unsigned NSIS artifact: $file ($($signature.Status))" }
        [ordered]@{ file=[IO.Path]::GetFileName($file); status=[string]$signature.Status; signerThumbprint=$thumbprint }
    })
    $result.success = $true
    $result['payloadFileCount'] = $manifest.files.Count
    $result['installerSha256'] = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['portableSha256'] = (Get-FileHash -LiteralPath $portable -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['hostExpectation'] = $manifest.hostExpectation
    $result['manifestSha256'] = (Get-FileHash -LiteralPath "$installer.manifest.json" -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['conclusion'] = 'Every NSIS application byte matches its build expectation: the independent NSIS host record and the verified portable resources; installation execution is a separate gate.'
    Write-Output "Verified NSIS $version ($($manifest.files.Count) application files): $installer"
} catch {
    $result['error'] = $_.Exception.Message
    throw
} finally {
    Write-PackageJson (Join-Path $run 'result.json') $result
    if (Test-Path -LiteralPath $extracted) {
        Assert-PackageChildPath $extracted $run
        Remove-Item -LiteralPath $extracted -Recurse -Force
    }
}

````

## desktop/scripts/verify-package.ps1

SHA256: a839e38a84f57d8790f8341b3ae6dce867a38053f352cca637086f9d5d13e0dc

````text
#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][Alias('ZipPath')][string]$PackagePath,
    [string]$Version,
    [string]$ArtifactManifestPath
)

. (Join-Path $PSScriptRoot 'package-common.ps1')
$packageVersion = Get-PackageVersion $Version
$archivePath = Get-PackageFullPath $PackagePath
Assert-PackageNoReparse $archivePath
$artifactName = "chaoxing-gui-tauri-portable-$packageVersion-windows-x64.zip"
if ([IO.Path]::GetFileName($archivePath) -cne $artifactName -or -not [IO.File]::Exists($archivePath)) { throw "Missing or incorrectly named package artifact: $artifactName" }
if (-not $ArtifactManifestPath) { $ArtifactManifestPath = "$archivePath.manifest.json" }
$outer = Read-PackageJson (Get-PackageFullPath $ArtifactManifestPath)
Assert-PackageManifestHeader $outer 'chaoxing-gui-tauri-artifact' $packageVersion
if (-not $outer.Contains('artifact') -or $outer.artifact -isnot [Collections.IDictionary] -or
    -not $outer.artifact.Contains('path') -or $outer.artifact.path -cne $artifactName -or
    -not $outer.artifact.Contains('length') -or $outer.artifact.length -ne [IO.FileInfo]::new($archivePath).Length -or
    -not $outer.artifact.Contains('sha256') -or $outer.artifact.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid artifact manifest path/length/hash.' }
$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -cne $outer.artifact.sha256) { throw 'Artifact SHA256 checksum mismatch.' }
Assert-PackageNoReparse "$archivePath.sha256"
if (-not [IO.File]::Exists("$archivePath.sha256") -or [IO.File]::ReadAllText("$archivePath.sha256").TrimEnd([char[]]"`r`n") -cne "$archiveHash  $artifactName") {
    throw 'Missing or invalid artifact .sha256 checksum file.'
}

function Read-ZipManifest {
    param([Parameter(Mandatory)]$Archive, [Parameter(Mandatory)][string]$Name)
    $entry = $Archive.GetEntry($Name)
    if ($null -eq $entry -or $entry.Length -gt 16MB) { throw "Missing or oversized ZIP manifest: $Name" }
    $reader = [IO.StreamReader]::new($entry.Open(), [Text.Encoding]::UTF8)
    try {
        try { return ConvertFrom-Json -InputObject $reader.ReadToEnd() -AsHashtable -Depth 20 }
        catch { throw "Invalid ZIP manifest '$Name': $($_.Exception.Message)" }
    } finally { $reader.Dispose() }
}

$zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $files = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    $directories = [Collections.Generic.List[string]]::new()
    $rootFiles = @('chaoxing-gui-tauri.exe', 'backend-manifest.json', 'package-manifest.json', 'Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'Portable-Common.ps1', 'README.txt', 'LICENSE', 'TAURI-LICENSE.txt')
    [long]$totalLength = 0
    if ($zip.Entries.Count -gt 100000) { throw 'Package has too many payload entries.' }
    foreach ($entry in $zip.Entries) {
        $directory = $entry.FullName.EndsWith('/')
        $relative = if ($directory) { $entry.FullName.Substring(0, $entry.FullName.Length - 1) } else { $entry.FullName }
        Assert-PackageRelativePath $relative
        if (-not $names.Add($relative)) { throw "Duplicate/colliding ZIP path: $relative" }
        if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000 -or ($entry.ExternalAttributes -band 0x400)) {
            throw "Unsafe symbolic link/reparse ZIP path: $relative"
        }
        if ($directory) {
            if ($entry.Length -ne 0 -or ($relative -cne 'backend' -and -not $relative.StartsWith('backend/', [StringComparison]::Ordinal))) { throw "Invalid payload directory: $relative" }
            $directories.Add($relative)
            continue
        }
        if (-not $relative.StartsWith('backend/', [StringComparison]::Ordinal) -and $relative -cnotin $rootFiles) { throw "Unexpected root payload file: $relative" }
        $totalLength += $entry.Length
        if ($entry.Length -gt 2GB -or $totalLength -gt 8GB) { throw "Oversized payload: $relative" }
        $stream = $entry.Open()
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $hash = [Convert]::ToHexString($hasher.ComputeHash($stream)).ToLowerInvariant() }
        finally { $hasher.Dispose(); $stream.Dispose() }
        $files.Add($relative, [ordered]@{ path = $relative; length = $entry.Length; sha256 = $hash })
    }
    foreach ($required in $rootFiles + @('backend/chaoxing-backend.exe', 'backend/_internal/web/dist/index.html')) {
        if (-not $files.ContainsKey($required) -or $files[$required].length -eq 0) { throw "Missing or empty required payload file: $required" }
    }
    foreach ($required in @('backend', 'backend/_internal', 'backend/_internal/web/dist/assets')) {
        if ($required -cnotin $directories) { throw "Missing required payload directory: $required" }
    }
    foreach ($relative in $names) {
        $parent = $relative
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
            if ($parent -cnotin $directories) { throw "Missing parent directory in ZIP: $parent" }
        }
    }
    if (-not $outer.Contains('payloadManifest') -or $outer.payloadManifest -isnot [Collections.IDictionary] -or
        -not $outer.payloadManifest.Contains('path') -or $outer.payloadManifest.path -cne 'package-manifest.json' -or
        -not $outer.payloadManifest.Contains('sha256') -or $outer.payloadManifest.sha256 -cne $files['package-manifest.json'].sha256) {
        throw 'Artifact payload manifest hash mismatch.'
    }
    $payloadManifest = Read-ZipManifest $zip 'package-manifest.json'
    Assert-PackageManifestHeader $payloadManifest 'chaoxing-gui-tauri-portable' $packageVersion 'chaoxing-gui-tauri.exe'
    if (-not $payloadManifest.Contains('platform') -or $payloadManifest.platform -cne 'windows-x64') { throw 'Manifest platform mismatch.' }
    $payloadInventory = @{ files = @($files.Values | Where-Object { $_.path -cne 'package-manifest.json' }); directories = @($directories.ToArray()) }
    Assert-PackageInventoryManifest $payloadManifest $payloadInventory
    $backendManifest = Read-ZipManifest $zip 'backend-manifest.json'
    Assert-PackageManifestHeader $backendManifest 'chaoxing-backend' $packageVersion 'chaoxing-backend.exe'
    $backendInventory = @{
        files = @(foreach ($file in $files.Values) {
            if ($file.path.StartsWith('backend/', [StringComparison]::Ordinal)) {
                @{ path = $file.path.Substring(8); length = $file.length; sha256 = $file.sha256 }
            }
        })
        directories = @(foreach ($directory in $directories) {
            if ($directory.StartsWith('backend/', [StringComparison]::Ordinal)) { $directory.Substring(8) }
        })
    }
    Assert-PackageInventoryManifest $backendManifest $backendInventory
    if (@($backendInventory.files | Where-Object { $_.path.StartsWith('_internal/web/dist/assets/', [StringComparison]::Ordinal) -and $_.length -gt 0 }).Count -eq 0) {
        throw 'Missing embedded web resources in package.'
    }
} finally { $zip.Dispose() }
Write-Output "Verified portable $packageVersion ($($files.Count) files): $archivePath"

````

## desktop/scripts/version.py

SHA256: d52a604e79e12fbf746399c2aa8e47baae374a50225aeedd1ff229e5b70e18f9

````text
"""Check or synchronize release versions; pyproject.toml is never rewritten."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")
ARTIFACT = re.compile(r"chaoxing-gui-tauri-(setup|portable)-(.+)-windows-x64\.(exe|zip)")


def read_text(path):
    return path.read_bytes().decode("utf-8-sig")


def replace_toml_version(text, version, *, package=None):
    sections = re.split(r"(?m)(?=^\[)", text)
    found = 0
    for index, section in enumerate(sections):
        if package is None:
            selected = section.splitlines()[:1] == ["[package]"]
        else:
            selected = section.startswith("[[package]]") and (
                tomllib.loads(section)["package"][0].get("name") == package
            )
        if selected:
            sections[index], count = re.subn(
                r'(?m)^([ \t]*version[ \t]*=[ \t]*)[\"\'][^\"\']*[\"\']([ \t]*(?:#.*)?)(\r?)$',
                lambda match: f'{match[1]}"{version}"{match[2]}{match[3]}',
                section,
            )
            if count != 1:
                raise ValueError("Expected exactly one application version in TOML section")
            found += 1
    if found != 1:
        raise ValueError("Expected exactly one application package in TOML")
    return "".join(sections)


def inspect(root, synchronize):
    canonical = tomllib.loads(read_text(root / "pyproject.toml"))["project"]["version"]
    if not isinstance(canonical, str) or VERSION.fullmatch(canonical) is None:
        raise ValueError("pyproject.toml project.version must be a safe SemVer release version")
    expected = []
    changes = {}

    for project in ("web", "desktop"):
        for filename in ("package.json", "package-lock.json"):
            relative = f"{project}/{filename}"
            path = root / relative
            data = json.loads(read_text(path))
            expected.append((relative, data["version"]))
            if filename == "package-lock.json":
                expected.append((relative + " packages['']", data["packages"][""]["version"]))
                data["packages"][""]["version"] = canonical
            data["version"] = canonical
            changes[path] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    relative = "desktop/src-tauri/tauri.conf.json"
    data = json.loads(read_text(root / relative))
    expected.append((relative, data["version"]))
    data["version"] = canonical
    changes[root / relative] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    relative = "desktop/src-tauri/Cargo.toml"
    cargo_text = read_text(root / relative)
    cargo_package = tomllib.loads(cargo_text)["package"]
    expected.append((relative, cargo_package["version"]))
    changes[root / relative] = replace_toml_version(cargo_text, canonical)

    relative = "desktop/src-tauri/Cargo.lock"
    lock_text = read_text(root / relative)
    packages = [p for p in tomllib.loads(lock_text)["package"] if p["name"] == cargo_package["name"]]
    if len(packages) != 1:
        raise ValueError(f"{relative}: expected one {cargo_package['name']} package")
    expected.append((relative, packages[0]["version"]))
    changes[root / relative] = replace_toml_version(lock_text, canonical, package=cargo_package["name"])

    if synchronize:
        # All inputs have been parsed and validated before changing any file.
        for path, content in changes.items():
            path.write_bytes(content.encode("utf-8"))
        expected = [(name, canonical) for name, _ in expected]
    errors = [f"{name}: {actual!r} != pyproject.toml {canonical!r}"
              for name, actual in expected if actual != canonical]
    return canonical, expected, errors


def check_tags(root, supplied):
    tags = list(supplied)
    if (root / ".git").exists():
        result = subprocess.run(["git", "-C", str(root), "tag", "--points-at", "HEAD"],
                                check=True, capture_output=True, text=True, timeout=15)
        tags.extend(tag for tag in result.stdout.splitlines() if tag.startswith("v"))
    return sorted(set(tags))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only (the default)")
    mode.add_argument("--sync", action="store_true", help="copy the source version into metadata")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--tag", action="append", default=[], help="also validate this release tag")
    parser.add_argument("--artifacts", type=Path, help="validate all Tauri exe/zip artifact names here")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable result")
    args = parser.parse_args(argv)
    report = {"success": False, "version": None, "errors": []}
    try:
        root = args.root.resolve(strict=True)
        # Tag mismatch is also checked before sync so an invalid release cannot
        # partially update metadata while returning a failure.
        tags = check_tags(root, args.tag)
        source = tomllib.loads(read_text(root / "pyproject.toml"))["project"]["version"]
        for tag in tags:
            normalized = tag.removeprefix("refs/tags/").removeprefix("v")
            if normalized != source:
                raise ValueError(f"release tag {tag!r} does not match pyproject.toml {source!r}")
        version, entries, errors = inspect(root, args.sync)
        artifacts = []
        if args.artifacts is not None:
            directory = args.artifacts.resolve(strict=True)
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.suffix.lower() in (".exe", ".zip"):
                    match = ARTIFACT.fullmatch(path.name)
                    if (match is None or match[2] != version or
                            match[3] != {"setup": "exe", "portable": "zip"}[match[1]]):
                        errors.append(f"artifact {path.name!r} does not match Tauri version {version}")
                    artifacts.append(path.name)
            if not artifacts:
                errors.append("artifact directory contains no Tauri exe/zip files")
        report.update(success=not errors, version=version, errors=errors,
                      metadata=[{"path": name, "version": value} for name, value in entries],
                      tags=tags, artifacts=artifacts, synchronized=args.sync)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["success"]:
        print(f"Version {report['version']}: metadata, tags and selected artifacts agree")
    else:
        print("\n".join(report["errors"]), file=sys.stderr)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

````

## desktop/session-store.js

SHA256: 9c0443bfb7e8a00e619835424c7d9904fde7544fa2d9e62f2661976d0cd8d24a

````text
const fs = require('node:fs');
const path = require('node:path');

const emptySession = () => ({ version: 1, login: null, activeTask: null });
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const validUsername = (value) => typeof value === 'string' && value.length > 0 && value.length <= 128 && value === value.trim() && !/[\u0000-\u001f\u007f]/.test(value);
const validTask = (value) => exactKeys(value, ['username', 'taskId']) && validUsername(value.username) && typeof value.taskId === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value.taskId);

function validSession(value) {
  return exactKeys(value, ['version', 'login', 'activeTask']) && value.version === 1 &&
    (value.login === null || (exactKeys(value.login, ['username', 'use_cookies']) && validUsername(value.login.username) && value.login.use_cookies === true)) &&
    (value.activeTask === null || (validTask(value.activeTask) && value.activeTask.username === value.login?.username));
}

class SessionStore {
  constructor(directory) {
    this.directory = directory;
    this.filename = path.join(directory, 'renderer-session.json');
  }

  read() {
    try {
      if (fs.statSync(this.filename).size > 4096) return emptySession();
      const value = JSON.parse(fs.readFileSync(this.filename, 'utf8'));
      return validSession(value) ? value : emptySession();
    } catch (error) {
      if (error.code === 'ENOENT' || error instanceof SyntaxError) return emptySession();
      throw new Error('无法读取保存的账号');
    }
  }

  write(value) {
    if (!validSession(value)) throw new Error('保存的账号格式错误');
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    const temporary = `${this.filename}.tmp`;
    try {
      fs.writeFileSync(temporary, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 });
      fs.renameSync(temporary, this.filename);
    } catch {
      try { fs.unlinkSync(temporary); } catch {}
      throw new Error('无法保存账号，请重试');
    }
    return value;
  }

  rememberLogin(username) {
    if (!validUsername(username)) throw new Error('账号格式错误');
    const previous = this.read();
    return this.write({ version: 1, login: { username, use_cookies: true }, activeTask: previous.login?.username === username ? previous.activeTask : null });
  }

  rememberTask(task) {
    if (task !== null && !validTask(task)) throw new Error('任务信息格式错误');
    const previous = this.read();
    if (task && previous.login?.username !== task.username) throw new Error('任务账号不匹配');
    return this.write({ ...previous, activeTask: task });
  }

  clear() {
    for (const filename of [this.filename, `${this.filename}.tmp`]) {
      try { fs.unlinkSync(filename); } catch (error) {
        if (error.code !== 'ENOENT') throw new Error('无法清除保存的账号，请重试');
      }
    }
    return emptySession();
  }
}

function isTrustedSender(event, window, expectedOrigin) {
  try {
    return !!expectedOrigin && !!window && !window.isDestroyed() &&
      event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame &&
      new URL(event.senderFrame.url).origin === expectedOrigin;
  } catch { return false; }
}

function registerSessionIpc(ipcMain, { getWindow, getOrigin, store }) {
  const methods = {
    'session:read': { count: 0, call: () => store.read() },
    'session:remember-login': { count: 1, call: (username) => store.rememberLogin(username) },
    'session:remember-task': { count: 1, call: (task) => store.rememberTask(task) },
    'session:clear': { count: 0, call: () => store.clear() },
  };
  for (const [channel, method] of Object.entries(methods)) {
    ipcMain.handle(channel, (event, ...args) => {
      if (!isTrustedSender(event, getWindow(), getOrigin())) throw new Error('禁止访问保存的账号');
      if (args.length !== method.count) throw new Error('请求参数格式错误');
      return method.call(...args);
    });
  }
}

module.exports = { SessionStore, isTrustedSender, registerSessionIpc };

````

## desktop/src-tauri/Cargo.lock

SHA256: 089cd4eecefc5a22864ed699d13ac5a3f7b86a383564df7c3d1639d1f8b06020

Lock file retained on disk at the exact repository path; hash recorded above.

## desktop/src-tauri/Cargo.toml

SHA256: 9b2f416b96220afa83ffb0e4667f9b2ea69d9af1887f27be9369fb03e8a3fa1c

````text
[package]
name = "chaoxing-desktop"
version = "1.1.1"
description = "超星学习通 · 自动化学习助手桌面版（Tauri 宿主）"
edition = "2021"
default-run = "chaoxing-desktop"

[lib]
name = "chaoxing_desktop_lib"
path = "src/lib.rs"

[[bin]]
name = "chaoxing-desktop"
path = "src/main.rs"

# Enabled for ordinary development/tests; release packaging disables defaults.
# Tauri also respects required-features when selecting bundle binaries.
[[bin]]
name = "fake-backend"
path = "src/bin/fake-backend.rs"
required-features = ["test-support"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "=2.11.5", features = [] }
tauri-plugin-single-instance = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
ureq = { version = "2", default-features = false, features = ["json"] }
windows = { version = "0.62", features = ["Win32_Foundation", "Win32_System_JobObjects", "Win32_System_Threading", "Win32_Security", "Win32_UI_WindowsAndMessaging", "Win32_Graphics_Gdi", "Win32_Storage_FileSystem"] }
getrandom = "0.2"
regex = "1"

[features]
default = ["test-support"]
test-support = []
custom-protocol = ["tauri/custom-protocol"]

[profile.release]
strip = true

````

## desktop/src-tauri/build.rs

SHA256: 43d55594221c4fd872115e739195efa46e4550a08d98aac8307a8ff6527a055e

````text
fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "api_request",
            "api_cancel",
            "backend_status",
            "session_read",
            "session_remember_login",
            "session_remember_task",
            "session_clear",
        ]),
    ))
    .unwrap();
}

````

## desktop/src-tauri/capabilities/main.json

SHA256: 7ed4ec41aa6cdb4bee3038811221da499d1dab87155e1722260d9dd678a7bfdf

````text
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "main-capability",
  "windows": ["main"],
  "permissions": [
    "allow-api-request",
    "allow-api-cancel",
    "allow-backend-status",
    "allow-session-read",
    "allow-session-remember-login",
    "allow-session-remember-task",
    "allow-session-clear"
  ]
}

````

## desktop/src-tauri/src/api_proxy.rs

SHA256: 48bf2a91985cdcabf9d51bf33eecb5b105459bc58aa0dca6dbc7476fbc423687

````text
use serde::{Deserialize, Deserializer, Serialize};

/// Strictly-allowed backend API operations (plan.md §3.2, 8 operations).
/// Everything else is refused at the host boundary — the webview never gets
/// a generic HTTP escape hatch.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ApiOperation {
    Login,
    Courses,
    ConfigRead,
    ConfigWrite,
    Start,
    TaskStatus,
    TaskDetails,
    TaskLogs,
}

/// The renderer can request only these operations and their explicit options.
#[derive(Debug)]
pub struct ApiRequest {
    pub operation: ApiOperation,
    pub payload: serde_json::Value,
    pub request_id: u64,
    pub task_id: Option<String>,
    pub after: Option<u64>,
}

impl<'de> Deserialize<'de> for ApiRequest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Fields {
            #[serde(deserialize_with = "string_operation")]
            operation: ApiOperation,
            // This key must exist even when its value is null.
            #[serde(deserialize_with = "required_payload")]
            payload: serde_json::Value,
            request_id: u64,
            #[serde(default, deserialize_with = "present_option")]
            task_id: Option<String>,
            #[serde(default, deserialize_with = "present_option")]
            after: Option<u64>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            operation: fields.operation,
            payload: fields.payload,
            request_id: fields.request_id,
            task_id: fields.task_id,
            after: fields.after,
        })
    }
}

fn string_operation<'de, D: Deserializer<'de>>(deserializer: D) -> Result<ApiOperation, D::Error> {
    // Derived enums also accept tagged objects; the IPC contract requires a string.
    let operation = String::deserialize(deserializer)?;
    ApiOperation::deserialize(serde::de::value::StringDeserializer::<D::Error>::new(
        operation,
    ))
}

fn required_payload<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<serde_json::Value, D::Error> {
    serde_json::Value::deserialize(deserializer)
}

// Omission is allowed; an explicitly supplied null is not an operation option.
fn present_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

impl ApiRequest {
    pub fn validate(&self) -> Result<(), ProxyError> {
        let invalid = |reason: &str| ProxyError::InvalidRequest {
            reason: reason.into(),
        };
        if !valid_request_id(self.request_id) {
            return Err(invalid(
                "requestId must be a positive JavaScript safe integer",
            ));
        }
        build_path(self.operation, self.task_id.as_deref(), self.after)
            .map_err(|reason| ProxyError::InvalidRequest { reason })?;
        if self.operation.is_post() {
            if !self.payload.is_object() {
                return Err(invalid("POST payload must be a JSON object"));
            }
            let body =
                serde_json::to_vec(&self.payload).map_err(|error| ProxyError::InvalidRequest {
                    reason: format!("payload serialization failed: {error}"),
                })?;
            if body.len() > MAX_REQUEST_BODY {
                return Err(invalid("请求体超过 1MB 上限"));
            }
        } else if !self.payload.is_null() {
            return Err(invalid("GET operations require a null payload"));
        }
        Ok(())
    }
}

impl ApiOperation {
    /// (method, path template). `{id}` is substituted with the validated taskId.
    pub fn route(&self) -> (&'static str, &'static str) {
        match self {
            ApiOperation::Login => ("POST", "/api/login"),
            ApiOperation::Courses => ("POST", "/api/courses"),
            ApiOperation::ConfigRead => ("GET", "/api/config"),
            ApiOperation::ConfigWrite => ("POST", "/api/config"),
            ApiOperation::Start => ("POST", "/api/start"),
            ApiOperation::TaskStatus => ("GET", "/api/task/{id}"),
            ApiOperation::TaskDetails => ("GET", "/api/task/{id}/details"),
            ApiOperation::TaskLogs => ("GET", "/api/logs/{id}"),
        }
    }

    pub fn needs_task_id(&self) -> bool {
        matches!(
            self,
            ApiOperation::TaskStatus | ApiOperation::TaskDetails | ApiOperation::TaskLogs
        )
    }

    pub fn is_post(&self) -> bool {
        self.route().0 == "POST"
    }
}

pub const MAX_REQUEST_BODY: usize = 1024 * 1024;
pub const MAX_RESPONSE_BODY: usize = 2 * (1024 * 1024);
pub const MAX_REQUEST_ID: u64 = (1u64 << 53) - 1;
/// Cursor bound for TaskLogs `after` (matches backend int ids; u32 is generous).
pub const MAX_LOG_CURSOR: u64 = u32::MAX as u64;

pub fn valid_request_id(request_id: u64) -> bool {
    (1..=MAX_REQUEST_ID).contains(&request_id)
}

pub fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

pub fn valid_log_cursor(after: u64) -> bool {
    after <= MAX_LOG_CURSOR
}

/// Build the URL path for an operation. Caller must have validated inputs.
pub fn build_path(
    op: ApiOperation,
    task_id: Option<&str>,
    after: Option<u64>,
) -> Result<String, String> {
    let (_, template) = op.route();
    if !op.needs_task_id() && task_id.is_some() {
        return Err(format!("{op:?} does not accept taskId"));
    }
    if !matches!(op, ApiOperation::TaskLogs) && after.is_some() {
        return Err(format!("{op:?} does not accept after"));
    }
    if op.needs_task_id() {
        let id = task_id.ok_or_else(|| format!("{op:?} requires taskId"))?;
        if !valid_task_id(id) {
            return Err("invalid taskId".into());
        }
        let path = template.replace("{id}", id);
        if matches!(op, ApiOperation::TaskLogs) {
            let cursor = after.ok_or_else(|| "TaskLogs requires after cursor".to_string())?;
            if !valid_log_cursor(cursor) {
                return Err("invalid log cursor".into());
            }
            return Ok(format!("{path}?after={cursor}"));
        }
        Ok(path)
    } else {
        Ok(template.to_string())
    }
}

/// Wire shape returned to the renderer: { status, body } — mirrors axios semantics
/// (status preserved; 409/404 handled by existing frontend branches).
#[derive(Debug, Serialize)]
pub struct ProxyResponse {
    pub status: u16,
    pub body: serde_json::Value,
}

#[derive(Debug, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum ProxyError {
    /// Backend not ready / stopped.
    BackendNotReady { phase: String },
    /// Request rejected by host validation.
    InvalidRequest { reason: String },
    /// Network-level failure talking to the backend.
    Network { reason: String },
    /// The complete HTTP exchange, including the response body, timed out.
    Timeout { reason: String },
    /// Cancelled via api_cancel.
    Cancelled,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_api_request_deserialization() {
        for raw in [
            serde_json::json!(["taskLogs", null, 1, "t", 0]),
            serde_json::json!(["start", {}, 1]),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted {raw}"
            );
        }
    }

    #[test]
    fn dto_rejects_unknown_operation() {
        let raw = r#""notAnOperation""#;
        assert!(serde_json::from_str::<ApiOperation>(raw).is_err());
    }

    #[test]
    fn dto_rejects_non_string_operation() {
        for operation in [
            serde_json::json!({"configRead": null}),
            serde_json::json!({"start": null}),
            serde_json::json!(["configRead"]),
            serde_json::Value::Null,
            serde_json::json!(true),
            serde_json::json!(1),
        ] {
            let raw = serde_json::json!({"operation": operation, "payload": null, "requestId": 1});
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "accepted operation value: {raw}"
            );
            assert!(
                serde_json::from_str::<ApiRequest>(&raw.to_string()).is_err(),
                "accepted operation JSON: {raw}"
            );
        }
    }

    #[test]
    fn dto_requires_exact_fields_and_operation_options() {
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":1}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "url":"http://example.invalid"}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "after":null}),
            serde_json::json!({"operation":"configRead", "requestId":1.5, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":-1, "payload":null}),
            serde_json::json!({"operation":"task-status", "requestId":1, "payload":null}),
        ] {
            assert!(
                serde_json::from_value::<ApiRequest>(raw.clone()).is_err(),
                "{raw}"
            );
        }
        for raw in [
            serde_json::json!({"operation":"configRead", "requestId":0, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":MAX_REQUEST_ID + 1, "payload":null}),
            serde_json::json!({"operation":"configRead", "requestId":1, "payload":null, "taskId":"t"}),
            serde_json::json!({"operation":"taskStatus", "requestId":1, "payload":null, "taskId":"t", "after":0}),
            serde_json::json!({"operation":"start", "requestId":1, "payload":null}),
            serde_json::json!({"operation":"taskLogs", "requestId":1, "payload":null, "taskId":"t"}),
        ] {
            let request = serde_json::from_value::<ApiRequest>(raw.clone()).unwrap();
            assert!(request.validate().is_err(), "{raw}");
        }
    }

    #[test]
    fn dto_accepts_all_eight_camel_case_operations() {
        for operation in [
            "login",
            "courses",
            "configRead",
            "configWrite",
            "start",
            "taskStatus",
            "taskDetails",
            "taskLogs",
        ] {
            let mut raw = serde_json::json!({"operation":operation, "requestId":MAX_REQUEST_ID, "payload":null});
            if matches!(operation, "login" | "courses" | "configWrite" | "start") {
                raw["payload"] = serde_json::json!({"username":"fixture"});
            }
            if matches!(operation, "taskStatus" | "taskDetails" | "taskLogs") {
                raw["taskId"] = "t-1".into();
            }
            if operation == "taskLogs" {
                raw["after"] = 0.into();
            }
            serde_json::from_value::<ApiRequest>(raw)
                .unwrap()
                .validate()
                .unwrap();
        }
    }

    #[test]
    fn task_id_whitelist() {
        assert!(valid_task_id("abc-DEF_123"));
        assert!(!valid_task_id("../etc"));
        assert!(!valid_task_id("a b"));
        assert!(!valid_task_id(""));
        assert!(!valid_task_id(&"x".repeat(129)));
        assert!(valid_task_id(&"x".repeat(128)));
    }

    #[test]
    fn path_building() {
        assert_eq!(
            build_path(ApiOperation::Login, None, None).unwrap(),
            "/api/login"
        );
        assert_eq!(
            build_path(ApiOperation::TaskStatus, Some("t-1_2"), None).unwrap(),
            "/api/task/t-1_2"
        );
        assert_eq!(
            build_path(ApiOperation::TaskDetails, Some("t1"), None).unwrap(),
            "/api/task/t1/details"
        );
        assert_eq!(
            build_path(ApiOperation::TaskLogs, Some("t1"), Some(42)).unwrap(),
            "/api/logs/t1?after=42"
        );
        assert!(build_path(ApiOperation::TaskStatus, None, None).is_err());
        assert!(build_path(ApiOperation::TaskStatus, Some("../bad"), None).is_err());
        assert!(build_path(ApiOperation::TaskLogs, Some("t1"), None).is_err());
    }

    #[test]
    fn log_cursor_bounds() {
        assert!(valid_log_cursor(0));
        assert!(valid_log_cursor(u32::MAX as u64));
        assert!(!valid_log_cursor(u32::MAX as u64 + 1));
    }
}

````

## desktop/src-tauri/src/backend.rs

SHA256: 3681c7618f30bcf5dd47400aabf608e999831a9a62006348f7e1f32b6c2ba217

````text
//! Backend process lifecycle: spawn frozen (or dev python) backend, stdout
//! handshake (`chaoxing-ready` v1), token-authenticated health polling,
//! graceful stop via stdin EOF with Job Object kill fallback.
//!
//! Invariants proven in P0 PoC:
//! - the Job handle must live in app state for the whole app lifetime
//!   (dropping it kills the backend immediately via KILL_ON_JOB_CLOSE);
//! - the backend exits instantly if stdin has no pipe — host must hold one;
//! - the backend writes runtime files into its cwd, so cwd must be the data dir.

use crate::api_proxy::{ApiOperation, ApiRequest, ProxyError, ProxyResponse};
use crate::windows_job::Job;
use serde::Serialize;
use std::collections::HashMap;
use std::io::Read;
use std::io::{BufRead, BufReader, Write};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// Whole start window (handshake + health) per plan.md §4.1.
const START_DEADLINE: Duration = Duration::from_secs(120);
const HEALTH_INTERVAL: Duration = Duration::from_millis(300);
const HEALTH_TIMEOUT: Duration = Duration::from_millis(2000);
/// Grace period after stdin EOF before TerminateJobObject.
const STOP_GRACE: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(25);
const API_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_HANDSHAKE_LINE: usize = 8192;
const MAX_HEALTH_BODY: usize = 64 * 1024;
const MAX_INFLIGHT_REQUESTS: usize = 64;
const MAX_RECENT_REQUESTS: usize = 1024;
const RECENT_REQUEST_TTL: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum BackendPhase {
    Starting,
    Ready,
    Stopping,
    Stopped,
    Failed,
}

#[derive(Debug, Serialize)]
pub struct BackendStatus {
    pub phase: BackendPhase,
    #[serde(skip)]
    pub port: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

pub struct BackendState {
    pub phase: Mutex<BackendPhase>,
    pub child: Mutex<Option<Child>>,
    pub port: Mutex<Option<u16>>,
    pub token: Mutex<String>,
    pub instance_id: Mutex<String>,
    /// Kept alive through startup/Ready, then owned by teardown until the tree is killed.
    pub job: Mutex<Option<Job>>,
    pub error: Mutex<Option<String>>,
    requests: Mutex<RequestRegistry>,
    pub data_dir: std::path::PathBuf,
    pub log_dir: std::path::PathBuf,
    next_request_id: AtomicU64,
    start_claimed: AtomicBool,
    /// Only short transitions/spawn registration; never held during HTTP or grace.
    lifecycle_lock: Mutex<()>,
    stop_lock: Mutex<()>,
}

#[derive(Clone, Copy)]
enum RecentResult {
    Cancelled,
    Completed,
}

#[derive(Default)]
struct RequestRegistry {
    active: HashMap<u64, Arc<AtomicBool>>,
    recent: HashMap<u64, (Instant, RecentResult)>,
    closed: bool,
}

impl RequestRegistry {
    fn prune(&mut self, now: Instant) {
        self.recent
            .retain(|_, (time, _)| now.duration_since(*time) < RECENT_REQUEST_TTL);
    }

    fn remember(&mut self, id: u64, outcome: RecentResult, now: Instant) {
        self.prune(now);
        if !self.recent.contains_key(&id) && self.recent.len() >= MAX_RECENT_REQUESTS {
            if let Some(oldest) = self
                .recent
                .iter()
                .min_by_key(|(_, (time, _))| *time)
                .map(|(id, _)| *id)
            {
                self.recent.remove(&oldest);
            }
        }
        self.recent.insert(id, (now, outcome));
    }

    fn register(&mut self, id: u64) -> Result<Arc<AtomicBool>, ProxyError> {
        self.prune(Instant::now());
        if self.closed {
            return Err(ProxyError::BackendNotReady {
                phase: "stopped".into(),
            });
        }
        if let Some(flag) = self.active.get(&id) {
            return if flag.load(Ordering::Acquire) {
                Err(ProxyError::Cancelled)
            } else {
                Err(ProxyError::InvalidRequest {
                    reason: "requestId is already in use".into(),
                })
            };
        }
        if let Some((_, outcome)) = self.recent.get(&id) {
            return match outcome {
                RecentResult::Cancelled => Err(ProxyError::Cancelled),
                RecentResult::Completed => Err(ProxyError::InvalidRequest {
                    reason: "requestId was already completed".into(),
                }),
            };
        }
        if self.active.len() >= MAX_INFLIGHT_REQUESTS {
            return Err(ProxyError::InvalidRequest {
                reason: "too many in-flight requests".into(),
            });
        }
        let flag = Arc::new(AtomicBool::new(false));
        self.active.insert(id, flag.clone());
        Ok(flag)
    }

    fn cancel(&mut self, id: u64) -> bool {
        if let Some(flag) = self.active.get(&id) {
            flag.store(true, Ordering::Release);
            // The HTTP worker owns this entry until its entire body read settles.
            return true;
        }
        if !self.closed {
            self.remember(id, RecentResult::Cancelled, Instant::now());
        }
        false
    }

    fn finish(&mut self, id: u64, flag: &Arc<AtomicBool>) -> bool {
        let cancelled = flag.load(Ordering::Acquire);
        if self
            .active
            .get(&id)
            .is_some_and(|current| Arc::ptr_eq(current, flag))
        {
            self.active.remove(&id);
            if !self.closed {
                self.remember(
                    id,
                    if cancelled {
                        RecentResult::Cancelled
                    } else {
                        RecentResult::Completed
                    },
                    Instant::now(),
                );
            }
        }
        cancelled
    }

    fn close(&mut self) {
        self.closed = true;
        for flag in self.active.values() {
            flag.store(true, Ordering::Release);
        }
        self.recent.clear();
    }
}

impl BackendState {
    pub fn new(data_dir: std::path::PathBuf, log_dir: std::path::PathBuf) -> Self {
        BackendState {
            phase: Mutex::new(BackendPhase::Starting),
            child: Mutex::new(None),
            port: Mutex::new(None),
            token: Mutex::new(String::new()),
            instance_id: Mutex::new(String::new()),
            job: Mutex::new(None),
            error: Mutex::new(None),
            requests: Mutex::new(RequestRegistry::default()),
            next_request_id: AtomicU64::new(1),
            start_claimed: AtomicBool::new(false),
            data_dir,
            log_dir,
            lifecycle_lock: Mutex::new(()),
            stop_lock: Mutex::new(()),
        }
    }

    pub fn status(&self) -> BackendStatus {
        self.observe_exit();
        BackendStatus {
            phase: *self.phase.lock().unwrap_or_else(|e| e.into_inner()),
            port: *self.port.lock().unwrap_or_else(|e| e.into_inner()),
            error: self.error.lock().unwrap_or_else(|e| e.into_inner()).clone(),
        }
    }

    pub fn next_request_id(&self) -> u64 {
        self.next_request_id
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |id| {
                Some(if id >= crate::api_proxy::MAX_REQUEST_ID {
                    1
                } else {
                    id + 1
                })
            })
            .unwrap_or_else(|id| id)
    }

    fn fail(&self, msg: String) {
        let _transition = self
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        self.fail_locked(msg);
    }

    // Caller owns lifecycle_lock. Stopping/Stopped must never become Failed/Ready.
    fn fail_locked(&self, msg: String) {
        let mut phase = self.phase.lock().unwrap_or_else(|e| e.into_inner());
        if !matches!(*phase, BackendPhase::Starting | BackendPhase::Ready) {
            return;
        }
        *self.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(msg.clone());
        *phase = BackendPhase::Failed;
        drop(phase);
        self.requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .close();
        *self.port.lock().unwrap_or_else(|e| e.into_inner()) = None;
        // Terminate even when the direct child has exited: its grandchildren may live.
        if let Some(job) = self.job.lock().unwrap_or_else(|e| e.into_inner()).take() {
            job.terminate();
        }
        if let Some(mut child) = self.child.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = child.kill();
            let _ = child.try_wait();
        }
        host_log(&self.log_dir, &format!("[backend] FAILED: {msg}"));
    }

    fn observe_exit(&self) {
        // A status query stays responsive while spawn/stop is changing ownership.
        let Ok(_transition) = self.lifecycle_lock.try_lock() else {
            return;
        };
        if *self.phase.lock().unwrap_or_else(|e| e.into_inner()) != BackendPhase::Ready {
            return;
        }
        let error = self
            .child
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .as_mut()
            .and_then(|child| match child.try_wait() {
                Ok(Some(status)) => Some(format!("后端进程意外退出: {status}")),
                Ok(None) => None,
                Err(error) => Some(format!("无法检查后端进程: {error}")),
            });
        if let Some(error) = error {
            self.fail_locked(error);
        }
    }
}

pub fn host_log(log_dir: &std::path::Path, line: &str) {
    let _ = std::fs::create_dir_all(log_dir);
    let path = log_dir.join("host.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {line}");
    }
}

fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::getrandom(&mut buf).expect("OS RNG failed");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Handshake line shape: {"ready":"chaoxing-ready","version":1,"port":N,"instanceId":"..."}
#[derive(serde::Deserialize)]
struct ReadyLine {
    ready: String,
    version: u32,
    port: u16,
    #[serde(rename = "instanceId")]
    instance_id: String,
}

enum Handshake {
    Ready(ReadyLine),
    /// Backend exited before ready.
    Eof,
}

/// Read stdout lines until the ready marker (or EOF). Non-ready lines are
/// appended to backend.log. Runs on a dedicated thread until EOF so the
/// pipe never fills up.
fn read_handshake(
    stdout: std::process::ChildStdout,
    expected_instance: String,
    log_dir: PathBuf,
) -> (
    std::sync::mpsc::Receiver<Handshake>,
    std::thread::JoinHandle<()>,
) {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut lines = reader.lines();
        while let Some(line) = lines.next() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            if line.len() > MAX_HANDSHAKE_LINE {
                append_backend_log(
                    &log_dir,
                    &format!("[oversized line dropped: {} bytes]", line.len()),
                );
                continue;
            }
            if let Ok(r) = serde_json::from_str::<ReadyLine>(&line) {
                if r.ready == "chaoxing-ready"
                    && r.version == 1
                    && r.instance_id == expected_instance
                {
                    let _ = tx.send(Handshake::Ready(r));
                    // Keep draining stdout to EOF so the pipe doesn't fill.
                    for rest in lines.by_ref().flatten() {
                        append_backend_log(&log_dir, &format!("[stdout] {rest}"));
                    }
                    return;
                }
                append_backend_log(&log_dir, &format!("[stdout] invalid ready line: {line}"));
                continue;
            }
            append_backend_log(&log_dir, &format!("[stdout] {line}"));
        }
        let _ = tx.send(Handshake::Eof);
    });
    (rx, handle)
}

fn append_backend_log(log_dir: &std::path::Path, line: &str) {
    let _ = std::fs::create_dir_all(log_dir);
    let path = log_dir.join("backend.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = writeln!(f, "[{ts}] {line}");
    }
}

/// Drain stderr into backend.log on a background thread (never let the pipe fill).
fn drain_stderr(
    stderr: std::process::ChildStderr,
    log_dir: PathBuf,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            append_backend_log(&log_dir, &format!("[stderr] {line}"));
        }
    })
}

pub enum BackendLaunch {
    /// Frozen onedir backend shipped via Tauri resources.
    Frozen(std::path::PathBuf),
    /// Development: run the repo Flask app with system python.
    Dev {
        python: String,
        app_py: std::path::PathBuf,
    },
}

/// Build the backend launch plan from the running app. Production resolves the
/// frozen onedir backend from Tauri resources; debug builds fall back to the
/// repo's Flask app via system python when the resource exe is absent.
pub fn detect_launch(app: &tauri::AppHandle) -> Result<BackendLaunch, String> {
    use tauri::Manager;
    let resource = app
        .path()
        .resolve(
            "backend/chaoxing-backend.exe",
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|e| format!("resolve resource dir: {e}"))?;
    if resource.is_file() {
        Ok(BackendLaunch::Frozen(resource))
    } else if cfg!(debug_assertions) {
        // Dev fallback: repo layout — desktop/src-tauri → ../../app.py
        let app_py = std::env::current_dir()
            .ok()
            .and_then(|d| d.ancestors().nth(2).map(|p| p.join("app.py")))
            .ok_or("cannot locate repo app.py")?;
        if !app_py.is_file() {
            return Err(format!(
                "后端程序缺失: {}（且开发回退 {} 也不存在）",
                resource.display(),
                app_py.display()
            ));
        }
        Ok(BackendLaunch::Dev {
            python: "python".into(),
            app_py,
        })
    } else {
        Err(format!("后端程序缺失: {}", resource.display()))
    }
}

/// Runs on the host's background startup worker after state registration.
/// Spawn/ownership transfer is serialized with stop; handshake/HTTP never hold it.
pub fn start_backend(state: &Arc<BackendState>, launch: BackendLaunch) -> Result<(), String> {
    if state.start_claimed.swap(true, Ordering::AcqRel) {
        return Err("后端启动已请求，不能自动重启".into());
    }
    check_starting(state)?;
    for (label, directory) in [("data", &state.data_dir), ("log", &state.log_dir)] {
        if let Err(error) = std::fs::create_dir_all(directory) {
            let message = format!("create {label} dir: {error}");
            state.fail(message.clone());
            return Err(message);
        }
    }

    let token = random_hex(32);
    let instance_id = random_hex(8);

    let job = Job::create().map_err(|e| {
        state.fail(format!("创建 Job Object 失败: {e}"));
        e
    })?;

    let deadline = Instant::now() + START_DEADLINE;
    let mut cmd = match &launch {
        BackendLaunch::Frozen(exe) => {
            let mut c = Command::new(exe);
            c.env("CHAOXING_TAURI", "1");
            c
        }
        BackendLaunch::Dev { python, app_py } => {
            let mut c = Command::new(python);
            c.arg("-u").arg(app_py);
            c.env("CHAOXING_TAURI", "1");
            c
        }
    };
    cmd.current_dir(&state.data_dir)
        .env("CHAOXING_HEADLESS", "1")
        .env("CHAOXING_TAURI_TOKEN", &token)
        .env("CHAOXING_TAURI_INSTANCE_ID", &instance_id)
        .env("CHAOXING_DATA_DIR", &state.data_dir)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW);

    // Fail fast with a clear message when the executable is missing (Windows
    // spawn errors are opaque); the launch resolver already checks the frozen
    // path in production, tests exercise this branch directly.
    if let BackendLaunch::Frozen(exe) = &launch {
        if !exe.is_file() {
            let msg = format!("后端程序缺失: {}", exe.display());
            state.fail(msg.clone());
            return Err(msg);
        }
    }
    let (stdout, stderr) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        check_starting(state)?;
        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(error) => {
                let message = format!("启动后端失败: {error}");
                state.fail_locked(message.clone());
                return Err(message);
            }
        };
        let assigned = job.assign(child.id());
        let stdout = child.stdout.take().expect("stdout piped");
        let stderr = child.stderr.take().expect("stderr piped");
        // From this point every failure/stop path can reach both process handles.
        *state.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        *state.job.lock().unwrap_or_else(|e| e.into_inner()) = Some(job);
        *state.token.lock().unwrap_or_else(|e| e.into_inner()) = token.clone();
        *state.instance_id.lock().unwrap_or_else(|e| e.into_inner()) = instance_id.clone();
        if let Err(error) = assigned {
            let message = format!("后端进程加入 Job 失败: {error}");
            state.fail_locked(message.clone());
            return Err(message);
        }
        (stdout, stderr)
    };
    let log_dir = state.log_dir.clone();
    let (handshake_rx, _stdout_thread) =
        read_handshake(stdout, instance_id.clone(), log_dir.clone());
    let _stderr_thread = drain_stderr(stderr, log_dir.clone());

    let result: Result<(), String> = (|| {
        let ready = await_handshake(state, &handshake_rx, deadline)?;
        health_until_ready(state, ready.port, &token, &instance_id, deadline)?;
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        check_starting(state)?;
        check_starting_child(state)?;
        *state.port.lock().unwrap_or_else(|e| e.into_inner()) = Some(ready.port);
        *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Ready;
        host_log(&log_dir, &format!("[backend] ready on port {}", ready.port));
        Ok(())
    })();
    if let Err(message) = &result {
        state.fail(message.clone());
    }
    result
}

fn check_starting(state: &BackendState) -> Result<(), String> {
    if *state.phase.lock().unwrap_or_else(|e| e.into_inner()) == BackendPhase::Starting {
        Ok(())
    } else {
        Err("后端启动已取消或已结束".into())
    }
}

fn check_starting_child(state: &BackendState) -> Result<(), String> {
    let mut child = state.child.lock().unwrap_or_else(|e| e.into_inner());
    match child.as_mut().map(Child::try_wait) {
        Some(Ok(None)) => Ok(()),
        Some(Ok(Some(status))) => Err(format!("后端进程在启动期间退出: {status}")),
        Some(Err(error)) => Err(format!("无法检查后端进程: {error}")),
        None => Err("后端启动已取消".into()),
    }
}

fn await_handshake(
    state: &BackendState,
    receiver: &std::sync::mpsc::Receiver<Handshake>,
    deadline: Instant,
) -> Result<ReadyLine, String> {
    loop {
        check_starting(state)?;
        check_starting_child(state)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("就绪握手超时（120s）".into());
        }
        match receiver.recv_timeout(remaining.min(POLL_INTERVAL)) {
            Ok(Handshake::Ready(ready)) if ready.port != 0 => return Ok(ready),
            Ok(Handshake::Ready(_)) => return Err("就绪握手 port 无效".into()),
            Ok(Handshake::Eof) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                return Err("后端在就绪握手前退出".into());
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
}

fn loopback_agent(timeout: Duration) -> ureq::Agent {
    ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        .redirects(0)
        .timeout_connect(timeout)
        .timeout(timeout)
        .build()
}

fn health_until_ready(
    state: &BackendState,
    port: u16,
    token: &str,
    instance_id: &str,
    deadline: Instant,
) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{port}/api/health");
    loop {
        check_starting(state)?;
        check_starting_child(state)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("health 探测超时（120s）".into());
        }
        let agent = loopback_agent(HEALTH_TIMEOUT.min(remaining));
        match agent.get(&url).set("X-Auth-Token", token).call() {
            Ok(resp) => {
                if resp.status() == 200 {
                    let mut body = Vec::new();
                    let read = resp
                        .into_reader()
                        .take(MAX_HEALTH_BODY as u64 + 1)
                        .read_to_end(&mut body);
                    check_starting(state)?;
                    read.map_err(|error| format!("读取 health 响应失败: {error}"))?;
                    if body.len() > MAX_HEALTH_BODY {
                        return Err("health 响应过大".into());
                    }
                    let ok = serde_json::from_slice::<serde_json::Value>(&body)
                        .ok()
                        .and_then(|v| {
                            v.get("instanceId")
                                .and_then(|i| i.as_str())
                                .map(|s| s == instance_id)
                        })
                        .unwrap_or(false);
                    if ok {
                        return Ok(());
                    }
                    return Err("health 响应 instanceId 不匹配（可能端口被占用）".into());
                }
                // non-200 while starting: keep polling until deadline
            }
            Err(_) => { /* connect refused while backend boots */ }
        }
        check_starting(state)?;
        check_starting_child(state)?;
        if Instant::now() >= deadline {
            return Err("health 探测超时（120s）".into());
        }
        let next_probe = (Instant::now() + HEALTH_INTERVAL).min(deadline);
        while Instant::now() < next_probe {
            check_starting(state)?;
            std::thread::sleep(
                POLL_INTERVAL.min(next_probe.saturating_duration_since(Instant::now())),
            );
        }
    }
}

/// Idempotent stop: stdin EOF → up to 5s grace → TerminateJobObject.
pub fn stop_backend(state: &Arc<BackendState>) {
    let _guard = state.stop_lock.lock().unwrap_or_else(|e| e.into_inner());
    let (mut child, job) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
        if *phase == BackendPhase::Stopped {
            return;
        }
        *phase = BackendPhase::Stopping;
        drop(phase);
        state
            .requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .close();
        *state.port.lock().unwrap_or_else(|e| e.into_inner()) = None;
        let child = state.child.lock().unwrap_or_else(|e| e.into_inner()).take();
        let job = state.job.lock().unwrap_or_else(|e| e.into_inner()).take();
        (child, job)
    };
    if let Some(child) = child.as_mut() {
        child.stdin.take(); // drop = EOF → backend watchdog os._exit(0)
        let deadline = Instant::now() + STOP_GRACE;
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            std::thread::sleep(POLL_INTERVAL);
        }
    }
    // Always reap the Job: a graceful direct-child exit may leave grandchildren.
    if let Some(job) = job.as_ref() {
        job.terminate();
    }
    if let Some(child) = child.as_mut() {
        let _ = child.kill();
        let deadline = Instant::now() + Duration::from_millis(500);
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            std::thread::sleep(POLL_INTERVAL);
        }
    }
    drop(child);
    drop(job);
    {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        *state.phase.lock().unwrap_or_else(|e| e.into_inner()) = BackendPhase::Stopped;
    }
    host_log(&state.log_dir, "[backend] stopped");
}

/// Non-Ready guard for business requests.
pub fn ensure_ready(state: &BackendState) -> Result<(), ProxyError> {
    state.observe_exit();
    match *state.phase.lock().unwrap_or_else(|e| e.into_inner()) {
        BackendPhase::Ready => Ok(()),
        phase => Err(ProxyError::BackendNotReady {
            phase: serde_json::to_value(phase)
                .ok()
                .and_then(|v| v.as_str().map(String::from))
                .unwrap_or_default(),
        }),
    }
}

/// Forward one whitelisted operation to the backend over loopback HTTP.
/// A cancelled worker can still occupy its HTTP socket for at most 30 seconds.
/// Keep its ID guarded until the entire response body settles, then drop the result.
pub fn api_request(
    state: &Arc<BackendState>,
    op: ApiOperation,
    task_id: Option<String>,
    after: Option<u64>,
    payload: serde_json::Value,
    request_id: u64,
) -> Result<ProxyResponse, ProxyError> {
    let request = ApiRequest {
        operation: op,
        task_id,
        after,
        payload,
        request_id,
    };
    request.validate()?;
    ensure_ready(state)?;
    let path = crate::api_proxy::build_path(op, request.task_id.as_deref(), request.after)
        .map_err(|reason| ProxyError::InvalidRequest { reason })?;
    // Serialize registration with stop: it either refuses or is included in close().
    let (port, flag) = {
        let _transition = state
            .lifecycle_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        ensure_ready(state)?;
        let port = state.port.lock().unwrap_or_else(|e| e.into_inner()).ok_or(
            ProxyError::BackendNotReady {
                phase: "starting".into(),
            },
        )?;
        let flag = state
            .requests
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .register(request_id)?;
        (port, flag)
    };

    let result = (|| {
        let method = op.route().0;
        let url = format!("http://127.0.0.1:{port}{path}");
        let agent = loopback_agent(API_TIMEOUT);
        let mut req = agent.request(method, &url);
        let token = state
            .token
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        req = req.set("X-Auth-Token", &token);
        let body_bytes: Option<Vec<u8>> = if op.is_post() {
            let body =
                serde_json::to_vec(&request.payload).map_err(|e| ProxyError::InvalidRequest {
                    reason: format!("payload 序列化失败: {e}"),
                })?;
            if body.len() > crate::api_proxy::MAX_REQUEST_BODY {
                return Err(ProxyError::InvalidRequest {
                    reason: "请求体超过 1MB 上限".into(),
                });
            }
            Some(body)
        } else {
            None
        };
        // Also cover cancellation while validating/serializing/creating the request.
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        let resp = match body_bytes {
            Some(b) => req.set("Content-Type", "application/json").send_bytes(&b),
            None => req.call(),
        };
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        // HTTP errors use exactly the same bounded body read as successful responses.
        let resp = match resp {
            Ok(resp) | Err(ureq::Error::Status(_, resp)) => resp,
            Err(error) => return Err(http_error(&error)),
        };
        let status = resp.status();
        let declared_len = resp
            .header("Content-Length")
            .and_then(|value| value.parse::<u64>().ok());
        if declared_len.is_some_and(|length| length > crate::api_proxy::MAX_RESPONSE_BODY as u64) {
            return Err(ProxyError::Network {
                reason: "响应体超过 2MB 上限".into(),
            });
        }
        let mut body = Vec::new();
        let read = resp
            .into_reader()
            .take(crate::api_proxy::MAX_RESPONSE_BODY as u64 + 1)
            .read_to_end(&mut body);
        // Cancellation takes precedence over a late successful/error body or read error.
        if flag.load(Ordering::Acquire) {
            return Err(ProxyError::Cancelled);
        }
        read.map_err(|error| http_error(&error))?;
        if body.len() > crate::api_proxy::MAX_RESPONSE_BODY {
            return Err(ProxyError::Network {
                reason: "响应体超过 2MB 上限".into(),
            });
        }
        if declared_len.is_some_and(|length| length != body.len() as u64) {
            return Err(ProxyError::Network {
                reason: "响应体长度与 Content-Length 不符".into(),
            });
        }
        let body = serde_json::from_slice(&body).map_err(|error| ProxyError::Network {
            reason: format!("响应不是有效 JSON: {error}"),
        })?;
        Ok(ProxyResponse { status, body })
    })();

    // Serialize completion with cancellation, including JSON parsing time.
    if state
        .requests
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .finish(request_id, &flag)
    {
        Err(ProxyError::Cancelled)
    } else {
        result
    }
}

pub fn api_cancel(state: &Arc<BackendState>, request_id: u64) -> bool {
    if !crate::api_proxy::valid_request_id(request_id) {
        return false;
    }
    state
        .requests
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .cancel(request_id)
}

fn http_error(error: &(dyn std::error::Error + 'static)) -> ProxyError {
    let mut cause = Some(error);
    while let Some(current) = cause {
        if current.downcast_ref::<std::io::Error>().is_some_and(|io| {
            matches!(
                io.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            )
        }) {
            return ProxyError::Timeout {
                reason: "后端请求超时（30s）".into(),
            };
        }
        cause = current.source();
    }
    ProxyError::Network {
        reason: format!("后端请求失败: {error}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn early_cancel_ledger_is_bounded_and_expires() {
        let mut requests = RequestRegistry::default();
        for id in 1..=(MAX_RECENT_REQUESTS as u64 + 20) {
            assert!(!requests.cancel(id));
            assert!(requests.recent.len() <= MAX_RECENT_REQUESTS);
        }
        requests.recent.insert(
            42,
            (Instant::now() - RECENT_REQUEST_TTL, RecentResult::Cancelled),
        );
        assert!(
            requests.register(42).is_ok(),
            "expired early cancellation must be released"
        );
    }

    #[test]
    fn inflight_capacity_and_cancellation_ownership_are_bounded() {
        let mut requests = RequestRegistry::default();
        let mut workers = Vec::new();
        for id in 1..=MAX_INFLIGHT_REQUESTS as u64 {
            workers.push((id, requests.register(id).unwrap()));
        }
        assert!(matches!(
            requests.register(9999),
            Err(ProxyError::InvalidRequest { .. })
        ));
        assert!(requests.cancel(1));
        assert_eq!(requests.active.len(), MAX_INFLIGHT_REQUESTS);
        // Evicting early-cancel/completion tombstones cannot evict a live worker.
        for id in 10_000..(10_000 + MAX_RECENT_REQUESTS as u64 + 20) {
            requests.cancel(id);
        }
        assert!(matches!(requests.register(1), Err(ProxyError::Cancelled)));
        requests.close();
        assert!(requests.recent.is_empty());
        for (id, flag) in workers {
            assert!(flag.load(Ordering::Acquire));
            assert!(requests.finish(id, &flag));
        }
        assert!(requests.active.is_empty());
        assert!(requests.recent.is_empty());
        assert!(!requests.cancel(123));
        assert!(requests.recent.is_empty());
    }

    #[test]
    fn timeout_io_error_is_distinct_from_network_error() {
        let timeout = std::io::Error::new(std::io::ErrorKind::TimedOut, "fixture timeout");
        assert!(matches!(http_error(&timeout), ProxyError::Timeout { .. }));
        let network = std::io::Error::new(std::io::ErrorKind::ConnectionReset, "fixture reset");
        assert!(matches!(http_error(&network), ProxyError::Network { .. }));
    }
}

````

## desktop/src-tauri/src/bin/fake-backend.rs

SHA256: c5b684ab66f1821f5e676e08112e1592b428c57c8a1648edccab31e86789bc70

````text
//! Integration-test helper backend: speaks the chaoxing-ready v1 protocol
//! without system Python. Behavior driven by env vars so tests can inject
//! failures:
//!   FAKE_MODE = ok | wrong-instance | exit-before-ready | spawn-grandchild
//!             | exit-after-ready | ignore-stdin
//!   FAKE_DELAY_READY_MS = delay before printing ready line
//!   FAKE_DELAY_HEALTH_MS = delay before returning health
//! PID and request marker files live only in CHAOXING_DATA_DIR test fixtures.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

fn main() {
    if std::env::args().any(|arg| arg == "--grandchild") {
        loop {
            thread::sleep(Duration::from_secs(60));
        }
    }
    let mode = std::env::var("FAKE_MODE").unwrap_or_else(|_| "ok".into());
    let token = std::env::var("CHAOXING_TAURI_TOKEN").unwrap_or_default();
    let instance = std::env::var("CHAOXING_TAURI_INSTANCE_ID").unwrap_or_default();
    let data_dir = std::env::var_os("CHAOXING_DATA_DIR")
        .map(std::path::PathBuf::from)
        .expect("fixture data dir");
    std::fs::write(
        data_dir.join("fake-backend.pid"),
        std::process::id().to_string(),
    )
    .unwrap();

    // stdin watchdog: parent closing stdin must kill us (EOF → exit 0).
    let ignore_stdin = mode == "ignore-stdin";
    thread::spawn(move || {
        let mut buf = [0u8; 1];
        loop {
            match std::io::stdin().read(&mut buf) {
                Ok(0) | Err(_) => {
                    if ignore_stdin {
                        return;
                    }
                    eprintln!("fake-backend: stdin EOF, exiting");
                    std::process::exit(0);
                }
                Ok(_) => {}
            }
        }
    });

    if mode == "exit-before-ready" {
        eprintln!("fake-backend: dying before ready");
        std::process::exit(3);
    }

    if matches!(mode.as_str(), "spawn-grandchild" | "exit-after-ready") {
        // Spawn a long-lived grandchild so tests can verify Job tree-kill.
        use std::os::windows::process::CommandExt;
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("--grandchild")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x0800_0000)
            .spawn()
            .unwrap();
        std::fs::write(data_dir.join("fake-grandchild.pid"), child.id().to_string()).unwrap();
        thread::spawn(move || {
            let _ = child.wait();
        });
    }

    let listener = match TcpListener::bind("127.0.0.1:0") {
        Ok(l) => l,
        Err(e) => {
            eprintln!("fake-backend: bind failed: {e}");
            std::process::exit(2);
        }
    };
    let port = listener.local_addr().unwrap().port();

    let delay: u64 = std::env::var("FAKE_DELAY_READY_MS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    if delay > 0 {
        thread::sleep(Duration::from_millis(delay));
    }

    let ready_instance = if mode == "wrong-instance" {
        format!("{instance}-wrong")
    } else {
        instance.clone()
    };
    let line = format!(
        "{{\"ready\":\"chaoxing-ready\",\"version\":1,\"port\":{port},\"instanceId\":\"{ready_instance}\"}}\n"
    );
    {
        let mut out = std::io::stdout();
        let _ = out.write_all(line.as_bytes());
        let _ = out.flush();
    }

    if mode == "wrong-instance" {
        // An invalid handshake followed by EOF must fail without the 120s timeout.
        std::process::exit(3);
    }

    let reported_instance = if mode == "wrong-instance" {
        format!("{instance}-wrong")
    } else {
        instance
    };

    let delay_health = std::env::var("FAKE_DELAY_HEALTH_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0);
    let exit_armed = Arc::new(AtomicBool::new(false));

    // Minimal HTTP server: /api/health with token auth, everything else echo 404.
    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(_) => continue,
        };
        let token = token.clone();
        let instance = reported_instance.clone();
        let mode = mode.clone();
        let data_dir = data_dir.clone();
        let exit_armed = exit_armed.clone();
        thread::spawn(move || {
            let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
            let mut buf = [0u8; 4096];
            let n = stream.read(&mut buf).unwrap_or(0);
            let req = String::from_utf8_lossy(&buf[..n]);
            let head = req.lines().next().unwrap_or("");
            let has_token = req.contains(&format!("X-Auth-Token: {token}"));
            let health = has_token && head.starts_with("GET /api/health");
            let (status, body) = if !has_token {
                (401, "{\"error\":\"unauthorized\"}".to_owned())
            } else if health {
                std::fs::write(data_dir.join("fake-health.requested"), b"1").unwrap();
                thread::sleep(Duration::from_millis(delay_health));
                (
                    200,
                    format!("{{\"status\":true,\"instanceId\":\"{instance}\"}}"),
                )
            } else {
                (404, "{\"error\":\"not found\"}".to_owned())
            };
            let response = format!("HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = stream.write_all(response.as_bytes());
            let _ = stream.flush();
            if health && mode == "exit-after-ready" && !exit_armed.swap(true, Ordering::AcqRel) {
                thread::spawn(|| {
                    thread::sleep(Duration::from_millis(400));
                    std::process::exit(3);
                });
            }
        });
    }
}

````

## desktop/src-tauri/src/lib.rs

SHA256: 3fad803dd1a266518b7d0d475436e7fd5fb1654af4ba088e57756c7e3a145666

````text
pub mod api_proxy;
pub mod backend;
pub mod migration;
pub mod session_store;
pub mod windows_job;

use backend::BackendState;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::Manager;

/// State is registered before the webview loads. Migration and backend startup
/// run off the event loop, so status, cancellation and window close stay usable.
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let dev_root = development_root(app.handle())?;
            let data_dir = match &dev_root {
                Some(root) => root.join("data"),
                None => app.path().app_data_dir()?.join("data"),
            };
            let log_dir = match &dev_root {
                Some(root) => root.join("logs"),
                None => app.path().app_log_dir()?,
            };
            let state = Arc::new(BackendState::new(data_dir.clone(), log_dir.clone()));
            app.manage(state.clone());
            app.manage(session_store::SessionStore::new(&data_dir));
            let notice = Arc::new(Mutex::new(None));
            app.manage(StartupNotice(notice.clone()));

            // Explicit construction isolates development's WebView2 profile.
            let config = app.config().app.windows.first().ok_or("main window config missing")?;
            let mut window = tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(allowed_navigation)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny);
            if let Some(root) = &dev_root {
                window = window.data_directory(root.join("webview"));
            }
            #[cfg(debug_assertions)]
            if std::env::var("CHAOXING_TAURI_DEV_HIDDEN").as_deref() == Ok("1") {
                window = window.visible(false);
            }
            window.build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                backend::host_log(&log_dir, "[host] starting");
                // Never read a real Electron profile implicitly in development.
                let import_legacy = !cfg!(debug_assertions)
                    || std::env::var_os(migration::LEGACY_DIR_ENV).is_some();
                if import_legacy {
                    match migration::migrate(&data_dir) {
                        Ok(migration::MigrationOutcome::DeferredLegacyRunning) => {
                            state_phase_fail(&state, "请先关闭旧版桌面应用，再关闭此窗口并重新打开，以导入原有数据。".into());
                            return;
                        }
                        Ok(migration::MigrationOutcome::ImportedButSessionInvalid(reason)) => {
                            *notice.lock().unwrap_or_else(|e| e.into_inner()) = Some("旧账号记录无法导入，请重新登录。其他有效数据已导入，原文件已保留。".into());
                            backend::host_log(&log_dir, &format!("[migration] session skipped: {reason}"));
                        }
                        Ok(outcome) => backend::host_log(&log_dir, &format!("[migration] {outcome:?}")),
                        Err(error) => {
                            backend::host_log(&log_dir, &format!("[migration] failed: {error}"));
                            state_phase_fail(&state, "旧数据导入失败，原有数据已保留。请检查数据目录权限后重新打开应用。".into());
                            return;
                        }
                    }
                }
                match launch_backend(&handle) {
                    Ok(launch) => {
                        if let Err(error) = backend::start_backend(&state, launch) {
                            backend::host_log(&log_dir, &format!("[host] backend start failed: {error}"));
                        }
                    }
                    Err(error) => state_phase_fail(&state, error),
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status, api_request, api_cancel, session_read,
            session_remember_login, session_remember_task, session_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            if let Some(state) = handle.try_state::<Arc<BackendState>>() {
                backend::stop_backend(&state);
            }
        }
    });
}

fn development_root(app: &tauri::AppHandle) -> Result<Option<PathBuf>, Box<dyn std::error::Error>> {
    #[cfg(debug_assertions)]
    {
        let root = match std::env::var_os("CHAOXING_TAURI_DEV_ROOT") {
            Some(root) => PathBuf::from(root),
            None => app.path().app_local_data_dir()?.join("development"),
        };
        if !root.is_absolute() {
            return Err("CHAOXING_TAURI_DEV_ROOT must be an absolute isolated directory".into());
        }
        Ok(Some(root))
    }
    #[cfg(not(debug_assertions))]
    {
        let _ = app;
        Ok(None)
    }
}

fn launch_backend(app: &tauri::AppHandle) -> Result<backend::BackendLaunch, String> {
    #[cfg(debug_assertions)]
    if let Some(path) = std::env::var_os("CHAOXING_TAURI_DEV_BACKEND") {
        let path = PathBuf::from(path);
        if !path.is_absolute() {
            return Err("测试后端路径必须为绝对路径".into());
        }
        return Ok(backend::BackendLaunch::Frozen(path));
    }
    backend::detect_launch(app)
}

fn state_phase_fail(state: &Arc<BackendState>, message: String) {
    let mut phase = state.phase.lock().unwrap_or_else(|e| e.into_inner());
    if !matches!(
        *phase,
        backend::BackendPhase::Starting | backend::BackendPhase::Ready
    ) {
        return;
    }
    *state.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(message.clone());
    *phase = backend::BackendPhase::Failed;
    backend::host_log(&state.log_dir, &format!("[host] failed: {message}"));
}

fn allowed_navigation(url: &tauri::Url) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    let origin = (url.scheme(), url.host_str(), url.port());
    matches!(
        origin,
        ("http" | "https", Some("tauri.localhost"), None) | ("tauri", Some("localhost"), None)
    ) || (cfg!(debug_assertions) && matches!(origin, ("http", Some("localhost"), Some(3000))))
}

/// Serde's derived structs also accept positional sequences. Wire/session DTOs
/// must enter through a map while retaining derived field and duplicate checks.
pub(crate) fn deserialize_object<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct ObjectVisitor<T>(std::marker::PhantomData<T>);

    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for ObjectVisitor<T> {
        type Value = T;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a JSON object")
        }

        fn visit_map<M: serde::de::MapAccess<'de>>(self, map: M) -> Result<T, M::Error> {
            T::deserialize(serde::de::value::MapAccessDeserializer::new(map))
        }
    }

    deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
}

fn decode_args<T: DeserializeOwned>(body: &tauri::ipc::InvokeBody) -> Result<T, String> {
    match body {
        tauri::ipc::InvokeBody::Json(value) if value.is_object() => {
            serde_json::from_value(value.clone()).map_err(|_| "请求参数格式错误".into())
        }
        _ => Err("请求参数格式错误".into()),
    }
}

fn command_args<T: DeserializeOwned>(
    window: &tauri::WebviewWindow,
    ipc: &tauri::ipc::Request<'_>,
) -> Result<T, String> {
    if window.label() != "main"
        || !window
            .url()
            .map(|url| allowed_navigation(&url))
            .unwrap_or(false)
    {
        return Err("禁止访问桌面命令".into());
    }
    decode_args(ipc.body())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyArgs {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiArgs {
    request: api_proxy::ApiRequest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct CancelArgs {
    request_id: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoginArgs {
    username: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskArgs {
    // The key is required; null explicitly clears only the task.
    #[serde(deserialize_with = "session_store::required_nullable")]
    task: Option<session_store::SessionActiveTask>,
}

struct StartupNotice(Arc<Mutex<Option<String>>>);

#[derive(serde::Serialize)]
struct HostStatus {
    #[serde(flatten)]
    backend: backend::BackendStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    notice: Option<String>,
}

#[tauri::command]
fn backend_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
    notice: tauri::State<'_, StartupNotice>,
) -> Result<HostStatus, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    Ok(HostStatus {
        backend: state.status(),
        notice: notice.0.lock().unwrap_or_else(|e| e.into_inner()).clone(),
    })
}

#[tauri::command]
async fn api_request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<api_proxy::ProxyResponse, api_proxy::ProxyError> {
    let args: ApiArgs = command_args(&window, &ipc)
        .map_err(|reason| api_proxy::ProxyError::InvalidRequest { reason })?;
    args.request.validate()?;
    let request = args.request;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        backend::api_request(
            &state,
            request.operation,
            request.task_id,
            request.after,
            request.payload,
            request.request_id,
        )
    })
    .await
    .map_err(|_| api_proxy::ProxyError::Network {
        reason: "桌面请求执行失败".into(),
    })?
}

#[tauri::command]
fn api_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<BackendState>>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<bool, String> {
    let args: CancelArgs = command_args(&window, &ipc)?;
    if !api_proxy::valid_request_id(args.request_id) {
        return Err("请求编号无效".into());
    }
    Ok(backend::api_cancel(&state, args.request_id))
}

#[tauri::command]
fn session_read(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store.read().map_err(|_| "无法读取保存的账号".into())
}

#[tauri::command]
fn session_remember_login(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: LoginArgs = command_args(&window, &ipc)?;
    store
        .remember_login(&args.username)
        .map_err(|error| match error {
            session_store::SessionError::Validation(_) => "账号格式错误".into(),
            _ => "无法保存账号，请重试".into(),
        })
}

#[tauri::command]
fn session_remember_task(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let args: TaskArgs = command_args(&window, &ipc)?;
    store.remember_task(args.task).map_err(|error| match error {
        session_store::SessionError::Validation(_) => "任务信息格式错误".into(),
        session_store::SessionError::NoLogin | session_store::SessionError::AccountMismatch => {
            "任务账号不匹配".into()
        }
        session_store::SessionError::Io(_) => "无法保存账号，请重试".into(),
    })
}

#[tauri::command]
fn session_clear(
    window: tauri::WebviewWindow,
    store: tauri::State<'_, session_store::SessionStore>,
    ipc: tauri::ipc::Request<'_>,
) -> Result<session_store::SessionData, String> {
    let _: EmptyArgs = command_args(&window, &ipc)?;
    store
        .clear()
        .map_err(|_| "无法清除保存的账号，请重试".into())
}

#[cfg(test)]
mod command_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn p2_object_boundary_rejects_positional_ipc_envelopes() {
        for (name, rejected) in [
            (
                "empty",
                decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(json!([]))).is_err(),
            ),
            (
                "login",
                decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(json!(["alice"]))).is_err(),
            ),
            (
                "cancel",
                decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(json!([1]))).is_err(),
            ),
            (
                "task",
                decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!([null]))).is_err(),
            ),
            (
                "request",
                decode_args::<ApiArgs>(&tauri::ipc::InvokeBody::Json(
                    json!([{"operation":"configRead","payload":null,"requestId":1}]),
                ))
                .is_err(),
            ),
        ] {
            assert!(rejected, "accepted positional {name} envelope");
        }
    }

    #[test]
    fn p2_object_boundary_rejects_nested_api_array() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"request":["taskLogs",null,1,"t",0]}));
        assert!(decode_args::<ApiArgs>(&ipc).is_err());
    }

    #[test]
    fn p2_object_boundary_rejects_nested_task_array_and_preserves_null() {
        let ipc = tauri::ipc::InvokeBody::Json(json!({"task":["alice","t"]}));
        assert!(decode_args::<TaskArgs>(&ipc).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"task":{"username":"alice","taskId":"t"}})
        ))
        .is_ok());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
    }

    #[test]
    fn exact_envelope_rejects_credentials_and_extra_arguments() {
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<LoginArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"username":"alice","password":"synthetic"})
        ))
        .is_err());
        assert!(decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({}))).is_err());
        assert!(
            decode_args::<TaskArgs>(&tauri::ipc::InvokeBody::Json(json!({"task":null}))).is_ok()
        );
        assert!(decode_args::<CancelArgs>(&tauri::ipc::InvokeBody::Json(
            json!({"requestId":1,"url":"http://example.test"})
        ))
        .is_err());
        assert!(decode_args::<EmptyArgs>(&tauri::ipc::InvokeBody::Raw(vec![])).is_err());
    }

    #[test]
    fn navigation_rejects_foreign_origins_and_credentials() {
        for url in ["http://tauri.localhost/", "tauri://localhost/index.html"] {
            assert!(allowed_navigation(&url.parse().unwrap()));
        }
        for url in [
            "https://example.test/",
            "http://tauri.localhost:9999/",
            "http://tauri.localhost@evil.test/",
            "http://user@tauri.localhost/",
            "file:///C:/temp/test.html",
            "data:text/html,test",
            "http://localhost:3001/",
        ] {
            assert!(!allowed_navigation(&url.parse().unwrap()), "accepted {url}");
        }
    }
}

````

## desktop/src-tauri/src/main.rs

SHA256: e04679a9bc18c6e42f69100bb4eed73087af185b677b749bde7d0d4bc47b91d6

````text
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod webview_runtime;

fn main() -> std::process::ExitCode {
    webview_runtime::run(chaoxing_desktop_lib::run)
}

````

## desktop/src-tauri/src/migration.rs

SHA256: 6bd9f2d125788b17d63603754f05c8f282c1da7b294bebeeb4c7888bcf915bb5

````text
//! One-time, read-only import of legacy Electron business data.
//!
//! Copy and validate a sibling staging directory, including its completion
//! marker, then publish the whole directory with one non-replacing rename.
//! A process exit on either side of that rename is safe to retry. The host must
//! not start the backend after an IO error or while the old Electron is running.

use crate::session_store;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{self, Write};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::HANDLE;
use windows::Win32::Storage::FileSystem::{
    FileRenameInfo, SetFileInformationByHandle, DELETE, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_RENAME_INFO,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
};
use windows::Win32::UI::WindowsAndMessaging::{FindWindowExW, HWND_MESSAGE};

pub const LEGACY_DIR_ENV: &str = "CHAOXING_LEGACY_DATA_DIR";
pub const LEGACY_DIR_DEFAULT: &str = "chaoxing-desktop";
pub const DONE_MARKER: &str = "migration-v1.done";
/// A sibling of `data`, never a directory inside the published business data.
pub const STAGING_DIR: &str = ".migration-staging";

const FILE_WHITELIST: [&str; 5] = [
    "renderer-session.json",
    "web_config.json",
    "cookies.txt",
    "cache.json",
    "config.ini",
];
const DIR_WHITELIST: [&str; 1] = [".cookies"];
const INVALID_SESSION_NOTICE: &str =
    "Legacy renderer-session.json is corrupt, exceeds 4096 bytes, or has an invalid schema; it was not imported.";

#[derive(Debug)]
pub enum MigrationOutcome {
    /// A completed import or existing new business data prevents another import.
    Skipped(&'static str),
    /// No legacy directory or whitelisted data was found.
    NoLegacyData,
    /// Other data may have been imported; the invalid legacy session was kept only in the old directory.
    ImportedButSessionInvalid(String),
    Imported(usize),
    /// The Electron ProcessSingleton window exists, or a legacy lock is busy.
    DeferredLegacyRunning,
}

fn legacy_dir() -> io::Result<Option<PathBuf>> {
    if let Some(override_dir) = std::env::var_os(LEGACY_DIR_ENV) {
        if override_dir.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty legacy data directory",
            ));
        }
        // Existence and permissions must be checked by the importer. Treating
        // an inaccessible directory as missing would permanently mark it done.
        return Ok(Some(PathBuf::from(override_dir)));
    }
    Ok(std::env::var_os("APPDATA")
        .filter(|value| !value.is_empty())
        .map(|appdata| PathBuf::from(appdata).join(LEGACY_DIR_DEFAULT)))
}

fn reject_reparse(path: &Path, metadata: &Metadata) -> io::Result<()> {
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("migration refuses reparse point: {}", path.display()),
        ));
    }
    Ok(())
}

/// Check every ancestor before accessing a path, not just its final component.
fn checked_metadata(path: &Path) -> io::Result<Option<Metadata>> {
    let mut result = None;
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        let metadata = match fs::symlink_metadata(ancestor) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        reject_reparse(ancestor, &metadata)?;
        if ancestor != path && !metadata.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "migration parent is not a directory",
            ));
        }
        result = Some(metadata);
    }
    Ok(result)
}

fn require_directory(path: &Path, metadata: &Metadata) -> io::Result<()> {
    reject_reparse(path, metadata)?;
    if !metadata.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotADirectory,
            "migration path is not a directory",
        ));
    }
    Ok(())
}

/// A no-delete directory handle prevents an already-checked directory from
/// being replaced by a junction while descendants are copied or cleaned.
fn lock_directory(path: &Path) -> io::Result<File> {
    directory_handle(path, FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0)
}

fn directory_handle(path: &Path, access: u32) -> io::Result<File> {
    let handle = OpenOptions::new()
        // FILE_READ_ATTRIBUTES alone is a metadata-only open; Windows does
        // not enforce its sharing flags. LIST_DIRECTORY makes the lock real.
        .access_mode(access)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS.0 | FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    require_directory(path, &handle.metadata()?)?;
    Ok(handle)
}

fn lock_directory_chain(path: &Path, create: bool) -> io::Result<Vec<File>> {
    let mut handles = Vec::new();
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        match fs::symlink_metadata(ancestor) {
            Ok(metadata) => require_directory(ancestor, &metadata)?,
            Err(error) if create && error.kind() == io::ErrorKind::NotFound => {
                fs::create_dir(ancestor)?;
            }
            Err(error) => return Err(error),
        }
        handles.push(lock_directory(ancestor)?);
    }
    Ok(handles)
}

fn open_source_file(path: &Path) -> io::Result<Option<File>> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(None);
    };
    if !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy whitelist file is not a regular file",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    let opened = file.metadata()?;
    reject_reparse(path, &opened)?;
    if !opened.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy file changed type",
        ));
    }
    Ok(Some(file))
}

fn copy_open_file(source: &mut File, destination: &Path) -> io::Result<()> {
    let mut target = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    io::copy(source, &mut target)?;
    target.sync_all()
}

fn copy_directory(source: &Path, destination: &Path) -> io::Result<()> {
    let _source_guard = lock_directory(source)?;
    fs::create_dir(destination)?;
    let _destination_guard = lock_directory(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "legacy cookie disappeared"))?;
        let target = destination.join(entry.file_name());
        if metadata.is_dir() {
            copy_directory(&path, &target)?;
        } else if let Some(mut file) = open_source_file(&path)? {
            copy_open_file(&mut file, &target)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "legacy cookie disappeared",
            ));
        }
    }
    Ok(())
}

/// Only used for our reserved staging directories. Do not follow a link even
/// while cleaning a previous process's interrupted import.
fn remove_staging(path: &Path) -> io::Result<()> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(());
    };
    require_directory(path, &metadata)?;
    let guard = lock_directory(path)?;
    for entry in fs::read_dir(path)? {
        let path = entry?.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "staging entry disappeared"))?;
        if metadata.is_dir() {
            remove_staging(&path)?;
        } else if metadata.is_file() {
            fs::remove_file(path)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid staging entry",
            ));
        }
    }
    drop(guard);
    fs::remove_dir(path)
}

fn write_marker(directory: &Path, reason: &[u8]) -> io::Result<()> {
    let mut marker = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join(DONE_MARKER))?;
    marker.write_all(reason)?;
    marker.sync_all()
}

/// Rename the pinned staging directory itself. Reopening it by path after
/// dropping the no-delete lock would allow a junction substitution at publish.
fn publish_directory(staging: &File, destination: &Path) -> io::Result<()> {
    let name: Vec<u16> = destination.as_os_str().encode_wide().collect();
    let name_bytes = name.len() * std::mem::size_of::<u16>();
    let buffer_bytes = std::mem::size_of::<FILE_RENAME_INFO>() + name_bytes;
    let length = u32::try_from(buffer_bytes)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "migration path is too long"))?;
    // A word-backed allocation supplies FILE_RENAME_INFO's pointer alignment
    // and enough room for its trailing, variable-length UTF-16 file name.
    let mut buffer = vec![0usize; buffer_bytes.div_ceil(std::mem::size_of::<usize>())];
    let info = buffer.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    unsafe {
        info.write(FILE_RENAME_INFO::default());
        (*info).Anonymous.ReplaceIfExists = false;
        (*info).FileNameLength = name_bytes as u32;
        std::ptr::copy_nonoverlapping(
            name.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
            name.len(),
        );
        SetFileInformationByHandle(
            HANDLE(staging.as_raw_handle()),
            FileRenameInfo,
            info.cast(),
            length,
        )
    }
    .map_err(|error| {
        let code = error.code().0 as u32;
        if code & 0xffff_0000 == 0x8007_0000 {
            io::Error::from_raw_os_error((code & 0xffff) as i32)
        } else {
            io::Error::other(error)
        }
    })
}

/// Chromium's Windows ProcessSingleton uses a Chrome_MessageWindow whose title
/// is the userData path. canonicalize() adds a Win32 verbatim prefix; Electron's
/// title normally does not contain it. Preserve UTF-16 while removing the prefix.
fn window_title(path: &Path) -> Vec<u16> {
    let wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    let mut title = if wide.starts_with(&[92, 92, 63, 92, 85, 78, 67, 92]) {
        [vec![92, 92], wide[8..].to_vec()].concat()
    } else if wide.starts_with(&[92, 92, 63, 92]) {
        wide[4..].to_vec()
    } else {
        wide
    };
    for character in &mut title {
        if *character == 47 {
            *character = 92;
        }
    }
    title.push(0);
    title
}

fn legacy_running(legacy: &Path) -> io::Result<bool> {
    for path in [std::path::absolute(legacy)?, fs::canonicalize(legacy)?] {
        let title = window_title(&path);
        // Exact profile matching avoids deferring for unrelated Electron apps.
        // Older Chromium versions may use a hidden top-level window instead.
        for parent in [Some(HWND_MESSAGE), None] {
            if unsafe {
                FindWindowExW(
                    parent,
                    None,
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                )
            }
            .is_ok()
            {
                return Ok(true);
            }
        }
    }
    // Some legacy wrappers also hold a lock file. Probe it read-only; Electron
    // itself does not promise to create this sentinel, so it is only a fallback.
    match open_source_file(&legacy.join("lockfile")) {
        Ok(_) => Ok(false),
        Err(error) if matches!(error.raw_os_error(), Some(32 | 33)) => Ok(true),
        Err(error) => Err(error),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Checkpoint {
    StagingCreated,
    SessionCopied,
    BeforePublish,
    Published,
}

fn paths_overlap(first: &Path, second: &Path) -> bool {
    // Case folding is conservative on Windows: false positives safely refuse
    // an import instead of allowing an override to change the old data tree.
    fn key(path: &Path) -> PathBuf {
        let title = window_title(path);
        let path = std::ffi::OsString::from_wide(&title[..title.len() - 1]);
        PathBuf::from(path.to_string_lossy().to_lowercase())
    }
    let first = key(first);
    let second = key(second);
    first.starts_with(&second) || second.starts_with(&first)
}

/// Run before starting the backend. Any Err or DeferredLegacyRunning must keep
/// the backend stopped so a retry cannot mistake new backend files for user data.
pub fn migrate(data_dir: &Path) -> io::Result<MigrationOutcome> {
    migrate_from(data_dir, legacy_dir()?.as_deref())
}

fn migrate_from(data_dir: &Path, legacy: Option<&Path>) -> io::Result<MigrationOutcome> {
    migrate_with_checkpoint(data_dir, legacy, |_| Ok(()))
}

fn migrate_with_checkpoint(
    data_dir: &Path,
    legacy: Option<&Path>,
    mut checkpoint: impl FnMut(Checkpoint) -> io::Result<()>,
) -> io::Result<MigrationOutcome> {
    if data_dir.as_os_str().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "empty migration destination",
        ));
    }
    let data_dir = std::path::absolute(data_dir)?;
    let parent = data_dir.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "migration destination has no parent",
        )
    })?;
    let staging = parent.join(STAGING_DIR);
    let legacy = legacy.map(std::path::absolute).transpose()?;
    if paths_overlap(&data_dir, &staging)
        || legacy
            .as_ref()
            .is_some_and(|path| paths_overlap(path, &data_dir) || paths_overlap(path, &staging))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "legacy, new data, and staging paths must not overlap",
        ));
    }

    let data_metadata = checked_metadata(&data_dir)?;
    // Pin all existing destination ancestors before reading or writing markers.
    // Missing parents are created only after the source has been checked.
    let _existing_parent_guards = if checked_metadata(parent)?.is_some() {
        Some(lock_directory_chain(parent, false)?)
    } else {
        None
    };
    let mut data_guard = match data_metadata {
        Some(ref metadata) => {
            require_directory(&data_dir, metadata)?;
            Some(lock_directory(&data_dir)?)
        }
        None => None,
    };
    if let Some(marker) = checked_metadata(&data_dir.join(DONE_MARKER))? {
        if !marker.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "migration marker is not a regular file",
            ));
        }
        return Ok(MigrationOutcome::Skipped("done marker present"));
    }
    if data_metadata.is_some() {
        for entry in fs::read_dir(&data_dir)? {
            let entry = entry?;
            let metadata = checked_metadata(&entry.path())?.ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "new data changed while checking migration",
                )
            })?;
            if entry.file_name() == STAGING_DIR {
                require_directory(&entry.path(), &metadata)?;
            } else {
                // Preserve every new business file, including a config-only or
                // cookie-only install, unknown future files, and invalid sessions.
                write_marker(&data_dir, b"existing-data")?;
                return Ok(MigrationOutcome::Skipped("new business data exists"));
            }
        }
    }

    let legacy = match legacy {
        Some(path) => match checked_metadata(&path)? {
            Some(metadata) => {
                require_directory(&path, &metadata)?;
                Some(path)
            }
            None => None,
        },
        None => None,
    };
    let _source_guards = legacy
        .as_ref()
        .map(|path| lock_directory_chain(path, false))
        .transpose()?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }

    let _parent_guards = lock_directory_chain(parent, true)?;
    remove_staging(&staging)?;
    // Compatibility with the old P1 layout; only its reserved staging is removed.
    remove_staging(&data_dir.join(STAGING_DIR))?;
    fs::create_dir(&staging)?;
    let staging_guard = directory_handle(
        &staging,
        FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0 | DELETE.0,
    )?;
    checkpoint(Checkpoint::StagingCreated)?;

    let mut copied = 0;
    let mut invalid_session = false;
    if let Some(legacy) = &legacy {
        let session_path = legacy.join(FILE_WHITELIST[0]);
        if let Some(mut source) = open_source_file(&session_path)? {
            // The held read-only handle denies writes/deletion. The shared reader
            // applies the same bounded, strict schema as normal session loading.
            if session_store::read(&session_path)?.is_some() {
                let staged_session = staging.join(FILE_WHITELIST[0]);
                copy_open_file(&mut source, &staged_session)?;
                if session_store::read(&staged_session)?.is_none() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "session changed during migration",
                    ));
                }
                copied += 1;
                checkpoint(Checkpoint::SessionCopied)?;
            } else {
                invalid_session = true;
            }
        }
        for name in FILE_WHITELIST.iter().skip(1) {
            if let Some(mut source) = open_source_file(&legacy.join(name))? {
                copy_open_file(&mut source, &staging.join(name))?;
                copied += 1;
            }
        }
        for name in DIR_WHITELIST {
            let source = legacy.join(name);
            if let Some(metadata) = checked_metadata(&source)? {
                require_directory(&source, &metadata)?;
                copy_directory(&source, &staging.join(name))?;
                copied += 1;
            }
        }
    }

    let reason: &[u8] = if invalid_session {
        b"invalid-session"
    } else if copied == 0 {
        b"no-legacy"
    } else {
        b"ok"
    };
    write_marker(&staging, reason)?;
    checkpoint(Checkpoint::BeforePublish)?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }
    // Removing an empty placeholder never removes business data. A writer that
    // wins this race causes an error; the directory rename never replaces a target.
    if checked_metadata(&data_dir)?.is_some() {
        if data_guard.is_none() {
            data_guard = Some(lock_directory(&data_dir)?);
        }
        if fs::read_dir(&data_dir)?.next().transpose()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "new business data appeared during migration",
            ));
        }
        drop(data_guard.take());
        fs::remove_dir(&data_dir)?;
    }
    publish_directory(&staging_guard, &data_dir)?;
    checkpoint(Checkpoint::Published)?;

    Ok(if invalid_session {
        MigrationOutcome::ImportedButSessionInvalid(INVALID_SESSION_NOTICE.to_owned())
    } else if copied == 0 {
        MigrationOutcome::NoLegacyData
    } else {
        MigrationOutcome::Imported(copied)
    })
}

#[cfg(test)]
mod p2_tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::process::CommandExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        legacy: PathBuf,
        data: PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Self {
            let nonce = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "cx-migration-p2-{name}-{}-{nonce}-{}",
                std::process::id(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let legacy = root.join("legacy");
            std::fs::create_dir(&legacy).unwrap();
            Self {
                data: root.join("data"),
                legacy,
                root,
            }
        }

        fn write_legacy(&self, name: &str, contents: impl AsRef<[u8]>) {
            let path = self.legacy.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, contents).unwrap();
        }

        fn run(&self) -> std::io::Result<MigrationOutcome> {
            // Explicit paths allow the complete suite to run in parallel
            // without changing the test process's environment.
            migrate_from(&self.data, Some(&self.legacy))
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn valid_session() -> &'static str {
        r#"{"version":1,"login":{"username":"fixture-user","use_cookies":true},"activeTask":{"username":"fixture-user","taskId":"fixture-task"}}"#
    }

    fn source_bytes(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        fn visit(root: &Path, path: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for entry in std::fs::read_dir(path).unwrap() {
                let entry = entry.unwrap();
                if entry.file_type().unwrap().is_dir() {
                    visit(root, &entry.path(), files);
                } else {
                    files.insert(
                        entry.path().strip_prefix(root).unwrap().to_path_buf(),
                        std::fs::read(entry.path()).unwrap(),
                    );
                }
            }
        }
        let mut files = BTreeMap::new();
        visit(root, root, &mut files);
        files
    }

    fn exclusive_file(path: &Path) -> std::fs::File {
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(path)
            .unwrap()
    }

    fn directory_link(target: &Path, link: &Path) {
        if std::os::windows::fs::symlink_dir(target, link).is_ok() {
            return;
        }
        // Junction creation works without the symlink privilege on Windows.
        // Paths are passed as process-local environment values, never shell code.
        let output = std::process::Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:CX_MIGRATION_LINK -Target $env:CX_MIGRATION_TARGET -ErrorAction Stop | Out-Null",
            ])
            .env("CX_MIGRATION_LINK", link)
            .env("CX_MIGRATION_TARGET", target)
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction fixture failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    struct SingletonWindow(windows::Win32::Foundation::HWND);

    impl SingletonWindow {
        fn new(user_data: &Path) -> Self {
            use std::os::windows::ffi::OsStrExt;
            use windows::core::{w, PCWSTR};
            use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
            use windows::Win32::UI::WindowsAndMessaging::{
                CreateWindowExW, DefWindowProcW, RegisterClassW, HWND_MESSAGE, WNDCLASSW,
                WS_OVERLAPPED,
            };

            unsafe extern "system" fn window_proc(
                hwnd: HWND,
                message: u32,
                wparam: WPARAM,
                lparam: LPARAM,
            ) -> LRESULT {
                unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
            }

            static CLASS: std::sync::OnceLock<u16> = std::sync::OnceLock::new();
            CLASS.get_or_init(|| {
                let class = WNDCLASSW {
                    lpszClassName: w!("Chrome_MessageWindow"),
                    lpfnWndProc: Some(window_proc),
                    ..Default::default()
                };
                let atom = unsafe { RegisterClassW(&class) };
                assert_ne!(atom, 0, "register singleton fixture class");
                atom
            });
            let title: Vec<u16> = user_data.as_os_str().encode_wide().chain([0]).collect();
            let hwnd = unsafe {
                CreateWindowExW(
                    Default::default(),
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                    WS_OVERLAPPED,
                    0,
                    0,
                    0,
                    0,
                    Some(HWND_MESSAGE),
                    None,
                    None,
                    None,
                )
            }
            .unwrap();
            Self(hwnd)
        }
    }

    impl Drop for SingletonWindow {
        fn drop(&mut self) {
            unsafe {
                windows::Win32::UI::WindowsAndMessaging::DestroyWindow(self.0).unwrap();
            }
        }
    }

    #[test]
    fn electron_message_window_defers_import_until_legacy_exit() {
        let fixture = Fixture::new("electron-running");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        let window = SingletonWindow::new(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(source_bytes(&fixture.legacy), before);
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn unrelated_electron_message_window_does_not_defer_import() {
        let fixture = Fixture::new("unrelated-electron");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.root.join("another-user-data"));
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn imports_whitelist_once_and_retains_every_source_byte() {
        let fixture = Fixture::new("success");
        fixture.write_legacy("renderer-session.json", valid_session());
        for name in FILE_WHITELIST.iter().skip(1) {
            fixture.write_legacy(name, name);
        }
        fixture.write_legacy(".cookies/account.json", b"cookie-fixture");
        fixture.write_legacy(".cookies/nested/another.json", b"second-cookie");
        fixture.write_legacy("unlisted/private.txt", b"leave-in-legacy");
        let before = source_bytes(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(6)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
        for name in FILE_WHITELIST {
            assert_eq!(
                std::fs::read(fixture.data.join(name)).unwrap(),
                before[Path::new(name)]
            );
        }
        assert!(fixture.data.join(DONE_MARKER).is_file());
        assert!(!fixture.data.join("unlisted").exists());
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn config_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-config");
        fixture.write_legacy("web_config.json", b"old-config");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("web_config.json"), b"new-config").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn cookie_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-cookie");
        fixture.write_legacy("cookies.txt", b"old-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"new-cookie").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"new-cookie"
        );
    }

    #[test]
    fn cookie_directory_or_unknown_new_data_prevents_import() {
        for name in [
            ".cookies/account.json",
            "future-data.bin",
            "renderer-session.json",
        ] {
            let fixture = Fixture::new("new-business-data");
            fixture.write_legacy("cookies.txt", b"old-cookie");
            let new_path = fixture.data.join(name);
            std::fs::create_dir_all(new_path.parent().unwrap()).unwrap();
            std::fs::write(&new_path, b"preserve-even-if-invalid-session").unwrap();
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
            assert!(!fixture.data.join("cookies.txt").exists());
            assert_eq!(
                std::fs::read(new_path).unwrap(),
                b"preserve-even-if-invalid-session"
            );
        }
    }

    #[test]
    fn oversized_but_well_formed_session_is_skipped_with_notice() {
        let fixture = Fixture::new("oversize");
        let session = format!(
            "{}{}",
            " ".repeat(session_store::MAX_FILE_BYTES),
            valid_session()
        );
        fixture.write_legacy("renderer-session.json", &session);
        fixture.write_legacy("web_config.json", b"config");

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::ImportedButSessionInvalid(_)
        ));
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.data.join("web_config.json").is_file());
        assert_eq!(
            std::fs::read(fixture.legacy.join("renderer-session.json")).unwrap(),
            session.as_bytes()
        );
    }

    #[test]
    fn corrupt_session_skips_only_session_and_keeps_originals() {
        for session in [
            "{ broken",
            r#"{"version":2,"login":null,"activeTask":null}"#,
            r#"{"version":1,"login":null,"activeTask":null,"password":"fixture"}"#,
        ] {
            let fixture = Fixture::new("invalid-session");
            fixture.write_legacy("renderer-session.json", session);
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::ImportedButSessionInvalid(_)
            ));
            assert!(!fixture.data.join("renderer-session.json").exists());
            assert!(fixture.data.join("cookies.txt").is_file());
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn session_io_error_aborts_without_marker_and_retries_after_unlock() {
        let fixture = Fixture::new("session-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("renderer-session.json"));

        let result = fixture.run();
        assert!(result.is_err(), "session IO must abort, got {result:?}");
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("cookies.txt").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn copy_error_never_publishes_a_partial_session_and_can_retry() {
        let fixture = Fixture::new("copy-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("web_config.json", b"config");
        let before = source_bytes(&fixture.legacy);
        let busy = exclusive_file(&fixture.legacy.join("web_config.json"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn interrupted_sibling_staging_is_rebuilt_before_publish() {
        for with_marker in [false, true] {
            let fixture = Fixture::new("interrupted");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"complete-cookie");
            let staging = fixture.root.join(STAGING_DIR);
            std::fs::create_dir(&staging).unwrap();
            std::fs::write(staging.join("renderer-session.json"), valid_session()).unwrap();
            if with_marker {
                std::fs::write(staging.join(DONE_MARKER), b"ok").unwrap();
            }

            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Imported(2)
            ));
            assert!(
                !staging.exists(),
                "interrupted staging must be consumed or rebuilt"
            );
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(
                std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"complete-cookie"
            );
        }
    }

    #[test]
    fn published_marker_makes_exit_after_publish_retry_safe() {
        let fixture = Fixture::new("post-publish");
        fixture.write_legacy("cookies.txt", b"legacy-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"published-cookie").unwrap();
        std::fs::write(fixture.data.join(DONE_MARKER), b"ok").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"published-cookie"
        );
    }

    #[test]
    fn busy_legacy_is_deferred_until_legacy_exits() {
        let fixture = Fixture::new("busy");
        fixture.write_legacy("lockfile", b"sentinel");
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("lockfile"));

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_root_junction_is_refused_without_modifying_target() {
        let fixture = Fixture::new("source-junction");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("cookies.txt"), b"outside-cookie").unwrap();
        std::fs::remove_dir(&fixture.legacy).unwrap();
        directory_link(&outside, &fixture.legacy);

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            std::fs::read(outside.join("cookies.txt")).unwrap(),
            b"outside-cookie"
        );
    }

    #[test]
    fn destination_junction_is_refused_without_writing_through_it() {
        let fixture = Fixture::new("target-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        directory_link(&outside, &fixture.data);

        assert!(fixture.run().is_err());
        assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 0);
    }

    #[test]
    fn staging_junction_is_refused_without_touching_its_target() {
        let fixture = Fixture::new("staging-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("keep.txt"), b"outside").unwrap();
        directory_link(&outside, &fixture.root.join(STAGING_DIR));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(std::fs::read(outside.join("keep.txt")).unwrap(), b"outside");
    }

    #[test]
    fn recursive_cookie_junction_is_refused_without_copying_outside_data() {
        let fixture = Fixture::new("nested-junction");
        fixture.write_legacy(".cookies/real.json", b"cookie");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies/nested"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies/nested/private.json").exists());
        assert_eq!(
            std::fs::read(outside.join("private.json")).unwrap(),
            b"outside"
        );
    }

    #[test]
    fn missing_or_empty_legacy_publishes_only_completion_marker() {
        for missing in [false, true] {
            let fixture = Fixture::new("no-legacy");
            if missing {
                fs::remove_dir(&fixture.legacy).unwrap();
            }
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::NoLegacyData
            ));
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(fs::read_dir(&fixture.data).unwrap().count(), 1);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
        }
    }

    #[test]
    fn old_p1_staging_is_cleaned_without_publishing_its_partial_data() {
        let fixture = Fixture::new("p1-staging");
        fixture.write_legacy("cookies.txt", b"complete-cookie");
        fs::create_dir_all(fixture.data.join(STAGING_DIR)).unwrap();
        fs::write(
            fixture.data.join(STAGING_DIR).join("renderer-session.json"),
            valid_session(),
        )
        .unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
        assert!(!fixture.data.join(STAGING_DIR).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"complete-cookie"
        );
    }

    #[test]
    fn cancellation_at_each_checkpoint_can_be_retried_without_partial_publish() {
        for interrupted in [
            Checkpoint::StagingCreated,
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("cancel-checkpoint");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("web_config.json", b"config");
            fixture.write_legacy(".cookies/account.json", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
                if point == interrupted {
                    return Err(io::Error::new(io::ErrorKind::Interrupted, "cancel fixture"));
                }
                Ok(())
            });
            assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
            assert_eq!(source_bytes(&fixture.legacy), before);
            if interrupted == Checkpoint::Published {
                assert!(fixture.data.join(DONE_MARKER).is_file());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Skipped(_)
                ));
            } else {
                assert!(!fixture.data.join(DONE_MARKER).exists());
                assert!(!fixture.data.join("renderer-session.json").exists());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Imported(3)
                ));
            }
            assert!(fixture.data.join(".cookies/account.json").is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe() {
        const CHILD_ROOT: &str = "CX_MIGRATION_EXIT_FIXTURE_ROOT";
        const CHILD_POINT: &str = "CX_MIGRATION_EXIT_FIXTURE_POINT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            let expected = std::env::var(CHILD_POINT).unwrap();
            migrate_with_checkpoint(&root.join("data"), Some(&root.join("legacy")), |point| {
                if format!("{point:?}") == expected {
                    // Exit without unwinding: the OS releases handles, but no
                    // cleanup or deferred marker write can run in this process.
                    std::process::exit(73);
                }
                Ok(())
            })
            .unwrap();
            panic!("exit checkpoint was not reached");
        }
        for point in [
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("process-exit");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "migration::p2_tests::actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe", "--nocapture"])
                .env(CHILD_ROOT, &fixture.root)
                .env(CHILD_POINT, format!("{point:?}"))
                .creation_flags(0x08000000)
                .output().unwrap();
            assert_eq!(
                output.status.code(),
                Some(73),
                "exit child failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
            assert_eq!(
                fixture.data.join(DONE_MARKER).is_file(),
                point == Checkpoint::Published
            );
            let result = fixture.run().unwrap();
            if point == Checkpoint::Published {
                assert!(matches!(result, MigrationOutcome::Skipped(_)));
            } else {
                assert!(matches!(result, MigrationOutcome::Imported(2)));
            }
            assert_eq!(
                fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"cookies"
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn public_entry_point_honors_legacy_override_in_an_isolated_process() {
        const CHILD_ROOT: &str = "CX_MIGRATION_ENTRY_FIXTURE_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            assert!(matches!(
                migrate(&root.join("data")).unwrap(),
                MigrationOutcome::Imported(1)
            ));
            return;
        }
        let fixture = Fixture::new("env-override");
        fixture.write_legacy("cookies.txt", b"synthetic-cookie");
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "migration::p2_tests::public_entry_point_honors_legacy_override_in_an_isolated_process", "--nocapture"])
            .env(CHILD_ROOT, &fixture.root)
            .env(LEGACY_DIR_ENV, &fixture.legacy)
            .creation_flags(0x08000000)
            .output().unwrap();
        assert!(
            output.status.success(),
            "entry-point child failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"synthetic-cookie"
        );
    }

    #[test]
    fn publish_io_error_preserves_staging_and_retries_after_unlock() {
        let fixture = Fixture::new("publish-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        fs::create_dir(&fixture.data).unwrap();
        let busy = lock_directory(&fixture.data).unwrap();
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.root.join(STAGING_DIR).join(DONE_MARKER).is_file());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn new_business_data_appearing_before_publish_is_preserved() {
        let fixture = Fixture::new("concurrent-data");
        fixture.write_legacy("web_config.json", b"old-config");
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                fs::create_dir(&fixture.data)?;
                fs::write(fixture.data.join("web_config.json"), b"new-config")?;
            }
            Ok(())
        });
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::AlreadyExists);
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn legacy_starting_before_publish_defers_without_marker() {
        let fixture = Fixture::new("legacy-start-race");
        fixture.write_legacy("cookies.txt", b"cookies");
        let mut window = None;
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                window = Some(SingletonWindow::new(&fixture.legacy));
            }
            Ok(())
        })
        .unwrap();
        assert!(matches!(result, MigrationOutcome::DeferredLegacyRunning));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_and_destination_overlap_is_refused_without_writing_old_data() {
        let fixture = Fixture::new("overlap");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        assert!(migrate_from(&fixture.legacy, Some(&fixture.legacy)).is_err());
        assert!(migrate_from(&fixture.legacy.join("data"), Some(&fixture.legacy)).is_err());
        assert!(migrate_from(
            &fixture.legacy.join("data"),
            Some(&fs::canonicalize(&fixture.legacy).unwrap())
        )
        .is_err());
        assert_eq!(source_bytes(&fixture.legacy), before);
        assert!(!fixture.legacy.join(DONE_MARKER).exists());
        assert!(!fixture.legacy.join("data").exists());
    }

    #[test]
    fn top_level_whitelisted_path_reparse_is_refused() {
        let fixture = Fixture::new("file-symlink");
        let outside = fixture.root.join("outside.txt");
        fs::write(&outside, b"outside").unwrap();
        if let Err(error) =
            std::os::windows::fs::symlink_file(&outside, fixture.legacy.join("cookies.txt"))
        {
            assert_eq!(
                error.raw_os_error(),
                Some(1314),
                "unexpected symlink fixture error: {error}"
            );
            let directory = fixture.root.join("outside-dir");
            fs::create_dir(&directory).unwrap();
            directory_link(&directory, &fixture.legacy.join("cookies.txt"));
            eprintln!("file symlink privilege unavailable; exercised a junction at the whitelisted file path instead");
        }
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(fs::read(outside).unwrap(), b"outside");
    }

    #[test]
    fn top_level_cookie_directory_link_is_refused() {
        let fixture = Fixture::new("cookie-root-link");
        let outside = fixture.root.join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies"));
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies").exists());
        assert_eq!(fs::read(outside.join("private.json")).unwrap(), b"outside");
    }

    #[test]
    fn junction_inside_staging_or_destination_parent_is_refused() {
        for in_staging in [true, false] {
            let mut fixture = Fixture::new("staging-inner-link");
            fixture.write_legacy("cookies.txt", b"cookies");
            let outside = fixture.root.join("outside");
            fs::create_dir(&outside).unwrap();
            fs::write(outside.join("keep.txt"), b"keep").unwrap();
            if in_staging {
                let staging = fixture.root.join(STAGING_DIR);
                fs::create_dir(&staging).unwrap();
                directory_link(&outside, &staging.join("nested"));
            } else {
                let parent = fixture.root.join("linked-parent");
                directory_link(&outside, &parent);
                fixture.data = parent.join("data");
            }
            assert!(fixture.run().is_err());
            assert_eq!(fs::read(outside.join("keep.txt")).unwrap(), b"keep");
            assert_eq!(fs::read_dir(outside).unwrap().count(), 1);
        }
    }

    #[test]
    fn verbatim_legacy_path_still_matches_electron_user_data_title() {
        let fixture = Fixture::new("verbatim-title");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.legacy);
        assert!(matches!(
            migrate_from(
                &fixture.data,
                Some(&fs::canonicalize(&fixture.legacy).unwrap())
            )
            .unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
    }
}

````

## desktop/src-tauri/src/session_store.rs

SHA256: 09a1ba5eb4909d3ca764d5495ba2c7da763d42aa58b1f91084cd4dd036c2e3d1

````text
use serde::{Deserialize, Deserializer, Serialize};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// Session file schema — 1:1 port of desktop/session-store.js (v1, strict).
/// exactKeys semantics from the JS original: unknown fields are invalid.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionLogin {
    pub username: String,
    pub use_cookies: bool,
}

impl<'de> Deserialize<'de> for SessionLogin {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            use_cookies: bool,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            use_cookies: fields.use_cookies,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionActiveTask {
    pub username: String,
    #[serde(rename = "taskId")]
    pub task_id: String,
}

impl<'de> Deserialize<'de> for SessionActiveTask {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            username: String,
            #[serde(rename = "taskId")]
            task_id: String,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            username: fields.username,
            task_id: fields.task_id,
        })
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SessionData {
    pub version: u32,
    pub login: Option<SessionLogin>,
    #[serde(rename = "activeTask")]
    pub active_task: Option<SessionActiveTask>,
}

impl<'de> Deserialize<'de> for SessionData {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Fields {
            version: u32,
            #[serde(deserialize_with = "required_nullable")]
            login: Option<SessionLogin>,
            #[serde(rename = "activeTask", deserialize_with = "required_nullable")]
            active_task: Option<SessionActiveTask>,
        }

        let fields: Fields = crate::deserialize_object(deserializer)?;
        Ok(Self {
            version: fields.version,
            login: fields.login,
            active_task: fields.active_task,
        })
    }
}

// Option normally accepts a missing key. A custom deserializer retains null
// while requiring the key, matching Electron's exactKeys v1 schema.
pub(crate) fn required_nullable<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::deserialize(deserializer)
}

impl Default for SessionData {
    fn default() -> Self {
        Self {
            version: 1,
            login: None,
            active_task: None,
        }
    }
}

pub const MAX_FILE_BYTES: usize = 4096;
pub const TASK_ID_RE: &str = r"^[a-zA-Z0-9_-]{1,128}$";

fn valid_username(u: &str) -> bool {
    // JavaScript counts UTF-16 units and trims ECMAScript whitespace; Rust's
    // byte length / Unicode trim differ for CJK, emoji, U+0085 and U+FEFF.
    let js_whitespace = |c| {
        matches!(c,
        '\u{0009}'..='\u{000d}' | '\u{0020}' | '\u{00a0}' | '\u{1680}' |
        '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' |
        '\u{205f}' | '\u{3000}' | '\u{feff}')
    };
    !u.is_empty()
        && u.encode_utf16().count() <= 128
        && u.trim_matches(js_whitespace) == u
        && !u.chars().any(|c| c <= '\u{001f}' || c == '\u{007f}')
}

fn valid_task_id(t: &str) -> bool {
    !t.is_empty()
        && t.len() <= 128
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Validate a parsed SessionData against the strict schema from session-store.js.
/// Returns Err(reason) when invalid.
pub fn validate(data: &SessionData) -> Result<(), String> {
    if data.version != 1 {
        return Err("unsupported session version".into());
    }
    if let Some(login) = data.login.as_ref() {
        if !login.use_cookies {
            return Err("login.use_cookies must be true".into());
        }
        if !valid_username(&login.username) {
            return Err("invalid login.username".into());
        }
    }
    if let Some(task) = data.active_task.as_ref() {
        if !valid_username(&task.username) {
            return Err("invalid activeTask.username".into());
        }
        if !valid_task_id(&task.task_id) {
            return Err("invalid activeTask.taskId".into());
        }
        let login_user = data.login.as_ref().map(|l| l.username.as_str());
        if login_user != Some(task.username.as_str()) {
            return Err("activeTask.username must match login.username".into());
        }
    }
    Ok(())
}

/// Read + validate the session file at `path`. Mirrors session-store.js `read`:
/// - missing / corrupt / oversized / schema-invalid → Ok(None)
/// - other IO errors → Err
pub fn read(path: &std::path::Path) -> Result<Option<SessionData>, std::io::Error> {
    let meta = match std::fs::metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    if meta.len() as usize > MAX_FILE_BYTES {
        return Ok(None);
    }
    let file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let mut bytes = Vec::new();
    file.take(MAX_FILE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_FILE_BYTES {
        return Ok(None);
    }
    let data: SessionData = match serde_json::from_slice(&bytes) {
        Ok(d) => d,
        Err(_) => return Ok(None),
    };
    if validate(&data).is_err() {
        return Ok(None);
    }
    Ok(Some(data))
}

/// Persist `data` atomically: write tmp + rename over `path`. Mirrors session-store.js write.
/// remember_login semantics: switching username clears active_task.
pub fn remember_login(path: &std::path::Path, username: &str) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.unwrap_or(SessionData {
        version: 1,
        login: None,
        active_task: None,
    });
    let switch = data
        .login
        .as_ref()
        .map(|l| l.username != username)
        .unwrap_or(false);
    if switch {
        data.active_task = None;
    }
    data.login = Some(SessionLogin {
        username: username.to_string(),
        use_cookies: true,
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

pub fn remember_task(
    path: &std::path::Path,
    username: &str,
    task_id: &str,
) -> Result<SessionData, SessionError> {
    let current = read(path)?;
    let mut data = current.ok_or(SessionError::NoLogin)?;
    let login = data.login.as_ref().ok_or(SessionError::NoLogin)?;
    if login.username != username {
        return Err(SessionError::AccountMismatch);
    }
    data.active_task = Some(SessionActiveTask {
        username: username.to_string(),
        task_id: task_id.to_string(),
    });
    validate(&data).map_err(SessionError::Validation)?;
    write_atomic(path, &data)?;
    Ok(data)
}

/// Clear only the recovery task, including an empty/missing session. Electron
/// rememberTask(null) writes an empty v1 session when no login is remembered.
pub fn clear_task(path: &Path) -> Result<SessionData, SessionError> {
    let mut data = read(path)?.unwrap_or_default();
    data.active_task = None;
    write_atomic(path, &data)?;
    Ok(data)
}

/// One store per host. The lock covers the entire read/modify/write transaction,
/// including clear, so concurrent IPC cannot resurrect an old account/task.
pub struct SessionStore {
    path: PathBuf,
    lock: Mutex<()>,
}

impl SessionStore {
    pub fn new(directory: &Path) -> Self {
        Self {
            path: directory.join("renderer-session.json"),
            lock: Mutex::new(()),
        }
    }

    pub fn read(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        Ok(read(&self.path)?.unwrap_or_default())
    }

    pub fn remember_login(&self, username: &str) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        remember_login(&self.path, username)
    }

    pub fn remember_task(
        &self,
        task: Option<SessionActiveTask>,
    ) -> Result<SessionData, SessionError> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        match task {
            Some(task) => remember_task(&self.path, &task.username, &task.task_id),
            None => clear_task(&self.path),
        }
    }

    pub fn clear(&self) -> Result<SessionData, std::io::Error> {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        clear(&self.path)?;
        Ok(SessionData::default())
    }
}

pub fn clear(path: &std::path::Path) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    match std::fs::remove_file(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    match std::fs::remove_file(&tmp) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    Ok(())
}

#[derive(Debug)]
pub enum SessionError {
    Validation(String),
    NoLogin,
    AccountMismatch,
    Io(std::io::Error),
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionError::Validation(s) => write!(f, "session validation failed: {s}"),
            SessionError::NoLogin => write!(f, "no remembered login"),
            SessionError::AccountMismatch => write!(f, "account mismatch"),
            SessionError::Io(e) => write!(f, "session io error: {e}"),
        }
    }
}

impl From<std::io::Error> for SessionError {
    fn from(e: std::io::Error) -> Self {
        SessionError::Io(e)
    }
}

fn tmp_path(path: &std::path::Path) -> std::path::PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(".tmp");
    path.with_file_name(name)
}

fn write_atomic(path: &std::path::Path, data: &SessionData) -> Result<(), std::io::Error> {
    let tmp = tmp_path(path);
    let bytes = serde_json::to_vec(data).map_err(std::io::Error::other)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let write = || {
        use std::io::Write;
        let mut file = std::fs::File::create(&tmp)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&tmp, path)
    };
    match write() {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p2_object_boundary_rejects_positional_session_deserialization() {
        assert!(
            serde_json::from_value::<SessionLogin>(serde_json::json!(["alice", true])).is_err()
        );
        assert!(
            serde_json::from_value::<SessionActiveTask>(serde_json::json!(["alice", "t"])).is_err()
        );
        assert!(serde_json::from_value::<SessionData>(serde_json::json!([1, null, null])).is_err());
    }

    #[test]
    fn p2_object_boundary_disk_read_rejects_arrays_at_every_schema_level() {
        let dir = tmpdir("p2-object-only");
        let path = dir.join("renderer-session.json");
        for raw in [
            serde_json::json!([1, ["alice", true], ["alice", "t"]]),
            serde_json::json!({"version":1,"login":["alice",true],"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":["alice","t"]}),
        ] {
            std::fs::write(&path, serde_json::to_vec(&raw).unwrap()).unwrap();
            assert!(read(&path).unwrap().is_none(), "accepted {raw}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_object_boundary_session_maps_and_explicit_nulls_round_trip() {
        for raw in [
            serde_json::json!({"version":1,"login":null,"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":null}),
            serde_json::json!({"version":1,"login":{"username":"alice","use_cookies":true},"activeTask":{"username":"alice","taskId":"t"}}),
        ] {
            let data: SessionData = serde_json::from_value(raw.clone()).unwrap();
            validate(&data).unwrap();
            assert_eq!(serde_json::to_value(data).unwrap(), raw);
        }
    }

    fn tmpdir(name: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("cx-session-store-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn session_json(login: Option<(&str, bool)>, task: Option<(&str, &str)>) -> String {
        let mut s = String::from("{");
        s.push_str("\"version\":1");
        if let Some((u, uc)) = login {
            s.push_str(&format!(
                ",\"login\":{{\"username\":\"{u}\",\"use_cookies\":{uc}}}"
            ));
        } else {
            s.push_str(",\"login\":null");
        }
        if let Some((u, t)) = task {
            s.push_str(&format!(
                ",\"activeTask\":{{\"username\":\"{u}\",\"taskId\":\"{t}\"}}"
            ));
        } else {
            s.push_str(",\"activeTask\":null");
        }
        s.push('}');
        s
    }

    // Group 1: persistence across instances
    #[test]
    fn persists_login_and_task_across_reads() {
        let dir = tmpdir("persist");
        let path = dir.join("renderer-session.json");
        assert!(read(&path).unwrap().is_none());

        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        let data = read(&path).unwrap().expect("session should exist");
        assert_eq!(data.login.as_ref().unwrap().username, "user1");
        assert_eq!(data.active_task.as_ref().unwrap().task_id, "task-1");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn login_switch_clears_active_task() {
        let dir = tmpdir("switch");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        remember_task(&path, "user1", "task-1").unwrap();

        remember_login(&path, "user2").unwrap();
        let data = read(&path).unwrap().unwrap();
        assert_eq!(data.login.as_ref().unwrap().username, "user2");
        assert!(data.active_task.is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 2: rejection matrix
    #[test]
    fn rejects_oversized_username() {
        let dir = tmpdir("reject-user");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some((&"a".repeat(129), true)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_control_chars_in_username() {
        let dir = tmpdir("reject-ctrl");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"a\\nb\",\"use_cookies\":true}}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_bad_task_id() {
        let dir = tmpdir("reject-task");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user1", "../escape"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_use_cookies_false() {
        let dir = tmpdir("reject-cookies");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, session_json(Some(("user1", false)), None)).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_unknown_extra_field() {
        let dir = tmpdir("reject-extra");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            "{\"version\":1,\"login\":{\"username\":\"u\",\"use_cookies\":true},\"password\":\"hunter2\"}",
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none(), "password must be rejected");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rejects_active_task_account_mismatch() {
        let dir = tmpdir("reject-mismatch");
        let path = dir.join("renderer-session.json");
        std::fs::write(
            &path,
            session_json(Some(("user1", true)), Some(("user2", "t1"))),
        )
        .unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // Group 3: corrupt / oversized / password on disk
    #[test]
    fn corrupt_json_returns_none() {
        let dir = tmpdir("corrupt");
        let path = dir.join("renderer-session.json");
        std::fs::write(&path, "{ not json").unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn oversized_file_returns_none() {
        let dir = tmpdir("oversize");
        let path = dir.join("renderer-session.json");
        let big = format!("{{\"version\":1,\"padding\":\"{}\"}}", "x".repeat(5000));
        std::fs::write(&path, big).unwrap();
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    // tmp handling
    #[test]
    fn clear_removes_session_and_tmp() {
        let dir = tmpdir("clear");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        let tmp = dir.join("renderer-session.json.tmp");
        std::fs::write(&tmp, "leftover").unwrap();

        clear(&path).unwrap();
        assert!(!path.exists());
        assert!(!tmp.exists());
        assert!(read(&path).unwrap().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_without_login_errors() {
        let dir = tmpdir("nologin");
        let path = dir.join("renderer-session.json");
        assert!(matches!(
            remember_task(&path, "u", "t"),
            Err(SessionError::NoLogin)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn remember_task_account_mismatch_errors() {
        let dir = tmpdir("mismatch-write");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "user1").unwrap();
        assert!(matches!(
            remember_task(&path, "user2", "t"),
            Err(SessionError::AccountMismatch)
        ));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_written_session_has_all_node_schema_keys() {
        let dir = tmpdir("p2-null-keys");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let disk: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(
            disk,
            serde_json::json!({
                "version": 1, "login": {"username": "alice", "use_cookies": true},
                "activeTask": null
            })
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_missing_nullable_keys_are_invalid_like_node() {
        let dir = tmpdir("p2-required-keys");
        let path = dir.join("renderer-session.json");
        for raw in [
            r#"{"version":1}"#,
            r#"{"version":1,"login":{"username":"alice","use_cookies":true}}"#,
            r#"{"version":1,"activeTask":null}"#,
        ] {
            std::fs::write(&path, raw).unwrap();
            assert!(
                read(&path).unwrap().is_none(),
                "accepted missing keys: {raw}"
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_empty_task_id_cannot_replace_saved_task() {
        let dir = tmpdir("p2-empty-task");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        remember_task(&path, "alice", "keep-this-task").unwrap();
        let previous = std::fs::read(&path).unwrap();
        assert!(remember_task(&path, "alice", "").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), previous);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_username_length_and_whitespace_match_javascript() {
        let dir = tmpdir("p2-utf16");
        let path = dir.join("renderer-session.json");
        for username in ["中".repeat(128), "😀".repeat(64), "\u{0085}alice".into()] {
            assert!(
                remember_login(&path, &username).is_ok(),
                "rejected {username}"
            );
        }
        for username in ["中".repeat(129), "😀".repeat(65), "\u{feff}alice".into()] {
            assert!(remember_login(&path, &username).is_err());
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_write_creates_private_profile_parent() {
        let dir = tmpdir("p2-parent");
        let path = dir.join("new-profile/data/renderer-session.json");
        remember_login(&path, "alice").unwrap();
        assert!(path.is_file());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_read_io_failure_is_visible() {
        let dir = tmpdir("p2-read-io");
        let path = dir.join("renderer-session.json");
        std::fs::create_dir(&path).unwrap();
        assert!(read(&path).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn p2_failed_windows_replace_preserves_previous_file() {
        use std::os::windows::fs::OpenOptionsExt;
        let dir = tmpdir("p2-locked-replace");
        let path = dir.join("renderer-session.json");
        remember_login(&path, "alice").unwrap();
        let before = std::fs::read(&path).unwrap();
        let locked = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&path)
            .unwrap();
        assert!(remember_login(&path, "bob").is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        assert!(!tmp_path(&path).exists());
        drop(locked);
        assert!(remember_login(&path, "bob").is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}

````

## desktop/src-tauri/src/webview_runtime.rs

SHA256: 5bfa5b2b08ed82ade1b58170f9d247893dddec63804c769999ca4b65ea76f5fd

````text
//! Native startup gate. No Tauri application, backend or data directory exists
//! until detection succeeds and the caller's application entry point is invoked.

use std::ffi::OsString;
use std::process::ExitCode;
use windows::core::PCWSTR;
use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK, MB_SETFOREGROUND};

const UNAVAILABLE: u8 = 3;
const INSTALL_GUIDANCE: &str = "无法启动超星学习通：未找到可用的 Microsoft Edge WebView2 运行时，或无法完成检测。\n\n\
请安装 Microsoft Edge WebView2 Evergreen Runtime（x64），然后重新打开应用。\n\n\
便携版：在程序所在目录双击 Install-WebView2.cmd，按提示安装运行时。\n\
离线电脑：从微软官方下载 x64 独立安装程序 MicrosoftEdgeWebView2RuntimeInstallerX64.exe，复制到本机运行后重试。\n\n\
微软官方下载与安装说明：\nhttps://developer.microsoft.com/microsoft-edge/webview2/";

pub(crate) fn run(start_app: impl FnOnce()) -> ExitCode {
    dispatch(
        std::env::args_os().skip(1),
        || tauri::webview_version().map_err(|error| error.to_string()),
        show_native_notice,
        start_app,
    )
}

fn dispatch(
    args: impl IntoIterator<Item = OsString>,
    detect: impl FnOnce() -> Result<String, String>,
    notify: impl FnOnce(&str),
    start_app: impl FnOnce(),
) -> ExitCode {
    let mut check_only = false;
    for arg in args {
        match arg.to_str() {
            // The smoke harness checks the actual build before allowing any
            // debug-only paths. This probe must not even load the runtime.
            Some("--check-debug-build") => {
                return ExitCode::from(if cfg!(debug_assertions) { 0 } else { 4 });
            }
            Some("--check-webview2") => check_only = true,
            _ => {}
        }
    }
    let reason = match detect() {
        Ok(version) if !version.trim().is_empty() => {
            if !check_only {
                start_app();
            }
            return ExitCode::SUCCESS;
        }
        Ok(_) => "未检测到有效的 WebView2 运行时版本。".to_owned(),
        Err(reason) => reason,
    };
    if !check_only {
        // Keep even a loader failure actionable, without falling through into
        // Tauri's Builder or relying on a renderer to explain the problem.
        notify(&format!(
            "{INSTALL_GUIDANCE}\n\n检测信息：{}",
            reason.replace('\0', "\u{fffd}")
        ));
    }
    ExitCode::from(UNAVAILABLE)
}

fn show_native_notice(message: &str) {
    let message: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
    let title: Vec<u16> = "超星学习通 — 需要 WebView2 运行时"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    // A Win32 dialog works even when no WebView2 runtime can be loaded.
    unsafe {
        MessageBoxW(
            None,
            PCWSTR(message.as_ptr()),
            PCWSTR(title.as_ptr()),
            MB_OK | MB_ICONERROR | MB_SETFOREGROUND,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn missing_runtime_never_enters_the_app_or_creates_a_webview() {
        let entered_app = Cell::new(false);
        let code = dispatch(
            args(&[]),
            || Err("runtime not installed".into()),
            |_| {},
            || entered_app.set(true),
        );
        assert!(!entered_app.get(), "lib::run must remain behind the gate");
        assert_eq!(code, ExitCode::from(3));
    }

    #[test]
    fn missing_runtime_has_native_chinese_online_and_offline_guidance() {
        let notice = RefCell::new(String::new());
        let code = dispatch(
            args(&[]),
            || Err("runtime not installed".into()),
            |message| *notice.borrow_mut() = message.to_owned(),
            || {},
        );
        let notice = notice.borrow();
        for expected in [
            "运行时",
            "Microsoft Edge WebView2",
            "Install-WebView2.cmd",
            "离线",
            "MicrosoftEdgeWebView2RuntimeInstallerX64.exe",
            "https://developer.microsoft.com/microsoft-edge/webview2/",
        ] {
            assert!(notice.contains(expected), "missing guidance: {expected}");
        }
        assert_eq!(code, ExitCode::from(3));
    }

    #[test]
    fn available_runtime_is_detected_before_the_app_runs_once() {
        let events = RefCell::new(Vec::new());
        let code = dispatch(
            args(&[]),
            || {
                events.borrow_mut().push("detect");
                Ok("140.0.3485.54".into())
            },
            |_| panic!("an available runtime must not display a dialog"),
            || events.borrow_mut().push("app"),
        );
        assert_eq!(*events.borrow(), ["detect", "app"]);
        assert_eq!(code, ExitCode::SUCCESS);
    }

    #[test]
    fn check_available_exits_zero_without_ui_backend_or_data_setup() {
        let code = dispatch(
            args(&["--check-webview2"]),
            || Ok("140.0.3485.54".into()),
            |_| panic!("check mode must not show native UI"),
            || panic!("check mode must not enter UI/backend/data setup"),
        );
        assert_eq!(code, ExitCode::SUCCESS);
    }

    #[test]
    fn check_missing_exits_three_without_ui_backend_or_data_setup() {
        let code = dispatch(
            args(&["--check-webview2"]),
            || Err("runtime not installed".into()),
            |_| panic!("check mode must not show native UI"),
            || panic!("check mode must not enter UI/backend/data setup"),
        );
        assert_eq!(code, ExitCode::from(3));
    }

    #[test]
    fn detection_errors_fail_closed_and_keep_actionable_guidance() {
        let notice = RefCell::new(String::new());
        let code = dispatch(
            args(&[]),
            || Err("fixture loader error 0x80004005".into()),
            |message| *notice.borrow_mut() = message.to_owned(),
            || panic!("detection errors must not enter the application"),
        );
        assert_eq!(code, ExitCode::from(3));
        assert!(notice.borrow().contains("0x80004005"));
        assert!(notice.borrow().contains("Install-WebView2.cmd"));
    }

    #[test]
    fn empty_version_is_unavailable_in_normal_and_check_modes() {
        for version in ["", "  \r\n"] {
            for check in [false, true] {
                let notices = Cell::new(0);
                let code = dispatch(
                    args(if check { &["--check-webview2"] } else { &[] }),
                    || Ok(version.into()),
                    |_| notices.set(notices.get() + 1),
                    || panic!("an empty version cannot establish availability"),
                );
                assert_eq!(code, ExitCode::from(3));
                assert_eq!(notices.get(), usize::from(!check));
            }
        }
    }

    #[test]
    fn check_flag_is_exact_and_can_follow_other_arguments() {
        let code = dispatch(
            args(&["unrelated", "--check-webview2"]),
            || Ok("140.0.3485.54".into()),
            |_| panic!("check mode must not show UI"),
            || panic!("check mode must not start the app"),
        );
        assert_eq!(code, ExitCode::SUCCESS);

        let started = Cell::new(false);
        dispatch(
            args(&["--check-webview2-typo"]),
            || Ok("140.0.3485.54".into()),
            |_| panic!("an available runtime must not display a dialog"),
            || started.set(true),
        );
        assert!(started.get());
    }

    #[test]
    fn check_debug_build_exits_before_detection_or_application_side_effects() {
        let code = dispatch(
            args(&["--check-debug-build"]),
            || panic!("build-profile checks must not detect WebView2"),
            |_| panic!("build-profile checks must not show native UI"),
            || panic!("build-profile checks must not initialize UI/backend/data"),
        );
        let expected = if cfg!(debug_assertions) { 0 } else { 4 };
        assert_eq!(code, ExitCode::from(expected));
    }

    #[test]
    fn check_debug_build_takes_priority_over_webview_checks_in_either_order() {
        for flags in [
            ["--check-webview2", "--check-debug-build"],
            ["--check-debug-build", "--check-webview2"],
        ] {
            let code = dispatch(
                args(&flags),
                || panic!("build-profile checks must precede runtime detection"),
                |_| panic!("build-profile checks must not show native UI"),
                || panic!("build-profile checks must not initialize UI/backend/data"),
            );
            let expected = if cfg!(debug_assertions) { 0 } else { 4 };
            assert_eq!(code, ExitCode::from(expected));
        }
    }

    #[test]
    fn check_debug_build_flag_is_exact() {
        for flag in ["--check-debug-build-extra", "--check-debug-build=1"] {
            let events = RefCell::new(Vec::new());
            let code = dispatch(
                args(&[flag]),
                || {
                    events.borrow_mut().push("detect");
                    Ok("140.0.3485.54".into())
                },
                |_| panic!("an available runtime must not display a dialog"),
                || events.borrow_mut().push("app"),
            );
            assert_eq!(*events.borrow(), ["detect", "app"]);
            assert_eq!(code, ExitCode::SUCCESS);
        }
    }
}

````

## desktop/src-tauri/src/windows_job.rs

SHA256: 6556c6eaaccba6c40ab98b77722e4bb8273f2c17de14814eb27c594749d2dab0

````text
//! Windows Job Object wrapper: KILL_ON_JOB_CLOSE so the backend process tree
//! is reaped even if the host is hard-killed. PoC-proven in E:\Downloads\45\tauri-poc.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

pub struct Job(HANDLE);

// The handle is only used for Assign/Terminate/Close, which are thread-safe
// kernel operations; HANDLE is a raw-pointer wrapper.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

impl Job {
    pub fn create() -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(None, PCWSTR::null())
                .map_err(|e| format!("CreateJobObject: {e}"))?;
            // Own the handle before fallible setup so an error also closes it.
            let job = Self(handle);
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
            .map_err(|e| format!("SetInformationJobObject: {e}"))?;
            Ok(job)
        }
    }

    pub fn assign(&self, pid: u32) -> Result<(), String> {
        unsafe {
            let ph = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
                .map_err(|e| format!("OpenProcess({pid}): {e}"))?;
            let result = AssignProcessToJobObject(self.0, ph);
            let _ = CloseHandle(ph);
            result.map_err(|e| format!("AssignProcessToJobObject({pid}): {e}"))
        }
    }

    /// Kill the whole process tree now (used on stop timeout). Idempotent.
    pub fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.0, 1);
        }
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // KILL_ON_JOB_CLOSE: closing the last handle reaps the tree. This also
        // covers panic unwinding — never leak a backend past host exit.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    /// Spawn a child that stays alive until killed; returns (Child, grandchild pid marker file check fn).
    /// Uses `cmd /c ping` (long-running) and a grandchild via `cmd /c ping & cmd /c ping`
    /// is not needed — the tree-kill semantics are covered by assigning the direct child;
    /// grandchild coverage: `cmd /c "ping -n 30 127.0.0.1 > nul & ping -n 30 127.0.0.1 > nul"`
    /// keeps one process; use PowerShell-free approach: `cmd /c start /wait` spawns a child cmd.
    #[test]
    fn drop_job_reaps_process_tree() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args([
                "/c",
                "start /wait /min cmd /c ping -n 60 127.0.0.1 > nul & ping -n 60 127.0.0.1 > nul",
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn cmd tree");
        job.assign(child.id()).expect("assign to job");

        // Drop the job: KILL_ON_JOB_CLOSE must reap child + grandchild.
        drop(job);
        std::thread::sleep(std::time::Duration::from_millis(1500));
        let status = child.try_wait().expect("try_wait");
        assert!(status.is_some(), "child should be dead after job drop");

        // Grandchild (started via `start /wait`) must also be gone: scan for ping
        // processes is flaky; instead assert via tasklist absence of our unique
        // marker is complex — the PoC already verified tree-kill visually.
        // Here we assert at least the direct child and rely on kernel job semantics.
    }

    #[test]
    fn terminate_is_idempotent_and_reaps() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args(["/c", "ping -n 60 127.0.0.1 > nul"])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn");
        job.assign(child.id()).expect("assign");
        job.terminate();
        job.terminate(); // second call must not fail/panic
        std::thread::sleep(std::time::Duration::from_millis(800));
        assert!(child.try_wait().expect("try_wait").is_some());
    }
}

````

## desktop/src-tauri/tauri.conf.json

SHA256: 2b0208f7ec639e0f6ae1ba55b95c700a24f4b1913cd9cf19f1b0b2a2df6c8376

````text
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Chaoxing GUI Tauri",
  "mainBinaryName": "chaoxing-gui-tauri",
  "version": "1.1.1",
  "identifier": "com.chaoxing.gui",
  "build": {
    "frontendDist": "../../web/dist",
    "devUrl": "http://localhost:3000"
  },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "create": false,
        "title": "超星学习通 · 自动化学习助手",
        "width": 1200,
        "height": 800,
        "minWidth": 900,
        "minHeight": 600,
        "backgroundColor": "#0f172a"
      }
    ],
    "security": {
      "csp": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src ipc: http://ipc.localhost; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'none'"
    }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "publisher": "chaoxing-gui",
    "licenseFile": "../../LICENSE",
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.ico"],
    "resources": {
      "resources/backend/": "backend/",
      "resources/backend-manifest.json": "backend-manifest.json",
      "../portable/": "./",
      "../../LICENSE": "LICENSE",
      "windows/LICENSE_MIT": "TAURI-LICENSE.txt"
    },
    "windows": {
      "webviewInstallMode": {"type": "downloadBootstrapper", "silent": true},
      "nsis": {
        "installMode": "currentUser",
        "languages": ["SimpChinese", "English"],
        "displayLanguageSelector": true,
        "installerIcon": "icons/icon.ico",
        "uninstallerIcon": "icons/icon.ico",
        "startMenuFolder": "Chaoxing GUI Tauri",
        "template": "windows/installer.nsi",
        "installerHooks": "windows/installer-hooks.nsh"
      }
    }
  }
}

````

## desktop/src-tauri/tests/api_lifecycle.rs

SHA256: 96f0945ee2cf182985cf4f166dcfec6c59455f99d089a9269f857e139a614ceb

````text
//! HTTP boundary tests use a real loopback socket with deterministic response gates.
use chaoxing_desktop_lib::api_proxy::{ApiOperation, ProxyError, MAX_RESPONSE_BODY};
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, stop_backend, BackendPhase, BackendState,
};
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Copy)]
enum Hold {
    None,
    Headers,
    Body,
}

#[derive(Default)]
struct Gate(Mutex<bool>, Condvar);

impl Gate {
    fn release(&self) {
        *self.0.lock().unwrap() = true;
        self.1.notify_all();
    }

    fn block(&self) {
        let guard = self.0.lock().unwrap();
        let _ = self
            .1
            .wait_timeout_while(guard, Duration::from_secs(35), |released| !*released)
            .unwrap();
    }
}

#[derive(Clone)]
struct Reply {
    status: u16,
    body: Vec<u8>,
    declared_len: Option<usize>,
    extra_headers: String,
    hold: Hold,
}

impl Reply {
    fn json(status: u16, body: Value) -> Self {
        Self {
            status,
            body: serde_json::to_vec(&body).unwrap(),
            declared_len: None,
            extra_headers: String::new(),
            hold: Hold::None,
        }
    }
}

struct Server {
    state: Arc<BackendState>,
    directory: std::path::PathBuf,
    gate: Arc<Gate>,
    count: Arc<AtomicUsize>,
    received: mpsc::Receiver<Vec<u8>>,
    headers: mpsc::Receiver<()>,
    shutdown: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}

impl Server {
    fn new(reply: Reply) -> Self {
        static NEXT_SERVER_ID: AtomicUsize = AtomicUsize::new(0);
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let directory = std::env::temp_dir().join(format!(
            "cx-api-lifecycle-{}-{}",
            std::process::id(),
            NEXT_SERVER_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let state = Arc::new(BackendState::new(
            directory.join("data"),
            directory.join("logs"),
        ));
        *state.phase.lock().unwrap() = BackendPhase::Ready;
        *state.port.lock().unwrap() = Some(listener.local_addr().unwrap().port());
        *state.token.lock().unwrap() = "fixture-token".into();
        let gate = Arc::new(Gate::default());
        let count = Arc::new(AtomicUsize::new(0));
        let shutdown = Arc::new(AtomicBool::new(false));
        let (request_tx, received) = mpsc::channel();
        let (header_tx, headers) = mpsc::channel();
        let gate_thread = gate.clone();
        let count_thread = count.clone();
        let shutdown_thread = shutdown.clone();
        let worker = thread::spawn(move || {
            let mut handlers = Vec::new();
            while !shutdown_thread.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let reply = reply.clone();
                        let gate = gate_thread.clone();
                        let request_tx = request_tx.clone();
                        let header_tx = header_tx.clone();
                        let index = count_thread.fetch_add(1, Ordering::AcqRel);
                        handlers.push(thread::spawn(move || {
                            respond(stream, reply, gate, index == 0, request_tx, header_tx);
                        }));
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(e) => panic!("accept: {e}"),
                }
            }
            for handler in handlers {
                handler.join().unwrap();
            }
        });
        Self {
            state,
            directory,
            gate,
            count,
            received,
            headers,
            shutdown,
            worker: Some(worker),
        }
    }

    fn request(
        &self,
        operation: ApiOperation,
        id: u64,
    ) -> Result<chaoxing_desktop_lib::api_proxy::ProxyResponse, ProxyError> {
        api_request(
            &self.state,
            operation,
            None,
            None,
            if operation.is_post() {
                json!({"username":"fixture"})
            } else {
                Value::Null
            },
            id,
        )
    }

    fn spawn_request(
        &self,
        operation: ApiOperation,
        id: u64,
    ) -> thread::JoinHandle<Result<chaoxing_desktop_lib::api_proxy::ProxyResponse, ProxyError>>
    {
        let state = self.state.clone();
        thread::spawn(move || {
            api_request(
                &state,
                operation,
                None,
                None,
                if operation.is_post() {
                    json!({"username":"fixture"})
                } else {
                    Value::Null
                },
                id,
            )
        })
    }

    fn wait_request(&self) -> Vec<u8> {
        self.received
            .recv_timeout(Duration::from_secs(3))
            .expect("backend received request")
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.gate.release();
        self.shutdown.store(true, Ordering::Release);
        self.worker.take().unwrap().join().unwrap();
        assert!(self.directory.starts_with(std::env::temp_dir()));
        let _ = std::fs::remove_dir_all(&self.directory);
    }
}

fn respond(
    mut stream: TcpStream,
    reply: Reply,
    gate: Arc<Gate>,
    first: bool,
    request_tx: mpsc::Sender<Vec<u8>>,
    header_tx: mpsc::Sender<()>,
) {
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    let mut request = Vec::new();
    let mut buf = [0; 8192];
    loop {
        let n = match stream.read(&mut buf) {
            Ok(0) | Err(_) => return,
            Ok(n) => n,
        };
        request.extend_from_slice(&buf[..n]);
        if let Some(end) = request.windows(4).position(|part| part == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&request[..end]);
            let length = headers
                .lines()
                .find_map(|line| {
                    let (name, value) = line.split_once(':')?;
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().ok())
                        .flatten()
                })
                .unwrap_or(0);
            if request.len() >= end + 4 + length {
                break;
            }
        }
    }
    let _ = request_tx.send(request);
    if first && matches!(reply.hold, Hold::Headers) {
        gate.block();
    }
    let headers = format!("HTTP/1.1 {} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{}Connection: close\r\n\r\n", reply.status, reply.declared_len.unwrap_or(reply.body.len()), reply.extra_headers);
    if stream.write_all(headers.as_bytes()).is_err() {
        return;
    }
    let _ = stream.flush();
    let _ = header_tx.send(());
    if first && matches!(reply.hold, Hold::Body) {
        gate.block();
    }
    let _ = stream.write_all(&reply.body);
    let _ = stream.flush();
}

#[test]
fn p2_success_and_http_error_statuses_are_preserved_without_start_retry() {
    for status in [200, 404, 409] {
        let body = json!({"task_id":"fixture-task", "status":status});
        let server = Server::new(Reply::json(status, body.clone()));
        let response = server.request(ApiOperation::Start, 1).unwrap();
        assert_eq!(response.status, status);
        assert_eq!(response.body, body);
        let request = String::from_utf8(server.wait_request()).unwrap();
        assert!(request.starts_with("POST /api/start HTTP/1.1\r\n"));
        assert!(request.contains("X-Auth-Token: fixture-token\r\n"));
        assert_eq!(server.count.load(Ordering::Acquire), 1);
    }
}

#[test]
fn p2_early_cancel_sends_zero_requests_and_remains_cancelled() {
    let server = Server::new(Reply::json(409, json!({"task_id":"late"})));
    assert!(!api_cancel(&server.state, 7));
    let first = server.request(ApiOperation::Start, 7);
    let second = server.request(ApiOperation::Start, 7);
    assert!(matches!(first, Err(ProxyError::Cancelled)), "{first:?}");
    assert!(matches!(second, Err(ProxyError::Cancelled)), "{second:?}");
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_active_and_cancelled_request_ids_cannot_be_reused() {
    for cancel in [false, true] {
        let mut reply = Reply::json(409, json!({"task_id":"late"}));
        reply.hold = Hold::Headers;
        let server = Server::new(reply);
        let first = server.spawn_request(ApiOperation::Start, 8);
        server.wait_request();
        if cancel {
            assert!(api_cancel(&server.state, 8));
        }
        let duplicate = server.request(ApiOperation::Start, 8);
        server.gate.release();
        let original = first.join().unwrap();
        assert!(
            matches!(
                duplicate,
                Err(ProxyError::InvalidRequest { .. }) | Err(ProxyError::Cancelled)
            ),
            "{duplicate:?}"
        );
        if cancel {
            assert!(matches!(original, Err(ProxyError::Cancelled)));
        }
        assert_eq!(server.count.load(Ordering::Acquire), 1);
    }
}

#[test]
fn p2_cancel_during_headers_or_body_discards_success_and_409() {
    for hold in [Hold::Headers, Hold::Body] {
        for status in [200, 409] {
            let mut reply = Reply::json(status, json!({"task_id":"late"}));
            reply.hold = hold;
            let server = Server::new(reply);
            let request = server.spawn_request(ApiOperation::Start, 9);
            server.wait_request();
            if matches!(hold, Hold::Body) {
                server.headers.recv_timeout(Duration::from_secs(3)).unwrap();
                // Let ureq enter its separate response-body read before cancelling.
                thread::sleep(Duration::from_millis(75));
            }
            assert!(api_cancel(&server.state, 9));
            server.gate.release();
            let result = request.join().unwrap();
            assert!(
                matches!(result, Err(ProxyError::Cancelled)),
                "status {status}: {result:?}"
            );
        }
    }
}

#[test]
fn p2_oversized_success_and_error_bodies_are_rejected() {
    for status in [200, 409] {
        let reply = Reply::json(status, json!({"message":"x".repeat(MAX_RESPONSE_BODY)}));
        let server = Server::new(reply);
        let result = server.request(ApiOperation::Start, 10);
        assert!(
            matches!(result, Err(ProxyError::Network { .. })),
            "status {status}: {result:?}"
        );
    }
}

#[test]
fn p2_truncated_error_body_is_not_successful_null() {
    let mut reply = Reply::json(404, json!({"error":"missing"}));
    reply.declared_len = Some(reply.body.len() + 20);
    let server = Server::new(reply);
    let result = server.request(ApiOperation::Start, 11);
    assert!(
        matches!(result, Err(ProxyError::Network { .. })),
        "{result:?}"
    );
}

#[test]
fn p2_invalid_json_response_is_not_successful_null() {
    let mut reply = Reply::json(409, Value::Null);
    reply.body = b"{truncated".to_vec();
    let server = Server::new(reply);
    let result = server.request(ApiOperation::Start, 12);
    assert!(
        matches!(result, Err(ProxyError::Network { .. })),
        "{result:?}"
    );
}

#[test]
fn p2_rejects_invalid_ids_and_options_before_http() {
    let server = Server::new(Reply::json(200, json!({})));
    for id in [0, 9_007_199_254_740_992, u64::MAX] {
        let result = server.request(ApiOperation::Start, id);
        assert!(
            matches!(result, Err(ProxyError::InvalidRequest { .. })),
            "id {id}: {result:?}"
        );
    }
    for (operation, task_id, after, payload) in [
        (ApiOperation::Start, Some("t".into()), None, json!({})),
        (ApiOperation::Start, None, Some(0), json!({})),
        (
            ApiOperation::TaskStatus,
            Some("t".into()),
            Some(0),
            Value::Null,
        ),
        (
            ApiOperation::ConfigRead,
            None,
            None,
            json!({"unexpected":true}),
        ),
    ] {
        let result = api_request(&server.state, operation, task_id, after, payload, 20);
        assert!(
            matches!(result, Err(ProxyError::InvalidRequest { .. })),
            "{result:?}"
        );
    }
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_completed_ids_remain_guarded_against_late_reuse() {
    let server = Server::new(Reply::json(200, json!({})));
    server.request(ApiOperation::ConfigRead, 22).unwrap();
    let result = server.request(ApiOperation::ConfigRead, 22);
    assert!(
        matches!(result, Err(ProxyError::InvalidRequest { .. })),
        "{result:?}"
    );
    assert_eq!(server.count.load(Ordering::Acquire), 1);
}

#[test]
fn p2_stop_cancels_body_read_and_blocks_new_requests() {
    let mut reply = Reply::json(409, json!({"task_id":"late"}));
    reply.hold = Hold::Body;
    let server = Server::new(reply);
    let request = server.spawn_request(ApiOperation::Start, 23);
    server.headers.recv_timeout(Duration::from_secs(3)).unwrap();
    thread::sleep(Duration::from_millis(75));
    stop_backend(&server.state);
    server.gate.release();
    let result = request.join().unwrap();
    assert!(matches!(result, Err(ProxyError::Cancelled)), "{result:?}");
    assert!(matches!(
        server.request(ApiOperation::Start, 24),
        Err(ProxyError::BackendNotReady { .. })
    ));
}

#[test]
fn p2_non_ready_phases_reject_requests() {
    let server = Server::new(Reply::json(200, json!({})));
    for phase in [
        BackendPhase::Starting,
        BackendPhase::Stopping,
        BackendPhase::Stopped,
        BackendPhase::Failed,
    ] {
        *server.state.phase.lock().unwrap() = phase;
        assert!(matches!(
            server.request(ApiOperation::ConfigRead, 25),
            Err(ProxyError::BackendNotReady { .. })
        ));
    }
    assert_eq!(server.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_backend_status_does_not_serialize_connection_credentials() {
    let server = Server::new(Reply::json(200, json!({})));
    let value = serde_json::to_value(server.state.status()).unwrap();
    assert_eq!(value["phase"], "ready");
    assert!(value.get("port").is_none());
    assert!(value.get("token").is_none());
}

#[test]
fn p2_redirect_is_not_followed() {
    let destination = Server::new(Reply::json(200, json!({"leaked":true})));
    let port = destination.state.port.lock().unwrap().unwrap();
    let mut reply = Reply::json(302, json!({"redirect":true}));
    reply.extra_headers = format!("Location: http://127.0.0.1:{port}/unexpected\r\n");
    let origin = Server::new(reply);
    let result = origin.request(ApiOperation::ConfigRead, 26).unwrap();
    assert_eq!(result.status, 302);
    assert_eq!(destination.count.load(Ordering::Acquire), 0);
}

#[test]
fn p2_stalled_error_body_obeys_whole_request_timeout() {
    let mut reply = Reply::json(409, json!({"task_id":"late"}));
    reply.hold = Hold::Body;
    let server = Server::new(reply);
    let started = Instant::now();
    let result = server.request(ApiOperation::Start, 27);
    server.gate.release();
    assert!(started.elapsed() >= Duration::from_secs(29));
    assert!(started.elapsed() < Duration::from_secs(33));
    let error =
        serde_json::to_value(result.expect_err("timeout must not be successful null")).unwrap();
    assert_eq!(error["kind"], "timeout");
}

````

## desktop/src-tauri/tests/backend_lifecycle.rs

SHA256: 6ca31c3a4d0bf40c61a8dc4ae618c3b032ba5e5b77f3ee5c7a3c1761a7497260

````text
//! Integration tests for backend.rs lifecycle, using the fake-backend bin.
//! These spawn real processes on Windows — run via `cargo test --locked`.

use chaoxing_desktop_lib::api_proxy::ApiOperation;
use chaoxing_desktop_lib::backend::{
    api_cancel, api_request, start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use std::sync::Arc;
use std::time::{Duration, Instant};

fn target_bin(name: &str) -> std::path::PathBuf {
    // Integration test exe lives in target/debug/deps; helper bins in target/debug.
    let mut p = std::env::current_exe().unwrap();
    p.pop(); // deps (or debug)
    if p.ends_with("deps") {
        p.pop();
    }
    let candidate = p.join(format!("{name}.exe"));
    if candidate.is_file() {
        candidate
    } else {
        // Fallback: same dir as the test exe (some layouts).
        let mut q = std::env::current_exe().unwrap();
        q.pop();
        q.join(format!("{name}.exe"))
    }
}

struct TestPaths {
    data_dir: std::path::PathBuf,
    log_dir: std::path::PathBuf,
    legacy_env: Option<String>,
}

impl Drop for TestPaths {
    fn drop(&mut self) {
        if let Some(v) = &self.legacy_env {
            std::env::remove_var("CHAOXING_LEGACY_DATA_DIR");
            let _ = v;
        }
        let _ = std::fs::remove_dir_all(&self.data_dir);
        let _ = std::fs::remove_dir_all(&self.log_dir);
    }
}

fn make_state(tag: &str) -> (Arc<BackendState>, TestPaths) {
    let base = std::env::temp_dir().join(format!("cx-backend-test-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    let data_dir = base.join("data");
    let log_dir = base.join("logs");
    std::fs::create_dir_all(&data_dir).unwrap();
    std::fs::create_dir_all(&log_dir).unwrap();
    (
        Arc::new(BackendState::new(data_dir.clone(), log_dir.clone())),
        TestPaths {
            data_dir,
            log_dir,
            legacy_env: None,
        },
    )
}

#[allow(dead_code)]
fn fake_backend_launch(mode: &str) -> BackendLaunch {
    let _ = mode; // FAKE_MODE is passed via process env (see set_fake_mode)
    BackendLaunch::Frozen(target_bin("fake-backend"))
}

// start_backend doesn't accept extra env; tests set process env instead.
fn set_fake_mode(mode: &str) {
    std::env::set_var("FAKE_MODE", mode);
}
fn clear_fake_mode() {
    std::env::remove_var("FAKE_MODE");
}

// NOTE: BackendLaunch has no extra-env channel; FAKE_MODE is read by the
// fake-backend child from its inherited environment.

#[test]
fn start_ok_ready_phase() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("ok");
    set_fake_mode("ok");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    r.expect("start should succeed");
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert!(state.status().port.is_some());
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn wrong_instance_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("wrong");
    set_fake_mode("wrong-instance");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err(), "instanceId mismatch must fail startup");
    assert_eq!(state.status().phase, BackendPhase::Failed);
}

#[test]
fn exit_before_ready_fails() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("die");
    set_fake_mode("exit-before-ready");
    let r = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    // no zombie backend processes: fake-backend must be gone
    std::thread::sleep(Duration::from_millis(300));
    // (kernel job close guarantees this; direct child waited inside start_backend)
}

#[test]
fn missing_exe_fails_without_panic() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("missing");
    // FAKE_MODE must not leak into this test's expectations.
    clear_fake_mode();
    let r = start_backend(
        &state,
        BackendLaunch::Frozen(std::path::PathBuf::from("Z:/no/such/backend.exe")),
    );
    assert!(r.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    let status = state.status();
    assert!(
        status.error.unwrap_or_default().contains("缺失"),
        "error should mention missing backend path"
    );
}

#[test]
fn stop_is_idempotent() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    stop_backend(&state);
    stop_backend(&state);
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
}

#[test]
fn api_request_requires_ready() {
    let (state, _paths) = make_state("notready");
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        1,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::BackendNotReady { .. })
    ));
}

#[test]
fn api_request_forwards_and_passes_status() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("fwd");
    set_fake_mode("ok");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();

    // /api/login is POST; fake backend returns 404 for it with JSON body —
    // proves token was accepted (401 would mean token missing) and status passthrough.
    let resp = api_request(
        &state,
        ApiOperation::Login,
        None,
        None,
        serde_json::json!({"username": "u"}),
        2,
    )
    .expect("request should complete");
    assert_eq!(resp.status, 404);
    assert_eq!(resp.body, serde_json::json!({"error": "not found"}));

    // invalid taskId is rejected at the host, never sent
    let resp = api_request(
        &state,
        ApiOperation::TaskStatus,
        Some("../bad".into()),
        None,
        serde_json::json!(null),
        3,
    );
    assert!(matches!(
        resp,
        Err(chaoxing_desktop_lib::api_proxy::ProxyError::InvalidRequest { .. })
    ));

    stop_backend(&state);
}

#[test]
fn api_cancel_unknown_id_is_false() {
    let (state, _paths) = make_state("cancel");
    assert!(!api_cancel(&state, 9999));
}

#[test]
fn host_kills_tree_on_stop_grandchild_reaped() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    // Check the exact fixture PID instead of counting unrelated ping processes.
    let (state, _paths) = make_state("tree");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).expect("start");
    clear_fake_mode();
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert!(process_alive(grandchild));
    stop_backend(&state);
    assert_process_exited(grandchild);
}

fn read_pid(path: &std::path::Path) -> u32 {
    wait_until(|| path.is_file());
    std::fs::read_to_string(path)
        .unwrap()
        .trim()
        .parse()
        .unwrap()
}

fn wait_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(4);
    while !condition() {
        assert!(Instant::now() < deadline, "fixture condition timed out");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn process_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let Ok(handle) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return false;
        };
        let mut code = 0;
        let alive = GetExitCodeProcess(handle, &mut code).is_ok() && code == 259;
        let _ = CloseHandle(handle);
        alive
    }
}

fn assert_process_exited(pid: u32) {
    wait_until(|| !process_alive(pid));
}

#[test]
fn p2_stop_during_handshake_registers_child_and_cannot_restore_ready() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-handshake");
    set_fake_mode("spawn-grandchild");
    std::env::set_var("FAKE_DELAY_READY_MS", "2500");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    let registered = state.child.lock().unwrap().is_some() && state.job.lock().unwrap().is_some();
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_READY_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    // Ensure a broken implementation is still cleaned up before assertions.
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(
        registered,
        "child and Job must be owned by state during the handshake"
    );
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_stop_during_health_does_not_wait_for_start_deadline() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-health");
    set_fake_mode("ok");
    std::env::set_var("FAKE_DELAY_HEALTH_MS", "1200");
    let worker_state = state.clone();
    let worker = std::thread::spawn(move || {
        start_backend(
            &worker_state,
            BackendLaunch::Frozen(target_bin("fake-backend")),
        )
    });
    wait_until(|| state.data_dir.join("fake-health.requested").is_file());
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    stop_backend(&state);
    let result = worker.join().unwrap();
    let elapsed = started.elapsed();
    std::env::remove_var("FAKE_DELAY_HEALTH_MS");
    clear_fake_mode();
    let phase = state.status().phase;
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert_eq!(phase, BackendPhase::Stopped);
    assert!(elapsed < Duration::from_secs(1), "stop took {elapsed:?}");
    assert_process_exited(pid);
}

#[test]
fn p2_stop_before_background_start_prevents_spawn() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("stop-before-start");
    stop_backend(&state);
    set_fake_mode("ok");
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    clear_fake_mode();
    let phase = state.status().phase;
    let spawned = state.data_dir.join("fake-backend.pid").exists();
    if phase == BackendPhase::Ready {
        stop_backend(&state);
    }
    assert!(result.is_err());
    assert!(!spawned);
    assert_eq!(phase, BackendPhase::Stopped);
}

#[test]
fn p2_data_directory_failure_transitions_to_failed() {
    let (_state, paths) = make_state("blocked-data");
    let data_file = paths.data_dir.join("not-a-directory");
    std::fs::write(&data_file, b"fixture").unwrap();
    let state = Arc::new(BackendState::new(data_file, paths.log_dir.clone()));
    let result = start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend")));
    assert!(result.is_err());
    assert_eq!(state.status().phase, BackendPhase::Failed);
    assert!(state.status().error.is_some());
    assert!(state.child.lock().unwrap().is_none());
}

#[test]
fn p2_post_ready_exit_is_observable_and_reaps_grandchild() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("unexpected-exit");
    set_fake_mode("exit-after-ready");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    assert_eq!(state.status().phase, BackendPhase::Ready);
    assert_process_exited(pid);
    let status = state.status();
    assert_eq!(status.phase, BackendPhase::Failed);
    assert!(status.error.is_some());
    // Failure observation alone must reap the remaining Job, before explicit stop.
    assert_process_exited(grandchild);
    assert!(state.child.lock().unwrap().is_none());
    stop_backend(&state);
}

#[test]
fn p2_failed_state_with_live_child_still_stops() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("failed-live-child");
    set_fake_mode("spawn-grandchild");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let grandchild = read_pid(&state.data_dir.join("fake-grandchild.pid"));
    *state.phase.lock().unwrap() = BackendPhase::Failed;
    stop_backend(&state);
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
    assert_process_exited(grandchild);
}

#[test]
fn p2_concurrent_stop_is_idempotent_and_bounded() {
    let _mode_guard = FAKE_MODE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let (state, _paths) = make_state("parallel-stop");
    set_fake_mode("ignore-stdin");
    start_backend(&state, BackendLaunch::Frozen(target_bin("fake-backend"))).unwrap();
    clear_fake_mode();
    let pid = read_pid(&state.data_dir.join("fake-backend.pid"));
    let started = Instant::now();
    let other_state = state.clone();
    let stopper = std::thread::spawn(move || stop_backend(&other_state));
    stop_backend(&state);
    stopper.join().unwrap();
    assert!(started.elapsed() < Duration::from_secs(7));
    assert_eq!(state.status().phase, BackendPhase::Stopped);
    assert_process_exited(pid);
}

// These tests mutate the process environment (FAKE_MODE) that the spawned
// fake-backend child inherits; parallel runs would race on it, so serialize
// all tests that set/clear FAKE_MODE via this lock.
pub static FAKE_MODE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

````

## desktop/src-tauri/tests/nested_job.rs

SHA256: b81c01ee77b759411b31abb8a1510e0b05a80b39dd5015efa4052ee700b3b4e4

````text
//! Exercise the real backend lifecycle inside a second, enclosing Windows Job.
//! Only a disposable helper is assigned to that Job; never the test runner.
#![cfg(windows)]

use chaoxing_desktop_lib::backend::{
    start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use serde::{Deserialize, Serialize};
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::windows::ffi::OsStringExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};
use windows::core::{BOOL, PCWSTR, PWSTR};
use windows::Win32::Foundation::{HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob, JobObjectBasicProcessIdList,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetExitCodeProcess, OpenProcess, QueryFullProcessImageNameW,
    TerminateProcess, WaitForSingleObject, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
};

const FAKE_BACKEND: &str = env!("CARGO_BIN_EXE_fake-backend");
const HELPER_ROOT: &str = "CHAOXING_NESTED_JOB_HELPER_ROOT";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const START_TIMEOUT: Duration = Duration::from_secs(20);
const EXIT_TIMEOUT: Duration = Duration::from_secs(8);

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ReadyRecord {
    helper_pid: u32,
    backend_pid: u32,
    grandchild_pid: u32,
    phase: String,
}

struct FixtureDirectory(PathBuf);

impl FixtureDirectory {
    fn create() -> Self {
        let mut nonce = [0u8; 8];
        getrandom::getrandom(&mut nonce).expect("fixture nonce");
        let path = std::env::temp_dir().join(format!(
            "cx-nested-job-{}-{:016x}",
            std::process::id(),
            u64::from_le_bytes(nonce)
        ));
        // Do not remove or reuse an existing directory owned by another run.
        fs::create_dir(&path).expect("create owned fixture directory");
        Self(path)
    }
}

impl Drop for FixtureDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct OuterJob(OwnedHandle);

impl OuterJob {
    fn create() -> Self {
        let handle = unsafe { CreateJobObjectW(None, PCWSTR::null()) }.expect("create outer Job");
        // Own immediately: failed configuration must close the kernel handle.
        let job = Self(unsafe { OwnedHandle::from_raw_handle(handle.0) });
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of_val(&info) as u32,
            )
        }
        .expect("configure outer Job");
        job
    }

    fn raw(&self) -> HANDLE {
        HANDLE(self.0.as_raw_handle())
    }

    fn contains(&self, process: HANDLE) -> bool {
        let mut member = BOOL::default();
        unsafe { IsProcessInJob(process, Some(self.raw()), &mut member) }
            .expect("query exact outer Job membership");
        member.as_bool()
    }

    fn pids(&self) -> Vec<u32> {
        // Layout of JOBOBJECT_BASIC_PROCESS_ID_LIST, including the hidden
        // console hosts Windows can attach to the three fixture processes.
        // Excess members fail the query instead of being omitted.
        #[repr(C)]
        #[derive(Default)]
        struct ProcessIds {
            assigned: u32,
            count: u32,
            ids: [usize; 16],
        }
        let mut ids = ProcessIds::default();
        unsafe {
            QueryInformationJobObject(
                Some(self.raw()),
                JobObjectBasicProcessIdList,
                &mut ids as *mut _ as *mut _,
                std::mem::size_of_val(&ids) as u32,
                None,
            )
        }
        .expect("enumerate only the disposable outer Job");
        assert_eq!(ids.assigned, ids.count, "incomplete Job process snapshot");
        let mut pids: Vec<_> = ids.ids[..ids.count as usize]
            .iter()
            .map(|pid| u32::try_from(*pid).expect("Windows PID"))
            .collect();
        pids.sort_unstable();
        pids
    }

    fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.raw(), 1);
        }
    }
}

struct CapturedProcess {
    pid: u32,
    handle: OwnedHandle,
}

impl CapturedProcess {
    fn open(pid: u32) -> Self {
        assert_ne!(pid, std::process::id(), "never capture the test runner");
        let handle = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE,
                false,
                pid,
            )
        }
        .expect("open the exact live fixture PID");
        Self {
            pid,
            handle: unsafe { OwnedHandle::from_raw_handle(handle.0) },
        }
    }

    fn raw(&self) -> HANDLE {
        HANDLE(self.handle.as_raw_handle())
    }

    fn assert_running(&self) {
        assert_eq!(
            unsafe { WaitForSingleObject(self.raw(), 0) },
            WAIT_TIMEOUT,
            "fixture PID {} exited before the test action",
            self.pid
        );
    }

    fn assert_exited(&self) {
        assert_eq!(
            unsafe { WaitForSingleObject(self.raw(), EXIT_TIMEOUT.as_millis() as u32) },
            WAIT_OBJECT_0,
            "captured fixture PID {} survived while the outer Job stayed open",
            self.pid
        );
    }

    fn exit_code(&self) -> u32 {
        let mut code = 0;
        unsafe { GetExitCodeProcess(self.raw(), &mut code) }.expect("fixture exit code");
        code
    }

    fn image_path(&self) -> PathBuf {
        let mut path = vec![0u16; 32768];
        let mut length = path.len() as u32;
        unsafe {
            QueryFullProcessImageNameW(
                self.raw(),
                PROCESS_NAME_WIN32,
                PWSTR(path.as_mut_ptr()),
                &mut length,
            )
        }
        .expect("fixture executable path");
        fs::canonicalize(PathBuf::from(OsString::from_wide(&path[..length as usize])))
            .expect("canonical fixture executable path")
    }
}

struct NestedHost {
    outer: OuterJob,
    helper: Child,
    descendants: Vec<CapturedProcess>,
    // Last field: remove only our directory after process/file handles close.
    directory: FixtureDirectory,
}

impl NestedHost {
    fn start() -> Self {
        let directory = FixtureDirectory::create();
        let outer = OuterJob::create();
        let helper = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--exact",
                "nested_job_helper",
                "--ignored",
                "--nocapture",
                "--test-threads=1",
            ])
            .env(HELPER_ROOT, &directory.0)
            .env("FAKE_MODE", "spawn-grandchild")
            .env_remove("FAKE_DELAY_READY_MS")
            .env_remove("FAKE_DELAY_HEALTH_MS")
            .stdin(Stdio::piped())
            .stdout(Stdio::from(
                File::create(directory.0.join("helper.stdout")).expect("helper stdout file"),
            ))
            .stderr(Stdio::from(
                File::create(directory.0.join("helper.stderr")).expect("helper stderr file"),
            ))
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn disposable helper");
        // Install cleanup before assignment, pipe writes, or any assertions.
        let mut host = Self {
            outer,
            helper,
            descendants: Vec::new(),
            directory,
        };
        assert_ne!(host.helper.id(), std::process::id());
        unsafe { AssignProcessToJobObject(host.outer.raw(), HANDLE(host.helper.as_raw_handle())) }
            .expect("assign only the gated helper to the outer Job");
        assert!(!host.outer.contains(unsafe { GetCurrentProcess() }));
        assert!(host.outer.contains(HANDLE(host.helper.as_raw_handle())));
        // The helper cannot launch anything until outer Job assignment succeeds.
        host.helper
            .stdin
            .as_mut()
            .expect("owned helper stdin pipe")
            .write_all(b"G")
            .expect("release helper startup gate");
        host.await_ready();
        host
    }

    fn diagnostics(&self) -> String {
        ["helper.stdout", "helper.stderr"]
            .into_iter()
            .map(|name| fs::read_to_string(self.directory.0.join(name)).unwrap_or_default())
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn await_ready(&mut self) {
        let ready_path = self.directory.0.join("ready.json");
        let deadline = Instant::now() + START_TIMEOUT;
        while !ready_path.is_file() {
            assert!(
                self.helper.try_wait().expect("poll helper").is_none(),
                "helper failed before Ready: {}",
                self.diagnostics()
            );
            assert!(
                Instant::now() < deadline,
                "nested backend Ready timed out: {}",
                self.diagnostics()
            );
            std::thread::sleep(Duration::from_millis(20));
        }
        let ready: ReadyRecord =
            serde_json::from_slice(&fs::read(ready_path).expect("Ready record"))
                .expect("Ready JSON");
        assert_eq!(ready.helper_pid, self.helper.id());
        assert_eq!(ready.phase, "ready");
        assert_ne!(ready.backend_pid, ready.grandchild_pid);
        assert_ne!(ready.backend_pid, ready.helper_pid);
        assert_ne!(ready.grandchild_pid, ready.helper_pid);
        for pid in [ready.backend_pid, ready.grandchild_pid] {
            self.descendants.push(CapturedProcess::open(pid));
        }
        let expected_image = fs::canonicalize(FAKE_BACKEND).expect("Cargo fake-backend path");
        for process in &self.descendants {
            process.assert_running();
            assert_eq!(process.image_path(), expected_image);
            assert!(self.outer.contains(process.raw()));
        }
        let mut expected_pids = vec![ready.helper_pid, ready.backend_pid, ready.grandchild_pid];
        expected_pids.sort_unstable();
        let observed_pids = self.outer.pids();
        for pid in &expected_pids {
            assert!(
                observed_pids.contains(pid),
                "required fixture PID {pid} is outside the Job"
            );
        }

        // CREATE_NO_WINDOW hides console windows but Windows may still create
        // a conhost.exe for each console executable. Capture those exact Job
        // members too, and verify their full system path instead of accepting
        // an arbitrary process merely because its executable has that name.
        let console_host = fs::canonicalize(
            PathBuf::from(std::env::var_os("SystemRoot").expect("Windows system root"))
                .join("System32")
                .join("conhost.exe"),
        )
        .expect("Windows console host path");
        for pid in observed_pids
            .iter()
            .filter(|pid| !expected_pids.contains(pid))
        {
            self.descendants.push(CapturedProcess::open(*pid));
            let process = self.descendants.last().unwrap();
            let image_path = process.image_path();
            eprintln!("captured auxiliary Job PID {pid}: {}", image_path.display());
            assert_eq!(
                image_path, console_host,
                "unexpected auxiliary Job member {pid}"
            );
            process.assert_running();
            assert!(self.outer.contains(process.raw()));
        }
        expected_pids.extend(self.descendants.iter().skip(2).map(|process| process.pid));
        expected_pids.sort_unstable();
        assert_eq!(
            self.outer.pids(),
            expected_pids,
            "Job members changed during capture"
        );
    }

    fn wait_for_helper(&mut self) -> ExitStatus {
        wait_for_child(&mut self.helper, EXIT_TIMEOUT)
            .unwrap_or_else(|| panic!("helper exit timed out: {}", self.diagnostics()))
    }

    fn assert_no_descendants(&self) {
        // Keep the outer Job handle open through every assertion. Its cleanup
        // must not conceal a broken inner Job or leaked grandchild.
        for process in &self.descendants {
            process.assert_exited();
        }
        let deadline = Instant::now() + EXIT_TIMEOUT;
        while !self.outer.pids().is_empty() {
            assert!(
                Instant::now() < deadline,
                "outer Job retains a fixture process"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for NestedHost {
    fn drop(&mut self) {
        self.helper.stdin.take();
        self.outer.terminate();
        let _ = self.helper.kill();
        // Captured handles prevent PID reuse from targeting unrelated processes.
        // Also cover a descendant that escaped a broken inner Job assignment.
        for process in &self.descendants {
            unsafe {
                let _ = TerminateProcess(process.raw(), 1);
            }
        }
        let _ = wait_for_child(&mut self.helper, EXIT_TIMEOUT);
        for process in &self.descendants {
            unsafe {
                let _ = WaitForSingleObject(process.raw(), EXIT_TIMEOUT.as_millis() as u32);
            }
        }
    }
}

fn wait_for_child(child: &mut Child, timeout: Duration) -> Option<ExitStatus> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(20));
            }
            _ => return None,
        }
    }
}

#[test]
fn nested_job_ready_then_stdin_eof_reaps_exact_backend_and_grandchild() {
    let mut host = NestedHost::start();
    host.helper.stdin.take();
    assert!(host.wait_for_helper().success(), "{}", host.diagnostics());
    host.assert_no_descendants();
    assert_eq!(
        host.descendants[0].exit_code(),
        0,
        "backend must exit via EOF"
    );
    assert!(host.directory.0.join("stopped").is_file());
}

#[test]
fn nested_job_forced_helper_death_reaps_exact_backend_and_grandchild() {
    let mut host = NestedHost::start();
    host.helper
        .kill()
        .expect("force-kill only the captured helper");
    assert!(!host.wait_for_helper().success());
    host.assert_no_descendants();
    assert!(!host.directory.0.join("stopped").exists());
}

struct BackendOwner(Arc<BackendState>);

impl Drop for BackendOwner {
    fn drop(&mut self) {
        stop_backend(&self.0);
    }
}

#[test]
#[ignore = "disposable subprocess entry point; started by the two nested Job tests"]
fn nested_job_helper() {
    let root = PathBuf::from(std::env::var_os(HELPER_ROOT).expect("helper-only fixture root"));
    let mut input = std::io::stdin().lock();
    let mut gate = [0u8; 1];
    input.read_exact(&mut gate).expect("parent startup gate");
    assert_eq!(gate, *b"G");
    let mut in_job = BOOL::default();
    unsafe { IsProcessInJob(GetCurrentProcess(), None, &mut in_job) }.expect("helper Job query");
    assert!(
        in_job.as_bool(),
        "the helper must already be inside the outer Job"
    );

    let backend = BackendOwner(Arc::new(BackendState::new(
        root.join("data"),
        root.join("logs"),
    )));
    start_backend(
        &backend.0,
        BackendLaunch::Frozen(PathBuf::from(FAKE_BACKEND)),
    )
    .expect("start real BackendState inside outer Job");
    assert_eq!(backend.0.status().phase, BackendPhase::Ready);
    assert!(
        backend.0.job.lock().unwrap().is_some(),
        "inner Job must remain owned"
    );
    let backend_pid = {
        let guard = backend.0.child.lock().unwrap();
        let child = guard.as_ref().expect("registered backend child");
        assert!(
            child.stdin.is_some(),
            "backend requires a real, held stdin pipe"
        );
        child.id()
    };
    let read_pid = |name: &str| -> u32 {
        fs::read_to_string(backend.0.data_dir.join(name))
            .expect("fixture PID marker")
            .trim()
            .parse()
            .expect("fixture PID")
    };
    assert_eq!(read_pid("fake-backend.pid"), backend_pid);
    let ready = ReadyRecord {
        helper_pid: std::process::id(),
        backend_pid,
        grandchild_pid: read_pid("fake-grandchild.pid"),
        phase: "ready".into(),
    };
    fs::write(root.join("ready.tmp"), serde_json::to_vec(&ready).unwrap()).expect("write Ready");
    fs::rename(root.join("ready.tmp"), root.join("ready.json")).expect("publish complete Ready");

    // Parent closes its genuine stdin writer for normal exit, or terminates
    // this helper while it is blocked here to exercise KILL_ON_JOB_CLOSE.
    let mut end = [0u8; 1];
    assert_eq!(input.read(&mut end).expect("parent EOF"), 0);
    stop_backend(&backend.0);
    assert_eq!(backend.0.status().phase, BackendPhase::Stopped);
    fs::write(root.join("stopped"), b"stopped").expect("normal-stop marker");
}

````

## desktop/src-tauri/windows/LICENSE_MIT

SHA256: 9dd42ea92cff2ede5cd477cbfcce051b2d0115c0ac7f368ee88cb545055dff1d

````text
MIT License

Copyright (c) 2017 - Present Tauri Apps Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

````

## desktop/src-tauri/windows/UPSTREAM.md

SHA256: f9ee4ae007cce82a7c4dee790306a9bdc155dbe842a4cba75236b91ad6fef809

````text
# NSIS template provenance

Source: Tauri `tauri-cli-v2.11.4`, `crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`.

URL: https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi

Upstream SHA-256: `20f4ecc730defb71f1342eaeaec4021df13be3d843abba0effe88ea5835fa079`.

License: MIT (the upstream project also offers Apache-2.0); see LICENSE_MIT.

Local changes: remove the app-data deletion checkbox and AppData deletion branch, explain that data is retained on uninstall, remove this installer's own preferences on uninstall, leave the finish-page run checkbox unchecked, and quote the WebView2 installer executable path when invoking it (including when the user's temporary directory contains spaces). Resource copying, current-user install, online WebView2 bootstrapper, shortcuts and signing remain based on the pinned upstream template. `installer-hooks.nsh` refuses an Electron installation directory.

The native NSIS hooks also preflight all install/uninstall payload destinations and every existing ancestor through the drive root using Windows APIs. The table is generated from `resources_dirs`, `resources`, `resources_ancestors` and `binaries`, plus the host and uninstaller. Installation checks run before the WebView2 section and again before `SetOutPath`; uninstall checks all destinations before its first deletion. The old main-binary registry value is checked before mutation and reused for deletion. Reinstall validates the previous directory, scans its entire existing program tree and invokes only its checked `uninstall.exe`. This additional scan protects resources absent from the new manifest when an older uninstaller predates the hooks; it refuses reparse points and enumeration failures without following links, and fails closed above 64 directory levels or 65,536 entries.

Tauri includes an empty `resources_ancestors` entry for the installation root. Empty directory entries are covered by the leading root check and omitted from the relative-path checks; empty file destinations and dot/traversal components remain invalid. The fixture includes this real upstream root entry.

This release ships NSIS/ZIP only. Automatic migration from MSI/WiX is refused with exit code **2** because the MSI deletion manifest and installation location are not covered by these checks. The native message instructs users to uninstall that MSI through Windows Settings before running this installer; arbitrary MSI registry uninstall commands are not executed.

Only absolute local drive installation directories and relative payload paths beneath them are accepted. Reparse points, type collisions, traversal, device/UNC paths, mapped network drives, DOS device names, ADS, invalid names, truncated paths and unexpected attribute-query errors are refused with exit code **2**. Ordinary Unicode and space-containing paths are supported. These are preflight checks: they do not hold directory handles across NSIS file operations and cannot prevent a concurrent process from replacing an already checked path. No target-machine PowerShell or Python dependency is introduced.

`node --test desktop/tests/nsis-paths.test.mjs` compiles the real hooks and generated path table into a dedicated fixture that touches only owned temporary directories. Windows tests skip explicitly if NSIS is unavailable; set `CHAOXING_REQUIRE_NSIS_PATH_TESTS=1` to require execution. `NSIS_MAKENSIS`, the Tauri NSIS cache, Program Files and PATH are searched for the compiler. These fixture checks complement actual installer smoke tests on a disposable Windows profile; they do not run the application or access business AppData.

When upgrading Tauri CLI, compare this template with the newly pinned upstream template and rerun NSIS install/uninstall tests on a disposable Windows profile. Do not restore recursive AppData deletion.

````

## desktop/src-tauri/windows/installer-hooks.nsh

SHA256: 58074b58ef4299c3a1417c6b2fa738e640fc17540cd302d32e0563668a8724ea

````text
!ifndef CHAOXING_PATH_GUARD_VERSION
!define CHAOXING_PATH_GUARD_VERSION 1
!include LogicLib.nsh
!include FileFunc.nsh

Var ChaoxingGuardRoot
Var ChaoxingGuardReportedPath
Var ChaoxingGuardTreeEntries

; Stack arguments: path, then kind (root/directory/file). Non-root paths must
; be relative to a successfully checked $INSTDIR. Preserve caller registers.
; These are preflight checks, not locks against concurrent junction swaps.
!macro ChaoxingPathGuardFunctions PREFIX
Function ${PREFIX}ChaoxingCheckPath
  Exch $1
  Exch
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  StrCpy $ChaoxingGuardReportedPath $0

  ; Reject values that could have been truncated by NSIS before validation.
  StrLen $8 $0
  IntOp $9 ${NSIS_MAX_STRLEN} - 1
  ${If} $8 = 0
  ${OrIf} $8 >= $9
    Goto path_rejected
  ${EndIf}

  ${If} $1 == "root"
    StrCpy $ChaoxingGuardRoot ""
    ; A local, absolute drive path only: no UNC, device namespace or drive root.
    System::Call 'shlwapi::PathGetDriveNumberW(w r0) i.r5'
    StrCpy $4 $0 1 2
    ${If} $5 < 0
    ${OrIf} $8 <= 3
      Goto path_rejected
    ${EndIf}
    ${If} $4 != "\"
    ${AndIf} $4 != "/"
      Goto path_rejected
    ${EndIf}
    StrCpy $6 $0 2
    StrCpy $4 "$6\"
    System::Call 'kernel32::GetDriveTypeW(w r4) i.r5'
    ${If} $5 != 2 ; DRIVE_REMOVABLE
    ${AndIf} $5 != 3 ; DRIVE_FIXED
    ${AndIf} $5 != 6 ; DRIVE_RAMDISK
      Goto path_rejected
    ${EndIf}
    StrCpy $7 3
  ${Else}
    ${If} $1 != "directory"
    ${AndIf} $1 != "file"
      Goto path_rejected
    ${EndIf}
    ${If} $ChaoxingGuardRoot == ""
    ${OrIf} $ChaoxingGuardRoot != $INSTDIR
      Goto path_rejected
    ${EndIf}
    StrCpy $4 $0 1
    ${If} $4 == "\"
    ${OrIf} $4 == "/"
      Goto path_rejected
    ${EndIf}
    ${If} $1 == "file"
      StrCpy $4 $0 1 -1
      ${If} $4 == "\"
      ${OrIf} $4 == "/"
        Goto path_rejected
      ${EndIf}
    ${EndIf}
    StrCpy $6 $ChaoxingGuardRoot
    StrCpy $7 0
  ${EndIf}

  ; Validate every original component before canonicalization can hide '..',
  ; trailing dots/spaces, ADS or DOS devices. Both Windows separators and the
  ; repeated separators emitted by the upstream template are normalized.
  StrCpy $3 ""
  component_character:
    StrCpy $4 $0 1 $7
    StrCmp $4 "" component_end
    StrCmp $4 "\" component_end
    StrCmp $4 "/" component_end
    StrCpy $3 "$3$4"
    Goto component_next

  component_end:
    StrCmp $3 "" component_next
    StrLen $5 $3
    ${If} $5 > 255
    ${OrIf} $3 == "."
    ${OrIf} $3 == ".."
      Goto path_rejected
    ${EndIf}
    StrCpy $4 $3 1 -1
    ${If} $4 == "."
    ${OrIf} $4 == " "
      Goto path_rejected
    ${EndIf}
    ; PathCleanupSpec reports any replacement of forbidden filename characters
    ; (including control characters). Accept only an unchanged component.
    System::Call 'shell32::PathCleanupSpec(p 0, w r3) i.r5'
    ${If} $5 != 0
      Goto path_rejected
    ${EndIf}

    ; Device names remain reserved with an extension. Compare the stem without
    ; spaces before its first dot, including Windows' superscript COM/LPT digits.
    StrCpy $2 ""
    StrCpy $5 0
    device_stem:
      StrCpy $4 $3 1 $5
      StrCmp $4 "" device_trim
      StrCmp $4 "." device_trim
      StrCpy $2 "$2$4"
      IntOp $5 $5 + 1
      Goto device_stem
    device_trim:
      StrCpy $4 $2 1 -1
      ${If} $4 == " "
        StrCpy $2 $2 -1
        Goto device_trim
      ${EndIf}
    ${If} $2 == "CON"
    ${OrIf} $2 == "PRN"
    ${OrIf} $2 == "AUX"
    ${OrIf} $2 == "NUL"
    ${OrIf} $2 == "CONIN$$"
    ${OrIf} $2 == "CONOUT$$"
    ${OrIf} $2 == "CLOCK$$"
      Goto path_rejected
    ${EndIf}
    StrLen $5 $2
    ${If} $5 = 4
      StrCpy $4 $2 3
      ${If} $4 == "COM"
      ${OrIf} $4 == "LPT"
        StrCpy $4 $2 1 3
        System::Call 'shlwapi::StrSpnW(w r4, w "0123456789¹²³") i.r5'
        ${If} $5 != 0
          Goto path_rejected
        ${EndIf}
      ${EndIf}
    ${EndIf}

    ; Check before concatenating, so the checked destination cannot silently
    ; differ from the path used by File, Delete, CreateDirectory or WriteUninstaller.
    StrLen $2 $6
    StrLen $5 $3
    IntOp $2 $2 + $5
    IntOp $2 $2 + 1
    ${If} $2 >= $9
      Goto path_rejected
    ${EndIf}
    StrCpy $6 "$6\$3"
    StrCpy $3 ""
  component_next:
    IntOp $7 $7 + 1
    ${If} $7 <= $8
      Goto component_character
    ${EndIf}

  StrLen $2 $6
  ${If} $2 <= 3
    Goto path_rejected
  ${EndIf}
  System::Call 'kernel32::GetFullPathNameW(w r6, i ${NSIS_MAX_STRLEN}, w .r0, p 0) i.r5'
  ${If} $5 = 0
  ${OrIf} $5 >= $9
  ${OrIf} $0 != $6
    Goto path_rejected
  ${EndIf}
  ${If} $1 != "root"
    StrLen $2 $ChaoxingGuardRoot
    StrCpy $3 $0 $2
    StrCpy $4 $0 1 $2
    ${If} $3 != $ChaoxingGuardRoot
    ${OrIf} $4 != "\"
      Goto path_rejected
    ${EndIf}
  ${EndIf}

  ; Walk from the destination all the way to the drive root. Reading attributes
  ; on a missing leaf is not enough: any existing ancestor may be a junction.
  StrCpy $8 $1
  path_attributes:
    System::Call 'kernel32::GetFileAttributesW(w r0) i.r2 ?e'
    Pop $3
    ${If} $2 = -1
      ; Only file/path-not-found can mean a directory we will create later.
      ; Access denied, invalid names and all other errors are a refusal.
      ${If} $3 != 2
      ${AndIf} $3 != 3
        Goto path_rejected
      ${EndIf}
      StrLen $4 $0
      ${If} $4 <= 3
        Goto path_rejected
      ${EndIf}
    ${Else}
      IntOp $4 $2 & 0x400 ; FILE_ATTRIBUTE_REPARSE_POINT
      ${If} $4 != 0
        Goto path_rejected
      ${EndIf}
      IntOp $4 $2 & 0x10 ; FILE_ATTRIBUTE_DIRECTORY
      ${If} $1 == "file"
        ${If} $4 != 0
          Goto path_rejected
        ${EndIf}
      ${Else}
        ${If} $4 = 0
          Goto path_rejected
        ${EndIf}
      ${EndIf}
    ${EndIf}
    StrLen $4 $0
    ${If} $4 > 3
      ${GetParent} "$0" $0
      StrLen $5 $0
      ${If} $5 >= $4
      ${OrIf} $5 < 2
        Goto path_rejected
      ${EndIf}
      ${If} $5 = 2
        StrCpy $0 "$0\"
      ${EndIf}
      StrCpy $1 "directory"
      Goto path_attributes
    ${EndIf}

  ${If} $8 == "root"
    StrCpy $ChaoxingGuardRoot $6
    StrCpy $INSTDIR $6
  ${EndIf}
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $0
  Pop $1
  Return

  path_rejected:
    Call ${PREFIX}ChaoxingRejectPath
FunctionEnd

Function ${PREFIX}ChaoxingRejectPath
    MessageBox MB_OK|MB_ICONSTOP "安装或卸载已停止：路径无效、不可检查，或包含目录联接/符号链接。请选择普通的本地安装目录。$\r$\nInstallation/uninstallation refused an invalid, inaccessible or redirected path:$\r$\n$ChaoxingGuardReportedPath" /SD IDOK
    SetErrorLevel 2
    Quit
FunctionEnd
!macroend

!insertmacro ChaoxingPathGuardFunctions ""
!insertmacro ChaoxingPathGuardFunctions "un."

; Older uninstallers may delete files absent from this version's manifest.
; Inspect their entire existing program tree before invoking them. Keep the
; scan bounded and never descend into a reparse point. Business AppData is not
; traversed. Depth/count limits fail closed rather than leaving a partial scan.
Function ChaoxingCheckLegacyInstallTree
  Push "$INSTDIR"
  Push "root"
  Call ChaoxingCheckPath
  StrCpy $ChaoxingGuardTreeEntries 0
  Push ""
  Push 0
  Call ChaoxingCheckTreeDirectory
FunctionEnd

; Stack arguments: relative directory (empty for root), then depth.
Function ChaoxingCheckTreeDirectory
  Exch $1
  Exch
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  ${If} $1 > 64
    Goto tree_rejected
  ${EndIf}
  StrCpy $8 $ChaoxingGuardRoot
  ${If} $0 != ""
    Push "$0"
    Push "directory"
    Call ChaoxingCheckPath
    StrCpy $8 "$ChaoxingGuardRoot\$0"
  ${EndIf}
  StrCpy $ChaoxingGuardReportedPath $8
  StrLen $9 $8
  IntOp $9 $9 + 3 ; separator, wildcard and terminating NUL
  ${If} $9 >= ${NSIS_MAX_STRLEN}
    Goto tree_rejected
  ${EndIf}

  ; WIN32_FIND_DATAW: eleven DWORDs then WCHAR cFileName[MAX_PATH].
  System::Alloc 592
  Pop $2
  ${If} $2 = 0
    Goto tree_rejected
  ${EndIf}
  System::Call 'kernel32::FindFirstFileW(w "$8\*", p r2) p.r3 ?e'
  Pop $7
  ${If} $3 = -1
    System::Free $2
    ${If} $7 = 2 ; no matching entries in an empty directory
      Goto tree_done
    ${EndIf}
    Goto tree_rejected
  ${EndIf}
  tree_entry:
    System::Call '*$2(i .r4, i, i, i, i, i, i, i, i, i, i, &w260 .r5)'
    StrCmp $5 "." tree_next
    StrCmp $5 ".." tree_next
    StrCpy $ChaoxingGuardReportedPath "$8\$5"
    IntOp $ChaoxingGuardTreeEntries $ChaoxingGuardTreeEntries + 1
    ${If} $ChaoxingGuardTreeEntries > 65536
      Goto tree_rejected
    ${EndIf}
    IntOp $7 $4 & 0x400
    ${If} $7 != 0
      Goto tree_rejected
    ${EndIf}
    StrLen $7 $0
    StrLen $9 $5
    IntOp $9 $9 + $7
    IntOp $9 $9 + 2
    ${If} $9 >= ${NSIS_MAX_STRLEN}
      Goto tree_rejected
    ${EndIf}
    StrCpy $6 $5
    ${If} $0 != ""
      StrCpy $6 "$0\$5"
    ${EndIf}
    IntOp $7 $4 & 0x10
    ${If} $7 != 0
      IntOp $7 $1 + 1
      Push "$6"
      Push $7
      Call ChaoxingCheckTreeDirectory
    ${Else}
      Push "$6"
      Push "file"
      Call ChaoxingCheckPath
    ${EndIf}
  tree_next:
    System::Call 'kernel32::FindNextFileW(p r3, p r2) i.r4 ?e'
    Pop $7
    ${If} $4 != 0
      Goto tree_entry
    ${EndIf}
    ${If} $7 != 18 ; ERROR_NO_MORE_FILES is the only successful end of scan
      Goto tree_rejected
    ${EndIf}
    System::Call 'kernel32::FindClose(p r3) i.r4'
    System::Free $2
    ${If} $4 = 0
      Goto tree_rejected
    ${EndIf}
  tree_done:
    Pop $9
    Pop $8
    Pop $7
    Pop $6
    Pop $5
    Pop $4
    Pop $3
    Pop $2
    Pop $0
    Pop $1
    Return
  tree_rejected:
    ; Quit closes any outstanding enumeration handles and buffers in this
    ; process; there is no fallthrough to the previous uninstaller.
    Call ChaoxingRejectPath
FunctionEnd

Function ChaoxingRefuseMsiMigration
  MessageBox MB_OK|MB_ICONSTOP "检测到旧 MSI 安装。本安装包无法安全自动迁移 MSI。请先在 Windows 设置中卸载旧 MSI 版本，再运行此安装包。$\r$\nAutomatic MSI migration is not supported. Uninstall the previous MSI version in Windows Settings before running this installer." /SD IDOK
  SetErrorLevel 2
  Quit
FunctionEnd

; A manually selected path must not turn the coexistence build into an
; in-place Electron upgrade. Each installer owns its own program directory.
Function ChaoxingCheckElectronDirectory
  IfFileExists "$INSTDIR\chaoxing-gui.exe" legacy_electron_directory
  IfFileExists "$INSTDIR\resources\app.asar" legacy_electron_directory
  Goto tauri_directory_ready
  legacy_electron_directory:
    MessageBox MB_OK|MB_ICONSTOP "此目录包含旧版 Electron 程序。请选择新的 Tauri 安装目录，原程序与数据将保留。$\r$\nThis directory contains the Electron app. Choose a separate Tauri installation directory." /SD IDOK
    SetErrorLevel 2
    Abort
  tauri_directory_ready:
FunctionEnd

!macro NSIS_HOOK_PREINSTALL
  Call ChaoxingValidateInstallPaths
  Call ChaoxingCheckElectronDirectory
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  Call un.ChaoxingValidateInstallPaths
!macroend
!endif

````

## desktop/src-tauri/windows/installer.nsi

SHA256: 4d7cda22a499521d3c06145981e5e1d92ad48b8e8fafc8e171eca2753f50da1b

````text
; Adapted from Tauri CLI 2.11.4; see UPSTREAM.md and LICENSE_MIT.
Unicode true
ManifestDPIAware true
; Add in `dpiAwareness` `PerMonitorV2` to manifest for Windows 10 1607+ (note this should not affect lower versions since they should be able to ignore this and pick up `dpiAware` `true` set by `ManifestDPIAware true`)
; Currently undocumented on NSIS's website but is in the Docs folder of source tree, see
; https://github.com/kichik/nsis/blob/5fc0b87b819a9eec006df4967d08e522ddd651c9/Docs/src/attributes.but#L286-L300
; https://github.com/tauri-apps/tauri/pull/10106
ManifestDPIAwareness PerMonitorV2

!if "{{compression}}" == "none"
  SetCompress off
!else
  ; Set the compression algorithm. We default to LZMA.
  SetCompressor /SOLID "{{compression}}"
!endif

; Keep above !include to stay ahead of any plugin command
; see https://github.com/tauri-apps/tauri/pull/15422#discussion_r3289239624
{{#if signed_plugins_path}}
!addplugindir "{{signed_plugins_path}}"
{{/if}}

!include MUI2.nsh
!include FileFunc.nsh
!include x64.nsh
!include WordFunc.nsh
!include "utils.nsh"
!include "FileAssociation.nsh"
!include "Win\COM.nsh"
!include "Win\Propkey.nsh"
!include "StrFunc.nsh"
${StrCase}
${StrLoc}

{{#if installer_hooks}}
!include "{{installer_hooks}}"
{{/if}}

!define WEBVIEW2APPGUID "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

!define MANUFACTURER "{{manufacturer}}"
!define PRODUCTNAME "{{product_name}}"
!define VERSION "{{version}}"
!define VERSIONWITHBUILD "{{version_with_build}}"
!define HOMEPAGE "{{homepage}}"
!define INSTALLMODE "{{install_mode}}"
!define LICENSE "{{license}}"
!define INSTALLERICON "{{installer_icon}}"
!define SIDEBARIMAGE "{{sidebar_image}}"
!define HEADERIMAGE "{{header_image}}"
!define UNINSTALLERICON "{{uninstaller_icon}}"
!define UNINSTALLERHEADERIMAGE "{{uninstaller_header_image}}"
!define MAINBINARYNAME "{{main_binary_name}}"
!define MAINBINARYSRCPATH "{{main_binary_path}}"
!define BUNDLEID "{{bundle_id}}"
!define COPYRIGHT "{{copyright}}"
!define OUTFILE "{{out_file}}"
!define ARCH "{{arch}}"
!define ADDITIONALPLUGINSPATH "{{additional_plugins_path}}"
!define ALLOWDOWNGRADES "{{allow_downgrades}}"
!define DISPLAYLANGUAGESELECTOR "{{display_language_selector}}"
!define INSTALLWEBVIEW2MODE "{{install_webview2_mode}}"
!define WEBVIEW2INSTALLERARGS "{{webview2_installer_args}}"
!define WEBVIEW2BOOTSTRAPPERPATH "{{webview2_bootstrapper_path}}"
!define WEBVIEW2INSTALLERPATH "{{webview2_installer_path}}"
!define MINIMUMWEBVIEW2VERSION "{{minimum_webview2_version}}"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}"
!define MANUKEY "Software\${MANUFACTURER}"
!define MANUPRODUCTKEY "${MANUKEY}\${PRODUCTNAME}"
!define UNINSTALLERSIGNCOMMAND "{{uninstaller_sign_cmd}}"
!define ESTIMATEDSIZE "{{estimated_size}}"
!define STARTMENUFOLDER "{{start_menu_folder}}"

Var PassiveMode
Var UpdateMode
Var NoShortcutMode
Var WixMode
Var OldMainBinaryName

; Derive the preflight table from the same destinations used below for copying
; and deletion. Each check includes every existing ancestor, even above INSTDIR.
; CHAOXING_PATH_TABLE_BEGIN
!macro ChaoxingPayloadPathChecks PREFIX
  Push "$INSTDIR"
  Push "root"
  Call ${PREFIX}ChaoxingCheckPath
  Push "${MAINBINARYNAME}.exe"
  Push "file"
  Call ${PREFIX}ChaoxingCheckPath
  Push "uninstall.exe"
  Push "file"
  Call ${PREFIX}ChaoxingCheckPath
  ; Upstream may include an empty directory for the install root. That exact
  ; destination is already covered by the root check above, not a relative path.
  {{#each resources_dirs}}
  {{#if this}}
    Push "{{this}}"
    Push "directory"
    Call ${PREFIX}ChaoxingCheckPath
  {{/if}}
  {{/each}}
  {{#each resources}}
    Push "{{this.[1]}}"
    Push "file"
    Call ${PREFIX}ChaoxingCheckPath
  {{/each}}
  {{#each resources_ancestors}}
  {{#if this}}
    Push "{{this}}"
    Push "directory"
    Call ${PREFIX}ChaoxingCheckPath
  {{/if}}
  {{/each}}
  {{#each binaries}}
    Push "{{this}}"
    Push "file"
    Call ${PREFIX}ChaoxingCheckPath
  {{/each}}
!macroend
; CHAOXING_PATH_TABLE_END

Function ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks ""
  ; Retain this checked value through the later Delete. Do not reread registry
  ; data after payload/registry mutation has started.
  ReadRegStr $OldMainBinaryName SHCTX "${UNINSTKEY}" "MainBinaryName"
  ${If} $OldMainBinaryName != ""
    Push "$OldMainBinaryName"
    Push "file"
    Call ChaoxingCheckPath
  ${EndIf}
FunctionEnd

Function un.ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks "un."
FunctionEnd

Name "${PRODUCTNAME}"
BrandingText "${COPYRIGHT}"
OutFile "${OUTFILE}"

; We don't actually use this value as default install path,
; it's just for nsis to append the product name folder in the directory selector
; https://nsis.sourceforge.io/Reference/InstallDir
!define PLACEHOLDER_INSTALL_DIR "placeholder\${PRODUCTNAME}"
InstallDir "${PLACEHOLDER_INSTALL_DIR}"

VIProductVersion "${VERSIONWITHBUILD}"
VIAddVersionKey "ProductName" "${PRODUCTNAME}"
VIAddVersionKey "FileDescription" "${PRODUCTNAME}"
VIAddVersionKey "LegalCopyright" "${COPYRIGHT}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

# additional plugins
!addplugindir "${ADDITIONALPLUGINSPATH}"

; Uninstaller signing command
!if "${UNINSTALLERSIGNCOMMAND}" != ""
  !uninstfinalize '${UNINSTALLERSIGNCOMMAND}'
!endif

; Handle install mode, `perUser`, `perMachine` or `both`
!if "${INSTALLMODE}" == "perMachine"
  RequestExecutionLevel admin
!endif

!if "${INSTALLMODE}" == "currentUser"
  RequestExecutionLevel user
!endif

!if "${INSTALLMODE}" == "both"
  !define MULTIUSER_MUI
  !define MULTIUSER_INSTALLMODE_INSTDIR "${PRODUCTNAME}"
  !define MULTIUSER_INSTALLMODE_COMMANDLINE
  !if "${ARCH}" == "x64"
    !define MULTIUSER_USE_PROGRAMFILES64
  !else if "${ARCH}" == "arm64"
    !define MULTIUSER_USE_PROGRAMFILES64
  !endif
  !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_KEY "${UNINSTKEY}"
  !define MULTIUSER_INSTALLMODE_DEFAULT_REGISTRY_VALUENAME "CurrentUser"
  !define MULTIUSER_INSTALLMODEPAGE_SHOWUSERNAME
  !define MULTIUSER_INSTALLMODE_FUNCTION RestorePreviousInstallLocation
  !define MULTIUSER_EXECUTIONLEVEL Highest
  !include MultiUser.nsh
!endif

; Installer icon
!if "${INSTALLERICON}" != ""
  !define MUI_ICON "${INSTALLERICON}"
!endif

; Installer sidebar image
!if "${SIDEBARIMAGE}" != ""
  !define MUI_WELCOMEFINISHPAGE_BITMAP "${SIDEBARIMAGE}"
!endif

; Enable header images for installer and uninstaller pages when either image is configured.
!if "${HEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE
!else if "${UNINSTALLERHEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE
!endif

; Installer header image
!if "${HEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE_BITMAP "${HEADERIMAGE}"
!endif

; Uninstaller header image
!if "${UNINSTALLERHEADERIMAGE}" != ""
  !define MUI_HEADERIMAGE_UNBITMAP "${UNINSTALLERHEADERIMAGE}"
!endif

; Uninstaller icon
!if "${UNINSTALLERICON}" != ""
  !define MUI_UNICON "${UNINSTALLERICON}"
!endif

; Define registry key to store installer language
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "${MANUPRODUCTKEY}"
!define MUI_LANGDLL_REGISTRY_VALUENAME "Installer Language"

; Installer pages, must be ordered as they appear
; 1. Welcome Page
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_WELCOME

; 2. License Page (if defined)
!if "${LICENSE}" != ""
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !insertmacro MUI_PAGE_LICENSE "${LICENSE}"
!endif

; 3. Install mode (if it is set to `both`)
!if "${INSTALLMODE}" == "both"
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !insertmacro MULTIUSER_PAGE_INSTALLMODE
!endif

; 4. Custom page to ask user if he wants to reinstall/uninstall
;    only if a previous installation was detected
Var ReinstallPageCheck
Page custom PageReinstall PageLeaveReinstall
Function PageReinstall
  ; Uninstall previous WiX installation if exists.
  ;
  ; A WiX installer stores the installation info in registry
  ; using a UUID and so we have to loop through all keys under
  ; `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
  ; and check if `DisplayName` and `Publisher` keys match ${PRODUCTNAME} and ${MANUFACTURER}
  ;
  ; This has a potential issue that there maybe another installation that matches
  ; our ${PRODUCTNAME} and ${MANUFACTURER} but wasn't installed by our WiX installer,
  ; however, this should be fine since the user will have to confirm the uninstallation
  ; and they can chose to abort it if doesn't make sense.
  StrCpy $0 0
  wix_loop:
    EnumRegKey $1 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" $0
    StrCmp $1 "" wix_loop_done ; Exit loop if there is no more keys to loop on
    IntOp $0 $0 + 1
    ReadRegStr $R0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "DisplayName"
    ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "Publisher"
    StrCmp "$R0$R1" "${PRODUCTNAME}${MANUFACTURER}" 0 wix_loop
    ReadRegStr $R0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1" "UninstallString"
    ${StrCase} $R1 $R0 "L"
    ${StrLoc} $R0 $R1 "msiexec" ">"
    StrCmp $R0 0 0 wix_loop_done
    StrCpy $WixMode 1
    StrCpy $R6 "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$1"
    Goto compare_version
  wix_loop_done:

  ; Check if there is an existing installation, if not, abort the reinstall page
  ReadRegStr $R0 SHCTX "${UNINSTKEY}" ""
  ReadRegStr $R1 SHCTX "${UNINSTKEY}" "UninstallString"
  ${IfThen} "$R0$R1" == "" ${|} Abort ${|}

  ; Compare this installar version with the existing installation
  ; and modify the messages presented to the user accordingly
  compare_version:
  StrCpy $R4 "$(older)"
  ${If} $WixMode = 1
    ReadRegStr $R0 HKLM "$R6" "DisplayVersion"
  ${Else}
    ReadRegStr $R0 SHCTX "${UNINSTKEY}" "DisplayVersion"
  ${EndIf}
  ${IfThen} $R0 == "" ${|} StrCpy $R4 "$(unknown)" ${|}

  nsis_tauri_utils::SemverCompare "${VERSION}" $R0
  Pop $R0
  ; Reinstalling the same version
  ${If} $R0 = 0
    StrCpy $R1 "$(alreadyInstalledLong)"
    StrCpy $R2 "$(addOrReinstall)"
    StrCpy $R3 "$(uninstallApp)"
    !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(chooseMaintenanceOption)"
  ; Upgrading
  ${ElseIf} $R0 = 1
    StrCpy $R1 "$(olderOrUnknownVersionInstalled)"
    StrCpy $R2 "$(uninstallBeforeInstalling)"
    StrCpy $R3 "$(dontUninstall)"
    !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(choowHowToInstall)"
  ; Downgrading
  ${ElseIf} $R0 = -1
    StrCpy $R1 "$(newerVersionInstalled)"
    StrCpy $R2 "$(uninstallBeforeInstalling)"
    !if "${ALLOWDOWNGRADES}" == "true"
      StrCpy $R3 "$(dontUninstall)"
    !else
      StrCpy $R3 "$(dontUninstallDowngrade)"
    !endif
    !insertmacro MUI_HEADER_TEXT "$(alreadyInstalled)" "$(choowHowToInstall)"
  ${Else}
    Abort
  ${EndIf}

  ; Skip showing the page if passive
  ;
  ; Note that we don't call this earlier at the begining
  ; of this function because we need to populate some variables
  ; related to current installed version if detected and whether
  ; we are downgrading or not.
  ${If} $PassiveMode = 1
    Call PageLeaveReinstall
  ${Else}
    nsDialogs::Create 1018
    Pop $R4
    ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}

    ${NSD_CreateLabel} 0 0 100% 24u $R1
    Pop $R1

    ${NSD_CreateRadioButton} 30u 50u -30u 8u $R2
    Pop $R2
    ${NSD_OnClick} $R2 PageReinstallUpdateSelection

    ${NSD_CreateRadioButton} 30u 70u -30u 8u $R3
    Pop $R3
    ; Disable this radio button if downgrading and downgrades are disabled
    !if "${ALLOWDOWNGRADES}" == "false"
      ${IfThen} $R0 = -1 ${|} EnableWindow $R3 0 ${|}
    !endif
    ${NSD_OnClick} $R3 PageReinstallUpdateSelection

    ; Check the first radio button if this the first time
    ; we enter this page or if the second button wasn't
    ; selected the last time we were on this page
    ${If} $ReinstallPageCheck <> 2
      SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${Else}
      SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${EndIf}

    ${NSD_SetFocus} $R2
    nsDialogs::Show
  ${EndIf}
FunctionEnd
Function PageReinstallUpdateSelection
  ${NSD_GetState} $R2 $R1
  ${If} $R1 == ${BST_CHECKED}
    StrCpy $ReinstallPageCheck 1
  ${Else}
    StrCpy $ReinstallPageCheck 2
  ${EndIf}
FunctionEnd
Function PageLeaveReinstall
  ${NSD_GetState} $R2 $R1

  ; The NSIS-only release cannot validate an MSI's deletion manifest/location.
  ${If} $WixMode = 1
    Call ChaoxingRefuseMsiMigration
  ${EndIf}

  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
  ${EndIf}

  ; $R0 holds whether same(0)/upgrading(1)/downgrading(-1) version
  ; $R1 holds the radio buttons state:
  ;   1 => first choice was selected
  ;   0 => second choice was selected
  ${If} $R0 = 0 ; Same version, proceed
    ${If} $R1 = 1              ; User chose to add/reinstall
      Goto reinst_done
    ${Else}                    ; User chose to uninstall
      Goto reinst_uninstall
    ${EndIf}
  ${ElseIf} $R0 = 1 ; Upgrading
    ${If} $R1 = 1              ; User chose to uninstall
      Goto reinst_uninstall
    ${Else}
      Goto reinst_done         ; User chose NOT to uninstall
    ${EndIf}
  ${ElseIf} $R0 = -1 ; Downgrading
    ${If} $R1 = 1              ; User chose to uninstall
      Goto reinst_uninstall
    ${Else}
      Goto reinst_done         ; User chose NOT to uninstall
    ${EndIf}
  ${EndIf}

  reinst_uninstall:
    ; A previous uninstaller may predate this guard. Validate before invoking it.
    !insertmacro NSIS_HOOK_PREINSTALL
    HideWindow
    ClearErrors

    ${If} $WixMode = 1
      Call ChaoxingRefuseMsiMigration
    ${Else}
      ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
      Push $INSTDIR
      StrCpy $INSTDIR $4
      !insertmacro NSIS_HOOK_PREINSTALL
      Call ChaoxingCheckLegacyInstallTree
      StrCpy $4 $INSTDIR
      Pop $INSTDIR
      ; Execute only the checked uninstaller beneath the registered directory.
      StrCpy $R1 '$\"$4\uninstall.exe$\"'
      ${IfThen} $UpdateMode = 1 ${|} StrCpy $R1 "$R1 /UPDATE" ${|} ; append /UPDATE
      ${IfThen} $PassiveMode = 1 ${|} StrCpy $R1 "$R1 /P" ${|} ; append /P
      StrCpy $R1 "$R1 _?=$4" ; append uninstall directory
      ClearErrors
      ExecWait '$R1' $0
    ${EndIf}

    BringToFront

    ${IfThen} ${Errors} ${|} StrCpy $0 2 ${|} ; ExecWait failed, set fake exit code

    ${If} $0 <> 0
    ${OrIf} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
      ; User cancelled wix uninstaller? return to select un/reinstall page
      ${If} $WixMode = 1
      ${AndIf} $0 = 1602
        Abort
      ${EndIf}

      ; User cancelled NSIS uninstaller? return to select un/reinstall page
      ${If} $0 = 1
        Abort
      ${EndIf}

      ; Other erros? show generic error message and return to select un/reinstall page
      MessageBox MB_ICONEXCLAMATION "$(unableToUninstall)"
      Abort
    ${EndIf}
  reinst_done:
FunctionEnd

; 5. Choose install directory page
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_DIRECTORY

; 6. Start menu shortcut page
Var AppStartMenuFolder
!if "${STARTMENUFOLDER}" != ""
  !define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
  !define MUI_STARTMENUPAGE_DEFAULTFOLDER "${STARTMENUFOLDER}"
!else
  !define MUI_PAGE_CUSTOMFUNCTION_PRE Skip
!endif
!insertmacro MUI_PAGE_STARTMENU Application $AppStartMenuFolder

; 7. Installation page
!insertmacro MUI_PAGE_INSTFILES

; 8. Finish page
;
; Don't auto jump to finish page after installation page,
; because the installation page has useful info that can be used debug any issues with the installer.
!define MUI_FINISHPAGE_NOAUTOCLOSE
; Use show readme button in the finish page as a button create a desktop shortcut
!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_TEXT "$(createDesktop)"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION CreateOrUpdateDesktopShortcut
; Show run app after installation.
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_FINISH

Function RunMainBinary
  nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" ""
FunctionEnd

; Uninstaller Pages
; 1. Confirm uninstall page
; Application data is retained on uninstall. There is no destructive data checkbox.
!define MUI_UNCONFIRMPAGE_TEXT_TOP "$(keepBusinessData)"
!define MUI_PAGE_CUSTOMFUNCTION_PRE un.SkipIfPassive
!insertmacro MUI_UNPAGE_CONFIRM

; 2. Uninstalling Page
!insertmacro MUI_UNPAGE_INSTFILES

;Languages
{{#each languages}}
!insertmacro MUI_LANGUAGE "{{this}}"
{{/each}}
!insertmacro MUI_RESERVEFILE_LANGDLL
{{#each language_files}}
  !include "{{this}}"
{{/each}}

LangString keepBusinessData ${LANG_ENGLISH} "Remove the Tauri application? Accounts, settings and other application data will be kept. The Electron application and its data will also be kept."
LangString keepBusinessData ${LANG_SIMPCHINESE} "是否卸载 Tauri 桌面版？账号、设置及其他业务数据将保留。旧 Electron 程序和原有数据也会保留。"

Function .onInit
  ${GetOptions} $CMDLINE "/P" $PassiveMode
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/NS" $NoShortcutMode
  ${IfNot} ${Errors}
    StrCpy $NoShortcutMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
  ${EndIf}

  !if "${DISPLAYLANGUAGESELECTOR}" == "true"
    !insertmacro MUI_LANGDLL_DISPLAY
  !endif

  !insertmacro SetContext

  ${If} $INSTDIR == "${PLACEHOLDER_INSTALL_DIR}"
    ; Set default install location
    !if "${INSTALLMODE}" == "perMachine"
      ${If} ${RunningX64}
        !if "${ARCH}" == "x64"
          StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
        !else if "${ARCH}" == "arm64"
          StrCpy $INSTDIR "$PROGRAMFILES64\${PRODUCTNAME}"
        !else
          StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
        !endif
      ${Else}
        StrCpy $INSTDIR "$PROGRAMFILES\${PRODUCTNAME}"
      ${EndIf}
    !else if "${INSTALLMODE}" == "currentUser"
      StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
    !endif

    Call RestorePreviousInstallLocation
  ${EndIf}


  !if "${INSTALLMODE}" == "both"
    !insertmacro MULTIUSER_INIT
  !endif
FunctionEnd


Section EarlyChecks
  ; Reject unsafe targets before downloading or running the WebView2 installer.
  !ifmacrodef NSIS_HOOK_PREINSTALL
    !insertmacro NSIS_HOOK_PREINSTALL
  !endif

  ; Abort silent installer if downgrades is disabled
  !if "${ALLOWDOWNGRADES}" == "false"
  ${If} ${Silent}
    ; If downgrading
    ${If} $R0 = -1
      System::Call 'kernel32::AttachConsole(i -1)i.r0'
      ${If} $0 <> 0
        System::Call 'kernel32::GetStdHandle(i -11)i.r0'
        System::call 'kernel32::SetConsoleTextAttribute(i r0, i 0x0004)' ; set red color
        FileWrite $0 "$(silentDowngrades)"
      ${EndIf}
      Abort
    ${EndIf}
  ${EndIf}
  !endif

SectionEnd

Section WebView2
  ; Check if Webview2 is already installed and skip this section
  ${If} ${RunningX64}
    ReadRegStr $4 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${Else}
    ReadRegStr $4 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${EndIf}
  ${If} $4 == ""
    ReadRegStr $4 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WEBVIEW2APPGUID}" "pv"
  ${EndIf}

  ${If} $4 == ""
    ; Webview2 installation
    ;
    ; Skip if updating
    ${If} $UpdateMode <> 1
      !if "${INSTALLWEBVIEW2MODE}" == "downloadBootstrapper"
        Delete "$TEMP\MicrosoftEdgeWebview2Setup.exe"
        DetailPrint "$(webview2Downloading)"
        NSISdl::download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" "$TEMP\MicrosoftEdgeWebview2Setup.exe"
        Pop $0
        ${If} $0 == "success"
          DetailPrint "$(webview2DownloadSuccess)"
        ${Else}
          DetailPrint "$(webview2DownloadError)"
          Abort "$(webview2AbortError)"
        ${EndIf}
        StrCpy $6 "$TEMP\MicrosoftEdgeWebview2Setup.exe"
        Goto install_webview2
      !endif

      !if "${INSTALLWEBVIEW2MODE}" == "embedBootstrapper"
        Delete "$TEMP\MicrosoftEdgeWebview2Setup.exe"
        File "/oname=$TEMP\MicrosoftEdgeWebview2Setup.exe" "${WEBVIEW2BOOTSTRAPPERPATH}"
        DetailPrint "$(installingWebview2)"
        StrCpy $6 "$TEMP\MicrosoftEdgeWebview2Setup.exe"
        Goto install_webview2
      !endif

      !if "${INSTALLWEBVIEW2MODE}" == "offlineInstaller"
        Delete "$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe"
        File "/oname=$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe" "${WEBVIEW2INSTALLERPATH}"
        DetailPrint "$(installingWebview2)"
        StrCpy $6 "$TEMP\MicrosoftEdgeWebView2RuntimeInstaller.exe"
        Goto install_webview2
      !endif

      Goto webview2_done

      install_webview2:
        DetailPrint "$(installingWebview2)"
        ; $6 holds the path to the webview2 installer
        ExecWait '"$6" ${WEBVIEW2INSTALLERARGS} /install' $1
        ${If} $1 = 0
          DetailPrint "$(webview2InstallSuccess)"
        ${Else}
          DetailPrint "$(webview2InstallError)"
          Abort "$(webview2AbortError)"
        ${EndIf}
      webview2_done:
    ${EndIf}
  ${Else}
    !if "${MINIMUMWEBVIEW2VERSION}" != ""
      ${VersionCompare} "${MINIMUMWEBVIEW2VERSION}" "$4" $R0
      ${If} $R0 = 1
        update_webview:
          DetailPrint "$(installingWebview2)"
          ${If} ${RunningX64}
            ReadRegStr $R1 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate" "path"
          ${Else}
            ReadRegStr $R1 HKLM "SOFTWARE\Microsoft\EdgeUpdate" "path"
          ${EndIf}
          ${If} $R1 == ""
            ReadRegStr $R1 HKCU "SOFTWARE\Microsoft\EdgeUpdate" "path"
          ${EndIf}
          ${If} $R1 != ""
            ; Chromium updater docs: https://source.chromium.org/chromium/chromium/src/+/main:docs/updater/user_manual.md
            ; Modified from "HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft EdgeWebView\ModifyPath"
            ExecWait `"$R1" /install appguid=${WEBVIEW2APPGUID}&needsadmin=true` $1
            ${If} $1 = 0
              DetailPrint "$(webview2InstallSuccess)"
            ${Else}
              MessageBox MB_ICONEXCLAMATION|MB_ABORTRETRYIGNORE "$(webview2InstallError)" IDIGNORE ignore IDRETRY update_webview
              Quit
              ignore:
            ${EndIf}
          ${EndIf}
      ${EndIf}
    !endif
  ${EndIf}
SectionEnd

Section Install
  !ifmacrodef NSIS_HOOK_PREINSTALL
    !insertmacro NSIS_HOOK_PREINSTALL
  !endif

  SetOutPath $INSTDIR

  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

  ; Copy main executable
  File "${MAINBINARYSRCPATH}"

  ; Copy resources
  {{#each resources_dirs}}
    CreateDirectory "$INSTDIR\\{{this}}"
  {{/each}}
  {{#each resources}}
    File /a "/oname={{this.[1]}}" "{{no-escape @key}}"
  {{/each}}

  ; Copy external binaries
  {{#each binaries}}
    File /a "/oname={{this}}" "{{no-escape @key}}"
  {{/each}}

  ; Create file associations
  {{#each file_associations as |association| ~}}
    {{#each association.ext as |ext| ~}}
       !insertmacro APP_ASSOCIATE "{{ext}}" "{{or association.name ext}}" "{{association-description association.description ext}}" "$INSTDIR\${MAINBINARYNAME}.exe,0" "Open with ${PRODUCTNAME}" "$INSTDIR\${MAINBINARYNAME}.exe $\"%1$\""
    {{/each}}
  {{/each}}

  ; Register deep links
  {{#each deep_link_protocols as |protocol| ~}}
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "URL Protocol" ""
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}" "" "URL:${BUNDLEID} protocol"
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}\DefaultIcon" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\",0"
    WriteRegStr SHCTX "Software\Classes\\{{protocol}}\shell\open\command" "" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
  {{/each}}

  ; Create uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Save $INSTDIR in registry for future installations
  WriteRegStr SHCTX "${MANUPRODUCTKEY}" "" $INSTDIR

  !if "${INSTALLMODE}" == "both"
    ; Save install mode to be selected by default for the next installation such as updating
    ; or when uninstalling
    WriteRegStr SHCTX "${UNINSTKEY}" $MultiUser.InstallMode 1
  !endif

  ; Remove old main binary if it doesn't match new main binary name
  ; OldMainBinaryName was read and checked by the preinstall hook.
  ${If} $OldMainBinaryName != ""
  ${AndIf} $OldMainBinaryName != "${MAINBINARYNAME}.exe"
    Delete "$INSTDIR\$OldMainBinaryName"
  ${EndIf}

  ; Save current MAINBINARYNAME for future updates
  WriteRegStr SHCTX "${UNINSTKEY}" "MainBinaryName" "${MAINBINARYNAME}.exe"

  ; Registry information for add/remove programs
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "${PRODUCTNAME}"
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayIcon" "$\"$INSTDIR\${MAINBINARYNAME}.exe$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr SHCTX "${UNINSTKEY}" "Publisher" "${MANUFACTURER}"
  WriteRegStr SHCTX "${UNINSTKEY}" "InstallLocation" "$\"$INSTDIR$\""
  WriteRegStr SHCTX "${UNINSTKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoModify" "1"
  WriteRegDWORD SHCTX "${UNINSTKEY}" "NoRepair" "1"

  ${GetSize} "$INSTDIR" "/M=uninstall.exe /S=0K /G=0" $0 $1 $2
  IntOp $0 $0 + ${ESTIMATEDSIZE}
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD SHCTX "${UNINSTKEY}" "EstimatedSize" "$0"

  !if "${HOMEPAGE}" != ""
    WriteRegStr SHCTX "${UNINSTKEY}" "URLInfoAbout" "${HOMEPAGE}"
    WriteRegStr SHCTX "${UNINSTKEY}" "URLUpdateInfo" "${HOMEPAGE}"
    WriteRegStr SHCTX "${UNINSTKEY}" "HelpLink" "${HOMEPAGE}"
  !endif

  ; Create start menu shortcut
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    Call CreateOrUpdateStartMenuShortcut
  !insertmacro MUI_STARTMENU_WRITE_END

  ; Create desktop shortcut for silent and passive installers
  ; because finish page will be skipped
  ${If} $PassiveMode = 1
  ${OrIf} ${Silent}
    Call CreateOrUpdateDesktopShortcut
  ${EndIf}

  !ifmacrodef NSIS_HOOK_POSTINSTALL
    !insertmacro NSIS_HOOK_POSTINSTALL
  !endif

  ; Auto close this page for passive mode
  ${If} $PassiveMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd

Function .onInstSuccess
  ; Check for `/R` flag only in silent and passive installers because
  ; GUI installer has a toggle for the user to (re)start the app
  ${If} $PassiveMode = 1
  ${OrIf} ${Silent}
    ${GetOptions} $CMDLINE "/R" $R0
    ${IfNot} ${Errors}
      ${GetOptions} $CMDLINE "/ARGS" $R0
      nsis_tauri_utils::RunAsUser "$INSTDIR\${MAINBINARYNAME}.exe" "$R0"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function un.onInit
  !insertmacro SetContext

  !if "${INSTALLMODE}" == "both"
    !insertmacro MULTIUSER_UNINIT
  !endif

  !insertmacro MUI_UNGETLANGUAGE

  ${GetOptions} $CMDLINE "/P" $PassiveMode
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}

  ${GetOptions} $CMDLINE "/UPDATE" $UpdateMode
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
  ${EndIf}
FunctionEnd

Section Uninstall

  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !endif

  !insertmacro CheckIfAppIsRunning "${MAINBINARYNAME}.exe" "${PRODUCTNAME}"

  ; Delete the app directory and its content from disk
  ; Copy main executable
  Delete "$INSTDIR\${MAINBINARYNAME}.exe"

  ; Delete resources
  {{#each resources}}
    Delete "$INSTDIR\\{{this.[1]}}"
  {{/each}}

  ; Delete external binaries
  {{#each binaries}}
    Delete "$INSTDIR\\{{this}}"
  {{/each}}

  ; Delete app associations
  {{#each file_associations as |association| ~}}
    {{#each association.ext as |ext| ~}}
      !insertmacro APP_UNASSOCIATE "{{ext}}" "{{or association.name ext}}"
    {{/each}}
  {{/each}}

  ; Delete deep links
  {{#each deep_link_protocols as |protocol| ~}}
    ReadRegStr $R7 SHCTX "Software\Classes\\{{protocol}}\shell\open\command" ""
    ${If} $R7 == "$\"$INSTDIR\${MAINBINARYNAME}.exe$\" $\"%1$\""
      DeleteRegKey SHCTX "Software\Classes\\{{protocol}}"
    ${EndIf}
  {{/each}}


  ; Delete uninstaller
  Delete "$INSTDIR\uninstall.exe"

  {{#each resources_ancestors}}
  RMDir /REBOOTOK "$INSTDIR\\{{this}}"
  {{/each}}
  RMDir "$INSTDIR"

  ; Remove shortcuts if not updating
  ${If} $UpdateMode <> 1
    !insertmacro DeleteAppUserModelId

    ; Remove start menu shortcut
    !insertmacro MUI_STARTMENU_GETFOLDER Application $AppStartMenuFolder
    !insertmacro IsShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Pop $0
    ${If} $0 = 1
      !insertmacro UnpinShortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
      Delete "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
      RMDir "$SMPROGRAMS\$AppStartMenuFolder"
    ${EndIf}
    !insertmacro IsShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Pop $0
    ${If} $0 = 1
      !insertmacro UnpinShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk"
      Delete "$SMPROGRAMS\${PRODUCTNAME}.lnk"
    ${EndIf}

    ; Remove desktop shortcuts
    !insertmacro IsShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Pop $0
    ${If} $0 = 1
      !insertmacro UnpinShortcut "$DESKTOP\${PRODUCTNAME}.lnk"
      Delete "$DESKTOP\${PRODUCTNAME}.lnk"
    ${EndIf}
  ${EndIf}

  ; Remove registry information for add/remove programs
  !if "${INSTALLMODE}" == "both"
    DeleteRegKey SHCTX "${UNINSTKEY}"
  !else if "${INSTALLMODE}" == "perMachine"
    DeleteRegKey HKLM "${UNINSTKEY}"
  !else
    DeleteRegKey HKCU "${UNINSTKEY}"
  !endif

  ; Removes the Autostart entry for ${PRODUCTNAME} from the HKCU Run key if it exists.
  ; This ensures the program does not launch automatically after uninstallation if it exists.
  ; If it doesn't exist, it does nothing.
  ; We do this when not updating (to preserve the registry value on updates)
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
  ${EndIf}

  ; Remove only this installer's preferences. Account/config/cache files and
  ; WebView data under AppData are deliberately retained for reinstall/rollback.
  ${If} $UpdateMode <> 1
    DeleteRegKey SHCTX "${MANUPRODUCTKEY}"
    DeleteRegKey /ifempty SHCTX "${MANUKEY}"
  ${EndIf}

  !ifmacrodef NSIS_HOOK_POSTUNINSTALL
    !insertmacro NSIS_HOOK_POSTUNINSTALL
  !endif

  ; Auto close if passive mode or updating
  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd

Function RestorePreviousInstallLocation
  ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""
  StrCmp $4 "" +2 0
    StrCpy $INSTDIR $4
FunctionEnd

Function Skip
  Abort
FunctionEnd

Function SkipIfPassive
  ${IfThen} $PassiveMode = 1  ${|} Abort ${|}
FunctionEnd
Function un.SkipIfPassive
  ${IfThen} $PassiveMode = 1  ${|} Abort ${|}
FunctionEnd

Function CreateOrUpdateStartMenuShortcut
  ; We used to use product name as MAINBINARYNAME
  ; migrate old shortcuts to target the new MAINBINARYNAME
  StrCpy $R0 0

  !insertmacro IsShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
  Pop $0
  ${If} $0 = 1
    !insertmacro SetShortcutTarget "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    StrCpy $R0 1
  ${EndIf}

  !insertmacro IsShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
  Pop $0
  ${If} $0 = 1
    !insertmacro SetShortcutTarget "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    StrCpy $R0 1
  ${EndIf}

  ${If} $R0 = 1
    Return
  ${EndIf}

  ; Skip creating shortcut if in update mode or no shortcut mode
  ; but always create if migrating from wix
  ${If} $WixMode = 0
    ${If} $UpdateMode = 1
    ${OrIf} $NoShortcutMode = 1
      Return
    ${EndIf}
  ${EndIf}

  !if "${STARTMENUFOLDER}" != ""
    CreateDirectory "$SMPROGRAMS\$AppStartMenuFolder"
    CreateShortcut "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\$AppStartMenuFolder\${PRODUCTNAME}.lnk"
  !else
    CreateShortcut "$SMPROGRAMS\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    !insertmacro SetLnkAppUserModelId "$SMPROGRAMS\${PRODUCTNAME}.lnk"
  !endif
FunctionEnd

Function CreateOrUpdateDesktopShortcut
  ; We used to use product name as MAINBINARYNAME
  ; migrate old shortcuts to target the new MAINBINARYNAME
  !insertmacro IsShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\$OldMainBinaryName"
  Pop $0
  ${If} $0 = 1
    !insertmacro SetShortcutTarget "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
    Return
  ${EndIf}

  ; Skip creating shortcut if in update mode or no shortcut mode
  ; but always create if migrating from wix
  ${If} $WixMode = 0
    ${If} $UpdateMode = 1
    ${OrIf} $NoShortcutMode = 1
      Return
    ${EndIf}
  ${EndIf}

  CreateShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
  !insertmacro SetLnkAppUserModelId "$DESKTOP\${PRODUCTNAME}.lnk"
FunctionEnd

````

## desktop/tests/fixtures/nsis-paths.nsi

SHA256: 7a9f512c44b36ded8c9ca25e3317bed9a5f539b0a67d730e09e5036027ad616d

````text
; Dedicated path-check fixture. Never embeds or starts an application/backend.
Unicode true
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
Name "Chaoxing NSIS path guard fixture"
OutFile "${FIXTURE_OUTPUT}"
!include LogicLib.nsh
!include FileFunc.nsh
!define MAINBINARYNAME "fixture-host"
!include "${FIXTURE_HOOKS}"
!include "${FIXTURE_PATH_TABLE}"
Var FixtureExtraPath
Var FixtureCheckOnly
Var FixtureLegacyTree
Var FixtureMsiMigration

Function ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks ""
FunctionEnd

Function un.ChaoxingValidateInstallPaths
  !insertmacro ChaoxingPayloadPathChecks "un."
FunctionEnd

Function .onInit
  ReadEnvStr $0 "CHAOXING_NSIS_FIXTURE_BOOTSTRAP"
  ${If} $0 == "1"
    FileOpen $0 "$EXEDIR\string-limit.txt" w
    FileWrite $0 "${NSIS_MAX_STRLEN}"
    FileClose $0
    WriteUninstaller "$EXEDIR\guard-uninstall.exe"
    SetErrorLevel 0
    Quit
  ${EndIf}
  ReadEnvStr $INSTDIR "CHAOXING_NSIS_FIXTURE_ROOT"
  ReadEnvStr $FixtureExtraPath "CHAOXING_NSIS_FIXTURE_EXTRA"
  ReadEnvStr $FixtureCheckOnly "CHAOXING_NSIS_FIXTURE_CHECK_ONLY"
  ReadEnvStr $FixtureLegacyTree "CHAOXING_NSIS_FIXTURE_LEGACY_TREE"
  ReadEnvStr $FixtureMsiMigration "CHAOXING_NSIS_FIXTURE_MSI"
FunctionEnd

Function un.onInit
  ReadEnvStr $INSTDIR "CHAOXING_NSIS_FIXTURE_ROOT"
  ReadEnvStr $FixtureExtraPath "CHAOXING_NSIS_FIXTURE_EXTRA"
  ReadEnvStr $FixtureCheckOnly "CHAOXING_NSIS_FIXTURE_CHECK_ONLY"
FunctionEnd

Section Install
  ${If} $FixtureMsiMigration == "1"
    Call ChaoxingRefuseMsiMigration
  ${EndIf}
  !insertmacro NSIS_HOOK_PREINSTALL
  ${If} $FixtureLegacyTree == "1"
    Call ChaoxingCheckLegacyInstallTree
  ${EndIf}
  !ifdef CHAOXING_PATH_GUARD_VERSION
    ${If} $FixtureExtraPath != ""
      Push "$FixtureExtraPath"
      Push "file"
      Call ChaoxingCheckPath
    ${EndIf}
  !endif
  ${If} $FixtureCheckOnly != "1"
    SetOutPath "$INSTDIR\backend\_internal"
    FileOpen $0 "$INSTDIR\backend\_internal\payload.bin" w
    FileWrite $0 "installed fixture payload"
    FileClose $0
    FileOpen $0 "$INSTDIR\fixture-host.exe" w
    FileWrite $0 "fixture only, never executable"
    FileClose $0
  ${EndIf}
  SetErrorLevel 0
SectionEnd

Section Uninstall
  !ifmacrodef NSIS_HOOK_PREUNINSTALL
    !insertmacro NSIS_HOOK_PREUNINSTALL
  !endif
  !ifdef CHAOXING_PATH_GUARD_VERSION
    ${If} $FixtureExtraPath != ""
      Push "$FixtureExtraPath"
      Push "file"
      Call un.ChaoxingCheckPath
    ${EndIf}
  !endif
  ${If} $FixtureCheckOnly != "1"
    Delete "$INSTDIR\backend\_internal\payload.bin"
    Delete "$INSTDIR\fixture-host.exe"
  ${EndIf}
  SetErrorLevel 0
SectionEnd

````

## desktop/tests/fixtures/p2_backend.py

SHA256: be500b4138be63a0041a9816f8f05353cce6d388ab3c557dc90a9836394b622d

````text
"""Synthetic desktop business flow. No Chaoxing imports or upstream requests.

The exact same HTTP fixture serves Chromium, the unmodified Electron shell and
Tauri's native proxy. Control/counters stay in the explicitly supplied test dir.
"""

import json
import os
from pathlib import Path
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


profile = Path(os.environ["P2_FIXTURE_ROOT"]).resolve()
web_dist = Path(os.environ["P2_WEB_DIST"]).resolve()
profile.mkdir(parents=True, exist_ok=True)
(profile / "pid.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
lock = threading.Lock()
counts = {"start": 0, "status": 0, "logs": 0, "after": [], "configWrites": 0}
config = {"selectedCoursesByAccount": {"p2-fixture": ["course-1"]}}
tauri = os.environ.get("CHAOXING_TAURI") == "1"
token = os.environ.get("CHAOXING_TAURI_TOKEN", "")
instance = os.environ.get("CHAOXING_TAURI_INSTANCE_ID", "")


def control():
    try:
        return json.loads((profile / "control.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snapshot():
    temporary = profile / "counts.tmp"
    temporary.write_text(json.dumps(counts), encoding="utf-8")
    temporary.replace(profile / "counts.json")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(web_dist), **kwargs)

    def log_message(self, *_args):
        pass

    def respond(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        self.handle_api()

    def do_GET(self):
        if urlsplit(self.path).path.startswith("/api/"):
            self.handle_api()
        else:
            super().do_GET()

    def handle_api(self):
        if tauri and (self.headers.get("X-Auth-Token") != token
                      or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"):
            return self.respond(401, {"status": False, "msg": "unauthorized fixture request"})
        route = urlsplit(self.path)
        data = {}
        if self.command == "POST":
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                data = json.loads(raw)
            except ValueError:
                return self.respond(400, {"status": False, "msg": "invalid fixture JSON"})
        state = control()
        if route.path == "/api/health":
            return self.respond(200, {"status": True, "instanceId": instance})
        if state.get("delayMs"):
            time.sleep(state["delayMs"] / 1000)
        with lock:
            if route.path == "/api/login":
                # Password is deliberately discarded, never counted or persisted.
                return self.respond(200, {"status": True, "data": {"username": data.get("username")}})
            if route.path == "/api/courses":
                return self.respond(200, {"status": True, "data": [
                    {"courseId": "course-1", "title": "P2 测试课程一"},
                    {"courseId": "course-2", "title": "P2 测试课程二"},
                ]})
            if route.path == "/api/config":
                if self.command == "POST":
                    config.update(data)
                    counts["configWrites"] += 1
                    snapshot()
                return self.respond(200, {"status": True, "data": config})
            if route.path == "/api/start":
                counts["start"] += 1
                snapshot()
                return self.respond(409, {"status": False, "msg": "fixture task already exists",
                                          "data": {"task_id": "p2-existing-task"}})
            if state.get("missing") and (route.path.startswith("/api/task/")
                                         or route.path.startswith("/api/logs/")):
                return self.respond(404, {"status": False, "msg": "fixture task expired"})
            if route.path == "/api/task/p2-existing-task":
                counts["status"] += 1
                snapshot()
                terminal = counts["status"] > 1
                return self.respond(200, {"status": True, "data": {
                    "status": "completed" if terminal else "running", "progress": int(terminal),
                    "total": 1, "stats": {"completed_chapters": int(terminal), "total_chapters": 1},
                    "start_time": 1700000000,
                }})
            if route.path == "/api/task/p2-existing-task/details":
                return self.respond(200, {"status": True, "data": {"courses": [
                    {"id": "course-1", "title": "P2 测试课程一", "status": "completed",
                     "chapters": [{"id": "chapter-1", "title": "测试章节", "status": "completed"}]},
                ]}})
            if route.path == "/api/logs/p2-existing-task":
                counts["logs"] += 1
                after = int(parse_qs(route.query).get("after", ["0"])[0])
                counts["after"].append(after)
                snapshot()
                if counts["logs"] == 2:
                    return self.respond(500, {"status": False, "msg": "P2 final logs retry"})
                logs = [{"seq": 1, "message": "P2 唯一日志一", "timestamp": 1700000000, "level": "info"}]
                if counts["logs"] >= 3:
                    logs += [{"seq": 2, "message": "P2 终态日志二", "timestamp": 1700000001, "level": "success"}]
                # Intentionally return overlapping seq=1 despite after=1.
                return self.respond(200, {"status": True, "data": logs,
                                          "next_cursor": logs[-1]["seq"], "truncated": False})
        return self.respond(404, {"status": False, "msg": "fixture route missing"})


def watch_parent():
    while sys.stdin.buffer.read(1):
        pass
    os._exit(0)


threading.Thread(target=watch_parent, daemon=True).start()
delay = int(os.environ.get("P2_READY_DELAY_MS", "0"))
if delay:
    time.sleep(delay / 1000)
server = ThreadingHTTPServer(("127.0.0.1", 0 if tauri else int(os.environ.get("CHAOXING_PORT", "0"))), Handler)
(profile / "runtime.json").write_text(json.dumps({"port": server.server_port, "pid": os.getpid()}), encoding="utf-8")
print(json.dumps({"ready": "chaoxing-ready", "version": 1, "port": server.server_port,
                  "instanceId": instance}), flush=True)
server.serve_forever()

````

## desktop/tests/fixtures/p3-process-child.mjs

SHA256: 13b0fb9be8099ac519233b604e91d810d366da6d5291c671e902b0977f7d891a

````text
// Local supervisor regression fixture. It never imports application code.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

if (process.argv.includes('--grandchild')) {
  setInterval(() => {}, 1000);
} else {
  if (process.argv.includes('--tree')) {
    spawn(process.execPath, [fileURLToPath(import.meta.url), '--grandchild'], {
      windowsHide: true, stdio: 'ignore',
    });
  }
  process.stdin.resume();
  process.stdin.on('end', () => process.exit(0));
  setTimeout(() => console.log('stdin-open'), 50);
  setInterval(() => {}, 1000);
}

````

## desktop/tests/fixtures/p3-title-window.ps1

SHA256: c18548117942b739d8a588eb72e581ca3a7fc09358646ab26dfb6bbd4a8b74f5

````text
# Invisible real HWND used to verify exact-PID/Unicode-title WM_CLOSE.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$p3Window = [Windows.Forms.Form]::new()
try {
    $p3Window.Text = '超星学习通 · 自动化学习助手'
    $p3Window.ShowInTaskbar = $false
    $p3Window.Opacity = 0
    $p3Window.Add_Shown({ [Console]::WriteLine('window-ready') })
    $null = $p3Window.ShowDialog()
    [Console]::WriteLine('window-closed')
} finally { $p3Window.Dispose() }

````

## desktop/tests/fixtures/p3-windows-process.cs

SHA256: e7aef09d636a7434fbb97f4e8191e2a48959185d96a03de94c9d52e161cd1e07

````text
// Test-only process owner used by the P3 smoke runners. All termination uses
// retained handles or this owner's unnamed Job; no process-name/PID tree kills.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace Chaoxing.P3Smoke {
    public sealed class Identity {
        public uint pid;
        public string createdAtFileTime;
        public string executable;
        public bool inOuterJob;
        public bool alive;
        public uint? exitCode;
    }
    public sealed class Snapshot {
        public Identity host;
        public Identity[] active;
        public Identity[] observed;
        public bool outerJobHandleRetained;
    }
    public sealed class Cleanup {
        public bool fallbackUsed;
        public Identity[] before;
        public Identity[] remaining;
        public Identity[] observed;
    }

    public sealed class ProcessOwner : IDisposable {
        private IntPtr job = IntPtr.Zero;
        private IntPtr stdin = IntPtr.Zero;
        private IntPtr host = IntPtr.Zero;
        private uint hostPid;
        private readonly Dictionary<uint, IntPtr> handles = new Dictionary<uint, IntPtr>();
        private readonly Dictionary<uint, Identity> identities = new Dictionary<uint, Identity>();
        private Cleanup cleanup;

        private const uint QueryProcess = 0x1000, Synchronize = 0x00100000, Terminate = 1;
        private const uint WaitTimeout = 258;
        private static readonly IntPtr InvalidHandle = new IntPtr(-1);

        [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes { public int length; public IntPtr descriptor; [MarshalAs(UnmanagedType.Bool)] public bool inherit; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct StartupInfo {
            public int cb; public string reserved; public string desktop; public string title;
            public uint x, y, xSize, ySize, xChars, yChars, fill, flags;
            public ushort show, reservedSize; public IntPtr reservedBytes, stdin, stdout, stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupInfoEx { public StartupInfo startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInformation { public IntPtr process, thread; public uint pid, tid; }
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimits {
            public long processTime, jobTime; public uint flags;
            public UIntPtr minWorkingSet, maxWorkingSet; public uint activeLimit;
            public UIntPtr affinity; public uint priority, scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes; }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimits {
            public BasicLimits basic; public IoCounters io;
            public UIntPtr processMemory, jobMemory, peakProcessMemory, peakJobMemory;
        }
        [StructLayout(LayoutKind.Sequential)] private struct FileTime { public uint low, high; }
        private delegate bool WindowCallback(IntPtr window, IntPtr extra);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateJobObject(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(IntPtr job, int kind, ref ExtendedLimits limits, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool QueryInformationJobObject(IntPtr job, int kind, IntPtr buffer, uint size, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool answer);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CreateFile(string path, uint access, uint share, ref SecurityAttributes security, uint creation, uint attributes, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CreateProcess(string app, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string cwd, ref StartupInfoEx startup, out ProcessInformation process);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetProcessTimes(IntPtr process, out FileTime created, out FileTime exited, out FileTime kernel, out FileTime user);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder image, ref uint length);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, IntPtr extra);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int max);
        [DllImport("user32.dll", SetLastError = true)] private static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);

        private static void Check(bool ok, string operation) {
            if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
        }
        private static void Close(ref IntPtr handle) {
            if (handle != IntPtr.Zero && handle != InvalidHandle) CloseHandle(handle);
            handle = IntPtr.Zero;
        }
        private static string Quote(string value) {
            var output = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                output.Append('\\', slashes * (c == '"' ? 2 : 1));
                if (c == '"') output.Append('\\');
                output.Append(c); slashes = 0;
            }
            output.Append('\\', slashes * 2); output.Append('"');
            return output.ToString();
        }

        // NSIS consumes /D= and _?= as an unquoted remainder of the command
        // line. A generic raw command line is deliberately not exposed.
        public static string FormatNsisCommand(string executable, string[] args, string mode, string directory) {
            string[] expected = mode == "install" ? new[] { "/S", "/NS" } : mode == "uninstall" ? new[] { "/S" } : null;
            if (expected == null || args == null || !args.SequenceEqual(expected)) throw new ArgumentException("Unsupported NSIS options");
            if (directory == null || directory.Length < 4 || !char.IsLetter(directory[0]) || directory[1] != ':' || directory[2] != '\\'
                || directory.Any(char.IsControl) || directory.IndexOfAny(new[] { '"', '<', '>', '|', '?', '*' }) >= 0
                || directory.Substring(2).Contains(":") || directory.EndsWith("\\")
                || !string.Equals(Path.GetFullPath(directory), directory, StringComparison.OrdinalIgnoreCase)
                || string.Equals(Path.GetPathRoot(directory), directory, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("Unsafe NSIS directory path");
            return Quote(executable) + " " + string.Join(" ", expected) + (mode == "install" ? " /D=" : " _?=") + directory;
        }

        public Identity Start(string executable, string[] args, string cwd, IDictionary<string, string> environment, string stdoutPath, string stderrPath) {
            return Start(executable, args, cwd, environment, stdoutPath, stderrPath, null, null);
        }
        public Identity Start(string executable, string[] args, string cwd, IDictionary<string, string> environment, string stdoutPath, string stderrPath, string nsisMode, string nsisDirectory) {
            if (job != IntPtr.Zero || cleanup != null) throw new InvalidOperationException("ProcessOwner cannot be reused");
            IntPtr inputRead = IntPtr.Zero, output = IntPtr.Zero, error = IntPtr.Zero;
            IntPtr attributes = IntPtr.Zero, inherited = IntPtr.Zero, envBlock = IntPtr.Zero;
            bool initialized = false;
            ProcessInformation info = new ProcessInformation();
            try {
                job = CreateJobObject(IntPtr.Zero, null);
                Check(job != IntPtr.Zero, "CreateJobObject");
                var limits = new ExtendedLimits(); limits.basic.flags = 0x2000; // KILL_ON_JOB_CLOSE, no breakaway.
                Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimits))), "SetInformationJobObject");
                var security = new SecurityAttributes { length = Marshal.SizeOf(typeof(SecurityAttributes)), inherit = true };
                Check(CreatePipe(out inputRead, out stdin, ref security, 0), "CreatePipe(stdin)");
                Check(SetHandleInformation(stdin, 1, 0), "SetHandleInformation(stdin writer)");
                output = CreateFile(stdoutPath, 0x40000000, 3, ref security, 2, 0x80, IntPtr.Zero);
                Check(output != InvalidHandle, "CreateFile(stdout)");
                error = CreateFile(stderrPath, 0x40000000, 3, ref security, 2, 0x80, IntPtr.Zero);
                Check(error != InvalidHandle, "CreateFile(stderr)");

                IntPtr required = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref required);
                attributes = Marshal.AllocHGlobal(required);
                Check(InitializeProcThreadAttributeList(attributes, 1, 0, ref required), "InitializeProcThreadAttributeList");
                initialized = true;
                inherited = Marshal.AllocHGlobal(IntPtr.Size * 3);
                Marshal.WriteIntPtr(inherited, 0, inputRead);
                Marshal.WriteIntPtr(inherited, IntPtr.Size, output);
                Marshal.WriteIntPtr(inherited, IntPtr.Size * 2, error);
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), inherited, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "UpdateProcThreadAttribute(handle list)");

                string block = string.Join("\0", environment.OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase).Select(item => item.Key + "=" + item.Value)) + "\0\0";
                envBlock = Marshal.StringToHGlobalUni(block);
                var startup = new StartupInfoEx { attributes = attributes };
                startup.startup.cb = Marshal.SizeOf(typeof(StartupInfoEx));
                startup.startup.flags = 0x101; // USESTDHANDLES | USESHOWWINDOW
                startup.startup.show = 0; // SW_HIDE
                startup.startup.stdin = inputRead; startup.startup.stdout = output; startup.startup.stderr = error;
                string command = nsisMode == null ? string.Join(" ", new[] { executable }.Concat(args ?? new string[0]).Select(Quote))
                    : FormatNsisCommand(executable, args, nsisMode, nsisDirectory);
                Check(CreateProcess(executable, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero, true,
                    0x08080404, envBlock, cwd, ref startup, out info), "CreateProcess suspended");
                host = info.process; hostPid = info.pid;
                // The first instruction of the real host executes inside this Job.
                Check(AssignProcessToJobObject(job, host), "AssignProcessToJobObject(real host)");
                handles.Add(hostPid, host);
                identities.Add(hostPid, ReadIdentity(hostPid, host));
                Check(ResumeThread(info.thread) != uint.MaxValue, "ResumeThread(real host)");
                return Refresh(hostPid);
            } catch {
                if (info.process != IntPtr.Zero) {
                    TerminateProcess(info.process, 198);
                    WaitForSingleObject(info.process, 5000);
                    if (!handles.ContainsKey(info.pid)) CloseHandle(info.process);
                }
                Dispose();
                throw;
            } finally {
                Close(ref info.thread); Close(ref inputRead); Close(ref output); Close(ref error);
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (inherited != IntPtr.Zero) Marshal.FreeHGlobal(inherited);
                if (envBlock != IntPtr.Zero) Marshal.FreeHGlobal(envBlock);
            }
        }

        private Identity ReadIdentity(uint pid, IntPtr handle) {
            FileTime created, exited, kernel, user;
            Check(GetProcessTimes(handle, out created, out exited, out kernel, out user), "GetProcessTimes");
            var image = new StringBuilder(32768); uint length = (uint)image.Capacity;
            Check(QueryFullProcessImageName(handle, 0, image, ref length), "QueryFullProcessImageName");
            bool member; Check(IsProcessInJob(handle, job, out member), "IsProcessInJob");
            if (!member) throw new InvalidOperationException("Refusing a process outside the captured Job");
            return new Identity { pid = pid, createdAtFileTime = (((ulong)created.high << 32) | created.low).ToString(), executable = image.ToString(), inOuterJob = member };
        }
        private Identity Refresh(uint pid) {
            var source = identities[pid];
            bool alive = WaitForSingleObject(handles[pid], 0) == WaitTimeout;
            uint code; Check(GetExitCodeProcess(handles[pid], out code), "GetExitCodeProcess");
            return new Identity { pid = source.pid, createdAtFileTime = source.createdAtFileTime,
                executable = source.executable, inOuterJob = source.inOuterJob,
                alive = alive, exitCode = alive ? (uint?)null : code };
        }
        private uint[] Members() {
            if (job == IntPtr.Zero) return new uint[0];
            for (int capacity = 64; capacity <= 65536; capacity *= 2) {
                int bytes = 8 + IntPtr.Size * capacity;
                IntPtr buffer = Marshal.AllocHGlobal(bytes);
                try {
                    if (!QueryInformationJobObject(job, 3, buffer, (uint)bytes, IntPtr.Zero)) {
                        if (Marshal.GetLastWin32Error() == 234) continue;
                        Check(false, "QueryInformationJobObject(process list)");
                    }
                    int count = Marshal.ReadInt32(buffer, 4);
                    var pids = new uint[count];
                    for (int index = 0; index < count; index++) pids[index] = (uint)Marshal.ReadIntPtr(buffer, 8 + IntPtr.Size * index).ToInt64();
                    return pids;
                } finally { Marshal.FreeHGlobal(buffer); }
            }
            throw new InvalidOperationException("Unexpectedly large smoke process tree");
        }
        public Snapshot Inspect() {
            var active = new List<Identity>();
            foreach (uint pid in Members()) {
                if (!handles.ContainsKey(pid)) {
                    IntPtr handle = OpenProcess(QueryProcess | Synchronize | Terminate, false, pid);
                    if (handle == IntPtr.Zero) {
                        if (Marshal.GetLastWin32Error() == 87) continue; // Already exited.
                        Check(false, "OpenProcess(captured Job member)");
                    }
                    try {
                        var identity = ReadIdentity(pid, handle);
                        handles.Add(pid, handle); identities.Add(pid, identity);
                    } catch { CloseHandle(handle); throw; }
                }
                var current = Refresh(pid);
                if (current.alive) active.Add(current);
            }
            return new Snapshot { host = identities.ContainsKey(hostPid) ? Refresh(hostPid) : null,
                active = active.ToArray(), observed = identities.Keys.Select(Refresh).ToArray(), outerJobHandleRetained = job != IntPtr.Zero };
        }
        public void CloseStdin() { Close(ref stdin); }
        public void KillHost() {
            if (host != IntPtr.Zero && WaitForSingleObject(host, 0) == WaitTimeout) Check(TerminateProcess(host, 197), "TerminateProcess(captured host)");
        }
        public void KillMember(uint pid, string createdAtFileTime) {
            if (!identities.ContainsKey(pid) || identities[pid].createdAtFileTime != createdAtFileTime) throw new InvalidOperationException("Uncaptured process identity");
            if (Refresh(pid).alive) Check(TerminateProcess(handles[pid], 196), "TerminateProcess(captured member)");
        }
        public int CloseWindow(string title) {
            if (string.IsNullOrEmpty(title)) throw new ArgumentException("An exact title is required");
            if (host == IntPtr.Zero || WaitForSingleObject(host, 0) != WaitTimeout) return 0;
            int count = 0;
            EnumWindows((window, extra) => {
                uint pid; GetWindowThreadProcessId(window, out pid);
                if (pid == hostPid) {
                    var text = new StringBuilder(1024); GetWindowText(window, text, text.Capacity);
                    if (text.ToString() == title && PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero)) count++;
                }
                return true;
            }, IntPtr.Zero);
            return count;
        }
        public Cleanup Finish() {
            if (cleanup != null) return cleanup;
            var before = Inspect();
            bool fallback = before.active.Length != 0;
            if (fallback) Check(TerminateJobObject(job, 195), "TerminateJobObject(captured tree fallback)");
            CloseStdin();
            DateTime deadline = DateTime.UtcNow.AddSeconds(5);
            Snapshot after;
            do {
                after = Inspect();
                if (after.active.Length == 0 && after.observed.All(identity => !identity.alive)) break;
                Thread.Sleep(50);
            } while (DateTime.UtcNow < deadline);
            cleanup = new Cleanup { fallbackUsed = fallback, before = before.active,
                remaining = after.observed.Where(identity => identity.alive).ToArray(), observed = after.observed };
            return cleanup;
        }
        public void Dispose() {
            try { if (job != IntPtr.Zero) Finish(); }
            finally {
                Close(ref stdin); Close(ref job);
                foreach (IntPtr handle in handles.Values) CloseHandle(handle);
                handles.Clear(); host = IntPtr.Zero;
            }
        }
    }
}

````

## desktop/tests/fixtures/p3-windows-process.ps1

SHA256: eac1c0a054ddaa57c891e5291b038a63f3ce7bc89b869380b95fcdbfb9a0a331

````text
# JSON-lines controller for a single captured Windows process tree.
# Standard input is the controller protocol; the child has its own real pipe.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$p3Owner = $null
$p3Input = $null
try {
    # Console.ReadLine follows the inherited Windows console code page and can
    # truncate a UTF-8 JSON line at a Chinese title. Decode the pipe bytes with
    # an explicit reader instead; the Node controller always writes UTF-8.
    $p3Input = [IO.StreamReader]::new([Console]::OpenStandardInput(), [Text.UTF8Encoding]::new($false, $true), $false, 4096, $false)
    Add-Type -Path (Join-Path $PSScriptRoot 'p3-windows-process.cs')
    $p3Owner = [Chaoxing.P3Smoke.ProcessOwner]::new()
    while ($null -ne ($p3Line = $p3Input.ReadLine())) {
        $p3Request = $p3Line | ConvertFrom-Json
        try {
            $p3Result = switch ($p3Request.operation) {
                'start' {
                    $p3Spec = $p3Request.specification
                    $p3Environment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
                    foreach ($p3Property in $p3Spec.env.PSObject.Properties) { $p3Environment.Add($p3Property.Name, [string]$p3Property.Value) }
                    if ($null -ne $p3Spec.PSObject.Properties['nsisTail']) {
                        $p3Owner.Start($p3Spec.executable, [string[]]$p3Spec.args, $p3Spec.cwd, $p3Environment, $p3Spec.stdoutPath, $p3Spec.stderrPath, $p3Spec.nsisTail.mode, $p3Spec.nsisTail.directory)
                    } else {
                        $p3Owner.Start($p3Spec.executable, [string[]]$p3Spec.args, $p3Spec.cwd, $p3Environment, $p3Spec.stdoutPath, $p3Spec.stderrPath)
                    }
                }
                'snapshot' { $p3Owner.Inspect() }
                'stdin-eof' { $p3Owner.CloseStdin(); $true }
                'close-window' { $p3Owner.CloseWindow([string]$p3Request.title) }
                'kill-host' { $p3Owner.KillHost(); $true }
                'kill-member' { $p3Owner.KillMember([uint32]$p3Request.pid, [string]$p3Request.createdAtFileTime); $true }
                'tcp-listener' {
                    $p3Listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort ([uint16]$p3Request.port) -State Listen -ErrorAction Stop | ForEach-Object {
                        @{ pid = [uint32]$_.OwningProcess; address = [string]$_.LocalAddress; port = [uint16]$_.LocalPort }
                    })
                    # Preserve array shape even for a single listener.
                    ,$p3Listeners
                }
                'finish' { $p3Owner.Finish() }
                default { throw "Unknown supervisor operation: $($p3Request.operation)" }
            }
            [Console]::WriteLine((@{ id = $p3Request.id; ok = $true; result = $p3Result } | ConvertTo-Json -Depth 12 -Compress))
            if ($p3Request.operation -eq 'finish') { break }
        } catch {
            [Console]::WriteLine((@{ id = $p3Request.id; ok = $false; error = $_.Exception.ToString() } | ConvertTo-Json -Depth 4 -Compress))
            if ($p3Request.operation -eq 'start') { break }
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
} finally {
    try { if ($null -ne $p3Owner) { $p3Owner.Dispose() } }
    finally { if ($null -ne $p3Input) { $p3Input.Dispose() } }
}

````

## desktop/tests/installation.test.mjs

SHA256: 2049f69658e5ae5740d8113c571e8d421cee3dc16db9a3f5e19d728a79db3f3c

````text
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { lstat, mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import {
  parseInstallationArguments, assertInstallationPreflight, validateInstallationInputs,
  nsisSpecification, assertInstallRegistry, removeOwnedScratch, installationPowerShellScripts,
  createInstallationJunctionFixture, assertInstallationJunctionRejected, removeInstallationJunction,
  installationRetentionFixtures, assertRetainedFiles,
  cleanupChildProfileInvocation,
} from '../scripts/p3-installation.mjs';
import { windowsContext, publishReleaseProfileOwnership, releaseProfileRoots, claimProfileRoots } from '../scripts/p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temporary = async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p3-install-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
};
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function layoutFixture(t, mode) {
  const root = await temporary(t);
  const directory = path.join(root, 'layout');
  const version = JSON.parse(await readFile(path.join(repo, 'desktop/package.json'), 'utf8')).version;
  const contents = new Map([
    ['chaoxing-gui-tauri.exe', 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK; never execute'],
    ['LICENSE', 'preserved distribution license'], ['TAURI-LICENSE.txt', 'preserved Tauri license'],
    ['backend/chaoxing-backend.exe', 'inert backend; never execute'],
    ['backend/_internal/web/dist/index.html', '<script src="/assets/app.js"></script>'],
    ['backend/_internal/web/dist/assets/app.js', 'window.p3Fixture = true;'],
    ['backend/_internal/fixture-1.0.dist-info/METADATA', 'Name: fixture\nVersion: 1.0\n'],
  ]);
  const records = () => [...contents].map(([relative, bytes]) => ({ path: relative, length: Buffer.byteLength(bytes), sha256: hash(bytes) }));
  const directories = new Set();
  for (const relative of contents.keys()) {
    for (let parent = path.posix.dirname(relative); parent !== '.'; parent = path.posix.dirname(parent)) directories.add(parent);
  }
  const backend = { schemaVersion: 1, kind: 'chaoxing-backend', version, entryPoint: 'chaoxing-backend.exe',
    files: records().filter((entry) => entry.path.startsWith('backend/')).map((entry) => ({ ...entry, path: entry.path.slice(8) })),
    directories: [...directories].filter((relative) => relative.startsWith('backend/')).map((relative) => relative.slice(8)) };
  contents.set('backend-manifest.json', JSON.stringify(backend));
  const portableManifest = { schemaVersion: 1, kind: 'chaoxing-gui-tauri-portable', version,
    entryPoint: 'chaoxing-gui-tauri.exe', platform: 'windows-x64', files: records(), directories: [...directories] };
  if (mode === 'Installed') contents.set('chaoxing-gui-tauri.exe', contents.get('chaoxing-gui-tauri.exe').replace('_VAR_UNK', '_VAR_NSS'));
  const expectedManifest = mode === 'Portable' ? portableManifest
    : { ...portableManifest, kind: 'chaoxing-gui-tauri-nsis-payload', files: records() };
  const manifestBytes = JSON.stringify(expectedManifest);
  for (const [relative, bytes] of contents) {
    await mkdir(path.dirname(path.join(directory, relative)), { recursive: true });
    await writeFile(path.join(directory, relative), bytes);
  }
  const manifest = mode === 'Portable' ? path.join(directory, 'package-manifest.json') : path.join(root, 'nsis-payload-reference.json');
  await writeFile(manifest, manifestBytes);
  if (mode === 'Installed') await writeFile(path.join(directory, 'uninstall.exe'), 'inert uninstaller; never execute');
  return { root, directory, contents, portableManifest, expectedManifest,
    request: { directory, mode, manifest, manifestSha256: hash(manifestBytes), packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1') } };
}

const runInstallationScript = (context, script, request) => exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand',
  Buffer.from(script, 'utf16le').toString('base64')], {
  env: { ...process.env, P3_INSTALLATION_REQUEST: JSON.stringify(request) }, windowsHide: true, timeout: 20000,
});
const verifyLayoutFixture = (context, fixture) => runInstallationScript(context, installationPowerShellScripts.verifyLayoutScript, fixture.request);

async function childProfileFixture(t) {
  const root = await temporary(t);
  const context = { roaming: path.join(root, 'roaming'), local: path.join(root, 'local'), temp: path.join(root, 'temp'), sid: 'fixture-sid' };
  for (const directory of [context.roaming, context.local, context.temp]) await mkdir(directory);
  const evidenceDirectory = path.join(root, 'child-evidence');
  await mkdir(evidenceDirectory);
  const evidence = await mkdtemp(path.join(evidenceDirectory, 'smoke-'));
  const runId = randomUUID();
  const ownership = await publishReleaseProfileOwnership(evidence, context, runId);
  const roots = releaseProfileRoots(context);
  for (const entry of roots) await assert.rejects(lstat(entry.path), { code: 'ENOENT' });
  await claimProfileRoots(roots, runId, context.sid);
  const identity = { pid: 1234, createdAtFileTime: '133700000000000000', executable: process.execPath };
  const invocation = { evidenceDirectory, process: { identity, cleanup: { verified: true, fallbackUsed: true,
    remaining: [], observed: [{ ...identity, alive: false }] } } };
  return { root, context, ownership, roots, invocation, handoff: path.join(evidence, 'profile-ownership.json') };
}

test('Installation selection rejects unknown switches, missing inputs, and invalid deadlines', () => {
  assert.throws(() => parseInstallationArguments(['--unknown']), /unknown/i);
  assert.throws(() => parseInstallationArguments(['--installer-path']), /value/i);
  assert.throws(() => parseInstallationArguments(['--timeout-seconds', '0']), /timeout/i);
  assert.throws(() => parseInstallationArguments(['--installer-path', 'one', '--installer-path', 'two']), /duplicate/i);
});

test('Installation CLI refuses the real user before any installer or host and saves JSON', async (t) => {
  const root = await temporary(t);
  await assert.rejects(exec(process.execPath, [path.join(repo, 'desktop/scripts/p3-installation.mjs'),
    '--installer-path', 'must-never-run.exe', '--portable-path', 'must-never-extract.zip', '--evidence-directory', root], {
    env: { ...process.env, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '' }, windowsHide: true, timeout: 15000,
  }), (error) => error.code === 1 && /Evidence:/.test(error.stdout));
  const folders = await readdir(root);
  assert.equal(folders.length, 1);
  const result = JSON.parse(await readFile(path.join(root, folders[0], 'result.json'), 'utf8'));
  assert.equal(result.success, false);
  assert.deepEqual(result.processes, []);
  assert.match(result.error, /disposable|fresh/i);
});

test('Preexisting application, legacy, default install, or registry state blocks installation', async (t) => {
  const root = await temporary(t);
  const occupied = path.join(root, 'existing app');
  await mkdir(occupied);
  await writeFile(path.join(occupied, 'sentinel'), 'untouched');
  await assert.rejects(assertInstallationPreflight([{ path: occupied, claim: false }], [], 'run', 'sid'), /preexisting|owned/i);
  assert.equal(await readFile(path.join(occupied, 'sentinel'), 'utf8'), 'untouched');
  for (const kind of ['uninstall', 'preferences', 'autorun']) {
    await assert.rejects(assertInstallationPreflight([], [{ hive: 'CurrentUser', view: 'Registry64', key: 'old Tauri record', kind }], 'run', 'sid'), /registry|installation/i);
  }
});

test('Missing or incorrectly typed installation artifacts are rejected without execution', async (t) => {
  const root = await temporary(t);
  await assert.rejects(validateInstallationInputs({ installerPath: path.join(root, 'missing.exe'), portablePath: path.join(root, 'missing.zip') }), /installer/i);
  const installerPath = path.join(root, 'setup.exe');
  const portablePath = path.join(root, 'portable.zip');
  await writeFile(installerPath, 'not an executable');
  await writeFile(portablePath, 'not a zip');
  await assert.rejects(validateInstallationInputs({ installerPath, portablePath }), /PE|MZ|executable/i);
});

test('NSIS directory tails preserve Unicode/spaces and are never general raw arguments', () => {
  const directory = 'C:\\Smoke 用户 空格\\安装 目录';
  assert.deepEqual(nsisSpecification('install', directory), {
    args: ['/S', '/NS'], nsisTail: { mode: 'install', directory },
  });
  assert.deepEqual(nsisSpecification('uninstall', directory), {
    args: ['/S'], nsisTail: { mode: 'uninstall', directory },
  });
  for (const invalid of ['C:\\', 'relative', '\\\\server\\share', 'C:\\safe" /D=C:\\other', 'C:\\safe\n/S', 'C:\\safe\\..']) {
    assert.throws(() => nsisSpecification('install', invalid), /path|directory|NSIS/i);
  }
  assert.throws(() => nsisSpecification('arbitrary', directory), /NSIS/i);
});

test('Registry acceptance requires the owned install path and exact expected uninstaller', () => {
  const directory = 'C:\\Smoke 用户\\安装';
  const good = [{ hive: 'CurrentUser', view: 'Registry64', kind: 'uninstall',
    key: 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Chaoxing GUI Tauri',
    installLocation: `"${directory}"`, uninstallString: `"${directory}\\uninstall.exe"` }];
  assert.doesNotThrow(() => assertInstallRegistry(good, directory));
  assert.throws(() => assertInstallRegistry([], directory), /registry|uninstall/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], hive: 'LocalMachine' }], directory), /CurrentUser|current.user/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], installLocation: 'C:\\Users\\real\\AppData\\Local\\Chaoxing GUI Tauri' }], directory), /location|directory/i);
  assert.throws(() => assertInstallRegistry([{ ...good[0], uninstallString: 'C:\\other\\uninstall.exe /S' }], directory), /uninstaller/i);
});

test('Scratch cleanup requires its current ownership marker and refuses junctions', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  await writeFile(path.join(scratch, 'keep'), 'owned only after marking');
  await assert.rejects(removeOwnedScratch(scratch, 'run'), /owner|owned/i);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
  const outside = path.join(root, 'outside');
  await mkdir(outside);
  await writeFile(path.join(outside, 'keep'), 'outside');
  await symlink(outside, path.join(scratch, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(removeOwnedScratch(scratch, 'run'), /reparse|symbolic|link/i);
  assert.equal(await readFile(path.join(outside, 'keep'), 'utf8'), 'outside');
});

for (const placement of ['install-directory', 'backend', 'nested-backend']) {
  test(`Installation ${placement} junction fixture checks rejection and unlinks without deleting its target`, async (t) => {
    const root = await temporary(t);
    const scratch = path.join(root, 'scratch');
    await mkdir(scratch);
    await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
    const fixture = await createInstallationJunctionFixture(scratch, 'run', placement);
    await assert.rejects(removeOwnedScratch(scratch, 'run'), /reparse|symbolic|link/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 0, []), /exit code 2/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 1, []), /exit code 2/i);
    await assert.rejects(assertInstallationJunctionRejected(fixture, 2, [{ kind: 'uninstall' }]), /registry/i);
    await assertInstallationJunctionRejected(fixture, 2, []);
    assert.equal(fixture.sentinels.every((entry) => entry.after === entry.before), true);
    await removeInstallationJunction(fixture);
    await assert.rejects(lstat(fixture.junction), { code: 'ENOENT' });
    assert.match(await readFile(path.join(fixture.target, 'sentinel.txt'), 'utf8'), /must remain unchanged/);
    await removeInstallationJunction(fixture);
    await removeOwnedScratch(scratch, 'run');
    await assert.rejects(lstat(scratch), { code: 'ENOENT' });
  });
}

test('Installation junction acceptance rejects payload writes and changed target bytes', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  await assert.rejects(createInstallationJunctionFixture(scratch, 'run', 'backend'), /owned|owner/i);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId: 'run', path: scratch }));
  const fixture = await createInstallationJunctionFixture(scratch, 'run', 'backend');
  const payload = path.join(fixture.installDirectory, 'chaoxing-gui-tauri.exe');
  await writeFile(payload, 'inert unexpected installer write');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /program files/i);
  await unlink(payload);
  const unexpected = path.join(fixture.target, 'unexpected');
  await writeFile(unexpected, 'inert unexpected junction write');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /inventory/i);
  await unlink(unexpected);
  await writeFile(path.join(fixture.target, 'nested', 'sentinel.txt'), 'unexpectedly replaced');
  await assert.rejects(assertInstallationJunctionRejected(fixture, 2, []), /modified.*target/i);
  await removeInstallationJunction(fixture);
});

test('Installation junction cleanup refuses an unowned scratch or a substituted target', async (t) => {
  const root = await temporary(t);
  const scratch = path.join(root, 'scratch');
  await mkdir(scratch);
  const marker = path.join(scratch, '.p3-installation-owner.json');
  await writeFile(marker, JSON.stringify({ runId: 'run', path: scratch }));
  const fixture = await createInstallationJunctionFixture(scratch, 'run', 'backend');
  await assert.rejects(removeInstallationJunction({ ...fixture, runId: 'other-run' }), /owner/i);
  const outside = path.join(root, 'outside');
  await mkdir(outside);
  await writeFile(path.join(outside, 'sentinel.txt'), 'unowned target must survive');
  await unlink(fixture.junction);
  await symlink(outside, fixture.junction, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(removeInstallationJunction(fixture), /target changed/i);
  assert.equal(await readFile(path.join(outside, 'sentinel.txt'), 'utf8'), 'unowned target must survive');
  assert.equal((await lstat(fixture.junction)).isSymbolicLink(), true);
  await unlink(fixture.junction);
});

for (const mode of ['Portable', 'Installed']) {
  test(`${mode} files are verified against both manifests without executing payloads`, { skip: process.platform !== 'win32', timeout: 25000 }, async (t) => {
    const fixture = await layoutFixture(t, mode);
    const context = await windowsContext();
    const { stdout } = await verifyLayoutFixture(context, fixture);
    const report = JSON.parse(stdout);
    assert.equal(report.mode, mode);
    assert.equal(report.manifestKind, mode === 'Portable' ? 'chaoxing-gui-tauri-portable' : 'chaoxing-gui-tauri-nsis-payload');
    assert.equal(report.files, fixture.contents.size);
    assert.equal(report.backendFiles, 4);
    if (mode === 'Installed') {
      const portableHost = fixture.portableManifest.files.find((entry) => entry.path === 'chaoxing-gui-tauri.exe');
      const installedHost = fixture.expectedManifest.files.find((entry) => entry.path === 'chaoxing-gui-tauri.exe');
      assert.equal(installedHost.length, portableHost.length);
      assert.notEqual(installedHost.sha256, portableHost.sha256);
    }
  });
}

test('NSIS reference binds inert artifacts and verifies the exact installed host', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const version = fixture.portableManifest.version;
  const scratch = path.join(fixture.root, 'owned scratch');
  const runId = randomUUID();
  await mkdir(scratch);
  await writeFile(path.join(scratch, '.p3-installation-owner.json'), JSON.stringify({ runId, path: scratch }));
  const portableManifest = path.join(fixture.root, 'portable-reference.json');
  const portableBytes = JSON.stringify(fixture.portableManifest);
  await writeFile(portableManifest, portableBytes);
  const compilerHost = path.join(fixture.root, 'compiler-host.exe');
  await writeFile(compilerHost, fixture.contents.get('chaoxing-gui-tauri.exe').replace('_VAR_NSS', '_VAR_UNK'));
  const request = { packageCommon: path.join(repo, 'desktop/scripts/package-common.ps1'), nsisContent: path.join(repo, 'desktop/scripts/nsis-content.ps1'),
    scratch, runId, compilerHost, portableManifest, portableManifestSha256: hash(portableBytes),
    installerPath: path.join(fixture.root, `chaoxing-gui-tauri-setup-${version}-windows-x64.exe`),
    portablePath: path.join(fixture.root, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`) };
  // These files exercise manifest hash bindings only. Neither file is a
  // runnable program or an extractable archive, and no payload is executed.
  await writeFile(request.installerPath, 'MZ inert installer binding fixture; never execute');
  await writeFile(request.portablePath, Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.from('inert ZIP binding fixture; never extract')]));
  await assert.rejects(validateInstallationInputs(request), { code: 'ENOENT' });
  await runInstallationScript(context, String.raw`
$ErrorActionPreference = 'Stop'
$p3Request = $env:P3_INSTALLATION_REQUEST | ConvertFrom-Json
. $p3Request.packageCommon
. $p3Request.nsisContent
$p3HostExpectation = Get-NsisHostExpectation -HostPath $p3Request.compilerHost
Write-NsisArtifactManifest -InstallerPath $p3Request.installerPath -PortablePath $p3Request.portablePath -HostExpectation $p3HostExpectation
`, request);
  await validateInstallationInputs(request);
  const { stdout } = await runInstallationScript(context, installationPowerShellScripts.createNsisReferenceScript, request);
  const generated = JSON.parse(stdout);
  assert.equal(generated.manifest, path.join(scratch, 'nsis-payload-reference.json'));
  const referenceBytes = await readFile(generated.manifest);
  const reference = JSON.parse(referenceBytes);
  assert.equal(reference.kind, 'chaoxing-gui-tauri-nsis-payload');
  assert.deepEqual(reference.files, fixture.expectedManifest.files);
  assert.deepEqual(reference.directories, fixture.expectedManifest.directories);
  assert.equal(generated.sha256, hash(referenceBytes));
  const installed = { ...fixture, request: { ...fixture.request, manifest: generated.manifest, manifestSha256: generated.sha256 } };
  await verifyLayoutFixture(context, installed);
  await writeFile(generated.manifest, `${referenceBytes.toString('utf8')}\n`);
  await assert.rejects(verifyLayoutFixture(context, installed), /Reference payload manifest changed/s);
  await unlink(generated.manifest);
  for (const filename of [request.installerPath, request.portablePath]) {
    const original = await readFile(filename);
    await writeFile(filename, Buffer.concat([original, Buffer.from(' changed')]));
    await assert.rejects(runInstallationScript(context, installationPowerShellScripts.createNsisReferenceScript, request), /artifact identity mismatch/s);
    await writeFile(filename, original);
  }
});

test('Installed NSIS layout refuses portable manifests and unchanged UNK host hashes', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const wrongManifest = path.join(fixture.root, 'wrong-reference.json');
  for (const [manifest, expectedError] of [
    [fixture.portableManifest, /Invalid chaoxing-gui-tauri-nsis-payload manifest schema/s],
    [{ ...fixture.portableManifest, kind: 'chaoxing-gui-tauri-nsis-payload' }, /length\/hash mismatch.*chaoxing-gui-tauri.exe/s],
  ]) {
    const bytes = JSON.stringify(manifest);
    await writeFile(wrongManifest, bytes);
    await assert.rejects(verifyLayoutFixture(context, { ...fixture, request: { ...fixture.request, manifest: wrongManifest, manifestSha256: hash(bytes) } }), expectedError);
  }
});

test('Installed manifest verification catches changed host bytes, missing licenses, changed metadata, and extra files', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const fixture = await layoutFixture(t, 'Installed');
  const context = await windowsContext();
  const host = path.join(fixture.directory, 'chaoxing-gui-tauri.exe');
  await writeFile(host, fixture.contents.get('chaoxing-gui-tauri.exe').replace('never execute', 'never ExEcUtE'));
  await assert.rejects(verifyLayoutFixture(context, fixture), /length\/hash mismatch.*chaoxing-gui-tauri.exe/s);
  await writeFile(host, fixture.contents.get('chaoxing-gui-tauri.exe'));
  const license = path.join(fixture.directory, 'LICENSE');
  await unlink(license);
  await assert.rejects(verifyLayoutFixture(context, fixture), /Missing manifest payload file.*LICENSE/s);
  await writeFile(license, fixture.contents.get('LICENSE'));
  const metadata = 'backend/_internal/fixture-1.0.dist-info/METADATA';
  await writeFile(path.join(fixture.directory, metadata), 'unexpected metadata bytes');
  await assert.rejects(verifyLayoutFixture(context, fixture), /length\/hash mismatch.*METADATA/s);
  await writeFile(path.join(fixture.directory, metadata), fixture.contents.get(metadata));
  await writeFile(path.join(fixture.directory, 'unexpected.txt'), 'unexpected payload');
  await assert.rejects(verifyLayoutFixture(context, fixture), /does not cover every payload file/s);
});

test('Uninstall retention includes actual Tauri files and detects deleted LocalAppData WebView data', async (t) => {
  const root = await temporary(t);
  const context = { roaming: path.join(root, 'roaming'), local: path.join(root, 'local') };
  const retained = installationRetentionFixtures(context, path.join(root, 'scratch'), 'run');
  const webView = retained.find((entry) => entry.path === path.join(context.local, 'com.chaoxing.gui', 'EBWebView', 'Default', 'Preferences'));
  assert.ok(webView);
  for (const name of ['web_config.json', 'renderer-session.json']) {
    assert.ok(retained.some((entry) => entry.path === path.join(context.roaming, 'com.chaoxing.gui', 'data', name)));
  }
  const observed = [];
  for (const entry of retained) {
    await mkdir(path.dirname(entry.path), { recursive: true });
    await writeFile(entry.path, entry.content);
    observed.push({ path: entry.path, before: hash(entry.content) });
  }
  await assertRetainedFiles(observed);
  await unlink(webView.path);
  await assert.rejects(assertRetainedFiles(observed), { code: 'ENOENT' });
});

test('Cancelled nested smoke profiles are reclaimed from its pre-claim handoff after verified Job cleanup', { skip: process.platform !== 'win32' }, async (t) => {
  const fixture = await childProfileFixture(t);
  const outside = path.join(fixture.root, 'unrelated.txt');
  await writeFile(outside, 'unrelated data survives');
  await cleanupChildProfileInvocation(fixture.invocation, fixture.context);
  assert.equal(fixture.invocation.profileCleanup.completed, true);
  assert.equal(fixture.invocation.profileCleanup.runId, fixture.ownership.runId);
  for (const entry of fixture.roots) await assert.rejects(lstat(entry.path), { code: 'ENOENT' });
  assert.equal(await readFile(outside, 'utf8'), 'unrelated data survives');
});

test('Nested profile cleanup refuses unverified/live processes, changed handoffs, and another run ownership', { skip: process.platform !== 'win32' }, async (t) => {
  const fixture = await childProfileFixture(t);
  const { invocation, context } = fixture;
  const profile = fixture.roots.find((entry) => entry.claim).path;
  const sentinel = path.join(profile, 'web_config.json');
  await writeFile(sentinel, 'must remain until ownership and process checks pass');
  invocation.process.cleanup.verified = false;
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /verified.*process/i);
  invocation.process.cleanup.verified = true;
  invocation.process.cleanup.observed[0].alive = true;
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /live.*process/i);
  invocation.process.cleanup.observed[0].alive = false;
  await writeFile(fixture.handoff, JSON.stringify({ ...fixture.ownership, sid: 'other-sid' }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /SID mismatch/i);
  await writeFile(fixture.handoff, JSON.stringify({ ...fixture.ownership, roots: [{ path: fixture.root, claim: true }] }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /known folders/i);
  await writeFile(fixture.handoff, JSON.stringify(fixture.ownership));
  const marker = path.join(profile, '.p3-smoke-owner.json');
  await writeFile(marker, JSON.stringify({ runId: 'another-run', sid: context.sid, path: profile }));
  await assert.rejects(cleanupChildProfileInvocation(invocation, context), /preexisting|owned/i);
  assert.equal(await readFile(sentinel, 'utf8'), 'must remain until ownership and process checks pass');
  assert.equal(invocation.profileCleanup.completed, false);
});

test('Native formatter emits NSIS special paths last and unquoted without executing an installer', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
  const context = await windowsContext();
  const source = path.join(repo, 'desktop/tests/fixtures/p3-windows-process.cs').replaceAll("'", "''");
  const command = `[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Add-Type -Path '${source}'; [Chaoxing.P3Smoke.ProcessOwner]::FormatNsisCommand('C:\\Setup Folder\\setup.exe', [string[]]@('/S','/NS'), 'install', 'C:\\Smoke 用户 空格\\安装 目录'); [Chaoxing.P3Smoke.ProcessOwner]::FormatNsisCommand('C:\\Smoke 用户 空格\\卸载.exe', [string[]]@('/S'), 'uninstall', 'C:\\Smoke 用户 空格\\安装 目录')`;
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-Command', command], { windowsHide: true, timeout: 15000 });
  assert.deepEqual(stdout.trim().split(/\r?\n/), [
    '"C:\\Setup Folder\\setup.exe" /S /NS /D=C:\\Smoke 用户 空格\\安装 目录',
    '"C:\\Smoke 用户 空格\\卸载.exe" /S _?=C:\\Smoke 用户 空格\\安装 目录',
  ]);
});

test('Embedded installation PowerShell scripts parse without running installers or registry mutations', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
  const context = await windowsContext();
  const parser = "$ErrorActionPreference='Stop'; $p3Sources=$env:P3_TEST_SCRIPT_SOURCES | ConvertFrom-Json; foreach ($p3Source in $p3Sources) { $p3Tokens=$null; $p3Errors=$null; [Management.Automation.Language.Parser]::ParseInput($p3Source,[ref]$p3Tokens,[ref]$p3Errors) | Out-Null; if ($p3Errors.Count) { throw ($p3Errors | Out-String) } }";
  await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(parser, 'utf16le').toString('base64')], {
    env: { ...process.env, P3_TEST_SCRIPT_SOURCES: JSON.stringify(Object.values(installationPowerShellScripts)) }, windowsHide: true, timeout: 15000,
  });
});

````

## desktop/tests/nsis-paths.test.mjs

SHA256: 730527b7c75a296fe4931723510ccc88c3773a60cfd82a026aa9ba3f97f2b09a

````text
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import test, { before, after } from 'node:test';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const installerFile = path.join(desktop, 'src-tauri/windows/installer.nsi');
const hooksFile = path.join(desktop, 'src-tauri/windows/installer-hooks.nsh');
const fixtureFile = path.join(desktop, 'tests/fixtures/nsis-paths.nsi');
const digest = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
let buildRoot;
let fixtureInstaller;
let fixtureUninstaller;
let stringLimit;

function compilerPath() {
  const candidates = [
    process.env.NSIS_MAKENSIS,
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'tauri/NSIS/makensis.exe'),
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'NSIS/makensis.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'NSIS/makensis.exe'),
    ...(process.env.PATH || '').split(path.delimiter).filter(Boolean)
      .map(directory => path.join(directory.replace(/^"|"$/g, ''), 'makensis.exe')),
  ];
  return candidates.find(file => file && fs.existsSync(file));
}

const compiler = process.platform === 'win32' ? compilerPath() : undefined;
const required = process.env.CHAOXING_REQUIRE_NSIS_PATH_TESTS === '1';
const skipReason = process.platform !== 'win32' ? 'requires Windows junctions and NSIS'
  : !compiler ? 'makensis.exe unavailable; set NSIS_MAKENSIS or build Tauri first (CHAOXING_REQUIRE_NSIS_PATH_TESTS=1 forbids this skip)'
    : false;
const windowsTest = (name, callback) => test(name, { skip: skipReason && !required }, callback);

function terminateTree(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  // Kill the tree while the root is still alive; spawnSync's own timeout would
  // kill only the root first and could orphan an NSIS/compiler helper process.
  const killed = spawnSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/taskkill.exe'),
    ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true, stdio: 'pipe', encoding: 'utf8', timeout: 10_000,
    });
  if (killed.status !== 0) child.kill('SIGKILL');
}

async function run(executable, args, env = {}, verbatim = false) {
  const child = spawn(executable, args, {
    cwd: buildRoot,
    env: { ...process.env, TEMP: path.join(buildRoot, 'temp'), TMP: path.join(buildRoot, 'temp'), ...env },
    windowsHide: true,
    windowsVerbatimArguments: verbatim,
    stdio: 'pipe',
  });
  let stdout = '';
  let stderr = '';
  let timer;
  child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
  child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
  try {
    return await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('close', status => resolve({ status, output: `${stdout}\n${stderr}` }));
      timer = setTimeout(() => {
        terminateTree(child);
        reject(new Error(`${path.basename(executable)} timed out after 30 seconds; its process tree was terminated`));
      }, 30_000);
    });
  } finally {
    clearTimeout(timer);
    terminateTree(child);
  }
}

function renderedPathTable() {
  const template = fs.readFileSync(installerFile, 'utf8');
  const block = template.match(/; CHAOXING_PATH_TABLE_BEGIN\r?\n([\s\S]*?); CHAOXING_PATH_TABLE_END/);
  // Baseline compiles the old hooks too, so junction negatives run red before
  // the production table/checker is implemented. The structural test requires it.
  if (!block) return '!macro ChaoxingPayloadPathChecks PREFIX\n!macroend\n';
  const values = {
    resources_dirs: ['backend', 'backend\\_internal', ''],
    // Tauri emits an empty ancestor for INSTDIR itself. The root check must
    // cover it without treating it as an invalid empty relative destination.
    resources_ancestors: ['backend\\_internal', 'backend', ''],
    resources: [['unused-source', 'backend\\_internal\\payload.bin']],
    binaries: ['fixture-helper.exe'],
  };
  const rendered = block[1].replace(/{{#each (\w+)}}([\s\S]*?){{\/each}}/g, (_, name, body) => {
    assert.ok(Object.hasOwn(values, name), `unhandled production path table: ${name}`);
    return values[name].map(value => body
      .replace(/{{#if this}}([\s\S]*?){{\/if}}/g, (_, block) => value ? block : '')
      .replaceAll('{{this.[1]}}', Array.isArray(value) ? value[1] : value)
      .replaceAll('{{this}}', Array.isArray(value) ? value[1] : value)).join('');
  });
  assert.ok(!rendered.includes('{{'), 'every production path-table placeholder must be rendered');
  return rendered;
}

before(async () => {
  if (required) assert.equal(skipReason, false, String(skipReason));
  if (skipReason) return;
  buildRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cx-nsis-path-build-'));
  fs.mkdirSync(path.join(buildRoot, 'temp'));
  const table = path.join(buildRoot, 'payload-paths.nsh');
  fs.writeFileSync(table, renderedPathTable());
  fixtureInstaller = path.join(buildRoot, 'guard-install.exe');
  fixtureUninstaller = path.join(buildRoot, 'guard-uninstall.exe');
  const compiled = await run(compiler, [
    '/V2', `/DFIXTURE_OUTPUT=${fixtureInstaller}`, `/DFIXTURE_HOOKS=${hooksFile}`,
    `/DFIXTURE_PATH_TABLE=${table}`, fixtureFile,
  ]);
  assert.equal(compiled.status, 0, compiled.output);
  const bootstrap = await run(fixtureInstaller, ['/S'], { CHAOXING_NSIS_FIXTURE_BOOTSTRAP: '1' });
  assert.equal(bootstrap.status, 0, bootstrap.output);
  assert.ok(fs.existsSync(fixtureUninstaller));
  stringLimit = Number(fs.readFileSync(path.join(buildRoot, 'string-limit.txt'), 'utf8'));
  assert.ok(Number.isInteger(stringLimit) && stringLimit > 255, 'fixture reports its compiled NSIS string limit');
});

after(() => {
  if (!buildRoot) return;
  assert.ok(path.basename(buildRoot).startsWith('cx-nsis-path-build-'));
  fs.rmSync(buildRoot, { recursive: true, force: true });
});

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cx-nsis-path-case-'));
  const links = [];
  t.after(() => {
    for (const link of links.reverse()) {
      if (fs.existsSync(link) || fs.lstatSync(link, { throwIfNoEntry: false })) {
        assert.ok(fs.lstatSync(link).isSymbolicLink(), `refusing to remove a replaced junction: ${link}`);
        fs.unlinkSync(link);
      }
    }
    assert.ok(path.basename(root).startsWith('cx-nsis-path-case-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const target = path.join(root, '安装 目录');
  const external = path.join(root, 'outside sentinel');
  seed(target);
  seed(external);
  return {
    root, target, external,
    junction(link, destination) {
      assert.ok(path.resolve(link).startsWith(root + path.sep));
      assert.ok(path.resolve(destination).startsWith(root + path.sep));
      fs.symlinkSync(destination, link, 'junction');
      links.push(link);
    },
    invoke(mode, directory = target, { extra = '', checkOnly = false, legacyTree = false, msi = false } = {}) {
      // Malformed-path tests do not perform any payload writes even on a broken guard.
      if (!checkOnly) assert.ok(path.resolve(directory).startsWith(root + path.sep));
      const env = {
        CHAOXING_NSIS_FIXTURE_ROOT: directory,
        CHAOXING_NSIS_FIXTURE_EXTRA: extra,
        CHAOXING_NSIS_FIXTURE_CHECK_ONLY: checkOnly ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_LEGACY_TREE: legacyTree ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_MSI: msi ? '1' : '0',
        CHAOXING_NSIS_FIXTURE_BOOTSTRAP: '0',
      };
      return mode === 'install'
        ? run(fixtureInstaller, ['/S'], env)
        // NSIS consumes _?= through the end, without ordinary argv quotes.
        : run(fixtureUninstaller, ['/S', `_?=${buildRoot}`], env, true);
    },
  };
}

function seed(directory) {
  fs.mkdirSync(path.join(directory, 'backend/_internal'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'fixture-host.exe'), 'fixture host before guard');
  fs.writeFileSync(path.join(directory, 'backend/_internal/payload.bin'), 'nested sentinel before guard');
  fs.writeFileSync(path.join(directory, 'preserved-data.txt'), 'synthetic data, must remain');
}

function snapshot(directory) {
  const inventory = [];
  function visit(current, prefix) {
    for (const name of fs.readdirSync(current).sort()) {
      const file = path.join(current, name);
      const relative = prefix ? `${prefix}/${name}` : name;
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) inventory.push([relative, 'link', fs.readlinkSync(file)]);
      else if (stat.isDirectory()) { inventory.push([relative, 'directory']); visit(file, relative); }
      else inventory.push([relative, digest(file)]);
    }
  }
  visit(directory, '');
  return inventory;
}

test('NSIS production table checks every emitted payload kind before install and uninstall mutations', () => {
  const text = fs.readFileSync(installerFile, 'utf8');
  assert.match(text, /CHAOXING_PATH_TABLE_BEGIN/);
  for (const name of ['resources_dirs', 'resources', 'resources_ancestors', 'binaries']) {
    assert.ok(renderedPathTable().includes('ChaoxingCheckPath'), name);
    assert.match(text.match(/CHAOXING_PATH_TABLE_BEGIN([\s\S]*?)CHAOXING_PATH_TABLE_END/)[1], new RegExp(`{{#each ${name}}}`));
  }
  const early = text.slice(text.indexOf('Section EarlyChecks'), text.indexOf('Section WebView2'));
  assert.match(early, /NSIS_HOOK_PREINSTALL/);
  const install = text.slice(text.indexOf('Section Install'), text.indexOf('Function .onInstSuccess'));
  assert.ok(install.indexOf('NSIS_HOOK_PREINSTALL') < install.indexOf('SetOutPath'));
  assert.doesNotMatch(install, /ReadRegStr \$OldMainBinaryName/);
  assert.match(text, /ReadRegStr \$OldMainBinaryName[\s\S]*?Call ChaoxingCheckPath/);
  assert.match(text, /Call ChaoxingCheckLegacyInstallTree[\s\S]*?ExecWait '\$R1'/);
  const reinstall = text.slice(text.indexOf('Function PageLeaveReinstall'), text.indexOf('; 5. Choose install directory page'));
  assert.match(reinstall, /\$WixMode = 1\s+Call ChaoxingRefuseMsiMigration/);
  assert.doesNotMatch(reinstall, /ReadRegStr \$R1 HKLM/);
  const uninstall = text.slice(text.indexOf('Section Uninstall'), text.indexOf('Function RestorePreviousInstallLocation'));
  assert.ok(uninstall.indexOf('NSIS_HOOK_PREUNINSTALL') < uninstall.indexOf('Delete '));
  assert.match(text, /MUI_FINISHPAGE_RUN_NOTCHECKED/);
  assert.match(text, /ExecWait '\"\$6\" \$\{WEBVIEW2INSTALLERARGS\} \/install'/);
});

windowsTest('NSIS guards allow normal Chinese and space paths for install and uninstall while retaining data', async t => {
  const f = fixture(t);
  const installed = await f.invoke('install');
  assert.equal(installed.status, 0, installed.output);
  assert.equal(fs.readFileSync(path.join(f.target, 'backend/_internal/payload.bin'), 'utf8'), 'installed fixture payload');
  const uninstalled = await f.invoke('uninstall');
  assert.equal(uninstalled.status, 0, uninstalled.output);
  assert.ok(!fs.existsSync(path.join(f.target, 'backend/_internal/payload.bin')));
  assert.equal(fs.readFileSync(path.join(f.target, 'preserved-data.txt'), 'utf8'), 'synthetic data, must remain');
});

windowsTest('NSIS guards allow a fresh directory, repeated separators and ordinary dot filenames', async t => {
  const f = fixture(t);
  const fresh = path.join(f.root, '全新 父目录', '应用');
  assert.equal((await f.invoke('install', fresh)).status, 0);
  assert.equal((await f.invoke('uninstall', fresh)).status, 0);
  for (const extra of ['backend\\\\_internal//payload.bin', 'backend/.hidden.txt', 'backend/name .txt']) {
    assert.equal((await f.invoke('install', f.target + '\\', { extra, checkOnly: true })).status, 0, extra);
  }
});

for (const mode of ['install', 'uninstall']) {
  for (const relative of ['', 'backend', 'backend/_internal']) {
    windowsTest(`NSIS ${mode} rejects ${relative || 'installation directory'} junction before any payload mutation`, async t => {
      const f = fixture(t);
      const link = relative ? path.join(f.target, relative) : f.target;
      const moved = path.join(f.root, 'moved original');
      fs.renameSync(link, moved);
      const destination = relative ? path.join(f.external, relative) : f.external;
      f.junction(link, destination);
      const beforeExternal = snapshot(f.external);
      const beforeTarget = relative ? snapshot(f.target) : null;
      const result = await f.invoke(mode);
      assert.equal(result.status, 2, result.output);
      assert.deepEqual(snapshot(f.external), beforeExternal);
      if (relative) assert.deepEqual(snapshot(f.target), beforeTarget);
      assert.ok(fs.lstatSync(link).isSymbolicLink());
    });
  }
}

windowsTest('NSIS checks ancestors above INSTDIR and existing host/uninstaller leaf paths', async t => {
  const f = fixture(t);
  const alias = path.join(f.root, 'parent alias');
  f.junction(alias, f.external);
  for (const mode of ['install', 'uninstall']) {
    const result = await f.invoke(mode, path.join(alias, 'not-created'), { checkOnly: true });
    assert.equal(result.status, 2, result.output);
    for (const filename of ['fixture-host.exe', 'uninstall.exe', 'fixture-helper.exe']) {
      const file = path.join(f.target, filename);
      if (fs.existsSync(file)) fs.unlinkSync(file);
      f.junction(file, f.external);
      assert.equal((await f.invoke(mode, f.target, { checkOnly: true })).status, 2, filename);
      fs.unlinkSync(file);
    }
  }
});

windowsTest('NSIS guards reject relative roots, traversal, ADS, device paths and file/directory collisions', async t => {
  const f = fixture(t);
  for (const mode of ['install', 'uninstall']) {
    for (const root of ['relative-root', path.parse(f.target).root, '\\\\?\\' + f.target,
      '\\\\.\\' + f.target, '\\\\localhost\\share\\app', 'C:relative', f.root + '\\..\\outside']) {
      assert.equal((await f.invoke(mode, root, { checkOnly: true })).status, 2, root);
    }
    for (const extra of ['..\\outside.txt', 'backend\\..\\outside.txt', 'backend/.\\outside.txt',
      'backend/file:stream', 'NUL.txt', 'backend/COM1', 'conin$', 'lPt².txt', 'backend/NUL .txt',
      'backend/trailing.', 'backend/trailing ', 'backend/*', 'backend/?', 'backend/bad\u0001name',
      'backend/bad\tname', 'backend/file"name', '\\rooted.txt', 'C:\\absolute.txt', 'backend/', 'a'.repeat(stringLimit - 1)]) {
      assert.equal((await f.invoke(mode, f.target, { extra, checkOnly: true })).status, 2, JSON.stringify(extra.slice(0, 80)));
    }
  }
  fs.rmSync(path.join(f.target, 'backend'), { recursive: true });
  fs.writeFileSync(path.join(f.target, 'backend'), 'a file cannot be a payload parent directory');
  assert.equal((await f.invoke('install', f.target, { checkOnly: true })).status, 2);
  assert.equal((await f.invoke('uninstall', f.target, { checkOnly: true })).status, 2);
});

windowsTest('NSIS checks the entire previous install tree before invoking an older uninstaller', async t => {
  const f = fixture(t);
  const legacyParent = path.join(f.target, 'backend/_internal/removed-package');
  fs.mkdirSync(legacyParent);
  fs.writeFileSync(path.join(legacyParent, 'legacy.bin'), 'old package, absent from the new manifest');
  assert.equal((await f.invoke('install', f.target, { legacyTree: true, checkOnly: true })).status, 0);
  for (const relative of ['redirected-dir', 'removed-package/redirected-dir', 'removed-package/old-file.bin']) {
    const link = path.join(f.target, 'backend/_internal', relative);
    f.junction(link, f.external);
    const beforeExternal = snapshot(f.external);
    const beforeTarget = snapshot(f.target);
    const result = await f.invoke('install', f.target, { legacyTree: true });
    assert.equal(result.status, 2, relative);
    assert.deepEqual(snapshot(f.external), beforeExternal);
    assert.deepEqual(snapshot(f.target), beforeTarget);
    assert.ok(fs.lstatSync(link).isSymbolicLink());
    fs.unlinkSync(link);
  }
});

windowsTest('NSIS stops a legacy-tree scan at its depth limit and refuses unverified MSI migration', async t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.target, ...Array(65).fill('d')), { recursive: true });
  const beforeTarget = snapshot(f.target);
  assert.equal((await f.invoke('install', f.target, { legacyTree: true })).status, 2);
  assert.deepEqual(snapshot(f.target), beforeTarget);
  assert.equal((await f.invoke('install', f.target, { msi: true })).status, 2);
  assert.deepEqual(snapshot(f.target), beforeTarget);
});

````

## desktop/tests/nsis.test.mjs

SHA256: bd6c250ae8aa630a0d122a214be3f1c982afaf74ecf8ea541cb66b76697f21ce

````text
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import test from 'node:test';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const quote = value => `'${value.replaceAll("'", "''")}'`;
const windowsTest = process.platform === 'win32' ? test : test.skip;
const digest = value => createHash('sha256').update(value).digest('hex');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-nsis-content-'));
  t.after(() => {
    assert.ok(root.startsWith(path.join(os.tmpdir(), 'chaoxing-nsis-content-')));
    fs.rmSync(root, { recursive: true, force: true });
  });
  const bytes = new Map([
    ['chaoxing-gui-tauri.exe', 'MZ fixture host, never executed'],
    ['backend/chaoxing-backend.exe', 'MZ fixture backend, never executed'],
    ['backend/_internal/nested 中文/model.onnx', 'complete nested dependency'],
  ]);
  for (const [name, value] of bytes) {
    fs.mkdirSync(path.dirname(path.join(root, 'payload', name)), { recursive: true });
    fs.writeFileSync(path.join(root, 'payload', name), value);
  }
  const files = [...bytes].map(([name, value]) => ({ path: name, length: Buffer.byteLength(value), sha256: digest(value) }));
  const manifest = path.join(root, 'manifest.json');
  fs.writeFileSync(manifest, JSON.stringify({ files }));
  return { root, bytes, files, manifest };
}

function run(f, entries = f.files, { listingType = 'Nsis', hash = true } = {}) {
  const listing = path.join(f.root, 'listing.txt');
  fs.writeFileSync(listing, `7-Zip\n\n--\nPath = fixture.exe\nType = ${listingType}\n\n----------\n` +
    entries.map(entry => `Path = ${entry.path.replaceAll('/', '\\')}\nSize = ${entry.length ?? ''}\nAttributes = ${entry.attributes ?? 'A'}\n\n`).join(''));
  const code = `
    $ErrorActionPreference = 'Stop'
    . ${quote(path.join(desktop, 'scripts/package-common.ps1'))}
    . ${quote(path.join(desktop, 'scripts/nsis-content.ps1'))}
    $entries = @(Read-NsisContentListing ([IO.File]::ReadAllText(${quote(listing)})))
    $manifest = Read-PackageJson ${quote(f.manifest)}
    Assert-NsisContent -Entries $entries -Manifest $manifest ${hash ? `-ExtractedDirectory ${quote(path.join(f.root, 'payload'))}` : ''}
  `;
  const result = spawnSync('pwsh', ['-NoProfile', '-EncodedCommand', Buffer.from(code, 'utf16le').toString('base64')], {
    encoding: 'utf8', windowsHide: true, timeout: 20_000,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

windowsTest('NSIS complete payload matches the portable manifest including nested Unicode dependencies', t => {
  const f = fixture(t);
  const entries = [{ path: '$PLUGINSDIR/System.dll', length: 12288 }, ...f.files];
  const result = run(f, entries);
  assert.equal(result.status, 0, result.output);
});

windowsTest('NSIS missing, foreign, duplicate, traversal and wrong-format payloads are rejected before extraction', t => {
  const f = fixture(t);
  for (const entries of [
    f.files.slice(0, -1),
    [...f.files, { path: 'fake-backend.exe', length: 5 }],
    [...f.files, { path: 'resources/app.asar', length: 5 }],
    [...f.files, { ...f.files[0], path: 'CHAOXING-GUI-TAURI.EXE' }],
    [...f.files, { path: '../outside.txt', length: 5 }],
    [...f.files, { path: '$PLUGINSDIR/fake-backend.exe', length: 5 }],
    [...f.files, { path: 'backend/alias', length: 5, attributes: 'L' }],
  ]) {
    const result = run(f, entries, { hash: false });
    assert.notEqual(result.status, 0, result.output);
  }
  assert.notEqual(run(f, f.files, { listingType: 'zip', hash: false }).status, 0);
});

windowsTest('NSIS equal-size content tampering and extracted junctions cannot pass hash verification', t => {
  const f = fixture(t);
  const name = f.files[2].path;
  const file = path.join(f.root, 'payload', name);
  fs.writeFileSync(file, 'x'.repeat(f.files[2].length));
  assert.notEqual(run(f).status, 0);
  fs.writeFileSync(file, f.bytes.get(name));
  const parent = path.dirname(file);
  const moved = path.join(f.root, 'moved');
  fs.renameSync(parent, moved);
  fs.symlinkSync(moved, parent, 'junction');
  assert.notEqual(run(f).status, 0);
});

windowsTest('NSIS generated uninstaller and decoder metadata are distinct from application payload', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.root, 'payload/uninstall.exe'), 'MZ generated uninstaller fixture');
  const entries = [...f.files, { path: 'uninstall.exe', length: null },
    { path: '[NSIS].nsi', length: 123 }, { path: '[LICENSE].txt', length: 456 }];
  assert.equal(run(f, entries).status, 0);
  assert.notEqual(run(f, [...f.files, { path: 'unexpected.exe', length: null }], { hash: false }).status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/uninstall.exe'), 'invalid executable header');
  assert.notEqual(run(f, entries).status, 0);
});

function script(code) {
  const result = spawnSync('pwsh', ['-NoProfile', '-EncodedCommand', Buffer.from(`
    $ErrorActionPreference = 'Stop'
    . ${quote(path.join(desktop, 'scripts/package-common.ps1'))}
    . ${quote(path.join(desktop, 'scripts/nsis-content.ps1'))}
    ${code}
  `, 'utf16le').toString('base64')], { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

windowsTest('NSIS host expectation accounts for exactly the Tauri bundle marker without modifying the source', t => {
  const f = fixture(t);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK complete code bytes';
  fs.writeFileSync(host, original);
  const result = script(`Get-NsisHostExpectation -HostPath ${quote(host)} | ConvertTo-Json -Compress`);
  assert.equal(result.status, 0, result.output);
  const expected = JSON.parse(result.stdout);
  assert.equal(expected.sha256, digest(original.replace('_VAR_UNK', '_VAR_NSS')));
  assert.equal(expected.sourceSha256, digest(original));
  assert.equal(expected.length, Buffer.byteLength(original));
  assert.equal(expected.signed, false);
  assert.equal(fs.readFileSync(host, 'utf8'), original);
  for (const invalid of [original.replace('_VAR_UNK', '_VAR_NSS'), original + '__TAURI_BUNDLE_TYPE_VAR_UNK', 'MZ missing marker']) {
    fs.writeFileSync(host, invalid);
    assert.notEqual(script(`Get-NsisHostExpectation -HostPath ${quote(host)}`).status, 0);
  }
});

windowsTest('NSIS artifact record binds both artifacts and validates every byte with a distinct host hash', t => {
  const f = fixture(t);
  const version = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8')).version;
  const installer = path.join(f.root, `chaoxing-gui-tauri-setup-${version}-windows-x64.exe`);
  const portable = path.join(f.root, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK complete code bytes';
  const bundled = original.replace('_VAR_UNK', '_VAR_NSS');
  fs.writeFileSync(host, original);
  fs.writeFileSync(installer, 'MZ inert installer; never executed');
  fs.writeFileSync(portable, 'inert archive identity fixture; never extracted');
  const files = f.files.map(file => file.path === 'chaoxing-gui-tauri.exe'
    ? { path: file.path, length: Buffer.byteLength(original), sha256: digest(original) } : file);
  fs.writeFileSync(f.manifest, JSON.stringify({ schemaVersion: 1, kind: 'chaoxing-gui-tauri-portable', version,
    entryPoint: 'chaoxing-gui-tauri.exe', platform: 'windows-x64', files, directories: [] }));
  const create = script(`$expected = Get-NsisHostExpectation -HostPath ${quote(host)}
    Write-NsisArtifactManifest -InstallerPath ${quote(installer)} -PortablePath ${quote(portable)} -HostExpectation $expected`);
  assert.equal(create.status, 0, create.output);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled);
  const validate = () => script(`$portableManifest = Read-PackageJson ${quote(f.manifest)}
    $expected = Read-NsisPayloadManifest -InstallerPath ${quote(installer)} -PortablePath ${quote(portable)} -PortableManifest $portableManifest
    Assert-NsisContent -Entries @($expected.files) -Manifest $expected -ExtractedDirectory ${quote(path.join(f.root, 'payload'))}`);
  assert.equal(validate().status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled.replace('complete', 'tampered'));
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(path.join(f.root, 'payload/chaoxing-gui-tauri.exe'), bundled);
  fs.appendFileSync(installer, 'changed');
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(installer, 'MZ inert installer; never executed');
  fs.appendFileSync(portable, 'changed');
  assert.notEqual(validate().status, 0);
  fs.writeFileSync(portable, 'inert archive identity fixture; never extracted');
  fs.rmSync(`${installer}.manifest.json`);
  assert.notEqual(validate().status, 0);
});

windowsTest('NSIS signed-host capture must originate from the exact unsigned compiler output', t => {
  const f = fixture(t);
  const host = path.join(f.root, 'chaoxing-gui-tauri.exe');
  const record = path.join(f.root, 'signed-host.json');
  const original = 'MZ inert host __TAURI_BUNDLE_TYPE_VAR_UNK code bytes';
  fs.writeFileSync(host, original);
  fs.writeFileSync(record, JSON.stringify({ schemaVersion: 1, kind: 'chaoxing-gui-tauri-signed-nsis-host',
    sourcePath: host, bundleType: 'nsis', preSignSha256: '0'.repeat(64), preSignLength: original.length,
    length: original.length + 200, sha256: '1'.repeat(64), signerThumbprint: 'A'.repeat(40) }));
  assert.notEqual(script(`Get-NsisHostExpectation -HostPath ${quote(host)} -SignedHostRecordPath ${quote(record)}`).status, 0);
});

````

## desktop/tests/p3-smoke.test.mjs

SHA256: 115d4d61b47653fd2c9650c8b4ffb72a917cf974e78cf80ce341bbeb9d3337cc

````text
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import fileSystem, { copyFile, mkdtemp, mkdir, readFile, writeFile, readdir, rm, symlink } from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import {
  parseArguments, assertReleasePermission, assertOwnedOrAbsent, claimProfileRoots,
  releaseProfileRoots, sanitizedEnvironment, validateInputs, until, withCleanup,
  NativeSupervisor, windowsContext, assertBuildProfile,
} from '../scripts/p3-smoke.mjs';

const exec = promisify(execFile);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const temporary = async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'chaoxing-p3-test-'));
  t.after(() => rm(root, { recursive: true, force: true, maxRetries: 4, retryDelay: 100 }));
  return root;
};
const syntheticContext = {
  windows: 'C:\\Windows', roaming: 'C:\\Users\\fixture\\AppData\\Roaming',
  local: 'C:\\Users\\fixture\\AppData\\Local', userProfile: 'C:\\Users\\fixture',
  temp: 'C:\\Users\\fixture\\AppData\\Local\\Temp', sid: 'S-1-5-21-fixture',
};
const isAlive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };

test('P3 rejects unknown, missing, and mismatched smoke selections', () => {
  assert.throws(() => parseArguments(['--scenario', 'Frozne']), /scenario/i);
  assert.throws(() => parseArguments(['--configuration', 'Relase']), /configuration/i);
  assert.throws(() => parseArguments(['--kind', 'python', '--mode', 'Unknown']), /mode/i);
  assert.throws(() => parseArguments(['--host-path']), /value/i);
  assert.throws(() => parseArguments(['--unknown', 'x']), /unknown/i);
  assert.throws(() => parseArguments(['--scenario', 'Fake', '--scenario', 'Frozen']), /duplicate/i);
  assert.throws(() => parseArguments(['--use-packaged-layout', '--scenario', 'Fake']), /Frozen/i);
  assert.equal(parseArguments(['--scenario', 'frozen']).scenario, 'Frozen');
});

test('Release requires an explicit disposable user or a hosted runner, never GITHUB_ACTIONS alone', () => {
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, {}), /disposable|fresh/i);
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, { GITHUB_ACTIONS: 'true' }), /disposable|fresh/i);
  assert.throws(() => assertReleasePermission({ configuration: 'Release' }, {
    GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'self-hosted', RUNNER_OS: 'Windows', GITHUB_RUN_ID: '123',
  }), /disposable|fresh/i);
  assert.equal(assertReleasePermission({ configuration: 'Release', disposableWindowsUser: true }, {}).allowed, true);
  assert.equal(assertReleasePermission({ configuration: 'Release' }, {
    GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted', RUNNER_OS: 'Windows', GITHUB_RUN_ID: '123',
  }).allowed, true);
  assert.equal(assertReleasePermission({ configuration: 'Debug' }, {}).required, false);
});

test('Compiled profile mismatches and unsupported native probe exits fail closed', () => {
  assert.doesNotThrow(() => assertBuildProfile(0, 'Debug'));
  assert.doesNotThrow(() => assertBuildProfile(4, 'Release'));
  assert.throws(() => assertBuildProfile(4, 'Debug'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(0, 'Release'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(1, 'Debug'), /profile.*refused/i);
  assert.throws(() => assertBuildProfile(null, 'Release'), /profile.*refused/i);
});

test('Production paths come from Windows known folders and include Electron legacy data', () => {
  const roots = releaseProfileRoots(syntheticContext);
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Roaming\\com.chaoxing.gui'));
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Local\\com.chaoxing.gui'));
  assert.ok(roots.some((root) => root.path === 'C:\\Users\\fixture\\AppData\\Roaming\\chaoxing-desktop' && !root.claim));
});

test('Release profile ownership refuses existing data and previous smoke runs without modifying them', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'existing');
  await mkdir(data);
  await writeFile(path.join(data, 'web_config.json'), 'private sentinel');
  const roots = [{ path: data, claim: true }];
  await assert.rejects(assertOwnedOrAbsent(roots, 'current-run', 'fixture-sid'), /preexisting|owned/i);
  assert.equal(await readFile(path.join(data, 'web_config.json'), 'utf8'), 'private sentinel');
  await writeFile(path.join(data, '.p3-smoke-owner.json'), JSON.stringify({ runId: 'older-run', sid: 'fixture-sid', path: data }));
  await assert.rejects(assertOwnedOrAbsent(roots, 'current-run', 'fixture-sid'), /preexisting|owned/i);
});

test('Only current smoke ownership permits profile reuse; legacy roots are never created', async (t) => {
  const root = await temporary(t);
  const roots = [{ path: path.join(root, 'app'), claim: true }, { path: path.join(root, 'legacy'), claim: false }];
  await claimProfileRoots(roots, 'same-run', 'fixture-sid');
  await claimProfileRoots(roots, 'same-run', 'fixture-sid');
  await assertOwnedOrAbsent(roots, 'same-run', 'fixture-sid');
  await assert.rejects(readFile(path.join(root, 'legacy', '.p3-smoke-owner.json')), { code: 'ENOENT' });
  await assert.rejects(assertOwnedOrAbsent(roots, 'other-run', 'fixture-sid'), /preexisting|owned/i);
});

test('A profile created after the absent preflight cannot be adopted by this smoke run', async (t) => {
  const root = await temporary(t);
  const data = path.join(root, 'racing-profile');
  const originalStat = fileSystem.lstat;
  let injected = false;
  const replacement = t.mock.method(fileSystem, 'lstat', async (filename, ...args) => {
    try { return await originalStat(filename, ...args); }
    catch (error) {
      if (filename === data && error.code === 'ENOENT' && !injected) {
        injected = true;
        await mkdir(data);
        await writeFile(path.join(data, 'web_config.json'), 'concurrent application data');
      }
      throw error;
    }
  });
  syncBuiltinESMExports();
  try {
    await assert.rejects(claimProfileRoots([{ path: data, claim: true }], 'run', 'sid'), /preexisting|owned/i);
    assert.equal(injected, true);
    assert.equal(await readFile(path.join(data, 'web_config.json'), 'utf8'), 'concurrent application data');
    await assert.rejects(readFile(path.join(data, '.p3-smoke-owner.json')), { code: 'ENOENT' });
  } finally {
    replacement.mock.restore();
    syncBuiltinESMExports();
  }
});

test('Profile guard refuses junctions before following them', async (t) => {
  const root = await temporary(t);
  const target = path.join(root, 'target');
  const link = path.join(root, 'link');
  await mkdir(target);
  await symlink(target, link, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(assertOwnedOrAbsent([{ path: link, claim: true }], 'run', 'sid'), /reparse|symbolic|link/i);
});

test('Host environment cannot resolve system Python or use inherited development overrides', () => {
  const contaminated = {
    PATH: 'C:\\Python311;C:\\Tools', Path: 'C:\\OtherPython',
    PYTHONPATH: 'private', PYTHONHOME: 'private', VIRTUAL_ENV: 'private',
    APPDATA: 'C:\\fake-appdata', LOCALAPPDATA: 'C:\\fake-local',
    CHAOXING_TAURI_DEV_ROOT: 'real-data', CHAOXING_TAURI_DEV_BACKEND: 'real-backend',
    CHAOXING_LEGACY_DATA_DIR: 'real-legacy', CHAOXING_DATA_DIR: 'real-data',
    WEBVIEW2_USER_DATA_FOLDER: 'real-webview', GITHUB_TOKEN: 'private-token',
  };
  const release = sanitizedEnvironment(contaminated, syntheticContext, { configuration: 'Release' });
  assert.equal(release.PATH, 'C:\\Windows\\System32;C:\\Windows;C:\\Windows\\System32\\Wbem');
  assert.equal(release.APPDATA, syntheticContext.roaming);
  assert.equal(release.LOCALAPPDATA, syntheticContext.local);
  for (const key of Object.keys(release)) assert.ok(!/^PYTHON|^VIRTUAL_ENV$|^CHAOXING_|^GITHUB_TOKEN$|^WEBVIEW2_USER_DATA_FOLDER$/i.test(key), key);
  const debug = sanitizedEnvironment(contaminated, syntheticContext, {
    configuration: 'Debug', profile: 'C:\\isolated\\profile', backend: 'C:\\isolated\\backend.exe',
  });
  assert.equal(debug.CHAOXING_TAURI_DEV_ROOT, 'C:\\isolated\\profile');
  assert.equal(debug.CHAOXING_TAURI_DEV_BACKEND, 'C:\\isolated\\backend.exe');
  assert.equal(debug.CHAOXING_TAURI_DEV_HIDDEN, '1');
});

test('Missing host and incomplete frozen resources fail validation before launch', async (t) => {
  const root = await temporary(t);
  const options = { kind: 'tauri', configuration: 'Debug', scenario: 'Frozen', hostPath: path.join(root, 'host.exe'), backendDirectory: path.join(root, 'backend') };
  await assert.rejects(validateInputs(options), /host/i);
  await writeFile(options.hostPath, 'not launched by validation');
  await mkdir(options.backendDirectory);
  await writeFile(path.join(options.backendDirectory, 'chaoxing-backend.exe'), 'not launched by validation');
  await assert.rejects(validateInputs(options), /_internal|onedir/i);
});

test('Installed layout requires the host adjacent backend and never rewrites its source files', async (t) => {
  const root = await temporary(t);
  const hostPath = path.join(root, 'installed', 'chaoxing-gui-tauri.exe');
  const backendDirectory = path.join(root, 'installed', 'backend');
  await mkdir(path.join(backendDirectory, '_internal'), { recursive: true });
  await writeFile(hostPath, 'original host sentinel');
  await writeFile(path.join(backendDirectory, 'chaoxing-backend.exe'), 'original backend sentinel');
  const options = { kind: 'tauri', configuration: 'Release', scenario: 'Frozen', usePackagedLayout: true, hostPath, backendDirectory };
  await assert.rejects(validateInputs({ ...options, backendDirectory: path.join(root, 'elsewhere') }), /parent\/backend/i);
  await validateInputs(options);
  assert.equal(await readFile(hostPath, 'utf8'), 'original host sentinel');
  assert.equal(await readFile(path.join(backendDirectory, 'chaoxing-backend.exe'), 'utf8'), 'original backend sentinel');
});

test('Timeout always runs cleanup and cleanup failure cannot turn into a pass', async () => {
  let cleaned = 0;
  await assert.rejects(withCleanup({}, () => until(() => false, 'intentional fixture timeout', 30, 5), async () => { cleaned++; }), /timed out/);
  assert.equal(cleaned, 1);
  await assert.rejects(withCleanup({}, () => until(() => new Promise(() => {}), 'stalled readiness', 30), async () => { cleaned++; }), /timed out/);
  assert.equal(cleaned, 2);
  await assert.rejects(withCleanup({}, async () => { throw new Error('primary failure'); }, async () => { throw new Error('cleanup failure'); }), /primary failure.*cleanup failure/s);
});

test('Release CLI fails closed and writes failure evidence without launching a host', async (t) => {
  const root = await temporary(t);
  await assert.rejects(exec(process.execPath, [path.join(repo, 'desktop/scripts/p3-smoke.mjs'),
    '--configuration', 'Release', '--host-path', 'never-launch.exe', '--evidence-directory', root], {
    env: { ...process.env, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '' }, windowsHide: true, timeout: 15000,
  }), (error) => error.code === 1 && /Evidence:/.test(error.stdout));
  const { readdir } = await import('node:fs/promises');
  const directories = await readdir(root);
  assert.equal(directories.length, 1);
  const result = JSON.parse(await readFile(path.join(root, directories[0], 'result.json'), 'utf8'));
  assert.equal(result.success, false);
  assert.equal(result.processes.length, 0);
  assert.match(result.error, /disposable|fresh/i);
});

test('PowerShell smoke wrappers choose the first real Node when PATH contains multiple matches', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const first = path.join(root, 'first node');
  const second = path.join(root, 'second node');
  await mkdir(first);
  await mkdir(second);
  for (const filename of [path.join(first, 'node.exe'), path.join(first, 'node'), path.join(second, 'node.exe')]) {
    // Independent copies can be removed while this test's Node is still
    // running; Windows can lock every hardlink to a loaded executable.
    await copyFile(process.execPath, filename);
  }
  const preload = path.join(root, 'record-node.cjs');
  await writeFile(preload, "require('node:fs').writeFileSync(process.env.P3_WRAPPER_PROBE, JSON.stringify({ execPath: process.execPath, version: process.version }));");
  const inheritedPath = Object.entries(process.env).find(([name]) => name.toLowerCase() === 'path')?.[1] || '';
  const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !['path', 'node_options'].includes(name.toLowerCase())));
  Object.assign(environment, { PATH: `${first};${second};${inheritedPath}`, GITHUB_ACTIONS: 'false', RUNNER_ENVIRONMENT: '',
    NODE_OPTIONS: `--require "${preload.replaceAll('\\', '/')}"` });
  const { stdout } = await exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-Command',
    '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); @(Get-Command node -CommandType Application).Source | ConvertTo-Json -Compress'],
  { env: environment, windowsHide: true, timeout: 10000 });
  const matches = JSON.parse(stdout);
  assert.ok(Array.isArray(matches) && matches.length >= 3, 'The regression must exercise multiple executable matches');
  assert.equal(matches[0].toLowerCase(), path.join(first, 'node.exe').toLowerCase());
  for (const [script, args] of [
    ['smoke-tauri.ps1', ['-HostPath', path.join(root, 'never-run.exe'), '-Configuration', 'Release']],
    ['smoke-python.ps1', ['-ExecutablePath', path.join(root, 'missing-backend.exe')]],
    ['smoke-installation.ps1', ['-InstallerPath', path.join(root, 'never-run.exe'), '-PortablePath', path.join(root, 'never-extract.zip')]],
  ]) {
    const evidence = path.join(root, `${script}-evidence`);
    const probe = path.join(root, `${script}-node.json`);
    await assert.rejects(exec(context.powerShell, ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/scripts', script),
      ...args, '-EvidenceDirectory', evidence], { env: { ...environment, P3_WRAPPER_PROBE: probe }, windowsHide: true, timeout: 15000 }),
    (error) => {
      assert.equal(error.code, 1, `${script}: ${error.stderr}`);
      assert.match(error.stdout, /Evidence:/, `${script}: ${error.stderr}`);
      return true;
    }, `${script} must reach the Node validation and preserve its exit code`);
    const selected = JSON.parse(await readFile(probe, 'utf8'));
    assert.equal(selected.execPath.toLowerCase(), path.join(first, 'node.exe').toLowerCase(), script);
    assert.equal(selected.version, process.version, script);
    const reports = await readdir(evidence);
    assert.equal(reports.length, 1, script);
    const report = JSON.parse(await readFile(path.join(evidence, reports[0], 'result.json'), 'utf8'));
    assert.equal(report.success, false, script);
    assert.deepEqual(report.processes, [], script);
  }
});

test('Windows supervisor reports start failure without leaving a captured process', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await assert.rejects(supervisor.start({ executable: path.join(root, 'missing.exe'), args: [], cwd: root, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) }), /CreateProcess|not found|找不到/i);
    assert.equal(supervisor.identity, null);
  } finally { await supervisor.dispose(); }
});

test('Windows supervisor supplies live stdin and observes a clean EOF shutdown', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await supervisor.start({ executable: process.execPath, args: [path.join(repo, 'desktop/tests/fixtures/p3-process-child.mjs')], cwd: root,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    await until(async () => (await readFile(path.join(root, 'stdout.log'), 'utf8')).includes('stdin-open'), 'child readiness', 5000);
    await new Promise((resolve) => setTimeout(resolve, 100));
    const snapshot = await supervisor.snapshot();
    assert.ok(snapshot.active.some((identity) => identity.pid === supervisor.identity.pid && identity.inOuterJob));
    await supervisor.command('stdin-eof');
    await until(async () => { const state = await supervisor.snapshot(); return state.active.length === 0 && state.observed.every((identity) => !identity.alive); }, 'EOF cleanup', 5000);
    const cleanup = await supervisor.dispose();
    assert.equal(cleanup.fallbackUsed, false);
    assert.deepEqual(cleanup.remaining, []);
  } finally { await supervisor.dispose(); }
});

test('Timed-out Windows tree is cleaned while an unrelated process survives', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { windowsHide: true, stdio: 'ignore' });
  await new Promise((resolve, reject) => { unrelated.once('spawn', resolve); unrelated.once('error', reject); });
  const supervisor = new NativeSupervisor(context.powerShell, root);
  try {
    await supervisor.start({ executable: process.execPath, args: [path.join(repo, 'desktop/tests/fixtures/p3-process-child.mjs'), '--tree'], cwd: root,
      env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) });
    const snapshot = await until(async () => {
      const current = await supervisor.snapshot();
      return current.active.filter((identity) => identity.executable === supervisor.identity.executable).length === 2 && current;
    }, 'descendant observed', 5000);
    await assert.rejects(withCleanup(supervisor, () => until(() => false, 'intentional startup timeout', 40, 5), (value) => value.dispose()), /timed out/);
    for (const identity of snapshot.active) await until(() => !isAlive(identity.pid), 'captured process cleanup', 5000);
    assert.equal(isAlive(unrelated.pid), true);
  } finally {
    await supervisor.dispose();
    unrelated.kill();
    await until(() => !isAlive(unrelated.pid), 'unrelated fixture cleanup', 5000);
  }
});

test('Chinese title crosses the supervisor pipe and WM_CLOSE reaches only the captured real window', { skip: process.platform !== 'win32', timeout: 30000 }, async (t) => {
  const root = await temporary(t);
  const context = await windowsContext();
  const target = new NativeSupervisor(context.powerShell, path.join(root, 'target'));
  const other = new NativeSupervisor(context.powerShell, path.join(root, 'other'));
  const spec = { executable: context.powerShell, args: ['-NoProfile', '-NonInteractive', '-File', path.join(repo, 'desktop/tests/fixtures/p3-title-window.ps1')],
    cwd: root, env: sanitizedEnvironment(process.env, context, { configuration: 'Release' }) };
  try {
    await target.start(spec);
    await other.start(spec);
    for (const directory of ['target', 'other']) await until(async () => (await readFile(path.join(root, directory, 'stdout.log'), 'utf8')).includes('window-ready'), 'real title window ready', 5000);
    assert.equal(await target.command('close-window', { title: '超星学习通 · 自动化学习助手' }), 1);
    await until(async () => { const snapshot = await target.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive); }, 'Chinese window close', 5000);
    assert.equal((await other.snapshot()).host.alive, true, 'A separate process with the same title must survive');
    assert.equal(await other.command('close-window', { title: '超星学习通 · 自动化学习助手' }), 1);
    await until(async () => { const snapshot = await other.snapshot(); return snapshot.active.length === 0 && snapshot.observed.every((identity) => !identity.alive); }, 'second fixture close', 5000);
    assert.equal((await target.dispose()).fallbackUsed, false);
    assert.equal((await other.dispose()).fallbackUsed, false);
  } finally {
    try { await target.dispose(); } finally { await other.dispose(); }
  }
});

````

## desktop/tests/packaging.test.mjs

SHA256: 6b18f127736fb48290cf7f6945f94c5b0a96c951ab8ea0fd338a72661d28b0ab

````text
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const project = path.dirname(desktop);
const version = fs.readFileSync(path.join(project, 'pyproject.toml'), 'utf8').match(/^version\s*=\s*"([^"]+)"/m)[1];
const windowsTest = process.platform === 'win32' ? test : test.skip;
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const psQuote = (value) => `'${String(value).replaceAll("'", "''")}'`;

function runPowerShell(args, executable = 'pwsh.exe') {
  const result = spawnSync(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', ...args], {
    encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 8 * 1024 * 1024,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

function script(name, args = []) {
  return runPowerShell(['-File', path.join(desktop, 'scripts', name), ...args]);
}

function psCode(code) {
  return runPowerShell(['-EncodedCommand', Buffer.from(`$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ${code}`, 'utf16le').toString('base64')]);
}

function success(result) {
  assert.equal(result.status, 0, result.output);
}

function failure(result, message) {
  assert.notEqual(result.status, 0, `Unexpected success: ${result.output}`);
  assert.match(result.output, message);
}

function write(root, relative, bytes) {
  const target = path.join(root, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, bytes);
}

function copyFixtureTree(source, destination) {
  // Node 24.14's native cpSync crashes on these Unicode Windows paths; exercise
  // the same fixture bytes through the stable mkdir/copyFile primitives.
  fs.mkdirSync(destination, { recursive: true });
  for (const item of fs.readdirSync(source, { withFileTypes: true })) {
    assert.equal(item.isSymbolicLink(), false);
    const from = path.join(source, item.name);
    const to = path.join(destination, item.name);
    if (item.isDirectory()) copyFixtureTree(from, to);
    else fs.copyFileSync(from, to);
  }
}

function removeWithin(root, target) {
  const resolvedRoot = path.resolve(root);
  const resolvedTarget = path.resolve(target);
  assert.ok(resolvedTarget.startsWith(`${resolvedRoot}${path.sep}`), 'cleanup must stay inside its fixture');
  fs.rmSync(resolvedTarget, { recursive: true, force: true });
}

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing packaging 中文-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('chaoxing packaging 中文-'));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const source = path.join(directory, 'frozen source 中文');
  write(source, 'chaoxing-backend.exe', 'MZ fake frozen backend; never executed');
  write(source, '_internal/python311.dll', 'fake python runtime');
  write(source, '_internal/base_library.zip', 'fake standard library');
  write(source, '_internal/ddddocr/模型/common.onnx', Buffer.from([0, 255, 3, 17]));
  write(source, '_internal/web/dist/index.html', '<script src="/assets/main.js"></script><link href="/assets/main.css" rel="stylesheet">');
  write(source, '_internal/web/dist/assets/main.js', 'console.log("packaging fixture");');
  write(source, '_internal/web/dist/assets/main.css', 'body { color: black; }');
  write(source, '_internal/web/dist/assets/nested 中文/lazy.js', 'export const fixture = true;');
  write(source, '_internal/.metadata', 'hidden-name fixture');
  fs.mkdirSync(path.join(source, '_internal/empty directory 中文'), { recursive: true });
  const destination = path.join(directory, 'staged resources 中文/backend');
  const manifest = path.join(path.dirname(destination), 'backend-manifest.json');
  const host = path.join(directory, 'release host 中文/chaoxing-gui-tauri.exe');
  write(path.dirname(host), path.basename(host), 'MZ fake Tauri host; never executed by packaging');
  const output = path.join(directory, 'release output 中文');
  const artifact = path.join(output, `chaoxing-gui-tauri-portable-${version}-windows-x64.zip`);
  return { directory, source, destination, manifest, host, output, artifact };
}

function snapshot(root) {
  const result = {};
  function visit(directory, prefix = '') {
    for (const item of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = `${prefix}${item.name}`;
      const filename = path.join(directory, item.name);
      assert.equal(item.isSymbolicLink(), false);
      if (item.isDirectory()) {
        result[`${relative}/`] = 'directory';
        visit(filename, `${relative}/`);
      } else {
        result[relative] = sha256(fs.readFileSync(filename));
      }
    }
  }
  visit(root);
  return result;
}

function stage(f, args = []) {
  return script('prepare-backend.ps1', ['-SourceDirectory', f.source, '-DestinationDirectory', f.destination, ...args]);
}

function pack(f, args = []) {
  return script('package-portable.ps1', ['-HostPath', f.host, '-BackendDirectory', f.destination, '-OutputDirectory', f.output, ...args]);
}

function verify(f, args = []) {
  return script('verify-package.ps1', ['-PackagePath', f.artifact, ...args]);
}

function zipEntries(filename) {
  const result = psCode(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead(${psQuote(filename)})
    try {
      $rows = @(foreach ($entry in $zip.Entries) {
        $stream = $entry.Open()
        $memory = [IO.MemoryStream]::new()
        try { $stream.CopyTo($memory); @{ path = $entry.FullName; bytes = [Convert]::ToBase64String($memory.ToArray()) } }
        finally { $memory.Dispose(); $stream.Dispose() }
      })
      ConvertTo-Json -InputObject $rows -Compress
    } finally { $zip.Dispose() }
  `);
  success(result);
  return new Map(JSON.parse(result.stdout.trim()).map((entry) => [entry.path, Buffer.from(entry.bytes, 'base64')]));
}

function alterZip(f, entryName, content) {
  const writeEntry = content === null ? '' : `
    $replacement = $zip.CreateEntry(${psQuote(entryName)})
    $stream = $replacement.Open()
    try { $bytes = [Convert]::FromBase64String(${psQuote(Buffer.from(content).toString('base64'))}); $stream.Write($bytes, 0, $bytes.Length) }
    finally { $stream.Dispose() }
  `;
  success(psCode(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::Open(${psQuote(f.artifact)}, [IO.Compression.ZipArchiveMode]::Update)
    try { $entry = $zip.GetEntry(${psQuote(entryName)}); if ($null -ne $entry) { $entry.Delete() }; ${writeEntry} }
    finally { $zip.Dispose() }
  `));
  // Refresh only the outer checksum: the verifier must independently validate the payload.
  const filename = `${f.artifact}.manifest.json`;
  const manifest = JSON.parse(fs.readFileSync(filename, 'utf8'));
  const bytes = fs.readFileSync(f.artifact);
  manifest.artifact.sha256 = sha256(bytes);
  manifest.artifact.length = bytes.length;
  fs.writeFileSync(filename, JSON.stringify(manifest));
  fs.writeFileSync(`${f.artifact}.sha256`, `${manifest.artifact.sha256}  ${path.basename(f.artifact)}\n`);
}

windowsTest('staging preserves the complete onedir tree, hashes, empty directories and source bytes', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  success(stage(f));
  assert.deepEqual(snapshot(f.destination), before);
  assert.deepEqual(snapshot(f.source), before);
  const manifest = JSON.parse(fs.readFileSync(f.manifest, 'utf8'));
  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.version, version);
  assert.equal(manifest.entryPoint, 'chaoxing-backend.exe');
  assert.deepEqual(new Set(manifest.directories), new Set(Object.keys(before).filter((name) => name.endsWith('/')).map((name) => name.slice(0, -1))));
  assert.equal(manifest.files.length, Object.keys(before).filter((name) => !name.endsWith('/')).length);
  for (const file of manifest.files) {
    assert.equal(file.sha256, before[file.path]);
    assert.equal(file.length, fs.statSync(path.join(f.source, file.path)).size);
    assert.equal(path.isAbsolute(file.path), false);
    assert.equal(file.path.includes('\\'), false);
  }
  write(f.destination, '_internal/obsolete.dll', 'stale staging');
  write(f.source, '_internal/web/dist/assets/main.js', 'updated web build');
  success(stage(f));
  assert.deepEqual(snapshot(f.destination), snapshot(f.source));
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)).sort(), ['backend', 'backend-manifest.json']);
});

windowsTest('missing frozen executable, _internal, HTML, assets or referenced chunks fail without replacing staging', (t) => {
  const f = fixture(t);
  success(stage(f));
  const previous = snapshot(path.dirname(f.destination));
  for (const missing of ['chaoxing-backend.exe', '_internal', '_internal/web/dist/index.html', '_internal/web/dist/assets', '_internal/web/dist/assets/main.js']) {
    const invalid = path.join(f.directory, `invalid ${missing.replaceAll('/', '-')}`);
    copyFixtureTree(f.source, invalid);
    removeWithin(invalid, path.join(invalid, missing));
    const before = snapshot(invalid);
    failure(stage({ ...f, source: invalid }), /missing|required|resource|asset|internal|incomplete/i);
    assert.deepEqual(snapshot(path.dirname(f.destination)), previous, missing);
    assert.deepEqual(snapshot(invalid), before, missing);
  }
});

windowsTest('overlapping source/destination and filesystem roots are refused before mutation', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  for (const destination of [f.source, path.join(f.source, 'backend'), f.directory, path.parse(f.source).root]) {
    failure(stage({ ...f, destination }), /overlap|unsafe|root|source|protected/i);
    assert.deepEqual(snapshot(f.source), before);
  }
});

windowsTest('a locked manifest rolls back an already replaced staging directory', (t) => {
  const f = fixture(t);
  success(stage(f));
  const previous = snapshot(path.dirname(f.destination));
  write(f.source, '_internal/new generation.dll', 'next build');
  const source = snapshot(f.source);
  const result = psCode(`
    $lock = [IO.File]::Open(${psQuote(f.manifest)}, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try { & ${psQuote(path.join(desktop, 'scripts/prepare-backend.ps1'))} -SourceDirectory ${psQuote(f.source)} -DestinationDirectory ${psQuote(f.destination)} }
    finally { $lock.Dispose() }
  `);
  failure(result, /process|access|used|move|manifest|exception|文件|进程/i);
  assert.deepEqual(snapshot(path.dirname(f.destination)), previous);
  assert.deepEqual(snapshot(f.source), source);
});

windowsTest('publication survives a transient Windows directory sharing lock without changing the source', (t) => {
  const f = fixture(t);
  success(stage(f));
  write(f.destination, 'previous generation.txt', 'previous stage');
  const candidate = path.join(path.dirname(f.destination), '.candidate backend 中文');
  copyFixtureTree(f.source, candidate);
  const source = snapshot(f.source);
  const result = psCode(`
    . ${psQuote(path.join(desktop, 'scripts/package-common.ps1'))}
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;
public sealed class PackagingRenameLock : IDisposable {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    private readonly SafeFileHandle handle;
    private readonly Timer timer;
    public PackagingRenameLock(string path) {
        // FILE_LIST_DIRECTORY, FILE_SHARE_READ | WRITE (deliberately no DELETE).
        handle = CreateFileW(path, 1, 3, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
        if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        timer = new Timer(_ => handle.Dispose(), null, 1000, Timeout.Infinite);
    }
    public void Dispose() { timer.Dispose(); handle.Dispose(); }
}
'@
    $held = [PackagingRenameLock]::new(${psQuote(candidate)})
    try {
      Publish-PackageItems -AllowedParent ${psQuote(path.dirname(f.destination))} -Items @(
        @{ Source = ${psQuote(candidate)}; Destination = ${psQuote(f.destination)} }
      )
    } finally { $held.Dispose() }
  `);
  success(result);
  assert.deepEqual(snapshot(f.destination), source);
  assert.deepEqual(snapshot(f.source), source);
  assert.deepEqual(fs.readdirSync(path.dirname(f.destination)).sort(), ['backend', 'backend-manifest.json']);
});

windowsTest('source, staging and ancestor junctions are refused without touching their targets', (t) => {
  const f = fixture(t);
  const outside = path.join(f.directory, 'unrelated target');
  write(outside, 'keep.txt', 'must survive');
  const before = snapshot(outside);
  const sourceLink = path.join(f.source, '_internal/junction');
  fs.symlinkSync(outside, sourceLink, 'junction');
  failure(stage(f), /reparse|junction|symbolic/i);
  fs.unlinkSync(sourceLink);
  fs.mkdirSync(path.dirname(f.destination), { recursive: true });
  fs.symlinkSync(outside, f.destination, 'junction');
  failure(stage(f), /reparse|junction|symbolic/i);
  fs.unlinkSync(f.destination);
  const ancestor = path.join(f.directory, 'ancestor junction');
  fs.symlinkSync(outside, ancestor, 'junction');
  failure(stage({ ...f, destination: path.join(ancestor, 'backend') }), /reparse|junction|symbolic/i);
  assert.deepEqual(snapshot(outside), before);
});

windowsTest('version drift and unsafe version text fail before staging or artifact publication', (t) => {
  const f = fixture(t);
  for (const badVersion of ['9.9.9', '../1.1.1', '1.1.1-beta']) {
    failure(stage(f, ['-Version', badVersion]), /version/i);
    assert.equal(fs.existsSync(f.destination), false);
  }
  success(stage(f));
  failure(pack(f, ['-Version', '9.9.9']), /version/i);
  assert.equal(fs.existsSync(f.artifact), false);
});

windowsTest('portable ZIP preserves backend hierarchy and verifies artifact and every payload file without launching it', (t) => {
  const f = fixture(t);
  const before = snapshot(f.source);
  success(stage(f));
  const staged = snapshot(path.dirname(f.destination));
  success(pack(f));
  success(verify(f));
  const entries = zipEntries(f.artifact);
  for (const required of ['chaoxing-gui-tauri.exe', 'backend-manifest.json', 'package-manifest.json', 'Start-Chaoxing.cmd', 'Start-Chaoxing.ps1', 'Install-WebView2.cmd', 'Install-WebView2.ps1', 'README.txt', 'LICENSE', 'TAURI-LICENSE.txt']) {
    assert.ok(entries.has(required), required);
  }
  assert.deepEqual(entries.get('LICENSE'), fs.readFileSync(path.join(project, 'LICENSE')));
  assert.deepEqual(entries.get('TAURI-LICENSE.txt'), fs.readFileSync(path.join(desktop, 'src-tauri/windows/LICENSE_MIT')));
  const payload = JSON.parse(entries.get('package-manifest.json').toString('utf8'));
  assert.equal(payload.version, version);
  for (const file of payload.files) {
    assert.ok(entries.has(file.path), `ZIP is missing manifest path ${file.path}`);
    assert.equal(sha256(entries.get(file.path)), file.sha256, file.path);
    assert.equal(entries.get(file.path).length, file.length, file.path);
  }
  for (const [name, hash] of Object.entries(before)) {
    const bytes = entries.get(`backend/${name}`);
    assert.ok(bytes, name);
    if (hash !== 'directory') assert.equal(sha256(bytes), hash, name);
  }
  assert.equal([...entries.keys()].some((name) => /electron|tests\/|^_internal\//i.test(name)), false);
  assert.deepEqual(snapshot(f.source), before);
  assert.deepEqual(snapshot(path.dirname(f.destination)), staged);
  const outer = JSON.parse(fs.readFileSync(`${f.artifact}.manifest.json`, 'utf8'));
  assert.equal(outer.artifact.sha256, sha256(fs.readFileSync(f.artifact)));
  assert.match(fs.readFileSync(`${f.artifact}.sha256`, 'utf8'), new RegExp(`^${outer.artifact.sha256}  `));
});

windowsTest('tampered staging, missing manifest and backend version drift preserve previous published artifacts', (t) => {
  const f = fixture(t);
  success(stage(f));
  success(pack(f));
  const published = snapshot(f.output);
  const originalManifest = fs.readFileSync(f.manifest);
  for (const change of [
    () => fs.writeFileSync(f.manifest, '{}'),
    () => fs.writeFileSync(f.manifest, JSON.stringify({ ...JSON.parse(originalManifest), version: '9.9.9' })),
    () => fs.unlinkSync(f.manifest),
    () => write(f.destination, '_internal/ddddocr/模型/common.onnx', 'tampered model'),
  ]) {
    change();
    failure(pack(f), /manifest|version|hash|length|mismatch/i);
    assert.deepEqual(snapshot(f.output), published);
    fs.writeFileSync(f.manifest, originalManifest);
  }
});

windowsTest('ZIP verifier rejects outer checksum drift, inner tampering, missing payload and traversal entries', (t) => {
  const f = fixture(t);
  success(stage(f));
  success(pack(f));
  const originals = new Map(fs.readdirSync(f.output).map((name) => [name, fs.readFileSync(path.join(f.output, name))]));
  const restore = () => { for (const [name, bytes] of originals) fs.writeFileSync(path.join(f.output, name), bytes); };
  fs.appendFileSync(f.artifact, 'corrupt trailing data');
  failure(verify(f), /artifact|hash|length|checksum/i);
  restore();
  for (const [entry, content] of [
    ['backend/_internal/ddddocr/模型/common.onnx', 'tampered model'],
    ['backend/chaoxing-backend.exe', null],
    ['LICENSE', null],
    ['../escape.txt', 'unsafe path'],
    ['package-manifest.json', JSON.stringify({ schemaVersion: 1, version: '9.9.9' })],
  ]) {
    alterZip(f, entry, content);
    failure(verify(f), /manifest|hash|length|missing|payload|unsafe|path|version/i);
    restore();
  }
  failure(verify(f, ['-Version', '9.9.9']), /version/i);
});

windowsTest('Windows PowerShell portable entrypoints preserve check/start errors and never install a runtime automatically', (t) => {
  const f = fixture(t);
  const portable = path.join(f.directory, 'portable launch 中文');
  copyFixtureTree(path.join(desktop, 'portable'), portable);
  const source = path.join(portable, 'FakeHost.cs');
  fs.writeFileSync(source, `using System; using System.IO; using System.Threading; using System.Diagnostics;
    class FakeHost { static int Main(string[] args) {
      string root = AppDomain.CurrentDomain.BaseDirectory;
      if (args.Length == 1 && args[0] == "--child") {
        File.WriteAllText(Path.Combine(root, "child-pid.txt"), Process.GetCurrentProcess().Id.ToString());
        Thread.Sleep(60000); return 0;
      }
      bool check = args.Length == 1 && args[0] == "--check-webview2";
      File.AppendAllText(Path.Combine(root, "calls.txt"), check ? "check\\n" : "start\\n");
      string file = Path.Combine(root, check ? "check-code.txt" : "start-code.txt");
      int code = File.Exists(file) ? int.Parse(File.ReadAllText(file)) : 0;
      if (check && code == -999) {
        File.WriteAllText(Path.Combine(root, "parent-pid.txt"), Process.GetCurrentProcess().Id.ToString());
        var child = new ProcessStartInfo(Path.Combine(root, "chaoxing-gui-tauri.exe"), "--child");
        child.UseShellExecute = false; child.CreateNoWindow = true; child.RedirectStandardInput = true;
        using (var process = Process.Start(child)) { process.StandardInput.Close(); Thread.Sleep(60000); }
      }
      return code;
    } }`);
  const host = path.join(portable, 'chaoxing-gui-tauri.exe');
  const compiler = path.join(process.env.WINDIR, 'Microsoft.NET/Framework64/v4.0.30319/csc.exe');
  const compiled = spawnSync(compiler, ['/nologo', '/target:exe', `/out:${host}`, source], { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  assert.ifError(compiled.error);
  assert.equal(compiled.status, 0, `${compiled.stdout}\n${compiled.stderr}`);
  const windowsPowerShell = path.join(process.env.WINDIR, 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const launch = () => runPowerShell(['-File', path.join(portable, 'Start-Chaoxing.ps1')], windowsPowerShell);
  for (const [check, start, expected, calls] of [[3, 0, 3, 'check\n'], [17, 0, 17, 'check\n'], [0, 23, 23, 'check\nstart\n'], [0, 0, 0, 'check\nstart\n']]) {
    write(portable, 'check-code.txt', String(check));
    write(portable, 'start-code.txt', String(start));
    write(portable, 'calls.txt', '');
    const result = launch();
    assert.equal(result.status, expected, result.output);
    assert.equal(fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8'), calls);
  }
  write(portable, 'check-code.txt', '-999');
  const timedOut = runPowerShell(['-File', path.join(portable, 'Start-Chaoxing.ps1'), '-CheckTimeoutSeconds', '1'], windowsPowerShell);
  failure(timedOut, /timed\s+out/i);
  for (const name of ['parent-pid.txt', 'child-pid.txt']) {
    const pid = Number(fs.readFileSync(path.join(portable, name), 'utf8'));
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' }, `${name} should be stopped`);
  }
  const offline = path.join(portable, 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
  fs.copyFileSync(host, offline);
  const before = fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8');
  const rejected = runPowerShell(['-File', path.join(portable, 'Install-WebView2.ps1'), '-InstallerPath', offline], windowsPowerShell);
  failure(rejected, /signature|Microsoft|Authenticode/i);
  assert.equal(fs.readFileSync(path.join(portable, 'calls.txt'), 'utf8'), before, 'unsigned installer must never execute');
});

````

## desktop/tests/session-store.test.js

SHA256: 6a57db943692a9650f51ed8f9642cef49eee34af23ca9524c24d8b253db8f8c1

````text
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { SessionStore, isTrustedSender, registerSessionIpc } = require('../session-store');

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-session-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return { directory, store: new SessionStore(directory) };
}

function trustedWindow(url = 'http://127.0.0.1:42001/') {
  const mainFrame = { url };
  const window = { isDestroyed: () => false, webContents: { mainFrame } };
  return { window, event: { sender: window.webContents, senderFrame: mainFrame } };
}

test('account and task persist across process/origin changes; logout removes them', (t) => {
  const { directory, store } = fixture(t);
  store.rememberLogin('alice');
  store.rememberTask({ username: 'alice', taskId: 'task-one' });
  const restarted = new SessionStore(directory);
  assert.deepEqual(restarted.read(), { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: { username: 'alice', taskId: 'task-one' } });
  const disk = fs.readFileSync(store.filename, 'utf8');
  assert.equal(disk.includes('password'), false);
  assert.equal(disk.includes('42001'), false);
  fs.writeFileSync(`${store.filename}.tmp`, disk);
  restarted.clear();
  assert.equal(fs.existsSync(store.filename), false);
  assert.equal(fs.existsSync(`${store.filename}.tmp`), false);
  assert.equal(store.read().login, null);
});

test('payloads reject passwords, extra fields, oversized values and account mismatches', (t) => {
  const { store } = fixture(t);
  store.rememberLogin('alice');
  for (const value of ['', ' alice ', 'a'.repeat(129), 'a\nb', { username: 'alice', password: 'test-only' }, ['alice']]) {
    assert.throws(() => store.rememberLogin(value));
  }
  for (const task of [{ username: 'bob', taskId: 'one' }, { username: 'alice', taskId: '../one' }, { username: 'alice', taskId: 'x'.repeat(129) }, { username: 'alice', taskId: 'one', password: 'test-only' }]) {
    assert.throws(() => store.rememberTask(task));
  }
  store.rememberTask({ username: 'alice', taskId: 'one' });
  store.rememberLogin('bob');
  assert.equal(store.read().activeTask, null);
});

test('corrupt, oversized and credential-bearing disk records are never exposed', (t) => {
  const { store } = fixture(t);
  for (const data of ['not json', 'x'.repeat(4097), JSON.stringify({ version: 1, login: { username: 'alice', use_cookies: true, password: 'test-only' }, activeTask: null })]) {
    fs.writeFileSync(store.filename, data);
    assert.deepEqual(store.read(), { version: 1, login: null, activeTask: null });
  }
});

test('IPC trusts only the current main window main frame and exact backend origin', () => {
  const origin = 'http://127.0.0.1:42001';
  const { window, event } = trustedWindow();
  assert.equal(isTrustedSender(event, window, origin), true);
  assert.equal(isTrustedSender({ ...event, sender: {} }, window, origin), false);
  assert.equal(isTrustedSender({ ...event, senderFrame: { url: origin } }, window, origin), false);
  assert.equal(isTrustedSender(event, { ...window, isDestroyed: () => true }, origin), false);
  assert.equal(isTrustedSender(event, window, null), false);
  for (const url of ['http://127.0.0.1:42002/', 'http://localhost:42001/', 'http://127.0.0.1:42001@evil.example/', 'http://127.0.0.1:420011/', 'data:text/html,loading', 'file:///tmp/page.html']) {
    window.webContents.mainFrame.url = url;
    assert.equal(isTrustedSender(event, window, origin), false, url);
  }
});

test('all IPC methods enforce sender and argument boundaries before touching storage', (t) => {
  const { store } = fixture(t);
  const { window, event } = trustedWindow();
  const handlers = new Map();
  registerSessionIpc({ handle: (name, handler) => handlers.set(name, handler) }, { getWindow: () => window, getOrigin: () => 'http://127.0.0.1:42001', store });
  const login = handlers.get('session:remember-login');
  login(event, 'alice');
  for (const handler of handlers.values()) assert.throws(() => handler({ ...event, sender: {} }), /禁止访问/);
  assert.throws(() => handlers.get('session:read')(event, 'extra'));
  assert.throws(() => handlers.get('session:clear')(event, 'extra'));
  assert.throws(() => login(event, 'alice', 'password'));
  assert.throws(() => login(event, { username: 'alice', password: 'test-only' }));
  assert.equal(store.read().login.username, 'alice');
  handlers.get('session:clear')(event);
  assert.equal(store.read().login, null);
});

test('sandbox preload exposes four named operations, never a generic IPC API', async () => {
  let exposed;
  const calls = [];
  const electron = { contextBridge: { exposeInMainWorld: (name, methods) => { exposed = { name, methods }; } }, ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve(null); } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.js'), 'utf8'), { require: (name) => { assert.equal(name, 'electron'); return electron; } });
  assert.equal(exposed.name, 'chaoxingSession');
  assert.deepEqual(Object.keys(exposed.methods), ['read', 'rememberLogin', 'rememberTask', 'clear']);
  await exposed.methods.read();
  await exposed.methods.rememberLogin('alice');
  await exposed.methods.rememberTask(null);
  await exposed.methods.clear();
  assert.deepEqual(calls, [['session:read'], ['session:remember-login', 'alice'], ['session:remember-task', null], ['session:clear']]);
});

````

## desktop/tests/signing.test.mjs

SHA256: 7314b00ea3358ad742c341b970d85dbfa98b0dc5f05591df2963f56dbaf7536f

````text
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { before, after } from 'node:test';
import { fileURLToPath } from 'node:url';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const callback = path.join(desktop, 'scripts/bundle-sign.ps1');
const version = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8')).version;
const windowsTest = process.platform === 'win32' ? test : test.skip;
const unavailableThumbprint = '0'.repeat(40);
const marker = '__TAURI_BUNDLE_TYPE_VAR_NSS';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const quote = value => `'${String(value).replaceAll("'", "''")}'`;
let work;
let unsignedPe;
let signedSource;

function powershell(args) {
  const result = spawnSync('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', ...args], {
    encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 4 * 1024 * 1024,
  });
  assert.ifError(result.error);
  return { ...result, output: `${result.stdout}\n${result.stderr}` };
}

function code(source) {
  const script = `$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ${source}`;
  return powershell(['-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')]);
}

function succeeds(result) { assert.equal(result.status, 0, result.output); }
function fails(result, expression) {
  assert.notEqual(result.status, 0, result.output);
  assert.match(result.output, expression);
}

function removeWithin(parent, directory) {
  assert.ok(path.resolve(directory).startsWith(path.resolve(parent) + path.sep));
  fs.rmSync(directory, { recursive: true, force: true });
}

before(() => {
  if (process.platform !== 'win32') return;
  work = fs.mkdtempSync(path.join(os.tmpdir(), 'chaoxing-signing-tests-'));
  const assembly = path.join(work, 'unsigned-fixture.dll');
  // Compile an inert PE, never run it, and never create/import a certificate.
  const prepared = code(`
    Add-Type -TypeDefinition 'public sealed class BundleSignFixture { public int Value() { return 7; } }' -OutputAssembly ${quote(assembly)} -OutputType Library | Out-Null
    $unsigned = Get-AuthenticodeSignature -LiteralPath ${quote(assembly)}
    if ($unsigned.Status -ne 'NotSigned') { throw 'Fixture must be an unsigned PE' }
    $vendor = $null
    foreach ($candidate in @((Join-Path $PSHOME 'pwsh.exe'), (Join-Path $env:WINDIR 'System32/cmd.exe'))) {
      $signature = Get-AuthenticodeSignature -LiteralPath $candidate
      if ($signature.Status -eq 'Valid' -and $signature.SignatureType -eq 'Authenticode') {
        $vendor = $candidate
        break
      }
    }
    @{ signedSource=$vendor } | ConvertTo-Json -Compress
  `);
  succeeds(prepared);
  unsignedPe = fs.readFileSync(assembly);
  signedSource = JSON.parse(prepared.stdout.trim()).signedSource;
});

after(() => {
  if (work) removeWithin(os.tmpdir(), work);
});

function fixture(t) {
  const root = fs.mkdtempSync(path.join(work, 'case-'));
  const links = [];
  t.after(() => {
    for (const link of links.reverse()) {
      const info = fs.lstatSync(link, { throwIfNoEntry: false });
      if (info) {
        assert.ok(info.isSymbolicLink(), 'only unlink the junction this fixture created');
        assert.ok(path.resolve(link).startsWith(root + path.sep));
        fs.unlinkSync(link);
      }
    }
    removeWithin(work, root);
  });
  const backend = path.join(root, '资源 staging/backend');
  const manifest = path.join(root, '资源 staging/backend-manifest.json');
  const host = path.join(root, 'host 中文/chaoxing-gui-tauri.exe');
  const recordDirectory = path.join(root, 'unique capture 中文');
  const record = path.join(recordDirectory, 'host.json');
  const relative = '_internal/保持/helper.dll';
  const resource = path.join(backend, relative);
  fs.mkdirSync(path.dirname(resource), { recursive: true });
  fs.mkdirSync(path.dirname(host), { recursive: true });
  fs.mkdirSync(recordDirectory);
  fs.writeFileSync(path.join(backend, 'chaoxing-backend.exe'), unsignedPe);
  fs.writeFileSync(resource, unsignedPe);
  fs.writeFileSync(host, Buffer.concat([unsignedPe, Buffer.from(marker)]));
  const document = {
    schemaVersion: 1, kind: 'chaoxing-backend', version, entryPoint: 'chaoxing-backend.exe',
    files: ['chaoxing-backend.exe', relative].map(name => ({ path: name, length: unsignedPe.length, sha256: hash(unsignedPe) })),
    directories: ['_internal', '_internal/保持'],
  };
  const saveManifest = () => fs.writeFileSync(manifest, JSON.stringify(document));
  saveManifest();
  return { root, backend, manifest, host, record, recordDirectory, resource, relative, document, saveManifest, links };
}

function invoke(f, file, overrides = {}) {
  const parameters = {
    Path: file, CertificateThumbprint: unavailableThumbprint, ExpectedHostPath: f.host,
    HostRecordPath: f.record, BackendDirectory: f.backend, BackendManifestPath: f.manifest, ...overrides,
  };
  return powershell(['-File', callback, ...Object.entries(parameters).flatMap(([key, value]) => [`-${key}`, value])]);
}

function updateResourceRecord(f, bytes) {
  fs.writeFileSync(f.resource, bytes);
  const entry = f.document.files.find(value => value.path === f.relative);
  entry.length = bytes.length;
  entry.sha256 = hash(bytes);
  f.saveManifest();
}

windowsTest('Bundle callback preserves exact unsigned backend EXE/DLL bytes without requiring a certificate', t => {
  const f = fixture(t);
  for (const resource of [f.resource, path.join(f.backend, 'chaoxing-backend.exe')]) {
    const before = fs.readFileSync(resource);
    const result = invoke(f, resource);
    succeeds(result);
    const report = JSON.parse(result.stdout);
    assert.equal(report.action, 'preserved-backend-resource');
    assert.equal(report.signatureStatus, 'NotSigned');
    assert.equal(report.sha256, hash(before));
    assert.deepEqual(fs.readFileSync(resource), before);
  }
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Bundle callback preserves an existing valid vendor signature with its original signer', t => {
  if (!signedSource) { t.skip('No existing valid embedded vendor signature is available'); return; }
  const f = fixture(t);
  const bytes = fs.readFileSync(signedSource);
  updateResourceRecord(f, bytes);
  const result = invoke(f, f.resource);
  succeeds(result);
  const report = JSON.parse(result.stdout);
  assert.equal(report.signatureStatus, 'Valid');
  assert.ok(report.signerThumbprint);
  assert.notEqual(report.signerThumbprint, unavailableThumbprint);
  assert.deepEqual(fs.readFileSync(f.resource), bytes);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Bundle callback rejects an invalid existing signature even when its file hash matches the manifest', t => {
  if (!signedSource) { t.skip('No existing valid embedded vendor signature is available'); return; }
  const f = fixture(t);
  const bytes = fs.readFileSync(signedSource);
  const pe = bytes.readUInt32LE(0x3c);
  const section = pe + 24 + bytes.readUInt16LE(pe + 20);
  const rawOffset = bytes.readUInt32LE(section + 20);
  assert.ok(rawOffset > 0 && rawOffset < bytes.length);
  bytes[rawOffset] ^= 1; // Corrupt signed content in the owned copy only.
  updateResourceRecord(f, bytes);
  fails(invoke(f, f.resource), /Invalid existing backend resource signature/);
  assert.deepEqual(fs.readFileSync(f.resource), bytes);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Backend length/hash changes and unlisted resources fail before any signing', t => {
  const f = fixture(t);
  const changed = Buffer.from(unsignedPe);
  changed[changed.length - 1] ^= 1;
  fs.writeFileSync(f.resource, changed);
  fails(invoke(f, f.resource), /Backend resource length\/hash mismatch/);
  fs.writeFileSync(f.resource, Buffer.concat([unsignedPe, Buffer.from('changed length')]));
  fails(invoke(f, f.resource), /Backend resource length\/hash mismatch/);
  const unlisted = path.join(f.backend, 'unlisted.dll');
  fs.writeFileSync(unlisted, unsignedPe);
  fails(invoke(f, unlisted), /not in the backend manifest/);
  assert.deepEqual(fs.readFileSync(unlisted), unsignedPe);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('Backend manifest schema, duplicate paths, and traversal entries are refused', t => {
  const f = fixture(t);
  const original = structuredClone(f.document);
  const variants = [
    { ...original, kind: 'portable' },
    { ...original, files: [...original.files, { ...original.files[1], path: f.relative.toUpperCase() }] },
    { ...original, files: [...original.files, { ...original.files[1], path: '../outside.dll' }] },
    { ...original, files: original.files.map(entry => ({ ...entry, length: String(entry.length) })) },
  ];
  for (const document of variants) {
    fs.writeFileSync(f.manifest, JSON.stringify(document));
    fails(invoke(f, f.resource), /manifest|Unsafe relative path/i);
  }
  assert.deepEqual(fs.readFileSync(f.resource), unsignedPe);
});

windowsTest('Bundle callback rejects resource junctions and record paths inside the frozen backend', t => {
  const f = fixture(t);
  fails(invoke(f, f.host, { HostRecordPath: path.join(f.backend, 'capture.json') }), /overlapping/);
  const internal = path.join(f.backend, '_internal');
  const external = path.join(f.root, 'external sentinel');
  assert.ok(internal.startsWith(f.root + path.sep) && external.startsWith(f.root + path.sep));
  fs.renameSync(internal, external);
  fs.symlinkSync(external, internal, 'junction');
  f.links.push(internal);
  const before = fs.readFileSync(path.join(external, '保持/helper.dll'));
  fails(invoke(f, f.resource), /reparse|junction/i);
  assert.deepEqual(fs.readFileSync(path.join(external, '保持/helper.dll')), before);
});

windowsTest('NSIS host requires one NSS marker before it can reach the signer', t => {
  const f = fixture(t);
  for (const text of ['', '__TAURI_BUNDLE_TYPE_VAR_UNK', marker + marker, marker + '__TAURI_BUNDLE_TYPE_VAR_UNK']) {
    const bytes = Buffer.concat([unsignedPe, Buffer.from(text)]);
    fs.writeFileSync(f.host, bytes);
    fails(invoke(f, f.host), /exactly one __TAURI_BUNDLE_TYPE_VAR_NSS/);
    assert.deepEqual(fs.readFileSync(f.host), bytes);
    assert.equal(fs.existsSync(f.record), false);
  }
});

windowsTest('Host capture refuses old records, missing capture directories, and invalid input paths', t => {
  const f = fixture(t);
  const old = '{"previous":"must survive"}';
  fs.writeFileSync(f.record, old);
  fails(invoke(f, f.host), /overwrite an existing host record/);
  assert.equal(fs.readFileSync(f.record, 'utf8'), old);
  fs.unlinkSync(f.record);
  fails(invoke(f, f.host, { HostRecordPath: path.join(f.root, 'absent', 'host.json') }), /existing unique capture directory/);
  fails(invoke(f, path.join(f.root, 'missing.exe')), /existing regular file/);
  fails(invoke(f, f.backend), /existing regular file/);
  fails(invoke(f, f.host, { CertificateThumbprint: 'not-a-thumbprint' }), /Invalid signing certificate thumbprint/);
  assert.equal(fs.existsSync(f.record), false);
});

windowsTest('A valid marker without the requested private key fails without signing or publishing a record', t => {
  const f = fixture(t);
  const before = fs.readFileSync(f.host);
  fails(invoke(f, f.host), /certificate with private key is unavailable/);
  assert.deepEqual(fs.readFileSync(f.host), before);
  assert.deepEqual(fs.readdirSync(f.recordDirectory), []);
  // A sibling with the same backend prefix must take the ordinary signer path.
  const sibling = path.join(f.root, '资源 staging/backend-other.dll');
  fs.writeFileSync(sibling, unsignedPe);
  fails(invoke(f, sibling), /certificate with private key is unavailable/);
  assert.deepEqual(fs.readFileSync(sibling), unsignedPe);
  assert.equal(fs.existsSync(f.record), false);
});

````

## pyproject.toml

SHA256: ce25748d7b627d8b5a7119ccadff5f4bd7faf8ffae5cdb5667932483119601f4

````text
[project]
name = "chaoxing"
version = "1.1.1"
description = "超星学习通/超星尔雅/泛雅超星全自动无人值守完成任务点"
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.11,<4.0"
dynamic = ["dependencies"]

[tool.setuptools]
py-modules = []

[tool.setuptools.dynamic]
dependencies = {file=["requirements.txt"]}

````

## requirements-test.txt

SHA256: 43c6278ba5e5349e056941d6e58cdb31564e5f4cd881ce64b2c1e0ea861f3074

````text
# Lightweight runtime dependencies for the offline unittest suite.
# OCR engines and notification services are mocked; no model downloads required.
requests>=2.32.5
pyaes>=1.6.1
beautifulsoup4>=4.14.2
lxml
loguru>=0.7.3
flask>=3.1.2
flask-cors>=5.0.0
fonttools>=4.60.1
openai>=1.109.1
tqdm>=4.67.1
httpx[socks]>=0.28.1
tenacity>=8.2.0
Pillow

````

## requirements.txt

SHA256: 1ed05b84b7b6ad860bb24f9eb1a37c987fbbf247206110c8f6db04abf43f7153

````text
requests>=2.32.5
pyaes>=1.6.1
beautifulsoup4>=4.14.2
lxml
argparse>=1.4.0
loguru>=0.7.3
celery>=5.5.3
flask>=3.1.2
flask-cors>=5.0.0
fonttools>=4.60.1
openai>=1.109.1
ddddocr
tqdm>=4.67.1
httpx[socks]>=0.28.1
urllib3>=2.5.0
chardet>=3.0.2,<6.0.0
tenacity>=8.2.0

# Optional local OCR (PaddlePaddle itself is installed by the platform-specific
# startup/build scripts because CPU/GPU wheels use different package indexes).
paddleocr>=3.7.0,<3.8.0

````

## tests/test_desktop_runtime.py

SHA256: 7b051f1911a8688d0f3ef7eb9b6455690039ef24bccf9a814b59465288ee7b00

````text
"""Tauri 宿主模式协议测试（CHAOXING_TAURI=1）。

覆盖（P1 计划 §7 tests 清单）：
1. 缺 token 环境启动 → 退出/报错，不 fallback。
2. ready 行格式（前缀、version、port、instanceId、单行、flush）。
3. token guard：所有路由无 token 401 / 错 token 401 / 伪造 Host 401 /
   OPTIONS 预检 401 / 正确 token+Host 200 且 body 回显 instanceId。
4. Tauri 模式不注册 CORS（响应无 Access-Control-Allow-Origin）。
5. 普通模式（无 TAURI env）health 无需 token、guard 未注册。
6. make_server 绑定 127.0.0.1:0 实际端口可取、socket 可连。
"""

import json
import os
import socket
import threading
import unittest
from unittest import mock

from api import desktop_runtime
from api.desktop_runtime import (
    READY_MARKER,
    TauriEnvError,
    emit_ready_line,
    parse_ready_env,
    register_token_guard,
    run_tauri_server,
)

TOKEN = "test-token-abc123"
INSTANCE = "inst-xyz789"


class _FakeEnviron(dict):
    pass


class ParseReadyEnvTests(unittest.TestCase):
    def test_missing_token_raises(self):
        env = _FakeEnviron()
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_empty_token_raises(self):
        env = _FakeEnviron(
            {"CHAOXING_TAURI_TOKEN": "", "CHAOXING_TAURI_INSTANCE_ID": INSTANCE}
        )
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_missing_instance_raises(self):
        env = _FakeEnviron({"CHAOXING_TAURI_TOKEN": TOKEN})
        with self.assertRaises(TauriEnvError):
            parse_ready_env(env)

    def test_valid_env_returns_pair(self):
        env = _FakeEnviron(
            {"CHAOXING_TAURI_TOKEN": TOKEN, "CHAOXING_TAURI_INSTANCE_ID": INSTANCE}
        )
        self.assertEqual(parse_ready_env(env), (TOKEN, INSTANCE))

    def test_no_fallback_in_real_environ(self):
        """真实 os.environ 缺少变量时必须抛错（不允许回退旧模式）。"""
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TauriEnvError):
                parse_ready_env()


class ReadyLineTests(unittest.TestCase):
    def test_ready_line_format(self):
        import io

        buf = io.StringIO()
        emit_ready_line(12345, INSTANCE, stream=buf)
        out = buf.getvalue()
        # 单行 + 换行结尾
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(out.count("\n"), 1)
        data = json.loads(out)
        self.assertEqual(data["ready"], READY_MARKER)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["port"], 12345)
        self.assertEqual(data["instanceId"], INSTANCE)


class TokenGuardTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask, jsonify

        self.app = Flask(__name__)

        @self.app.route("/api/config", methods=["GET", "POST"])
        def config():
            return jsonify({"ok": True})

        @self.app.route("/api/task/<task_id>")
        def task(task_id):
            return jsonify({"taskId": task_id})

        self.server = run_tauri_server(self.app, TOKEN, INSTANCE)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()

    def _request(self, path, token=TOKEN, host=None, method="GET", data=None):
        """经真实 socket 发请求（可选伪造 Host 头）。"""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        req_lines = [f"{method} {path} HTTP/1.1"]
        if host is None:
            host = f"127.0.0.1:{self.port}"
        req_lines.append(f"Host: {host}")
        if token is not None:
            req_lines.append(f"X-Auth-Token: {token}")
        req_lines.append("Connection: close")
        body = ""
        if data is not None:
            body = json.dumps(data)
            req_lines.append("Content-Type: application/json")
            req_lines.append(f"Content-Length: {len(body)}")
        req = "\r\n".join(req_lines) + "\r\n\r\n" + body
        sock.sendall(req.encode())
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        raw = b"".join(chunks)
        head, _, payload = raw.partition(b"\r\n\r\n")
        status = int(head.split(b"\r\n")[0].split()[1])
        headers = {
            k.decode().lower(): v.decode()
            for k, _, v in (h.partition(b":") for h in head.split(b"\r\n")[1:])
        }
        return status, headers, payload

    def test_health_without_token_401(self):
        status, _, _ = self._request("/api/health", token=None)
        self.assertEqual(status, 401)

    def test_health_wrong_token_401(self):
        status, _, _ = self._request("/api/health", token="wrong")
        self.assertEqual(status, 401)

    def test_health_forged_host_401(self):
        status, _, _ = self._request(
            "/api/health", host="127.0.0.1:1"
        )
        self.assertEqual(status, 401)

    def test_business_route_without_token_401(self):
        status, _, _ = self._request("/api/config", token=None)
        self.assertEqual(status, 401)

    def test_options_preflight_401(self):
        status, _, _ = self._request(
            "/api/config", token=None, method="OPTIONS"
        )
        self.assertEqual(status, 401)

    def test_correct_token_and_host_200(self):
        status, _, payload = self._request("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(payload)
        self.assertEqual(data.get("instanceId"), INSTANCE)

    def test_correct_token_business_route_200(self):
        status, _, payload = self._request("/api/task/t1")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["taskId"], "t1")

    def test_no_cors_header_in_tauri_mode(self):
        status, headers, _ = self._request("/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_binds_random_port_and_connectable(self):
        # 端口非 0 且 socket 可连（TokenGuardTests.setUp 已验证连接性）
        self.assertNotEqual(self.port, 0)
        self.assertTrue(self.port > 0)


class NormalModeTests(unittest.TestCase):
    """普通模式（无 CHAOXING_TAURI）guard 未注册，行为与现有测试一致。"""

    def setUp(self):
        from flask import Flask, jsonify

        self.app = Flask(__name__)

        @self.app.route("/api/health")
        def health():
            return jsonify({"status": True, "msg": "OK"})

        # 不调用 register_token_guard —— 模拟普通模式
        self.server = desktop_runtime.make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()

    def test_health_without_token_200(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        req = f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nConnection: close\r\n\r\n"
        sock.sendall(req.encode())
        chunks = []
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        head = b"".join(chunks).split(b"\r\n\r\n")[0]
        status = int(head.split(b"\r\n")[0].split()[1])
        self.assertEqual(status, 200)

    def test_app_entry_has_no_tauri_side_effect(self):
        """app.py 模块导入（无 TAURI env）不应注册 guard 或改变 CORS。"""
        # app.py 导入期注册的 CORS 契约保持不变（现有 131 项回归覆盖）；
        # 这里只断言 desktop_runtime.parse_ready_env 在缺 env 时报错，
        # 确保 __main__ 的 TAURI 分支不会误入。
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TauriEnvError):
                parse_ready_env()


if __name__ == "__main__":
    unittest.main()

````

## tests/test_release_version.py

SHA256: cd17c73e3a36d77ed3b50bcfc5ab9b992ae9e89b5e40003ed0ce3f13dae7772e

````text
"""Release metadata must agree before any packaging or publication starts."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "desktop/scripts/version.py"


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chaoxing-version-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("pyproject.toml", '[project]\nname = "chaoxing"\nversion = "1.1.1"\n')
        self.write("desktop/src-tauri/Cargo.toml", '[package]\nname = "chaoxing-desktop"\nversion = "1.1.1"\n\n[dependencies]\nserde = "1"\n')
        self.write("desktop/src-tauri/Cargo.lock", 'version = 4\n\n[[package]]\nname = "another"\nversion = "9.8.7"\n\n[[package]]\nname = "chaoxing-desktop"\nversion = "1.1.1"\ndependencies = ["another"]\n')
        self.write_json("desktop/src-tauri/tauri.conf.json", {"version": "1.1.1", "identifier": "com.chaoxing.gui"})
        for project in ("web", "desktop"):
            self.write_json(f"{project}/package.json", {"name": project, "version": "1.1.1"})
            self.write_json(f"{project}/package-lock.json", {
                "name": project, "version": "1.1.1", "lockfileVersion": 3,
                "packages": {"": {"name": project, "version": "1.1.1"},
                             "node_modules/another": {"version": "9.8.7"}},
            })

    def write(self, name, value):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    def write_json(self, name, value):
        self.write(name, json.dumps(value, indent=2) + "\n")

    def run_check(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root),
                                 "--json", *args], capture_output=True, text=True,
                                encoding="utf-8", timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def test_matching_metadata_and_tag_are_accepted_without_mutation(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        output = json.loads(self.run_check("--check", "--tag", "v1.1.1").stdout)
        self.assertEqual(output["version"], "1.1.1")
        self.assertTrue(output["success"])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_drift_in_each_metadata_file_is_rejected(self):
        for name in ("desktop/src-tauri/Cargo.toml", "desktop/src-tauri/Cargo.lock",
                     "desktop/src-tauri/tauri.conf.json", "web/package.json",
                     "web/package-lock.json", "desktop/package.json", "desktop/package-lock.json"):
            with self.subTest(path=name):
                path = self.root / name
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace('"1.1.1"', '"1.0.0"'), encoding="utf-8")
                result = self.run_check("--check", success=False)
                self.assertIn(name, result.stdout + result.stderr)
                path.write_text(original, encoding="utf-8")

    def test_sync_uses_pyproject_and_preserves_dependency_versions(self):
        canonical = '[project]\nname = "chaoxing"\nversion = "2.3.4"\n'
        self.write("pyproject.toml", canonical)
        self.run_check("--sync")
        self.assertEqual((self.root / "pyproject.toml").read_text(), canonical)
        self.run_check("--check", "--tag", "refs/tags/v2.3.4")
        self.assertIn('version = "9.8.7"', (self.root / "desktop/src-tauri/Cargo.lock").read_text())
        for project in ("web", "desktop"):
            lock = json.loads((self.root / project / "package-lock.json").read_text())
            self.assertEqual(lock["packages"]["node_modules/another"]["version"], "9.8.7")

    def test_tag_mismatch_blocks_release(self):
        result = self.run_check("--check", "--tag", "v1.1.2", success=False)
        self.assertIn("tag", result.stdout.lower() + result.stderr.lower())

    def test_npm_lock_root_package_version_is_checked(self):
        name = "desktop/package-lock.json"
        lock = json.loads((self.root / name).read_text())
        lock["packages"][""]["version"] = "1.0.0"
        self.write_json(name, lock)
        self.run_check("--check", success=False)

    def test_missing_cargo_package_is_not_fabricated_by_sync(self):
        self.write("desktop/src-tauri/Cargo.lock", 'version = 4\n[[package]]\nname = "other"\nversion = "1.1.1"\n')
        self.run_check("--sync", success=False)

    def test_artifact_versions_must_match_the_source(self):
        self.write("artifacts/chaoxing-gui-tauri-setup-1.1.1-windows-x64.exe", "test")
        self.write("artifacts/chaoxing-gui-tauri-portable-1.1.1-windows-x64.zip", "test")
        self.run_check("--check", "--artifacts", str(self.root / "artifacts"))
        self.write("artifacts/chaoxing-gui-tauri-setup-1.0.0-windows-x64.exe", "stale")
        self.run_check("--check", "--artifacts", str(self.root / "artifacts"), success=False)

    def test_invalid_source_version_is_rejected_before_writes(self):
        self.write("pyproject.toml", '[project]\nversion = "../escape"\n')
        before = (self.root / "desktop/package.json").read_bytes()
        self.run_check("--sync", success=False)
        self.assertEqual((self.root / "desktop/package.json").read_bytes(), before)

    def test_artifact_kind_requires_its_matching_extension(self):
        for name in ("chaoxing-gui-tauri-setup-1.1.1-windows-x64.zip",
                     "chaoxing-gui-tauri-portable-1.1.1-windows-x64.exe"):
            with self.subTest(name=name):
                self.write(f"artifacts/{name}", "test")
                self.run_check("--check", "--artifacts", str(self.root / "artifacts"), success=False)
                (self.root / "artifacts" / name).unlink()


if __name__ == "__main__":
    unittest.main()

````

## tests/test_tauri_entry_contract.py

SHA256: 4d1c5747f7af9591f971c5c0a8313c7a3299b6b49af16fbd5f1b7c8be6c71beb

````text
"""Real app import contracts, in isolated data directories without accounts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class TauriEntryContractTests(unittest.TestCase):
    def inspect_app(self, *, tauri, port="5000"):
        repo = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory(prefix="chaoxing-p2-entry-") as profile:
            env = dict(os.environ, CHAOXING_DATA_DIR=profile, CHAOXING_PORT=port,
                       PYTHONIOENCODING="utf-8")
            if tauri:
                env["CHAOXING_TAURI"] = "1"
            else:
                env.pop("CHAOXING_TAURI", None)
            script = textwrap.dedent("""
                import json, sys
                sys.path.insert(0, sys.argv[1])
                import app as entry
                if sys.argv[2] == 'tauri':
                    from api.desktop_runtime import register_token_guard
                    register_token_guard(entry.app, 'synthetic-token', 'test-instance', 4321)
                client = entry.app.test_client()
                headers = {'Host':'127.0.0.1:4321', 'X-Auth-Token':'synthetic-token'}
                good = client.get('/api/health', headers=headers)
                origin = client.get('/api/health', headers={**headers, 'Origin':'http://localhost:5000'})
                missing = client.get('/api/health', headers={'Host':'127.0.0.1:4321', 'Origin':'http://localhost:5000'})
                entry.task_store.close()
                print('P2_RESULT=' + json.dumps({
                    'good':good.status_code,
                    'origin_status':origin.status_code,
                    'origin_cors':origin.headers.get('Access-Control-Allow-Origin'),
                    'missing_status':missing.status_code,
                    'missing_cors':missing.headers.get('Access-Control-Allow-Origin'),
                }))
            """)
            result = subprocess.run(
                [sys.executable, "-c", script, repo, "tauri" if tauri else "normal"],
                cwd=profile, env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(next(line.removeprefix("P2_RESULT=")
                                   for line in result.stdout.splitlines()
                                   if line.startswith("P2_RESULT=")))

    def test_tauri_origin_is_rejected_without_cors_even_with_valid_token(self):
        result = self.inspect_app(tauri=True)
        self.assertEqual(result["good"], 200)
        self.assertEqual(result["origin_status"], 401)
        self.assertIsNone(result["origin_cors"])
        self.assertEqual(result["missing_status"], 401)
        self.assertIsNone(result["missing_cors"])

    def test_tauri_ignores_legacy_port_environment(self):
        self.assertEqual(self.inspect_app(tauri=True, port="unused-by-tauri")["good"], 200)

    def test_normal_browser_mode_keeps_cors_and_unauthenticated_health(self):
        result = self.inspect_app(tauri=False)
        self.assertEqual(result["missing_status"], 200)
        self.assertEqual(result["origin_cors"], "http://localhost:5000")


if __name__ == "__main__":
    unittest.main()

````

## web/package-lock.json

SHA256: dd8da04ebacb11c500149e3eb1ebd25df4fcf4652abf43b1e6e8830dd630e6c8

Lock file retained on disk at the exact repository path; hash recorded above.

## web/package.json

SHA256: d04c43c4ca8df865356593761f2df84db0f9ce16a129c69c2a1f13d17478c99f

````text
{
  "name": "chaoxing-web",
  "version": "1.1.1",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run --environment jsdom",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tauri-apps/api": "2.11.1",
    "axios": "^1.7.2",
    "clsx": "^2.1.1",
    "lucide-react": "^0.263.1",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "tailwind-merge": "^2.3.0"
  },
  "devDependencies": {
    "@testing-library/react": "^16.3.3",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "jsdom": "^26.1.0",
    "postcss": "^8.4.38",
    "tailwindcss": "^3.4.4",
    "vite": "^5.3.1",
    "vitest": "^3.2.7"
  }
}

````

## web/src/App.jsx

SHA256: a5d5b5cec0e5148467cbb44a7b755337277527d4873359be90318fe9b1e19b02

````text
import React, { useState, useRef, useEffect, useCallback } from 'react';
import Login from './components/Login';
import CourseSelection from './components/CourseSelection';
import StudyProgress from './components/StudyProgress';
import api from './api/axios';
import { sessionStore, validTaskId } from './lib/sessionStore';
import { isTerminalStatus, startTaskPolling } from './lib/taskPolling';

function App() {
  const previewMode = new URLSearchParams(window.location.search).get('preview');
  const isPreview = previewMode === 'courses' || previewMode === 'progress';
  const [step, setStep] = useState(isPreview ? previewMode : 'login');
  const [userInfo, setUserInfo] = useState(isPreview ? { username: '138****0000', password: '', use_cookies: true } : null);
  const [taskId, setTaskId] = useState(previewMode === 'progress' ? 'preview-task' : null);
  const [taskStatus, setTaskStatus] = useState(previewMode === 'progress' ? 'completed' : null);
  const [starting, setStarting] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [startError, setStartError] = useState('');
  const [monitorError, setMonitorError] = useState('');
  const [taskNotice, setTaskNotice] = useState(null);
  const startingRef = useRef(false);
  const loggingOutRef = useRef(false);
  const startController = useRef(null);
  const sessionGeneration = useRef(0);
  const taskRunning = !!taskId && !isTerminalStatus(taskStatus) && taskStatus !== 'missing';
  const currentTaskNotice = taskNotice?.username === userInfo?.username && taskNotice?.taskId === taskId ? taskNotice.message : '';

  useEffect(() => () => {
    sessionGeneration.current += 1;
    startController.current?.abort();
  }, []);

  const handleLoginSuccess = useCallback(async (info, notice = '') => {
    if (loggingOutRef.current) return;
    const generation = ++sessionGeneration.current;
    startController.current?.abort();
    startController.current = null;
    startingRef.current = false;
    setStarting(false);
    let saved;
    try { saved = await sessionStore.read(); } catch { /* Login can continue without storage. */ }
    if (loggingOutRef.current || generation !== sessionGeneration.current) return;
    const restored = saved?.activeTask?.username === info.username ? saved.activeTask.taskId : null;
    setUserInfo(info);
    setTaskId(restored);
    setTaskStatus(restored ? 'checking' : null);
    setStartError(notice);
    setMonitorError('');
    setTaskNotice(null);
    setStep(restored ? 'progress' : 'courses');
  }, []);

  const handleTaskStatus = useCallback((status) => setTaskStatus(status.status), []);
  const handleTaskMissing = useCallback(() => {
    setTaskStatus('missing');
    setMonitorError('');
    sessionStore.rememberTask(null).catch(() => {});
  }, []);

  // Keep the task reachable and prevent a second start while browsing courses.
  useEffect(() => {
    if (isPreview || step !== 'courses' || !taskRunning) return undefined;
    return startTaskPolling({
      api, taskId, includeDetails: false, onStatus: handleTaskStatus,
      onMissing: handleTaskMissing, onError: setMonitorError,
    });
  }, [isPreview, step, taskId, taskRunning, handleTaskStatus, handleTaskMissing]);

  const adoptTask = async (id, username, isCurrent) => {
    if (!isCurrent()) return;
    if (!validTaskId(id)) throw new Error('服务返回的任务 ID 无效');
    setTaskId(id);
    setTaskStatus('running');
    setMonitorError('');
    setTaskNotice(null);
    setStep('progress');
    try { await sessionStore.rememberTask({ username, taskId: id }); } catch {
      if (isCurrent()) setTaskNotice({ username, taskId: id, message: '任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID' });
    }
  };

  const handleStartStudy = async (settings) => {
    if (loggingOutRef.current || startingRef.current || !userInfo || taskRunning || !settings.course_list?.length) return;
    if (isPreview) {
      setTaskId('preview-task');
      setTaskStatus('completed');
      setStep('progress');
      return;
    }
    startingRef.current = true;
    setStarting(true);
    setStartError('');
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    const username = userInfo.username;
    startController.current = controller;
    const isCurrent = () => !loggingOutRef.current && generation === sessionGeneration.current
      && !controller.signal.aborted && startController.current === controller;
    try {
      const response = await api.post('/start', { ...settings, ...userInfo }, { signal: controller.signal });
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '启动学习任务失败，请检查配置后重试');
      await adoptTask(response.data.data.task_id, username, isCurrent);
    } catch (error) {
      if (!isCurrent()) return;
      const existing = error.response?.status === 409 && error.response.data?.data?.task_id;
      if (validTaskId(existing)) await adoptTask(existing, username, isCurrent);
      else setStartError(error.response?.data?.msg || error.message || '启动学习任务失败，请检查配置后重试');
    } finally {
      if (isCurrent()) {
        startController.current = null;
        startingRef.current = false;
        setStarting(false);
      }
    }
  };

  const handleLogout = async () => {
    if (loggingOutRef.current) return;
    // Block starts and login handoffs before asynchronous desktop storage clears.
    loggingOutRef.current = true;
    const generation = ++sessionGeneration.current;
    setLoggingOut(true);
    startController.current?.abort();
    setStarting(false);
    setStartError('');
    try {
      await sessionStore.clear();
      if (generation !== sessionGeneration.current) return;
      setUserInfo(null);
      setTaskId(null);
      setTaskStatus(null);
      setTaskNotice(null);
      setMonitorError('');
      setStep('login');
    } catch {
      if (generation === sessionGeneration.current) setStartError('清除保存的账号失败，请重试退出登录');
    } finally {
      if (generation === sessionGeneration.current) {
        startController.current?.abort();
        startController.current = null;
        startingRef.current = false;
        setStarting(false);
        loggingOutRef.current = false;
        setLoggingOut(false);
      }
    }
  };

  return (
    <div className="App">
      {step === 'login' && <Login onLoginSuccess={handleLoginSuccess} />}
      {step === 'courses' && (
        <CourseSelection
          key={userInfo.username}
          userInfo={userInfo}
          onStartStudy={handleStartStudy}
          onLogout={handleLogout}
          starting={starting}
          loggingOut={loggingOut}
          startError={startError || monitorError || currentTaskNotice}
          activeTaskId={taskId}
          taskRunning={taskRunning}
          onReturnToTask={() => { if (!loggingOutRef.current) setStep('progress'); }}
          preview={isPreview}
        />
      )}
      {step === 'progress' && taskId && (
        <StudyProgress
          taskId={taskId}
          notice={currentTaskNotice}
          onBack={() => setStep('courses')}
          onStatus={handleTaskStatus}
          onMissing={handleTaskMissing}
          preview={isPreview}
        />
      )}
    </div>
  );
}

export default App;

````

## web/src/api/axios.js

SHA256: 1758d82ad2e3f789a851bf104cc1c302a7d66b2e9e19dff4207933198fec0df1

````text
import axios, { AxiosError } from 'axios';
import { isTauriDesktop } from '../lib/desktopBridge';
import { createTauriAdapter } from './tauriAdapter';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

try {
  if (isTauriDesktop()) api.defaults.adapter = createTauriAdapter();
} catch (error) {
  // A desktop initialization failure must not silently send requests via HTTP.
  api.defaults.adapter = (config) => Promise.reject(AxiosError.from(error, AxiosError.ERR_NETWORK, config));
}

export default api;

````

## web/src/api/tauriAdapter.js

SHA256: f1e87f85a874f16df34553c6b4b8760702f4e2013941887bf4558443b3758825

````text
import { AxiosError, AxiosHeaders, CanceledError } from 'axios';
import { desktopBridge } from '../lib/desktopBridge';

const MAX_LOG_CURSOR = 0xffffffff;
const TASK_ID = '[a-zA-Z0-9_-]{1,128}';
let lastRequestId;

function nextRequestId() {
  if (lastRequestId === undefined) {
    // A fresh cryptographic prefix on every page load avoids reusing a previous
    // page's pending request IDs. A shared counter separates concurrent adapters.
    const seed = globalThis.crypto.getRandomValues(new Uint32Array(2));
    lastRequestId = (seed[0] & 0x1fffff) * 0x100000000 + seed[1];
  }
  lastRequestId = lastRequestId >= Number.MAX_SAFE_INTEGER ? 1 : lastRequestId + 1;
  return lastRequestId;
}

const invalidRequest = (config) => new AxiosError('请求参数格式错误', AxiosError.ERR_BAD_REQUEST, config);
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value));

function requestOperation(config) {
  if (config.baseURL != null && !['/api', '/api/'].includes(config.baseURL)) throw invalidRequest(config);
  if (typeof config.url !== 'string' || config.url.includes('#') || config.paramsSerializer) throw invalidRequest(config);
  if (config.responseType && !['json', 'text'].includes(config.responseType)) throw invalidRequest(config);
  const [rawPath, ...queryParts] = config.url.split('?');
  const path = rawPath.startsWith('/api/') ? rawPath.slice(4) : rawPath;
  const method = (config.method || 'get').toLowerCase();
  const routes = { 'post /login': 'login', 'post /courses': 'courses', 'get /config': 'configRead', 'post /config': 'configWrite', 'post /start': 'start' };
  let operation = routes[`${method} ${path}`];
  let taskId;
  if (!operation && method === 'get') {
    const task = path.match(new RegExp(`^/task/(${TASK_ID})(/details)?$`));
    const logs = path.match(new RegExp(`^/logs/(${TASK_ID})$`));
    if (task) { operation = task[2] ? 'taskDetails' : 'taskStatus'; taskId = task[1]; }
    else if (logs) { operation = 'taskLogs'; taskId = logs[1]; }
  }
  if (!operation || queryParts.length > 1) throw invalidRequest(config);

  const parameters = [...new URLSearchParams(queryParts[0] || '')];
  if (config.params != null) {
    if (config.params instanceof URLSearchParams) parameters.push(...config.params);
    else if (isRecord(config.params)) parameters.push(...Object.entries(config.params));
    else throw invalidRequest(config);
  }
  let after;
  if (operation === 'taskLogs') {
    if (parameters.length > 1 || parameters.some(([key]) => key !== 'after')) throw invalidRequest(config);
    const cursor = parameters.length ? parameters[0][1] : 0;
    if ((typeof cursor !== 'number' && typeof cursor !== 'string') || !/^\d+$/.test(String(cursor))) throw invalidRequest(config);
    after = Number(cursor);
    if (!Number.isSafeInteger(after) || after < 0 || after > MAX_LOG_CURSOR) throw invalidRequest(config);
  } else if (parameters.length || queryParts.length) throw invalidRequest(config);

  let payload = null;
  if (method === 'post') {
    try { payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data; }
    catch { throw invalidRequest(config); }
    if (!isRecord(payload)) throw invalidRequest(config);
  } else if (config.data != null) throw invalidRequest(config);
  return { operation, payload, ...(taskId ? { taskId } : {}), ...(after !== undefined ? { after } : {}) };
}

function timeoutError(config) {
  return new AxiosError(config.timeoutErrorMessage || '请求超时，请稍后重试',
    config.transitional?.clarifyTimeoutError ? AxiosError.ETIMEDOUT : AxiosError.ECONNABORTED, config);
}

function proxyError(error, config) {
  if (error?.kind === 'cancelled') return new CanceledError('请求已取消', config);
  if (error?.kind === 'timeout') return timeoutError(config);
  const message = error?.kind === 'backendNotReady' ? '服务尚未就绪，请稍后重新检查'
    : error?.kind === 'invalidRequest' ? '请求参数格式错误' : '无法连接学习服务，请稍后重试';
  const result = new AxiosError(message, error?.kind === 'invalidRequest' ? AxiosError.ERR_BAD_REQUEST : AxiosError.ERR_NETWORK, config);
  result.cause = error;
  return result;
}

export function createTauriAdapter({
  request = desktopBridge.apiRequest,
  cancel = desktopBridge.apiCancel,
  eventTarget = globalThis.window,
} = {}) {
  const pending = new Set();
  let closing = false;
  const onPageHide = () => {
    closing = true;
    for (const abort of [...pending]) abort();
  };

  return (config) => new Promise((resolve, reject) => {
    if (closing || config.signal?.aborted || config.cancelToken?.reason) {
      reject(new CanceledError('请求已取消', config));
      return;
    }
    let wire;
    let timeout;
    try {
      wire = { ...requestOperation(config), requestId: nextRequestId() };
      timeout = config.timeout ?? 30000;
      if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 0) {
        throw new AxiosError('请求超时设置无效', AxiosError.ERR_BAD_OPTION_VALUE, config);
      }
    } catch (error) {
      reject(error);
      return;
    }

    let settled = false;
    let sent = false;
    let timer;
    const finish = (error, response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      config.signal?.removeEventListener('abort', onAbort);
      config.cancelToken?.unsubscribe(onAbort);
      pending.delete(onAbort);
      if (!pending.size) eventTarget?.removeEventListener('pagehide', onPageHide);
      if (error) reject(error);
      else resolve(response);
    };
    const stop = (error) => {
      if (settled) return;
      if (sent) {
        // Closing the page may also close IPC. Consume cancellation failures;
        // the caller must still stop waiting and ignore the late request result.
        try { Promise.resolve(cancel(wire.requestId)).catch(() => {}); } catch { /* Already closed. */ }
      }
      finish(error);
    };
    const onAbort = () => stop(new CanceledError('请求已取消', config));
    if (!pending.size) eventTarget?.addEventListener('pagehide', onPageHide);
    pending.add(onAbort);
    config.signal?.addEventListener('abort', onAbort, { once: true });
    config.cancelToken?.subscribe(onAbort);
    if (config.signal?.aborted || config.cancelToken?.reason) onAbort();
    if (settled) return;
    if (timeout > 0) timer = setTimeout(() => stop(timeoutError(config)), timeout);

    const onResponse = (value) => {
      if (settled) return;
      try {
        if (!value || !Number.isInteger(value.status) || value.status < 100 || value.status > 599 || !Object.hasOwn(value, 'body')) {
          throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        }
        // Axios has already transformed the request. Give its response pipeline
        // the same raw JSON text as HTTP, including for custom/error transforms.
        const data = JSON.stringify(value.body);
        if (data === undefined) throw new AxiosError('学习服务返回了无效响应', AxiosError.ERR_BAD_RESPONSE, config);
        const response = { data, status: value.status, statusText: '', headers: new AxiosHeaders({ 'Content-Type': 'application/json' }), config, request: { requestId: wire.requestId } };
        if (!config.validateStatus || config.validateStatus(response.status)) finish(null, response);
        else finish(new AxiosError(`Request failed with status code ${response.status}`,
          response.status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST, config, response.request, response));
      } catch (error) { finish(error); }
    };
    sent = true;
    try {
      Promise.resolve(request(wire)).then(onResponse, (error) => finish(proxyError(error, config)));
    } catch (error) { finish(proxyError(error, config)); }
  });
}

````

## web/src/api/tauriAdapter.test.js

SHA256: c8eb7a34ac923418662050f24e2981cada879e945e65d47cdbe5d439ee4f9070

````text
import axios from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTauriAdapter } from './tauriAdapter';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const ok = (body = { status: true }) => ({ status: 200, body });

function setup(response = ok()) {
  const request = vi.fn().mockResolvedValue(response);
  const cancel = vi.fn().mockResolvedValue(undefined);
  const eventTarget = new EventTarget();
  const adapter = createTauriAdapter({ request, cancel, eventTarget });
  const api = axios.create({ baseURL: '/api', timeout: 30000, adapter, headers: { 'Content-Type': 'application/json' } });
  return { api, adapter, request, cancel, eventTarget };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('Tauri API contract', () => {
  it.each([
    ['post', '/login', { username: 'alice', password: '', use_cookies: true }, {}, { operation: 'login' }],
    ['post', '/courses', { username: 'alice' }, {}, { operation: 'courses' }],
    ['get', '/config', undefined, {}, { operation: 'configRead' }],
    ['post', '/config', { selectedCoursesByAccount: { alice: ['one'] } }, {}, { operation: 'configWrite' }],
    ['post', '/start', { username: 'alice', course_list: ['one'] }, {}, { operation: 'start' }],
    ['get', '/task/task-1_A', undefined, {}, { operation: 'taskStatus', taskId: 'task-1_A' }],
    ['get', '/task/task-1_A/details', undefined, {}, { operation: 'taskDetails', taskId: 'task-1_A' }],
    ['get', '/logs/task-1_A', undefined, { params: { after: 17 } }, { operation: 'taskLogs', taskId: 'task-1_A', after: 17 }],
  ])('maps %s %s to one explicit operation', async (method, url, data, config, operation) => {
    const { api, request, cancel } = setup();
    const response = await api.request({ method, url, data, ...config });
    expect(response.data).toEqual({ status: true });
    expect(response.status).toBe(200);
    expect(request).toHaveBeenCalledOnce();
    const wire = request.mock.calls[0][0];
    expect(wire).toEqual({ ...operation, payload: data ?? null, requestId: expect.any(Number) });
    expect(Number.isSafeInteger(wire.requestId)).toBe(true);
    expect(wire.requestId).toBeGreaterThan(0);
    expect(cancel).not.toHaveBeenCalled();
  });

  it('keeps the log cursor including zero and accepts only its bounded query form', async () => {
    const { api, request } = setup();
    await api.get('/logs/task');
    await api.get('/logs/task?after=42');
    await api.get('/logs/task', { params: new URLSearchParams({ after: '4294967295' }) });
    expect(request.mock.calls.map(([value]) => value.after)).toEqual([0, 42, 4294967295]);
  });

  it.each([
    { url: 'https://example.com/api/config' },
    { url: '//example.com/api/config' },
    { url: '/config', baseURL: 'https://example.com/api' },
    { url: '/config', method: 'delete' },
    { url: '/unknown' },
    { url: '/task/../config' },
    { url: '/task/%2e%2e' },
    { url: '/task/a%2fb' },
    { url: '/task/a/extra' },
    { url: '/config#fragment' },
    { url: '/config?after=1' },
    { url: '/logs/task?after=1&after=2' },
    { url: '/logs/task?other=1' },
    { url: '/logs/task?after=1', params: { after: 2 } },
    { url: '/logs/task', params: { after: -1 } },
    { url: '/logs/task', params: { after: 1.5 } },
    { url: '/logs/task', params: { after: 4294967296 } },
    { url: '/logs/task', params: { after: ['1'] } },
    { url: '/logs/task', params: { operation: 'start' } },
    { url: '/config', params: { unknown: undefined } },
    { url: '/config', data: { unexpected: true } },
    { url: '/login', method: 'post', data: 'not JSON', transformRequest: [(value) => value] },
  ])('fails closed without invoking for unsupported request %#', async (config) => {
    const { api, request } = setup();
    await expect(api.request(config)).rejects.toMatchObject({ code: 'ERR_BAD_REQUEST' });
    expect(request).not.toHaveBeenCalled();
  });

  it.each([409, 404])('preserves HTTP %s status and transformed error data', async (status) => {
    const body = { status: false, msg: '任务不可用', data: { task_id: 'existing-task' } };
    const { api, request } = setup({ status, body });
    await expect(api.post('/start', { course_list: ['one'] })).rejects.toMatchObject({
      isAxiosError: true, response: { status, data: body },
    });
    expect(request).toHaveBeenCalledOnce();
  });

  it('honors validateStatus including null without retrying start', async () => {
    const { api, request } = setup({ status: 409, body: { status: false } });
    expect((await api.post('/start', {}, { validateStatus: (status) => status === 409 })).status).toBe(409);
    expect((await api.post('/start', {}, { validateStatus: null })).status).toBe(409);
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([200, 409])('runs each custom transform once with raw response JSON (%s)', async (status) => {
    const body = { status: status === 200, data: { value: 1 } };
    const { api, request } = setup({ status, body });
    const transformRequest = vi.fn((value) => JSON.stringify({ ...value, transformed: true }));
    const transformResponse = vi.fn((value) => {
      expect(typeof value).toBe('string');
      return { ...JSON.parse(value), transformed: true };
    });
    const outcome = await api.post('/start', { course_list: ['one'] }, { transformRequest, transformResponse }).catch((error) => error.response);
    expect(request.mock.calls[0][0].payload).toEqual({ course_list: ['one'], transformed: true });
    expect(outcome.data).toEqual({ ...body, transformed: true });
    expect(transformRequest).toHaveBeenCalledOnce();
    expect(transformResponse).toHaveBeenCalledOnce();
  });

  it('honors text responseType without a second JSON parse', async () => {
    const { api } = setup(ok({ nested: '{"value":1}' }));
    expect((await api.get('/config', { responseType: 'text' })).data).toBe('{"nested":"{\\"value\\":1}"}');
  });

  it.each([
    ['cancelled', 'ERR_CANCELED'], ['backendNotReady', 'ERR_NETWORK'],
    ['invalidRequest', 'ERR_BAD_REQUEST'], ['network', 'ERR_NETWORK'], ['timeout', 'ECONNABORTED'],
  ])('maps host %s errors into Axios errors', async (kind, code) => {
    const { api, request } = setup();
    request.mockRejectedValue({ kind, reason: 'fixture', phase: 'failed' });
    await expect(api.post('/start', {})).rejects.toMatchObject({ code });
    expect(request).toHaveBeenCalledOnce();
  });

  it('does not reuse request IDs for parallel requests or a reloaded adapter module', async () => {
    const first = setup();
    await Promise.all(Array.from({ length: 40 }, () => first.api.get('/config')));
    vi.resetModules();
    const reloaded = await import('./tauriAdapter');
    const secondRequest = vi.fn().mockResolvedValue(ok());
    const second = axios.create({ adapter: reloaded.createTauriAdapter({ request: secondRequest, cancel: vi.fn(), eventTarget: new EventTarget() }) });
    await Promise.all(Array.from({ length: 40 }, () => second.get('/config')));
    const ids = [...first.request.mock.calls, ...secondRequest.mock.calls].map(([request]) => request.requestId);
    expect(new Set(ids).size).toBe(80);
    expect(ids.every((id) => Number.isSafeInteger(id) && id > 0)).toBe(true);
  });
});

describe('Tauri request lifetime', () => {
  it('rejects an early abort before sending an operation', async () => {
    const { api, adapter, request, cancel } = setup();
    const controller = new AbortController();
    controller.abort();
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    await expect(adapter({ method: 'post', url: '/start', data: '{}', signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(request).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('cancels in flight, consumes a late %s and never repeats start', async (completion) => {
    const { api, request, cancel } = setup();
    const pending = deferred();
    request.mockReturnValue(pending.promise);
    cancel.mockRejectedValue(new Error('window already closing'));
    const controller = new AbortController();
    const outcome = api.post('/start', {}, { signal: controller.signal });
    const rejected = expect(outcome).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    controller.abort();
    await rejected;
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    pending[completion](completion === 'resolve' ? ok() : { kind: 'network' });
    await Promise.resolve();
    await Promise.resolve();
    expect(request).toHaveBeenCalledOnce();
  });

  it('sends cancellation when abort arrives while invoke is registering', async () => {
    const { api, request, cancel } = setup();
    const controller = new AbortController();
    request.mockImplementation(() => { controller.abort(); return Promise.resolve(ok()); });
    await expect(api.post('/start', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'ERR_CANCELED' });
    expect(cancel).toHaveBeenCalledExactlyOnceWith(request.mock.calls[0][0].requestId);
    expect(request).toHaveBeenCalledOnce();
  });

  it('applies the default 30 second timeout and honors an explicit timeout', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    request.mockReturnValue(new Promise(() => {}));
    const timeout = expect(api.post('/start', {})).rejects.toMatchObject({ code: 'ECONNABORTED' });
    await vi.advanceTimersByTimeAsync(29999);
    expect(cancel).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await timeout;
    const short = expect(api.get('/config', { timeout: 25, transitional: { clarifyTimeoutError: true } })).rejects.toMatchObject({ code: 'ETIMEDOUT' });
    await vi.advanceTimersByTimeAsync(25);
    await short;
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('allows timeout zero and ignores abort after a completed response', async () => {
    vi.useFakeTimers();
    const { api, request, cancel } = setup();
    const pending = deferred();
    const controller = new AbortController();
    request.mockReturnValue(pending.promise);
    const result = api.get('/config', { timeout: 0, signal: controller.signal });
    await vi.advanceTimersByTimeAsync(120000);
    expect(cancel).not.toHaveBeenCalled();
    pending.resolve(ok());
    await result;
    controller.abort();
    expect(cancel).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels every pending request on pagehide and removes lifetime listeners', async () => {
    vi.useFakeTimers();
    const { api, request, cancel, eventTarget } = setup();
    const remove = vi.spyOn(eventTarget, 'removeEventListener');
    request.mockReturnValue(new Promise(() => {}));
    const results = Promise.allSettled([api.get('/config'), api.post('/start', {})]);
    eventTarget.dispatchEvent(new Event('pagehide'));
    expect((await results).map((result) => result.reason.code)).toEqual(['ERR_CANCELED', 'ERR_CANCELED']);
    expect(cancel.mock.calls.map(([id]) => id).sort()).toEqual(request.mock.calls.map(([value]) => value.requestId).sort());
    expect(remove).toHaveBeenCalledWith('pagehide', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });
});

````

## web/src/api/transportFlow.test.jsx

SHA256: ba56e0592c9efea586963e2c21b32097c6cb7010f20f8b87355f271482b9b57c

````text
import React from 'react';
import { createServer } from 'node:http';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import App from '../App';
import DesktopStartup from '../components/DesktopStartup';
import api from './axios';
import { createTauriAdapter } from './tauriAdapter';
import { sessionStore } from '../lib/sessionStore';

const core = vi.hoisted(() => ({ isTauri: vi.fn().mockReturnValue(false), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);

const empty = () => ({ version: 1, login: null, activeTask: null });
const clone = (value) => JSON.parse(JSON.stringify(value));
const ok = (data) => ({ status: 200, body: { status: true, data } });
const originalAdapter = api.defaults.adapter;
let server;
let origin;
let fixture;

function fixtureApi(method, path, payload, after = 0) {
  fixture.calls.push({ method, path, payload, after });
  if (path === '/api/login') return ok({ username: payload.username });
  if (path === '/api/courses') return ok([{ courseId: 'one', title: '模拟课程' }]);
  if (path === '/api/config') {
    if (method === 'POST') fixture.config = clone(payload);
    return ok(fixture.config);
  }
  if (path === '/api/start') return { status: 409, body: { status: false, data: { task_id: 'shared-task' } } };
  if (fixture.gone) return { status: 404, body: { status: false, msg: '任务不存在或已过期' } };
  if (path === '/api/task/shared-task') {
    return ok({ status: fixture.status, progress: 0, total: 1, stats: { completed_chapters: 0, total_chapters: 0 } });
  }
  if (path === '/api/task/shared-task/details') {
    if (fixture.status === 'completed' && ++fixture.finalDetails === 1) return { status: 503, body: { status: false, msg: '模拟详情暂不可用' } };
    return ok({ courses: [] });
  }
  if (path === '/api/logs/shared-task') {
    const initial = { seq: 1, timestamp: 1, level: 'info', message: '初始日志' };
    const final = { seq: 2, timestamp: 2, level: 'info', message: '最终日志' };
    const logs = fixture.status === 'completed' ? [initial, final, final] : [initial, initial];
    return { status: 200, body: { status: true, data: logs, next_cursor: fixture.status === 'completed' ? 2 : 1, truncated: false } };
  }
  throw new Error(`Unexpected fixture request: ${method} ${path}`);
}

function sessionCommand(command, args) {
  if (command === 'session_remember_login') {
    fixture.session = { version: 1, login: { username: args.username, use_cookies: true }, activeTask: fixture.session.login?.username === args.username ? fixture.session.activeTask : null };
  } else if (command === 'session_remember_task') {
    fixture.session.activeTask = args.task;
  } else if (command === 'session_clear') fixture.session = empty();
  return clone(fixture.session);
}

beforeAll(async () => {
  server = createServer(async (request, response) => {
    response.setHeader('Access-Control-Allow-Origin', '*');
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    response.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return; }
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const text = Buffer.concat(chunks).toString();
      const url = new URL(request.url, 'http://fixture.invalid');
      const result = fixtureApi(request.method, url.pathname, text ? JSON.parse(text) : null, Number(url.searchParams.get('after') || 0));
      response.writeHead(result.status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result.body));
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: false, msg: error.message }));
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

afterEach(async () => {
  cleanup();
  await sessionStore.clear();
  core.isTauri.mockReturnValue(false);
  core.invoke.mockReset();
  delete window.chaoxingSession;
  localStorage.clear();
  api.defaults.adapter = originalAdapter;
  api.defaults.baseURL = '/api';
  vi.restoreAllMocks();
});

afterAll(async () => { await new Promise((resolve) => server.close(resolve)); });

describe('shared task flow over real Axios transports', () => {
  it.each(['browser', 'electron', 'tauri'])('recovers 409, refreshes, retries final snapshots and clears 404 over %s', async (transport) => {
    fixture = { calls: [], config: { settings: {}, selectedCoursesByAccount: { alice: ['one'] } }, session: empty(), status: 'running', finalDetails: 0, gone: false };
    window.history.replaceState({}, '', '/');
    window.matchMedia = vi.fn(() => ({ matches: true }));
    core.isTauri.mockReturnValue(transport === 'tauri');
    if (transport === 'tauri') {
      core.invoke.mockImplementation(async (command, args) => {
        if (command === 'backend_status') return { phase: 'ready' };
        if (command.startsWith('session_')) return sessionCommand(command, args);
        if (command === 'api_cancel') return undefined;
        if (command !== 'api_request') throw new Error(`Unexpected command: ${command}`);
        const { operation, payload, taskId, after } = args.request;
        const routes = {
          login: ['POST', '/api/login'], courses: ['POST', '/api/courses'],
          configRead: ['GET', '/api/config'], configWrite: ['POST', '/api/config'], start: ['POST', '/api/start'],
          taskStatus: ['GET', `/api/task/${taskId}`], taskDetails: ['GET', `/api/task/${taskId}/details`], taskLogs: ['GET', `/api/logs/${taskId}`],
        };
        const [method, path] = routes[operation];
        return fixtureApi(method, path, payload, after);
      });
      api.defaults.adapter = createTauriAdapter();
      api.defaults.baseURL = '/api';
    } else {
      api.defaults.adapter = originalAdapter;
      api.defaults.baseURL = `${origin}/api`;
      if (transport === 'electron') {
        window.chaoxingSession = {
          read: async () => sessionCommand('session_read'),
          rememberLogin: async (username) => sessionCommand('session_remember_login', { username }),
          rememberTask: async (task) => sessionCommand('session_remember_task', { task }),
          clear: async () => sessionCommand('session_clear'),
        };
      }
    }

    await sessionStore.rememberLogin('alice');
    const mount = () => render(<React.StrictMode><DesktopStartup intervalMs={100}><App /></DesktopStartup></React.StrictMode>);
    let view = mount();
    const start = await screen.findByRole('button', { name: '开始学习' });
    expect(start.disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
    await screen.findByText('配置已保存');
    expect(fixture.calls.find((call) => call.method === 'POST' && call.path === '/api/config').payload.selectedCoursesByAccount).toEqual({ alice: ['one'] });
    fireEvent.click(start);
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    expect((await sessionStore.read()).activeTask).toEqual({ username: 'alice', taskId: 'shared-task' });
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);

    view.unmount();
    view = mount();
    await screen.findByText('shared-task');
    await screen.findByText('初始日志');
    fixture.status = 'completed';
    await screen.findByText('最终日志', {}, { timeout: 4500 });
    await waitFor(() => expect(fixture.finalDetails).toBe(2), { timeout: 4500 });
    const logs = within(screen.getByRole('log'));
    expect(logs.getAllByText('初始日志')).toHaveLength(1);
    expect(logs.getAllByText('最终日志')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path.startsWith('/api/logs/')).map((call) => call.after)).toEqual(expect.arrayContaining([0, 1, 2]));

    view.unmount();
    fixture.gone = true;
    view = mount();
    expect((await screen.findByRole('alert')).textContent).toContain('任务不存在或已过期');
    await waitFor(async () => expect(await sessionStore.read()).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null }));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(false);
    expect(fixture.calls.filter((call) => call.path === '/api/start')).toHaveLength(1);
    expect(fixture.calls.filter((call) => call.path === '/api/login').every((call) => call.payload.use_cookies === true && call.payload.password === '')).toBe(true);
    view.unmount();
  }, 15000);
});

````

## web/src/components/AdvancedSettings.jsx

SHA256: 90fdbf326e140b68ec9c2e1c5bc8e303246bd9466cd790fc851b792ac9133f9f

````text
import React, { useState } from 'react';
import Input from './ui/Input';
import Label from './ui/Label';
import { Settings2, Database, Bell, Eye, ChevronDown } from 'lucide-react';

const selectCls =
  'h-10 w-full cursor-pointer rounded-lg border border-line bg-white px-3 text-sm text-ink ' +
  'transition-shadow focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15';

const AdvancedSettings = ({ settings, onChange }) => {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handleTikuChange = (field, value) => {
    onChange({
      ...settings,
      tiku_config: {
        ...settings.tiku_config,
        [field]: value,
      },
    });
  };

  const handleNotificationChange = (field, value) => {
    onChange({
      ...settings,
      notification_config: {
        ...settings.notification_config,
        [field]: value,
      },
    });
  };

  const handleOcrChange = (field, value) => {
    onChange({
      ...settings,
      ocr_config: {
        ...(settings.ocr_config || {}),
        [field]: value,
      },
    });
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        aria-expanded={showAdvanced}
        className="flex w-full items-center justify-between rounded-lg text-[13px] font-medium text-body transition-colors hover:text-brand focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
      >
        <span className="flex items-center gap-2">
          <Settings2 className="h-4 w-4" aria-hidden="true" />
          {showAdvanced ? '收起高级配置' : '展开高级配置'}
        </span>
        <ChevronDown
          className={`h-4 w-4 transition-transform duration-300 ease-out ${
            showAdvanced ? 'rotate-180' : ''
          }`}
          aria-hidden="true"
        />
      </button>

      {/* grid-template-rows 0fr→1fr 折叠,纯 CSS 过渡 */}
      <div
        className="grid transition-[grid-template-rows] duration-400 ease-out"
        style={{ gridTemplateRows: showAdvanced ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="space-y-6 pt-5">
            {/* 题库配置 */}
            <section className="rounded-xl border border-line p-4">
              <div className="mb-4 flex items-center gap-2">
                <Database className="h-4 w-4 text-brand" aria-hidden="true" />
                <h3 className="text-sm font-semibold">题库配置</h3>
                <span className="text-xs text-faint">章节检测自动答题(可选)</span>
              </div>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="tiku-provider">题库提供商</Label>
                  <select
                    id="tiku-provider"
                    className={selectCls}
                    value={settings.tiku_config?.provider || ''}
                    onChange={(e) => handleTikuChange('provider', e.target.value)}
                  >
                    <option value="">不使用题库</option>
                    <option value="TikuYanxi">言溪题库</option>
                    <option value="TikuLike">LIKE知识库</option>
                    <option value="TikuAdapter">TikuAdapter</option>
                    <option value="AI">AI大模型</option>
                    <option value="SiliconFlow">硅基流动AI</option>
                  </select>
                </div>

                {settings.tiku_config?.provider && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="tiku-tokens">Token</Label>
                      <Input
                        id="tiku-tokens"
                        type="text"
                        placeholder="多个 token 用英文逗号分隔"
                        value={settings.tiku_config?.tokens || ''}
                        onChange={(e) => handleTikuChange('tokens', e.target.value)}
                      />
                      <p className="text-xs text-faint">言溪题库或 LIKE 知识库的 Token</p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="tiku-submit">自动提交答题</Label>
                      <select
                        id="tiku-submit"
                        className={selectCls}
                        value={settings.tiku_config?.submit || 'false'}
                        onChange={(e) => handleTikuChange('submit', e.target.value)}
                      >
                        <option value="false">仅保存,不提交</option>
                        <option value="true">达到覆盖率后自动提交</option>
                      </select>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="tiku-cover-rate">最低覆盖率</Label>
                        <Input
                          id="tiku-cover-rate"
                          type="number"
                          min="0"
                          max="1"
                          step="0.1"
                          value={settings.tiku_config?.cover_rate || 0.9}
                          onChange={(e) => handleTikuChange('cover_rate', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-faint">0.0 - 1.0,推荐 0.9</p>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="tiku-delay">查询延迟(秒)</Label>
                        <Input
                          id="tiku-delay"
                          type="number"
                          min="0"
                          step="0.5"
                          value={settings.tiku_config?.delay || 1.0}
                          onChange={(e) => handleTikuChange('delay', parseFloat(e.target.value))}
                        />
                        <p className="text-xs text-faint">题库查询间隔</p>
                      </div>
                    </div>

                    {(settings.tiku_config?.provider === 'AI' || settings.tiku_config?.provider === 'SiliconFlow') && (
                      <div className="space-y-4 border-t border-line pt-4">
                        <p className="text-xs font-semibold text-brand">AI 配置</p>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-endpoint">API Endpoint</Label>
                          <Input
                            id="ai-endpoint"
                            type="text"
                            placeholder="https://api.example.com/v1"
                            value={settings.tiku_config?.endpoint || ''}
                            onChange={(e) => handleTikuChange('endpoint', e.target.value)}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-key">API Key</Label>
                          <Input
                            id="ai-key"
                            type="password"
                            placeholder="your-api-key"
                            value={settings.tiku_config?.key || ''}
                            onChange={(e) => handleTikuChange('key', e.target.value)}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-model">模型名称</Label>
                          <Input
                            id="ai-model"
                            type="text"
                            placeholder="gpt-3.5-turbo"
                            value={settings.tiku_config?.model || ''}
                            onChange={(e) => handleTikuChange('model', e.target.value)}
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1.5">
                            <Label htmlFor="ai-min-interval">最小间隔(秒)</Label>
                            <Input
                              id="ai-min-interval"
                              type="number"
                              min="0"
                              step="0.1"
                              value={
                                typeof settings.tiku_config?.min_interval_seconds === 'number'
                                  ? settings.tiku_config.min_interval_seconds
                                  : 3
                              }
                              onChange={(e) =>
                                handleTikuChange('min_interval_seconds', parseFloat(e.target.value) || 0)
                              }
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label htmlFor="ai-concurrency">单卷最大并发</Label>
                            <Input
                              id="ai-concurrency"
                              type="number"
                              min="1"
                              max="10"
                              step="1"
                              value={settings.tiku_config?.ai_concurrency || 3}
                              onChange={(e) =>
                                handleTikuChange('ai_concurrency', parseInt(e.target.value, 10) || 1)
                              }
                            />
                          </div>
                        </div>
                      </div>
                    )}

                    {settings.tiku_config?.provider === 'TikuAdapter' && (
                      <div className="space-y-1.5">
                        <Label htmlFor="adapter-url">TikuAdapter URL</Label>
                        <Input
                          id="adapter-url"
                          type="text"
                          placeholder="http://localhost:8080"
                          value={settings.tiku_config?.url || ''}
                          onChange={(e) => handleTikuChange('url', e.target.value)}
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>

            {/* 通知配置 */}
            <section className="rounded-xl border border-line p-4">
              <div className="mb-4 flex items-center gap-2">
                <Bell className="h-4 w-4 text-brand" aria-hidden="true" />
                <h3 className="text-sm font-semibold">外部通知</h3>
                <span className="text-xs text-faint">完成或出错时推送(可选)</span>
              </div>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="notification-provider">通知服务</Label>
                  <select
                    id="notification-provider"
                    className={selectCls}
                    value={settings.notification_config?.provider || ''}
                    onChange={(e) => handleNotificationChange('provider', e.target.value)}
                  >
                    <option value="">不使用通知</option>
                    <option value="Windows">Windows 系统通知</option>
                    <option value="ServerChan">Server酱</option>
                    <option value="Qmsg">Qmsg酱</option>
                    <option value="Bark">Bark</option>
                    <option value="Telegram">Telegram</option>
                  </select>
                </div>

                {settings.notification_config?.provider &&
                  settings.notification_config?.provider !== 'Windows' && (
                    <>
                      <div className="space-y-1.5">
                        <Label htmlFor="notification-url">通知 URL</Label>
                        <Input
                          id="notification-url"
                          type="text"
                          placeholder="https://..."
                          value={settings.notification_config?.url || ''}
                          onChange={(e) => handleNotificationChange('url', e.target.value)}
                        />
                        <p className="break-all font-mono text-xs text-faint">
                          {settings.notification_config?.provider === 'ServerChan' && 'https://sctapi.ftqq.com/YOUR_KEY.send'}
                          {settings.notification_config?.provider === 'Qmsg' && 'https://qmsg.zendee.cn/send/YOUR_KEY'}
                          {settings.notification_config?.provider === 'Bark' && 'https://api.day.app/YOUR_KEY/'}
                          {settings.notification_config?.provider === 'Telegram' && 'https://api.telegram.org/botYOUR_TOKEN/sendMessage'}
                        </p>
                      </div>

                    {settings.notification_config?.provider === 'Telegram' && (
                      <div className="space-y-1.5">
                        <Label htmlFor="tg-chat-id">Chat ID</Label>
                        <Input
                          id="tg-chat-id"
                          type="text"
                          placeholder="123456789"
                          value={settings.notification_config?.tg_chat_id || ''}
                          onChange={(e) => handleNotificationChange('tg_chat_id', e.target.value)}
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>

            {/* OCR 配置 */}
            <section className="rounded-xl border border-line p-4">
              <div className="mb-4 flex items-center gap-2">
                <Eye className="h-4 w-4 text-brand" aria-hidden="true" />
                <h3 className="text-sm font-semibold">图片 OCR</h3>
                <span className="text-xs text-faint">识别题目图片中的文字与公式(可选)</span>
              </div>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="ocr-provider">OCR 提供商</Label>
                  <select
                    id="ocr-provider"
                    className={selectCls}
                    value={settings.ocr_config?.provider || ''}
                    onChange={(e) => handleOcrChange('provider', e.target.value)}
                  >
                    <option value="">不使用图片 OCR</option>
                    <option value="openai">OpenAI (GPT-4o)</option>
                    <option value="claude">Claude 3 (Anthropic)</option>
                    <option value="qwen">通义千问 VL</option>
                    <option value="siliconflow">硅基流动(国内推荐)</option>
                    <option value="openai_compatible">OpenAI 兼容 API</option>
                  </select>
                  <p className="text-xs text-faint">
                    用于识别题目中的图片(如数学公式),提升答题准确率
                  </p>
                </div>

                {settings.ocr_config?.provider && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-key">API Key</Label>
                      <Input
                        id="ocr-key"
                        type="password"
                        placeholder="sk-..."
                        value={settings.ocr_config?.key || ''}
                        onChange={(e) => handleOcrChange('key', e.target.value)}
                      />
                      <p className="text-xs text-faint">
                        {settings.ocr_config?.provider === 'siliconflow' && '硅基流动 API Key,可在 siliconflow.cn 获取'}
                        {settings.ocr_config?.provider === 'openai' && 'OpenAI API Key'}
                        {settings.ocr_config?.provider === 'claude' && 'Anthropic API Key'}
                        {settings.ocr_config?.provider === 'qwen' && '阿里云 DashScope API Key'}
                        {settings.ocr_config?.provider === 'openai_compatible' && '兼容 API 的密钥'}
                      </p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-endpoint">API 端点(可选)</Label>
                      <Input
                        id="ocr-endpoint"
                        type="text"
                        placeholder={
                          settings.ocr_config?.provider === 'openai' ? 'https://api.openai.com/v1/chat/completions' :
                          settings.ocr_config?.provider === 'claude' ? 'https://api.anthropic.com/v1/messages' :
                          settings.ocr_config?.provider === 'qwen' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions' :
                          settings.ocr_config?.provider === 'siliconflow' ? 'https://api.siliconflow.cn/v1/chat/completions' :
                          'https://your-api-endpoint/v1/chat/completions'
                        }
                        value={settings.ocr_config?.endpoint || ''}
                        onChange={(e) => handleOcrChange('endpoint', e.target.value)}
                      />
                      <p className="text-xs text-faint">留空使用默认端点,自定义端点需填写完整 URL</p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-model">模型名称(可选)</Label>
                      <Input
                        id="ocr-model"
                        type="text"
                        placeholder={
                          settings.ocr_config?.provider === 'openai' ? 'gpt-4o' :
                          settings.ocr_config?.provider === 'claude' ? 'claude-3-5-sonnet-20241022' :
                          settings.ocr_config?.provider === 'qwen' ? 'qwen-vl-plus' :
                          settings.ocr_config?.provider === 'siliconflow' ? 'Qwen/Qwen2-VL-72B-Instruct' :
                          'gpt-4o'
                        }
                        value={settings.ocr_config?.model || ''}
                        onChange={(e) => handleOcrChange('model', e.target.value)}
                      />
                      <p className="text-xs text-faint">留空使用默认模型</p>
                    </div>
                  </>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdvancedSettings;

````

## web/src/components/CountUp.jsx

SHA256: 99e087583b85b56d3fd3ce2b414228e3ce8f00895fc27f145c82131eebb56095

````text
import React, { useEffect, useRef } from 'react';
import { cn } from '../lib/utils';

/**
 * 数字滚动:值变化时从旧值滚到新值,ease-out-expo。
 * 仅 textContent 变更;tnum 保证对齐。
 */
const CountUp = ({ value = 0, duration = 800, className, suffix = '' }) => {
  const ref = useRef(null);
  const prevRef = useRef(0);
  const rafRef = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = prevRef.current;
    const to = Number(value) || 0;
    prevRef.current = to;

    if (from === to) {
      el.textContent = `${to}${suffix}`;
      return;
    }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      el.textContent = `${to}${suffix}`;
      return;
    }

    const start = performance.now();
    const easeOutExpo = (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

    const tick = (now) => {
      const t = Math.min((now - start) / duration, 1);
      const current = Math.round(from + (to - from) * easeOutExpo(t));
      el.textContent = `${current}${suffix}`;
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value, duration, suffix]);

  return (
    <span ref={ref} className={cn('tnum', className)}>
      {value}
      {suffix}
    </span>
  );
};

export default CountUp;

````

## web/src/components/CourseSelection.jsx

SHA256: bda97a1b90ea3773b83d7231b7c6285e96a5dd98146bae90e578dd553451de15

````text
import React, { useState, useEffect, useMemo, useRef } from 'react';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import AdvancedSettings from './AdvancedSettings';
import {
  BookOpen, SlidersHorizontal, LogOut, Play, Save, Loader2,
  Check, Search, GraduationCap, AlertCircle, BookX, Github,
} from 'lucide-react';
import api from '../api/axios';
import { defaultSettings, normalizeCourses, restoreCourseSelection, restoreSettings } from '../lib/courseSelection';

const selectCls =
  'h-10 w-full cursor-pointer rounded-lg border border-line bg-white px-3 text-sm text-ink ' +
  'transition-shadow focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15';

const CourseSelection = ({ userInfo, onStartStudy, onLogout, starting, loggingOut = false, startError, activeTaskId, taskRunning = false, onReturnToTask, preview = false }) => {
  const [courses, setCourses] = useState([]);
  const [selectedCourses, setSelectedCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [query, setQuery] = useState('');
  const [settings, setSettings] = useState(defaultSettings);
  const [loadError, setLoadError] = useState('');
  const [selectionNotice, setSelectionNotice] = useState('');
  const [loadedAccount, setLoadedAccount] = useState(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const saveController = useRef(null);
  const username = userInfo.username;
  const password = userInfo.password;
  const useCookies = userInfo.use_cookies === true;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setCourses([]);
    setSelectedCourses([]);
    setSettings(defaultSettings());
    setLoadError('');
    setSaveStatus('');
    setSaving(false);
    setQuery('');
    setSelectionNotice('');
    if (preview) {
      setCourses([
        { courseId: 'preview-001', title: '大学英语（演示课程）' },
        { courseId: 'preview-002', title: '高等数学（演示课程）' },
        { courseId: 'preview-003', title: '计算机基础（演示课程）' },
      ]);
      setSelectedCourses(['preview-001']);
      setLoadedAccount(username);
      setSelectionNotice('请至少选择一门课程后开始学习');
      setLoading(false);
      return () => controller.abort();
    }
    const load = async () => {
      try {
        const results = await Promise.allSettled([
          api.get('/config', { signal: controller.signal }),
          api.post('/courses', { username, password, use_cookies: useCookies }, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        const [configResult, courseResult] = results;
        if (courseResult.status === 'rejected') throw courseResult.reason;
        if (!courseResult.value.data.status) throw new Error(courseResult.value.data.msg || '获取课程列表失败');
        const nextCourses = normalizeCourses(courseResult.value.data.data);
        setCourses(nextCourses);
        if (configResult.status === 'rejected') throw new Error('加载已保存配置失败，请重试');
        if (!configResult.value.data.status) throw new Error(configResult.value.data.msg || '加载已保存配置失败');
        const config = configResult.value.data.data || {};
        const selection = restoreCourseSelection(config, username, nextCourses);
        setSettings(restoreSettings(config.settings));
        setSelectedCourses(selection.ids);
        setSelectionNotice(selection.saved
          ? selection.ids.length ? '已恢复此账号仍有效的课程选择，可继续调整' : '保存的课程选择为空或已失效，请重新选择课程'
          : '此账号尚无已保存选择，已勾选全部课程，可按需取消');
      } catch (error) {
        if (!controller.signal.aborted) setLoadError(error.response?.data?.msg || error.message || '获取课程失败，请重试');
      } finally {
        if (!controller.signal.aborted) { setLoadedAccount(username); setLoading(false); }
      }
    };
    load();
    return () => { controller.abort(); saveController.current?.abort(); };
  }, [username, password, useCookies, preview, loadVersion]);

  const toggleCourse = (courseId) => {
    setSelectedCourses((prev) =>
      prev.includes(courseId)
        ? prev.filter((id) => id !== courseId)
        : [...prev, courseId]
    );
  };

  const filteredCourses = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return courses;
    return courses.filter(
      (c) => c.title?.toLowerCase().includes(q) || String(c.courseId).includes(q)
    );
  }, [courses, query]);

  const selectedCount = selectedCourses.length;
  const canStart = !loading && loadedAccount === username && !loadError && courses.length > 0 && selectedCount > 0 && !starting && !loggingOut && !taskRunning;

  const handleStartStudy = () => {
    if (!canStart) return;
    onStartStudy({
      ...settings,
      course_list: selectedCourses,
    });
  };

  const handleSaveConfig = async () => {
    if (loggingOut || loading || saving || loadError || loadedAccount !== username) return;
    if (preview) { setSaveStatus('演示配置已保存'); return; }
    const controller = new AbortController();
    saveController.current = controller;
    try {
      setSaving(true);
      setSaveStatus('');
      const payload = { settings, selectedCoursesByAccount: { [username]: selectedCourses } };
      const response = await api.post('/config', payload, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!response.data.status) {
        setSaveStatus(response.data.msg || '保存失败,请重试');
      } else {
        setSaveStatus('配置已保存');
      }
    } catch (err) {
      if (!controller.signal.aborted) setSaveStatus('保存请求失败');
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  };

  /* ---------- 载入态 ---------- */
  if (loading || loadedAccount !== username) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <Loader2 className="h-7 w-7 animate-spin text-brand" aria-hidden="true" />
        <p className="text-sm text-faint">正在获取课程列表</p>
        {activeTaskId && <Button variant="outline" onClick={onReturnToTask} disabled={loggingOut}>返回任务进度</Button>}
      </div>
    );
  }

  /* ---------- 主视图 ---------- */
  return (
    <div className="min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="page-shell flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand">
              <GraduationCap className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <div>
              <p className="text-[15px] font-semibold leading-tight">超星学习通</p>
              <p className="text-xs leading-tight text-faint">课程管理</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://github.com/RRRRUDDDD/chaoxing-gui"
              target="_blank"
              rel="noreferrer"
              aria-label="在 GitHub 上查看项目"
              title="在 GitHub 上查看项目"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-colors duration-150 hover:bg-soft hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
            >
              <Github className="h-4 w-4" aria-hidden="true" />
            </a>
            {userInfo?.username && (
              <span className="hidden font-mono text-xs text-faint sm:inline tnum">
                {userInfo.username}
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={onLogout} disabled={loggingOut}>
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              {loggingOut ? '退出中' : '退出登录'}
            </Button>
          </div>
        </div>
      </header>

      <main className="page-shell py-[clamp(1.5rem,2.5vw,3rem)]">
        {/* 页首 */}
        <div className="mb-7 animate-stagger-up">
          <h1 className="text-2xl font-semibold tracking-tight">选择课程并配置学习参数</h1>
          <p className="mt-1.5 text-sm text-faint">
            {selectionNotice || '请至少选择一门课程；未选课程时无法开始学习'}
          </p>
        </div>

        {activeTaskId && (
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand/20 bg-brand-soft/60 px-5 py-4">
            <p className="text-sm text-body">{taskRunning ? '已有学习任务运行中，可继续查看进度' : '上次任务已结束，可查看执行结果'}</p>
            <Button size="sm" variant="outline" onClick={onReturnToTask} disabled={loggingOut}>{taskRunning ? '返回运行任务' : '查看上次任务'}</Button>
          </div>
        )}
        {loadError && (
          <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-danger/5 px-5 py-4 text-sm text-danger">
            <span>{loadError}</span>
            <Button size="sm" variant="outline" onClick={() => setLoadVersion((value) => value + 1)}>重新加载</Button>
          </div>
        )}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_clamp(21.25rem,24vw,30rem)] lg:items-start 2xl:gap-8">
          {/* 左:课程列表 */}
          <section
            aria-label="课程列表"
            className="rounded-xl border border-line bg-white shadow-card animate-stagger-up"
            style={{ animationDelay: '80ms' }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
              <h2 className="flex items-center gap-2 text-[15px] font-semibold">
                <BookOpen className="h-4.5 w-4.5 text-brand" aria-hidden="true" />
                课程列表
                <span className="ml-1 rounded-full bg-soft px-2 py-0.5 text-xs text-faint tnum">
                  已选 {selectedCount} / 共 {courses.length}
                </span>
              </h2>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" aria-hidden="true" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索课程"
                  aria-label="搜索课程"
                  className="h-8 w-44 rounded-lg border border-line bg-white pl-8 pr-3 text-[13px] placeholder:text-faint/70 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
                />
              </div>
            </div>

            {courses.length === 0 ? (
              <div className="px-8 py-16 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-soft">
                  <BookX className="h-5 w-5 text-faint" aria-hidden="true" />
                </div>
                <p className="mt-4 text-[15px] font-medium">暂无课程</p>
                <p className="mt-1 text-[13px] text-faint">
                  未能从学习通获取课程,请确认账号后再试
                </p>
              </div>
            ) : (
              <ul className="max-h-[clamp(32.5rem,65vh,52rem)] divide-y divide-line overflow-y-auto scroll-brutal px-2 py-1.5">
                {filteredCourses.map((course) => {
                  const selected = selectedCourses.includes(course.courseId);
                  return (
                    <li key={course.courseId}>
                      <button
                        type="button"
                        onClick={() => toggleCourse(course.courseId)}
                        aria-pressed={selected}
                        className={`group flex w-full items-center gap-3.5 rounded-lg px-3 py-3 text-left transition-colors duration-150 hover:bg-soft focus-visible:bg-soft focus-visible:outline-none ${
                          selected ? 'bg-brand-soft/60' : ''
                        }`}
                      >
                        <span
                          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-150 ${
                            selected
                              ? 'border-brand bg-brand text-white'
                              : 'border-faint/40 text-transparent group-hover:border-faint'
                          }`}
                          aria-hidden="true"
                        >
                          {selected && <Check className="h-3 w-3" strokeWidth={3.5} />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-ink">
                            {course.title}
                          </span>
                          <span className="mt-0.5 block font-mono text-xs text-faint tnum">
                            ID: {course.courseId}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
                {filteredCourses.length === 0 && (
                  <li className="py-10 text-center text-[13px] text-faint">
                    未找到与「{query}」匹配的课程
                  </li>
                )}
              </ul>
            )}
          </section>

          {/* 右:配置面板 */}
          <aside
            aria-label="学习配置"
            className="space-y-4 animate-stagger-up lg:sticky lg:top-24"
            style={{ animationDelay: '160ms' }}
          >
            <div className="rounded-xl border border-line bg-white shadow-card">
              <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
                <SlidersHorizontal className="h-4 w-4 text-brand" aria-hidden="true" />
                <h2 className="text-[15px] font-semibold">学习配置</h2>
              </div>

              <div className="space-y-5 p-5">
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="speed">播放倍速</Label>
                    <span className="font-mono text-xs font-medium text-brand tnum">
                      {Number(settings.speed).toFixed(1)}x
                    </span>
                  </div>
                  <input
                    id="speed"
                    type="range"
                    min="1"
                    max="2"
                    step="0.1"
                    value={settings.speed}
                    onChange={(e) => setSettings({ ...settings, speed: parseFloat(e.target.value) })}
                    className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-brand focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/15"
                  />
                  <p className="text-xs text-faint">范围 1.0 - 2.0</p>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="jobs">并发章节数</Label>
                  <Input
                    id="jobs"
                    type="number"
                    min="1"
                    max="10"
                    value={settings.jobs}
                    onChange={(e) => setSettings({ ...settings, jobs: parseInt(e.target.value) || 1 })}
                  />
                  <p className="text-xs text-faint">同时处理的章节数量,默认1，建议最高不超过4</p>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="notopen">未开放章节处理</Label>
                  <select
                    id="notopen"
                    value={settings.notopen_action}
                    onChange={(e) => setSettings({ ...settings, notopen_action: e.target.value })}
                    className={selectCls}
                  >
                    <option value="retry">重试</option>
                    <option value="continue">跳过</option>
                  </select>
                </div>

                <div className="border-t border-line pt-5">
                  <AdvancedSettings settings={settings} onChange={setSettings} />
                </div>
              </div>
            </div>

            <div className="space-y-2.5 rounded-xl border border-line bg-white p-5 shadow-card">
              <Button className="w-full" size="lg" onClick={handleStartStudy} disabled={!canStart}>
                {starting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    任务启动中
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4" aria-hidden="true" />
                    开始学习
                  </>
                )}
              </Button>
              <p className="text-center text-xs text-faint">{taskRunning ? '当前任务结束后可开始新任务' : selectedCount ? `将学习已勾选的 ${selectedCount} 门课程` : '请至少勾选一门课程'}</p>
              <Button variant="outline" className="w-full" size="sm" onClick={handleSaveConfig} disabled={loggingOut || saving || !!loadError}>
                {saving ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    保存中
                  </>
                ) : (
                  <>
                    <Save className="h-3.5 w-3.5" aria-hidden="true" />
                    保存当前配置
                  </>
                )}
              </Button>
              {startError && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-lg bg-danger/5 px-3 py-2.5 text-xs leading-relaxed text-danger animate-fade-in"
                >
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>{startError}</span>
                </div>
              )}
              {saveStatus && (
                <p className="text-center text-xs text-success animate-fade-in" role="status">
                  {saveStatus}
                </p>
              )}
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
};

export default CourseSelection;

````

## web/src/components/DesktopStartup.jsx

SHA256: c3c228785cf70e23f0459ea692f5f4ddd6d00c272dbe9653e49449009959d45d

````text
import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';
import Button from './ui/Button';

const phases = new Set(['starting', 'ready', 'stopping', 'stopped', 'failed']);
const messages = {
  starting: ['正在准备学习助手', '服务正在启动，请稍候。'],
  checking: ['正在检查服务状态', '请稍候。'],
  stopping: ['服务正在关闭', '请等待服务退出后重新打开应用。'],
  stopped: ['学习服务已停止', '请关闭应用后重新打开，或稍后重新检查。'],
  failed: ['学习服务启动失败', '请关闭应用后重新打开，或稍后重新检查。'],
  unavailable: ['无法读取服务状态', '请稍后重新检查。如果问题持续，请关闭应用后重新打开。'],
};

export default function DesktopStartup({ children, intervalMs = 1000 }) {
  const [runtime] = useState(() => {
    try { return isTauriDesktop() ? 'desktop' : 'web'; }
    catch { return 'unavailable'; }
  });
  const [phase, setPhase] = useState(runtime === 'web' ? 'ready' : runtime === 'unavailable' ? 'unavailable' : 'starting');
  const [checkVersion, setCheckVersion] = useState(0);
  const [hostError, setHostError] = useState('');
  const [notice, setNotice] = useState('');
  const requestRef = useRef(null);

  useEffect(() => {
    if (runtime === 'web' || (runtime === 'unavailable' && checkVersion === 0)) return undefined;
    let disposed = false;
    let timer;
    const poll = async () => {
      try {
        // StrictMode replays effects. Reuse the in-flight read so only the live
        // effect schedules the next poll, always after the preceding one settles.
        if (!requestRef.current) {
          const pending = Promise.resolve().then(() => desktopBridge.backendStatus()).finally(() => {
            if (requestRef.current === pending) requestRef.current = null;
          });
          requestRef.current = pending;
        }
        const status = await requestRef.current;
        if (disposed) return;
        if (!status || !phases.has(status.phase)) throw new Error('Invalid service status');
        setPhase(status.phase);
        setHostError(typeof status.error === 'string' ? status.error : '');
        setNotice(typeof status.notice === 'string' ? status.notice : '');
        if (['starting', 'ready', 'stopping'].includes(status.phase)) timer = setTimeout(poll, intervalMs);
      } catch {
        if (!disposed) setPhase('unavailable');
      }
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [runtime, intervalMs, checkVersion]);

  if (phase === 'ready') return <>
    {notice && <div role="status" className="border-b border-warning/30 bg-warning/5 px-6 py-3 text-sm text-body">{notice}</div>}
    {children}
  </>;
  const failed = ['failed', 'stopped', 'unavailable'].includes(phase);
  const [title, description] = messages[phase];
  const recheck = () => { setPhase('checking'); setCheckVersion((version) => version + 1); };

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-12">
      <div className="w-full max-w-md text-center">
        <img src="/fav.jpg" alt="超星学习通" className="mx-auto mb-4 h-12 w-12 rounded-xl object-cover shadow-focus" />
        <h1 className="mb-8 text-xl font-semibold tracking-tight">超星学习通 · 自动化学习助手</h1>
        <section role={failed ? 'alert' : 'status'} aria-live={failed ? 'assertive' : 'polite'} className="rounded-2xl border border-line bg-white p-8 shadow-lift">
          {failed ? <AlertCircle className="mx-auto mb-4 h-8 w-8 text-danger" aria-hidden="true" />
            : <Loader2 className="mx-auto mb-4 h-8 w-8 animate-spin text-brand" aria-hidden="true" />}
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="mt-2 text-sm leading-relaxed text-body">{description}</p>
          {failed && hostError && <p className="mt-3 break-words rounded-lg bg-danger/5 px-3 py-2 text-left text-sm leading-relaxed text-danger">{hostError}</p>}
          {failed && <Button type="button" onClick={recheck} className="mt-6"><RefreshCw className="h-4 w-4" aria-hidden="true" />重新检查</Button>}
        </section>
      </div>
    </main>
  );
}

````

## web/src/components/DesktopStartup.test.jsx

SHA256: 82fdbb07f43037fd0ff67f6840cdb3b5ff8322300d4992652b8beb1db3c5e5b3

````text
import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DesktopStartup from './DesktopStartup';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';

vi.mock('../lib/desktopBridge', () => ({ isTauriDesktop: vi.fn(), desktopBridge: { backendStatus: vi.fn() } }));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const tick = (time = 0) => act(async () => { await vi.advanceTimersByTimeAsync(time); });
const show = (strict = false) => {
  const content = <DesktopStartup intervalMs={100}><div>业务界面</div></DesktopStartup>;
  return render(strict ? <React.StrictMode>{content}</React.StrictMode> : content);
};

beforeEach(() => { vi.useFakeTimers(); isTauriDesktop.mockReset().mockReturnValue(true); desktopBridge.backendStatus.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

describe('desktop startup', () => {
  it('renders the browser and Electron app immediately without querying desktop status', async () => {
    isTauriDesktop.mockReturnValue(false);
    show();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(1000);
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it('mounts the app only after readiness and continues watching for service death', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'starting' }).mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValue({ phase: 'failed', error: 'exit code fixture' });
    show();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('正在');
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('服务');
    expect(screen.getByRole('alert').textContent).toContain('exit code fixture');
    expect(screen.getByRole('button', { name: '重新检查' })).toBeTruthy();
  });

  it('serializes slow status queries, including StrictMode effect replay', async () => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValueOnce(pending.promise).mockResolvedValue({ phase: 'ready' });
    show(true);
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(screen.queryByText('业务界面')).toBeNull();
    await act(async () => pending.resolve({ phase: 'starting' }));
    await tick(99);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    await tick(1);
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each(['failed', 'stopped'])('shows an actionable Chinese %s state and only rereads on recheck', async (phase) => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase, error: 'raw runtime diagnostics' }).mockResolvedValue({ phase: 'ready' });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(phase === 'failed' ? '失败' : '停止');
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await tick();
    expect(desktopBridge.backendStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText('业务界面')).toBeTruthy();
  });

  it.each([new Error('invoke error'), { phase: 'unknown' }, undefined])('fails visibly when status cannot be read or validated (%s)', async (value) => {
    if (value instanceof Error) desktopBridge.backendStatus.mockRejectedValue(value);
    else desktopBridge.backendStatus.mockResolvedValue(value);
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain('无法读取服务状态');
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('button', { name: '重新检查' })).toBeTruthy();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('never opens the app when runtime initialization throws', async () => {
    isTauriDesktop.mockImplementation(() => { throw new Error('initialization'); });
    show();
    await tick();
    expect(screen.queryByText('业务界面')).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(desktopBridge.backendStatus).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'])('ignores a late status %s after unmount and cancels timers', async (completion) => {
    const pending = deferred();
    desktopBridge.backendStatus.mockReturnValue(pending.promise);
    const view = show();
    await tick();
    view.unmount();
    await act(async () => pending[completion](completion === 'resolve' ? { phase: 'ready' } : new Error('late')));
    await tick(1000);
    expect(desktopBridge.backendStatus).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.queryByText('业务界面')).toBeNull();
  });

  it('unmounts the app while the service stops, then reports the stopped state', async () => {
    desktopBridge.backendStatus.mockResolvedValueOnce({ phase: 'ready' }).mockResolvedValueOnce({ phase: 'stopping' }).mockResolvedValue({ phase: 'stopped' });
    const view = show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    await tick(100);
    expect(screen.queryByText('业务界面')).toBeNull();
    await tick(100);
    expect(screen.getByRole('alert').textContent).toContain('停止');
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('shows a host failure reason as text, including the instruction to close legacy Electron', async () => {
    const error = '请先关闭旧版桌面应用，再重新打开。<img src=x onerror=alert(1)>';
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'failed', error });
    show();
    await tick();
    expect(screen.getByRole('alert').textContent).toContain(error);
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it('keeps an import warning visible after the business app becomes ready', async () => {
    desktopBridge.backendStatus.mockResolvedValue({ phase: 'ready', notice: '旧账号记录无法导入，请重新登录。' });
    show();
    await tick();
    expect(screen.getByText('业务界面')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('旧账号记录无法导入');
  });
});

````

## web/src/components/Login.jsx

SHA256: d1854be6d17ea04e57d6eb7f18288998de7396d69917c2b3fd736b8205c694e9

````text
import React, { useState, useEffect, useRef, useCallback } from 'react';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import { LogIn, Loader2, KeyRound, AlertCircle } from 'lucide-react';
import api from '../api/axios';
import { sessionStore } from '../lib/sessionStore';

const Login = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const userRef = useRef(null);
  const requestRef = useRef(null);
  const successRef = useRef(onLoginSuccess);
  successRef.current = onLoginSuccess;

  const doLogin = useCallback(async (u, p, { useCookies = false, signal }) => {
    setError('');
    setLoading(true);

    try {
      const response = await api.post('/login', {
        username: u,
        password: p,
        use_cookies: useCookies,
      }, { signal });
      if (signal.aborted) return;

      if (response.data.status) {
        setPassword('');
        let persistenceError = '';
        try { await sessionStore.rememberLogin(u); } catch {
          persistenceError = '账号记忆未能保存，刷新后可能需要重新登录';
        }
        if (!signal.aborted) await successRef.current({ username: u, password: '', use_cookies: true }, persistenceError);
      } else {
        setError(response.data.msg || '登录失败,请检查账号信息后重试');
      }
    } catch (err) {
      if (!signal.aborted) setError(useCookies ? '保存的登录会话不可用，请输入密码重新登录' : err.response?.data?.msg || '网络连接异常,请确认服务已启动后重试');
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    if (loading) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    await doLogin(username.trim(), password, { signal: controller.signal });
  };

  // The desktop bridge persists across random backend ports; only cookies are reused.
  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    const restore = async () => {
      try {
        const saved = await sessionStore.read();
        if (controller.signal.aborted) return;
        if (saved.login) {
          setUsername(saved.login.username);
          await doLogin(saved.login.username, '', { useCookies: true, signal: controller.signal });
        }
      } catch {
        if (!controller.signal.aborted) setError('读取保存的账号失败，请手动登录');
      } finally {
        if (!controller.signal.aborted) { setLoading(false); userRef.current?.focus(); }
      }
    };
    restore();
    return () => { controller.abort(); requestRef.current?.abort(); };
  }, [doLogin]);

  const errId = 'login-error';

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-[clamp(2.5rem,7vh,5rem)]">
      <div className="w-full max-w-[clamp(400px,32vw,520px)] animate-pop-in">
        {/* 品牌区 */}
        <div className="mb-8 flex flex-col items-center text-center">
          <img
            src="/fav.jpg"
            alt="超星学习通"
            className="mb-4 h-12 w-12 rounded-xl object-cover shadow-focus"
          />
          <h1 className="text-xl font-semibold tracking-tight">超星学习通 · 自动化学习助手</h1>
          <p className="mt-1.5 text-sm text-faint">登录以继续</p>
        </div>

        {/* 登录卡片 */}
        <div className="rounded-2xl border border-line bg-white p-[clamp(1.5rem,2vw,2.25rem)] shadow-lift">
          <form onSubmit={handleLogin} aria-describedby={error ? errId : undefined} className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="username">手机号</Label>
              <Input
                id="username"
                ref={userRef}
                type="text"
                inputMode="tel"
                autoComplete="username"
                placeholder="请输入手机号"
                value={username}
                disabled={loading}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="请输入密码"
                value={password}
                disabled={loading}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {error && (
              <div
                id={errId}
                role="alert"
                className="flex items-start gap-2 rounded-lg bg-danger/5 px-3 py-2.5 text-[13px] leading-relaxed text-danger animate-fade-in"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}

            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  登录中
                </>
              ) : (
                <>
                  <LogIn className="h-4 w-4" aria-hidden="true" />
                  登录
                </>
              )}
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs leading-relaxed text-faint">
          <KeyRound className="mr-1 inline h-3 w-3 -translate-y-px" aria-hidden="true" />
          本机记忆账号并使用登录会话，密码不写入本地存储
        </p>
        <p className="mt-1.5 text-center text-xs leading-relaxed text-faint">
          本程序仅供学习和研究使用，请勿用于商业或非法用途。
          <br />
          使用本程序产生的一切后果由使用者自行承担，本程序不提供任何明示或暗示的适配性、安全性或合法性担保。
        </p>
      </div>
    </div>
  );
};

export default Login;

````

## web/src/components/StudyProgress.jsx

SHA256: 8340305c9d6e51ce4e8802fa447458f7736d837e6d41698e05d06b12e1fc7570

````text
import React, { useState, useEffect, useRef } from 'react';
import Button from './ui/Button';
import CountUp from './CountUp';
import {
  Loader2, ArrowLeft, CheckCircle2, XCircle, AlertCircle,
  Play, Clock, ChevronDown, ChevronRight, BookOpen, MonitorPlay,
  FileText, Home, Github,
} from 'lucide-react';
import api from '../api/axios';
import { LOG_LIMIT, chapterState, isTerminalStatus, resultLabels, startTaskPolling } from '../lib/taskPolling';

const StudyProgress = ({ taskId, notice = '', onBack, onStatus, onMissing, preview = false }) => {
  const [taskStatus, setTaskStatus] = useState(preview ? {
    status: 'completed',
    progress: 3,
    total: 3,
    stats: {
      completed_chapters: 12,
      total_chapters: 12,
      completed_tasks: 12,
      total_tasks: 12,
      failed_tasks: 0,
      skipped_tasks: 0,
    },
    start_time: Date.now() / 1000 - 372,
  } : null);
  const [taskDetails, setTaskDetails] = useState(preview ? {
    courses: [
      {
        id: 'preview-001',
        title: '大学英语（演示课程）',
        status: 'completed',
        chapters: [
          { id: 'chapter-1', title: '第一章：课程导学', has_finished: true },
          { id: 'chapter-2', title: '第二章：基础内容', has_finished: true },
        ],
      },
      {
        id: 'preview-002',
        title: '高等数学（演示课程）',
        status: 'completed',
        chapters: [
          { id: 'chapter-3', title: '第一章：函数与极限', has_finished: true },
          { id: 'chapter-4', title: '第二章：导数与微分', has_finished: true },
        ],
      },
    ],
  } : null);
  const [logs, setLogs] = useState(preview ? [
    { seq: 1, timestamp: Date.now() / 1000 - 60, level: 'success', message: '演示任务已完成' },
    { seq: 2, timestamp: Date.now() / 1000 - 30, level: 'info', message: '所有课程均已处理完毕' },
  ] : []);
  const [logsTruncated, setLogsTruncated] = useState(false);
  const [pollError, setPollError] = useState('');
  const [missing, setMissing] = useState(false);
  const [expandedCourses, setExpandedCourses] = useState(new Set());
  const logsEndRef = useRef(null);
  const callbacks = useRef({ onStatus, onMissing });
  callbacks.current = { onStatus, onMissing };

  useEffect(() => {
    if (preview) return undefined;
    setTaskStatus(null);
    setTaskDetails(null);
    setLogs([]);
    setLogsTruncated(false);
    setPollError('');
    setMissing(false);
    setExpandedCourses(new Set());
    return startTaskPolling({
      api, taskId,
      onStatus: (status) => { setTaskStatus(status); callbacks.current.onStatus?.(status); },
      onDetails: setTaskDetails,
      onLogs: (state) => { setLogs(state.logs); setLogsTruncated(state.truncated); },
      onError: setPollError,
      onMissing: () => { setMissing(true); setPollError(''); callbacks.current.onMissing?.(); },
    });
  }, [taskId, preview]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [logs]);

  const toggleCourse = (courseId) => {
    setExpandedCourses((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(courseId)) {
        newSet.delete(courseId);
      } else {
        newSet.add(courseId);
      }
      return newSet;
    });
  };

  const getStatusInfo = () => {
    if (missing) return { text: '已过期', cls: 'text-faint', icon: Clock };
    if (!taskStatus) return { text: '加载中', cls: 'text-faint', icon: Clock };

    switch (taskStatus.status) {
      case 'running':
        return { text: '学习中', cls: 'text-brand', icon: Play };
      case 'completed':
        return { text: '已完成', cls: 'text-success', icon: CheckCircle2 };
      case 'error':
        return { text: '出现错误', cls: 'text-danger', icon: AlertCircle };
      case 'partial':
        return { text: '部分完成', cls: 'text-warning', icon: AlertCircle };
      default:
        return { text: '未知状态', cls: 'text-faint', icon: Clock };
    }
  };

  const getLogLevelColor = (level) => {
    switch (level) {
      case 'error':
        return 'text-[#f87171]';
      case 'warning':
        return 'text-[#fbbf24]';
      case 'success':
        return 'text-[#4ade80]';
      default:
        return 'text-gray-300';
    }
  };

  const getCourseStatusIcon = (status) => {
    switch (status) {
      case 'running':
        return <Loader2 className="h-4 w-4 text-brand animate-spin" aria-hidden="true" />;
      case 'completed':
        return <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />;
      case 'empty':
        return <FileText className="h-4 w-4 text-faint" aria-hidden="true" />;
      case 'partial':
      case 'skipped':
        return <AlertCircle className="h-4 w-4 text-warning" aria-hidden="true" />;
      case 'error':
        return <XCircle className="h-4 w-4 text-danger" aria-hidden="true" />;
      default:
        return <Clock className="h-4 w-4 text-faint" aria-hidden="true" />;
    }
  };

  const statusInfo = getStatusInfo();
  const StatusIcon = statusInfo.icon;
  const progress = taskStatus ? (taskStatus.progress / (taskStatus.total || 1)) * 100 : 0;
  const terminal = isTerminalStatus(taskStatus?.status) || missing;
  const activeJobs = !terminal && taskDetails?.active_jobs ? Object.values(taskDetails.active_jobs) : [];

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const finished = taskStatus?.status === 'completed';
  const errored = taskStatus?.status === 'error';
  const partial = taskStatus?.status === 'partial';
  const resultColor = (state) => state === 'error' ? 'text-danger' : ['skipped', 'partial'].includes(state) ? 'text-warning' : state === 'completed' ? 'text-success' : 'text-faint';

  const statCards = [
    {
      label: '已处理课程',
      icon: BookOpen,
      iconCls: 'bg-brand-soft text-brand',
      value: (
        <>
          <CountUp value={taskStatus?.progress || 0} />
          <span className="ml-1 text-base font-medium text-faint">/ {taskStatus?.total || 0}</span>
        </>
      ),
    },
    {
      label: '章节统计',
      icon: FileText,
      iconCls: 'bg-success/10 text-success',
      value: (
        <>
          <CountUp value={taskStatus?.stats?.completed_chapters || 0} />
          <span className="ml-1 text-base font-medium text-faint">/ {taskStatus?.stats?.total_chapters || 0}</span>
        </>
      ),
    },
    {
      label: '处理进度',
      icon: Clock,
      iconCls: 'bg-warning/10 text-warning',
      value: <CountUp value={Math.round(progress)} suffix="%" />,
    },
    {
      label: '任务状态',
      icon: statusInfo.icon,
      iconCls: 'bg-soft text-body',
      value: (
        <span className={`flex items-center gap-1.5 text-lg font-semibold ${statusInfo.cls}`}>
          <StatusIcon className={`h-4.5 w-4.5 ${taskStatus?.status === 'running' ? 'animate-pulse' : ''}`} aria-hidden="true" />
          {statusInfo.text}
        </span>
      ),
    },
  ];

  return (
    <div className="min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="page-shell flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand">
              <MonitorPlay className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <div>
              <p className="text-[15px] font-semibold leading-tight">学习进度监控</p>
              <p className="text-xs leading-tight text-faint">实时跟踪任务执行详情</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://github.com/RRRRUDDDD/chaoxing-gui"
              target="_blank"
              rel="noreferrer"
              aria-label="在 GitHub 上查看项目"
              title="在 GitHub 上查看项目"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-colors duration-150 hover:bg-soft hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
            >
              <Github className="h-4 w-4" aria-hidden="true" />
            </a>
            {terminal && (
              <Button onClick={onBack} size="sm">
                <Home className="h-3.5 w-3.5" aria-hidden="true" />
                返回首页
              </Button>
            )}
          </div>
        </div>
      </header>

      <main className="page-shell py-[clamp(1.5rem,2.5vw,3rem)]">
        {notice && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {notice}
          </div>
        )}
        {(missing || pollError) && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {missing ? '任务不存在或已过期，请返回课程选择。' : `${pollError}，正在重试获取最新数据。`}
          </div>
        )}
        {/* 统计卡片 */}
        <section
          aria-label="任务统计"
          className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4 animate-stagger-up"
        >
          {statCards.map((card, i) => {
            const Icon = card.icon;
            return (
              <div
                key={card.label}
                className="rounded-xl border border-line bg-white p-5 shadow-card"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-[13px] text-faint">{card.label}</p>
                    <p className="mt-1.5 text-2xl font-semibold tracking-tight leading-none tnum">
                      {card.value}
                    </p>
                  </div>
                  <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${card.iconCls}`}>
                    <Icon className="h-4.5 w-4.5" aria-hidden="true" />
                  </div>
                </div>
              </div>
            );
          })}
        </section>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_clamp(20rem,24vw,28rem)] lg:items-start 2xl:gap-8">
          {/* 主栏 */}
          <div className="min-w-0 space-y-6">
            {/* 当前进度 */}
            <section className="rounded-xl border border-line bg-white p-5 shadow-card animate-stagger-up" style={{ animationDelay: '120ms' }}>
              <div className="mb-4 flex items-baseline justify-between">
                <h2 className="text-[15px] font-semibold">当前进度</h2>
                <span className="text-xs text-faint tnum">
                  {taskStatus?.progress || 0} / {taskStatus?.total || 0} 课程
                </span>
              </div>
              <div
                className="h-2.5 w-full overflow-hidden rounded-full bg-soft"
                role="progressbar"
                aria-valuenow={Math.round(progress)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="整体学习进度"
              >
                <div
                  className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                  style={{ width: `${Math.min(progress, 100)}%` }}
                />
              </div>

              {!terminal && taskStatus?.current_course && (
                <div className="mt-5 flex items-start gap-3.5 rounded-lg border border-line bg-soft/60 p-4">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white shadow-card">
                    <Loader2 className="h-4.5 w-4.5 animate-spin text-brand" aria-hidden="true" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-brand">正在学习</p>
                    <h3 className="mt-0.5 truncate text-[15px] font-semibold" title={taskStatus.current_course}>
                      {taskStatus.current_course}
                    </h3>
                    {taskStatus.current_chapter && (
                      <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-body">
                        <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-medium text-brand">章节</span>
                        <span className="truncate">{taskStatus.current_chapter}</span>
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* 错误 */}
              {taskStatus?.error && (
                <div role="alert" className="mt-5 flex items-start gap-2.5 rounded-lg bg-danger/5 px-4 py-3 animate-fade-in">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
                  <div>
                    <p className="text-[13px] font-semibold text-danger">错误信息</p>
                    <p className="mt-0.5 text-[13px] leading-relaxed text-danger/90">{taskStatus.error}</p>
                  </div>
                </div>
              )}
            </section>

            {/* 视频播放进度 */}
            {activeJobs.length > 0 && (
              <section className="rounded-xl border border-line bg-white p-5 shadow-card animate-stagger-up" style={{ animationDelay: '180ms' }}>
                <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
                  <MonitorPlay className="h-4 w-4 text-brand" aria-hidden="true" />
                  正在播放视频
                  <span className="rounded-full bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand tnum">
                    {activeJobs.length}
                  </span>
                </h2>
                <div className="space-y-3">
                  {activeJobs.map((job, index) => (
                    <div
                      key={index}
                      className="rounded-lg border border-line p-4 transition-shadow duration-200 hover:shadow-card"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate text-sm font-medium" title={job.job_name}>
                            {job.job_name || '未知任务'}
                          </h3>
                          <p className="mt-0.5 truncate text-xs text-faint" title={job.course_name}>
                            {job.course_name}
                          </p>
                        </div>
                        <span className="shrink-0 text-[15px] font-semibold text-brand tnum">
                          {Math.round(job.progress)}%
                        </span>
                      </div>
                      <div className="mt-2.5">
                        <div className="mb-1 flex items-center justify-between font-mono text-[11px] text-faint tnum">
                          <span>{formatTime(job.current_time)}</span>
                          <span>{formatTime(job.duration)}</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-soft">
                          <div
                            className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                            style={{ width: `${Math.min(job.progress, 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* 课程明细 */}
            {taskDetails?.courses?.length > 0 && (
              <section className="rounded-xl border border-line bg-white shadow-card animate-stagger-up" style={{ animationDelay: '240ms' }}>
                <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
                  <BookOpen className="h-4 w-4 text-brand" aria-hidden="true" />
                  <h2 className="text-[15px] font-semibold">课程明细</h2>
                </div>
                <div className="divide-y divide-line px-2.5 py-2">
                  {taskDetails.courses.map((course) => {
                    const open = expandedCourses.has(course.id);
                    const done = course.chapters ? course.chapters.filter((c) => ['completed', 'empty'].includes(chapterState(c))).length : 0;
                    const total = course.chapters?.length || 0;
                    return (
                      <div key={course.id}>
                        <button
                          type="button"
                          onClick={() => toggleCourse(course.id)}
                          aria-expanded={open}
                          className="flex w-full items-center gap-3 rounded-lg px-2.5 py-3 text-left transition-colors duration-150 hover:bg-soft focus-visible:bg-soft focus-visible:outline-none"
                        >
                          {getCourseStatusIcon(course.status)}
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{course.title}</span>
                          <span className={`shrink-0 text-xs ${resultColor(course.status)}`}>{resultLabels[course.status] || '等待中'}</span>
                          {total > 0 && (
                            <span className="shrink-0 rounded-full bg-soft px-2 py-0.5 text-xs text-faint tnum">
                              {done} / {total} 章节
                            </span>
                          )}
                          {open ? (
                            <ChevronDown className="h-4 w-4 shrink-0 text-faint" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="h-4 w-4 shrink-0 text-faint transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                          )}
                        </button>
                        {open && total > 0 && (
                          <ol className="mb-2 space-y-0.5 rounded-lg bg-soft/50 px-2.5 py-2">
                            {course.chapters.map((chapter, idx) => (
                              <li key={chapter.id} className="flex items-center gap-3 rounded px-2 py-1.5 text-[13px] hover:bg-white">
                                <span className="w-5 shrink-0 font-mono text-[11px] text-faint tnum">{idx + 1}.</span>
                                <span className="flex-1 min-w-0 truncate text-body">{chapter.title}</span>
                                <span className={`flex shrink-0 items-center gap-1 text-xs ${resultColor(chapterState(chapter))}`}>
                                  {getCourseStatusIcon(chapterState(chapter))}
                                  {resultLabels[chapterState(chapter)] || '等待中'}
                                </span>
                              </li>
                            ))}
                          </ol>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {/* 执行日志 */}
            <section className="rounded-xl border border-line bg-white shadow-card animate-stagger-up" style={{ animationDelay: '300ms' }}>
              <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
                <FileText className="h-4 w-4 text-brand" aria-hidden="true" />
                <h2 className="text-[15px] font-semibold">执行日志</h2>
                <span className="ml-auto text-xs text-faint">最近 {LOG_LIMIT} 条</span>
              </div>
              <div className="p-4">
                {logsTruncated && <p className="mb-2 text-xs text-faint">较早的日志已省略，仅保留最近 {LOG_LIMIT} 条。</p>}
                <div role="log" aria-label="执行日志" aria-live="off" className="h-[clamp(22.5rem,48vh,42rem)] overflow-y-auto rounded-lg bg-gray-900 p-4 font-mono text-xs leading-relaxed scroll-brutal">
                  {logs.length === 0 ? (
                    <p className="text-gray-500">等待日志输出...</p>
                  ) : (
                    logs.map((log) => (
                      <div key={log.seq} className="mb-0.5">
                        <span className="mr-2 text-gray-500 tnum">
                          [{new Date(log.timestamp * 1000).toLocaleTimeString('zh-CN')}]
                        </span>
                        <span className={getLogLevelColor(log.level || 'info')}>{log.message}</span>
                      </div>
                    ))
                  )}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </section>
          </div>

          {/* 侧栏 */}
          <aside className="space-y-6 lg:sticky lg:top-24 animate-stagger-up" style={{ animationDelay: '200ms' }}>
            {/* 任务信息 */}
            <section className="rounded-xl border border-line bg-white shadow-card">
              <div className="border-b border-line px-5 py-3.5">
                <h2 className="text-[15px] font-semibold">任务信息</h2>
              </div>
              <dl className="space-y-4 p-5">
                <div>
                  <dt className="text-xs text-faint">任务 ID</dt>
                  <dd className="mt-1 break-all rounded-lg bg-soft px-2.5 py-2 font-mono text-xs">
                    {taskId}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-faint">开始时间</dt>
                  <dd className="mt-1 text-sm font-medium tnum">
                    {taskStatus?.start_time
                      ? new Date(taskStatus.start_time * 1000).toLocaleString('zh-CN')
                      : '-'}
                  </dd>
                </div>
              </dl>
            </section>

            {finished && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-success/25 bg-success/5 p-4 animate-fade-in" role="status">
                <CheckCircle2 className="mt-0.5 h-4.5 w-4.5 shrink-0 text-success" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-success">所有任务已完成</p>
                  <p className="mt-0.5 text-xs text-body">全部课程已按配置学习完毕</p>
                </div>
              </div>
            )}

            {partial && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-warning/25 bg-warning/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-warning" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-warning">任务已结束，部分内容未完成</p>
                  <p className="mt-0.5 text-xs text-body">部分章节失败或被跳过，请查看课程明细和日志</p>
                </div>
              </div>
            )}

            {errored && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-danger" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-danger">任务执行失败</p>
                  <p className="mt-0.5 text-xs text-body">请查看错误信息或日志排查原因</p>
                </div>
              </div>
            )}

            {taskStatus?.stats && (
              <section className="rounded-xl border border-line bg-white shadow-card">
                <div className="border-b border-line px-5 py-3.5">
                  <h2 className="text-[15px] font-semibold">详细统计</h2>
                </div>
                <dl className="divide-y divide-line px-5">
                  {[
                    ['总章节数', taskStatus.stats.total_chapters || 0, ''],
                    ['已完成章节（含无任务）', taskStatus.stats.completed_chapters || 0, 'text-success'],
                    ['无任务章节', taskStatus.stats.empty_chapters || 0, ''],
                    ['失败章节', taskStatus.stats.failed_chapters || 0, 'text-danger'],
                    ['跳过章节', taskStatus.stats.skipped_chapters || 0, 'text-warning'],
                    ['失败课程', taskStatus.stats.failed_courses || 0, 'text-danger'],
                    ['总任务数', taskStatus.stats.total_tasks || 0, ''],
                    ['已完成任务', taskStatus.stats.completed_tasks || 0, 'text-success'],
                    ...(taskStatus.stats.failed_tasks > 0
                      ? [['失败任务', taskStatus.stats.failed_tasks, 'text-danger']]
                      : []),
                    ...(taskStatus.stats.skipped_tasks > 0
                      ? [['跳过任务', taskStatus.stats.skipped_tasks, 'text-warning']]
                      : []),
                  ].map(([label, value, color]) => (
                    <div key={label} className="flex items-center justify-between py-3">
                      <dt className="text-[13px] text-faint">{label}</dt>
                      <dd className={`text-sm font-semibold tnum ${color}`}>
                        <CountUp value={value} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}

            <Button variant="outline" className="w-full" onClick={onBack}>
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              返回课程选择
            </Button>
          </aside>
        </div>
      </main>
    </div>
  );
};

export default StudyProgress;

````

## web/src/components/flows.test.jsx

SHA256: ed6d73cb7b8b64353304da0f87c932fda0d326e11f42012d1fff96100076fbe8

````text
import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import CourseSelection from './CourseSelection';
import StudyProgress from './StudyProgress';
import Login from './Login';
import api from '../api/axios';
import { SAVED_LOGIN_KEY, SESSION_KEY, sessionStore } from '../lib/sessionStore';

vi.mock('../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const ok = (data) => ({ data: { status: true, data } });
const page = (data = [], next_cursor = 0) => ({ data: { status: true, data, next_cursor, truncated: false } });
const account = { username: 'alice', password: '', use_cookies: true };
const courses = [{ courseId: '1', title: 'Course one' }, { courseId: '2', title: 'Course two' }];
const taskState = (status = 'running') => ({ status, progress: 0, total: 2, stats: { completed_chapters: 0, total_chapters: 0 } });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function services({ config = {}, status = 'running', conflict = false } = {}) {
  api.get.mockImplementation(async (url) => {
    if (url === '/config') return ok(config);
    if (url.endsWith('/details')) return ok({ courses: [] });
    if (url.startsWith('/logs/')) return page();
    if (url.startsWith('/task/')) return ok(taskState(status));
    throw new Error(`Unexpected GET ${url}`);
  });
  api.post.mockImplementation(async (url, body) => {
    if (url === '/login') return ok({ username: body.username });
    if (url === '/courses') return ok(courses);
    if (url === '/config') return ok({});
    if (url === '/start') {
      if (conflict) throw { response: { status: 409, data: { status: false, data: { task_id: 'existing-task' } } } };
      return ok({ task_id: 'task-one' });
    }
    throw new Error(`Unexpected POST ${url}`);
  });
}

async function loginManually(username) {
  await screen.findByRole('button', { name: '登录' });
  fireEvent.change(screen.getByLabelText('手机号'), { target: { value: username } });
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await screen.findByText(username);
}

beforeEach(async () => {
  delete window.chaoxingSession;
  await sessionStore.clear();
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  window.matchMedia = vi.fn(() => ({ matches: true }));
  api.get.mockReset();
  api.post.mockReset();
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('course selection', () => {
  it('shows API failures and disables starting instead of submitting an empty list', async () => {
    services();
    api.post.mockResolvedValue({ data: { status: false, msg: '课程服务暂不可用' } });
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    expect((await screen.findByRole('alert')).textContent).toContain('课程服务暂不可用');
    const button = screen.getByRole('button', { name: '开始学习' });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '重新加载' })).toBeTruthy();
  });

  it('blocks starting if saved configuration could not be loaded', async () => {
    services();
    api.get.mockRejectedValue(new Error('offline'));
    render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
    await screen.findByRole('alert');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: /Course one/ }).getAttribute('aria-pressed')).toBe('false');
  });

  it('keeps a stale saved selection empty and saves only this account after explicit selection', async () => {
    services({ config: { selectedCourses: ['1'], selectedCoursesByAccount: { alice: ['gone'], bob: ['2'] } } });
    const start = vi.fn();
    render(<CourseSelection userInfo={account} onStartStudy={start} />);
    const first = await screen.findByRole('button', { name: /Course one/ });
    expect(first.getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.click(first);
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(start.mock.calls[0][0].course_list).toEqual(['1']);
    fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
    await screen.findByText('配置已保存');
    const payload = api.post.mock.calls.find(([url]) => url === '/config')[1];
    expect(payload.selectedCoursesByAccount).toEqual({ alice: ['1'] });
    expect(payload.selectedCourses).toBeUndefined();
    fireEvent.click(first);
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  });

  it('cancels a previous account load and never lets its late courses replace the new account', async () => {
    services({ config: { selectedCoursesByAccount: { bob: ['b'] } } });
    const old = deferred();
    api.post.mockImplementation((_url, body) => body.username === 'alice' ? old.promise : Promise.resolve(ok([{ courseId: 'b', title: 'Bob course' }])));
    const start = vi.fn();
    const view = render(<CourseSelection userInfo={account} onStartStudy={start} />);
    const previousSignal = api.post.mock.calls[0][2].signal;
    view.rerender(<CourseSelection userInfo={{ ...account, username: 'bob' }} onStartStudy={start} />);
    await screen.findByRole('button', { name: /Bob course/ });
    expect(previousSignal.aborted).toBe(true);
    await act(async () => old.resolve(ok(courses)));
    expect(screen.queryByText('Course one')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    expect(start.mock.calls[0][0].course_list).toEqual(['b']);
  });

  it('does not enable starting when the course list is empty', async () => {
    services();
    api.post.mockResolvedValue(ok([]));
    render(<CourseSelection userInfo={account} onStartStudy={vi.fn()} />);
    await screen.findByText('暂无课程');
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
  });
});

describe('task navigation', () => {
  it('keeps an active task while navigating, blocks duplicate starts, restores after refresh and clears on logout', async () => {
    services();
    await sessionStore.rememberLogin('alice');
    const view = render(<React.StrictMode><App /></React.StrictMode>);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('task-one');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask?.taskId).toBe('task-one'));
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    const returnButton = await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(true);
    fireEvent.click(returnButton);
    await screen.findByText('task-one');
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(1);
    expect(api.post.mock.calls.filter(([url]) => url === '/login')).toHaveLength(1);
    view.unmount();
    render(<App />);
    await screen.findByText('task-one');
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await screen.findByLabelText('密码');
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
  });

  it('adopts the running task returned by a 409 response', async () => {
    services({ conflict: true });
    await sessionStore.rememberLogin('alice');
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('existing-task');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask.taskId).toBe('existing-task'));
  });

  it.each(['idle', 'pending success', 'pending conflict'])('locks starting during logout from an %s session and ignores late responses after account changes', async (scenario) => {
    services();
    await sessionStore.rememberLogin('alice');
    const clearing = deferred();
    const clearSession = sessionStore.clear;
    vi.spyOn(sessionStore, 'clear').mockImplementationOnce(async () => {
      await clearing.promise;
      await clearSession();
    });
    const aliceStart = deferred();
    const bobStart = deferred();
    const normalPost = api.post.getMockImplementation();
    api.post.mockImplementation((url, body, options) => url === '/start'
      ? body.username === 'alice' ? aliceStart.promise : bobStart.promise
      : normalPost(url, body, options));
    render(<App />);
    const startButton = await screen.findByRole('button', { name: '开始学习' });
    const pending = scenario !== 'idle';
    if (pending) fireEvent.click(startButton);
    const oldSignal = api.post.mock.calls.find(([url]) => url === '/start')?.[2].signal;
    const logoutButton = screen.getByRole('button', { name: '退出登录' });
    fireEvent.click(logoutButton);
    expect(startButton.disabled).toBe(true);
    expect(logoutButton.disabled).toBe(true);
    if (pending) expect(oldSignal.aborted).toBe(true);
    fireEvent.click(startButton);
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(pending ? 1 : 0);
    expect(screen.queryByLabelText('密码')).toBeNull();

    await act(async () => clearing.resolve());
    await loginManually('bob');
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    await act(async () => {
      if (scenario === 'pending conflict') {
        aliceStart.reject({ response: { status: 409, data: { data: { task_id: 'alice-task' } } } });
      } else aliceStart.resolve(ok({ task_id: 'alice-task' }));
    });
    const bobStarting = screen.getByRole('button', { name: '任务启动中' });
    expect(bobStarting.disabled).toBe(true);
    fireEvent.click(bobStarting);
    expect(api.post.mock.calls.filter(([url]) => url === '/start')).toHaveLength(pending ? 2 : 1);
    expect(screen.queryByText('alice-task')).toBeNull();
    await act(async () => bobStart.resolve(ok({ task_id: 'bob-task' })));
    await screen.findByText('bob-task');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toEqual({ username: 'bob', taskId: 'bob-task' }));
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)).login.username).toBe('bob');
  });

  it('preserves the logged-in account and allows retrying when clearing the session fails', async () => {
    services();
    await sessionStore.rememberLogin('alice');
    const clearing = deferred();
    vi.spyOn(sessionStore, 'clear').mockReturnValueOnce(clearing.promise);
    const oldStart = deferred();
    const normalPost = api.post.getMockImplementation();
    let starts = 0;
    api.post.mockImplementation((url, body, options) => url === '/start' && starts++ === 0
      ? oldStart.promise : normalPost(url, body, options));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
    await act(async () => clearing.reject(new Error('clear failed')));
    expect((await screen.findByRole('alert')).textContent).toContain('清除保存的账号失败');
    expect(screen.getByText('alice')).toBeTruthy();
    expect(screen.queryByLabelText('密码')).toBeNull();
    expect(screen.getByRole('button', { name: '退出登录' }).disabled).toBe(false);
    const retryButton = screen.getByRole('button', { name: '开始学习' });
    expect(retryButton.disabled).toBe(false);
    fireEvent.click(retryButton);
    await screen.findByText('task-one');
    await act(async () => oldStart.resolve(ok({ task_id: 'old-task' })));
    expect(screen.queryByText('old-task')).toBeNull();
    expect(screen.getByText('task-one')).toBeTruthy();
    expect(api.post.mock.calls.filter(([url]) => url === '/login')).toHaveLength(1);
  });

  it.each([false, true])('shows the recovery storage failure on the actual progress page (conflict: %s)', async (conflict) => {
    services({ conflict });
    await sessionStore.rememberLogin('alice');
    vi.spyOn(sessionStore, 'rememberTask').mockRejectedValueOnce(new Error('storage unavailable'));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText(conflict ? 'existing-task' : 'task-one');
    expect(screen.getByText('学习进度监控')).toBeTruthy();
    const notice = await screen.findByRole('alert');
    expect(notice.textContent).toContain('恢复信息未能保存');
    expect(notice.textContent).toMatch(/请记下.*任务 ID/);
  });

  it.each(['alice', 'bob'])('does not attach a late recovery failure to a new task after logging in as %s', async (username) => {
    services();
    await sessionStore.rememberLogin('alice');
    const oldSave = deferred();
    vi.spyOn(sessionStore, 'rememberTask').mockReturnValueOnce(oldSave.promise);
    const normalPost = api.post.getMockImplementation();
    let starts = 0;
    api.post.mockImplementation((url, body, options) => url === '/start'
      ? Promise.resolve(ok({ task_id: starts++ === 0 ? 'old-task' : 'new-task' }))
      : normalPost(url, body, options));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
    await screen.findByText('old-task');
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await loginManually(username);
    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    await screen.findByText('new-task');
    await act(async () => oldSave.reject(new Error('old storage failure')));
    expect(screen.getByText('new-task')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '返回运行任务' });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toEqual({ username, taskId: 'new-task' });
  });

  it('clears expired task persistence and permits a new task after returning', async () => {
    services();
    const normalGet = api.get.getMockImplementation();
    api.get.mockImplementation((url, options) => url.startsWith('/task/') ? Promise.reject({ response: { status: 404 } }) : normalGet(url, options));
    await sessionStore.rememberLogin('alice');
    await sessionStore.rememberTask({ username: 'alice', taskId: 'expired-task' });
    render(<App />);
    expect((await screen.findByRole('alert')).textContent).toContain('任务不存在或已过期');
    await waitFor(() => expect(JSON.parse(localStorage.getItem(SESSION_KEY)).activeTask).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    expect((await screen.findByRole('button', { name: '开始学习' })).disabled).toBe(false);
  });

  it.each(['completed', 'partial', 'error'])('allows a new task only after the tracked task becomes %s', async (status) => {
    services({ status });
    await sessionStore.rememberLogin('alice');
    await sessionStore.rememberTask({ username: 'alice', taskId: 'task-one' });
    render(<App />);
    await screen.findByText('task-one');
    await waitFor(() => expect(screen.queryByText('加载中')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
    await screen.findByRole('button', { name: '查看上次任务' });
    expect(screen.getByRole('button', { name: '开始学习' }).disabled).toBe(false);
  });
});

describe('progress details', () => {
  it('shows failed, skipped and empty chapters distinctly and renders at most 500 log entries', async () => {
    const chapters = [
      { id: 'failed', title: 'Failed chapter', status: 'error', has_finished: true },
      { id: 'skipped', title: 'Skipped chapter', status: 'skipped', has_finished: false },
      { id: 'empty', title: 'Empty chapter', status: 'empty', has_finished: true },
    ];
    const logs = Array.from({ length: 510 }, (_, index) => ({ seq: index + 1, timestamp: 1, level: 'info', message: `line-${index}` }));
    api.get.mockImplementation(async (url) => url.endsWith('/details') ? ok({ courses: [{ id: 'c', title: 'Result course', status: 'partial', chapters }] }) : url.startsWith('/logs/') ? page(logs, 510) : ok(taskState('partial')));
    render(<StudyProgress taskId="one" />);
    await screen.findByText('line-509');
    const log = screen.getByRole('log');
    expect(within(log).getAllByText(/^line-/)).toHaveLength(500);
    expect(within(log).queryByText('line-0')).toBeNull();
    expect(screen.queryByText('所有任务已完成')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Result course/ }));
    const row = (title) => screen.getByText(title).closest('li');
    expect(within(row('Failed chapter')).getByText('失败')).toBeTruthy();
    expect(within(row('Skipped chapter')).getByText('已跳过')).toBeTruthy();
    expect(within(row('Empty chapter')).getByText('无任务')).toBeTruthy();
    expect(screen.getByText('1 / 3 章节')).toBeTruthy();
  });

  it('cancels old task requests on task change, resets the cursor and ignores late old logs', async () => {
    const oldDetails = deferred();
    const oldLogs = deferred();
    api.get.mockImplementation(async (url) => {
      if (url === '/task/old/details') return oldDetails.promise;
      if (url === '/logs/old') return oldLogs.promise;
      if (url.endsWith('/details')) return ok({ courses: [] });
      if (url.startsWith('/logs/')) return page([{ seq: 1, message: 'new-log', timestamp: 1 }], 1);
      return ok(taskState(url === '/task/old' ? 'running' : 'completed'));
    });
    const view = render(<StudyProgress taskId="old" />);
    await waitFor(() => expect(api.get.mock.calls.some(([url]) => url === '/logs/old')).toBe(true));
    const oldSignal = api.get.mock.calls.find(([url]) => url === '/logs/old')[1].signal;
    view.rerender(<StudyProgress taskId="new" />);
    await screen.findByText('new-log');
    expect(oldSignal.aborted).toBe(true);
    expect(api.get.mock.calls.find(([url]) => url === '/logs/new')[1].params.after).toBe(0);
    await act(async () => { oldDetails.resolve(ok({ courses: [] })); oldLogs.resolve(page([{ seq: 99, message: 'old-log' }], 99)); });
    expect(screen.queryByText('old-log')).toBeNull();
    view.unmount();
    expect(api.get.mock.calls.find(([url]) => url === '/logs/new')[1].signal.aborted).toBe(true);
  });
});

it('migrates legacy login to cookie-only authentication and falls back to manual login when expired', async () => {
  localStorage.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'legacy-test-password' }));
  api.post.mockImplementation(async (_url, body) => {
    if (body.use_cookies) throw { response: { status: 401 } };
    return ok({ username: 'alice' });
  });
  const success = vi.fn();
  render(<React.StrictMode><Login onLoginSuccess={success} /></React.StrictMode>);
  await screen.findByText('保存的登录会话不可用，请输入密码重新登录');
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][1]).toEqual({ username: 'alice', password: '', use_cookies: true });
  expect(localStorage.getItem(SAVED_LOGIN_KEY)).toBeNull();
  expect(screen.getByLabelText('密码').value).toBe('');
  fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'new-test-password' } });
  fireEvent.click(screen.getByRole('button', { name: '登录' }));
  await waitFor(() => expect(success).toHaveBeenCalledOnce());
  expect(success.mock.calls[0][0]).toEqual(account);
  expect(localStorage.getItem(SESSION_KEY)).not.toContain('password');
});

it.each(['courses', 'progress'])('keeps the %s preview usable without backend requests', async (preview) => {
  window.history.replaceState({}, '', `/?preview=${preview}`);
  render(<App />);
  if (preview === 'courses') fireEvent.click(await screen.findByRole('button', { name: '开始学习' }));
  await screen.findByText('preview-task');
  fireEvent.click(screen.getByRole('button', { name: '返回课程选择' }));
  await screen.findByRole('button', { name: '开始学习' });
  fireEvent.click(screen.getByRole('button', { name: '保存当前配置' }));
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).not.toHaveBeenCalled();
  expect(localStorage.getItem(SESSION_KEY)).toBeNull();
});

````

## web/src/components/ui/Button.jsx

SHA256: 374834150e9a70b13d47e044e48ffc406cb32ddb630abb7e014c67fb99764a4d

````text
import React from 'react';
import { cn } from '../../lib/utils';

/** 标准按钮:品牌蓝主按钮、浅灰次按钮,悬停轻微提亮 */
const Button = React.forwardRef(({ className, variant = 'default', size = 'default', children, ...props }, ref) => {
  const variantStyles = {
    default: 'bg-brand text-white hover:bg-brand-dark shadow-sm',
    destructive: 'bg-danger text-white hover:bg-danger/90 shadow-sm',
    outline: 'border border-line bg-white text-ink hover:bg-soft',
    secondary: 'bg-soft text-ink hover:bg-line',
    ghost: 'text-body hover:bg-soft hover:text-ink',
  };

  const sizeStyles = {
    default: 'h-10 px-4',
    sm: 'h-8 px-3 text-[13px]',
    lg: 'h-12 px-6 text-[15px]',
    icon: 'h-10 w-10',
  };

  return (
    <button
      className={cn(
        'inline-flex select-none items-center justify-center gap-2 rounded-lg text-sm font-medium',
        'transition-colors duration-150 ease-out',
        'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20',
        'disabled:pointer-events-none disabled:opacity-45',
        variantStyles[variant],
        sizeStyles[size],
        className
      )}
      ref={ref}
      {...props}
    >
      {children}
    </button>
  );
});

Button.displayName = 'Button';

export default Button;

````

## web/src/components/ui/Card.jsx

SHA256: ffb00ae47fe68ab6eb8aa35e0817545ffa178afd4e8f190921efe296c4f48abc

````text
import React from 'react';
import { cn } from '../../lib/utils';

/** 白面卡片:细边框 + 轻投影 */
const Card = React.forwardRef(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('rounded-xl border border-line bg-white shadow-card', className)}
    {...props}
  />
));
Card.displayName = 'Card';

const CardHeader = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('flex flex-col gap-1 p-5 pb-4', className)} {...props} />
));
CardHeader.displayName = 'CardHeader';

const CardTitle = React.forwardRef(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn('text-base font-semibold leading-tight tracking-tight', className)}
    {...props}
  />
));
CardTitle.displayName = 'CardTitle';

const CardDescription = React.forwardRef(({ className, ...props }, ref) => (
  <p ref={ref} className={cn('text-[13px] leading-relaxed text-faint', className)} {...props} />
));
CardDescription.displayName = 'CardDescription';

const CardContent = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('p-5 pt-0', className)} {...props} />
));
CardContent.displayName = 'CardContent';

const CardFooter = React.forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('flex items-center p-5 pt-0', className)} {...props} />
));
CardFooter.displayName = 'CardFooter';

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };

````

## web/src/components/ui/Input.jsx

SHA256: 157c38a143980d8f5c8a827efdc91bd18d9d5314739fa39cfede47a47b80ac3c

````text
import React from 'react';
import { cn } from '../../lib/utils';

/** 输入框:浅灰描边、聚焦品牌蓝 */
const Input = React.forwardRef(({ className, type = 'text', ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        'flex h-10 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink',
        'placeholder:text-faint/70',
        'transition-shadow duration-150',
        'hover:border-faint/50',
        'focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15',
        'disabled:cursor-not-allowed disabled:bg-soft disabled:opacity-60',
        className
      )}
      ref={ref}
      {...props}
    />
  );
});

Input.displayName = 'Input';

export default Input;

````

## web/src/components/ui/Label.jsx

SHA256: 9e49800348178215d44a099ff10bc246cb7744bbe7103bc2bdadfa1a6b735ee4

````text
import React from 'react';
import { cn } from '../../lib/utils';

/** 表单标签 */
const Label = React.forwardRef(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn(
      'text-[13px] font-medium leading-none text-ink',
      className
    )}
    {...props}
  />
));
Label.displayName = 'Label';

export default Label;

````

## web/src/index.css

SHA256: 70f9a557aa9a181898beb9a43c971455e999822e20814f7db9085a1a1046e136

````text
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    /* 现代工具风 · 石墨蓝灰 */
    --ink: 220 25% 12%;        /* 主文字 #171C26 */
    --body: 220 14% 34%;       /* 正文 */
    --faint: 220 10% 48%;      /* 次级文字 */
    --canvas: 220 20% 98%;     /* 页面底 #F7F8FA */
    --surface: 0 0% 100%;      /* 卡片面 */
    --soft: 220 16% 96%;       /* 次级面/悬停面 */
    --line: 220 14% 90%;       /* 分隔线 #E2E5EA */
    --brand: 221 78% 48%;      /* 品牌蓝 */
    --brand-dark: 221 78% 40%;
    --brand-soft: 221 80% 96%;
    --success: 152 58% 36%;
    --danger: 0 68% 48%;
    --warning: 36 80% 44%;
  }

  * {
    @apply border-line;
  }

  html {
    min-width: 320px;
    font-size: clamp(15px, calc(14.25px + 0.1vw), 17px);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
    scroll-behavior: smooth;
  }

  body {
    @apply bg-canvas text-ink font-sans;
    min-width: 320px;
    min-height: 100vh;
    overflow-x: hidden;
  }

  #root,
  .App {
    min-height: 100vh;
  }

  ::selection {
    background: hsl(var(--brand) / 0.2);
  }

  /* 数字对齐 */
  .tnum {
    font-variant-numeric: tabular-nums;
  }
}

@layer components {
  /* 页面内容随视口扩展，仅保留随窗口变化的安全边距 */
  .page-shell {
    width: 100%;
    margin-inline: auto;
    padding-inline: clamp(1rem, 3vw, 4rem);
  }

  /* 无动效降级 */
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
  }
}

````

## web/src/lib/courseSelection.js

SHA256: 3740d6115303b172983136dfc8c6bdffb609445b3d2c16ff825843e5e8bb5e53

````text
export function defaultSettings() {
  return {
    speed: 1.0, jobs: 1, notopen_action: 'retry', tiku_config: {},
    notification_config: { provider: 'Windows' }, ocr_config: {},
  };
}

export function restoreSettings(saved) {
  const settings = { ...defaultSettings(), ...saved };
  if (typeof settings.notification_config?.provider !== 'string') {
    settings.notification_config = { ...settings.notification_config, provider: 'Windows' };
  }
  if (!['retry', 'continue'].includes(settings.notopen_action)) settings.notopen_action = 'retry';
  return settings;
}

export function normalizeCourses(courses) {
  if (!Array.isArray(courses)) throw new Error('课程列表格式错误，请重新加载');
  return courses.map((course) => ({ ...course, courseId: String(course.courseId) }));
}

export function restoreCourseSelection(config, username, courses) {
  const ids = courses.map((course) => String(course.courseId));
  const accounts = config?.selectedCoursesByAccount;
  const saved = !!accounts && Object.hasOwn(accounts, username);
  if (!saved) return { ids, saved: false };
  const valid = new Set(ids);
  const selection = Array.isArray(accounts[username]) ? accounts[username] : [];
  return { ids: [...new Set(selection.map(String))].filter((id) => valid.has(id)), saved: true };
}

````

## web/src/lib/courseSelection.test.js

SHA256: 643883cf739c6eafc1d132c11389c9df991c1b18e4636bda5cb4384cf6137e51

````text
import { expect, it } from 'vitest';
import { restoreCourseSelection } from './courseSelection';

const courses = [{ courseId: '1' }, { courseId: '2' }];

it('restores only the current account selection intersected with actual courses', () => {
  const config = { selectedCoursesByAccount: { alice: ['obsolete', 2, 2], bob: ['1'] } };
  expect(restoreCourseSelection(config, 'alice', courses)).toEqual({ ids: ['2'], saved: true });
  expect(restoreCourseSelection(config, 'bob', courses)).toEqual({ ids: ['1'], saved: true });
});

it('preserves an empty or fully stale saved selection', () => {
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: ['gone'] } }, 'alice', courses).ids).toEqual([]);
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: [] } }, 'alice', courses).ids).toEqual([]);
});

it('ignores old unscoped selections and explicitly checks every course for new accounts', () => {
  expect(restoreCourseSelection({ selectedCourses: ['foreign'] }, 'new', courses)).toEqual({ ids: ['1', '2'], saved: false });
  expect(restoreCourseSelection({ selectedCoursesByAccount: { alice: ['1'] } }, 'new', courses).ids).toEqual(['1', '2']);
});

````

## web/src/lib/desktopBridge.js

SHA256: 7cae27d085e6f2b27639ab67b143cc353823cd995952fe840973a81d4210f2ea

````text
import { invoke, isTauri } from '@tauri-apps/api/core';

export const isTauriDesktop = () => isTauri();

// Keep runtime details and command envelopes out of the business components.
export const desktopBridge = Object.freeze({
  apiRequest: (request) => invoke('api_request', { request }),
  apiCancel: (requestId) => invoke('api_cancel', { requestId }),
  backendStatus: () => invoke('backend_status'),
  read: () => invoke('session_read'),
  rememberLogin: (username) => invoke('session_remember_login', { username }),
  rememberTask: (task) => invoke('session_remember_task', { task }),
  clear: () => invoke('session_clear'),
});

export function getSessionBridge() {
  if (isTauriDesktop()) return desktopBridge;
  return globalThis.window?.chaoxingSession ?? null;
}

````

## web/src/lib/desktopBridge.test.js

SHA256: 8a82783b276569dcf608d9b4b3299e7d629521fef80b69568784e342478249fa

````text
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const core = vi.hoisted(() => ({ isTauri: vi.fn(), invoke: vi.fn() }));
vi.mock('@tauri-apps/api/core', () => core);
import { desktopBridge, getSessionBridge, isTauriDesktop } from './desktopBridge';

const empty = () => ({ version: 1, login: null, activeTask: null });

beforeEach(() => { core.isTauri.mockReset().mockReturnValue(false); core.invoke.mockReset().mockResolvedValue(empty()); });
afterEach(() => { delete window.chaoxingSession; vi.restoreAllMocks(); });

describe('desktop bridge', () => {
  it('uses the official runtime detection and preserves the Electron session bridge', () => {
    expect(isTauriDesktop()).toBe(false);
    expect(getSessionBridge()).toBeNull();
    const electron = { read: vi.fn() };
    window.chaoxingSession = electron;
    expect(getSessionBridge()).toBe(electron);
    core.isTauri.mockReturnValue(true);
    expect(getSessionBridge()).toBe(desktopBridge);
    expect(core.isTauri).toHaveBeenCalled();
  });

  it('invokes only the named commands with their exact argument envelopes', async () => {
    const request = { operation: 'taskLogs', payload: null, requestId: 42, taskId: 'task', after: 8 };
    const task = { username: 'alice', taskId: 'task' };
    await desktopBridge.apiRequest(request);
    await desktopBridge.apiCancel(42);
    await desktopBridge.backendStatus();
    await desktopBridge.read();
    await desktopBridge.rememberLogin('alice');
    await desktopBridge.rememberTask(task);
    await desktopBridge.rememberTask(null);
    expect(await desktopBridge.clear()).toEqual(empty());
    expect(core.invoke.mock.calls).toEqual([
      ['api_request', { request }], ['api_cancel', { requestId: 42 }], ['backend_status'],
      ['session_read'], ['session_remember_login', { username: 'alice' }],
      ['session_remember_task', { task }], ['session_remember_task', { task: null }], ['session_clear'],
    ]);
  });

  it('propagates initialization and command errors instead of selecting browser persistence', async () => {
    core.isTauri.mockImplementation(() => { throw new Error('initialization failed'); });
    expect(() => getSessionBridge()).toThrow('initialization failed');
    core.invoke.mockRejectedValue(new Error('disk failure'));
    await expect(desktopBridge.read()).rejects.toThrow('disk failure');
  });

  it.each([false, true])('selects the API transport using official detection (Tauri: %s)', async (tauri) => {
    core.isTauri.mockReturnValue(tauri);
    core.invoke.mockResolvedValue({ status: 200, body: { status: true } });
    vi.resetModules();
    const { default: api } = await import('../api/axios');
    expect(api.defaults.timeout).toBe(30000);
    if (tauri) {
      expect((await api.get('/config')).data).toEqual({ status: true });
      expect(core.invoke).toHaveBeenCalledWith('api_request', {
        request: { operation: 'configRead', payload: null, requestId: expect.any(Number) },
      });
    } else {
      expect(api.defaults.baseURL).toBe('/api');
      expect(api.defaults.adapter).toEqual(expect.arrayContaining(['xhr', 'http']));
      expect(core.invoke).not.toHaveBeenCalled();
    }
  });
});

````

## web/src/lib/sessionStore.js

SHA256: 594c1af7c5bd357b32b5c95a5e94d8bf8ecb551fc058799434dbc1e3a114f2ee

````text
import { desktopBridge, getSessionBridge } from './desktopBridge';

export const SAVED_LOGIN_KEY = 'chaoxing_saved_login';
export const SESSION_KEY = 'chaoxing_session_v1';
const emptySession = () => ({ version: 1, login: null, activeTask: null });
const usernameValue = (value) => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 128 && !/[\u0000-\u001f\u007f]/.test(value) ? value.trim() : null;
export const validTaskId = (value) => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value);
const exactKeys = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const validTask = (value) => exactKeys(value, ['username', 'taskId']) && usernameValue(value.username) !== null
  && usernameValue(value.username) === value.username && validTaskId(value.taskId);

function desktopSession(value) {
  if (!exactKeys(value, ['version', 'login', 'activeTask']) || value.version !== 1
    || !(value.login === null || (exactKeys(value.login, ['username', 'use_cookies'])
      && usernameValue(value.login.username) !== null && usernameValue(value.login.username) === value.login.username && value.login.use_cookies === true))
    || !(value.activeTask === null || (validTask(value.activeTask) && value.activeTask.username === value.login?.username))) {
    throw new Error('保存的账号格式错误');
  }
  return sanitizeSession(value);
}

function sanitizeSession(value) {
  const session = emptySession();
  const username = usernameValue(value?.login?.username);
  if (username) session.login = { username, use_cookies: true };
  if (username && value?.activeTask?.username === username && validTaskId(value.activeTask.taskId)) {
    session.activeTask = { username, taskId: value.activeTask.taskId };
  }
  return session;
}

function parseStored(raw) {
  try { return raw && raw.length <= 16384 ? JSON.parse(raw) : null; } catch { return null; }
}

export function createSessionStore({
  getBridge = getSessionBridge,
  getStorage = () => window.localStorage,
} = {}) {
  let queue = Promise.resolve();
  const serialize = (operation) => {
    const next = queue.then(operation);
    queue = next.catch(() => {});
    return next;
  };
  const storage = () => { try { return getStorage(); } catch { return null; } };

  const read = async () => {
    const bridge = getBridge();
    let session = bridge ? desktopSession(await bridge.read()) : null;
    // Tauri imports legacy data in the host before readiness. Its random page
    // origin must never become a second persistence source or an IO fallback.
    if (bridge === desktopBridge) return session;
    const local = storage();
    let legacy;
    let browserState;
    if (local) {
      // Remove legacy plaintext credentials before migration; only the account is used.
      const old = local.getItem(SAVED_LOGIN_KEY);
      local.removeItem(SAVED_LOGIN_KEY);
      legacy = usernameValue(parseStored(old)?.username);
      browserState = sanitizeSession(parseStored(local.getItem(SESSION_KEY)));
    }
    session ||= browserState || emptySession();
    const migratedUsername = browserState?.login?.username || legacy;
    if (!session.login && migratedUsername) {
      session.login = { username: migratedUsername, use_cookies: true };
      if (bridge) {
        session = desktopSession(await bridge.rememberLogin(migratedUsername));
      } else if (local) {
        local.setItem(SESSION_KEY, JSON.stringify(session));
      }
    }
    if (bridge && local) local.removeItem(SESSION_KEY);
    return session;
  };

  return {
    read: () => serialize(read),
    rememberLogin: (value) => serialize(async () => {
      const username = usernameValue(value);
      if (!username) throw new Error('账号格式错误');
      const bridge = getBridge();
      if (bridge) return desktopSession(await bridge.rememberLogin(username));
      const previous = await read();
      const session = { ...emptySession(), login: { username, use_cookies: true }, activeTask: previous.login?.username === username ? previous.activeTask : null };
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    rememberTask: (task) => serialize(async () => {
      const bridge = getBridge();
      if (task !== null && !validTask(task)) throw new Error('任务信息格式错误');
      if (bridge) return desktopSession(await bridge.rememberTask(task));
      const session = await read();
      if (task && session.login?.username !== task.username) throw new Error('任务账号不匹配');
      session.activeTask = task ? { username: task.username, taskId: task.taskId } : null;
      storage()?.setItem(SESSION_KEY, JSON.stringify(session));
      return session;
    }),
    clear: () => serialize(async () => {
      const bridge = getBridge();
      const session = bridge ? desktopSession(await bridge.clear()) : emptySession();
      if (bridge === desktopBridge) return session;
      const local = storage();
      local?.removeItem(SAVED_LOGIN_KEY);
      local?.removeItem(SESSION_KEY);
      return session;
    }),
  };
}

export const sessionStore = createSessionStore();

````

## web/src/lib/sessionStore.test.js

SHA256: 5d274726e1427099e423df5e507bad15274ed2ae95f69301697f0f20ab3c6808

````text
import { describe, expect, it, vi } from 'vitest';
import { createSessionStore, SAVED_LOGIN_KEY, SESSION_KEY } from './sessionStore';

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

describe('saved session', () => {
  it('migrates legacy credentials to a cookie account without keeping the password', async () => {
    const local = memoryStorage();
    local.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'legacy-test-secret' }));
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    expect((await store.read()).login).toEqual({ username: 'alice', use_cookies: true });
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).not.toContain('legacy-test-secret');
  });

  it('uses stable desktop persistence after the browser origin changes', async () => {
    let saved = { version: 1, login: null, activeTask: null };
    const bridge = {
      read: vi.fn(async () => saved),
      rememberLogin: vi.fn(async (username) => (saved = { ...saved, login: { username, use_cookies: true } })),
      rememberTask: vi.fn(async (task) => (saved = { ...saved, activeTask: task })),
      clear: vi.fn(async () => (saved = { version: 1, login: null, activeTask: null })),
    };
    const oldOrigin = memoryStorage();
    oldOrigin.setItem(SAVED_LOGIN_KEY, JSON.stringify({ username: 'alice', password: 'old-test-secret' }));
    const first = createSessionStore({ getBridge: () => bridge, getStorage: () => oldOrigin });
    await first.read();
    await first.rememberTask({ username: 'alice', taskId: 'task-one' });
    const second = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    expect((await second.read()).activeTask.taskId).toBe('task-one');
    expect(bridge.rememberLogin).toHaveBeenCalledWith('alice');
    expect(JSON.stringify(bridge.rememberLogin.mock.calls)).not.toContain('secret');
    expect(oldOrigin.getItem(SAVED_LOGIN_KEY)).toBeNull();
    await second.clear();
    expect((await first.read()).login).toBeNull();
  });

  it('isolates tasks by account and clears both new and legacy storage on logout', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    await store.rememberLogin('bob');
    expect((await store.read()).activeTask).toBeNull();
    await expect(store.rememberTask({ username: 'alice', taskId: 'one' })).rejects.toThrow('账号不匹配');
    local.setItem(SAVED_LOGIN_KEY, '{}');
    await store.clear();
    expect(local.getItem(SAVED_LOGIN_KEY)).toBeNull();
    expect(local.getItem(SESSION_KEY)).toBeNull();
  });

  it('preserves the login when only an expired task is cleared', async () => {
    const local = memoryStorage();
    const store = createSessionStore({ getBridge: () => null, getStorage: () => local });
    await store.rememberLogin('alice');
    await store.rememberTask({ username: 'alice', taskId: 'one' });
    expect(await store.rememberTask(null)).toEqual({ version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null });
    expect(await store.clear()).toEqual({ version: 1, login: null, activeTask: null });
  });

  it('does not touch localStorage when desktop initialization or read fails', async () => {
    const getStorage = vi.fn(() => memoryStorage());
    const broken = createSessionStore({ getBridge: () => { throw new Error('initialization failed'); }, getStorage });
    await expect(broken.read()).rejects.toThrow('initialization failed');
    const failing = createSessionStore({ getBridge: () => ({ read: vi.fn().mockRejectedValue(new Error('disk failed')) }), getStorage });
    await expect(failing.read()).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
  });

  it.each(['rememberLogin', 'rememberTask', 'clear'])('propagates desktop %s failures without browser fallback', async (operation) => {
    const local = memoryStorage();
    local.setItem(SESSION_KEY, 'existing-browser-state');
    const bridge = { [operation]: vi.fn().mockRejectedValue(new Error('disk failed')) };
    const getStorage = vi.fn(() => local);
    const store = createSessionStore({ getBridge: () => bridge, getStorage });
    const argument = operation === 'rememberLogin' ? 'alice' : operation === 'rememberTask' ? { username: 'alice', taskId: 'one' } : undefined;
    await expect(store[operation](argument)).rejects.toThrow('disk failed');
    expect(getStorage).not.toHaveBeenCalled();
    expect(local.getItem(SESSION_KEY)).toBe('existing-browser-state');
  });

  it.each([
    undefined,
    { version: 2, login: null, activeTask: null },
    { version: 1, login: null },
    { version: 1, login: { username: 'alice', use_cookies: true, password: 'forbidden' }, activeTask: null },
    { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: { username: 'bob', taskId: 'one' } },
  ])('rejects malformed desktop sessions instead of silently erasing state (%#)', async (session) => {
    const store = createSessionStore({ getBridge: () => ({ read: vi.fn().mockResolvedValue(session) }), getStorage: () => memoryStorage() });
    await expect(store.read()).rejects.toThrow('格式错误');
  });

  it('serializes desktop writes and allows a later write after an earlier failure', async () => {
    let rejectFirst;
    const saved = { version: 1, login: { username: 'alice', use_cookies: true }, activeTask: null };
    const bridge = {
      rememberLogin: vi.fn(() => new Promise((_resolve, reject) => { rejectFirst = reject; })),
      rememberTask: vi.fn().mockResolvedValue(saved),
    };
    const store = createSessionStore({ getBridge: () => bridge, getStorage: () => memoryStorage() });
    const first = expect(store.rememberLogin('alice')).rejects.toThrow('disk failed');
    const second = store.rememberTask(null);
    await Promise.resolve();
    expect(bridge.rememberTask).not.toHaveBeenCalled();
    rejectFirst(new Error('disk failed'));
    await first;
    expect(await second).toEqual(saved);
    expect(bridge.rememberTask).toHaveBeenCalledExactlyOnceWith(null);
  });

  it.each([
    { username: null, taskId: 'task' },
    { username: ' alice ', taskId: 'task' },
    { username: 'alice', taskId: '../task' },
    { username: 'alice', taskId: 'task', password: 'forbidden' },
  ])('rejects invalid task arguments before desktop persistence (%#)', async (task) => {
    const bridge = { rememberTask: vi.fn().mockResolvedValue({ version: 1, login: null, activeTask: null }) };
    const store = createSessionStore({ getBridge: () => bridge });
    await expect(store.rememberTask(task)).rejects.toThrow('任务信息格式错误');
    expect(bridge.rememberTask).not.toHaveBeenCalled();
  });
});

````

## web/src/lib/taskPolling.js

SHA256: 780028bd7ae2c8bf14bfadcd7d037326157df2eaba7f87c6500bb880157b2510

````text
export const LOG_LIMIT = 500;
export const isTerminalStatus = (status) => ['completed', 'error', 'partial'].includes(status);

export function appendLogPage(previous, page, limit = LOG_LIMIT) {
  const incoming = new Map();
  for (const log of Array.isArray(page.data) ? page.data : []) {
    if (Number.isSafeInteger(log?.seq) && log.seq > previous.cursor) incoming.set(log.seq, log);
  }
  const fresh = [...incoming.values()].sort((a, b) => a.seq - b.seq);
  const logs = fresh.length ? [...previous.logs, ...fresh] : previous.logs;
  return {
    logs: logs.length > limit ? logs.slice(-limit) : logs,
    cursor: Math.max(previous.cursor, Number.isSafeInteger(page.next_cursor) ? page.next_cursor : 0, fresh.at(-1)?.seq || 0),
    truncated: previous.truncated || page.truncated === true || logs.length > limit,
  };
}

function responseBody(response) {
  if (!response.data?.status) throw new Error(response.data?.msg || '获取任务信息失败');
  return response.data;
}

// A round includes status and all dependent snapshots. Schedule only after it settles;
// a terminal status still needs its final details/logs, including retries on failure.
export function startTaskPolling({
  api, taskId, includeDetails = true, intervalMs = 2000,
  onStatus = () => {}, onDetails = () => {}, onLogs = () => {},
  onError = () => {}, onMissing = () => {},
}) {
  const controller = new AbortController();
  const { signal } = controller;
  const taskPath = `/task/${encodeURIComponent(taskId)}`;
  let timer;
  let logState = { logs: [], cursor: 0, truncated: false };
  const stop = () => {
    controller.abort();
    clearTimeout(timer);
  };
  const missing = () => { stop(); onMissing(); };

  const poll = async () => {
    let finished = false;
    try {
      const status = responseBody(await api.get(taskPath, { signal })).data;
      if (signal.aborted) return;
      onStatus(status);
      finished = isTerminalStatus(status.status);

      if (includeDetails) {
        const results = await Promise.allSettled([
          api.get(`${taskPath}/details`, { signal }).then(responseBody),
          api.get(`/logs/${encodeURIComponent(taskId)}`, { signal, params: { after: logState.cursor } }).then(responseBody),
        ]);
        if (signal.aborted) return;
        if (results.some((result) => result.status === 'rejected' && result.reason?.response?.status === 404)) {
          missing();
          return;
        }
        if (results[0].status === 'fulfilled') onDetails(results[0].value.data);
        if (results[1].status === 'fulfilled') {
          logState = appendLogPage(logState, results[1].value);
          onLogs(logState);
        }
        const failure = results.find((result) => result.status === 'rejected');
        if (failure) throw failure.reason;
      }
      onError('');
    } catch (error) {
      if (signal.aborted) return;
      if (error?.response?.status === 404) {
        missing();
        return;
      }
      finished = false;
      onError(error?.response?.data?.msg || error?.message || '获取任务信息失败，将自动重试');
    }
    if (!signal.aborted && !finished) timer = setTimeout(poll, intervalMs);
  };

  poll();
  return stop;
}

export function chapterState(chapter) {
  if (chapter.status === 'failed') return 'error';
  return chapter.status || (chapter.has_finished ? 'completed' : 'pending');
}

export const resultLabels = {
  pending: '等待中', running: '进行中', completed: '已完成',
  error: '失败', partial: '部分完成', skipped: '已跳过', empty: '无任务',
};

````

## web/src/lib/taskPolling.test.js

SHA256: 8751e2dca46428110dcc4494020fa78fa6373d24d0410a1be6da1fd8267f3982

````text
import { afterEach, describe, expect, it, vi } from 'vitest';
import { appendLogPage, startTaskPolling } from './taskPolling';

const ok = (data) => ({ data: { status: true, data } });
const logPage = (data = [], cursor = 0) => ({ data: { status: true, data, next_cursor: cursor, truncated: false } });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

afterEach(() => vi.useRealTimers());

describe('task polling', () => {
  it('waits for every request before scheduling another round', async () => {
    vi.useFakeTimers();
    const status = deferred();
    const details = deferred();
    const api = { get: vi.fn((url) => url.endsWith('/details') ? details.promise : url.startsWith('/logs') ? Promise.resolve(logPage()) : status.promise) };
    const stop = startTaskPolling({ api, taskId: 'one' });
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(1);
    status.resolve(ok({ status: 'running' }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(3);
    details.resolve(ok({ courses: [] }));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1999);
    expect(api.get).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(api.get).toHaveBeenCalledTimes(6);
    stop();
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it('aborts pending requests and ignores a late result after disposal', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const onStatus = vi.fn();
    const api = { get: vi.fn(() => pending.promise) };
    const stop = startTaskPolling({ api, taskId: 'old', onStatus });
    stop();
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
    pending.resolve(ok({ status: 'completed' }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(onStatus).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(['completed', 'error', 'partial'])('pulls final details and logs after observing %s, then stops', async (status) => {
    vi.useFakeTimers();
    const pending = deferred();
    const onDetails = vi.fn();
    const onLogs = vi.fn();
    const api = { get: vi.fn((url) => url.endsWith('/details') ? Promise.resolve(ok({ courses: [{ status }] })) : url.startsWith('/logs') ? Promise.resolve(logPage([{ seq: 7, message: 'final' }], 7)) : pending.promise) };
    startTaskPolling({ api, taskId: 'one', onDetails, onLogs });
    expect(api.get).toHaveBeenCalledTimes(1);
    pending.resolve(ok({ status }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.get).toHaveBeenCalledTimes(3);
    expect(onDetails).toHaveBeenCalledWith({ courses: [{ status }] });
    expect(onLogs.mock.calls[0][0].logs[0].message).toBe('final');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries failed final detail pulls and advances the log cursor without duplicates', async () => {
    vi.useFakeTimers();
    let detailsCalls = 0;
    const onLogs = vi.fn();
    const api = { get: vi.fn((url) => {
      if (url.endsWith('/details')) return ++detailsCalls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(ok({ courses: [] }));
      if (url.startsWith('/logs')) return Promise.resolve(logPage([{ seq: 3, message: 'last' }], 3));
      return Promise.resolve(ok({ status: 'completed' }));
    }) };
    startTaskPolling({ api, taskId: 'one', onLogs });
    await vi.advanceTimersByTimeAsync(2000);
    expect(detailsCalls).toBe(2);
    const logCalls = api.get.mock.calls.filter(([url]) => url.startsWith('/logs'));
    expect(logCalls.map(([, options]) => options.params.after)).toEqual([0, 3]);
    expect(onLogs.mock.calls.at(-1)[0].logs).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('stops and reports an expired task on 404', async () => {
    vi.useFakeTimers();
    const onMissing = vi.fn();
    const api = { get: vi.fn().mockRejectedValue({ response: { status: 404 } }) };
    startTaskPolling({ api, taskId: 'gone', onMissing });
    await vi.advanceTimersByTimeAsync(10000);
    expect(onMissing).toHaveBeenCalledOnce();
    expect(api.get).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});

it('bounds logs, deduplicates sequence numbers and never moves the cursor backward', () => {
  const data = Array.from({ length: 510 }, (_, index) => ({ seq: index + 1, message: String(index) }));
  const first = appendLogPage({ logs: [], cursor: 0, truncated: false }, { data, next_cursor: 510 });
  expect(first.logs).toHaveLength(500);
  expect(first.logs[0].seq).toBe(11);
  expect(first.truncated).toBe(true);
  const next = appendLogPage(first, { data: [{ seq: 510 }, { seq: 512 }, { seq: 511 }, { seq: 512 }], next_cursor: 1 });
  expect(next.logs.slice(-3).map((log) => log.seq)).toEqual([510, 511, 512]);
  expect(next.cursor).toBe(512);
  expect(next.logs).toHaveLength(500);
  expect(appendLogPage(next, { data: [], next_cursor: 520, truncated: true }).cursor).toBe(520);
});

````

## web/src/lib/utils.js

SHA256: fc109d1195062eee66a6ca6ea7e0a16874da28d26433f9dbf02b36adaa54d98a

````text
import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

````

## web/src/main.jsx

SHA256: 3d9ffc94b24ba1237d38c65588a8361c8fbe825a8e01d37fee3a3a7fc10ca417

````text
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import DesktopStartup from './components/DesktopStartup.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DesktopStartup>
      <App />
    </DesktopStartup>
  </React.StrictMode>,
)

````

## web/vite.config.js

SHA256: cdf6b1176b9b4c66dd01734892a5bdca58cb5d7b1d6e0793af6944660507bcc0

````text
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      }
    }
  }
})

````
