// SPDX-License-Identifier: MPL-2.0
//! Host-bound integration guards: no echo, default credentials or useful
//! enrollment through a non-privileged workspace test runner.
use std::io::Write;
use std::process::{Command, Stdio};

#[test]
fn refused_enrollment_never_echoes_password_bytes() {
    let secret = b"integration-test-secret-without-real-account";
    let mut child = Command::new(env!("CARGO_BIN_EXE_veil-enroll"))
        .arg("--password-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(secret).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(!output.status.success(), "host test must not enroll accounts");
    assert!(!output.stdout.windows(secret.len()).any(|value| value == secret));
    assert!(!output.stderr.windows(secret.len()).any(|value| value == secret));
    assert!(!output.stdout.windows(b"SYNVEIL_CREDENTIAL_ENROLLED".len())
        .any(|value| value == b"SYNVEIL_CREDENTIAL_ENROLLED"));
}

#[test]
fn argument_supplied_password_is_refused_without_echo() {
    let secret = "argument-must-never-be-password-storage";
    let output = Command::new(env!("CARGO_BIN_EXE_veil-enroll"))
        .args(["--password-stdin", secret])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!String::from_utf8_lossy(&output.stdout).contains(secret));
    assert!(!String::from_utf8_lossy(&output.stderr).contains(secret));
}
