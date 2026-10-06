#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/../build/scripts/common.sh"

LOG_DIR="$OUT_DIR/logs"
LOG_FILE="$LOG_DIR/qemu-smoke.log"
TIMEOUT_SECONDS="${SYNVEIL_SMOKE_TIMEOUT:-30}"

mkdir -p "$LOG_DIR"
rm -f "$LOG_FILE"

log "booting Synveil in QEMU for up to ${TIMEOUT_SECONDS}s"
set +e
timeout "${TIMEOUT_SECONDS}s" bash "$ROOT_DIR/build/scripts/qemu.sh" >"$LOG_FILE" 2>&1
status=$?
set -e

if grep -q 'SYNVEIL_BOOT_OK' "$LOG_FILE"; then
    log "QEMU smoke boot passed"
    exit 0
fi

printf '[synveil] QEMU smoke boot failed (qemu/timeout status %s)\n' "$status" >&2
tail -n 120 "$LOG_FILE" >&2 || true
exit 1
