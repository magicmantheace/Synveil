#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/../build/scripts/common.sh"

LOG_DIR="$OUT_DIR/logs"
LOG_FILE="$LOG_DIR/qemu-smoke.log"
TIMEOUT_SECONDS="${SYNVEIL_SMOKE_TIMEOUT:-30}"
REQUIRE_NATIVE="${SYNVEIL_SMOKE_REQUIRE_NATIVE:-0}"
case "$REQUIRE_NATIVE" in
    0) ;;
    1) LOG_FILE="$LOG_DIR/qemu-native-smoke.log" ;;
    *) die "SYNVEIL_SMOKE_REQUIRE_NATIVE must be 0 or 1" ;;
esac

mkdir -p "$LOG_DIR"
rm -f "$LOG_FILE"

log "booting Synveil in QEMU for up to ${TIMEOUT_SECONDS}s"
set +e
timeout "${TIMEOUT_SECONDS}s" bash "$ROOT_DIR/build/scripts/qemu.sh" >"$LOG_FILE" 2>&1
status=$?
set -e

if grep -Eq $'^SYNVEIL_BOOT_OK\r?$' "$LOG_FILE"; then
    if [[ "$REQUIRE_NATIVE" == 1 ]]; then
        if ! grep -Eq $'^SYNVEIL_CORE_READY\r?$' "$LOG_FILE" ||
           grep -Eq $'^SYNVEIL_CORE_UNAVAILABLE\r?$' "$LOG_FILE"; then
            log "native core did not complete a successful status check"
            tail -n 120 "$LOG_FILE" >&2 || true
            exit 1
        fi
        log "QEMU native core status smoke passed"
        exit 0
    fi
    log "QEMU smoke boot passed"
    exit 0
fi

printf '[synveil] QEMU smoke boot failed (qemu/timeout status %s)\n' "$status" >&2
tail -n 120 "$LOG_FILE" >&2 || true
exit 1
