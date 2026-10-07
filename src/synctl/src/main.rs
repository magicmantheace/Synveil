// SPDX-License-Identifier: MPL-2.0

use serde_json::json;
use std::env;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::process::ExitCode;
use veil_protocol::{MAX_MESSAGE_BYTES, Request, decode_response, encode_line};

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
    stream.write_all(&encode_line(&request)?)?;
    stream.flush()?;

    let mut line = Vec::new();
    BufReader::new(stream)
        .take((MAX_MESSAGE_BYTES + 2) as u64)
        .read_until(b'\n', &mut line)?;

    if line.len() > MAX_MESSAGE_BYTES + 1 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "core response exceeded protocol size limit",
        )
        .into());
    }
    if line.last() == Some(&b'\n') {
        line.pop();
    }

    let response = decode_response(&line)?;

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

    Ok(ExitCode::SUCCESS)
}

fn print_help() {
    println!(
        "Usage: synctl [--socket PATH] [--json] [status]\n\
         \n\
         Commands:\n\
           status    Query veil-core deterministic service status"
    );
}
