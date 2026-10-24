#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

NATIVE_ROOTFS="${SYNVEIL_NATIVE_ROOTFS:-0}"
NETWORK_ROOTFS="${SYNVEIL_NETWORK_ROOTFS:-0}"
[[ "$NETWORK_ROOTFS" == 0 || "$NETWORK_ROOTFS" == 1 ]] || die "SYNVEIL_NETWORK_ROOTFS must be 0 or 1"
AUTH_DESKTOP_ROOTFS="${SYNVEIL_AUTH_DESKTOP_ROOTFS:-0}"
[[ "$AUTH_DESKTOP_ROOTFS" == 0 || "$AUTH_DESKTOP_ROOTFS" == 1 ]] || die "SYNVEIL_AUTH_DESKTOP_ROOTFS must be 0 or 1"
DEV_SERIAL_ROOTFS="${SYNVEIL_DEV_SERIAL_ROOTFS:-0}"
[[ "$DEV_SERIAL_ROOTFS" == 0 || "$DEV_SERIAL_ROOTFS" == 1 ]] || die "SYNVEIL_DEV_SERIAL_ROOTFS must be 0 or 1"
[[ "$DEV_SERIAL_ROOTFS" != 1 || "$AUTH_DESKTOP_ROOTFS" == 1 ]] || die "developer serial override requires authenticated preview"
DESKTOP_AUTOSTART="${SYNVEIL_DESKTOP_AUTOSTART:-0}"
[[ "$DESKTOP_AUTOSTART" == 0 || "$DESKTOP_AUTOSTART" == 1 ]] || die "SYNVEIL_DESKTOP_AUTOSTART must be 0 or 1"
GRAPHICS_ROOTFS="${SYNVEIL_GRAPHICS_ROOTFS:-0}"
[[ "$GRAPHICS_ROOTFS" == 0 || "$GRAPHICS_ROOTFS" == 1 ]] || die "SYNVEIL_GRAPHICS_ROOTFS must be 0 or 1"
[[ "$DESKTOP_AUTOSTART" != 1 || "$GRAPHICS_ROOTFS" == 1 ]] || die "desktop autostart requires SYNVEIL_GRAPHICS_ROOTFS=1"
PERSISTENCE_ROOTFS="${SYNVEIL_PERSISTENCE_ROOTFS:-0}"
[[ "$PERSISTENCE_ROOTFS" == 0 || "$PERSISTENCE_ROOTFS" == 1 ]] || die "SYNVEIL_PERSISTENCE_ROOTFS must be 0 or 1"
[[ "$NATIVE_ROOTFS" == 0 || "$NATIVE_ROOTFS" == 1 ]] || die "SYNVEIL_NATIVE_ROOTFS must be 0 or 1"
[[ "$AUTH_DESKTOP_ROOTFS" != 1 || (
   "$GRAPHICS_ROOTFS" == 1 && "$NATIVE_ROOTFS" == 1 &&
   "$PERSISTENCE_ROOTFS" == 1 && "$DESKTOP_AUTOSTART" == 1
) ]] || die "auth desktop preview needs native, graphics, persistence and autostart"
if [[ "$NATIVE_ROOTFS" == 1 ]]; then
    python3 "$ROOT_DIR/tools/package_native.py" --bundle "$OUT_DIR/native"
fi

ensure_dirs
SYNVEIL_VERSION="$(tr -d '\n' < "$ROOT_DIR/VERSION")"
[[ -x "$TOOLCHAIN_DIR/bin/$TARGET-gcc" ]] || die "toolchain missing; run 'bash build.sh toolchain' first"
python3 "$ROOT_DIR/tools/source_lock.py" fetch busybox

BUSYBOX_SRC="$(prepare_source busybox)"
rm -rf "$ROOTFS_DIR"
mkdir -p "$ROOTFS_DIR"

log "building BusyBox $(source_version busybox)"
(
    cd "$BUSYBOX_SRC"
    make distclean
    make ARCH=x86_64 CROSS_COMPILE="$TARGET-" defconfig

    if grep -q '^# CONFIG_STATIC is not set' .config; then
        sed -i 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' .config
    elif grep -q '^CONFIG_STATIC=' .config; then
        sed -i 's/^CONFIG_STATIC=.*/CONFIG_STATIC=y/' .config
    else
        printf '%s\n' 'CONFIG_STATIC=y' >> .config
    fi

    # This bootstrap does not use traffic control. BusyBox's tc still references
    # CBQ UAPI definitions removed from the pinned Linux headers.
    sed -i 's/^CONFIG_TC=y/# CONFIG_TC is not set/' .config

    make ARCH=x86_64 CROSS_COMPILE="$TARGET-" oldconfig </dev/null
    make -j"$JOBS" ARCH=x86_64 CROSS_COMPILE="$TARGET-"
    make ARCH=x86_64 CROSS_COMPILE="$TARGET-" CONFIG_PREFIX="$ROOTFS_DIR" install
)

mkdir -p     "$ROOTFS_DIR"/{dev,etc,proc,root,run,sys,tmp,var}     "$ROOTFS_DIR"/usr/{bin,sbin}     "$ROOTFS_DIR"/var/{log,tmp}

install -m 0755 "$ROOT_DIR/build/rootfs/init" "$ROOTFS_DIR/init"
install -D -m 0755 "$ROOT_DIR/build/rootfs/recovery-status" \
    "$ROOTFS_DIR/usr/libexec/synveil/recovery-status"
if [[ "$GRAPHICS_ROOTFS" == 1 ]]; then
    # Diagnostic framebuffer splash, not a desktop compositor.
    "$TARGET-gcc" --sysroot="$SYSROOT" -static -O2 -std=c11 -Wall -Wextra -Werror \
        "$ROOT_DIR/src/veil-display/main.c" "$ROOT_DIR/src/veil-display/preferences.c" \
        "$ROOT_DIR/src/veil-display/core_health.c" \
        "$ROOT_DIR/src/veil-display/network_diag.c" \
        "$ROOT_DIR/src/veil-display/pressure_diag.c" \
        "$ROOT_DIR/src/veil-display/enroll.c" \
        "$ROOT_DIR/src/veil-display/files.c" \
        -o "$ROOTFS_DIR/usr/bin/synveil-display"
    "$TARGET-gcc" --sysroot="$SYSROOT" -static -O2 -std=c11 -Wall -Wextra -Werror \
        "$ROOT_DIR/src/veil-display/input.c" -o "$ROOTFS_DIR/usr/bin/synveil-input"
    "$TARGET-gcc" --sysroot="$SYSROOT" -static -O2 -std=c11 -Wall -Wextra -Werror \
        "$ROOT_DIR/src/veil-display/vt.c" -o "$ROOTFS_DIR/usr/bin/synveil-vt"
    install -D -m 0755 "$ROOT_DIR/build/rootfs/start-desktop" \
        "$ROOTFS_DIR/usr/libexec/synveil/start-desktop"
    if [[ "$DESKTOP_AUTOSTART" == 1 ]]; then
        install -D -m 0644 /dev/null "$ROOTFS_DIR/etc/synveil/desktop-autostart"
    fi
    if [[ "$AUTH_DESKTOP_ROOTFS" == 1 ]]; then
        install -D -m 0644 /dev/null "$ROOTFS_DIR/etc/synveil/auth-desktop"
        # Never installed automatically. This explicitly unsafe testing
        # override must not be used in a physical/owner-facing build.
        if [[ "$DEV_SERIAL_ROOTFS" == 1 ]]; then
            install -D -m 0644 /dev/null "$ROOTFS_DIR/etc/synveil/developer-serial-recovery"
        fi
    fi
    # Non-root disposable developer login, deliberately password-locked.
    # The graphical launcher only opens this identity, never a root shell.
    mkdir -p "$ROOTFS_DIR/home/synveil"
    chmod 0700 "$ROOTFS_DIR/home/synveil"
fi
if [[ "$NETWORK_ROOTFS" == 1 ]]; then
    install -D -m 0755 "$ROOT_DIR/build/rootfs/configure-network" "$ROOTFS_DIR/usr/libexec/synveil/configure-network"
    install -D -m 0755 "$ROOT_DIR/build/rootfs/udhcpc-script" "$ROOTFS_DIR/usr/libexec/synveil/udhcpc-script"
fi
if [[ "$PERSISTENCE_ROOTFS" == 1 ]]; then
    install -D -m 0755 "$ROOT_DIR/build/rootfs/mount-state" "$ROOTFS_DIR/usr/libexec/synveil/mount-state"
fi
if [[ "$NATIVE_ROOTFS" == 1 ]]; then
    python3 "$ROOT_DIR/tools/package_native.py" --bundle "$OUT_DIR/native" --rootfs "$ROOTFS_DIR"
    install -D -m 0755 "$ROOT_DIR/build/rootfs/start-core" "$ROOTFS_DIR/usr/libexec/synveil/start-core"
    install -D -m 0755 "$ROOT_DIR/build/rootfs/start-seat" "$ROOTFS_DIR/usr/libexec/synveil/start-seat"
fi
chmod 1777 "$ROOTFS_DIR/tmp" "$ROOTFS_DIR/var/tmp"

cat >"$ROOTFS_DIR/etc/os-release" <<EOF
NAME="Synveil"
ID=synveil
PRETTY_NAME="Synveil bootstrap"
VERSION="$SYNVEIL_VERSION"
VERSION_ID="$SYNVEIL_VERSION"
HOME_URL="https://github.com/magicmantheace/Synveil"
EOF

cat >"$ROOTFS_DIR/etc/passwd" <<'EOF'
root:x:0:0:root:/root:/bin/sh
EOF

cat >"$ROOTFS_DIR/etc/group" <<'EOF'
root:x:0:
EOF

if [[ "$GRAPHICS_ROOTFS" == 1 ]]; then
    # The demo account does not grant local or remote password login.
    # It exists only for synveil-vt's fixed-identity demo console.
    printf '%s\n' 'synveil:x:1000:1000:Synveil VM demo:/home/synveil:/bin/sh' >> "$ROOTFS_DIR/etc/passwd"
    printf '%s\n' 'synveil:x:1000:' >> "$ROOTFS_DIR/etc/group"
fi

cat >"$ROOTFS_DIR/etc/hostname" <<'EOF'
synveil
EOF

cat >"$ROOTFS_DIR/etc/profile" <<'EOF'
export PATH=/sbin:/bin:/usr/sbin:/usr/bin
export HOME=/root
export USER=root
PS1='synveil:\w# '
EOF

mkdir -p "$OUT_DIR"
log "packing rootfs artifacts"
(
    cd "$ROOTFS_DIR"
    find . -print0         | sort -z         | cpio --null -o --format=newc --owner=0:0 2>/dev/null         > "$OUT_DIR/synveil-initramfs.cpio"
)
zstd -q -T0 -19 -f "$OUT_DIR/synveil-initramfs.cpio" -o "$OUT_DIR/synveil-initramfs.cpio.zst"

tar     --sort=name     --mtime="@${SOURCE_DATE_EPOCH:-0}"     --owner=0 --group=0 --numeric-owner     -C "$ROOTFS_DIR" -cf - .     | zstd -q -T0 -19 -f -o "$OUT_DIR/synveil-rootfs.tar.zst"

log "rootfs ready: $ROOTFS_DIR"
