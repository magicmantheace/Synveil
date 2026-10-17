// SPDX-License-Identifier: MPL-2.0
use serde_json::Value;
use std::ffi::OsString;
use std::io;
use std::path::PathBuf;
use std::process::ExitCode;

fn invalid_args() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "invalid arguments")
}

fn run() -> io::Result<Value> {
    let mut args = std::env::args_os().skip(1);
    let command = args.next().ok_or_else(invalid_args)?;
    let mut path = None;
    let mut output = None;
    let mut snapshot = None;
    let mut authorization = None;
    let mut digest: Option<OsString> = None;
    while let Some(flag) = args.next() {
        let value = args.next().ok_or_else(invalid_args)?;
        match flag.to_str() {
            Some("--path") if path.is_none() => path = Some(PathBuf::from(value)),
            Some("--output") if output.is_none() => output = Some(PathBuf::from(value)),
            Some("--digest") if digest.is_none() => digest = Some(value),
            Some("--snapshot") if snapshot.is_none() => snapshot = Some(PathBuf::from(value)),
            Some("--authorization") if authorization.is_none() => {
                authorization = Some(PathBuf::from(value))
            }
            _ => return Err(invalid_args()),
        }
    }
    if (command == "release-archive" || command == "inspect-release") && output.is_none() {
        let operation = if command == "release-archive" {
            veil_core::audit_retention::release_archive
        } else {
            veil_core::audit_retention::inspect_release
        };
        return operation(
            &path.ok_or_else(invalid_args)?,
            &snapshot.ok_or_else(invalid_args)?,
            &authorization.ok_or_else(invalid_args)?,
            digest
                .as_ref()
                .and_then(|value| value.to_str())
                .ok_or_else(invalid_args)?,
        );
    }
    if snapshot.is_some() || authorization.is_some() {
        return Err(invalid_args());
    }
    match command.to_str() {
        Some("check") if output.is_none() && digest.is_none() => veil_core::audit_check::inspect(
            &path.unwrap_or_else(|| PathBuf::from("/var/log/synveil/core-audit.jsonl")),
        ),
        Some("export") if digest.is_none() => veil_core::audit_export::export(
            &path.ok_or_else(invalid_args)?,
            &output.ok_or_else(invalid_args)?,
        ),
        Some("verify") if output.is_none() => veil_core::audit_export::verify(
            &path.ok_or_else(invalid_args)?,
            digest
                .as_ref()
                .and_then(|value| value.to_str())
                .ok_or_else(invalid_args)?,
        ),
        _ => Err(invalid_args()),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(report) => {
            println!("{report}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            // Never echo supplied paths, digests or event content.
            eprintln!("veil-audit: operation refused ({:?})", error.kind());
            ExitCode::FAILURE
        }
    }
}
