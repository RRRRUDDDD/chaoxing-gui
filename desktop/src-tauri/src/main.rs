#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod webview_runtime;

fn main() -> std::process::ExitCode {
    webview_runtime::run(chaoxing_desktop_lib::run)
}
