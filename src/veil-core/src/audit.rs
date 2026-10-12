// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

pub const AUDIT_SCHEMA_V1: &str = "synveil.audit/v1";
static SEQUENCE: AtomicU64 = AtomicU64::new(1);

pub fn record(event: &str, fields: Value) -> Value {
    let unix_time_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok());
    record_at(event, fields, unix_time_ms)
}

fn record_at(event: &str, fields: Value, unix_time_ms: Option<u64>) -> Value {
    json!({
        "schema": AUDIT_SCHEMA_V1,
        "component": "veil-core",
        "pid": std::process::id(),
        "sequence": SEQUENCE.fetch_add(1, Ordering::Relaxed),
        "unix_time_ms": unix_time_ms,
        "version": veil_protocol::BUILD_VERSION,
        "git_revision": veil_protocol::BUILD_REVISION,
        "event": event,
        "fields": fields,
    })
}

pub fn emit(event: &str, fields: Value) {
    let line = record(event, fields).to_string();
    eprintln!("{line}");
    // Opt-in audit journal, only set by bootstrap after /var is mounted.
    // Console logging remains available even if persistent storage fails.
    if let Some(path) = std::env::var_os("SYNVEIL_AUDIT_PATH")
        && let Err(error) = append_record(Path::new(&path), &line)
    {
        eprintln!("[synveil-audit] persistent write failed: {error}");
    }
}

// Linux O_NOFOLLOW and O_NONBLOCK: refuse final symlinks and FIFOs.
const LINUX_O_NOFOLLOW: i32 = 0o400000;
const LINUX_O_NONBLOCK: i32 = 0o4000;

fn unsafe_journal() -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, "unsafe audit journal path")
}

fn append_record(path: &Path, line: &str) -> io::Result<()> {
    let parent = path.parent().ok_or_else(unsafe_journal)?;
    let directory = fs::symlink_metadata(parent)?;
    if !directory.file_type().is_dir() || directory.mode() & 0o022 != 0 {
        return Err(unsafe_journal());
    }
    // Refuse existing suspicious objects, then validate the opened inode.
    match fs::symlink_metadata(path) {
        Ok(meta) if !meta.file_type().is_file() => return Err(unsafe_journal()),
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .custom_flags(LINUX_O_NOFOLLOW | LINUX_O_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.file_type().is_file()
        || meta.uid() != directory.uid()
        || meta.nlink() != 1
        || meta.permissions().mode() & 0o777 != 0o600
    {
        return Err(unsafe_journal());
    }
    let mut bytes = line.as_bytes().to_vec();
    bytes.push(b'\n');
    file.write_all(&bytes)?;
    file.sync_data()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_build_process_and_event_identity() {
        let value = record_at(
            "request_rejected",
            json!({ "code": "malformed_message" }),
            Some(123),
        );
        assert_eq!(value["schema"], AUDIT_SCHEMA_V1);
        assert_eq!(value["component"], "veil-core");
        assert_eq!(value["pid"], std::process::id());
        assert_eq!(value["version"], veil_protocol::BUILD_VERSION);
        assert_eq!(value["event"], "request_rejected");
        assert_eq!(value["fields"]["code"], "malformed_message");
        assert_eq!(value["unix_time_ms"], 123);
    }

    #[test]
    fn sequences_increase_within_process() {
        let first = record("start", json!({}))["sequence"].as_u64().unwrap();
        let second = record("shutdown", json!({}))["sequence"].as_u64().unwrap();
        assert!(first > 0 && second > first);
    }

    #[test]
    fn unavailable_clock_is_explicit() {
        assert!(record_at("start", json!({}), None)["unix_time_ms"].is_null());
    }

    fn test_dir(label: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "synveil-audit-{label}-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn symlinks_are_rejected_without_touching_target() {
        let dir = test_dir("link");
        let target = dir.join("target");
        fs::write(&target, b"original").unwrap();
        let journal = dir.join("journal");
        std::os::unix::fs::symlink(&target, &journal).unwrap();
        assert_eq!(
            append_record(&journal, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read(&target).unwrap(), b"original");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn hardlinks_and_permissive_journals_are_rejected() {
        let dir = test_dir("hardlink");
        let original = dir.join("original");
        fs::write(&original, b"unchanged").unwrap();
        fs::set_permissions(&original, fs::Permissions::from_mode(0o600)).unwrap();
        let journal = dir.join("journal");
        fs::hard_link(&original, &journal).unwrap();
        assert_eq!(
            append_record(&journal, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        fs::remove_file(&journal).unwrap();
        fs::set_permissions(&original, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            append_record(&original, "injected").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read(&original).unwrap(), b"unchanged");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn private_append_succeeds_but_unsafe_parent_refuses_write() {
        let dir = test_dir("parent");
        let journal = dir.join("journal");
        append_record(&journal, "first").unwrap();
        append_record(&journal, "second").unwrap();
        assert_eq!(fs::read_to_string(&journal).unwrap(), "first\nsecond\n");
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o777)).unwrap();
        assert_eq!(
            append_record(&journal, "third").unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(fs::read_to_string(&journal).unwrap(), "first\nsecond\n");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn untrusted_fields_cannot_inject_record_lines() {
        let value = record("request", json!({ "id": "one\ntwo\r\nthree" }));
        let encoded = serde_json::to_string(&value).unwrap();
        assert!(!encoded.contains('\n') && !encoded.contains('\r'));
        assert_eq!(serde_json::from_str::<Value>(&encoded).unwrap(), value);
    }
}
