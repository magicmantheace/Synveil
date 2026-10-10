// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
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
    eprintln!("{}", record(event, fields));
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

    #[test]
    fn untrusted_fields_cannot_inject_record_lines() {
        let value = record("request", json!({ "id": "one\ntwo\r\nthree" }));
        let encoded = serde_json::to_string(&value).unwrap();
        assert!(!encoded.contains('\n') && !encoded.contains('\r'));
        assert_eq!(serde_json::from_str::<Value>(&encoded).unwrap(), value);
    }
}
