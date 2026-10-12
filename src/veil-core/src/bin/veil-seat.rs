// SPDX-License-Identifier: MPL-2.0
//! Experimental root-owned graphical seat broker; no production UI yet.
use std::io::{self, IsTerminal};
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
        [flag] if flag == "--login-stdin" => {
            if io::stdin().is_terminal() {
                eprintln!("[synveil-seat] refusing TTY password input");
                return ExitCode::FAILURE;
            }
            return match veil_core::seat::login_from_reader(
                io::stdin(),
                Path::new(veil_core::seat::DEFAULT_SEAT_SOCKET),
            ) {
                Ok(true) => ExitCode::SUCCESS,
                Ok(false) | Err(_) => {
                    eprintln!("[synveil-seat] login denied");
                    ExitCode::FAILURE
                }
            };
        }
        [flag] if flag == "--help" => {
            println!("Usage: veil-seat [--socket PATH] | --login-stdin");
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
