// SPDX-License-Identifier: MPL-2.0
//! One-shot developer-VM credential enrollment. No stdin TTY echo, argv
//! password, environment password, default credential or login IPC.
use std::io::{self, IsTerminal, Read};
use std::process::ExitCode;
use veil_core::credential::enroll_root_credential;

fn main() -> ExitCode {
    if std::env::args_os().skip(1).collect::<Vec<_>>() != ["--password-stdin"] {
        eprintln!("usage: veil-enroll --password-stdin (protected pipe only)");
        return ExitCode::FAILURE;
    }
    if io::stdin().is_terminal() {
        eprintln!("[synveil-enroll] refusing echoed terminal input; supply a protected pipe");
        return ExitCode::FAILURE;
    }
    let mut password = Vec::new();
    let read_result = io::stdin().take(1025).read_to_end(&mut password);
    let result = match read_result {
        Ok(_) => enroll_root_credential(&password),
        Err(error) => Err(error),
    };
    // Best-effort buffer overwrite. A security-reviewed secret-handling
    // component is still required before offering interactive login.
    password.fill(0);
    match result {
        Ok(()) => {
            println!("SYNVEIL_CREDENTIAL_ENROLLED");
            ExitCode::SUCCESS
        }
        Err(_) => {
            eprintln!("[synveil-enroll] enrollment refused");
            ExitCode::FAILURE
        }
    }
}
