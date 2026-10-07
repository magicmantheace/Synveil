// SPDX-License-Identifier: MPL-2.0

use serde_json::json;
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
    if command != "status" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("unknown command: {command}"),
        )
        .into());
    }

    let request = Request::new(
        format!("synctl-{}", std::process::id()),
        "status",
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

fn print_help() {
    println!(
        "Usage: synctl [--socket PATH] [--json] [status]\n\
         \n\
         Commands:\n\
           status    Query veil-core deterministic service status"
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
}
