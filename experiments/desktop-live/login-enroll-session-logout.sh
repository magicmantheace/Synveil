#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Disposable QEMU-only XFCE user-session logout exercise.
# This is NOT a production session manager, login authority, or credential UI.
set -eu
[ -f /etc/synveil/ci-login-enroll ] || exit 1
[ "$(id -u)" -ne 0 ] || exit 1
[ -n "$DISPLAY" ] || exit 1
umask 077
# One logout per independent guest process; do not loop after re-login.
once="$HOME/.synveil-ci-logout-once"
[ ! -L "$once" ] || exit 1
if [ -e "$once" ]; then
    exit 0
fi
( set -C; printf '%s\n' 'ci-only' > "$once" ) || exit 1
# Wait for actual completed window actions, not a scheduled sleep guessed
# to imply the desktop works. The other user test writes the status marker.
n=0
while [ "$n" -lt 130 ]; do
    if [ -f /tmp/synveil-desktop-proof.status ] &&
       [ ! -L /tmp/synveil-desktop-proof.status ] &&
       [ "$(cat /tmp/synveil-desktop-proof.status)" = SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS ]; then
        # Give host QMP an opportunity to inspect the authenticated GUI.
        sleep 5
        exec xfce4-session-logout --logout --fast
    fi
    n=$((n + 1))
    sleep 1
done
exit 1
