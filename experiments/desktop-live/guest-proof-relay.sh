#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# CI-only: emit an exact, allowlisted diagnostic to a QEMU serial port.
# No input is executed, and no shell or privilege is offered to the session.
set -eu
STATUS=/tmp/synveil-desktop-proof.status
i=0
while [ "$i" -lt 150 ]; do
    if [ -f "$STATUS" ] && [ ! -L "$STATUS" ]; then
        IFS= read -r answer < "$STATUS" || answer=''
        case "$answer" in
            SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS|SYNVEIL_QEMU_DESKTOP_PROBE_FAILED)
                if [ -c /dev/ttyS0 ]; then
                    printf '%s\n' "$answer" > /dev/ttyS0
                fi
                exit 0
                ;;
        esac
    fi
    i=$((i + 1))
    sleep 2
done
exit 1
