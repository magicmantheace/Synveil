// SPDX-License-Identifier: MPL-2.0

use serde_json::{Value, json};
use std::env;
use std::io::{self, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;
use veil_protocol::{Request, Response, decode_response, encode_line, read_frame};

const DEFAULT_SOCKET_PATH: &str = "/run/synveil/veil-core.sock";

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("synctl: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<ExitCode, Box<dyn std::error::Error>> {
    let mut socket = env::var_os("SYNVEIL_CORE_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_SOCKET_PATH));
    let mut json_output = false;
    let mut command = None;

    let mut arguments = env::args_os().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.to_string_lossy().as_ref() {
            "--socket" => {
                let value = arguments.next().ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidInput, "--socket requires a path")
                })?;
                socket = PathBuf::from(value);
            }
            "--json" => json_output = true,
            "--version" => {
                println!(
                    "synctl {} {}",
                    veil_protocol::BUILD_VERSION,
                    veil_protocol::BUILD_REVISION.unwrap_or("unknown")
                );
                return Ok(ExitCode::SUCCESS);
            }
            "--help" | "-h" => {
                print_help();
                return Ok(ExitCode::SUCCESS);
            }
            value if command.is_none() => command = Some(value.to_owned()),
            value => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("unexpected argument: {value}"),
                )
                .into());
            }
        }
    }

    let command = command.unwrap_or_else(|| "status".to_owned());
    if command != "status" && command != "observe" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("unknown command: {command}"),
        )
        .into());
    }

    let request = Request::new(
        format!("synctl-{}", std::process::id()),
        &command,
        json!({}),
    );

    let mut stream = UnixStream::connect(&socket)?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(&encode_line(&request)?)?;
    stream.flush()?;

    let line = read_frame(&mut BufReader::new(stream))?
        .ok_or_else(|| io::Error::new(io::ErrorKind::UnexpectedEof, "core returned no response"))?;
    let response = decode_response(&line)?;
    validate_correlation(&response, &request.id)?;
    if command == "observe" && response.ok {
        validate_observe_result(response.result.as_ref().expect("validated response"))?;
    }

    if json_output {
        println!("{}", serde_json::to_string_pretty(&response)?);
    } else if response.ok {
        println!(
            "{}",
            serde_json::to_string_pretty(response.result.as_ref().expect("validated response"))?
        );
    } else {
        let error = response.error.as_ref().expect("validated response");
        eprintln!("synctl: {}: {}", error.code, error.message);
        return Ok(ExitCode::FAILURE);
    }

    Ok(response_exit_code(&response))
}

fn response_exit_code(response: &Response) -> ExitCode {
    if response.ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

fn validate_correlation(response: &Response, id: &str) -> io::Result<()> {
    if response.id != id {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "core response id does not match request",
        ));
    }
    Ok(())
}

fn validate_observe_result(result: &Value) -> io::Result<()> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidData, "invalid observe response");
    if result.get("schema").and_then(Value::as_str) != Some("synveil.observe/v1") {
        return Err(invalid());
    }
    let utilization = result
        .get("cpu")
        .and_then(|cpu| cpu.get("utilization"))
        .ok_or_else(invalid)?;
    match utilization.get("status").and_then(Value::as_str) {
        Some("unavailable") => Ok(()),
        Some("ready") => {
            if utilization
                .get("busy_basis_points")
                .and_then(Value::as_u64)
                .filter(|value| *value <= 10_000)
                .is_none()
                || utilization
                    .get("sample_window_ms")
                    .and_then(Value::as_u64)
                    .filter(|value| *value > 0)
                    .is_none()
                || utilization
                    .get("omitted_logical_cpus")
                    .and_then(Value::as_u64)
                    .is_none()
            {
                return Err(invalid());
            }
            let logical = utilization
                .get("logical_cpus")
                .and_then(Value::as_array)
                .filter(|cpus| !cpus.is_empty())
                .ok_or_else(invalid)?;
            let mut previous_id = None;
            let mut ready = 0usize;
            for cpu in logical {
                let id = cpu
                    .get("id")
                    .and_then(Value::as_u64)
                    .filter(|id| *id <= u64::from(u32::MAX))
                    .ok_or_else(invalid)?;
                if previous_id.is_some_and(|previous| id <= previous) {
                    return Err(invalid());
                }
                previous_id = Some(id);
                match cpu.get("status").and_then(Value::as_str) {
                    Some("ready") => {
                        if cpu
                            .get("busy_basis_points")
                            .and_then(Value::as_u64)
                            .filter(|value| *value <= 10_000)
                            .is_none()
                        {
                            return Err(invalid());
                        }
                        ready += 1;
                    }
                    Some("unavailable") if cpu.get("busy_basis_points").is_none() => {}
                    _ => return Err(invalid()),
                }
            }
            if ready == 0 {
                return Err(invalid());
            }
            Ok(())
        }
        _ => Err(invalid()),
    }?;

    let processes = result.get("processes").ok_or_else(invalid)?;
    let records = processes
        .get("records")
        .and_then(Value::as_array)
        .ok_or_else(invalid)?;
    if records.len() > 128 {
        return Err(invalid());
    }
    let unavailable = processes
        .get("unavailable_processes")
        .and_then(Value::as_u64)
        .ok_or_else(invalid)?;
    let omitted = processes
        .get("omitted_processes")
        .and_then(Value::as_u64)
        .ok_or_else(invalid)?;
    let scan_truncated = processes
        .get("scan_truncated")
        .and_then(Value::as_bool)
        .ok_or_else(invalid)?;
    match processes.get("status").and_then(Value::as_str) {
        Some("ready")
            if !records.is_empty() && unavailable == 0 && omitted == 0 && !scan_truncated => {}
        Some("partial")
            if !records.is_empty() && unavailable > 0 && omitted == 0 && !scan_truncated => {}
        Some("truncated") if omitted > 0 || scan_truncated => {}
        Some("unavailable") if records.is_empty() && omitted == 0 && !scan_truncated => {}
        Some("invalid")
            if records.is_empty() && unavailable == 0 && omitted == 0 && !scan_truncated =>
        {
            return Ok(());
        }
        _ => return Err(invalid()),
    }
    let mut previous_pid = None;
    for record in records {
        let pid = record
            .get("pid")
            .and_then(Value::as_u64)
            .filter(|pid| *pid > 0 && *pid <= u64::from(u32::MAX))
            .ok_or_else(invalid)?;
        if previous_pid.is_some_and(|previous| pid <= previous) {
            return Err(invalid());
        }
        previous_pid = Some(pid);
        for field in ["parent_pid", "process_group_id", "session_id"] {
            if record
                .get(field)
                .and_then(Value::as_u64)
                .filter(|value| *value <= u64::from(u32::MAX))
                .is_none()
            {
                return Err(invalid());
            }
        }
        let state = record.get("state").and_then(Value::as_str).filter(|state| {
            state.len() == 1
                && matches!(
                    state.as_bytes()[0],
                    b'R' | b'S'
                        | b'D'
                        | b'Z'
                        | b'T'
                        | b't'
                        | b'X'
                        | b'x'
                        | b'K'
                        | b'W'
                        | b'P'
                        | b'I'
                )
        });
        if state.is_none() {
            return Err(invalid());
        }
        for field in [
            "user_ticks",
            "system_ticks",
            "start_time_ticks",
            "virtual_memory_bytes",
            "resident_pages",
        ] {
            if record.get(field).and_then(Value::as_u64).is_none() {
                return Err(invalid());
            }
        }
        if record
            .get("thread_count")
            .and_then(Value::as_u64)
            .filter(|threads| *threads > 0)
            .is_none()
            || record
                .get("nice")
                .and_then(Value::as_i64)
                .filter(|nice| (-20..=19).contains(nice))
                .is_none()
        {
            return Err(invalid());
        }
    }
    Ok(())
}

fn print_help() {
    println!(
        "Usage: synctl [--socket PATH] [--json] [status|observe]\n\
         \n\
         Commands:\n\
           status    Query veil-core deterministic service status\n\
           observe   Read bounded kernel resource and process counters"
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use veil_protocol::{ErrorCode, ProtocolError};

    #[test]
    fn validates_response_correlation() {
        let response = Response::success("expected", json!({}));
        assert!(validate_correlation(&response, "expected").is_ok());
        assert_eq!(
            validate_correlation(&response, "other").unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn error_response_retains_failure_with_json_output() {
        let response = Response::failure(
            "expected",
            ProtocolError::new(ErrorCode::UnknownMethod, "unknown"),
        );
        assert!(response_exit_code(&response) == ExitCode::FAILURE);
        assert!(validate_correlation(&response, "expected").is_ok());
    }

    #[test]
    fn observe_validation_requires_sorted_per_cpu_measurements() {
        let processes = json!({
            "status": "ready",
            "records": [{
                "pid": 1, "parent_pid": 0, "process_group_id": 1,
                "session_id": 1, "state": "S", "user_ticks": 1,
                "system_ticks": 2, "start_time_ticks": 3,
                "virtual_memory_bytes": 4096, "resident_pages": 1,
                "thread_count": 1, "nice": 0
            }],
            "unavailable_processes": 0,
            "omitted_processes": 0,
            "scan_truncated": false
        });
        let valid = json!({
            "schema": "synveil.observe/v1",
            "cpu": {"utilization": {
                "status": "ready",
                "busy_basis_points": 5000,
                "sample_window_ms": 101,
                "logical_cpus": [
                    {"id": 0, "status": "ready", "busy_basis_points": 7500},
                    {"id": 2, "status": "unavailable"}
                ],
                "omitted_logical_cpus": 0
            }},
            "processes": processes.clone()
        });
        assert!(validate_observe_result(&valid).is_ok());

        for invalid in [
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101, "logical_cpus": [],
                "omitted_logical_cpus": 0
            }}, "processes": processes.clone()}),
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101,
                "logical_cpus": [{"id": 0, "status": "ready"}],
                "omitted_logical_cpus": 0
            }}, "processes": processes.clone()}),
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101,
                "logical_cpus": [
                    {"id": 2, "status": "ready", "busy_basis_points": 1},
                    {"id": 1, "status": "ready", "busy_basis_points": 1}
                ],
                "omitted_logical_cpus": 0
            }}, "processes": processes.clone()}),
        ] {
            assert!(validate_observe_result(&invalid).is_err());
        }
    }

    #[test]
    fn observe_validation_requires_sorted_process_identity_and_counters() {
        let valid_record = json!({
            "pid": 7, "parent_pid": 1, "process_group_id": 7,
            "session_id": 7, "state": "R", "user_ticks": 10,
            "system_ticks": 2, "start_time_ticks": 100,
            "virtual_memory_bytes": 8192, "resident_pages": 2,
            "thread_count": 1, "nice": 0
        });
        let response = |processes: Value| {
            json!({
                "schema": "synveil.observe/v1",
                "cpu": {"utilization": {"status": "unavailable"}},
                "processes": processes,
            })
        };
        assert!(
            validate_observe_result(&response(json!({
                "status": "ready", "records": [valid_record.clone()],
            "unavailable_processes": 0, "omitted_processes": 0,
            "scan_truncated": false
            })))
            .is_ok()
        );

        for invalid in [
            json!({"status": "ready", "records": [],
                "unavailable_processes": 0, "omitted_processes": 0,
                "scan_truncated": false}),
            json!({"status": "ready", "records": [valid_record.clone(), valid_record.clone()],
                "unavailable_processes": 0, "omitted_processes": 0,
                "scan_truncated": false}),
            json!({"status": "partial", "records": [valid_record.clone()],
                "unavailable_processes": 0, "omitted_processes": 0,
                "scan_truncated": false}),
            json!({"status": "truncated", "records": [valid_record.clone()],
                "unavailable_processes": 0, "omitted_processes": 0,
                "scan_truncated": false}),
            json!({"status": "ready", "records": [{
                "pid": 7, "parent_pid": 1, "process_group_id": 7,
                "session_id": 7, "state": "?", "user_ticks": 10,
                "system_ticks": 2, "start_time_ticks": 100,
                "virtual_memory_bytes": 8192, "resident_pages": 2,
                "thread_count": 0, "nice": 25
            }], "unavailable_processes": 0, "omitted_processes": 0,
                "scan_truncated": false}),
        ] {
            assert!(validate_observe_result(&response(invalid)).is_err());
        }

        let too_many_records = (1..=129u32)
            .map(|pid| {
                json!({
                    "pid": pid, "parent_pid": 1, "process_group_id": pid,
                    "session_id": pid, "state": "S", "user_ticks": 0,
                    "system_ticks": 0, "start_time_ticks": u64::from(pid),
                    "virtual_memory_bytes": 4096, "resident_pages": 1,
                    "thread_count": 1, "nice": 0
                })
            })
            .collect::<Vec<_>>();
        assert!(
            validate_observe_result(&response(json!({
                "status": "truncated", "records": too_many_records,
                "unavailable_processes": 0, "omitted_processes": 1,
                "scan_truncated": false
            })))
            .is_err()
        );
    }
}
