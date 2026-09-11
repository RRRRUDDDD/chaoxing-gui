//! One-time, read-only import of legacy Electron business data.
//!
//! Copy and validate a sibling staging directory, including its completion
//! marker, then publish the whole directory with one non-replacing rename.
//! A process exit on either side of that rename is safe to retry. The host must
//! not start the backend after an IO error or while the old Electron is running.

use crate::session_store;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{self, Write};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::HANDLE;
use windows::Win32::Storage::FileSystem::{
    FileRenameInfo, SetFileInformationByHandle, DELETE, FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, FILE_RENAME_INFO,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
};
use windows::Win32::UI::WindowsAndMessaging::{FindWindowExW, HWND_MESSAGE};

pub const LEGACY_DIR_ENV: &str = "CHAOXING_LEGACY_DATA_DIR";
pub const LEGACY_DIR_DEFAULT: &str = "chaoxing-desktop";
pub const DONE_MARKER: &str = "migration-v1.done";
/// A sibling of `data`, never a directory inside the published business data.
pub const STAGING_DIR: &str = ".migration-staging";

const FILE_WHITELIST: [&str; 5] = [
    "renderer-session.json",
    "web_config.json",
    "cookies.txt",
    "cache.json",
    "config.ini",
];
const DIR_WHITELIST: [&str; 1] = [".cookies"];
const INVALID_SESSION_NOTICE: &str =
    "Legacy renderer-session.json is corrupt, exceeds 4096 bytes, or has an invalid schema; it was not imported.";

#[derive(Debug)]
pub enum MigrationOutcome {
    /// A completed import or existing new business data prevents another import.
    Skipped(&'static str),
    /// No legacy directory or whitelisted data was found.
    NoLegacyData,
    /// Other data may have been imported; the invalid legacy session was kept only in the old directory.
    ImportedButSessionInvalid(String),
    Imported(usize),
    /// The Electron ProcessSingleton window exists, or a legacy lock is busy.
    DeferredLegacyRunning,
}

fn legacy_dir() -> io::Result<Option<PathBuf>> {
    if let Some(override_dir) = std::env::var_os(LEGACY_DIR_ENV) {
        if override_dir.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty legacy data directory",
            ));
        }
        // Existence and permissions must be checked by the importer. Treating
        // an inaccessible directory as missing would permanently mark it done.
        return Ok(Some(PathBuf::from(override_dir)));
    }
    Ok(std::env::var_os("APPDATA")
        .filter(|value| !value.is_empty())
        .map(|appdata| PathBuf::from(appdata).join(LEGACY_DIR_DEFAULT)))
}

fn reject_reparse(path: &Path, metadata: &Metadata) -> io::Result<()> {
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("migration refuses reparse point: {}", path.display()),
        ));
    }
    Ok(())
}

/// Check every ancestor before accessing a path, not just its final component.
fn checked_metadata(path: &Path) -> io::Result<Option<Metadata>> {
    let mut result = None;
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        let metadata = match fs::symlink_metadata(ancestor) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        reject_reparse(ancestor, &metadata)?;
        if ancestor != path && !metadata.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "migration parent is not a directory",
            ));
        }
        result = Some(metadata);
    }
    Ok(result)
}

fn require_directory(path: &Path, metadata: &Metadata) -> io::Result<()> {
    reject_reparse(path, metadata)?;
    if !metadata.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::NotADirectory,
            "migration path is not a directory",
        ));
    }
    Ok(())
}

/// A no-delete directory handle prevents an already-checked directory from
/// being replaced by a junction while descendants are copied or cleaned.
fn lock_directory(path: &Path) -> io::Result<File> {
    directory_handle(path, FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0)
}

fn directory_handle(path: &Path, access: u32) -> io::Result<File> {
    let handle = OpenOptions::new()
        // FILE_READ_ATTRIBUTES alone is a metadata-only open; Windows does
        // not enforce its sharing flags. LIST_DIRECTORY makes the lock real.
        .access_mode(access)
        .share_mode(FILE_SHARE_READ.0 | FILE_SHARE_WRITE.0)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS.0 | FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    require_directory(path, &handle.metadata()?)?;
    Ok(handle)
}

fn lock_directory_chain(path: &Path, create: bool) -> io::Result<Vec<File>> {
    let mut handles = Vec::new();
    for ancestor in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        match fs::symlink_metadata(ancestor) {
            Ok(metadata) => require_directory(ancestor, &metadata)?,
            Err(error) if create && error.kind() == io::ErrorKind::NotFound => {
                fs::create_dir(ancestor)?;
            }
            Err(error) => return Err(error),
        }
        handles.push(lock_directory(ancestor)?);
    }
    Ok(handles)
}

fn open_source_file(path: &Path) -> io::Result<Option<File>> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(None);
    };
    if !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy whitelist file is not a regular file",
        ));
    }
    let file = OpenOptions::new()
        .read(true)
        .share_mode(FILE_SHARE_READ.0)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT.0)
        .open(path)?;
    let opened = file.metadata()?;
    reject_reparse(path, &opened)?;
    if !opened.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "legacy file changed type",
        ));
    }
    Ok(Some(file))
}

fn copy_open_file(source: &mut File, destination: &Path) -> io::Result<()> {
    let mut target = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?;
    io::copy(source, &mut target)?;
    target.sync_all()
}

fn copy_directory(source: &Path, destination: &Path) -> io::Result<()> {
    let _source_guard = lock_directory(source)?;
    fs::create_dir(destination)?;
    let _destination_guard = lock_directory(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "legacy cookie disappeared"))?;
        let target = destination.join(entry.file_name());
        if metadata.is_dir() {
            copy_directory(&path, &target)?;
        } else if let Some(mut file) = open_source_file(&path)? {
            copy_open_file(&mut file, &target)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "legacy cookie disappeared",
            ));
        }
    }
    Ok(())
}

/// Only used for our reserved staging directories. Do not follow a link even
/// while cleaning a previous process's interrupted import.
fn remove_staging(path: &Path) -> io::Result<()> {
    let Some(metadata) = checked_metadata(path)? else {
        return Ok(());
    };
    require_directory(path, &metadata)?;
    let guard = lock_directory(path)?;
    for entry in fs::read_dir(path)? {
        let path = entry?.path();
        let metadata = checked_metadata(&path)?
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "staging entry disappeared"))?;
        if metadata.is_dir() {
            remove_staging(&path)?;
        } else if metadata.is_file() {
            fs::remove_file(path)?;
        } else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid staging entry",
            ));
        }
    }
    drop(guard);
    fs::remove_dir(path)
}

fn write_marker(directory: &Path, reason: &[u8]) -> io::Result<()> {
    let mut marker = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join(DONE_MARKER))?;
    marker.write_all(reason)?;
    marker.sync_all()
}

/// Rename the pinned staging directory itself. Reopening it by path after
/// dropping the no-delete lock would allow a junction substitution at publish.
fn publish_directory(staging: &File, destination: &Path) -> io::Result<()> {
    let name: Vec<u16> = destination.as_os_str().encode_wide().collect();
    let name_bytes = name.len() * std::mem::size_of::<u16>();
    let buffer_bytes = std::mem::size_of::<FILE_RENAME_INFO>() + name_bytes;
    let length = u32::try_from(buffer_bytes)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "migration path is too long"))?;
    // A word-backed allocation supplies FILE_RENAME_INFO's pointer alignment
    // and enough room for its trailing, variable-length UTF-16 file name.
    let mut buffer = vec![0usize; buffer_bytes.div_ceil(std::mem::size_of::<usize>())];
    let info = buffer.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    unsafe {
        info.write(FILE_RENAME_INFO::default());
        (*info).Anonymous.ReplaceIfExists = false;
        (*info).FileNameLength = name_bytes as u32;
        std::ptr::copy_nonoverlapping(
            name.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
            name.len(),
        );
        SetFileInformationByHandle(
            HANDLE(staging.as_raw_handle()),
            FileRenameInfo,
            info.cast(),
            length,
        )
    }
    .map_err(|error| {
        let code = error.code().0 as u32;
        if code & 0xffff_0000 == 0x8007_0000 {
            io::Error::from_raw_os_error((code & 0xffff) as i32)
        } else {
            io::Error::other(error)
        }
    })
}

/// Chromium's Windows ProcessSingleton uses a Chrome_MessageWindow whose title
/// is the userData path. canonicalize() adds a Win32 verbatim prefix; Electron's
/// title normally does not contain it. Preserve UTF-16 while removing the prefix.
fn window_title(path: &Path) -> Vec<u16> {
    let wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    let mut title = if wide.starts_with(&[92, 92, 63, 92, 85, 78, 67, 92]) {
        [vec![92, 92], wide[8..].to_vec()].concat()
    } else if wide.starts_with(&[92, 92, 63, 92]) {
        wide[4..].to_vec()
    } else {
        wide
    };
    for character in &mut title {
        if *character == 47 {
            *character = 92;
        }
    }
    title.push(0);
    title
}

fn legacy_running(legacy: &Path) -> io::Result<bool> {
    for path in [std::path::absolute(legacy)?, fs::canonicalize(legacy)?] {
        let title = window_title(&path);
        // Exact profile matching avoids deferring for unrelated Electron apps.
        // Older Chromium versions may use a hidden top-level window instead.
        for parent in [Some(HWND_MESSAGE), None] {
            if unsafe {
                FindWindowExW(
                    parent,
                    None,
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                )
            }
            .is_ok()
            {
                return Ok(true);
            }
        }
    }
    // Some legacy wrappers also hold a lock file. Probe it read-only; Electron
    // itself does not promise to create this sentinel, so it is only a fallback.
    match open_source_file(&legacy.join("lockfile")) {
        Ok(_) => Ok(false),
        Err(error) if matches!(error.raw_os_error(), Some(32 | 33)) => Ok(true),
        Err(error) => Err(error),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Checkpoint {
    StagingCreated,
    SessionCopied,
    BeforePublish,
    Published,
}

fn paths_overlap(first: &Path, second: &Path) -> bool {
    // Case folding is conservative on Windows: false positives safely refuse
    // an import instead of allowing an override to change the old data tree.
    fn key(path: &Path) -> PathBuf {
        let title = window_title(path);
        let path = std::ffi::OsString::from_wide(&title[..title.len() - 1]);
        PathBuf::from(path.to_string_lossy().to_lowercase())
    }
    let first = key(first);
    let second = key(second);
    first.starts_with(&second) || second.starts_with(&first)
}

/// Run before starting the backend. Any Err or DeferredLegacyRunning must keep
/// the backend stopped so a retry cannot mistake new backend files for user data.
pub fn migrate(data_dir: &Path) -> io::Result<MigrationOutcome> {
    migrate_from(data_dir, legacy_dir()?.as_deref())
}

fn migrate_from(data_dir: &Path, legacy: Option<&Path>) -> io::Result<MigrationOutcome> {
    migrate_with_checkpoint(data_dir, legacy, |_| Ok(()))
}

fn migrate_with_checkpoint(
    data_dir: &Path,
    legacy: Option<&Path>,
    mut checkpoint: impl FnMut(Checkpoint) -> io::Result<()>,
) -> io::Result<MigrationOutcome> {
    if data_dir.as_os_str().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "empty migration destination",
        ));
    }
    let data_dir = std::path::absolute(data_dir)?;
    let parent = data_dir.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "migration destination has no parent",
        )
    })?;
    let staging = parent.join(STAGING_DIR);
    let legacy = legacy.map(std::path::absolute).transpose()?;
    if paths_overlap(&data_dir, &staging)
        || legacy
            .as_ref()
            .is_some_and(|path| paths_overlap(path, &data_dir) || paths_overlap(path, &staging))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "legacy, new data, and staging paths must not overlap",
        ));
    }

    let data_metadata = checked_metadata(&data_dir)?;
    // Pin all existing destination ancestors before reading or writing markers.
    // Missing parents are created only after the source has been checked.
    let _existing_parent_guards = if checked_metadata(parent)?.is_some() {
        Some(lock_directory_chain(parent, false)?)
    } else {
        None
    };
    let mut data_guard = match data_metadata {
        Some(ref metadata) => {
            require_directory(&data_dir, metadata)?;
            Some(lock_directory(&data_dir)?)
        }
        None => None,
    };
    if let Some(marker) = checked_metadata(&data_dir.join(DONE_MARKER))? {
        if !marker.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "migration marker is not a regular file",
            ));
        }
        return Ok(MigrationOutcome::Skipped("done marker present"));
    }
    if data_metadata.is_some() {
        for entry in fs::read_dir(&data_dir)? {
            let entry = entry?;
            let metadata = checked_metadata(&entry.path())?.ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "new data changed while checking migration",
                )
            })?;
            if entry.file_name() == STAGING_DIR {
                require_directory(&entry.path(), &metadata)?;
            } else {
                // Preserve every new business file, including a config-only or
                // cookie-only install, unknown future files, and invalid sessions.
                write_marker(&data_dir, b"existing-data")?;
                return Ok(MigrationOutcome::Skipped("new business data exists"));
            }
        }
    }

    let legacy = match legacy {
        Some(path) => match checked_metadata(&path)? {
            Some(metadata) => {
                require_directory(&path, &metadata)?;
                Some(path)
            }
            None => None,
        },
        None => None,
    };
    let _source_guards = legacy
        .as_ref()
        .map(|path| lock_directory_chain(path, false))
        .transpose()?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }

    let _parent_guards = lock_directory_chain(parent, true)?;
    remove_staging(&staging)?;
    // Compatibility with the old P1 layout; only its reserved staging is removed.
    remove_staging(&data_dir.join(STAGING_DIR))?;
    fs::create_dir(&staging)?;
    let staging_guard = directory_handle(
        &staging,
        FILE_READ_ATTRIBUTES.0 | FILE_LIST_DIRECTORY.0 | DELETE.0,
    )?;
    checkpoint(Checkpoint::StagingCreated)?;

    let mut copied = 0;
    let mut invalid_session = false;
    if let Some(legacy) = &legacy {
        let session_path = legacy.join(FILE_WHITELIST[0]);
        if let Some(mut source) = open_source_file(&session_path)? {
            // The held read-only handle denies writes/deletion. The shared reader
            // applies the same bounded, strict schema as normal session loading.
            if session_store::read(&session_path)?.is_some() {
                let staged_session = staging.join(FILE_WHITELIST[0]);
                copy_open_file(&mut source, &staged_session)?;
                if session_store::read(&staged_session)?.is_none() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "session changed during migration",
                    ));
                }
                copied += 1;
                checkpoint(Checkpoint::SessionCopied)?;
            } else {
                invalid_session = true;
            }
        }
        for name in FILE_WHITELIST.iter().skip(1) {
            if let Some(mut source) = open_source_file(&legacy.join(name))? {
                copy_open_file(&mut source, &staging.join(name))?;
                copied += 1;
            }
        }
        for name in DIR_WHITELIST {
            let source = legacy.join(name);
            if let Some(metadata) = checked_metadata(&source)? {
                require_directory(&source, &metadata)?;
                copy_directory(&source, &staging.join(name))?;
                copied += 1;
            }
        }
    }

    let reason: &[u8] = if invalid_session {
        b"invalid-session"
    } else if copied == 0 {
        b"no-legacy"
    } else {
        b"ok"
    };
    write_marker(&staging, reason)?;
    checkpoint(Checkpoint::BeforePublish)?;
    if legacy
        .as_ref()
        .map(|path| legacy_running(path))
        .transpose()?
        .unwrap_or(false)
    {
        return Ok(MigrationOutcome::DeferredLegacyRunning);
    }
    // Removing an empty placeholder never removes business data. A writer that
    // wins this race causes an error; the directory rename never replaces a target.
    if checked_metadata(&data_dir)?.is_some() {
        if data_guard.is_none() {
            data_guard = Some(lock_directory(&data_dir)?);
        }
        if fs::read_dir(&data_dir)?.next().transpose()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "new business data appeared during migration",
            ));
        }
        drop(data_guard.take());
        fs::remove_dir(&data_dir)?;
    }
    publish_directory(&staging_guard, &data_dir)?;
    checkpoint(Checkpoint::Published)?;

    Ok(if invalid_session {
        MigrationOutcome::ImportedButSessionInvalid(INVALID_SESSION_NOTICE.to_owned())
    } else if copied == 0 {
        MigrationOutcome::NoLegacyData
    } else {
        MigrationOutcome::Imported(copied)
    })
}

#[cfg(test)]
mod p2_tests {
    use super::*;
    use std::collections::BTreeMap;
    use std::os::windows::fs::OpenOptionsExt;
    use std::os::windows::process::CommandExt;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

    struct Fixture {
        root: PathBuf,
        legacy: PathBuf,
        data: PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Self {
            let nonce = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let root = std::env::temp_dir().join(format!(
                "cx-migration-p2-{name}-{}-{nonce}-{}",
                std::process::id(),
                NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let legacy = root.join("legacy");
            std::fs::create_dir(&legacy).unwrap();
            Self {
                data: root.join("data"),
                legacy,
                root,
            }
        }

        fn write_legacy(&self, name: &str, contents: impl AsRef<[u8]>) {
            let path = self.legacy.join(name);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, contents).unwrap();
        }

        fn run(&self) -> std::io::Result<MigrationOutcome> {
            // Explicit paths allow the complete suite to run in parallel
            // without changing the test process's environment.
            migrate_from(&self.data, Some(&self.legacy))
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn valid_session() -> &'static str {
        r#"{"version":1,"login":{"username":"fixture-user","use_cookies":true},"activeTask":{"username":"fixture-user","taskId":"fixture-task"}}"#
    }

    fn source_bytes(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        fn visit(root: &Path, path: &Path, files: &mut BTreeMap<PathBuf, Vec<u8>>) {
            for entry in std::fs::read_dir(path).unwrap() {
                let entry = entry.unwrap();
                if entry.file_type().unwrap().is_dir() {
                    visit(root, &entry.path(), files);
                } else {
                    files.insert(
                        entry.path().strip_prefix(root).unwrap().to_path_buf(),
                        std::fs::read(entry.path()).unwrap(),
                    );
                }
            }
        }
        let mut files = BTreeMap::new();
        visit(root, root, &mut files);
        files
    }

    fn exclusive_file(path: &Path) -> std::fs::File {
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(path)
            .unwrap()
    }

    fn directory_link(target: &Path, link: &Path) {
        if std::os::windows::fs::symlink_dir(target, link).is_ok() {
            return;
        }
        // Junction creation works without the symlink privilege on Windows.
        // Paths are passed as process-local environment values, never shell code.
        let output = std::process::Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:CX_MIGRATION_LINK -Target $env:CX_MIGRATION_TARGET -ErrorAction Stop | Out-Null",
            ])
            .env("CX_MIGRATION_LINK", link)
            .env("CX_MIGRATION_TARGET", target)
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "junction fixture failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    struct SingletonWindow(windows::Win32::Foundation::HWND);

    impl SingletonWindow {
        fn new(user_data: &Path) -> Self {
            use std::os::windows::ffi::OsStrExt;
            use windows::core::{w, PCWSTR};
            use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
            use windows::Win32::UI::WindowsAndMessaging::{
                CreateWindowExW, DefWindowProcW, RegisterClassW, HWND_MESSAGE, WNDCLASSW,
                WS_OVERLAPPED,
            };

            unsafe extern "system" fn window_proc(
                hwnd: HWND,
                message: u32,
                wparam: WPARAM,
                lparam: LPARAM,
            ) -> LRESULT {
                unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
            }

            static CLASS: std::sync::OnceLock<u16> = std::sync::OnceLock::new();
            CLASS.get_or_init(|| {
                let class = WNDCLASSW {
                    lpszClassName: w!("Chrome_MessageWindow"),
                    lpfnWndProc: Some(window_proc),
                    ..Default::default()
                };
                let atom = unsafe { RegisterClassW(&class) };
                assert_ne!(atom, 0, "register singleton fixture class");
                atom
            });
            let title: Vec<u16> = user_data.as_os_str().encode_wide().chain([0]).collect();
            let hwnd = unsafe {
                CreateWindowExW(
                    Default::default(),
                    w!("Chrome_MessageWindow"),
                    PCWSTR(title.as_ptr()),
                    WS_OVERLAPPED,
                    0,
                    0,
                    0,
                    0,
                    Some(HWND_MESSAGE),
                    None,
                    None,
                    None,
                )
            }
            .unwrap();
            Self(hwnd)
        }
    }

    impl Drop for SingletonWindow {
        fn drop(&mut self) {
            unsafe {
                windows::Win32::UI::WindowsAndMessaging::DestroyWindow(self.0).unwrap();
            }
        }
    }

    #[test]
    fn electron_message_window_defers_import_until_legacy_exit() {
        let fixture = Fixture::new("electron-running");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        let window = SingletonWindow::new(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(source_bytes(&fixture.legacy), before);
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn unrelated_electron_message_window_does_not_defer_import() {
        let fixture = Fixture::new("unrelated-electron");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.root.join("another-user-data"));
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn imports_whitelist_once_and_retains_every_source_byte() {
        let fixture = Fixture::new("success");
        fixture.write_legacy("renderer-session.json", valid_session());
        for name in FILE_WHITELIST.iter().skip(1) {
            fixture.write_legacy(name, name);
        }
        fixture.write_legacy(".cookies/account.json", b"cookie-fixture");
        fixture.write_legacy(".cookies/nested/another.json", b"second-cookie");
        fixture.write_legacy("unlisted/private.txt", b"leave-in-legacy");
        let before = source_bytes(&fixture.legacy);

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(6)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
        for name in FILE_WHITELIST {
            assert_eq!(
                std::fs::read(fixture.data.join(name)).unwrap(),
                before[Path::new(name)]
            );
        }
        assert!(fixture.data.join(DONE_MARKER).is_file());
        assert!(!fixture.data.join("unlisted").exists());
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn config_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-config");
        fixture.write_legacy("web_config.json", b"old-config");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("web_config.json"), b"new-config").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn cookie_only_new_data_is_never_overwritten() {
        let fixture = Fixture::new("new-cookie");
        fixture.write_legacy("cookies.txt", b"old-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"new-cookie").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"new-cookie"
        );
    }

    #[test]
    fn cookie_directory_or_unknown_new_data_prevents_import() {
        for name in [
            ".cookies/account.json",
            "future-data.bin",
            "renderer-session.json",
        ] {
            let fixture = Fixture::new("new-business-data");
            fixture.write_legacy("cookies.txt", b"old-cookie");
            let new_path = fixture.data.join(name);
            std::fs::create_dir_all(new_path.parent().unwrap()).unwrap();
            std::fs::write(&new_path, b"preserve-even-if-invalid-session").unwrap();
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
            assert!(!fixture.data.join("cookies.txt").exists());
            assert_eq!(
                std::fs::read(new_path).unwrap(),
                b"preserve-even-if-invalid-session"
            );
        }
    }

    #[test]
    fn oversized_but_well_formed_session_is_skipped_with_notice() {
        let fixture = Fixture::new("oversize");
        let session = format!(
            "{}{}",
            " ".repeat(session_store::MAX_FILE_BYTES),
            valid_session()
        );
        fixture.write_legacy("renderer-session.json", &session);
        fixture.write_legacy("web_config.json", b"config");

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::ImportedButSessionInvalid(_)
        ));
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.data.join("web_config.json").is_file());
        assert_eq!(
            std::fs::read(fixture.legacy.join("renderer-session.json")).unwrap(),
            session.as_bytes()
        );
    }

    #[test]
    fn corrupt_session_skips_only_session_and_keeps_originals() {
        for session in [
            "{ broken",
            r#"{"version":2,"login":null,"activeTask":null}"#,
            r#"{"version":1,"login":null,"activeTask":null,"password":"fixture"}"#,
        ] {
            let fixture = Fixture::new("invalid-session");
            fixture.write_legacy("renderer-session.json", session);
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::ImportedButSessionInvalid(_)
            ));
            assert!(!fixture.data.join("renderer-session.json").exists());
            assert!(fixture.data.join("cookies.txt").is_file());
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn session_io_error_aborts_without_marker_and_retries_after_unlock() {
        let fixture = Fixture::new("session-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("renderer-session.json"));

        let result = fixture.run();
        assert!(result.is_err(), "session IO must abort, got {result:?}");
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("cookies.txt").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn copy_error_never_publishes_a_partial_session_and_can_retry() {
        let fixture = Fixture::new("copy-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("web_config.json", b"config");
        let before = source_bytes(&fixture.legacy);
        let busy = exclusive_file(&fixture.legacy.join("web_config.json"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
        assert_eq!(source_bytes(&fixture.legacy), before);
    }

    #[test]
    fn interrupted_sibling_staging_is_rebuilt_before_publish() {
        for with_marker in [false, true] {
            let fixture = Fixture::new("interrupted");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"complete-cookie");
            let staging = fixture.root.join(STAGING_DIR);
            std::fs::create_dir(&staging).unwrap();
            std::fs::write(staging.join("renderer-session.json"), valid_session()).unwrap();
            if with_marker {
                std::fs::write(staging.join(DONE_MARKER), b"ok").unwrap();
            }

            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Imported(2)
            ));
            assert!(
                !staging.exists(),
                "interrupted staging must be consumed or rebuilt"
            );
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(
                std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"complete-cookie"
            );
        }
    }

    #[test]
    fn published_marker_makes_exit_after_publish_retry_safe() {
        let fixture = Fixture::new("post-publish");
        fixture.write_legacy("cookies.txt", b"legacy-cookie");
        std::fs::create_dir(&fixture.data).unwrap();
        std::fs::write(fixture.data.join("cookies.txt"), b"published-cookie").unwrap();
        std::fs::write(fixture.data.join(DONE_MARKER), b"ok").unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            std::fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"published-cookie"
        );
    }

    #[test]
    fn busy_legacy_is_deferred_until_legacy_exits() {
        let fixture = Fixture::new("busy");
        fixture.write_legacy("lockfile", b"sentinel");
        fixture.write_legacy("cookies.txt", b"cookies");
        let busy = exclusive_file(&fixture.legacy.join("lockfile"));

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_root_junction_is_refused_without_modifying_target() {
        let fixture = Fixture::new("source-junction");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("cookies.txt"), b"outside-cookie").unwrap();
        std::fs::remove_dir(&fixture.legacy).unwrap();
        directory_link(&outside, &fixture.legacy);

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            std::fs::read(outside.join("cookies.txt")).unwrap(),
            b"outside-cookie"
        );
    }

    #[test]
    fn destination_junction_is_refused_without_writing_through_it() {
        let fixture = Fixture::new("target-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        directory_link(&outside, &fixture.data);

        assert!(fixture.run().is_err());
        assert_eq!(std::fs::read_dir(&outside).unwrap().count(), 0);
    }

    #[test]
    fn staging_junction_is_refused_without_touching_its_target() {
        let fixture = Fixture::new("staging-junction");
        fixture.write_legacy("cookies.txt", b"cookies");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("keep.txt"), b"outside").unwrap();
        directory_link(&outside, &fixture.root.join(STAGING_DIR));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(std::fs::read(outside.join("keep.txt")).unwrap(), b"outside");
    }

    #[test]
    fn recursive_cookie_junction_is_refused_without_copying_outside_data() {
        let fixture = Fixture::new("nested-junction");
        fixture.write_legacy(".cookies/real.json", b"cookie");
        let outside = fixture.root.join("outside");
        std::fs::create_dir(&outside).unwrap();
        std::fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies/nested"));

        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies/nested/private.json").exists());
        assert_eq!(
            std::fs::read(outside.join("private.json")).unwrap(),
            b"outside"
        );
    }

    #[test]
    fn missing_or_empty_legacy_publishes_only_completion_marker() {
        for missing in [false, true] {
            let fixture = Fixture::new("no-legacy");
            if missing {
                fs::remove_dir(&fixture.legacy).unwrap();
            }
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::NoLegacyData
            ));
            assert!(fixture.data.join(DONE_MARKER).is_file());
            assert_eq!(fs::read_dir(&fixture.data).unwrap().count(), 1);
            assert!(matches!(
                fixture.run().unwrap(),
                MigrationOutcome::Skipped(_)
            ));
        }
    }

    #[test]
    fn old_p1_staging_is_cleaned_without_publishing_its_partial_data() {
        let fixture = Fixture::new("p1-staging");
        fixture.write_legacy("cookies.txt", b"complete-cookie");
        fs::create_dir_all(fixture.data.join(STAGING_DIR)).unwrap();
        fs::write(
            fixture.data.join(STAGING_DIR).join("renderer-session.json"),
            valid_session(),
        )
        .unwrap();

        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
        assert!(!fixture.data.join(STAGING_DIR).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"complete-cookie"
        );
    }

    #[test]
    fn cancellation_at_each_checkpoint_can_be_retried_without_partial_publish() {
        for interrupted in [
            Checkpoint::StagingCreated,
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("cancel-checkpoint");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("web_config.json", b"config");
            fixture.write_legacy(".cookies/account.json", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
                if point == interrupted {
                    return Err(io::Error::new(io::ErrorKind::Interrupted, "cancel fixture"));
                }
                Ok(())
            });
            assert_eq!(result.unwrap_err().kind(), io::ErrorKind::Interrupted);
            assert_eq!(source_bytes(&fixture.legacy), before);
            if interrupted == Checkpoint::Published {
                assert!(fixture.data.join(DONE_MARKER).is_file());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Skipped(_)
                ));
            } else {
                assert!(!fixture.data.join(DONE_MARKER).exists());
                assert!(!fixture.data.join("renderer-session.json").exists());
                assert!(matches!(
                    fixture.run().unwrap(),
                    MigrationOutcome::Imported(3)
                ));
            }
            assert!(fixture.data.join(".cookies/account.json").is_file());
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe() {
        const CHILD_ROOT: &str = "CX_MIGRATION_EXIT_FIXTURE_ROOT";
        const CHILD_POINT: &str = "CX_MIGRATION_EXIT_FIXTURE_POINT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            let expected = std::env::var(CHILD_POINT).unwrap();
            migrate_with_checkpoint(&root.join("data"), Some(&root.join("legacy")), |point| {
                if format!("{point:?}") == expected {
                    // Exit without unwinding: the OS releases handles, but no
                    // cleanup or deferred marker write can run in this process.
                    std::process::exit(73);
                }
                Ok(())
            })
            .unwrap();
            panic!("exit checkpoint was not reached");
        }
        for point in [
            Checkpoint::SessionCopied,
            Checkpoint::BeforePublish,
            Checkpoint::Published,
        ] {
            let fixture = Fixture::new("process-exit");
            fixture.write_legacy("renderer-session.json", valid_session());
            fixture.write_legacy("cookies.txt", b"cookies");
            let before = source_bytes(&fixture.legacy);
            let output = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "migration::p2_tests::actual_process_exit_at_copy_and_publish_boundaries_is_retry_safe", "--nocapture"])
                .env(CHILD_ROOT, &fixture.root)
                .env(CHILD_POINT, format!("{point:?}"))
                .creation_flags(0x08000000)
                .output().unwrap();
            assert_eq!(
                output.status.code(),
                Some(73),
                "exit child failed: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
            assert_eq!(
                fixture.data.join(DONE_MARKER).is_file(),
                point == Checkpoint::Published
            );
            let result = fixture.run().unwrap();
            if point == Checkpoint::Published {
                assert!(matches!(result, MigrationOutcome::Skipped(_)));
            } else {
                assert!(matches!(result, MigrationOutcome::Imported(2)));
            }
            assert_eq!(
                fs::read(fixture.data.join("cookies.txt")).unwrap(),
                b"cookies"
            );
            assert_eq!(source_bytes(&fixture.legacy), before);
        }
    }

    #[test]
    fn public_entry_point_honors_legacy_override_in_an_isolated_process() {
        const CHILD_ROOT: &str = "CX_MIGRATION_ENTRY_FIXTURE_ROOT";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let root = PathBuf::from(root);
            assert!(root.starts_with(std::env::temp_dir()));
            assert!(matches!(
                migrate(&root.join("data")).unwrap(),
                MigrationOutcome::Imported(1)
            ));
            return;
        }
        let fixture = Fixture::new("env-override");
        fixture.write_legacy("cookies.txt", b"synthetic-cookie");
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "migration::p2_tests::public_entry_point_honors_legacy_override_in_an_isolated_process", "--nocapture"])
            .env(CHILD_ROOT, &fixture.root)
            .env(LEGACY_DIR_ENV, &fixture.legacy)
            .creation_flags(0x08000000)
            .output().unwrap();
        assert!(
            output.status.success(),
            "entry-point child failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            fs::read(fixture.data.join("cookies.txt")).unwrap(),
            b"synthetic-cookie"
        );
    }

    #[test]
    fn publish_io_error_preserves_staging_and_retries_after_unlock() {
        let fixture = Fixture::new("publish-io");
        fixture.write_legacy("renderer-session.json", valid_session());
        fixture.write_legacy("cookies.txt", b"cookies");
        fs::create_dir(&fixture.data).unwrap();
        let busy = lock_directory(&fixture.data).unwrap();
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join("renderer-session.json").exists());
        assert!(fixture.root.join(STAGING_DIR).join(DONE_MARKER).is_file());
        drop(busy);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(2)
        ));
    }

    #[test]
    fn new_business_data_appearing_before_publish_is_preserved() {
        let fixture = Fixture::new("concurrent-data");
        fixture.write_legacy("web_config.json", b"old-config");
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                fs::create_dir(&fixture.data)?;
                fs::write(fixture.data.join("web_config.json"), b"new-config")?;
            }
            Ok(())
        });
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::AlreadyExists);
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Skipped(_)
        ));
        assert_eq!(
            fs::read(fixture.data.join("web_config.json")).unwrap(),
            b"new-config"
        );
    }

    #[test]
    fn legacy_starting_before_publish_defers_without_marker() {
        let fixture = Fixture::new("legacy-start-race");
        fixture.write_legacy("cookies.txt", b"cookies");
        let mut window = None;
        let result = migrate_with_checkpoint(&fixture.data, Some(&fixture.legacy), |point| {
            if point == Checkpoint::BeforePublish {
                window = Some(SingletonWindow::new(&fixture.legacy));
            }
            Ok(())
        })
        .unwrap();
        assert!(matches!(result, MigrationOutcome::DeferredLegacyRunning));
        assert!(!fixture.data.join(DONE_MARKER).exists());
        drop(window);
        assert!(matches!(
            fixture.run().unwrap(),
            MigrationOutcome::Imported(1)
        ));
    }

    #[test]
    fn source_and_destination_overlap_is_refused_without_writing_old_data() {
        let fixture = Fixture::new("overlap");
        fixture.write_legacy("cookies.txt", b"cookies");
        let before = source_bytes(&fixture.legacy);
        assert!(migrate_from(&fixture.legacy, Some(&fixture.legacy)).is_err());
        assert!(migrate_from(&fixture.legacy.join("data"), Some(&fixture.legacy)).is_err());
        assert!(migrate_from(
            &fixture.legacy.join("data"),
            Some(&fs::canonicalize(&fixture.legacy).unwrap())
        )
        .is_err());
        assert_eq!(source_bytes(&fixture.legacy), before);
        assert!(!fixture.legacy.join(DONE_MARKER).exists());
        assert!(!fixture.legacy.join("data").exists());
    }

    #[test]
    fn top_level_whitelisted_path_reparse_is_refused() {
        let fixture = Fixture::new("file-symlink");
        let outside = fixture.root.join("outside.txt");
        fs::write(&outside, b"outside").unwrap();
        if let Err(error) =
            std::os::windows::fs::symlink_file(&outside, fixture.legacy.join("cookies.txt"))
        {
            assert_eq!(
                error.raw_os_error(),
                Some(1314),
                "unexpected symlink fixture error: {error}"
            );
            let directory = fixture.root.join("outside-dir");
            fs::create_dir(&directory).unwrap();
            directory_link(&directory, &fixture.legacy.join("cookies.txt"));
            eprintln!("file symlink privilege unavailable; exercised a junction at the whitelisted file path instead");
        }
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert_eq!(fs::read(outside).unwrap(), b"outside");
    }

    #[test]
    fn top_level_cookie_directory_link_is_refused() {
        let fixture = Fixture::new("cookie-root-link");
        let outside = fixture.root.join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("private.json"), b"outside").unwrap();
        directory_link(&outside, &fixture.legacy.join(".cookies"));
        assert!(fixture.run().is_err());
        assert!(!fixture.data.join(DONE_MARKER).exists());
        assert!(!fixture.data.join(".cookies").exists());
        assert_eq!(fs::read(outside.join("private.json")).unwrap(), b"outside");
    }

    #[test]
    fn junction_inside_staging_or_destination_parent_is_refused() {
        for in_staging in [true, false] {
            let mut fixture = Fixture::new("staging-inner-link");
            fixture.write_legacy("cookies.txt", b"cookies");
            let outside = fixture.root.join("outside");
            fs::create_dir(&outside).unwrap();
            fs::write(outside.join("keep.txt"), b"keep").unwrap();
            if in_staging {
                let staging = fixture.root.join(STAGING_DIR);
                fs::create_dir(&staging).unwrap();
                directory_link(&outside, &staging.join("nested"));
            } else {
                let parent = fixture.root.join("linked-parent");
                directory_link(&outside, &parent);
                fixture.data = parent.join("data");
            }
            assert!(fixture.run().is_err());
            assert_eq!(fs::read(outside.join("keep.txt")).unwrap(), b"keep");
            assert_eq!(fs::read_dir(outside).unwrap().count(), 1);
        }
    }

    #[test]
    fn verbatim_legacy_path_still_matches_electron_user_data_title() {
        let fixture = Fixture::new("verbatim-title");
        fixture.write_legacy("cookies.txt", b"cookies");
        let _window = SingletonWindow::new(&fixture.legacy);
        assert!(matches!(
            migrate_from(
                &fixture.data,
                Some(&fs::canonicalize(&fixture.legacy).unwrap())
            )
            .unwrap(),
            MigrationOutcome::DeferredLegacyRunning
        ));
        assert!(!fixture.data.join(DONE_MARKER).exists());
    }
}
