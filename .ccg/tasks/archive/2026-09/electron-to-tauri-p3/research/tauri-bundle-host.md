# NSIS / portable host identity

The first full payload hash gate rejected `chaoxing-gui-tauri.exe`. A read-only extraction and byte comparison found exactly three differing bytes at offsets 7042792–7042794: `UNK` in the release/portable executable versus `NSS` in the NSIS executable. No installer or product host was run. Full hashes and offsets are in `verification/nsis-host-diagnostic-5072e048073649c48dc3e997345e6172/comparison.json`.

Pinned upstream sources, retrieved through Firecrawl and cached outside Git:

- https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle.rs — `patch_binary` replaces `__TAURI_BUNDLE_TYPE_VAR_UNK` with `__TAURI_BUNDLE_TYPE_VAR_NSS`; the main executable is signed after patching and restored to its original unsigned bytes after bundling.
- https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle/windows/nsis/mod.rs — resource enumeration can sign previously unsigned EXE/DLL files in place.
- https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.11.4/crates/tauri-bundler/src/bundle/windows/sign.rs — `should_sign` checks EXE/DLL extensions and existing signature validity.

The build now derives the complete expected unsigned NSIS host hash from the original compiler output by replacing exactly one marker in memory. It never changes the original host and never derives the expectation from extracted installer bytes. For signing, a callback captures the exact signed NSIS host hash after checking its pre-sign source; the restored portable host is signed separately. Backend resource callbacks verify their manifest records and retain their existing bytes, including vendor signatures, so a bundle cannot invalidate the staging manifest by re-signing a DLL.

`setup.exe.manifest.json` binds the installer and portable artifact hashes and records the separate NSIS host expectation. Content and installation checks use all portable resource hashes plus that exact host record. Extracted signed hosts, uninstallers and setup executables must have valid matching signatures. No host bytes are excluded from content verification. Actual certificate signing remains unverified until a usable certificate is available.
