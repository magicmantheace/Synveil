// SPDX-License-Identifier: MPL-2.0
//! Offline structural inspection. This does not authenticate journal contents.
use serde_json::{Value, json};
use std::fs::OpenOptions;
use std::io::{self, Read};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::Path;

const MAX_BYTES: u64 = 4 * 1024 * 1024;
const MAX_LINE: usize = 64 * 1024;
const NOFOLLOW_NONBLOCK: i32 = 0o400000 | 0o4000;
const LOCK_SHARED_NONBLOCK: i32 = 1 | 4;

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "invalid audit journal structure",
    )
}

/// Inspect a stable, bounded snapshot without printing event contents or
/// requiring veil-core, the seat broker, a model, or network access.
pub fn inspect(path: &Path) -> io::Result<Value> {
    with_snapshot(path, |_, report| Ok(report))
}

pub(crate) fn with_snapshot<T>(
    path: &Path,
    operation: impl FnOnce(&[u8], Value) -> io::Result<T>,
) -> io::Result<T> {
    with_snapshot_file(path, |_, bytes, report| operation(bytes, report))
}

pub(crate) fn with_snapshot_file<T>(
    path: &Path,
    operation: impl FnOnce(&std::fs::File, &[u8], Value) -> io::Result<T>,
) -> io::Result<T> {
    let _lock = super::audit::directory_lock(path, false)?;
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(NOFOLLOW_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file() || meta.nlink() != 1 || meta.mode() & 0o777 != 0o600 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "audit journal must be a private singly linked regular file",
        ));
    }
    if unsafe { flock(file.as_raw_fd(), LOCK_SHARED_NONBLOCK) } != 0 {
        return Err(io::Error::last_os_error());
    }
    if file.metadata()?.len() > MAX_BYTES {
        return Err(invalid());
    }
    // Keep the descriptor (and its lock) alive until inspection completes.
    let mut bytes = Vec::new();
    (&file).take(MAX_BYTES + 1).read_to_end(&mut bytes)?;
    let report = inspect_bytes(&bytes)?;
    // Keep both shared locks until the consumer finishes copying/hashing.
    operation(&file, &bytes, report)
}

pub(crate) fn inspect_bytes(bytes: &[u8]) -> io::Result<Value> {
    if bytes.len() as u64 > MAX_BYTES || (!bytes.is_empty() && !bytes.ends_with(b"\n")) {
        return Err(invalid());
    }
    let mut records = 0u64;
    for line in bytes.split_inclusive(|byte| *byte == b'\n') {
        if line.len() > MAX_LINE || line.contains(&b'\r') {
            return Err(invalid());
        }
        let value = super::audit_json::decode(line).map_err(|_| invalid())?;
        check_record(&value)?;
        records += 1;
    }
    Ok(json!({
        "schema": "synveil.audit-inspection/v1",
        "record_schema": super::audit::AUDIT_SCHEMA_V1,
        "records": records,
        "bytes": bytes.len(),
        "authenticated": false,
    }))
}

fn bounded_text(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|text| !text.is_empty() && text.len() <= 128)
}

fn check_record(value: &Value) -> io::Result<()> {
    let keys = [
        "schema",
        "component",
        "pid",
        "sequence",
        "unix_time_ms",
        "version",
        "git_revision",
        "event",
        "fields",
    ];
    let valid = value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    }) && value["schema"] == super::audit::AUDIT_SCHEMA_V1
        && value["component"] == "veil-core"
        && value["pid"]
            .as_u64()
            .is_some_and(|pid| pid > 0 && pid <= u32::MAX as u64)
        && value["sequence"]
            .as_u64()
            .is_some_and(|sequence| sequence > 0)
        && (value["unix_time_ms"].is_null() || value["unix_time_ms"].as_u64().is_some())
        && bounded_text(&value["version"])
        && (value["git_revision"].is_null() || bounded_text(&value["git_revision"]))
        && bounded_text(&value["event"])
        && value["fields"].is_object();
    if valid { Ok(()) } else { Err(invalid()) }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record() -> Value {
        super::super::audit::record("request", json!({"id": "private-value"}))
    }

    #[test]
    fn inspection_counts_records_without_disclosing_event_fields() {
        let line = format!("{}\n", record());
        let report = inspect_bytes(line.repeat(2).as_bytes()).unwrap();
        assert_eq!(report["records"], 2);
        assert_eq!(report["authenticated"], false);
        assert!(!report.to_string().contains("private-value"));
        assert_eq!(inspect_bytes(b"").unwrap()["records"], 0);
    }

    #[test]
    fn malformed_truncated_and_oversized_input_is_refused() {
        for bytes in [b"\n".as_slice(), b"{}\n", b"{", b"\xff\n", b"{}\r\n"] {
            assert!(inspect_bytes(bytes).is_err());
        }
        assert!(inspect_bytes(&vec![b' '; MAX_LINE + 1]).is_err());
        assert!(inspect_bytes(&vec![b'\n'; MAX_BYTES as usize + 1]).is_err());
        let mut line = record().to_string().into_bytes();
        assert!(inspect_bytes(&line).is_err());
        line.push(b'\n');
        assert!(inspect_bytes(&line).is_ok());
    }

    #[test]
    fn all_required_fields_and_supported_schema_are_enforced() {
        let original = record();
        for key in original.as_object().unwrap().keys() {
            let mut value = original.clone();
            value.as_object_mut().unwrap().remove(key);
            assert!(check_record(&value).is_err(), "accepted missing {key}");
        }
        for (key, wrong) in [
            ("schema", json!("synveil.audit/v99")),
            ("component", json!("planner")),
            ("pid", json!(0)),
            ("sequence", json!(-1)),
            ("unix_time_ms", json!("yesterday")),
            ("version", json!("")),
            ("git_revision", json!(false)),
            ("event", json!("x".repeat(129))),
            ("fields", json!([])),
        ] {
            let mut value = original.clone();
            value[key] = wrong;
            assert!(check_record(&value).is_err(), "accepted invalid {key}");
        }
    }

    #[test]
    fn duplicate_envelope_and_nested_field_keys_are_refused() {
        let original = record().to_string();
        for key in ["pid", "\\u0070id"] {
            let line = format!("{{\"{key}\":123,{}\n", &original[1..]);
            assert!(inspect_bytes(line.as_bytes()).is_err());
        }
        let mut value = record();
        value["fields"] = json!({"nested": [{"x": 1}]});
        let line = format!("{value}\n").replace("\"x\":1", "\"x\":1,\"\\u0078\":2");
        assert!(inspect_bytes(line.as_bytes()).is_err());
    }
}
