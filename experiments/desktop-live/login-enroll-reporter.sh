#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Observational guest proof of PAM-backed LightDM auth with a one-time CI
# password. Does not execute guest commands or accept a login assertion.
set -eu
[ -f /etc/synveil/ci-login-enroll ] || exit 1
send_marker() {
    if [ -c /dev/ttyS0 ]; then
        printf '\n%s\n' "$1" > /dev/ttyS0
    fi
}
send_marker SYNVEIL_QEMU_TEST_AUTH_REPORTER_STARTED
i=0
until pgrep -x lightdm >/dev/null 2>&1 &&
      pgrep -f '/usr/sbin/lightdm-gtk-greeter' >/dev/null 2>&1 &&
      [ "$(passwd -S synveil 2>/dev/null | awk '{print $2}')" = P ] &&
      ! pgrep -x xfce4-session >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 110 ]; then
        send_marker SYNVEIL_QEMU_TEST_AUTH_FAILED
        exit 1
    fi
    sleep 1
done
send_marker SYNVEIL_QEMU_TEST_AUTH_GREETER_READY
i=0
stable=0
while [ "$i" -lt 170 ]; do
    i=$((i + 1))
    uid="$(id -u synveil 2>/dev/null || echo '')"
    if [ -n "$uid" ] && [ "$uid" -ne 0 ] &&
       pgrep -u "$uid" -x xfce4-session >/dev/null 2>&1 &&
       pgrep -u "$uid" -x xfce4-panel >/dev/null 2>&1; then
        stable=$((stable + 1))
        if [ "$stable" -ge 4 ]; then
            send_marker SYNVEIL_QEMU_TEST_AUTH_SESSION_ACTIVE
            exit 0
        fi
    else
        stable=0
    fi
    sleep 1
done
send_marker SYNVEIL_QEMU_TEST_AUTH_FAILED
exit 1
