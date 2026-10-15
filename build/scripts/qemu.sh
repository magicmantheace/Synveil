#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

IMAGE="$OUT_DIR/synveil-x86_64.img"
[[ -f "$IMAGE" ]] || die "image missing; run 'bash build.sh image' first"
require_cmd qemu-system-x86_64
DISPLAY_BACKEND="${SYNVEIL_QEMU_DISPLAY:-none}"
case "$DISPLAY_BACKEND" in
    none|gtk|sdl) ;;
    *) die "SYNVEIL_QEMU_DISPLAY must be none, gtk, or sdl" ;;
esac
VIDEO="${SYNVEIL_QEMU_VIDEO:-0}"
[[ "$VIDEO" == 0 || "$VIDEO" == 1 ]] || die "SYNVEIL_QEMU_VIDEO must be 0 or 1"
NETWORK="${SYNVEIL_QEMU_NETWORK:-0}"
[[ "$NETWORK" == 0 || "$NETWORK" == 1 ]] || die "SYNVEIL_QEMU_NETWORK must be 0 or 1"
# Developer test automation only: a Unix QMP monitor is powerful and must
# be reachable solely inside a directory owned by this user, mode 0700.
QMP_SOCKET="${SYNVEIL_QEMU_QMP_SOCKET:-}"
if [[ -n "$QMP_SOCKET" ]]; then
    [[ "$QMP_SOCKET" == /* && ${#QMP_SOCKET} -le 100 ]] ||
        die "QMP socket must be an absolute short path"
    qmp_parent="$(dirname "$QMP_SOCKET")"
    [[ -d "$qmp_parent" && -O "$qmp_parent" &&
       "$(stat -c '%a' "$qmp_parent")" == 700 ]] ||
        die "QMP socket parent must be a private owner-owned mode 0700 directory"
    [[ ! -e "$QMP_SOCKET" && ! -L "$QMP_SOCKET" ]] ||
        die "refusing existing QMP socket path"
    umask 077
fi
STATE_DISK="${SYNVEIL_QEMU_STATE_DISK:-}"
if [[ -n "$STATE_DISK" ]]; then
    [[ -f "$STATE_DISK" && ! -L "$STATE_DISK" ]] || die "state disk must be a regular file: $STATE_DISK"
fi

QEMU_MACHINE=q35
if [[ "$DISPLAY_BACKEND" != none || "$VIDEO" == 1 ]]; then
    # Q35 otherwise also supplies a PS/2 keyboard. The guest's first evdev
    # keyboard can differ from QMP's USB target, silently losing all input.
    # Keep the graphical test target on its one explicitly configured keyboard.
    QEMU_MACHINE=q35,i8042=off,vmport=off
fi
QEMU_ARGS=(
    -machine "$QEMU_MACHINE"
    -m "${SYNVEIL_QEMU_MEMORY:-512M}"
    -smp "${SYNVEIL_QEMU_CPUS:-2}"
    -display "$DISPLAY_BACKEND"
    -serial stdio
    -monitor none
    -no-reboot
    -drive "if=none,id=synveil_disk,file=$IMAGE,format=raw,readonly=on"
    -device "virtio-blk-pci,drive=synveil_disk,bootindex=1"
)

if [[ -n "$QMP_SOCKET" ]]; then
    QEMU_ARGS+=(-qmp "unix:$QMP_SOCKET,server=on,wait=off")
fi

# Explicit opt-in graphics/input and user-mode network for the VM desktop target.
# Serial-console CI remains headless, offline and recovery-capable by default.
if [[ "$DISPLAY_BACKEND" != none || "$VIDEO" == 1 ]]; then
    QEMU_ARGS+=(
        -device virtio-vga
        -device qemu-xhci
        -device usb-kbd
        -device usb-tablet
    )
fi
if [[ "$NETWORK" == 1 ]]; then
    QEMU_ARGS+=(-netdev user,id=synveil_net -device virtio-net-pci,netdev=synveil_net)
else
    QEMU_ARGS+=(-net none)
fi

# State is a second, separately writable disk; EFI remains read-only.
if [[ -n "$STATE_DISK" ]]; then
    QEMU_ARGS+=(
        -drive "if=none,id=synveil_state,file=$STATE_DISK,format=raw,readonly=off"
        -device "virtio-blk-pci,drive=synveil_state"
    )
fi

if [[ -r /dev/kvm && "${SYNVEIL_QEMU_TCG:-0}" != 1 ]]; then
    QEMU_ARGS+=(-accel kvm)
else
    QEMU_ARGS+=(-accel tcg)
fi

CODE="${SYNVEIL_OVMF_CODE:-}"
VARS="${SYNVEIL_OVMF_VARS:-}"
COMBINED=""

if [[ -n "$CODE" ]]; then
    [[ -f "$CODE" ]] || die "SYNVEIL_OVMF_CODE does not exist: $CODE"
    if [[ -z "$VARS" ]]; then
        inferred="${CODE/CODE/VARS}"
        [[ -f "$inferred" ]] && VARS="$inferred"
    fi
else
    firmware_pairs=(
        "/usr/share/OVMF/OVMF_CODE_4M.fd|/usr/share/OVMF/OVMF_VARS_4M.fd"
        "/usr/share/OVMF/OVMF_CODE.fd|/usr/share/OVMF/OVMF_VARS.fd"
        "/usr/share/edk2/ovmf/OVMF_CODE.fd|/usr/share/edk2/ovmf/OVMF_VARS.fd"
        "/usr/share/edk2/x64/OVMF_CODE.fd|/usr/share/edk2/x64/OVMF_VARS.fd"
    )
    for pair in "${firmware_pairs[@]}"; do
        candidate_code="${pair%%|*}"
        candidate_vars="${pair#*|}"
        if [[ -f "$candidate_code" && -f "$candidate_vars" ]]; then
            CODE="$candidate_code"
            VARS="$candidate_vars"
            break
        fi
    done

    if [[ -z "$CODE" && -f /usr/share/ovmf/OVMF.fd ]]; then
        COMBINED=/usr/share/ovmf/OVMF.fd
    fi
fi

cleanup_vars=""
if [[ -n "$COMBINED" ]]; then
    QEMU_ARGS+=(-bios "$COMBINED")
elif [[ -n "$CODE" ]]; then
    [[ -n "$VARS" && -f "$VARS" ]]         || die "matching OVMF variable store not found; set SYNVEIL_OVMF_VARS"
    cleanup_vars="$(mktemp "${TMPDIR:-/tmp}/synveil-ovmf-vars.XXXXXX.fd")"
    cp "$VARS" "$cleanup_vars"
    trap 'rm -f "$cleanup_vars"' EXIT
    QEMU_ARGS+=(
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$CODE"
        -drive "if=pflash,format=raw,unit=1,file=$cleanup_vars"
    )
else
    die "OVMF firmware not found; set SYNVEIL_OVMF_CODE and SYNVEIL_OVMF_VARS"
fi

# Keep the wrapper alive until the emulator exits.  A caller that waits on
# this script must be able to reuse its writable state disk immediately;
# exiting while a QEMU child still owns its image lock breaks two-boot tests.
qemu_pid=""
stop_qemu() {
    trap - TERM INT
    if [[ -n "$qemu_pid" ]]; then
        kill -TERM "$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
    fi
}
trap 'stop_qemu; exit 143' TERM
trap 'stop_qemu; exit 130' INT

set +e
# Bash redirects a background command's stdin from /dev/null unless the
# redirection is explicit.  Preserve serial stdio for QEMU guest automation.
qemu-system-x86_64 "${QEMU_ARGS[@]}" <&0 9>&- &
qemu_pid=$!
wait "$qemu_pid"
status=$?
set -e
trap - TERM INT
exit "$status"
