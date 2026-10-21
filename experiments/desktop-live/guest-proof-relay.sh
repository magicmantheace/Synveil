#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# CI-only: emit an exact, allowlisted diagnostic to a QEMU serial port.
# No input is executed, and no shell or privilege is offered to the session.
set -eu
STATUS=/tmp/synveil-desktop-proof.status
send_marker() {
    if [ -c /dev/ttyS0 ]; then
        printf '%s\n' "$1" > /dev/ttyS0
    fi
}
# Separately indicate that userspace/systemd started this fixed service.
# Neither this nor the SESSION_STARTED marker is a desktop pass.
send_marker SYNVEIL_QEMU_RELAY_STARTED
session_started=0
i=0
while [ "$i" -lt 150 ]; do
    if [ -f "$STATUS" ] && [ ! -L "$STATUS" ]; then
        IFS= read -r answer < "$STATUS" || answer=''
        case "$answer" in
            SYNVEIL_QEMU_DESKTOP_PROBE_STARTED)
                if [ "$session_started" -eq 0 ]; then
                    send_marker SYNVEIL_QEMU_DESKTOP_PROBE_STARTED
                    session_started=1
                fi
                ;;
            SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS|SYNVEIL_QEMU_DESKTOP_PROBE_FAILED)
                # Home-state proof is a separate exact marker and must never
                # substitute for the real desktop/widow-management proof.
                home_status=/tmp/synveil-desktop-home-proof.status
                if [ -f "$home_status" ] && [ ! -L "$home_status" ]; then
                    IFS= read -r home_result < "$home_status" || home_result=''
                    case "$home_result" in
                        SYNVEIL_QEMU_HOME_WRITTEN|SYNVEIL_QEMU_HOME_RECOVERED|SYNVEIL_QEMU_HOME_PROOF_FAILED)
                            send_marker "$home_result"
                            ;;
                    esac
                fi
                send_marker "$answer"
                exit 0
                ;;
        esac
    fi
    i=$((i + 1))
    sleep 2
done
exit 1
