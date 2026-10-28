#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

ensure_dirs
KERNEL_EFI="$OUT_DIR/synveil-x86_64.efi"
IMAGE="$OUT_DIR/synveil-x86_64.img"
SIZE_MB="${SYNVEIL_ESP_SIZE_MB:-128}"
# Opt-in: append the designated state partition (ADR 0017) after the ESP,
# sized in whole MiB, so the image carries its own persistent state. The
# default (0) keeps the historical single-partition layout byte-for-byte.
STATE_SIZE_MB="${SYNVEIL_STATE_SIZE_MB:-0}"
[[ "$STATE_SIZE_MB" =~ ^[0-9]+$ ]] || die "SYNVEIL_STATE_SIZE_MB must be a whole number of MiB"
if (( STATE_SIZE_MB > 0 )); then
    (( STATE_SIZE_MB >= 64 && STATE_SIZE_MB <= 16384 )) || \
        die "SYNVEIL_STATE_SIZE_MB must be between 64 and 16384 MiB"
    require_cmd mkfs.ext4
fi
STATE_PARTUUID="7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053"

[[ -f "$KERNEL_EFI" ]] || die "kernel EFI image missing; run 'bash build.sh kernel' first"
require_cmd truncate
require_cmd dd
require_cmd sgdisk
require_cmd mkfs.vfat
require_cmd mmd
require_cmd mcopy

rm -f "$IMAGE"
truncate -s "$((SIZE_MB + STATE_SIZE_MB))M" "$IMAGE"

# Create a real GPT disk with an EFI System Partition without requiring root
# privileges or loop devices.
if (( STATE_SIZE_MB > 0 )); then
    sgdisk --clear \
        --new=1:2048:+"$((SIZE_MB - 2))"M --typecode=1:EF00 --change-name=1:"Synveil EFI" \
        --new=2:0:+"${STATE_SIZE_MB}"M --typecode=2:8300 --change-name=2:"Synveil State" \
        --partition-guid=2:"$STATE_PARTUUID" \
        "$IMAGE" >/dev/null
else
    sgdisk --clear     --new=1:2048:0     --typecode=1:EF00     --change-name=1:"Synveil EFI"     "$IMAGE" >/dev/null
fi

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

if (( STATE_SIZE_MB > 0 )); then
    STATE_FIRST_SECTOR="$(sgdisk -i 2 "$IMAGE" | gawk '/First sector:/ {print $3}')"
    [[ "$STATE_FIRST_SECTOR" =~ ^[0-9]+$ ]] || die "could not determine state partition offset"
    # A fresh, empty state filesystem. mount-state creates the private
    # directory skeleton on first boot; nothing is pre-enrolled.
    mkfs.ext4 -F -q -L SYNVEIL_STATE -b 4096 \
        -E "offset=$((STATE_FIRST_SECTOR * 512))" "$IMAGE" \
        "$((STATE_SIZE_MB * 256))" >/dev/null
fi

bash "$ROOT_DIR/tests/inspect-image.sh" "$IMAGE"
if (( STATE_SIZE_MB > 0 )); then
    log "UEFI GPT image ready: $IMAGE (with ${STATE_SIZE_MB} MiB state partition)"
else
    log "UEFI GPT image ready: $IMAGE"
fi
