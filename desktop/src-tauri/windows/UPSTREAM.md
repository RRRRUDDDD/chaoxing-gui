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
