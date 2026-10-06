#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    cat <<'EOF'
Synveil bootstrap builder

Usage:
  bash build.sh doctor
  bash build.sh fetch
  bash build.sh toolchain
  bash build.sh rootfs
  bash build.sh kernel
  bash build.sh image
  bash build.sh manifest
  bash build.sh qemu
  bash build.sh smoke
  bash build.sh all
  bash build.sh clean
  bash build.sh distclean
EOF
}

command="${1:-help}"
case "$command" in
    doctor)
        bash "$ROOT_DIR/build/scripts/doctor.sh"
        ;;
    fetch)
        python3 "$ROOT_DIR/tools/source_lock.py" fetch
        ;;
    toolchain)
        bash "$ROOT_DIR/build/scripts/doctor.sh"
        bash "$ROOT_DIR/build/scripts/toolchain.sh"
        ;;
    rootfs)
        bash "$ROOT_DIR/build/scripts/rootfs.sh"
        ;;
    kernel)
        bash "$ROOT_DIR/build/scripts/kernel.sh"
        ;;
    image)
        bash "$ROOT_DIR/build/scripts/image.sh"
        ;;
    manifest)
        python3 "$ROOT_DIR/tools/build_manifest.py"
        ;;
    qemu)
        [[ -f "$ROOT_DIR/out/synveil-x86_64.img" ]] || bash "$ROOT_DIR/build/scripts/image.sh"
        bash "$ROOT_DIR/build/scripts/qemu.sh"
        ;;
    smoke)
        [[ -f "$ROOT_DIR/out/synveil-x86_64.img" ]] || bash "$ROOT_DIR/build/scripts/image.sh"
        bash "$ROOT_DIR/tests/smoke-boot.sh"
        ;;
    all)
        bash "$ROOT_DIR/build/scripts/doctor.sh"
        python3 "$ROOT_DIR/tools/source_lock.py" fetch
        bash "$ROOT_DIR/build/scripts/toolchain.sh"
        bash "$ROOT_DIR/build/scripts/rootfs.sh"
        bash "$ROOT_DIR/build/scripts/kernel.sh"
        bash "$ROOT_DIR/build/scripts/image.sh"
        python3 "$ROOT_DIR/tools/build_manifest.py"
        ;;
    clean)
        rm -rf "$ROOT_DIR/out" "$ROOT_DIR/build/work"
        ;;
    distclean)
        rm -rf "$ROOT_DIR/out" "$ROOT_DIR/build/work" "$ROOT_DIR/build/toolchain/out" "$ROOT_DIR/.cache"
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        printf 'unknown build command: %s\n\n' "$command" >&2
        usage >&2
        exit 2
        ;;
esac
