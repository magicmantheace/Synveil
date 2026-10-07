#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

ensure_dirs
[[ -x "$TOOLCHAIN_DIR/bin/$TARGET-gcc" ]] || die "toolchain missing; run 'bash build.sh toolchain' first"
INITRAMFS="$OUT_DIR/synveil-initramfs.cpio"
[[ -f "$INITRAMFS" ]] || die "initramfs missing; run 'bash build.sh rootfs' first"

python3 "$ROOT_DIR/tools/source_lock.py" fetch linux
# Header installation mutates its source tree; keep the kernel source separate.
LINUX_SRC="$(prepare_source linux linux-kernel)"
KERNEL_BUILD="$WORK_DIR/kernel-build"

rm -rf "$KERNEL_BUILD"
mkdir -p "$KERNEL_BUILD"

log "configuring Linux $(source_version linux)"
make -C "$LINUX_SRC"     O="$KERNEL_BUILD"     ARCH=x86     CROSS_COMPILE="$TARGET-"     x86_64_defconfig

"$LINUX_SRC/scripts/kconfig/merge_config.sh"     -m     -O "$KERNEL_BUILD"     "$KERNEL_BUILD/.config"     "$ROOT_DIR/build/config/kernel.fragment"

"$LINUX_SRC/scripts/config" --file "$KERNEL_BUILD/.config"     --set-str INITRAMFS_SOURCE "$INITRAMFS"     --set-str CMDLINE "console=ttyS0,115200n8 earlycon=uart,io,0x3f8,115200 rdinit=/init panic=-1 loglevel=6"     -e EFI     -e EFI_STUB     -e BLK_DEV_INITRD     -e SERIAL_8250     -e SERIAL_8250_CONSOLE     -e CMDLINE_BOOL     -e CMDLINE_OVERRIDE

make -C "$LINUX_SRC"     O="$KERNEL_BUILD"     ARCH=x86     CROSS_COMPILE="$TARGET-"     olddefconfig

log "building Linux EFI-stub kernel"
make -C "$LINUX_SRC"     O="$KERNEL_BUILD"     ARCH=x86     CROSS_COMPILE="$TARGET-"     -j"$JOBS"     bzImage

mkdir -p "$OUT_DIR/manifests"
cp "$KERNEL_BUILD/arch/x86/boot/bzImage" "$OUT_DIR/synveil-x86_64.efi"
cp "$KERNEL_BUILD/.config" "$OUT_DIR/manifests/kernel.config"
if [[ -f "$KERNEL_BUILD/System.map" ]]; then
    cp "$KERNEL_BUILD/System.map" "$OUT_DIR/manifests/System.map"
fi

log "EFI-stub kernel ready: $OUT_DIR/synveil-x86_64.efi"
