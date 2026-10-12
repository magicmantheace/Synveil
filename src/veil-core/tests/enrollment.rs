// SPDX-License-Identifier: MPL-2.0
//! Host tests verify that the enrollment CLI never logs input secrets.
use std::io::Write;
use std::process::{Command, Stdio};

#[test]
fn refused_enrollment_never_echoes_password_bytes() {
    const SECRET: &str = "integration-test-secret-without-real-account";
    let mut child = Command::new(env!("CARGO_BIN_EXE_veil-enroll"))
        .arg("--password-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(SECRET.as_bytes())
        .unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!output.status.success());
    assert!(!stdout.contains(SECRET));
    assert!(!stderr.contains(SECRET));
    assert!(!stdout.contains("SYNVEIL_CREDENTIAL_ENROLLED"));
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

#[test]
fn seat_password_client_rejects_secret_arguments() {
    let secret = "do-not-allow-credentials-on-argv";
    let output = Command::new(env!("CARGO_BIN_EXE_veil-seat"))
        .args(["--login-stdin", secret])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!String::from_utf8_lossy(&output.stdout).contains(secret));
    assert!(!String::from_utf8_lossy(&output.stderr).contains(secret));
}
