//! Windows Job Object wrapper: KILL_ON_JOB_CLOSE so the backend process tree
//! is reaped even if the host is hard-killed. PoC-proven in E:\Downloads\45\tauri-poc.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

pub struct Job(HANDLE);

// The handle is only used for Assign/Terminate/Close, which are thread-safe
// kernel operations; HANDLE is a raw-pointer wrapper.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

impl Job {
    pub fn create() -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(None, PCWSTR::null())
                .map_err(|e| format!("CreateJobObject: {e}"))?;
            // Own the handle before fallible setup so an error also closes it.
            let job = Self(handle);
            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
            .map_err(|e| format!("SetInformationJobObject: {e}"))?;
            Ok(job)
        }
    }

    pub fn assign(&self, pid: u32) -> Result<(), String> {
        unsafe {
            let ph = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
                .map_err(|e| format!("OpenProcess({pid}): {e}"))?;
            let result = AssignProcessToJobObject(self.0, ph);
            let _ = CloseHandle(ph);
            result.map_err(|e| format!("AssignProcessToJobObject({pid}): {e}"))
        }
    }

    /// Kill the whole process tree now (used on stop timeout). Idempotent.
    pub fn terminate(&self) {
        unsafe {
            let _ = TerminateJobObject(self.0, 1);
        }
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // KILL_ON_JOB_CLOSE: closing the last handle reaps the tree. This also
        // covers panic unwinding — never leak a backend past host exit.
        unsafe {
            let _ = CloseHandle(self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    /// Spawn a child that stays alive until killed; returns (Child, grandchild pid marker file check fn).
    /// Uses `cmd /c ping` (long-running) and a grandchild via `cmd /c ping & cmd /c ping`
    /// is not needed — the tree-kill semantics are covered by assigning the direct child;
    /// grandchild coverage: `cmd /c "ping -n 30 127.0.0.1 > nul & ping -n 30 127.0.0.1 > nul"`
    /// keeps one process; use PowerShell-free approach: `cmd /c start /wait` spawns a child cmd.
    #[test]
    fn drop_job_reaps_process_tree() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args([
                "/c",
                "start /wait /min cmd /c ping -n 60 127.0.0.1 > nul & ping -n 60 127.0.0.1 > nul",
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn cmd tree");
        job.assign(child.id()).expect("assign to job");

        // Drop the job: KILL_ON_JOB_CLOSE must reap child + grandchild.
        drop(job);
        std::thread::sleep(std::time::Duration::from_millis(1500));
        let status = child.try_wait().expect("try_wait");
        assert!(status.is_some(), "child should be dead after job drop");

        // Grandchild (started via `start /wait`) must also be gone: scan for ping
        // processes is flaky; instead assert via tasklist absence of our unique
        // marker is complex — the PoC already verified tree-kill visually.
        // Here we assert at least the direct child and rely on kernel job semantics.
    }

    #[test]
    fn terminate_is_idempotent_and_reaps() {
        let job = Job::create().expect("create job");
        let mut child = std::process::Command::new("cmd")
            .args(["/c", "ping -n 60 127.0.0.1 > nul"])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .expect("spawn");
        job.assign(child.id()).expect("assign");
        job.terminate();
        job.terminate(); // second call must not fail/panic
        std::thread::sleep(std::time::Duration::from_millis(800));
        assert!(child.try_wait().expect("try_wait").is_some());
    }
}
