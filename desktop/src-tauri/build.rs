fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "api_request",
            "api_cancel",
            "backend_status",
            "session_read",
            "session_remember_login",
            "session_remember_task",
            "session_clear",
        ]),
    ))
    .unwrap();
}
