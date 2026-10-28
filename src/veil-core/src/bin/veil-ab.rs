// SPDX-License-Identifier: MPL-2.0
//! Guest A/B update agent and early-init accounting hook
//! (docs/AB_UPDATER.md). Root-only. Three modes:
//!
//!   veil-ab boot-hook   init hook: provisioning + trial attempt
//!                       accounting BEFORE services start; reboots
//!                       immediately when a trial is exhausted.
//!   veil-ab serve       update agent daemon: health assessment
//!                       for a booted trial, then the typed-action
//!                       socket (synveil.ab-agent/v1).
//!   veil-ab request <status|stage|confirm|rollback|reboot>
//!                       session-side client over that socket.
//!
//! On systems without /etc/synveil/ab-system every mode is inert:
//! the shipped single-image system is behaviorally unchanged.

use std::process::ExitCode;
use veil_core::ab;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("boot-hook") if args.len() == 1 => boot_hook(),
        Some("serve") if args.len() == 1 => serve(),
        Some("request") if args.len() == 2 => request(&args[1]),
        _ => {
            eprintln!(
                "usage: veil-ab <boot-hook|serve|request <status|stage|confirm|rollback|reboot>>"
            );
            ExitCode::FAILURE
        }
    }
}

fn boot_hook() -> ExitCode {
    // Trial exhaustion, hand-over and fail-closed holds are all
    // resolved inside the hook itself (chain-load or an in-place
    // recovery hold); returning here means boot may proceed.
    match ab::boot_hook() {
        Ok(_) => ExitCode::SUCCESS,
        Err(error) => {
            // Fail closed but do not wedge the machine: the
            // confirmed slot continues to boot, updates stay inert,
            // and the failure is on the console log.
            eprintln!("SYNVEIL_AB_HOOK_ERROR error={error}");
            ExitCode::SUCCESS
        }
    }
}

fn serve() -> ExitCode {
    match ab::serve() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("SYNVEIL_AB_AGENT_ERROR error={error}");
            ExitCode::FAILURE
        }
    }
}

fn request(action: &str) -> ExitCode {
    match action {
        "status" | "stage" | "confirm" | "rollback" | "reboot" => {}
        _ => {
            eprintln!("usage: veil-ab request <status|stage|confirm|rollback|reboot>");
            return ExitCode::FAILURE;
        }
    }
    match ab::request(action) {
        Ok(response) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&response).unwrap_or_else(|_| "{}".into())
            );
            if response.get("ok").and_then(serde_json::Value::as_bool) == Some(true) {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(error) => {
            eprintln!("veil-ab: agent request failed: {error}");
            ExitCode::FAILURE
        }
    }
}
