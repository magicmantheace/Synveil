// SPDX-License-Identifier: MPL-2.0
use serde_json::{Value, json};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::io::AsRawFd;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

struct Fixture(std::path::PathBuf);

impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "veil-audit-check-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn invoke(&self, name: &str) -> Output {
        Command::new(env!("CARGO_BIN_EXE_veil-audit"))
            .args(["check", "--path"])
            .arg(self.0.join(name))
            .env("SYNVEIL_CORE_SOCKET", self.0.join("absent-core.sock"))
            .output()
            .unwrap()
    }

    fn journal(&self) -> std::path::PathBuf {
        let path = self.0.join("journal");
        let line = format!("{}\n", veil_core::audit::record("start", json!({})));
        fs::write(&path, line).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        path
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn offline_check_preserves_journal_and_needs_no_core_socket() {
    let fixture = Fixture::new();
    let path = fixture.journal();
    let original = fs::read(&path).unwrap();
    let output = fixture.invoke("journal");
    assert!(output.status.success());
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["schema"], "synveil.audit-inspection/v1");
    assert_eq!(report["records"], 1);
    assert_eq!(report["bytes"], original.len());
    assert_eq!(report["authenticated"], false);
    assert!(output.stderr.is_empty());
    assert_eq!(fs::read(&path).unwrap(), original);
}

#[test]
fn damaged_journal_is_not_repaired_or_disclosed() {
    let fixture = Fixture::new();
    let path = fixture.journal();
    let bytes = b"private-incomplete-content";
    fs::write(&path, bytes).unwrap();
    let output = fixture.invoke("journal");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(
        !String::from_utf8(output.stderr)
            .unwrap()
            .contains("private")
    );
    assert_eq!(fs::read(&path).unwrap(), bytes);
    let missing = fixture.invoke("missing");
    assert!(!missing.status.success());
    assert!(!fixture.0.join("missing").exists());
}

#[test]
fn unsafe_file_objects_are_refused() {
    let fixture = Fixture::new();
    let path = fixture.journal();
    let original = fs::read(&path).unwrap();
    std::os::unix::fs::symlink(&path, fixture.0.join("link")).unwrap();
    assert!(!fixture.invoke("link").status.success());
    fs::hard_link(&path, fixture.0.join("hardlink")).unwrap();
    assert!(!fixture.invoke("hardlink").status.success());
    fs::remove_file(fixture.0.join("hardlink")).unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    assert!(!fixture.invoke("journal").status.success());
    assert!(!fixture.invoke(".").status.success());
    assert_eq!(fs::read(&path).unwrap(), original);
}

#[test]
fn inspection_refuses_busy_writer_and_recovers_after_release() {
    let fixture = Fixture::new();
    let path = fixture.journal();
    let held = fs::OpenOptions::new().append(true).open(&path).unwrap();
    assert_eq!(unsafe { flock(held.as_raw_fd(), 2 | 4) }, 0);
    let output = fixture.invoke("journal");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(
        String::from_utf8(output.stderr)
            .unwrap()
            .contains("WouldBlock")
    );
    drop(held);
    assert!(fixture.invoke("journal").status.success());
}

#[test]
fn fifo_is_refused_without_waiting_for_a_writer() {
    let fixture = Fixture::new();
    assert!(
        Command::new("mkfifo")
            .arg(fixture.0.join("fifo"))
            .status()
            .unwrap()
            .success()
    );
    assert!(!fixture.invoke("fifo").status.success());
}

#[test]
fn ambiguous_record_is_preserved_without_printing_its_fields() {
    let fixture = Fixture::new();
    let path = fixture.journal();
    let record = veil_core::audit::record("start", json!({})).to_string();
    let bytes = format!("{{\"pid\":123,{}\n", &record[1..]).into_bytes();
    fs::write(&path, &bytes).unwrap();
    let output = fixture.invoke("journal");
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(fs::read(&path).unwrap(), bytes);
    let error = String::from_utf8(output.stderr).unwrap();
    assert!(error.contains("InvalidData"));
    assert!(!error.contains("pid"));
}
