// SPDX-License-Identifier: MPL-2.0
use serde_json::{Value, json};
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static NEXT: AtomicU64 = AtomicU64::new(0);

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

struct Fixture {
    root: PathBuf,
    active: PathBuf,
    archive: PathBuf,
    snapshot: PathBuf,
    receipt: PathBuf,
    digest: String,
}

fn write_record(path: &Path, label: &str) {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .unwrap();
    writeln!(
        file,
        "{}",
        veil_core::audit::record("request", json!({"private": label}))
    )
    .unwrap();
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "synveil-retention-cli-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
        let copies = root.join("copies");
        fs::DirBuilder::new().mode(0o700).create(&copies).unwrap();
        let active = root.join("journal");
        let archive = root.join("journal.1");
        let snapshot = copies.join("snapshot");
        let receipt = copies.join("release.json");
        write_record(&active, "active-private-content");
        write_record(&archive, "archived-private-content");
        let exported = Command::new(env!("CARGO_BIN_EXE_veil-audit"))
            .arg("export")
            .arg("--path")
            .arg(&archive)
            .arg("--output")
            .arg(&snapshot)
            .output()
            .unwrap();
        assert!(exported.status.success(), "{exported:?}");
        let report: Value = serde_json::from_slice(&exported.stdout).unwrap();
        let digest = report["digest"].as_str().unwrap().to_owned();
        Self {
            root,
            active,
            archive,
            snapshot,
            receipt,
            digest,
        }
    }

    fn arguments(&self) -> Vec<OsString> {
        vec![
            "release-archive".into(),
            "--path".into(),
            self.active.clone().into_os_string(),
            "--snapshot".into(),
            self.snapshot.clone().into_os_string(),
            "--authorization".into(),
            self.receipt.clone().into_os_string(),
            "--digest".into(),
            self.digest.clone().into(),
        ]
    }

    fn run(&self, args: &[OsString]) -> Output {
        let mut child = Command::new(env!("CARGO_BIN_EXE_veil-audit"))
            .args(args)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while child.try_wait().unwrap().is_none() {
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("veil-audit did not return within the CLI test deadline");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        child.wait_with_output().unwrap()
    }

    fn refused(&self, args: &[OsString]) {
        let result = self.run(args);
        assert!(!result.status.success(), "{result:?}");
        assert!(result.stdout.is_empty());
        let error = String::from_utf8(result.stderr).unwrap();
        assert!(error.starts_with("veil-audit: operation refused ("));
        // Inspect the actual supplied values, including malformed digests.
        for value in args.iter().skip(2).step_by(2) {
            assert!(!error.contains(value.to_string_lossy().as_ref()));
        }
        for private in [
            self.root.to_str().unwrap(),
            self.digest.as_str(),
            "private-content",
            "private-argument",
        ] {
            assert!(!error.contains(private));
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn release_cli_rejects_incomplete_duplicate_and_foreign_arguments_without_mutation() {
    let fixture = Fixture::new();
    let active = fs::read(&fixture.active).unwrap();
    let archived = fs::read(&fixture.archive).unwrap();
    let original = fixture.arguments();
    let mut cases = Vec::new();
    for index in [1, 3, 5, 7] {
        let mut missing = original.clone();
        missing.drain(index..index + 2);
        cases.push(missing);
        let mut duplicate = original.clone();
        duplicate.extend_from_slice(&original[index..index + 2]);
        cases.push(duplicate);
    }
    for flag in ["--output", "--unknown"] {
        let mut extra = original.clone();
        extra.extend([flag.into(), "private-argument".into()]);
        cases.push(extra);
    }
    let mut dangling = original.clone();
    dangling.push("--unknown".into());
    cases.push(dangling);
    let mut foreign_command = original.clone();
    foreign_command[0] = "check".into();
    cases.push(foreign_command);
    for digest in [
        OsString::from("private-argument"),
        OsString::from(fixture.digest.to_uppercase()),
        OsString::from_vec(vec![0xff]),
    ] {
        let mut invalid = original.clone();
        invalid[8] = digest;
        cases.push(invalid);
    }
    for args in cases {
        fixture.refused(&args);
        assert_eq!(fs::read(&fixture.active).unwrap(), active);
        assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
        assert_eq!(fs::read(&fixture.snapshot).unwrap(), archived);
        assert!(!fixture.receipt.exists());
    }
}

#[test]
fn release_cli_replay_cannot_replace_receipt_or_remove_a_new_archive() {
    let fixture = Fixture::new();
    let active_meta = fs::metadata(&fixture.active).unwrap();
    let active = fs::read(&fixture.active).unwrap();
    let archived = fs::read(&fixture.archive).unwrap();
    let result = fixture.run(&fixture.arguments());
    assert!(result.status.success(), "{result:?}");
    let completed: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(completed["released"], true);
    assert_eq!(completed["authenticated"], false);
    let saved_receipt = fs::read(&fixture.receipt).unwrap();
    let lines: Vec<Value> = String::from_utf8(saved_receipt.clone())
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(lines.len(), 2);
    assert_eq!(lines[0]["phase"], "authorized-before-unlink");
    assert_eq!(lines[1], completed);
    fixture.refused(&fixture.arguments());
    assert!(!fixture.archive.exists());

    // The next archive can contain identical bytes. Reusing an old receipt
    // must still refuse before unlink; a fresh explicit receipt is required.
    fs::copy(&fixture.snapshot, &fixture.archive).unwrap();
    let next_archive = fs::metadata(&fixture.archive).unwrap();
    fixture.refused(&fixture.arguments());
    assert_eq!(
        fs::metadata(&fixture.archive).unwrap().ino(),
        next_archive.ino()
    );
    assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
    assert_eq!(fs::read(&fixture.receipt).unwrap(), saved_receipt);
    let mut fresh = fixture.arguments();
    let new_receipt = fixture.receipt.with_file_name("next-release.json");
    fresh[6] = new_receipt.clone().into_os_string();
    assert!(fixture.run(&fresh).status.success());
    assert!(!fixture.archive.exists());
    assert_eq!(fs::read(&fixture.receipt).unwrap(), saved_receipt);
    assert_eq!(fs::read(&fixture.active).unwrap(), active);
    assert_eq!(
        fs::metadata(&fixture.active).unwrap().ino(),
        active_meta.ino()
    );
    assert_eq!(fs::read(&fixture.snapshot).unwrap(), archived);
    let next_intent: Value = serde_json::from_str(
        fs::read_to_string(new_receipt)
            .unwrap()
            .lines()
            .next()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(next_intent["archive_inode"], next_archive.ino());
}

#[test]
fn release_cli_refuses_busy_files_and_retries_after_lock_release() {
    let fixture = Fixture::new();
    let active = fs::read(&fixture.active).unwrap();
    let archived = fs::read(&fixture.archive).unwrap();
    for path in [&fixture.active, &fixture.archive, &fixture.snapshot] {
        let lock = OpenOptions::new().read(true).open(path).unwrap();
        assert_eq!(unsafe { flock(lock.as_raw_fd(), 2 | 4) }, 0);
        fixture.refused(&fixture.arguments());
        assert_eq!(fs::read(&fixture.active).unwrap(), active);
        assert_eq!(fs::read(&fixture.archive).unwrap(), archived);
        assert_eq!(fs::read(&fixture.snapshot).unwrap(), archived);
        assert!(!fixture.receipt.exists());
        drop(lock);
    }
    assert!(fixture.run(&fixture.arguments()).status.success());
    assert!(!fixture.archive.exists());
    assert_eq!(fs::read(&fixture.active).unwrap(), active);
    assert_eq!(fs::read(&fixture.snapshot).unwrap(), archived);
}

#[test]
fn inspection_cli_observes_complete_and_interrupted_receipts_without_repair() {
    let fixture = Fixture::new();
    assert!(fixture.run(&fixture.arguments()).status.success());
    let receipt = fs::read(&fixture.receipt).unwrap();
    let intent_end = receipt.iter().position(|b| *b == b'\n').unwrap() + 1;
    let mut args = fixture.arguments();
    args[0] = "inspect-release".into();
    for (size, state) in [
        (receipt.len(), "completion-recorded"),
        (intent_end, "authorization-only"),
        (intent_end + 17, "incomplete-completion"),
    ] {
        fs::write(&fixture.receipt, &receipt[..size]).unwrap();
        let output = fixture.run(&args);
        assert!(output.status.success(), "{output:?}");
        let report: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(report["state"], state);
        assert_eq!(report["archive_state"], "not-inspected");
        assert!(!String::from_utf8_lossy(&output.stdout).contains("private-content"));
        assert_eq!(fs::read(&fixture.receipt).unwrap(), receipt[..size]);
        assert!(!fixture.archive.exists());
    }
    fs::write(&fixture.receipt, b"invalid-private-content\n").unwrap();
    fixture.refused(&args);
    assert_eq!(
        fs::read(&fixture.receipt).unwrap(),
        b"invalid-private-content\n"
    );
    args.extend([
        OsString::from("--output"),
        OsString::from("private-argument"),
    ]);
    fixture.refused(&args);
}
