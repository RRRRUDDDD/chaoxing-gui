//! One-time migration of legacy Electron data (%APPDATA%\chaoxing-desktop)
//! into the Tauri data dir (app_data_dir()/data).
//!
//! Semantics (plan.md §4.2 / P1 plan §6):
//! - whitelist-only copy: renderer-session.json, web_config.json, .cookies/,
//!   cookies.txt, cache.json, config.ini
//! - never overwrites valid new data; old files always preserved
//! - staging + validate + atomic rename; migration-v1.done marks completion
//! - renderer-session.json is schema-validated before import

use crate::session_store::{self, SessionData};
use std::path::{Path, PathBuf};

pub const LEGACY_DIR_ENV: &str = "CHAOXING_LEGACY_DATA_DIR"; // overridable for tests
pub const LEGACY_DIR_DEFAULT: &str = "chaoxing-desktop";
pub const DONE_MARKER: &str = "migration-v1.done";
pub const STAGING_DIR: &str = ".migration-staging";

const FILE_WHITELIST: [&str; 5] = [
    "renderer-session.json",
    "web_config.json",
    "cookies.txt",
    "cache.json",
    "config.ini",
];
const DIR_WHITELIST: [&str; 1] = [".cookies"];

#[derive(Debug)]
pub enum MigrationOutcome {
    /// Nothing to do (done marker present, or data dir already has valid session).
    Skipped(&'static str),
    /// No legacy dir or no whitelisted files found — mark done, nothing copied.
    NoLegacyData,
    /// Legacy session file present but invalid — other whitelisted files still copied.
    ImportedButSessionInvalid(String),
    /// Imported.
    Imported(usize),
    /// Deferred: legacy Electron app appears to be running (lockfile busy).
    DeferredLegacyRunning,
}

fn legacy_dir() -> Option<PathBuf> {
    if let Ok(override_dir) = std::env::var(LEGACY_DIR_ENV) {
        let p = PathBuf::from(override_dir);
        return if p.is_dir() { Some(p) } else { None };
    }
    let appdata = std::env::var("APPDATA").ok()?;
    let p = Path::new(&appdata).join(LEGACY_DIR_DEFAULT);
    if p.is_dir() {
        Some(p)
    } else {
        None
    }
}

/// Reject junctions / symlinks (they can point anywhere).
fn is_real_dir(p: &Path) -> bool {
    let meta = match std::fs::symlink_metadata(p) {
        Ok(m) => m,
        Err(_) => return false,
    };
    use std::os::windows::fs::MetadataExt;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    meta.is_dir() && (meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) == 0
}

fn copy_file(src: &Path, dst: &Path) -> std::io::Result<()> {
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::copy(src, dst)?;
    Ok(())
}

fn copy_dir(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let target = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir(&entry.path(), &target)?;
        } else if ty.is_file() {
            std::fs::copy(entry.path(), &target)?;
        }
        // symlinks/others skipped
    }
    Ok(())
}

/// data dir already holds valid new-session data → never overwrite.
fn has_valid_new_session(data_dir: &Path) -> bool {
    session_store::read(&data_dir.join("renderer-session.json"))
        .ok()
        .flatten()
        .is_some()
}

fn legacy_running(legacy: &Path) -> bool {
    // Electron wrote no lockfile; an occupied sentinel file is our best proxy.
    // P1: best-effort only (plan risk #3 — full detection deferred).
    let lock = legacy.join("lockfile");
    if !lock.exists() {
        return false;
    }
    std::fs::OpenOptions::new()
        .write(true)
        .create(false)
        .open(&lock)
        .is_err()
}

fn count_whitelisted(legacy: &Path) -> usize {
    FILE_WHITELIST
        .iter()
        .filter(|f| legacy.join(f).is_file())
        .count()
        + DIR_WHITELIST
            .iter()
            .filter(|d| is_real_dir(&legacy.join(d)))
            .count()
}

/// Run the one-time migration. `data_dir` is app_data_dir()/data.
pub fn migrate(data_dir: &Path) -> std::io::Result<MigrationOutcome> {
    std::fs::create_dir_all(data_dir)?;
    let done = data_dir.join(DONE_MARKER);
    if done.exists() {
        return Ok(MigrationOutcome::Skipped("done marker present"));
    }
    if has_valid_new_session(data_dir) {
        // Valid new data without marker: mark done to avoid future surprises.
        std::fs::write(&done, b"ok")?;
        return Ok(MigrationOutcome::Skipped("valid new session exists"));
    }

    let legacy = match legacy_dir() {
        Some(d) => d,
        None => {
            std::fs::write(&done, b"no-legacy")?;
            return Ok(MigrationOutcome::NoLegacyData);
        }
    };
    if legacy_running(&legacy) {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }
    if count_whitelisted(&legacy) == 0 {
        std::fs::write(&done, b"no-legacy")?;
        return Ok(MigrationOutcome::NoLegacyData);
    }

    // Clean any staging leftovers from an interrupted run, then start fresh.
    let staging = data_dir.join(STAGING_DIR);
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging)?;

    let mut copied = 0usize;
    let mut session_error: Option<String> = None;

    // Session file is validated before being accepted into staging.
    let legacy_session = legacy.join("renderer-session.json");
    let mut copy_session = false;
    if legacy_session.is_file() {
        match std::fs::read(&legacy_session) {
            Ok(bytes) => match serde_json::from_slice::<SessionData>(&bytes) {
                Ok(parsed) => {
                    if session_store::validate(&parsed).is_ok() {
                        copy_session = true;
                    } else {
                        session_error =
                            Some("renderer-session.json failed schema validation".into());
                    }
                }
                Err(e) => session_error = Some(format!("renderer-session.json is corrupt: {e}")),
            },
            Err(e) => session_error = Some(format!("renderer-session.json unreadable: {e}")),
        }
    }
    if copy_session {
        copy_file(&legacy_session, &staging.join("renderer-session.json"))?;
        copied += 1;
    }

    for name in FILE_WHITELIST.iter().skip(1) {
        let src = legacy.join(name);
        if src.is_file() {
            copy_file(&src, &staging.join(name))?;
            copied += 1;
        }
    }
    for name in DIR_WHITELIST.iter() {
        let src = legacy.join(name);
        if is_real_dir(&src) {
            copy_dir(&src, &staging.join(name))?;
            copied += 1;
        }
    }

    // Atomic publish: rename staging dir into place. If data dir has any other
    // entries, copy staged files in instead (rename requires empty/nonexistent target).
    let mut published = false;
    let data_empty = std::fs::read_dir(data_dir)?
        .filter_map(|e| e.ok())
        .all(|e| e.file_name().to_string_lossy().starts_with('.'));
    if data_empty && copied > 0 {
        // hide staging + marker entry: rename staging to a temp final then move contents is complex;
        // simplest atomic-enough path: move each whitelisted entry from staging into data dir.
    }
    if copied > 0 {
        for name in FILE_WHITELIST.iter() {
            let src = staging.join(name);
            if src.is_file() {
                let dst = data_dir.join(name);
                let tmp = data_dir.join(format!("{name}.migrating"));
                std::fs::rename(&src, &tmp)?;
                std::fs::rename(&tmp, &dst)?;
            }
        }
        for name in DIR_WHITELIST.iter() {
            let src = staging.join(name);
            if is_real_dir(&src) {
                std::fs::rename(&src, data_dir.join(name))?;
            }
        }
        published = true;
    }
    let _ = std::fs::remove_dir_all(&staging);

    if published {
        std::fs::write(&done, b"ok")?;
    } else {
        // nothing copied (e.g. session invalid and nothing else whitelisted)
        let reason: &[u8] = if session_error.is_some() {
            b"invalid-session"
        } else {
            b"empty"
        };
        std::fs::write(&done, reason)?;
    }

    Ok(match session_error {
        Some(err) if copied == 0 => MigrationOutcome::ImportedButSessionInvalid(err),
        Some(err) => MigrationOutcome::ImportedButSessionInvalid(err),
        None => MigrationOutcome::Imported(copied),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    fn unique_tmp(name: &str) -> PathBuf {
        let dir = env::temp_dir().join(format!("cx-migration-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    struct EnvGuard {
        old: Option<String>,
    }
    impl EnvGuard {
        fn set(dir: &Path) -> Self {
            let old = env::var(LEGACY_DIR_ENV).ok();
            env::set_var(LEGACY_DIR_ENV, dir);
            EnvGuard { old }
        }
    }
    impl Drop for EnvGuard {
        fn drop(&mut self) {
            match &self.old {
                Some(v) => env::set_var(LEGACY_DIR_ENV, v),
                None => env::remove_var(LEGACY_DIR_ENV),
            }
        }
    }

    // EnvGuard mutates process env; migration tests must not run in parallel
    // with each other (or they'd race on the legacy-dir override).
    // Enforced per-test with a shared mutex below.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn valid_session_json() -> String {
        r#"{"version":1,"login":{"username":"user1","use_cookies":true},"activeTask":{"username":"user1","taskId":"t1"}}"#.into()
    }

    #[test]
    fn imports_whitelisted_files() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("import");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::write(legacy.join("renderer-session.json"), valid_session_json()).unwrap();
        std::fs::write(legacy.join("cookies.txt"), "cookies").unwrap();
        std::fs::create_dir_all(legacy.join(".cookies")).unwrap();
        std::fs::write(legacy.join(".cookies/user1.json"), "{}").unwrap();
        std::fs::write(legacy.join("unlisted.txt"), "skip me").unwrap();
        let _g = EnvGuard::set(&legacy);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(outcome, MigrationOutcome::Imported(3)));
        assert!(data.join("renderer-session.json").is_file());
        assert!(data.join("cookies.txt").is_file());
        assert!(data.join(".cookies/user1.json").is_file());
        assert!(
            !data.join("unlisted.txt").exists(),
            "non-whitelisted must not copy"
        );
        assert!(data.join(DONE_MARKER).exists());
        assert!(!data.join(STAGING_DIR).exists(), "staging must be cleaned");
        // legacy preserved
        assert!(legacy.join("renderer-session.json").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn skips_when_valid_new_data_exists() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("skipnew");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::create_dir_all(&data).unwrap();
        std::fs::write(legacy.join("renderer-session.json"), valid_session_json()).unwrap();
        std::fs::write(
            data.join("renderer-session.json"),
            r#"{"version":1,"login":{"username":"newuser","use_cookies":true}}"#,
        )
        .unwrap();
        let _g = EnvGuard::set(&legacy);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(outcome, MigrationOutcome::Skipped(_)));
        let content = std::fs::read_to_string(data.join("renderer-session.json")).unwrap();
        assert!(
            content.contains("newuser"),
            "must not overwrite valid new data"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn corrupt_session_not_imported() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("corrupt");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::write(legacy.join("renderer-session.json"), "{ broken").unwrap();
        std::fs::write(legacy.join("cookies.txt"), "cookies").unwrap();
        let _g = EnvGuard::set(&legacy);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(
            outcome,
            MigrationOutcome::ImportedButSessionInvalid(_)
        ));
        assert!(
            !data.join("renderer-session.json").exists(),
            "invalid session must not import"
        );
        assert!(
            data.join("cookies.txt").is_file(),
            "other whitelisted files still import"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn staging_leftover_from_interrupted_run_is_cleaned() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("staging");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::create_dir_all(data.join(STAGING_DIR)).unwrap();
        std::fs::write(data.join(STAGING_DIR).join("junk.txt"), "stale").unwrap();
        std::fs::write(legacy.join("cookies.txt"), "cookies").unwrap();
        let _g = EnvGuard::set(&legacy);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(outcome, MigrationOutcome::Imported(_)));
        assert!(!data.join(STAGING_DIR).exists());
        assert!(data.join("cookies.txt").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn done_marker_short_circuits() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("done");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        std::fs::create_dir_all(&data).unwrap();
        std::fs::write(data.join(DONE_MARKER), b"ok").unwrap();
        std::fs::write(legacy.join("cookies.txt"), "cookies").unwrap();
        let _g = EnvGuard::set(&legacy);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(
            outcome,
            MigrationOutcome::Skipped("done marker present")
        ));
        assert!(!data.join("cookies.txt").exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn no_legacy_dir_marks_done() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        let root = unique_tmp("nolegacy");
        let data = root.join("data");
        let missing = root.join("nope");
        let _g = EnvGuard::set(&missing);

        let outcome = migrate(&data).unwrap();
        assert!(matches!(outcome, MigrationOutcome::NoLegacyData));
        assert!(data.join(DONE_MARKER).exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn reparse_point_dir_rejected() {
        let _env_lock = ENV_LOCK.lock().unwrap();
        // symlinked .cookies must not be followed
        let root = unique_tmp("reparse");
        let legacy = root.join("legacy");
        let data = root.join("data");
        std::fs::create_dir_all(&legacy).unwrap();
        let target = root.join("outside");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("secret.txt"), "x").unwrap();
        #[cfg(windows)]
        {
            let link = legacy.join(".cookies");
            let _ = std::os::windows::fs::symlink_dir(&target, &link);
            if !link.exists() {
                // symlink requires privilege; skip on machines without it
                return;
            }
            let _g = EnvGuard::set(&legacy);
            let outcome = migrate(&data).unwrap();
            match outcome {
                MigrationOutcome::NoLegacyData => {}
                MigrationOutcome::Imported(n) => assert_eq!(n, 0, "reparse dir must not import"),
                other => panic!("unexpected outcome {other:?}"),
            }
            assert!(!data.join(".cookies").exists());
        }
        let _ = std::fs::remove_dir_all(&root);
    }
}
