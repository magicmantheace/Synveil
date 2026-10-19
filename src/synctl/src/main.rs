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
    }
}

fn print_help() {
    println!(
        "Usage: synctl [--socket PATH] [--json] [status|observe]\n\
         \n\
         Commands:\n\
           status    Query veil-core deterministic service status\n\
           observe   Read kernel CPU, memory, load, disk and network counters"
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
            }}
        });
        assert!(validate_observe_result(&valid).is_ok());

        for invalid in [
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101, "logical_cpus": [],
                "omitted_logical_cpus": 0
            }}}),
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101,
                "logical_cpus": [{"id": 0, "status": "ready"}],
                "omitted_logical_cpus": 0
            }}}),
            json!({"schema": "synveil.observe/v1", "cpu": {"utilization": {
                "status": "ready", "busy_basis_points": 5000,
                "sample_window_ms": 101,
                "logical_cpus": [
                    {"id": 2, "status": "ready", "busy_basis_points": 1},
                    {"id": 1, "status": "ready", "busy_basis_points": 1}
                ],
                "omitted_logical_cpus": 0
            }}}),
        ] {
            assert!(validate_observe_result(&invalid).is_err());
        }
    }
}
