#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Guarded writer for Synveil TEST media (owner first-boot test).
#
# Writes a checksummed Synveil test image to ONE removable USB disk
# after verifying: the image digest equals the digest the operator
# supplies from the committed validation record, the target is a
# whole removable/USB disk (never a partition, never an internal
# disk), and no mounted filesystem or swap area is backed by the
# target. "Backed by" is decided by tracing every mount and swap
# source through its sysfs backing-device relationships — partitions
# resolve to their parent disk, device-mapper devices (LUKS, LVM)
# recurse through their slaves, md devices through their components,
# and loop devices through the filesystem hosting their backing
# file. A device-name prefix is never trusted on its own: a mapper
# volume named /dev/mapper/anything can sit on the selected disk.
# If any backing relationship cannot be resolved, the writer fails
# closed and refuses. The operator must confirm the exact target
# device name after being shown its model, serial and size. After
# writing, the image bytes are read back and their digest compared.
#
# This is a test-media writer, not an installer: it copies a
# prebuilt image byte for byte and creates no partitions itself.
#
# Fixture mode (tests/write-test-media.py): when
# SYNVEIL_TESTMEDIA_FIXTURE=1, SYNVEIL_TESTMEDIA_SYSFS and
# SYNVEIL_TESTMEDIA_PROC point at disposable fixture trees and every
# check runs against them. Fixture mode NEVER prompts and NEVER
# writes — it exits right after the checks with
# SYNVEIL_TEST_MEDIA_CHECKS_PASSED or a refusal. Outside fixture
# mode those variables are ignored and the live /sys and /proc are
# always used, so the checks cannot be redirected by the environment
# on a real run.
set -Eeuo pipefail

usage() {
    cat >&2 <<'EOF'
usage: write-test-media.sh --image IMAGE --device /dev/WHOLE_DISK \
       --expected-sha256 HEX64

  --image           regular file (not a symlink) with the test image
  --device          whole-disk device, e.g. /dev/sdb (no partitions)
  --expected-sha256 SHA-256 of the image from the validation record
EOF
    exit 2
}

IMAGE='' DEVICE='' EXPECTED=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image) IMAGE=${2:-}; shift 2 ;;
        --device) DEVICE=${2:-}; shift 2 ;;
        --expected-sha256) EXPECTED=${2:-}; shift 2 ;;
        *) usage ;;
    esac
done
[[ -n "$IMAGE" && -n "$DEVICE" && -n "$EXPECTED" ]] || usage
[[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: --expected-sha256 must be 64 lowercase hex characters" >&2
    exit 2
}

FIXTURE=0
SYSFS=/sys/class/block
MOUNTS_FILE=/proc/mounts
SWAPS_FILE=/proc/swaps
if [[ "${SYNVEIL_TESTMEDIA_FIXTURE:-0}" == 1 ]]; then
    FIXTURE=1
    SYSFS=${SYNVEIL_TESTMEDIA_SYSFS:?fixture mode requires SYNVEIL_TESTMEDIA_SYSFS}
    FIXTURE_PROC=${SYNVEIL_TESTMEDIA_PROC:?fixture mode requires SYNVEIL_TESTMEDIA_PROC}
    MOUNTS_FILE=$FIXTURE_PROC/mounts
    SWAPS_FILE=$FIXTURE_PROC/swaps
fi

if [[ $FIXTURE -eq 0 && $EUID -ne 0 ]]; then
    echo "ERROR: run as root (sudo) — raw device writes require it" >&2
    exit 2
fi
for cmd in sha256sum dd stat readlink awk; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "ERROR: required command not found: $cmd" >&2; exit 2; }
done
[[ -r "$MOUNTS_FILE" && -r "$SWAPS_FILE" ]] || {
    echo "ERROR: cannot read $MOUNTS_FILE / $SWAPS_FILE" >&2; exit 2; }

# --- backing-device resolution --------------------------------------
# All functions print whole-disk basenames, one per line, and return
# nonzero when a relationship cannot be resolved (uncertain). The
# caller treats uncertainty as grounds for refusal, never as absence
# of use.

# backing_disks NAME — whole disks ultimately backing block device
# NAME (a /sys/class/block basename).
backing_disks() {
    local name=$1 dir entry slave comp parent resolved
    dir="$SYSFS/$name"
    [[ -d "$dir" ]] || return 1
    if [[ -d "$dir/dm" ]]; then
        # Device-mapper (LUKS, LVM, multipath…): backed by its slaves.
        local found=0
        for entry in "$dir"/slaves/*; do
            [[ -e "$entry" ]] || continue
            found=1
            backing_disks "$(basename "$entry")" || return 1
        done
        [[ $found -eq 1 ]] || return 1
        return 0
    fi
    if [[ -d "$dir/md" ]]; then
        # Software RAID: components are listed as md/dev-<name>.
        local found=0
        for entry in "$dir"/slaves/*; do
            [[ -e "$entry" ]] || continue
            found=1
            backing_disks "$(basename "$entry")" || return 1
        done
        if [[ $found -eq 0 ]]; then
            for entry in "$dir"/md/dev-*; do
                [[ -e "$entry" ]] || continue
                found=1
                comp=${entry##*/}
                backing_disks "${comp#dev-}" || return 1
            done
        fi
        [[ $found -eq 1 ]] || return 1
        return 0
    fi
    if [[ -f "$dir/partition" ]]; then
        # A partition: its disk is the parent of its sysfs directory.
        # The parent can itself be a stacked device (kpartx on dm),
        # so recurse unless it is a plain whole disk.
        resolved=$(readlink -f "$dir") || return 1
        parent=$(basename "$(dirname "$resolved")")
        [[ -n "$parent" && "$parent" != "$name" ]] || return 1
        [[ -d "$SYSFS/$parent" ]] || return 1
        if [[ -d "$SYSFS/$parent/dm" || -d "$SYSFS/$parent/md" ]]; then
            backing_disks "$parent" || return 1
        else
            printf '%s\n' "$parent"
        fi
        return 0
    fi
    if [[ -f "$dir/loop/backing_file" ]]; then
        # A loop device is backed by a file; the backing chain
        # continues through the filesystem mounted over that file.
        local backing src
        backing=$(cat "$dir/loop/backing_file") || return 1
        [[ -n "$backing" ]] || return 1
        src=$(mount_source_for_path "$backing") || return 1
        device_disks "$src" || return 1
        return 0
    fi
    # A plain whole disk.
    printf '%s\n' "$name"
}

# device_disks PATH — whole disks backing the device at PATH
# (e.g. /dev/sdb2, /dev/mapper/cryptroot, /dev/dm-0).
device_disks() {
    local path=$1 base resolved want d
    if [[ -e "$path" || -L "$path" ]]; then
        resolved=$(readlink -f "$path") || return 1
        base=$(basename "$resolved")
    else
        base=$(basename "$path")
    fi
    if [[ ! -d "$SYSFS/$base" && "$path" == /dev/mapper/* ]]; then
        # The mapper name itself is not a sysfs name; resolve it
        # through the device-mapper name attribute.
        want=${path#/dev/mapper/}
        base=''
        for d in "$SYSFS"/*; do
            [[ -f "$d/dm/name" ]] || continue
            if [[ "$(cat "$d/dm/name")" == "$want" ]]; then
                base=$(basename "$d")
                break
            fi
        done
        [[ -n "$base" ]] || return 1
    fi
    [[ -d "$SYSFS/$base" ]] || return 1
    backing_disks "$base"
}

# mount_source_for_path FILE — the mount source of the longest
# mountpoint in the mounts table that prefixes FILE.
mount_source_for_path() {
    local file=$1 src mp best='' bestlen=-1
    while read -r src mp _; do
        mp=${mp//\\040/ }
        case "$file" in
            "$mp"/*|"$mp")
                if (( ${#mp} > bestlen )); then
                    best=$src bestlen=${#mp}
                fi
                ;;
        esac
    done < "$MOUNTS_FILE"
    [[ -n "$best" ]] || return 1
    printf '%s\n' "$best"
}

# --- image identity -------------------------------------------------
[[ -f "$IMAGE" && ! -L "$IMAGE" ]] || {
    echo "ERROR: image must be a regular, non-symlink file: $IMAGE" >&2
    exit 1
}
IMAGE_SIZE=$(stat -c '%s' "$IMAGE")
IMAGE_SHA=$(sha256sum "$IMAGE" | awk '{print $1}')
if [[ "$IMAGE_SHA" != "$EXPECTED" ]]; then
    echo "ERROR: image digest mismatch — refusing to write." >&2
    echo "  expected (validation record): $EXPECTED" >&2
    echo "  actual file:                  $IMAGE_SHA" >&2
    exit 1
fi
echo "Image:  $IMAGE"
echo "  size:   $IMAGE_SIZE bytes"
echo "  sha256: $IMAGE_SHA  (matches the validation record)"

# --- device identity ------------------------------------------------
[[ "$DEVICE" =~ ^/dev/(sd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)$ ]] || {
    echo "ERROR: device must be a whole disk (no partition suffix): $DEVICE" >&2
    exit 1
}
BASE=${DEVICE#/dev/}
SYS=$SYSFS/$BASE
[[ -d "$SYS" ]] || { echo "ERROR: no sysfs entry for $BASE" >&2; exit 1; }
if [[ $FIXTURE -eq 0 ]]; then
    [[ -b "$DEVICE" ]] || {
        echo "ERROR: not a block device: $DEVICE" >&2; exit 1
    }
fi

REMOVABLE=$(cat "$SYS/removable" 2>/dev/null || echo '?')
DEVPATH=$(readlink -f "$SYS/device" 2>/dev/null || echo '')
TRANSPORT=$(cat "$SYS/device/transport" 2>/dev/null || echo '')
if [[ "$REMOVABLE" != 1 && "$DEVPATH" != *'/usb'* && "$TRANSPORT" != *usb* ]]; then
    echo "ERROR: $DEVICE is not removable and not USB-attached — refusing." >&2
    echo "  removable=$REMOVABLE transport=$TRANSPORT path=$DEVPATH" >&2
    exit 1
fi

SIZE_SECTORS=$(cat "$SYS/size" 2>/dev/null || echo '')
[[ "$SIZE_SECTORS" =~ ^[0-9]+$ ]] || {
    echo "ERROR: cannot determine the size of $DEVICE — refusing." >&2
    exit 1
}
DEV_SIZE=$((SIZE_SECTORS * 512))
[[ "$DEV_SIZE" -ge "$IMAGE_SIZE" ]] || {
    echo "ERROR: device ($DEV_SIZE bytes) is smaller than the image" \
         "($IMAGE_SIZE bytes)" >&2; exit 1
}

# --- in-use check: trace every mount and swap source to its disks ---
# Any /dev source whose backing chain cannot be fully resolved makes
# the whole check fail closed.
declare -A BACKED_BY=()
UNCERTAIN=''
SOURCES=()
while read -r src _; do SOURCES+=("$src"); done < <(awk '{print $1}' "$MOUNTS_FILE")
while read -r src _; do
    [[ "$src" == Filename ]] || SOURCES+=("$src")
done < "$SWAPS_FILE"
for src in "${SOURCES[@]}"; do
    case "$src" in /dev/*) ;; *) continue ;; esac
    if ! disks=$(device_disks "$src"); then
        UNCERTAIN=$src
        break
    fi
    for d in $disks; do BACKED_BY[$d]=1; done
done
if [[ -n "$UNCERTAIN" ]]; then
    echo "ERROR: cannot determine the backing devices of $UNCERTAIN" \
         "— refusing (fail closed)." >&2
    exit 1
fi
if [[ -n "${BACKED_BY[$BASE]:-}" ]]; then
    echo "ERROR: $DEVICE (or a partition, mapper volume or array on" \
         "it) is in use by the running system as a mounted filesystem" \
         "or swap — refusing." >&2
    exit 1
fi

MODEL=$(cat "$SYS/device/model" 2>/dev/null || echo '(unknown)')
VENDOR=$(cat "$SYS/device/vendor" 2>/dev/null || echo '(unknown)')
SERIAL=$(cat "$SYS/device/serial" 2>/dev/null || echo '(unknown)')
echo
echo "Target device: $DEVICE"
echo "  model:  $VENDOR $MODEL"
echo "  serial: $SERIAL"
echo "  size:   $DEV_SIZE bytes (image uses $IMAGE_SIZE)"
echo "  removable=$REMOVABLE transport=${TRANSPORT:-n/a}"
echo

if [[ $FIXTURE -eq 1 ]]; then
    # Fixture mode ends here BY CONSTRUCTION: the confirmation prompt
    # and the write below are unreachable in fixture mode.
    echo "SYNVEIL_TEST_MEDIA_CHECKS_PASSED device=$DEVICE sha256=$IMAGE_SHA"
    exit 0
fi

echo "ALL DATA ON $DEVICE WILL BE DESTROYED."
printf 'Type the device name (%s) to confirm: ' "$BASE"
read -r CONFIRM
[[ "$CONFIRM" == "$BASE" ]] || {
    echo "Confirmation did not match — nothing written." >&2; exit 1
}

# --- write + verify ---------------------------------------------------
echo "Writing image…"
dd if="$IMAGE" of="$DEVICE" bs=4M conv=fsync status=progress
sync
echo "Reading back $IMAGE_SIZE bytes for verification…"
READBACK_SHA=$(dd if="$DEVICE" iflag=count_bytes count="$IMAGE_SIZE" \
    bs=4M status=none | sha256sum | awk '{print $1}')
if [[ "$READBACK_SHA" != "$EXPECTED" ]]; then
    echo "ERROR: read-back digest mismatch — media is NOT trustworthy." >&2
    echo "  expected: $EXPECTED" >&2
    echo "  readback: $READBACK_SHA" >&2
    exit 1
fi
sync
echo
echo "SYNVEIL_TEST_MEDIA_WRITTEN device=$DEVICE sha256=$READBACK_SHA"
echo "The media is verified. Eject it before unplugging."
