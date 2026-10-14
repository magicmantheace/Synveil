// SPDX-License-Identifier: MPL-2.0
use std::ffi::OsStr;
use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(OsStr::new("check")) {
        eprintln!("usage: veil-audit check [--path JOURNAL]");
        return ExitCode::FAILURE;
    }
    let path = match args.next() {
        None => PathBuf::from("/var/log/synveil/core-audit.jsonl"),
        Some(flag) if flag == "--path" => match args.next() {
            Some(path) => PathBuf::from(path),
            None => return ExitCode::FAILURE,
        },
        Some(_) => return ExitCode::FAILURE,
    };
    if args.next().is_some() {
        return ExitCode::FAILURE;
    }
    match veil_core::audit_check::inspect(&path) {
        Ok(report) => {
            println!("{report}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            // Do not echo the supplied path or journal content on failure.
            eprintln!("veil-audit: inspection refused ({:?})", error.kind());
            ExitCode::FAILURE
        }
    }
}
