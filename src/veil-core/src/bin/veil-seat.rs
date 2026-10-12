// SPDX-License-Identifier: MPL-2.0
//! Experimental root-owned graphical seat broker; no production UI yet.
use std::path::Path;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let socket = match args.as_slice() {
        [] => veil_core::seat::DEFAULT_SEAT_SOCKET,
        [flag, value] if flag == "--socket" => {
            let Some(path) = value.to_str() else {
                return ExitCode::FAILURE;
            };
            return run(path);
        }
        [flag] if flag == "--help" => {
            println!("Usage: veil-seat [--socket PATH]");
            return ExitCode::SUCCESS;
        }
        _ => return ExitCode::FAILURE,
    };
    run(socket)
}

fn run(path: &str) -> ExitCode {
    match veil_core::seat::serve(Path::new(path)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => {
            eprintln!("[synveil-seat] failed closed");
            ExitCode::FAILURE
        }
    }
}
