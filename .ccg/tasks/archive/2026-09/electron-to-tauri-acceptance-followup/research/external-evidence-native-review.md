# External invocation evidence

Collected at `2026-09-08T22:18:50.496073+00:00`.

All values below come from completed invocation files and structured API-error records. A failed invocation or blank stdout is `passed=false`; this collector never grants a review pass.

| Lane | Exit | Timed out | Duration (s) | Stdout bytes | Report body | API status | Passed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| native-supplement-review-a | 1 | false | 197.256 | 0 | false | [429] | false |
| native-supplement-review-b | 1 | false | 196.168 | 0 | false | [429] | false |

## Actual API errors

- `native-supplement-review-a`: `2026-09-08T22:00:37.130Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `native-supplement-review-b`: `2026-09-08T22:00:35.984Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`

## Exact session-file access

Session IDs were extracted from each completed lane's `stderr.log`. Only these exact files under `C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui/` were opened; no session-directory enumeration was performed.

- `fc00c15d-af9f-4ead-874e-bb66dbc60925.jsonl`
- `06be634c-65d9-4c82-97d2-b2dbd6c78c4a.jsonl`

## Collection boundaries and verification

- Source result/stdout/stderr and accessed session SHA256 values are recorded in `../verification/external-errors-native-review.json`.
- Output contains allowlisted API-error fields only. User prompts, tool inputs, general messages, and full sessions are omitted; potential credentials are redacted.
- Incomplete or changing sources are recorded as unavailable; this run did not alter any invocation files.
- The collector uses exclusive creation and refuses to overwrite either finished output file.
- The collector was checked by in-memory Python compilation before this capture. No model, network, release host, installer, or business AppData access was used.
- Exit 0 and nonempty stdout would still require lead review of the report. These records establish invocation failure, not a substantive code-review finding.

Collection issues: none.
