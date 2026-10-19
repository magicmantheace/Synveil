// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
use std::io::{BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use veil_protocol::{Request, decode_response, encode_line, read_frame};

struct Core(Option<Child>);

impl Drop for Core {
    fn drop(&mut self) {
        if let Some(child) = self.0.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[test]
fn running_core_emits_versioned_status_lifecycle_records() {
    exercise_core_journal(false);
}

#[test]
fn incomplete_journal_keeps_core_status_and_console_audit_available() {
    exercise_core_journal(true);
}

fn exercise_core_journal(incomplete: bool) {
    let directory = std::env::temp_dir().join(format!(
        "veil-audit-test-{}-{incomplete}",
        std::process::id()
    ));
    std::fs::create_dir(&directory).unwrap();
    let socket = directory.join("core.sock");
    let journal = directory.join("core-audit.jsonl");
    if incomplete {
        std::fs::write(&journal, b"preserved-partial-record").unwrap();
        std::fs::set_permissions(&journal, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    let mut core = Core(Some(
        Command::new(env!("CARGO_BIN_EXE_veil-core"))
            .arg("--once")
            .arg("--socket")
            .arg(&socket)
            .env("SYNVEIL_AUDIT_PATH", &journal)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap(),
    ));
    let pid = core.0.as_ref().unwrap().id();
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut stream = loop {
        if let Ok(stream) = UnixStream::connect(&socket) {
            break stream;
        }
        assert!(Instant::now() < deadline, "core never accepted connections");
        thread::sleep(Duration::from_millis(10));
    };
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let request = Request::new("audit-test", "status", json!({}));
    stream.write_all(&encode_line(&request).unwrap()).unwrap();
    let mut reader = BufReader::new(stream.try_clone().unwrap());
    let response = decode_response(&read_frame(&mut reader).unwrap().unwrap()).unwrap();
    assert!(response.ok);
    let status = response.result.as_ref().unwrap();
    assert_eq!(status["persistent_audit"]["configured"], true);
    assert_eq!(
        status["persistent_audit"]["state"],
        if incomplete { "degraded" } else { "healthy" }
    );
    assert_eq!(
        status["persistent_audit"]["failed_writes"],
        if incomplete { 3 } else { 0 }
    );
    let damaged = journal.with_extension("damaged");
    if incomplete {
        std::fs::rename(&journal, &damaged).unwrap();
        let request = Request::new("audit-recovered", "status", json!({}));
        stream.write_all(&encode_line(&request).unwrap()).unwrap();
        let response = decode_response(&read_frame(&mut reader).unwrap().unwrap()).unwrap();
        let status = response.result.unwrap();
        assert_eq!(status["persistent_audit"]["state"], "healthy");
        assert_eq!(status["persistent_audit"]["failed_writes"], 3);
    }
    drop(reader);
    drop(stream);
    let output = core.0.take().unwrap().wait_with_output().unwrap();
    assert!(output.status.success());
    let console = String::from_utf8(output.stderr).unwrap();
    assert_eq!(
        console.contains("[synveil-audit] persistent write failed:"),
        incomplete
    );
    let records: Vec<Value> = console
        .lines()
        .filter(|line| !line.starts_with("[synveil-audit]"))
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert!(records.len() >= 4);
    let mut previous = 0;
    for record in &records {
        assert_eq!(record["schema"], "synveil.audit/v1");
        assert_eq!(record["pid"], pid);
        assert_eq!(record["version"], veil_protocol::BUILD_VERSION);
        let sequence = record["sequence"].as_u64().unwrap();
        assert!(sequence > previous);
        previous = sequence;
    }
    for event in ["start", "socket_ready", "request", "shutdown"] {
        assert!(records.iter().any(|record| record["event"] == event));
    }
    let request = records
        .iter()
        .find(|record| record["event"] == "request")
        .unwrap();
    assert_eq!(request["fields"]["id"], "audit-test");
    assert_eq!(request["fields"]["method"], "status");
    if incomplete {
        assert_eq!(
            std::fs::read(&damaged).unwrap(),
            b"preserved-partial-record"
        );
    } else {
        let persisted: Vec<Value> = std::fs::read_to_string(&journal)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(
            persisted, records,
            "journal must match console event stream"
        );
    }
    assert_eq!(
        std::fs::metadata(&journal).unwrap().permissions().mode() & 0o777,
        0o600,
        "journal must be owner-readable only"
    );
    std::fs::remove_file(journal).unwrap();
    if incomplete {
        std::fs::remove_file(damaged).unwrap();
    }
    std::fs::remove_file(directory.join(".audit-lock")).unwrap();
    std::fs::remove_dir(directory).unwrap();
}
