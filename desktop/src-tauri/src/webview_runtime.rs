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
