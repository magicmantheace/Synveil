#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

ensure_dirs
KERNEL_EFI="$OUT_DIR/synveil-x86_64.efi"
IMAGE="$OUT_DIR/synveil-x86_64.img"
SIZE_MB="${SYNVEIL_ESP_SIZE_MB:-128}"

[[ -f "$KERNEL_EFI" ]] || die "kernel EFI image missing; run 'bash build.sh kernel' first"
require_cmd truncate
require_cmd mkfs.vfat
require_cmd mmd
require_cmd mcopy

rm -f "$IMAGE"
truncate -s "${SIZE_MB}M" "$IMAGE"
mkfs.vfat -F 32 -n SYNVEIL "$IMAGE" >/dev/null

mmd -i "$IMAGE" ::/EFI
mmd -i "$IMAGE" ::/EFI/BOOT
mcopy -i "$IMAGE" "$KERNEL_EFI" ::/EFI/BOOT/BOOTX64.EFI

log "UEFI image ready: $IMAGE"
