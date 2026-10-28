#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Fault-test the actual upstream run_reset function with storage doubles.

This extracts the unchanged function, compiles it, and injects a revocation
write failure after successful password replacement. This is a control-flow
fault test, not a guest boot or an actual power-cut test. The test fails if
the function reports success while its single-use factor remains active.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

PREFIX = r'''
#![allow(dead_code)]
use std::io::{self, BufReader};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
static PASSWORD_CHANGED: AtomicBool = AtomicBool::new(false);
static FACTOR_ACTIVE: AtomicBool = AtomicBool::new(true);
static REVOKE_ATTEMPTED: AtomicBool = AtomicBool::new(false);
static INPUT_INDEX: AtomicUsize = AtomicUsize::new(0);
const MAX_FACTOR_ENTRIES: u32 = 8;
const MAX_ATTEMPTS: u32 = 3;
const MAX_FACTOR_LINE: usize = 128;
const MAX_COMMAND_LINE: usize = 64;
const MAX_PASSWORD_LINE: usize = 1024;
enum Line { Text(Vec<u8>), TooLong, Eof }
fn read_line(_: &mut BufReader<io::Stdin>, _: usize) -> Line {
    Line::Text(match INPUT_INDEX.fetch_add(1, Ordering::SeqCst) {
        0 => b"synthetic-factor".to_vec(),
        1 => b"confirm".to_vec(),
        _ => b"synthetic-new-password".to_vec(),
    })
}
fn out(text: &str) { println!("{text}"); }
fn set_echo(_: bool) -> bool { true }
fn reset_denied(_: &str, _: &str) -> ExitCode { ExitCode::FAILURE }
fn replace_root_credential(_: &[u8]) -> Result<(), ()> {
    PASSWORD_CHANGED.store(true, Ordering::SeqCst); Ok(())
}
mod secret { pub fn clear(bytes: &mut [u8]) { bytes.fill(0); } }
enum RecoveryFactorState { NoState, Absent, Invalid, Revoked { generation: u64 }, Active {} }
enum AnchorState { Present([u8; 32]), Absent, Invalid, NoState }
enum FactorParse { Key([u8; 32]), Transcription, Malformed }
struct Record { installation_id: [u8; 32], failed_attempts: u32, generation: u64 }
mod recovery {
    use super::*;
    pub const MAX_FAILED_ATTEMPTS: u32 = 5;
    pub fn append_audit(_: &str, _: &str) -> Result<(), ()> { Ok(()) }
    pub fn recovery_factor_state() -> RecoveryFactorState { RecoveryFactorState::Active {} }
    pub fn load_active_record() -> Option<Record> {
        Some(Record { installation_id: [0; 32], failed_attempts: 0, generation: 1 })
    }
    pub fn anchor_state() -> AnchorState { AnchorState::Present([0; 32]) }
    pub fn parse_entered_factor(_: &[u8]) -> FactorParse { FactorParse::Key([0; 32]) }
    pub fn verify_factor(_: &[u8; 32], _: &Record, _: &[u8; 32]) -> bool {
        FACTOR_ACTIVE.load(Ordering::SeqCst)
    }
    pub fn record_failed_attempt(_: u64) -> Result<u32, ()> { Ok(1) }
    pub fn now_unix() -> u64 { 1 }
    pub fn revoke_factor(_: u64) -> Result<u64, ()> {
        REVOKE_ATTEMPTED.store(true, Ordering::SeqCst);
        Err(()) // injected storage failure; active record remains unchanged
    }
}
'''

SUFFIX = r'''
fn main() {
    let status = run_reset();
    let changed = PASSWORD_CHANGED.load(Ordering::SeqCst);
    let active = FACTOR_ACTIVE.load(Ordering::SeqCst);
    let attempted = REVOKE_ATTEMPTED.load(Ordering::SeqCst);
    println!("FAULT_RESULT password_changed={changed} revoke_attempted={attempted} factor_active={active} success={}", status == ExitCode::SUCCESS);
    assert!(changed && attempted, "fault injection did not reach the intended path");
    assert!(!(active && status == ExitCode::SUCCESS),
            "reset reported success while the supposedly single-use factor is still active");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_file.read_text()
    start = source.index("fn run_reset() -> ExitCode {")
    end = source.index("\nfn verify_restore_postcondition", start)
    with tempfile.TemporaryDirectory(prefix="synveil-reset-fault-") as temp:
        root = Path(temp)
        test = root / "fault.rs"
        binary = root / "fault"
        test.write_text(PREFIX + source[start:end] + SUFFIX)
        subprocess.run(["rustc", "--edition=2024", str(test), "-o", str(binary)],
                       check=True)
        return subprocess.run([str(binary)], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
