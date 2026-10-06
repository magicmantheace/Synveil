#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

ensure_dirs
python3 "$ROOT_DIR/tools/source_lock.py" fetch binutils gcc linux glibc

BINUTILS_SRC="$(prepare_source binutils)"
GCC_SRC="$(prepare_source gcc)"
LINUX_SRC="$(prepare_source linux)"
GLIBC_SRC="$(prepare_source glibc)"

rm -rf "$WORK_DIR/toolchain"
mkdir -p "$WORK_DIR/toolchain/binutils" "$WORK_DIR/toolchain/gcc-stage1" "$WORK_DIR/toolchain/glibc"

log "building binutils $(source_version binutils)"
(
    cd "$WORK_DIR/toolchain/binutils"
    "$BINUTILS_SRC/configure"         --prefix="$TOOLCHAIN_DIR"         --target="$TARGET"         --with-sysroot="$SYSROOT"         --disable-nls         --disable-werror
    make -j"$JOBS"
    make install
)

export PATH="$TOOLCHAIN_DIR/bin:$PATH"

log "building GCC stage 1 $(source_version gcc)"
(
    cd "$WORK_DIR/toolchain/gcc-stage1"
    "$GCC_SRC/configure"         --target="$TARGET"         --prefix="$TOOLCHAIN_DIR"         --with-sysroot="$SYSROOT"         --with-newlib         --without-headers         --disable-nls         --disable-shared         --disable-multilib         --disable-threads         --disable-libatomic         --disable-libgomp         --disable-libquadmath         --disable-libsanitizer         --disable-libssp         --disable-libstdcxx-pch         --disable-libvtv         --enable-languages=c
    make -j"$JOBS" all-gcc all-target-libgcc
    make install-gcc install-target-libgcc
)

log "installing Linux UAPI headers"
(
    cd "$LINUX_SRC"
    make mrproper
    make ARCH=x86 headers_install INSTALL_HDR_PATH="$SYSROOT/usr"
)

log "building glibc $(source_version glibc)"
BUILD_TRIPLET="$("$GLIBC_SRC/scripts/config.guess")"
(
    cd "$WORK_DIR/toolchain/glibc"
    CC="$TARGET-gcc"     AR="$TARGET-ar"     RANLIB="$TARGET-ranlib"     "$GLIBC_SRC/configure"         --prefix=/usr         --host="$TARGET"         --build="$BUILD_TRIPLET"         --with-headers="$SYSROOT/usr/include"         --disable-multilib         --enable-kernel=6.18
    make -j"$JOBS"
    make DESTDIR="$SYSROOT" install
)

log "checking target compiler against the Synveil sysroot"
cat >"$WORK_DIR/toolchain/sanity.c" <<'EOF'
int main(void) { return 0; }
EOF
"$TARGET-gcc" --sysroot="$SYSROOT" "$WORK_DIR/toolchain/sanity.c" -o "$WORK_DIR/toolchain/sanity"
"$TARGET-readelf" -h "$WORK_DIR/toolchain/sanity" >/dev/null
rm -f "$WORK_DIR/toolchain/sanity.c" "$WORK_DIR/toolchain/sanity"

cat >"$TOOLCHAIN_DIR/SYNVEIL-TOOLCHAIN" <<EOF
target=$TARGET
binutils=$(source_version binutils)
gcc=$(source_version gcc)
glibc=$(source_version glibc)
linux_headers=$(source_version linux)
EOF

log "bootstrap toolchain ready: $TARGET"
