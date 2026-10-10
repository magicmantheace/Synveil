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
            veil_core::audit::emit("fatal", serde_json::json!({ "error": error.to_string() }));
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut socket = env::var_os("SYNVEIL_CORE_SOCKET")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_SOCKET_PATH));
    let mut max_connections = None;
    let mut supervised = false;

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
            "--supervise" => supervised = true,
            "--version" => {
                println!(
                    "veil-core {} {}",
                    veil_protocol::BUILD_VERSION,
                    veil_protocol::BUILD_REVISION.unwrap_or("unknown")
                );
                return Ok(());
            }
            "--help" | "-h" => {
                println!("Usage: veil-core [--socket PATH] [--once | --supervise] [--version]");
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

    veil_core::audit::emit(
        "start",
        serde_json::json!({
            "version": veil_protocol::BUILD_VERSION,
            "git_revision": veil_protocol::BUILD_REVISION,
            "protocol": veil_protocol::CORE_SCHEMA_V1,
        }),
    );

    if supervised {
        if max_connections.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "--once cannot be combined with --supervise",
            )
            .into());
        }
        veil_core::supervision::supervise(&socket)?;
    } else {
        serve_path(&socket, max_connections)?;
    }
    Ok(())
}
