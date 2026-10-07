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
    "$SYSROOT"

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
        --host="$TARGET" \
        --build="$BUILD_TRIPLET" \
        --with-headers="$SYSROOT/usr/include" \
        --disable-multilib \
        --disable-werror \
        --enable-kernel=6.18

    make CXX= -j"$JOBS"
    make CXX= DESTDIR="$SYSROOT" install
)

log "validating glibc installation"
for required in \
    "$SYSROOT/usr/lib/crt1.o" \
    "$SYSROOT/usr/lib/crti.o" \
    "$SYSROOT/usr/lib/crtn.o" \
    "$SYSROOT/usr/lib/libc.so"
do
    [[ -e "$required" ]] || die "glibc install missing required target file: $required"
done

log "checking target compiler against the Synveil sysroot"
cat >"$WORK_DIR/toolchain/sanity.c" <<'EOF'
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

rm -f "$WORK_DIR/toolchain/sanity.c" "$WORK_DIR/toolchain/sanity"

cat >"$TOOLCHAIN_DIR/SYNVEIL-TOOLCHAIN" <<EOF
target=$TARGET
binutils=$(source_version binutils)
gcc=$(source_version gcc)
glibc=$(source_version glibc)
linux_headers=$(source_version linux)
EOF

log "bootstrap toolchain ready: $TARGET"
