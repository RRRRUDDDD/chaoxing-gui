//! Exercise the real backend lifecycle inside a second, enclosing Windows Job.
//! Only a disposable helper is assigned to that Job; never the test runner.
#![cfg(windows)]

use chaoxing_desktop_lib::backend::{
    start_backend, stop_backend, BackendLaunch, BackendPhase, BackendState,
};
use serde::{Deserialize, Serialize};
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::windows::ffi::OsStringExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};
use windows::core::{BOOL, PCWSTR, PWSTR};
use windows::Win32::Foundation::{HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob, JobObjectBasicProcessIdList,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, GetExitCodeProcess, OpenProcess, QueryFullProcessImageNameW,
    TerminateProcess, WaitForSingleObject, PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
};

const FAKE_BACKEND: &str = env!("CARGO_BIN_EXE_fake-backend");
const HELPER_ROOT: &str = "CHAOXING_NESTED_JOB_HELPER_ROOT";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const START_TIMEOUT: Duration = Duration::from_secs(20);
const EXIT_TIMEOUT: Duration = Duration::from_secs(8);

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ReadyRecord {
    helper_pid: u32,
    backend_pid: u32,
    grandchild_pid: u32,
    phase: String,
}

struct FixtureDirectory(PathBuf);

impl FixtureDirectory {
    fn create() -> Self {
        let mut nonce = [0u8; 8];
        getrandom::getrandom(&mut nonce).expect("fixture nonce");
        let path = std::env::temp_dir().join(format!(
            "cx-nested-job-{}-{:016x}",
            std::process::id(),
            u64::from_le_bytes(nonce)
        ));
        // Do not remove or reuse an existing directory owned by another run.
        fs::create_dir(&path).expect("create owned fixture directory");
        Self(path)
    }
}

impl Drop for FixtureDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct OuterJob(OwnedHandle);

impl OuterJob {
    fn create() -> Self {
        let handle = unsafe { CreateJobObjectW(None, PCWSTR::null()) }.expect("create outer Job");
        // Own immediately: failed configuration must close the kernel handle.
        let job = Self(unsafe { OwnedHandle::from_raw_handle(handle.0) });
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of_val(&info) as u32,
            )
        }
        .expect("configure outer Job");
        job
    }

    fn raw(&self) -> HANDLE {
        HANDLE(self.0.as_raw_handle())
    }

    fn contains(&self, process: HANDLE) -> bool {
        let mut member = BOOL::default();
        unsafe { IsProcessInJob(process, Some(self.raw()), &mut member) }
            .expect("query exact outer Job membership");
        member.as_bool()
    }

    fn pids(&self) -> Vec<u32> {
        // Layout of JOBOBJECT_BASIC_PROCESS_ID_LIST, including the hidden
        // console hosts Windows can attach to the three fixture processes.
        // Excess members fail the query instead of being omitted.
        #[repr(C)]
        #[derive(Default)]
        struct ProcessIds {
            assigned: u32,
            count: u32,
            ids: [usize; 16],
        }
        let mut ids = ProcessIds::default();
        unsafe {
            QueryInformationJobObject(
                Some(self.raw()),
                JobObjectBasicProcessIdList,
                &mut ids as *mut _ as *mut _,
                std::mem::size_of_val(&ids) as u32,
                None,
            )
        }
        .expect("enumerate only the disposable outer Job");
        assert_eq!(ids.assigned, ids.count, "incomplete Job process snapshot");
        let mut pids: Vec<_> = ids.ids[..ids.count as usize]
            .iter()
            .map(|pid| u32::try_from(*pid).expect("Windows PID"))
            .collect();
        pids.sort_unstable();
        pids
    }

    fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.raw(), 1);
        }
    }
}

struct CapturedProcess {
    pid: u32,
    handle: OwnedHandle,
}

impl CapturedProcess {
    fn open(pid: u32) -> Self {
        assert_ne!(pid, std::process::id(), "never capture the test runner");
        let handle = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE | PROCESS_TERMINATE,
                false,
                pid,
            )
        }
        .expect("open the exact live fixture PID");
        Self {
            pid,
            handle: unsafe { OwnedHandle::from_raw_handle(handle.0) },
        }
    }

    fn raw(&self) -> HANDLE {
        HANDLE(self.handle.as_raw_handle())
    }

    fn assert_running(&self) {
        assert_eq!(
            unsafe { WaitForSingleObject(self.raw(), 0) },
            WAIT_TIMEOUT,
            "fixture PID {} exited before the test action",
            self.pid
        );
    }

    fn assert_exited(&self) {
        assert_eq!(
            unsafe { WaitForSingleObject(self.raw(), EXIT_TIMEOUT.as_millis() as u32) },
            WAIT_OBJECT_0,
            "captured fixture PID {} survived while the outer Job stayed open",
            self.pid
        );
    }

    fn exit_code(&self) -> u32 {
        let mut code = 0;
        unsafe { GetExitCodeProcess(self.raw(), &mut code) }.expect("fixture exit code");
        code
    }

    fn image_path(&self) -> PathBuf {
        let mut path = vec![0u16; 32768];
        let mut length = path.len() as u32;
        unsafe {
            QueryFullProcessImageNameW(
                self.raw(),
                PROCESS_NAME_WIN32,
                PWSTR(path.as_mut_ptr()),
                &mut length,
            )
        }
        .expect("fixture executable path");
        fs::canonicalize(PathBuf::from(OsString::from_wide(&path[..length as usize])))
            .expect("canonical fixture executable path")
    }
}

struct NestedHost {
    outer: OuterJob,
    helper: Child,
    descendants: Vec<CapturedProcess>,
    // Last field: remove only our directory after process/file handles close.
    directory: FixtureDirectory,
}

impl NestedHost {
    fn start() -> Self {
        let directory = FixtureDirectory::create();
        let outer = OuterJob::create();
        let helper = Command::new(std::env::current_exe().expect("test executable"))
            .args([
                "--exact",
                "nested_job_helper",
                "--ignored",
                "--nocapture",
                "--test-threads=1",
            ])
            .env(HELPER_ROOT, &directory.0)
            .env("FAKE_MODE", "spawn-grandchild")
            .env_remove("FAKE_DELAY_READY_MS")
            .env_remove("FAKE_DELAY_HEALTH_MS")
            .stdin(Stdio::piped())
            .stdout(Stdio::from(
                File::create(directory.0.join("helper.stdout")).expect("helper stdout file"),
            ))
            .stderr(Stdio::from(
                File::create(directory.0.join("helper.stderr")).expect("helper stderr file"),
            ))
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn disposable helper");
        // Install cleanup before assignment, pipe writes, or any assertions.
        let mut host = Self {
            outer,
            helper,
            descendants: Vec::new(),
            directory,
        };
        assert_ne!(host.helper.id(), std::process::id());
        unsafe { AssignProcessToJobObject(host.outer.raw(), HANDLE(host.helper.as_raw_handle())) }
            .expect("assign only the gated helper to the outer Job");
        assert!(!host.outer.contains(unsafe { GetCurrentProcess() }));
        assert!(host.outer.contains(HANDLE(host.helper.as_raw_handle())));
        // The helper cannot launch anything until outer Job assignment succeeds.
        host.helper
            .stdin
            .as_mut()
            .expect("owned helper stdin pipe")
            .write_all(b"G")
            .expect("release helper startup gate");
        host.await_ready();
        host
    }

    fn diagnostics(&self) -> String {
        ["helper.stdout", "helper.stderr"]
            .into_iter()
            .map(|name| fs::read_to_string(self.directory.0.join(name)).unwrap_or_default())
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn await_ready(&mut self) {
        let ready_path = self.directory.0.join("ready.json");
        let deadline = Instant::now() + START_TIMEOUT;
        while !ready_path.is_file() {
            assert!(
                self.helper.try_wait().expect("poll helper").is_none(),
                "helper failed before Ready: {}",
                self.diagnostics()
            );
            assert!(
                Instant::now() < deadline,
                "nested backend Ready timed out: {}",
                self.diagnostics()
            );
            std::thread::sleep(Duration::from_millis(20));
        }
        let ready: ReadyRecord =
            serde_json::from_slice(&fs::read(ready_path).expect("Ready record"))
                .expect("Ready JSON");
        assert_eq!(ready.helper_pid, self.helper.id());
        assert_eq!(ready.phase, "ready");
        assert_ne!(ready.backend_pid, ready.grandchild_pid);
        assert_ne!(ready.backend_pid, ready.helper_pid);
        assert_ne!(ready.grandchild_pid, ready.helper_pid);
        for pid in [ready.backend_pid, ready.grandchild_pid] {
            self.descendants.push(CapturedProcess::open(pid));
        }
        let expected_image = fs::canonicalize(FAKE_BACKEND).expect("Cargo fake-backend path");
        for process in &self.descendants {
            process.assert_running();
            assert_eq!(process.image_path(), expected_image);
            assert!(self.outer.contains(process.raw()));
        }
        let mut expected_pids = vec![ready.helper_pid, ready.backend_pid, ready.grandchild_pid];
        expected_pids.sort_unstable();
        let observed_pids = self.outer.pids();
        for pid in &expected_pids {
            assert!(
                observed_pids.contains(pid),
                "required fixture PID {pid} is outside the Job"
            );
        }

        // CREATE_NO_WINDOW hides console windows but Windows may still create
        // a conhost.exe for each console executable. Capture those exact Job
        // members too, and verify their full system path instead of accepting
        // an arbitrary process merely because its executable has that name.
        let console_host = fs::canonicalize(
            PathBuf::from(std::env::var_os("SystemRoot").expect("Windows system root"))
                .join("System32")
                .join("conhost.exe"),
        )
        .expect("Windows console host path");
        for pid in observed_pids
            .iter()
            .filter(|pid| !expected_pids.contains(pid))
        {
            self.descendants.push(CapturedProcess::open(*pid));
            let process = self.descendants.last().unwrap();
            let image_path = process.image_path();
            eprintln!("captured auxiliary Job PID {pid}: {}", image_path.display());
            assert_eq!(
                image_path, console_host,
                "unexpected auxiliary Job member {pid}"
            );
            process.assert_running();
            assert!(self.outer.contains(process.raw()));
        }
        expected_pids.extend(self.descendants.iter().skip(2).map(|process| process.pid));
        expected_pids.sort_unstable();
        assert_eq!(
            self.outer.pids(),
            expected_pids,
            "Job members changed during capture"
        );
    }

    fn wait_for_helper(&mut self) -> ExitStatus {
        wait_for_child(&mut self.helper, EXIT_TIMEOUT)
            .unwrap_or_else(|| panic!("helper exit timed out: {}", self.diagnostics()))
    }

    fn assert_no_descendants(&self) {
        // Keep the outer Job handle open through every assertion. Its cleanup
        // must not conceal a broken inner Job or leaked grandchild.
        for process in &self.descendants {
            process.assert_exited();
        }
        let deadline = Instant::now() + EXIT_TIMEOUT;
        while !self.outer.pids().is_empty() {
            assert!(
                Instant::now() < deadline,
                "outer Job retains a fixture process"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for NestedHost {
    fn drop(&mut self) {
        self.helper.stdin.take();
        self.outer.terminate();
        let _ = self.helper.kill();
        // Captured handles prevent PID reuse from targeting unrelated processes.
        // Also cover a descendant that escaped a broken inner Job assignment.
        for process in &self.descendants {
            unsafe {
                let _ = TerminateProcess(process.raw(), 1);
            }
        }
        let _ = wait_for_child(&mut self.helper, EXIT_TIMEOUT);
        for process in &self.descendants {
            unsafe {
                let _ = WaitForSingleObject(process.raw(), EXIT_TIMEOUT.as_millis() as u32);
            }
        }
    }
}

fn wait_for_child(child: &mut Child, timeout: Duration) -> Option<ExitStatus> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status),
            Ok(None) if Instant::now() < deadline => {
                std::thread::sleep(Duration::from_millis(20));
            }
            _ => return None,
        }
    }
}

#[test]
fn nested_job_ready_then_stdin_eof_reaps_exact_backend_and_grandchild() {
    let mut host = NestedHost::start();
    host.helper.stdin.take();
    assert!(host.wait_for_helper().success(), "{}", host.diagnostics());
    host.assert_no_descendants();
    assert_eq!(
        host.descendants[0].exit_code(),
        0,
        "backend must exit via EOF"
    );
    assert!(host.directory.0.join("stopped").is_file());
}

#[test]
fn nested_job_forced_helper_death_reaps_exact_backend_and_grandchild() {
    let mut host = NestedHost::start();
    host.helper
        .kill()
        .expect("force-kill only the captured helper");
    assert!(!host.wait_for_helper().success());
    host.assert_no_descendants();
    assert!(!host.directory.0.join("stopped").exists());
}

struct BackendOwner(Arc<BackendState>);

impl Drop for BackendOwner {
    fn drop(&mut self) {
        stop_backend(&self.0);
    }
}

#[test]
#[ignore = "disposable subprocess entry point; started by the two nested Job tests"]
fn nested_job_helper() {
    let root = PathBuf::from(std::env::var_os(HELPER_ROOT).expect("helper-only fixture root"));
    let mut input = std::io::stdin().lock();
    let mut gate = [0u8; 1];
    input.read_exact(&mut gate).expect("parent startup gate");
    assert_eq!(gate, *b"G");
    let mut in_job = BOOL::default();
    unsafe { IsProcessInJob(GetCurrentProcess(), None, &mut in_job) }.expect("helper Job query");
    assert!(
        in_job.as_bool(),
        "the helper must already be inside the outer Job"
    );

    let backend = BackendOwner(Arc::new(BackendState::new(
        root.join("data"),
        root.join("logs"),
    )));
    start_backend(
        &backend.0,
        BackendLaunch::Frozen(PathBuf::from(FAKE_BACKEND)),
    )
    .expect("start real BackendState inside outer Job");
    assert_eq!(backend.0.status().phase, BackendPhase::Ready);
    assert!(
        backend.0.job.lock().unwrap().is_some(),
        "inner Job must remain owned"
    );
    let backend_pid = {
        let guard = backend.0.child.lock().unwrap();
        let child = guard.as_ref().expect("registered backend child");
        assert!(
            child.stdin.is_some(),
            "backend requires a real, held stdin pipe"
        );
        child.id()
    };
    let read_pid = |name: &str| -> u32 {
        fs::read_to_string(backend.0.data_dir.join(name))
            .expect("fixture PID marker")
            .trim()
            .parse()
            .expect("fixture PID")
    };
    assert_eq!(read_pid("fake-backend.pid"), backend_pid);
    let ready = ReadyRecord {
        helper_pid: std::process::id(),
        backend_pid,
        grandchild_pid: read_pid("fake-grandchild.pid"),
        phase: "ready".into(),
    };
    fs::write(root.join("ready.tmp"), serde_json::to_vec(&ready).unwrap()).expect("write Ready");
    fs::rename(root.join("ready.tmp"), root.join("ready.json")).expect("publish complete Ready");

    // Parent closes its genuine stdin writer for normal exit, or terminates
    // this helper while it is blocked here to exercise KILL_ON_JOB_CLOSE.
    let mut end = [0u8; 1];
    assert_eq!(input.read(&mut end).expect("parent EOF"), 0);
    stop_backend(&backend.0);
    assert_eq!(backend.0.status().phase, BackendPhase::Stopped);
    fs::write(root.join("stopped"), b"stopped").expect("normal-stop marker");
}
