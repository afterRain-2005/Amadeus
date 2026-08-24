use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, ExitStatus, Stdio},
    sync::Mutex,
};

use serde::Serialize;
use thiserror::Error;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

mod platform {
    #[cfg(windows)]
    mod windows {
        use std::{
            ffi::c_void,
            io,
            mem::{size_of, zeroed},
            os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
            process::Child,
            ptr::null,
        };

        use windows_sys::Win32::{
            Foundation::{CloseHandle, HANDLE},
            System::JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
                SetInformationJobObject,
            },
        };

        /// A Windows Job Object configured to terminate every assigned process
        /// when the final handle is closed.
        pub struct KillOnCloseJob {
            handle: OwnedHandle,
        }

        impl KillOnCloseJob {
            pub fn new() -> io::Result<Self> {
                let raw = unsafe { CreateJobObjectW(null(), null()) };
                if raw.is_null() {
                    return Err(io::Error::last_os_error());
                }

                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let configured = unsafe {
                    SetInformationJobObject(
                        raw,
                        JobObjectExtendedLimitInformation,
                        (&raw const info).cast::<c_void>(),
                        size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                    )
                };
                if configured == 0 {
                    unsafe { CloseHandle(raw) };
                    return Err(io::Error::last_os_error());
                }

                let handle = unsafe { OwnedHandle::from_raw_handle(raw.cast()) };
                Ok(Self { handle })
            }

            pub fn assign(&self, child: &Child) -> io::Result<()> {
                let job = self.handle.as_raw_handle().cast::<c_void>() as HANDLE;
                let process = child.as_raw_handle().cast::<c_void>() as HANDLE;
                let assigned = unsafe { AssignProcessToJobObject(job, process) };
                if assigned == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            }
        }
    }

    #[cfg(not(windows))]
    mod portable {
        use std::{io, process::Child};

        pub struct KillOnCloseJob;

        impl KillOnCloseJob {
            pub fn new() -> io::Result<Self> {
                Ok(Self)
            }

            pub fn assign(&self, _child: &Child) -> io::Result<()> {
                Ok(())
            }
        }
    }

    #[cfg(not(windows))]
    pub use portable::KillOnCloseJob;
    #[cfg(windows)]
    pub use windows::KillOnCloseJob;
}

use platform::KillOnCloseJob;

#[derive(Clone, Debug)]
pub struct SidecarSpec {
    pub id: String,
    pub executable: PathBuf,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
}

impl SidecarSpec {
    #[must_use]
    pub fn new(id: impl Into<String>, executable: impl Into<PathBuf>) -> Self {
        Self {
            id: id.into(),
            executable: executable.into(),
            args: Vec::new(),
            cwd: None,
        }
    }

    #[must_use]
    pub fn args(mut self, args: impl IntoIterator<Item = impl Into<String>>) -> Self {
        self.args = args.into_iter().map(Into::into).collect();
        self
    }

    #[must_use]
    pub fn cwd(mut self, cwd: impl Into<PathBuf>) -> Self {
        self.cwd = Some(cwd.into());
        self
    }

    fn validate(&self) -> Result<(), ProcessError> {
        if self.id.trim().is_empty() {
            return Err(ProcessError::InvalidId);
        }
        validate_absolute_file(&self.executable)?;
        if let Some(cwd) = &self.cwd
            && (!cwd.is_absolute() || !cwd.is_dir())
        {
            return Err(ProcessError::InvalidWorkingDirectory(cwd.clone()));
        }
        Ok(())
    }
}

fn validate_absolute_file(path: &Path) -> Result<(), ProcessError> {
    if !path.is_absolute() || !path.is_file() {
        return Err(ProcessError::InvalidExecutable(path.to_owned()));
    }
    Ok(())
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ManagedProcessInfo {
    pub id: String,
    pub pid: u32,
}

pub struct ManagedProcessPipes {
    pub info: ManagedProcessInfo,
    pub stdin: ChildStdin,
    pub stdout: ChildStdout,
}

struct ManagedChild {
    child: Child,
    pid: u32,
}

#[derive(Debug, Error)]
pub enum ProcessError {
    #[error("sidecar id must not be empty")]
    InvalidId,
    #[error("sidecar executable must be an existing absolute file: {0}")]
    InvalidExecutable(PathBuf),
    #[error("sidecar working directory must be an existing absolute directory: {0}")]
    InvalidWorkingDirectory(PathBuf),
    #[error("sidecar is already running: {0}")]
    AlreadyRunning(String),
    #[error("failed to initialize process job: {0}")]
    JobInitialization(std::io::Error),
    #[error("failed to spawn sidecar {id}: {source}")]
    Spawn {
        id: String,
        #[source]
        source: std::io::Error,
    },
    #[error("sidecar {id} did not expose its {pipe} pipe")]
    MissingPipe { id: String, pipe: &'static str },
    #[error("failed to assign sidecar {id} to the process job: {source}")]
    JobAssignment {
        id: String,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to wait for sidecar {id}: {source}")]
    Wait {
        id: String,
        #[source]
        source: std::io::Error,
    },
    #[error("failed to terminate sidecar {id}: {source}")]
    Terminate {
        id: String,
        #[source]
        source: std::io::Error,
    },
    #[error("sidecar is not running: {0}")]
    NotRunning(String),
    #[error("process supervisor lock was poisoned")]
    LockPoisoned,
}

pub struct ProcessSupervisor {
    job: KillOnCloseJob,
    children: Mutex<HashMap<String, ManagedChild>>,
}

impl ProcessSupervisor {
    pub fn new() -> Result<Self, ProcessError> {
        Ok(Self {
            job: KillOnCloseJob::new().map_err(ProcessError::JobInitialization)?,
            children: Mutex::new(HashMap::new()),
        })
    }

    pub fn spawn(&self, spec: SidecarSpec) -> Result<ManagedProcessInfo, ProcessError> {
        spec.validate()?;
        let mut children = self
            .children
            .lock()
            .map_err(|_| ProcessError::LockPoisoned)?;
        if children.contains_key(&spec.id) {
            return Err(ProcessError::AlreadyRunning(spec.id));
        }

        let mut command = Command::new(&spec.executable);
        command
            .args(&spec.args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if let Some(cwd) = &spec.cwd {
            command.current_dir(cwd);
        }
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        let mut child = command.spawn().map_err(|source| ProcessError::Spawn {
            id: spec.id.clone(),
            source,
        })?;
        if let Err(source) = self.job.assign(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(ProcessError::JobAssignment {
                id: spec.id,
                source,
            });
        }

        let info = ManagedProcessInfo {
            id: spec.id.clone(),
            pid: child.id(),
        };
        children.insert(
            spec.id,
            ManagedChild {
                pid: info.pid,
                child,
            },
        );
        Ok(info)
    }

    pub fn spawn_piped(&self, spec: SidecarSpec) -> Result<ManagedProcessPipes, ProcessError> {
        spec.validate()?;
        let mut children = self
            .children
            .lock()
            .map_err(|_| ProcessError::LockPoisoned)?;
        if children.contains_key(&spec.id) {
            return Err(ProcessError::AlreadyRunning(spec.id));
        }

        let mut command = Command::new(&spec.executable);
        command
            .args(&spec.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if let Some(cwd) = &spec.cwd {
            command.current_dir(cwd);
        }
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        let mut child = command.spawn().map_err(|source| ProcessError::Spawn {
            id: spec.id.clone(),
            source,
        })?;
        if let Err(source) = self.job.assign(&child) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(ProcessError::JobAssignment {
                id: spec.id,
                source,
            });
        }
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| ProcessError::MissingPipe {
                id: spec.id.clone(),
                pipe: "stdin",
            })?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| ProcessError::MissingPipe {
                id: spec.id.clone(),
                pipe: "stdout",
            })?;
        let info = ManagedProcessInfo {
            id: spec.id.clone(),
            pid: child.id(),
        };
        children.insert(
            spec.id,
            ManagedChild {
                pid: info.pid,
                child,
            },
        );
        Ok(ManagedProcessPipes {
            info,
            stdin,
            stdout,
        })
    }

    pub fn try_wait(&self, id: &str) -> Result<Option<ExitStatus>, ProcessError> {
        let mut children = self
            .children
            .lock()
            .map_err(|_| ProcessError::LockPoisoned)?;
        let status = children
            .get_mut(id)
            .ok_or_else(|| ProcessError::NotRunning(id.to_owned()))?
            .child
            .try_wait()
            .map_err(|source| ProcessError::Wait {
                id: id.to_owned(),
                source,
            })?;
        if status.is_some() {
            children.remove(id);
        }
        Ok(status)
    }

    pub fn terminate(&self, id: &str) -> Result<bool, ProcessError> {
        let mut children = self
            .children
            .lock()
            .map_err(|_| ProcessError::LockPoisoned)?;
        let Some(mut managed) = children.remove(id) else {
            return Ok(false);
        };
        managed
            .child
            .kill()
            .map_err(|source| ProcessError::Terminate {
                id: id.to_owned(),
                source,
            })?;
        let _ = managed.child.wait();
        Ok(true)
    }

    pub fn snapshot(&self) -> Result<Vec<ManagedProcessInfo>, ProcessError> {
        let children = self
            .children
            .lock()
            .map_err(|_| ProcessError::LockPoisoned)?;
        let mut processes = children
            .iter()
            .map(|(id, managed)| ManagedProcessInfo {
                id: id.clone(),
                pid: managed.pid,
            })
            .collect::<Vec<_>>();
        processes.sort_by(|left, right| left.id.cmp(&right.id));
        Ok(processes)
    }

    pub fn shutdown(&self) {
        let Ok(mut children) = self.children.lock() else {
            return;
        };
        for (_, mut managed) in children.drain() {
            let _ = managed.child.kill();
            let _ = managed.child.wait();
        }
    }
}

impl Drop for ProcessSupervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_relative_executable_paths() {
        let supervisor = ProcessSupervisor::new().expect("create supervisor");
        let error = supervisor
            .spawn(SidecarSpec::new("unsafe", "python"))
            .expect_err("relative executable must be rejected");
        assert!(matches!(error, ProcessError::InvalidExecutable(_)));
    }

    #[test]
    fn starts_and_stops_a_managed_process() {
        let supervisor = ProcessSupervisor::new().expect("create supervisor");

        #[cfg(windows)]
        let spec = {
            let executable = std::env::var_os("ComSpec")
                .map(PathBuf::from)
                .expect("ComSpec should be set on Windows");
            SidecarSpec::new("fixture", executable)
                .args(["/D", "/C", "timeout", "/T", "30", "/NOBREAK"])
        };

        #[cfg(not(windows))]
        let spec = SidecarSpec::new("fixture", PathBuf::from("/bin/sleep")).args(["30"]);

        let process = supervisor.spawn(spec).expect("spawn fixture sidecar");
        assert!(process.pid > 0);
        assert_eq!(supervisor.snapshot().expect("snapshot").len(), 1);
        supervisor.shutdown();
        assert!(
            supervisor
                .snapshot()
                .expect("snapshot after shutdown")
                .is_empty()
        );
    }

    #[test]
    fn piped_process_can_be_terminated_by_id() {
        let supervisor = ProcessSupervisor::new().expect("create supervisor");

        #[cfg(windows)]
        let spec = {
            let executable = std::env::var_os("ComSpec")
                .map(PathBuf::from)
                .expect("ComSpec should be set on Windows");
            SidecarSpec::new("piped", executable).args(["/D", "/Q", "/K"])
        };

        #[cfg(not(windows))]
        let spec = SidecarSpec::new("piped", PathBuf::from("/bin/sh"));

        let pipes = supervisor.spawn_piped(spec).expect("spawn piped fixture");
        assert!(pipes.info.pid > 0);
        assert!(supervisor.terminate("piped").expect("terminate fixture"));
        assert!(supervisor.snapshot().expect("snapshot").is_empty());
    }
}
