#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# VM-only observer of a locked LightDM greeter. No guest actions or secrets.
set -eu
[ -f /etc/synveil/ci-login-gate ] || exit 1
send_marker() {
    if [ -c /dev/ttyS0 ]; then
        # Linux getty can leave an unfinished "synveil login: " prompt.
        # Terminate that line before writing a fixed, machine-readable token.
        printf '\n%s\n' "$1" > /dev/ttyS0
    fi
}
send_marker SYNVEIL_QEMU_LOGIN_GATE_STARTED
i=0
stable=0
while [ "$i" -lt 110 ]; do
    i=$((i + 1))
    status="$(passwd -S synveil 2>/dev/null || true)"
    locked=no
    case "$status" in
        "synveil L "*|"synveil L") locked=yes ;;
    esac
    if pgrep -x lightdm >/dev/null 2>&1 &&
       pgrep -f '/usr/sbin/lightdm-gtk-greeter' >/dev/null 2>&1 &&
       [ "$locked" = yes ] &&
       ! pgrep -x xfce4-session >/dev/null 2>&1 &&
       ! pgrep -x xfce4-panel >/dev/null 2>&1; then
        stable=$((stable + 1))
        if [ "$stable" -ge 4 ]; then
            send_marker SYNVEIL_QEMU_LOGIN_GREETER_LOCKED
            exit 0
        fi
    else
        stable=0
    fi
    sleep 1
done
send_marker SYNVEIL_QEMU_LOGIN_GREETER_FAILED
exit 1
