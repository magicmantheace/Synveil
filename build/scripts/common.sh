# SPDX-License-Identifier: MPL-2.0

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${SYNVEIL_OUT_DIR:-$ROOT_DIR/out}"
WORK_DIR="${SYNVEIL_WORK_DIR:-$ROOT_DIR/build/work}"
TOOLCHAIN_DIR="${SYNVEIL_TOOLCHAIN_DIR:-$ROOT_DIR/build/toolchain/out}"
TARGET="${SYNVEIL_TARGET:-x86_64-synveil-linux-gnu}"
SYSROOT="${SYNVEIL_SYSROOT:-$TOOLCHAIN_DIR/$TARGET/sysroot}"
ROOTFS_DIR="${SYNVEIL_ROOTFS_DIR:-$WORK_DIR/rootfs}"
JOBS="${SYNVEIL_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

export PATH="$TOOLCHAIN_DIR/bin:$PATH"
export LC_ALL=C

log() { printf '[synveil] %s\n' "$*" >&2; }
die() { printf '[synveil] ERROR: %s\n' "$*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

source_path() {
    python3 "$ROOT_DIR/tools/source_lock.py" path "$1"
}

source_version() {
    python3 "$ROOT_DIR/tools/source_lock.py" version "$1"
}

prepare_source() {
    local name="$1"
    local destination="$WORK_DIR/src/$name"
    local archive temp entries

    archive="$(source_path "$name")"
    rm -rf "$destination" || die "could not remove previous source tree for $name"
    mkdir -p "$WORK_DIR/src" || die "could not create source work directory"

    if [[ -d "$archive/.git" ]]; then
        mkdir -p "$destination" || die "could not create Git source directory for $name"
        git -C "$archive" archive --format=tar HEAD | tar --no-same-owner -xf - -C "$destination" \
            || die "could not materialize Git source for $name"
        printf '%s\n' "$destination"
        return 0
    fi

    temp="$(mktemp -d "$WORK_DIR/src/.extract.XXXXXX")"

    tar --no-same-owner -xf "$archive" -C "$temp" || {
        rm -rf "$temp"
        die "could not extract source archive for $name"
    }
    shopt -s nullglob dotglob
    entries=("$temp"/*)
    shopt -u nullglob dotglob
    [[ "${#entries[@]}" -eq 1 && -d "${entries[0]}" ]] || {
        rm -rf "$temp"
        die "source archive for $name did not contain exactly one top-level directory"
    }

    mv -T "${entries[0]}" "$destination" || die "could not install source tree for $name"
    rmdir "$temp" || die "could not remove temporary extraction directory for $name"
    printf '%s\n' "$destination"
}

ensure_dirs() {
    mkdir -p "$OUT_DIR" "$WORK_DIR" "$TOOLCHAIN_DIR" "$SYSROOT"
}
