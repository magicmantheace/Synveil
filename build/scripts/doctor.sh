#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

commands=(
    bash python3 make gcc g++ ld ar as
    tar xz bzip2 sed awk grep patch perl
    bison flex makeinfo
    find sort cpio zstd
    truncate mkfs.vfat mcopy mmd
    qemu-system-x86_64 timeout
)

missing=0
for command in "${commands[@]}"; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf '[doctor] missing: %s\n' "$command" >&2
        missing=1
    fi
done

if [[ "$missing" -ne 0 ]]; then
    cat >&2 <<'EOF'

Install the missing host build dependencies and run the doctor again.
See docs/BUILDING.md for Debian/Ubuntu and Fedora package examples.
EOF
    exit 1
fi

ovmf_found=0
for candidate in     "${SYNVEIL_OVMF_CODE:-}"     /usr/share/OVMF/OVMF_CODE.fd     /usr/share/OVMF/OVMF_CODE_4M.fd     /usr/share/edk2/ovmf/OVMF_CODE.fd     /usr/share/edk2/x64/OVMF_CODE.fd     /usr/share/ovmf/OVMF.fd
do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
        printf '[doctor] OVMF: %s\n' "$candidate"
        ovmf_found=1
        break
    fi
done

if [[ "$ovmf_found" -eq 0 ]]; then
    printf '[doctor] missing: OVMF UEFI firmware (or set SYNVEIL_OVMF_CODE)\n' >&2
    exit 1
fi

cat <<EOF
[doctor] host prerequisites look usable
[doctor] target: $TARGET
[doctor] jobs: $JOBS
[doctor] toolchain: $TOOLCHAIN_DIR
[doctor] sysroot: $SYSROOT
EOF
