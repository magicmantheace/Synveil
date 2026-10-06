#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

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

    make ARCH=x86_64 CROSS_COMPILE="$TARGET-" oldconfig </dev/null
    make -j"$JOBS" ARCH=x86_64 CROSS_COMPILE="$TARGET-"
    make ARCH=x86_64 CROSS_COMPILE="$TARGET-" CONFIG_PREFIX="$ROOTFS_DIR" install
)

mkdir -p     "$ROOTFS_DIR"/{dev,etc,proc,root,run,sys,tmp,var}     "$ROOTFS_DIR"/usr/{bin,sbin}     "$ROOTFS_DIR"/var/{log,tmp}

install -m 0755 "$ROOT_DIR/build/rootfs/init" "$ROOTFS_DIR/init"
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
