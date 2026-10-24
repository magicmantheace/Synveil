#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Complete the pinned native SDK without rebuilding its proven C/C++ compiler.
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
[[ "$EUID" -ne 0 ]] || die "refusing privileged atomic runtime build"
python3 "$ROOT_DIR/tools/native_cxx_runtime.py" --verify
python3 "$ROOT_DIR/tools/source_lock.py" fetch gcc
ATOMIC_SRC="$(prepare_source gcc gcc-atomic)"
ATOMIC_BUILD="$WORK_DIR/toolchain/atomic"
[[ ! -e "$ATOMIC_BUILD" ]] || die "atomic runtime build output already exists"
mkdir -p "$ATOMIC_BUILD"
(
    cd "$ATOMIC_BUILD"
    unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH LIBRARY_PATH COMPILER_PATH GCC_EXEC_PREFIX
    CC="$TARGET-gcc" AR="$TARGET-ar" RANLIB="$TARGET-ranlib" \
        CFLAGS="-O2 --sysroot=$SYSROOT" \
        "$ATOMIC_SRC/libatomic/configure" \
        --build="$(gcc -dumpmachine)" --host="$TARGET" \
        --prefix="$TOOLCHAIN_DIR/$TARGET" \
        --disable-multilib --enable-shared --enable-static
    # The aggregate GCC target also installs into a sibling compiler-build
    # directory. This standalone stage needs the real library target only;
    # our checked staging below installs its exact products into the sysroot.
    make -j"$JOBS" libatomic.la
)
python3 "$ROOT_DIR/tools/native_atomic.py" --stage "$ATOMIC_BUILD/.libs"
python3 "$ROOT_DIR/tools/native_atomic.py" --execute
