// SPDX-License-Identifier: MPL-2.0
//! Local text-mode authenticated maintenance (ADR 0010 tier 2).
//!
//! Runs only when the kernel command line carries
//! `synveil.recovery=maint` (see build/rootfs/init): after the state
//! mount, before core, seat, network or desktop start. The boot entry
//! is the ESP's `EFI/synveil/maintenance.efi`, an owner firmware-menu
//! pick, exactly like the tier-1 recovery entry.
//!
//! Authentication reuses the separately audited ADR 0009 verifier
//! (`credential::verify_root_credential`) directly. It does NOT go
//! through the graphical seat broker, and it never starts one. The
//! credential store's coarse state (no state / absent / invalid /
//! present) is reported with fixed markers; a wrong password against
//! a present store is an ordinary denial.
//!
//! After authentication the operator gets a fixed set of enumerated
//! commands, matched exactly, one per line:
//!
//!   status   bounded A/B status (read-only)
//!   restore  restore the known-good generation via veil-ab's
//!            rollback machinery (typed action, verified postcondition)
//!   reboot   reboot the machine
//!   exit     end the session; init then locks the console
//!
//! Input is never interpreted as shell text, never passed to a shell,
//! and no other program is executed except /bin/stty with fixed
//! arguments to switch console echo off while the password is read.
//! Bounds: 3 password attempts, 16 commands per session, 1024-byte
//! password lines, 64-byte command lines. Every authentication
//! outcome and action is appended to an audit log on the state
//! volume; passwords and credential material are never logged.

use std::fs::OpenOptions;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::process::{Command, ExitCode};
use std::time::{SystemTime, UNIX_EPOCH};
use veil_core::ab;
use veil_core::credential::{
    LOCAL_ACCOUNT, RootCredentialState, root_credential_state, verify_root_credential,
};
use veil_core::secret;

const MAX_ATTEMPTS: u32 = 3;
const MAX_COMMANDS: u32 = 16;
const MAX_PASSWORD_LINE: usize = 1024;
const MAX_COMMAND_LINE: usize = 64;
const AUDIT_LOG_PATH: &str = "/var/log/synveil/recovery-maintenance.log";

fn audit(event: &str, fields: &str) {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Some(parent) = std::path::Path::new(AUDIT_LOG_PATH).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(AUDIT_LOG_PATH)
    {
        let _ = writeln!(file, "t={secs} event={event} {fields}");
        let _ = file.sync_all();
    }
}

/// Switch console echo off/on with a fixed-program, fixed-argument
/// stty call. stty operates on its standard input, which init has
/// already attached to /dev/console; the operator's input is never
/// part of the arguments. (A positional device operand is not
/// accepted by the rootfs stty and must not be passed.)
fn set_echo(on: bool) -> bool {
    let setting = if on { "echo" } else { "-echo" };
    Command::new("/bin/stty")
        .args([setting])
        .stdin(std::process::Stdio::inherit())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

enum Line {
    Text(Vec<u8>),
    TooLong,
    Eof,
}

/// Read one line byte-wise with a hard cap, so an unbounded input
/// stream can never grow memory. A too-long line is drained to its
/// newline and reported as `TooLong`.
fn read_line(reader: &mut BufReader<io::Stdin>, max: usize) -> Line {
    let mut buf: Vec<u8> = Vec::new();
    let mut too_long = false;
    loop {
        let available = match reader.fill_buf() {
            Ok(chunk) => chunk,
            Err(_) => return Line::Eof,
        };
        if available.is_empty() {
            if buf.is_empty() && !too_long {
                return Line::Eof;
            }
            break;
        }
        let byte = available[0];
        reader.consume(1);
        if byte == b'\n' {
            break;
        }
        if buf.len() < max {
            buf.push(byte);
        } else {
            too_long = true;
        }
    }
    if too_long {
        return Line::TooLong;
    }
    if buf.last() == Some(&b'\r') {
        buf.pop();
    }
    Line::Text(buf)
}

fn out(text: &str) {
    let stdout = io::stdout();
    let mut lock = stdout.lock();
    let _ = writeln!(lock, "{text}");
    let _ = lock.flush();
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if !args.is_empty() {
        eprintln!("usage: veil-recovery (no arguments; kernel cmdline selects this mode)");
        return ExitCode::FAILURE;
    }
    out("SYNVEIL_RECOVERY_MAINT_V1");
    audit("session-start", "");

    // Store-state denials happen before any prompt: there is nothing
    // a password could authenticate against.
    match root_credential_state() {
        RootCredentialState::NoState => {
            out("SYNVEIL_RECOVERY_MAINT_DENIED reason=no-state");
            audit("auth-denied", "reason=no-state");
            return ExitCode::FAILURE;
        }
        RootCredentialState::Absent => {
            out("SYNVEIL_RECOVERY_MAINT_DENIED reason=no-credential");
            audit("auth-denied", "reason=no-credential");
            return ExitCode::FAILURE;
        }
        RootCredentialState::Invalid => {
            out("SYNVEIL_RECOVERY_MAINT_DENIED reason=corrupt-credential");
            audit("auth-denied", "reason=corrupt-credential");
            return ExitCode::FAILURE;
        }
        RootCredentialState::Present => {}
    }

    if !set_echo(false) {
        out("SYNVEIL_RECOVERY_MAINT_UNAVAILABLE");
        audit("unavailable", "reason=echo-control");
        return ExitCode::FAILURE;
    }
    let mut reader = BufReader::new(io::stdin());
    let mut authenticated = false;
    for attempt in 1..=MAX_ATTEMPTS {
        out("SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT");
        match read_line(&mut reader, MAX_PASSWORD_LINE) {
            Line::Text(mut password) => {
                let ok = verify_root_credential(LOCAL_ACCOUNT, &password);
                secret::clear(&mut password);
                if ok {
                    authenticated = true;
                    break;
                }
                out(&format!("SYNVEIL_RECOVERY_MAINT_DENIED attempt={attempt}"));
                audit("auth-denied", &format!("attempt={attempt}"));
            }
            Line::TooLong => {
                out(&format!("SYNVEIL_RECOVERY_MAINT_DENIED attempt={attempt}"));
                audit("auth-denied", &format!("attempt={attempt} reason=oversize"));
            }
            Line::Eof => break,
        }
    }
    let _ = set_echo(true);
    if !authenticated {
        audit("session-end", "result=denied");
        return ExitCode::FAILURE;
    }
    out("SYNVEIL_RECOVERY_MAINT_AUTH_OK");
    audit("auth-ok", "");
    out("SYNVEIL_RECOVERY_MAINT_READY");

    for _ in 0..MAX_COMMANDS {
        match read_line(&mut reader, MAX_COMMAND_LINE) {
            Line::Text(line) => {
                let Ok(command) = std::str::from_utf8(&line) else {
                    out("SYNVEIL_RECOVERY_MAINT_REJECTED");
                    audit("command-rejected", "reason=non-utf8");
                    continue;
                };
                match command {
                    "status" => action_status(),
                    "restore" => action_restore(),
                    "reboot" => {
                        out("SYNVEIL_RECOVERY_MAINT_REBOOT");
                        audit("action", "name=reboot result=initiated");
                        io::stdout().flush().ok();
                        ab::reboot_now();
                    }
                    "exit" => {
                        out("SYNVEIL_RECOVERY_MAINT_DONE");
                        audit("session-end", "result=exit");
                        return ExitCode::SUCCESS;
                    }
                    _ => {
                        out("SYNVEIL_RECOVERY_MAINT_REJECTED");
                        audit("command-rejected", "reason=unknown");
                    }
                }
            }
            Line::TooLong => {
                out("SYNVEIL_RECOVERY_MAINT_REJECTED");
                audit("command-rejected", "reason=oversize");
            }
            Line::Eof => {
                audit("session-end", "result=eof");
                return ExitCode::SUCCESS;
            }
        }
    }
    out("SYNVEIL_RECOVERY_MAINT_DONE");
    audit("session-end", "result=command-limit");
    ExitCode::SUCCESS
}

/// Bounded read-only status: the fixed state lines plus the A/B
/// agent's own status value (control record contents are size-capped
/// by the A/B schema: history is capped at 32 entries).
fn action_status() {
    out("SYNVEIL_RECOVERY_MAINT_STATUS_BEGIN");
    out("SYNVEIL_RECOVERY_STATE_MOUNTED");
    match ab::status() {
        Ok(value) => {
            let confirmed = value.get("confirmed");
            let slot = confirmed
                .and_then(|c| c.get("slot"))
                .and_then(|v| v.as_str())
                .unwrap_or("none");
            let version = confirmed
                .and_then(|c| c.get("version"))
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            out(&format!(
                "SYNVEIL_RECOVERY_MAINT_AB confirmed_slot={slot} confirmed_version={version}"
            ));
            match value.get("trial") {
                Some(trial) if !trial.is_null() => {
                    let tslot = trial.get("slot").and_then(|v| v.as_str()).unwrap_or("none");
                    let tversion = trial.get("version").and_then(|v| v.as_u64()).unwrap_or(0);
                    out(&format!(
                        "SYNVEIL_RECOVERY_MAINT_AB trial_slot={tslot} trial_version={tversion}"
                    ));
                }
                _ => out("SYNVEIL_RECOVERY_MAINT_AB trial=none"),
            }
            out(&format!(
                "SYNVEIL_RECOVERY_MAINT_AB_JSON {}",
                serde_json::to_string(&value).unwrap_or_else(|_| "{}".into())
            ));
        }
        Err(_) => out("SYNVEIL_RECOVERY_MAINT_AB_UNAVAILABLE"),
    }
    out("SYNVEIL_RECOVERY_MAINT_STATUS_END");
    audit("action", "name=status result=ok");
}

/// Restore the known-good generation. Preconditions and the mutation
/// itself are veil-ab's typed rollback action: a pending trial is
/// discarded, otherwise the retained generation in the other slot is
/// digest-verified against the control record and armed as a fresh
/// trial. Postcondition checked here: the reloaded control record
/// must show exactly the state the action reported.
fn action_restore() {
    match ab::rollback() {
        Ok(result) => {
            let verified = verify_restore_postcondition(&result);
            match (result.get("reason").and_then(|v| v.as_str()), verified) {
                (Some("pending-trial-discarded"), true) => {
                    out("SYNVEIL_RECOVERY_MAINT_RESTORE_DISCARDED");
                    audit("action", "name=restore result=trial-discarded");
                }
                (_, true) => {
                    let slot = result
                        .get("selected_slot")
                        .and_then(|v| v.as_str())
                        .unwrap_or("none");
                    let version = result.get("version").and_then(|v| v.as_u64()).unwrap_or(0);
                    out(&format!(
                        "SYNVEIL_RECOVERY_MAINT_RESTORE_OK slot={slot} version={version}"
                    ));
                    audit(
                        "action",
                        &format!("name=restore result=armed slot={slot} version={version}"),
                    );
                }
                _ => {
                    out("SYNVEIL_RECOVERY_MAINT_RESTORE_REFUSED");
                    audit("action", "name=restore result=postcondition-failed");
                }
            }
        }
        Err(_) => {
            out("SYNVEIL_RECOVERY_MAINT_RESTORE_REFUSED");
            audit("action", "name=restore result=refused");
        }
    }
}

fn verify_restore_postcondition(result: &serde_json::Value) -> bool {
    let Ok(layout) = ab::Layout::load() else {
        return false;
    };
    let Ok(control) = ab::load_control(&layout) else {
        return false;
    };
    match result.get("reason").and_then(|v| v.as_str()) {
        Some("pending-trial-discarded") => control.trial.is_none(),
        _ => {
            let Some(trial) = control.trial.as_ref() else {
                return false;
            };
            let generation = trial.generation();
            Some(generation.slot.as_str()) == result.get("selected_slot").and_then(|v| v.as_str())
                && Some(generation.version) == result.get("version").and_then(|v| v.as_u64())
                && trial.attempts == ab::TRIAL_ATTEMPTS
        }
    }
}
