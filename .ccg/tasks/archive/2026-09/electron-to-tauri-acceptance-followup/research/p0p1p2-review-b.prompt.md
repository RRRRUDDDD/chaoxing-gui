ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/reviewer.md
<TASK>
Stage p0p1p2-review, independent lane b.
Repository: E:/Downloads/45/chaoxing-gui; HEAD 37fde9160229e99d2fa837d2f1a850203c260d81 on main.
P1/P2 foundation and P3 implementation are already committed (9219fe24, 0d224f38, 6b2c5a2d). Do not repeat implementation or apply historical patches.
Read .ccg/tasks/electron-to-tauri-acceptance-followup/requirements.md and plan.md; current source snapshot .ccg/tasks/electron-to-tauri-acceptance-followup/research/current-p0-p3-source-snapshot.md and hash manifest current-p0-p3-source-manifest.json. Source bodies can be read individually at manifest paths; do not rely on empty working git diff. Locks are on disk and hashed in manifest.
Read .ccg/spec/backend/index.md and frontend/index.md; parent .ccg/tasks/electron-to-tauri-implementation/task.json, implementation.md, verification/p3-final-handoff.json; archived P3 delivery.md, review.md and reproduce.md under .ccg/tasks/archive/2026-09/electron-to-tauri-p3/.
Current local P3 evidence: Rust114, Python3.11/3.13 each160, Node20 Web125/Desktop86, debug host23, unsigned NSIS/ZIP bytes verified. These are historical local results, not remote CI or release installation acceptance. All six prior external calls failed 429 without reports: NO external passing result exists.
Read-only work: no code/task/Git writes, no spawn, no recursive CCG workflow, no processes that run release host or installer, no real accounts or AppData business data, no publishing/push/tag/certificate creation. Protected user archived plan.md must remain untouched; current hash differs from handoff by user edit. Preserve poc-window.png. Do not enter P4/P5. You may read relevant complete files and recorded test evidence; do not claim tests not run.

Perform formal independent P0/P1/P2 code review of CURRENT integrated Rust host, Python desktop protocol, Tauri/HTTP frontend adapter, session persistence, transaction import, cancellation/shutdown and Electron/browser coexistence. Include main.jsx/App.jsx and consumers, not only adapter helpers. Read P0 PoC evidence and P2 archive delivery/review/verification as context, but assess current source after P3. Baseline relevant commit range 5899b5f..37fde91; current snapshot authoritative. Focus frontend async/account/cancellation/HTTP semantics, Python runtime, Electron/browser behavior and cross-layer integration, while considering all P0/P1/P2 gates. Identify exact material current bugs, file/line, trigger and correction. Old resolved findings must not be repeated as current. CI/release/clean VM absent evidence is a validation limitation, never an automatic code pass.
</TASK>
OUTPUT: 中文、明确实际结论，Critical/Warning/Info 分级（无发现也明确），具体文件行号/触发/影响/建议；区分已审源码与未验证运行环境。仅输出报告，不修改文件。分析阶段附建议顺序。

