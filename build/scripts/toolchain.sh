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

# A toolchain rebuild must not inherit stale installed target files.
rm -rf "$WORK_DIR/toolchain" "$TOOLCHAIN_DIR"
mkdir -p \
    "$WORK_DIR/toolchain/binutils" \
    "$WORK_DIR/toolchain/gcc-stage1" \
    "$WORK_DIR/toolchain/glibc" \
    "$TOOLCHAIN_DIR" \
    "$SYSROOT" \
    "$SYSROOT/lib" \
    "$SYSROOT/lib64" \
    "$SYSROOT/usr/lib" \
    "$SYSROOT/usr/lib64"

log "building binutils $(source_version binutils)"
(
    cd "$WORK_DIR/toolchain/binutils"
    "$BINUTILS_SRC/configure" \
        --prefix="$TOOLCHAIN_DIR" \
        --target="$TARGET" \
        --with-sysroot="$SYSROOT" \
        --disable-nls \
        --disable-werror \
        --disable-gdb \
        --disable-gdbserver \
        --disable-gprofng \
        --disable-sim
    make -j"$JOBS"
    make install
)

export PATH="$TOOLCHAIN_DIR/bin:$PATH"

log "building GCC stage 1 $(source_version gcc)"
(
    cd "$WORK_DIR/toolchain/gcc-stage1"
    "$GCC_SRC/configure" \
        --target="$TARGET" \
        --prefix="$TOOLCHAIN_DIR" \
        --with-sysroot="$SYSROOT" \
        --with-newlib \
        --without-headers \
        --disable-nls \
        --disable-shared \
        --disable-multilib \
        --disable-threads \
        --disable-libatomic \
        --disable-libgomp \
        --disable-libquadmath \
        --disable-libsanitizer \
        --disable-libssp \
        --disable-libstdcxx-pch \
        --disable-libvtv \
        --enable-languages=c
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

    # Stage 1 intentionally has no target C++ runtime. Keeping CXX empty
    # makes glibc select its C-only support helper instead of linking
    # links-dso-program against a nonexistent target libstdc++.
    CC="$TARGET-gcc" \
    CXX= \
    BUILD_CC=gcc \
    AR="$TARGET-ar" \
    AS="$TARGET-as" \
    LD="$TARGET-ld" \
    NM="$TARGET-nm" \
    OBJCOPY="$TARGET-objcopy" \
    OBJDUMP="$TARGET-objdump" \
    RANLIB="$TARGET-ranlib" \
    READELF="$TARGET-readelf" \
    "$GLIBC_SRC/configure" \
        --prefix=/usr \
        --libdir=/usr/lib64 \
        --host="$TARGET" \
        --build="$BUILD_TRIPLET" \
        --with-headers="$SYSROOT/usr/include" \
        --disable-multilib \
        --disable-werror \
        --enable-kernel=6.18

    make CXX= -j"$JOBS"
    make CXX= DESTDIR="$SYSROOT" install
)

log "refreshing GCC limits after glibc installation"
GCC_INCLUDE_DIR="$("$TARGET-gcc" -print-file-name=include)"
[[ "$GCC_INCLUDE_DIR" == "$TOOLCHAIN_DIR"/* ]] \
    || die "target compiler include directory is outside the Synveil toolchain"
# GCC was built before libc headers existed, so its first limits.h is the
# standalone variant. Use GCC's own normal header construction now that glibc
# is installed; it must include the target libc's POSIX/GNU limits as well.
cat "$GCC_SRC/gcc/limitx.h" "$GCC_SRC/gcc/glimits.h" "$GCC_SRC/gcc/limity.h" \
    >"$GCC_INCLUDE_DIR/limits.h"

log "validating glibc installation"
for required in \
    "$SYSROOT/usr/lib64/crt1.o" \
    "$SYSROOT/usr/lib64/Scrt1.o" \
    "$SYSROOT/usr/lib64/crti.o" \
    "$SYSROOT/usr/lib64/crtn.o" \
    "$SYSROOT/usr/lib64/libc.so" \
    "$SYSROOT/usr/lib64/libc.a" \
    "$SYSROOT/lib64/ld-linux-x86-64.so.2"
do
    [[ -e "$required" ]] || die "glibc install missing required target file: $required"
done

log "checking target compiler against the Synveil sysroot"
cat >"$WORK_DIR/toolchain/sanity.c" <<'EOF'
#define _GNU_SOURCE
#include <limits.h>
_Static_assert(LONG_BIT == 64, "target long width must match x86_64");
_Static_assert(MB_LEN_MAX >= 16, "target libc limits must be included");
int main(void) { return 0; }
EOF

if ! "$TARGET-gcc" --sysroot="$SYSROOT" \
    "$WORK_DIR/toolchain/sanity.c" \
    -o "$WORK_DIR/toolchain/sanity"
then
    log "target compiler search directories:"
    "$TARGET-gcc" --sysroot="$SYSROOT" -print-search-dirs >&2 || true
    log "target runtime files present in sysroot:"
    find "$SYSROOT" -maxdepth 4 \
        \( -name 'crt*.o' -o -name 'libc.so*' -o -name 'ld-linux*.so*' \) \
        -print >&2 || true
    die "target compiler could not link against the installed Synveil sysroot"
fi

"$TARGET-readelf" -h "$WORK_DIR/toolchain/sanity" >/dev/null
"$TARGET-readelf" -l "$WORK_DIR/toolchain/sanity" \
    | grep -q '/lib64/ld-linux-x86-64.so.2' \
    || die "target sanity binary does not use the expected glibc interpreter"

# BusyBox needs a static target link as well as the dynamic compiler probe.
"$TARGET-gcc" --sysroot="$SYSROOT" -static \
    "$WORK_DIR/toolchain/sanity.c" -o "$WORK_DIR/toolchain/sanity-static" \
    || die "target compiler could not link statically against the Synveil sysroot"
"$TARGET-readelf" -h "$WORK_DIR/toolchain/sanity-static" >/dev/null

rm -f "$WORK_DIR/toolchain/sanity.c" "$WORK_DIR/toolchain/sanity" "$WORK_DIR/toolchain/sanity-static"

cat >"$TOOLCHAIN_DIR/SYNVEIL-TOOLCHAIN" <<EOF
target=$TARGET
binutils=$(source_version binutils)
gcc=$(source_version gcc)
glibc=$(source_version glibc)
linux_headers=$(source_version linux)
EOF

log "bootstrap toolchain ready: $TARGET"
