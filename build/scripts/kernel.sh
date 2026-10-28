#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

ensure_dirs
[[ -x "$TOOLCHAIN_DIR/bin/$TARGET-gcc" ]] || die "toolchain missing; run 'bash build.sh toolchain' first"
INITRAMFS="$OUT_DIR/synveil-initramfs.cpio"
[[ -f "$INITRAMFS" ]] || die "initramfs missing; run 'bash build.sh rootfs' first"
# QEMU remains the default, including all existing guest acceptance tests.
# This switch changes only the kernel build, not the rootfs security profile.
BOOT_TARGET="${SYNVEIL_BOOT_TARGET:-qemu}"
case "$BOOT_TARGET" in
    qemu|hardware) ;;
    *) die "SYNVEIL_BOOT_TARGET must be qemu or hardware" ;;
esac

# Guest-only build acceleration: reuse the base kernel's compiled objects,
# not its initramfs or its boot image. All fixture initramfs changes still
# trigger a real kernel relink and a fresh UEFI disk build. An explicit seed
# must be complete; never silently fall back to a slower or unproven image.
SEED_WORK="${SYNVEIL_KERNEL_SEED_WORK:-}"
KERNEL_BUILD="$WORK_DIR/kernel-build"
if [[ -n "$SEED_WORK" ]]; then
    [[ "$SEED_WORK" == /* && ! -L "$SEED_WORK" ]] ||
        die "kernel build seed must be an absolute non-symlink work directory"
    SEED_KERNEL="$SEED_WORK/kernel-build"
    LINUX_SRC="$SEED_WORK/src/linux-kernel"
    [[ -d "$LINUX_SRC" && ! -L "$LINUX_SRC" &&
       -d "$SEED_KERNEL" && ! -L "$SEED_KERNEL" &&
       -f "$SEED_KERNEL/.config" &&
       -f "$SEED_KERNEL/include/config/auto.conf" &&
       -s "$SEED_KERNEL/arch/x86/boot/bzImage" ]] ||
        die "complete base kernel build required before isolated guest fixture"
    # Keep O= stable across fixtures. A different O path changes Kbuild's
    # recorded compiler commands and recompiles most objects unnecessarily.
    # This guest-only cache never shares writable objects with the base build.
    KERNEL_BUILD="$SEED_WORK/guest-fixtures/shared-kernel-build"
    [[ "$(realpath -m "$KERNEL_BUILD")" != "$(realpath -e "$SEED_KERNEL")" ]] ||
        die "guest kernel output must not overwrite base kernel objects"
    # A seed from another revision/config must not contaminate this checkout.
    seed_identity="$(sha256sum "$SEED_KERNEL/.config" "$SEED_KERNEL/arch/x86/boot/bzImage" | sha256sum | cut -d' ' -f1)"
    stamp="$KERNEL_BUILD/.synveil-base-identity"
    if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$seed_identity" ]]; then
        [[ ! -L "$KERNEL_BUILD" ]] || die "refusing symlinked kernel cache"
        rm -rf "$KERNEL_BUILD"
        mkdir -p "$(dirname "$KERNEL_BUILD")"
        # Never hardlink: Kbuild may mutate these objects. Reflinks/copies
        # leave the source-built baseline intact.
        cp -a --reflink=auto -- "$SEED_KERNEL" "$KERNEL_BUILD"
        printf '%s\n' "$seed_identity" > "$stamp"
        log "initialized guest-only shared kernel object cache"
    else
        [[ -d "$KERNEL_BUILD" && ! -L "$KERNEL_BUILD" &&
           -f "$KERNEL_BUILD/.config" &&
           -s "$KERNEL_BUILD/arch/x86/boot/bzImage" ]] ||
            die "invalid shared kernel cache"
        log "reusing guest-only kernel objects for next isolated image"
    fi
else
    python3 "$ROOT_DIR/tools/source_lock.py" fetch linux
    # Header installation mutates its source tree; keep it separate.
    LINUX_SRC="$(prepare_source linux linux-kernel)"
    rm -rf "$KERNEL_BUILD"
    mkdir -p "$KERNEL_BUILD"
    log "configuring Linux $(source_version linux)"
    make -C "$LINUX_SRC" O="$KERNEL_BUILD" ARCH=x86 CROSS_COMPILE="$TARGET-" x86_64_defconfig
fi

KERNEL_FRAGMENTS=("$ROOT_DIR/build/config/kernel.fragment")
if [[ "$BOOT_TARGET" == hardware ]]; then
    KERNEL_FRAGMENTS+=("$ROOT_DIR/build/config/kernel-hardware.fragment")
fi
"$LINUX_SRC/scripts/kconfig/merge_config.sh"     -m     -O "$KERNEL_BUILD"     "$KERNEL_BUILD/.config"     "${KERNEL_FRAGMENTS[@]}"

# Dedicated recovery QEMU fixture only: no arbitrary injected command line.
RECOVERY_STATUS_TEST="${SYNVEIL_KERNEL_TEST_RECOVERY_STATUS:-0}"
[[ "$RECOVERY_STATUS_TEST" == 0 || "$RECOVERY_STATUS_TEST" == 1 ]] ||
    die "SYNVEIL_KERNEL_TEST_RECOVERY_STATUS must be 0 or 1"
RECOVERY_MAINT_TEST="${SYNVEIL_KERNEL_TEST_RECOVERY_MAINT:-0}"
[[ "$RECOVERY_MAINT_TEST" == 0 || "$RECOVERY_MAINT_TEST" == 1 ]] ||
    die "SYNVEIL_KERNEL_TEST_RECOVERY_MAINT must be 0 or 1"
KERNEL_CMDLINE="console=ttyS0,115200n8 earlycon=uart,io,0x3f8,115200 rdinit=/init panic=-1 loglevel=6"
if [[ "$BOOT_TARGET" == hardware ]]; then
    # An HP Pavilion need not expose the QEMU COM1 console. A real virtual
    # console is required to see early boot diagnostics via UEFI GOP.
    KERNEL_CMDLINE="console=tty0 rdinit=/init panic=-1 loglevel=6"
fi
if [[ "$RECOVERY_STATUS_TEST" == 1 ]]; then
    KERNEL_CMDLINE="$KERNEL_CMDLINE synveil.recovery=status"
fi
if [[ "$RECOVERY_MAINT_TEST" == 1 ]]; then
    KERNEL_CMDLINE="$KERNEL_CMDLINE synveil.recovery=maint"
fi

"$LINUX_SRC/scripts/config" --file "$KERNEL_BUILD/.config"     --set-str INITRAMFS_SOURCE "$INITRAMFS"     --set-str CMDLINE "$KERNEL_CMDLINE"     -e EFI     -e EFI_STUB     -e BLK_DEV_INITRD     -e SERIAL_8250     -e SERIAL_8250_CONSOLE     -e CMDLINE_BOOL     -e CMDLINE_OVERRIDE

make -C "$LINUX_SRC"     O="$KERNEL_BUILD"     ARCH=x86     CROSS_COMPILE="$TARGET-"     olddefconfig

# Fail the build instead of silently shipping a GPU driver without the
# /dev/fb0 interface consumed by the bootstrap display and terminal.
required_graphics=(
    DRM VIRTIO_PCI DRM_VIRTIO_GPU DRM_FBDEV_EMULATION
    DRM_CLIENT_DEFAULT_FBDEV FB_DEVICE VT VT_CONSOLE FRAMEBUFFER_CONSOLE
    INPUT_EVDEV USB_HID USB_XHCI_HCD
)
for option in "${required_graphics[@]}"; do
    if ! grep -qx "CONFIG_${option}=y" "$KERNEL_BUILD/.config"; then
        die "kernel configuration dropped required CONFIG_${option}=y"
    fi
done
if [[ "$BOOT_TARGET" == hardware ]]; then
    # Check the resolved Kconfig after olddefconfig, never only the requested
    # fragment: silent dependency drops would leave a black-screen image.
    required_hardware=(
        DRM_SIMPLEDRM SYSFB_SIMPLEFB USB_STORAGE USB_UAS
        BLK_DEV_NVME SATA_AHCI
    )
    for option in "${required_hardware[@]}"; do
        grep -qx "CONFIG_${option}=y" "$KERNEL_BUILD/.config" ||
            die "hardware kernel configuration dropped CONFIG_${option}=y"
    done
fi

log "building Linux EFI-stub kernel"
make -C "$LINUX_SRC"     O="$KERNEL_BUILD"     ARCH=x86     CROSS_COMPILE="$TARGET-"     -j"$JOBS"     bzImage

mkdir -p "$OUT_DIR/manifests"
cp "$KERNEL_BUILD/arch/x86/boot/bzImage" "$OUT_DIR/synveil-x86_64.efi"
cp "$KERNEL_BUILD/.config" "$OUT_DIR/manifests/kernel.config"
# Record what was *actually* built, independent of the caller's later env.
# Otherwise a hardware-target image could be packaged as the QEMU baseline.
printf '%s\n' "$BOOT_TARGET" > "$OUT_DIR/manifests/kernel-boot-target.txt"
if [[ -f "$KERNEL_BUILD/System.map" ]]; then
    cp "$KERNEL_BUILD/System.map" "$OUT_DIR/manifests/System.map"
fi

log "EFI-stub kernel ready: $OUT_DIR/synveil-x86_64.efi"
