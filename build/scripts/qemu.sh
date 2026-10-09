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
    -drive "if=none,id=synveil_disk,file=$IMAGE,format=raw,readonly=on"
    -device "virtio-blk-pci,drive=synveil_disk,bootindex=1"
)

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

set +e
qemu-system-x86_64 "${QEMU_ARGS[@]}" 9>&-
status=$?
set -e
exit "$status"
