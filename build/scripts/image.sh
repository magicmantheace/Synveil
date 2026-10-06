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
require_cmd dd
require_cmd sgdisk
require_cmd mkfs.vfat
require_cmd mmd
require_cmd mcopy

rm -f "$IMAGE"
truncate -s "${SIZE_MB}M" "$IMAGE"

# Create a real GPT disk with an EFI System Partition without requiring root
# privileges or loop devices.
sgdisk --clear     --new=1:2048:0     --typecode=1:EF00     --change-name=1:"Synveil EFI"     "$IMAGE" >/dev/null

FIRST_SECTOR="$(sgdisk -i 1 "$IMAGE" | gawk '/First sector:/ {print $3}')"
LAST_SECTOR="$(sgdisk -i 1 "$IMAGE" | gawk '/Last sector:/ {print $3}')"
[[ "$FIRST_SECTOR" =~ ^[0-9]+$ && "$LAST_SECTOR" =~ ^[0-9]+$ ]]     || die "could not determine EFI partition bounds"

ESP_SECTORS=$((LAST_SECTOR - FIRST_SECTOR + 1))
ESP_TMP="$(mktemp "${TMPDIR:-/tmp}/synveil-esp.XXXXXX.img")"
trap 'rm -f "$ESP_TMP"' EXIT

truncate -s "$((ESP_SECTORS * 512))" "$ESP_TMP"
mkfs.vfat -F 32 -n SYNVEIL "$ESP_TMP" >/dev/null
mmd -i "$ESP_TMP" ::/EFI
mmd -i "$ESP_TMP" ::/EFI/BOOT
mcopy -i "$ESP_TMP" "$KERNEL_EFI" ::/EFI/BOOT/BOOTX64.EFI

dd if="$ESP_TMP" of="$IMAGE" bs=512 seek="$FIRST_SECTOR" conv=notrunc status=none

log "UEFI GPT image ready: $IMAGE"
