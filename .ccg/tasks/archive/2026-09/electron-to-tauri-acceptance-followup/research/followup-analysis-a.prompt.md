ROLE_FILE: C:/Users/RUD/.claude/.ccg/prompts/claude/analyzer.md
<TASK>
Stage followup-analysis, independent lane a.
Repository: E:/Downloads/45/chaoxing-gui; HEAD 37fde9160229e99d2fa837d2f1a850203c260d81 on main.
P1/P2 foundation and P3 implementation are already committed (9219fe24, 0d224f38, 6b2c5a2d). Do not repeat implementation or apply historical patches.
Read .ccg/tasks/electron-to-tauri-acceptance-followup/requirements.md and plan.md; current source snapshot .ccg/tasks/electron-to-tauri-acceptance-followup/research/current-p0-p3-source-snapshot.md and hash manifest current-p0-p3-source-manifest.json. Source bodies can be read individually at manifest paths; do not rely on empty working git diff. Locks are on disk and hashed in manifest.
Read .ccg/spec/backend/index.md and frontend/index.md; parent .ccg/tasks/electron-to-tauri-implementation/task.json, implementation.md, verification/p3-final-handoff.json; archived P3 delivery.md, review.md and reproduce.md under .ccg/tasks/archive/2026-09/electron-to-tauri-p3/.
Current local P3 evidence: Rust114, Python3.11/3.13 each160, Node20 Web125/Desktop86, debug host23, unsigned NSIS/ZIP bytes verified. These are historical local results, not remote CI or release installation acceptance. All six prior external calls failed 429 without reports: NO external passing result exists.
Read-only work: no code/task/Git writes, no spawn, no recursive CCG workflow, no processes that run release host or installer, no real accounts or AppData business data, no publishing/push/tag/certificate creation. Protected user archived plan.md must remain untouched; current hash differs from handoff by user edit. Preserve poc-window.png. Do not enter P4/P5. You may read relevant complete files and recorded test evidence; do not claim tests not run.

Provide actionable analysis for this acceptance follow-up only. P0-P3 implementation is complete locally, external review and clean environment acceptance are pending. Identify current-source review priorities, what can be safely verified on RUD without release execution, prerequisites for already available disposable Windows VM/user/runner, and concrete stop conditions. Avoid proposing P4/P5 or republishing commits. Focus: P0/P1/P2 contracts, IPC/Origin/token boundaries, session/migration/lifecycle correctness and current review coverage. Both lanes should consider complete scope. The root is checking actual environment independently; do not assume any VM/runner/credentials are available.
</TASK>
OUTPUT: 中文、明确实际结论，Critical/Warning/Info 分级（无发现也明确），具体文件行号/触发/影响/建议；区分已审源码与未验证运行环境。仅输出报告，不修改文件。分析阶段附建议顺序。

