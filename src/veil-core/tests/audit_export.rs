// SPDX-License-Identifier: MPL-2.0
use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::process::Command;

#[test]
fn offline_cli_exports_verifies_and_refuses_replacement_or_bad_arguments() {
    let root = std::env::temp_dir().join(format!("synveil-export-cli-{}", std::process::id()));
    fs::DirBuilder::new().mode(0o700).create(&root).unwrap();
    let copies = root.join("copies");
    fs::DirBuilder::new().mode(0o700).create(&copies).unwrap();
    let source = root.join("journal");
    let copy = copies.join("snapshot");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&source)
        .unwrap();
    writeln!(
        file,
        "{}",
        veil_core::audit::record("request", json!({"private": "secret"}))
    )
    .unwrap();
    drop(file);
    let tool = env!("CARGO_BIN_EXE_veil-audit");
    let exported = Command::new(tool)
        .arg("export")
        .arg("--path")
        .arg(&source)
        .arg("--output")
        .arg(&copy)
        .output()
        .unwrap();
    assert!(exported.status.success());
    let receipt: Value = serde_json::from_slice(&exported.stdout).unwrap();
    assert!(!String::from_utf8_lossy(&exported.stdout).contains("secret"));
    let digest = receipt["digest"].as_str().unwrap();
    let verified = Command::new(tool)
        .arg("verify")
        .arg("--path")
        .arg(&copy)
        .arg("--digest")
        .arg(digest)
        .output()
        .unwrap();
    assert!(verified.status.success());
    let saved = fs::read(&copy).unwrap();
    let refused = Command::new(tool)
        .arg("export")
        .arg("--path")
        .arg(&source)
        .arg("--output")
        .arg(&copy)
        .output()
        .unwrap();
    assert!(!refused.status.success());
    assert_eq!(fs::read(&copy).unwrap(), saved);
    for arguments in [
        vec!["export"],
        vec!["verify", "--digest", "private-input"],
        vec!["check", "--output", "private-input"],
        vec!["check", "--path", "a", "--path", "b"],
        vec!["check", "--snapshot", "private-input"],
        vec!["export", "--authorization", "private-input"],
        vec!["release-archive"],
        vec!["release-archive", "--path", "a", "--path", "b"],
    ] {
        let result = Command::new(tool).args(arguments).output().unwrap();
        assert!(!result.status.success());
        assert!(!String::from_utf8_lossy(&result.stderr).contains("private-input"));
    }
    let archive = source.with_file_name("journal.1");
    fs::copy(&source, &archive).unwrap();
    let intent = copies.join("release.json");
    let released = Command::new(tool)
        .arg("release-archive")
        .arg("--path")
        .arg(&source)
        .arg("--snapshot")
        .arg(&copy)
        .arg("--authorization")
        .arg(&intent)
        .arg("--digest")
        .arg(digest)
        .output()
        .unwrap();
    assert!(released.status.success());
    let released_report: Value = serde_json::from_slice(&released.stdout).unwrap();
    assert_eq!(released_report["released"], true);
    assert!(!archive.exists());
    assert_eq!(fs::read(&source).unwrap(), saved);
    assert_eq!(fs::read(&copy).unwrap(), saved);
    assert!(!String::from_utf8_lossy(&released.stdout).contains("secret"));
    assert!(intent.exists());
    fs::remove_dir_all(root).unwrap();
}
