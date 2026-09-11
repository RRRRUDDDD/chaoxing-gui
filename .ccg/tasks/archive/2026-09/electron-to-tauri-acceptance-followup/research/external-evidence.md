# External invocation evidence

Collected at `2026-09-08T12:24:42.204820+00:00`.

All values below come from completed invocation files and structured API-error records. A failed invocation or blank stdout is `passed=false`; this collector never grants a review pass.

| Lane | Exit | Timed out | Duration (s) | Stdout bytes | Report body | API status | Passed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| followup-analysis-a | 1 | false | 194.409 | 0 | false | [429] | false |
| followup-analysis-b | 1 | false | 204.862 | 0 | false | [429] | false |
| p0p1p2-review-a | 1 | false | 194.273 | 0 | false | [429] | false |
| p0p1p2-review-b | 1 | false | 210.192 | 0 | false | [429] | false |
| p3-review-a | 1 | false | 201.815 | 0 | false | [429] | false |
| p3-review-b | 1 | false | 205.466 | 0 | false | [429] | false |

## Actual API errors

- `followup-analysis-a`: `2026-09-08T08:24:07.265Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `followup-analysis-b`: `2026-09-08T08:24:17.589Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `p0p1p2-review-a`: `2026-09-08T09:02:10.638Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `p0p1p2-review-b`: `2026-09-08T09:02:26.536Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `p3-review-a`: `2026-09-08T09:46:59.272Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`
- `p3-review-b`: `2026-09-08T09:47:02.979Z`, JSONL line 7, status `429`, type `rate_limit`: `API Error: Request rejected (429) · Service Unavailable`

## Exact session-file access

Session IDs were extracted from each completed lane's `stderr.log`. Only these exact files under `C:/Users/RUD/.claude/projects/E--Downloads-45-chaoxing-gui/` were opened; no session-directory enumeration was performed.

- `849048d4-bbd3-43fd-ae5e-f9a2bdf4e7a2.jsonl`
- `17b7ce2b-e808-4b08-abdd-e3d36f91fc85.jsonl`
- `11362d3a-3d50-4fe0-892b-e996c1db8023.jsonl`
- `843b2c00-a95b-4b8b-bdde-5c1b094ee39d.jsonl`
- `f1e51f95-543d-4cf9-ab5d-cf02d0a4fe79.jsonl`
- `c26cf619-1926-4bad-b95a-2e00d74ac92d.jsonl`

## Collection boundaries and verification

- Source result/stdout/stderr and accessed session SHA256 values are recorded in `../verification/external-errors.json`.
- Output contains allowlisted API-error fields only. User prompts, tool inputs, general messages, and full sessions are omitted; potential credentials are redacted.
- Incomplete or changing sources are recorded as unavailable; this run did not alter any invocation files.
- The collector uses exclusive creation and refuses to overwrite either finished output file.
- The collector was checked by in-memory Python compilation before this capture. No model, network, release host, installer, or business AppData access was used.
- Exit 0 and nonempty stdout would still require lead review of the report. These records establish invocation failure, not a substantive code-review finding.

Collection issues: none.
