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
trap - EXIT HUP INT TERM
printf 'SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n' > "$STATUS"
