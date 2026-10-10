// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
use std::io::{BufReader, Write};
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
    let directory = std::env::temp_dir().join(format!("veil-audit-test-{}", std::process::id()));
    std::fs::create_dir(&directory).unwrap();
    let socket = directory.join("core.sock");
    let mut core = Core(Some(
        Command::new(env!("CARGO_BIN_EXE_veil-core"))
            .arg("--once")
            .arg("--socket")
            .arg(&socket)
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
    drop(reader);
    drop(stream);
    let output = core.0.take().unwrap().wait_with_output().unwrap();
    assert!(output.status.success());
    let records: Vec<Value> = String::from_utf8(output.stderr)
        .unwrap()
        .lines()
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
    std::fs::remove_dir(directory).unwrap();
}
