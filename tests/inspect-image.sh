#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/../build/scripts/common.sh"

IMAGE="${1:-$OUT_DIR/synveil-x86_64.img}"
[[ -f "$IMAGE" ]] || die "image not found: $IMAGE"

require_cmd sgdisk
require_cmd gawk
require_cmd mdir

PARTITION_INFO="$(sgdisk -i 1 "$IMAGE")"
grep -q 'Partition GUID code:.*EF00' <<<"$PARTITION_INFO"     || die "partition 1 is not an EFI System Partition"

FIRST_SECTOR="$(gawk '/First sector:/ {print $3}' <<<"$PARTITION_INFO")"
[[ "$FIRST_SECTOR" =~ ^[0-9]+$ ]] || die "could not determine EFI partition offset"

OFFSET=$((FIRST_SECTOR * 512))
LISTING="$(mdir -i "$IMAGE@@$OFFSET" ::/EFI/BOOT 2>&1)" || {
    printf '%s\n' "$LISTING" >&2
    die "could not read EFI/BOOT from image"
}

grep -qi 'BOOTX64[.]EFI' <<<"$LISTING"     || die "EFI fallback executable BOOTX64.EFI is missing"

log "image structure verified: GPT + EFI System Partition + BOOTX64.EFI"
