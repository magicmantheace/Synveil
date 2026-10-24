#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# VM-only proof run from XFCE session autostart as the live, non-root user.
# Never used to authorize login or installed-system changes.
set -eu
STATUS=/tmp/synveil-desktop-proof.status
umask 077
printf 'SYNVEIL_QEMU_DESKTOP_PROBE_STARTED\n' > "$STATUS"
trap 'printf "SYNVEIL_QEMU_DESKTOP_PROBE_FAILED\\n" > "$STATUS"' EXIT HUP INT TERM

# A working session, desktop window manager, and X11 display are mandatory.
[ -n "${DISPLAY:-}" ] || exit 1
[ "$(id -u)" -ne 0 ] || exit 1
attempt=0
until wmctrl -m 2>/dev/null | grep -qi 'Name:.*Xfwm4'; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 40 ] || exit 1
    sleep 2
done

# Exercise two distinct real graphical applications, not a fake status marker.
xfce4-terminal --disable-server --title SynveilTerminalProof >/dev/null 2>&1 &
thunar "$HOME" >/dev/null 2>&1 &
attempt=0
terminal=''
files=''
while [ "$attempt" -lt 45 ]; do
    windows="$(wmctrl -lx 2>/dev/null || true)"
    terminal="$(printf '%s\n' "$windows" | awk 'tolower($0) ~ /xfce4-terminal/ {print $1; exit}')"
    files="$(printf '%s\n' "$windows" | awk 'tolower($0) ~ /thunar/ {print $1; exit}')"
    [ -n "$terminal" ] && [ -n "$files" ] && break
    attempt=$((attempt + 1))
    sleep 2
done
[ -n "$terminal" ] && [ -n "$files" ] || exit 1
[ "$terminal" != "$files" ] || exit 1

# Verify a managed client's geometry changes after real Xfwm4 operations.
before="$(xwininfo -id "$terminal" 2>/dev/null | grep 'Absolute upper-left X:' | awk '{print $NF}')"
[ -n "$before" ] || exit 1
wmctrl -ir "$terminal" -e '0,160,120,660,420'
sleep 2
after="$(xwininfo -id "$terminal" 2>/dev/null | grep 'Absolute upper-left X:' | awk '{print $NF}')"
[ -n "$after" ] && [ "$before" != "$after" ] || exit 1

wmctrl -ir "$terminal" -b add,maximized_vert,maximized_horz
sleep 2
xprop -id "$terminal" _NET_WM_STATE | grep -q '_NET_WM_STATE_MAXIMIZED_HORZ' || exit 1
wmctrl -ir "$terminal" -b remove,maximized_vert,maximized_horz
sleep 2
xdotool windowminimize "$terminal"
sleep 2
xprop -id "$terminal" _NET_WM_STATE | grep -q '_NET_WM_STATE_HIDDEN' || exit 1
wmctrl -ia "$terminal"
sleep 2
if xprop -id "$terminal" _NET_WM_STATE | grep -q '_NET_WM_STATE_HIDDEN'; then
    exit 1
fi
# Both managed windows must still exist after minimize/restore.
xwininfo -id "$terminal" >/dev/null 2>&1
xwininfo -id "$files" >/dev/null 2>&1

# The everyday desktop must have a functioning panel and genuine Settings
# window, not just two manually launched applications.
panel_wait=0
until pgrep -u "$(id -u)" -x xfce4-panel >/dev/null 2>&1; do
    panel_wait=$((panel_wait + 1))
    [ "$panel_wait" -lt 25 ] || exit 1
    sleep 2
done
xfce4-settings-manager >/dev/null 2>&1 &
settings=''
settings_wait=0
while [ "$settings_wait" -lt 40 ]; do
    windows="$(wmctrl -lx 2>/dev/null || true)"
    settings="$(printf '%s\n' "$windows" | awk 'tolower($0) ~ /xfce4-settings-manager/ {print $1; exit}')"
    [ -n "$settings" ] && break
    settings_wait=$((settings_wait + 1))
    sleep 2
done
[ -n "$settings" ] && [ "$settings" != "$terminal" ] && [ "$settings" != "$files" ] || exit 1
wmctrl -ia "$settings"
xwininfo -id "$settings" >/dev/null 2>&1
# The original windows must remain present while Settings is opened.
xwininfo -id "$terminal" >/dev/null 2>&1
xwininfo -id "$files" >/dev/null 2>&1
# The named, opt-in VM-only filesystem uses Debian live-boot's /home bind.
# No candidate root, account secret, EFI or system partition is modified.
# This marker is advisory *guest test evidence*, not authenticated storage.
HOME_STATUS=/tmp/synveil-desktop-home-proof.status
if mountpoint -q /home; then
    probe="$HOME/.synveil-vm-home-proof"
    if [ -L "$probe" ]; then
        printf 'SYNVEIL_QEMU_HOME_PROOF_FAILED\n' > "$HOME_STATUS"
        exit 1
    elif [ -e "$probe" ]; then
        [ -f "$probe" ] && [ "$(stat -c '%u' "$probe")" = "$(id -u)" ] || {
            printf 'SYNVEIL_QEMU_HOME_PROOF_FAILED\n' > "$HOME_STATUS"
            exit 1
        }
        [ "$(cat "$probe")" = 'synveil-vm-only-persistence-v1' ] || {
            printf 'SYNVEIL_QEMU_HOME_PROOF_FAILED\n' > "$HOME_STATUS"
            exit 1
        }
        printf 'SYNVEIL_QEMU_HOME_RECOVERED\n' > "$HOME_STATUS"
    else
        ( set -C; printf '%s\n' 'synveil-vm-only-persistence-v1' > "$probe" ) || {
            printf 'SYNVEIL_QEMU_HOME_PROOF_FAILED\n' > "$HOME_STATUS"
            exit 1
        }
        chmod 0600 "$probe"
        sync "$probe"
        printf 'SYNVEIL_QEMU_HOME_WRITTEN\n' > "$HOME_STATUS"
    fi
fi
trap - EXIT HUP INT TERM
printf 'SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n' > "$STATUS"
