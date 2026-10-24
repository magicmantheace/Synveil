#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Explicit post-glibc compiler stage for source-built desktop dependencies.
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
[[ "$EUID" -ne 0 ]] || die "refusing privileged desktop toolchain build"
[[ -f "$TOOLCHAIN_DIR/SYNVEIL-TOOLCHAIN" ]] || die "build the native C/glibc toolchain first"
for expected in "target=$TARGET" "gcc=$(source_version gcc)" "glibc=$(source_version glibc)"; do
    grep -Fxq "$expected" "$TOOLCHAIN_DIR/SYNVEIL-TOOLCHAIN" || die "native toolchain identity mismatch: $expected"
done
[[ -f "$SYSROOT/usr/include/stdio.h" && -f "$SYSROOT/usr/lib64/libc.so" ]] \
    || die "native glibc sysroot missing"
python3 "$ROOT_DIR/tools/source_lock.py" fetch gcc
GCC_FINAL_SRC="$(prepare_source gcc gcc-final)"
FINAL_BUILD="$WORK_DIR/toolchain/gcc-final"
mkdir -p "$FINAL_BUILD"
[[ ! -e "$FINAL_BUILD/Makefile" ]] || die "final compiler build already configured; use a fresh build tree"
log "building native GCC C/C++ against the existing Synveil glibc"
(
    cd "$FINAL_BUILD"
    "$GCC_FINAL_SRC/configure" \
        --target="$TARGET" --prefix="$TOOLCHAIN_DIR" \
        --with-sysroot="$SYSROOT" --with-build-sysroot="$SYSROOT" \
        --enable-languages=c,c++ --enable-threads=posix --enable-shared \
        --disable-bootstrap --disable-multilib --disable-nls \
        --disable-libatomic --disable-libgomp --disable-libquadmath \
        --disable-libsanitizer --disable-libssp --disable-libstdcxx-pch --disable-libvtv
    make -j"$JOBS" all-gcc all-target-libgcc all-target-libstdc++-v3
    make install-gcc install-target-libgcc install-target-libstdc++-v3
)

# GCC installs its target runtimes beside its sysroot. Stage the native runtime
# SONAMEs into the existing glibc sysroot as well, so a future guest and external
# dependency builder never need a host runtime or compiler-prefix mount.
python3 "$ROOT_DIR/tools/native_cxx_runtime.py" --stage
log "native C/C++ toolchain and runtime ELF checks complete"
