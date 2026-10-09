// SPDX-License-Identifier: MPL-2.0

use std::io;
use std::os::raw::{c_int, c_ulong};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::Duration;

const MAX_RESTARTS: usize = 3;
const RESTART_DELAY: Duration = Duration::from_millis(250);

unsafe extern "C" {
    fn prctl(option: c_int, ...) -> c_int;
    fn getppid() -> c_int;
}

struct ManagedChild(Child);

impl Drop for ManagedChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

pub fn supervise(socket: &Path) -> io::Result<()> {
    let executable = std::env::current_exe()?;
    let mut command = Command::new(executable);
    command.arg("--socket").arg(socket).stdin(Stdio::null());
    let parent_pid = std::process::id() as c_int;
    // Linux kills the worker if its supervisor dies, even during startup.
    // pre_exec uses only async-signal-safe system calls and numeric errors.
    unsafe {
        command.pre_exec(move || {
            if prctl(1, 9 as c_ulong, 0 as c_ulong, 0 as c_ulong, 0 as c_ulong) != 0 {
                return Err(io::Error::last_os_error());
            }
            if getppid() != parent_pid {
                return Err(io::Error::from_raw_os_error(10));
            }
            Ok(())
        });
    }
    run_children(&mut command, MAX_RESTARTS, RESTART_DELAY)
}

fn run_children(command: &mut Command, max_restarts: usize, delay: Duration) -> io::Result<()> {
    for attempt in 0..=max_restarts {
        let mut child = ManagedChild(command.spawn()?);
        super::log_event(
            "supervisor_child_started",
            serde_json::json!({ "pid": child.0.id(), "attempt": attempt }),
        );
        let status = child.0.wait()?;
        super::log_event(
            "supervisor_child_exited",
            serde_json::json!({ "attempt": attempt, "status": status.to_string() }),
        );
        if status.success() {
            return Ok(());
        }
        if attempt < max_restarts {
            thread::sleep(delay);
        }
    }
    super::log_event(
        "supervisor_exhausted",
        serde_json::json!({ "restarts": max_restarts }),
    );
    Err(io::Error::other("core restart budget exhausted"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn successful_exit_is_not_restarted() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exit 0"]);
        run_children(&mut command, 3, Duration::ZERO).unwrap();
    }

    #[test]
    fn repeated_failure_exhausts_budget() {
        let counter =
            std::env::temp_dir().join(format!("veil-restart-test-{}", std::process::id()));
        let _ = std::fs::remove_file(&counter);
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "printf x >> \"$1\"; exit 7", "supervisor-test"])
            .arg(&counter);
        assert!(run_children(&mut command, 2, Duration::ZERO).is_err());
        assert_eq!(std::fs::read(&counter).unwrap(), b"xxx");
        std::fs::remove_file(counter).unwrap();
    }
}
