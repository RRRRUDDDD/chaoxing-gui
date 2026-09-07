"""Retain P2 deltas without staging the pre-existing, uncommitted P1 foundation.

Run from the repository root. This only prepares task evidence and spec notes;
it never changes the real Git index, commits, or restores working-tree files.
"""

from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


repo = Path.cwd().resolve()
task = Path(__file__).resolve().parent.parent
verification = task / "verification"
baseline = verification / "baseline"
patches = task / "patches"
patches.mkdir(exist_ok=True)
baseline_commit = "d4ae08f44772ef68cffb227feb450c05b84a9d9b"
protected_plan = ".ccg/tasks/archive/2026-09/electron-to-tauri-migration-plan/plan.md"

direct = [
    "web/package.json", "web/package-lock.json", "web/src/main.jsx",
    "web/src/api/axios.js", "web/src/api/tauriAdapter.js",
    "web/src/api/tauriAdapter.test.js", "web/src/api/transportFlow.test.jsx",
    "web/src/lib/desktopBridge.js", "web/src/lib/desktopBridge.test.js",
    "web/src/lib/sessionStore.js", "web/src/lib/sessionStore.test.js",
    "web/src/components/DesktopStartup.jsx", "web/src/components/DesktopStartup.test.jsx",
    "desktop/scripts/p2-smoke.mjs", "desktop/scripts/p2-close-window.ps1",
    "desktop/tests/fixtures/p2_backend.py", "desktop/tests/fixtures/p2-electron.cjs",
]
mixed = [
    "app.py", "desktop/src-tauri/Cargo.toml", "desktop/src-tauri/tauri.conf.json",
    "desktop/src-tauri/src/lib.rs", "desktop/src-tauri/src/api_proxy.rs",
    "desktop/src-tauri/src/backend.rs", "desktop/src-tauri/src/session_store.rs",
    "desktop/src-tauri/src/migration.rs", "desktop/src-tauri/src/bin/fake-backend.rs",
    "desktop/src-tauri/tests/backend_lifecycle.rs",
]
new_with_p1 = ["desktop/src-tauri/tests/api_lifecycle.rs", "tests/test_tauri_entry_contract.py"]
unsnapshotted = ["api/desktop_runtime.py"]


def git(arguments, *, cwd=repo, env=None, data=None):
    result = subprocess.run(["git", *arguments], cwd=cwd, env=env, input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(path):
    return path.read_text(encoding="utf-8-sig")


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


baseline_files = json.loads(text(baseline / "files.json"))
expected_plan = next(item["sha256"].lower() for item in baseline_files if item["path"] == protected_plan)
assert digest(repo / protected_plan) == expected_plan, "User's /plan edit changed"

# Reconstruct the exact P1 app.py text from the initial tracked diff, using an
# isolated index. The initial snapshots did not include this file directly.
scratch = repo / "desktop/src-tauri/target" / ("p2-delivery-" + uuid.uuid4().hex)
scratch.mkdir(parents=True)
assert scratch.resolve().is_relative_to((repo / "desktop/src-tauri/target").resolve())
index_env = {**os.environ, "GIT_INDEX_FILE": str(scratch / "baseline.index")}
git(["read-tree", baseline_commit], env=index_env)
tracked_patch = text(baseline / "tracked.diff")
git(["apply", "--cached", "--whitespace=nowarn", "-"], env=index_env, data=tracked_patch.encode("utf-8"))
(baseline / "app.py").write_bytes(git(["show", ":app.py"], env=index_env))

# Do not put the user's /plan edit or its copied source into the archive commit.
blocks = ["diff --git " + block for block in tracked_patch.split("diff --git ")[1:]]
sanitized = "".join(block for block in blocks if not block.startswith(f"diff --git a/{protected_plan} "))
(baseline / "implementation-tracked.diff").write_text(sanitized, encoding="utf-8")

lessons = {
    ".ccg/spec/frontend/index.md": """

## Tauri 2 桌面边界（P2）

- Tauri 的会话读写失败必须保留为可见错误，不能读取或回写 localStorage；普通浏览器与 Electron 兼容分支分别验证。
- Serde `deny_unknown_fields` 不代表仅接受 JSON object：派生结构也接受数组，枚举也接受对象。IPC envelope、嵌套 DTO 与磁盘 v1 会话应显式要求 object，operation 应先要求 String，再匹配白名单；可空但必填的键需要单独校验。
- 真实 Windows 关窗 smoke 仅向捕获的宿主 PID、准确主窗口标题发送 WM_CLOSE。不要关闭该 PID 的隐藏 dispatcher/COM 窗口；它们被关闭会破坏退出流程。
- GUI smoke 的浏览器、Electron、Tauri 启动与断言失败都必须关闭已创建的测试进程；未知场景名应失败，不能零检查报成功。DOM click 与物理鼠标输入要区分记录。
""",
    ".ccg/spec/backend/index.md": """

## Windows 桌面数据与生命周期（P2）

- 迁移整目录发布前完成白名单复制、校验、文件刷新与完成标记。保留原目录；新目录出现业务数据即停止覆盖；固定 staging 与源/目标路径拒绝 reparse point。
- Windows 目录共享锁不能只用 FILE_READ_ATTRIBUTES：使用 FILE_LIST_DIRECTORY 才能形成所需锁约束；发布通过持有的目录句柄 FileRenameInfo 完成且禁止替换目标。用真实进程中断及 junction 测试验证。
- Tauri 开发 profile 同时隔离业务、日志和 WebView2，调试后端覆写只允许 debug 构建；开发态不隐式读取真实 Electron 数据。
- 同步 HTTP 在阻塞工作线程运行；取消后的工作线程仍可能等待到 30s 网络截止，登记必须保持有界并在实际结束后释放，防止重复 ID 或迟到响应重新生效。后台启动与 stop 需要共享短状态转换锁。
""",
}
for filename, addition in lessons.items():
    original = repo / filename
    snapshot = baseline / filename
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        shutil.copyfile(original, snapshot)
    if addition.splitlines()[2] not in text(original):
        with original.open("ab") as stream:
            stream.write(addition.encode("utf-8"))

patch_paths = mixed + new_with_p1 + list(lessons)
patch = []
for filename in patch_paths:
    previous = None if filename in new_with_p1 else baseline / filename
    before = [] if previous is None else text(previous).splitlines(keepends=True)
    after = text(repo / filename).splitlines(keepends=True)
    patch.append(f"diff --git a/{filename} b/{filename}\n")
    if previous is None:
        patch.append("new file mode 100644\n")
    patch.extend(difflib.unified_diff(before, after, fromfile="/dev/null" if previous is None else "a/" + filename, tofile="b/" + filename))
patch_file = patches / "p2-on-preexisting-p1.patch"
patch_file.write_text("".join(patch), encoding="utf-8")

# Verify the retained delta in an isolated fixture repository, then compare
# every patched file to the delivered working tree. No user file is restored.
fixture = scratch / "patch-check"
fixture.mkdir()
git(["init", "--quiet"], cwd=fixture)
for filename in patch_paths:
    if filename in new_with_p1:
        continue
    destination = fixture / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text(baseline / filename), encoding="utf-8")
git(["apply", "--check", str(patch_file)], cwd=fixture)
git(["apply", str(patch_file)], cwd=fixture)
for filename in patch_paths:
    assert text(fixture / filename) == text(repo / filename), filename
dump(verification / "retained-patch-check.json", {"success": True, "files": patch_paths, "baselineCommit": baseline_commit})

# No byte-for-byte snapshot of the pre-existing runtime was taken on entry.
# Preserve the final file for review, explicitly without inventing a before diff.
for filename in unsnapshotted:
    target = patches / "retained-final-snapshots" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo / filename, target)

manifest = {
    "preparedAt": datetime.now(timezone.utc).isoformat(),
    "baselineCommit": baseline_commit,
    "directSourceCommit": direct,
    "retainedMixedWithP1": mixed,
    "retainedTestsRequiringP1": new_with_p1,
    "retainedWithoutInitialByteSnapshot": unsnapshotted,
    "specNotesRemainWithPreexistingUntrackedSpecs": list(lessons),
    "protectedUserPlan": {"path": protected_plan, "sha256": expected_plan, "unchanged": True, "stage": False},
    "sourceHashes": [{"path": filename, "sha256": digest(repo / filename)} for filename in direct + mixed + new_with_p1 + unsnapshotted + list(lessons)],
    "notes": "P1 foundation is intentionally not adopted into the P2 source commit. Working tree contains the tested integration; the patch is verified against entry snapshots. The whole migration and required external reviews are not complete.",
}
dump(task / "delivery-manifest.json", manifest)
print(json.dumps({"directSourceFiles": len(direct), "retainedPatchFiles": len(patch_paths), "patchVerified": True, "userPlanUnchanged": True}))
