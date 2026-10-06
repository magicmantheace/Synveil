#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

IMAGE="$OUT_DIR/synveil-x86_64.img"
[[ -f "$IMAGE" ]] || die "image missing; run 'bash build.sh image' first"
require_cmd qemu-system-x86_64

QEMU_ARGS=(
    -machine q35
    -m "${SYNVEIL_QEMU_MEMORY:-512M}"
    -smp "${SYNVEIL_QEMU_CPUS:-2}"
    -display none
    -serial stdio
    -monitor none
    -no-reboot
    -net none
    -drive "file=$IMAGE,format=raw,if=virtio,readonly=on"
)

if [[ -r /dev/kvm && "${SYNVEIL_QEMU_TCG:-0}" != 1 ]]; then
    QEMU_ARGS+=(-accel kvm)
else
    QEMU_ARGS+=(-accel tcg)
fi

CODE="${SYNVEIL_OVMF_CODE:-}"
VARS="${SYNVEIL_OVMF_VARS:-}"

if [[ -z "$CODE" ]]; then
    for candidate in         /usr/share/OVMF/OVMF_CODE.fd         /usr/share/OVMF/OVMF_CODE_4M.fd         /usr/share/edk2/ovmf/OVMF_CODE.fd         /usr/share/edk2/x64/OVMF_CODE.fd
    do
        if [[ -f "$candidate" ]]; then
            CODE="$candidate"
            break
        fi
    done
fi

if [[ -z "$VARS" ]]; then
    for candidate in         /usr/share/OVMF/OVMF_VARS.fd         /usr/share/OVMF/OVMF_VARS_4M.fd         /usr/share/edk2/ovmf/OVMF_VARS.fd         /usr/share/edk2/x64/OVMF_VARS.fd
    do
        if [[ -f "$candidate" ]]; then
            VARS="$candidate"
            break
        fi
    done
fi

cleanup_vars=""
if [[ -n "$CODE" && -f "$CODE" ]]; then
    QEMU_ARGS+=(-drive "if=pflash,format=raw,unit=0,readonly=on,file=$CODE")
    if [[ -n "$VARS" && -f "$VARS" ]]; then
        cleanup_vars="$(mktemp "${TMPDIR:-/tmp}/synveil-ovmf-vars.XXXXXX.fd")"
        cp "$VARS" "$cleanup_vars"
        trap 'rm -f "$cleanup_vars"' EXIT
        QEMU_ARGS+=(-drive "if=pflash,format=raw,unit=1,file=$cleanup_vars")
    fi
elif [[ -f /usr/share/ovmf/OVMF.fd ]]; then
    QEMU_ARGS+=(-bios /usr/share/ovmf/OVMF.fd)
else
    die "OVMF firmware not found; set SYNVEIL_OVMF_CODE and optionally SYNVEIL_OVMF_VARS"
fi

exec qemu-system-x86_64 "${QEMU_ARGS[@]}"
