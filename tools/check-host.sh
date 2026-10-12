#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Local replacement for the native-core-host workflow while Actions is offline.
set -eu
cd "$(dirname "$0")/.."
if [ ! -f Cargo.lock ] || [ ! -f Cargo.toml ]; then
    echo "Run from a complete Synveil checkout" >&2
    exit 2
fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
for test in \
    native-preflight native-build native-rootfs native-startup \
    seat-supervisor native-smoke native-recovery qemu-lock \
    native-protocol-probe native-guest-fixture graphical-login-fixture
do
    echo "==> tests/$test.py"
    python3 "tests/$test.py"
done
if [ "${SYNVEIL_SKIP_RUST_CHECKS:-0}" = 1 ]; then
    echo "Rust checks skipped explicitly (SYNVEIL_SKIP_RUST_CHECKS=1)"
    exit 0
fi
command -v cargo >/dev/null || { echo "cargo is required" >&2; exit 2; }
command -v rustc >/dev/null || { echo "rustc is required" >&2; exit 2; }
echo "==> cargo fmt --all --check"
cargo fmt --all --check
echo "==> cargo test --workspace --all-targets --locked"
cargo test --workspace --all-targets --locked
echo "==> cargo clippy --workspace --all-targets --locked -- -D warnings"
cargo clippy --workspace --all-targets --locked -- -D warnings
echo "SYNVEIL_OFFLINE_HOST_CHECKS_PASS"
