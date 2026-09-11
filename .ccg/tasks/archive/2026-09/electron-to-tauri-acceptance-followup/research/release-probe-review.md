# Supplementary local review: optimized Release build-profile probe

The Release smoke failure is a false negative in the byte-string precheck. The packaged host implements the native probe; its optimized comparison does not retain the complete ASCII string. Recommend preserving the Debug precheck and using the native Release probe only after disposable-user permission, fresh-profile checks, and explicit ownership claims, with verified process and profile cleanup.

This is a local, read-only review, not the required external-model review or a P3 acceptance pass. No product, installer, or test command was executed by this reviewer. The lead ran the cited Windows Sandbox checks. Earlier external-review evidence remains unchanged.

## Concrete evidence

Line references below describe the pre-fix source captured for Sandbox run `337961b4979e4d81abe3fb42f0f90049`, source commit `37fde9160229e99d2fa837d2f1a850203c260d81`. The captured `desktop/scripts/p3-smoke.mjs` SHA256 is `5accf433f07a46c110042914b6cb12d0104a17f1d2b718c06de577ee52bddcf3`.

- [p3-smoke.mjs:217](/E:/Downloads/45/chaoxing-gui/desktop/scripts/p3-smoke.mjs:217) searches for the complete `--check-debug-build` byte sequence before starting a supervisor. The first guest smoke result has `success=false`, `checks=[]`, and `processes=[]`; it failed at this assertion, before native profile verification or fake-backend assertions.
- [webview_runtime.rs:36](/E:/Downloads/45/chaoxing-gui/desktop/src-tauri/src/webview_runtime.rs:36) matches the flag and returns compiled Debug `0` / Release `4` before runtime detection, UI, backend, or data setup. The entry point calls this gate at [main.rs:6](/E:/Downloads/45/chaoxing-gui/desktop/src-tauri/src/main.rs:6).
- Read the portable ZIP in memory with Python `zipfile`, parsed its PE sections, and used the already installed `capstone`/`pefile` libraries for static disassembly. No extraction or executable launch was needed. ZIP SHA256 `3318ca14cf4c20818f69fedded0f52b479ab542003cc12625e19398f7b993e76` matches the archived P3 artifact manifest. Its `chaoxing-gui-tauri.exe` member is 9,379,328 bytes, SHA256 `f75e63e7bb16a0be922e41830c4417b8e03b919dc0972a707cef64c6f9581a2d`.
- That host has zero complete ASCII or UTF-16 occurrences of `--check-debug-build`. Instead, `.rdata` stores `heck-debug-build` at file offset `0x636d30` and `--check-debug-bu` at `0x636d40`. Each is 16 bytes; together they cover overlapping slices of the 19-byte flag.

| Static instruction address | Meaning |
| --- | --- |
| `0x140001be8`, `0x140001bf0` | Load the two constants into `xmm7` and `xmm8`. |
| `0x140001ca2` | Compare argument length with `0x13` (19). |
| `0x140001cac` through `0x140001cba` | Load argument bytes at offsets 0 and 3; compare each 16-byte slice against its constant with `pcmpeqb`. |
| `0x140001cbe` through `0x140001cd1` | Combine comparisons, require all bytes equal, and branch to the matched-flag path. |

This confirms optimized overlapping vector comparisons, not removal of the flag behavior. `strip=true` in [Cargo.toml:42](/E:/Downloads/45/chaoxing-gui/desktop/src-tauri/Cargo.toml:42) removes symbols but does not make a whole-string search a valid capability contract.

The lead's second fresh Sandbox run `dbf135f6e7ae4958a79316d35fa2bf1a` supplies actual native confirmation: its guest `result.json` records `native-release-build-profile` exit `4`, expected `4`, success `true`, from `2026-09-08T13:34:47.8156645Z` to `2026-09-08T13:34:47.8330552Z`. The unchanged harness then fails `release-fake` with exit `1`. See [sandbox launch record](/E:/Downloads/45/chaoxing-gui/.ccg/tasks/electron-to-tauri-acceptance-followup/verification/sandbox-launch-run-dbf135f6e7ae4958a79316d35fa2bf1a.json) for the exact guest-result path. This proves the native flag on that artifact; it does not prove the remaining smoke scenarios or Job cleanup assertions.

## Choice of correction

| Option | Assessment |
| --- | --- |
| Add a stable embedded capability marker | Possible longer-term contract, but it requires Rust changes, a retained final-PE marker, rebuilt packages, and new artifact hashes. A plain constant or `#[used]` alone does not guarantee survival through final linker garbage collection. Verify retention in both final Debug and Release executables; still require native exit-code verification. |
| Keep the Debug byte guard; omit it only for already guarded Release probing | Smallest appropriate fix for the current artifacts. Preserve `assertReleasePermission`, Windows known-folder lookup, fresh-profile/reparse-point checks, and native expected exit `4`. Add profile claims and cleanup around the Release probe before allowing an old host that ignores the flag to execute. |

Do not remove the pre-execution guard from Debug: a Release/legacy host passed as Debug may ignore the flag and use production profile paths before a later exit mismatch can reject it. [lib.rs:105](/E:/Downloads/45/chaoxing-gui/desktop/src-tauri/src/lib.rs:105) compiles development overrides out of Release.

## Ownership and cleanup requirements

1. Preserve current guard order. [main:747](/E:/Downloads/45/chaoxing-gui/desktop/scripts/p3-smoke.mjs:747) checks Release permission before Windows context and [publishReleaseProfileOwnership:124](/E:/Downloads/45/chaoxing-gui/desktop/scripts/p3-smoke.mjs:124) validates freshness and writes the handoff. Publication does **not** claim directories. The first actual claims currently occur much later in `runTauri` at line 437. A flag-ignoring host can create unowned profile data during the probe unless this gap is closed.
2. Claim the two current-run application roots with existing `claimProfileRoots` before native Release probing, inside the cleanup scope. Keep all legacy roots `claim:false`, validate SID/run ID/path, and publish the handoff first. Never adopt a directory that appeared after the absent preflight.
3. Retain `NativeSupervisor`: [p3-windows-process.cs:137](/E:/Downloads/45/chaoxing-gui/desktop/tests/fixtures/p3-windows-process.cs:137) creates a kill-on-close Job without breakaway, then assigns the suspended child before its first instruction at line 173. Keep expected Release exit `4`, bounded `waitForEmptyJob`, captured identities, empty active/remaining lists, and observed processes dead. A timeout, wrong exit, or forced fallback remains a failed probe.
4. On success or failure, dispose the captured Job before deleting owned profiles. Use `withCleanup` or equivalent error aggregation so a primary probe failure and cleanup failure both survive. Only use `removeOwnedProfiles` after verified termination; it already restricts removal to exact current-run roots and rejects links. A verified forced kill can permit cleanup, but cannot satisfy successful native-probe acceptance.
5. Handle start failure explicitly. [NativeSupervisor.dispose:315](/E:/Downloads/45/chaoxing-gui/desktop/scripts/p3-smoke.mjs:315) sets `verified=true` only after a captured identity completes `finish`. With no identity it can return an empty default **without** `verified`. Treat a proven never-started process separately; otherwise retain the owned roots and report cleanup unverifiable. Do not interpret empty arrays alone as a verified captured tree.
6. A killed enclosing smoke process relies on the handoff plus [cleanupChildProfileInvocation:215](/E:/Downloads/45/chaoxing-gui/desktop/scripts/p3-installation.mjs:215), which verifies the outer captured tree and exact ownership before removal. Direct smoke must also clean its own probe roots; merely publishing the handoff is insufficient. Preserve profile removal checks if legacy/unowned roots appear, and report any retained evidence instead of broadening deletion.

## Validation still needed after the lead's patch

Existing [p3-smoke.test.mjs:54](/E:/Downloads/45/chaoxing-gui/desktop/tests/p3-smoke.test.mjs:54) tests compiled exit-code mismatch; ownership/race tests begin at line 70. They do not exercise the markerless Release probe plus profile cleanup. Add focused coverage for denied Release before spawn, markerless Debug before spawn, a guarded markerless Release, wrong exit/timeout, failed start, and cleanup failure with no false pass. Keep tests on synthetic roots/processes.

Then rerun the real Release fake/frozen and relevant installation/portable scenarios only in the disposable guest, retaining actual native exit `4`, captured Job-empty evidence, and owned-profile removal results. This reviewer ran no tests and makes no claim that those pending checks passed. This recommendation stays within P3; it changes neither P4/P5 scope nor the external-review requirement.
