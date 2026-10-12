#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Local entrypoint for recorded, commit-attributed checks without Actions.
set -eu
cd "$(dirname "$0")/.."
if [ ! -f Cargo.lock ] || [ ! -f Cargo.toml ]; then
    echo "Run from a complete Synveil checkout" >&2
    exit 2
fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
case "${SYNVEIL_SKIP_RUST_CHECKS:-0}" in
    0) exec python3 tools/offline_checks.py --level host ;;
    1) exec python3 tools/offline_checks.py --level quick ;;
    *) echo "SYNVEIL_SKIP_RUST_CHECKS must be 0 or 1" >&2; exit 2 ;;
esac
