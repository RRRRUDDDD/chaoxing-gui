# External invocation evidence

Collected at `2026-09-08T16:58:57.500758+00:00`.

All values below come from completed invocation files and structured API-error records. A failed invocation or blank stdout is `passed=false`; this collector never grants a review pass.

| Lane | Exit | Timed out | Duration (s) | Stdout bytes | Report body | API status | Passed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| followup-final-review-a | 1 | false | 205.835 | 0 | false | [429] | false |
| followup-final-review-b | 1 | false | 206.328 | 0 | false | [429] | false |

## Actual API errors

- `followup-final-review-a`: `2026-09-08T16:14:02.761Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `followup-final-review-b`: `2026-09-08T16:14:03.288Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`

## Exact session-file access

Session IDs were extracted from each completed lane's `stderr.log`. Only these exact files under `C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui/` were opened; no session-directory enumeration was performed.

- `79c36652-51e8-4717-b04f-70ca4764cfd4.jsonl`
- `42519a6f-37fb-446b-bf55-d8a1a159d2e4.jsonl`

## Collection boundaries and verification

- Source result/stdout/stderr and accessed session SHA256 values are recorded in `../verification/external-errors-final-review.json`.
- Output contains allowlisted API-error fields only. User prompts, tool inputs, general messages, and full sessions are omitted; potential credentials are redacted.
- Incomplete or changing sources are recorded as unavailable; this run did not alter any invocation files.
- The collector uses exclusive creation and refuses to overwrite either finished output file.
- The collector was checked by in-memory Python compilation before this capture. No model, network, release host, installer, or business AppData access was used.
- Exit 0 and nonempty stdout would still require lead review of the report. These records establish invocation failure, not a substantive code-review finding.

Collection issues: none.
