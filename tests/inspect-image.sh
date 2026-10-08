#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/../build/scripts/common.sh"

IMAGE="${1:-$OUT_DIR/synveil-x86_64.img}"
[[ -f "$IMAGE" ]] || die "image not found: $IMAGE"

require_cmd sgdisk
require_cmd gawk
require_cmd mtype

PARTITION_INFO="$(sgdisk -i 1 "$IMAGE")"
# sgdisk reports the on-disk GUID here, not its EF00 command-line shorthand.
grep -qi '^Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B ' <<<"$PARTITION_INFO" \
    || die "partition 1 is not an EFI System Partition"

FIRST_SECTOR="$(gawk '/First sector:/ {print $3}' <<<"$PARTITION_INFO")"
[[ "$FIRST_SECTOR" =~ ^[0-9]+$ ]] || die "could not determine EFI partition offset"

OFFSET=$((FIRST_SECTOR * 512))
mtype -i "$IMAGE@@$OFFSET" ::/EFI/BOOT/BOOTX64.EFI >/dev/null 2>&1     || die "EFI fallback executable BOOTX64.EFI is missing or unreadable"

log "image structure verified: GPT + EFI System Partition + BOOTX64.EFI"
