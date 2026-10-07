// SPDX-License-Identifier: MPL-2.0

use std::env;
use std::io;
use std::path::PathBuf;
use std::process::ExitCode;
use veil_core::{DEFAULT_SOCKET_PATH, serve_path};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!(
                "{}",
                serde_json::json!({
                    "component": "veil-core",
                    "event": "fatal",
                    "error": error.to_string(),
                })
            );
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut socket = env::var_os("SYNVEIL_CORE_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_SOCKET_PATH));
    let mut max_connections = None;

    let mut arguments = env::args_os().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.to_string_lossy().as_ref() {
            "--socket" => {
                let value = arguments.next().ok_or_else(|| {
                    io::Error::new(io::ErrorKind::InvalidInput, "--socket requires a path")
                })?;
                socket = PathBuf::from(value);
            }
            "--once" => max_connections = Some(1),
            "--help" | "-h" => {
                println!("Usage: veil-core [--socket PATH] [--once]");
                return Ok(());
            }
            other => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("unknown argument: {other}"),
                )
                .into());
            }
        }
    }

    eprintln!(
        "{}",
        serde_json::json!({
            "component": "veil-core",
            "event": "start",
            "version": env!("CARGO_PKG_VERSION"),
            "protocol": veil_protocol::CORE_SCHEMA_V1,
        })
    );

    serve_path(&socket, max_connections)?;
    Ok(())
}
