#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    cat <<'EOF'
Synveil bootstrap builder

Usage:
  bash build.sh lint
  bash build.sh doctor
  bash build.sh native-check
  bash build.sh offline-check [--level quick|host|guest] [--plan]
  bash build.sh native
  bash build.sh fetch
  bash build.sh toolchain
  bash build.sh toolchain-cxx
  bash build.sh rootfs
  bash build.sh kernel
  bash build.sh image
  bash build.sh manifest
  bash build.sh qemu
  bash build.sh smoke
  bash build.sh native-smoke
  bash build.sh recovery-smoke
  bash build.sh guest-fixture --case absent|protocol|graphical-login|graphical-locked|graphical-onboard|recovery-status|audit-release-crash
  bash build.sh state-disk [--path PATH] [--size-mib N]
  bash build.sh persistence-smoke [--timeout SECONDS]
  bash build.sh audit-crash-smoke [--rollover | --archive-only] [--release-archive] [--timeout SECONDS]
  bash build.sh ui-preferences-smoke [--timeout SECONDS]
  bash build.sh vm-bundle [--dest PATH] [--size-mib N]
  bash build.sh auth-vm-bundle [--fixture PATH] [--dest PATH] [--size-mib N]
  bash build.sh all
  bash build.sh clean
  bash build.sh distclean
EOF
}

command="${1:-help}"
case "$command" in
    all|native|fetch|toolchain|toolchain-cxx|rootfs|kernel|image|manifest|qemu|smoke|native-smoke|recovery-smoke|guest-fixture|state-disk|persistence-smoke|audit-crash-smoke|ui-preferences-smoke|vm-bundle|auth-vm-bundle|clean|distclean)
        exec 9>"$ROOT_DIR/.synveil-build.lock"
        flock -n 9 || {
            printf '[synveil] another build command owns this checkout; wait for it to finish\n' >&2
            exit 1
        }
        ;;
esac
case "$command" in
    lint)
        bash "$ROOT_DIR/tests/lint-bootstrap.sh"
        ;;
    doctor)
        bash "$ROOT_DIR/build/scripts/doctor.sh"
        ;;
    offline-check)
        exec python3 "$ROOT_DIR/tools/offline_checks.py" "${@:2}"
        ;;
    native-check)
        source "$ROOT_DIR/build/scripts/common.sh"
        python3 "$ROOT_DIR/tools/native_preflight.py" \
            --toolchain-dir "$TOOLCHAIN_DIR" --sysroot "$SYSROOT" --target "$TARGET" "${@:2}"
        ;;
    native)
        source "$ROOT_DIR/build/scripts/common.sh"
        python3 "$ROOT_DIR/tools/native_build.py" \
            --toolchain-dir "$TOOLCHAIN_DIR" --sysroot "$SYSROOT" --target "$TARGET" \
            --work-dir "$WORK_DIR" --out-dir "$OUT_DIR" "${@:2}"
        ;;
    fetch)
        python3 "$ROOT_DIR/tools/source_lock.py" fetch
        ;;
    toolchain)
        bash "$ROOT_DIR/build/scripts/doctor.sh"
        bash "$ROOT_DIR/build/scripts/toolchain.sh"
        ;;
    toolchain-cxx)
        bash "$ROOT_DIR/build/scripts/toolchain-cxx.sh"
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
        # Respect a private VM bundle's SYNVEIL_OUT_DIR; never regenerate it.
        source "$ROOT_DIR/build/scripts/common.sh"
        [[ -f "$OUT_DIR/synveil-x86_64.img" ]] || {
            printf '[synveil] QEMU boot image missing: %s\n' "$OUT_DIR/synveil-x86_64.img" >&2
            exit 1
        }
        bash "$ROOT_DIR/build/scripts/qemu.sh"
        ;;
    smoke)
        [[ -f "$ROOT_DIR/out/synveil-x86_64.img" ]] || bash "$ROOT_DIR/build/scripts/image.sh"
        bash "$ROOT_DIR/tests/smoke-boot.sh"
        ;;
    native-smoke)
        SYNVEIL_SMOKE_REQUIRE_NATIVE=1 bash "$ROOT_DIR/tests/smoke-boot.sh"
        ;;
    recovery-smoke)
        python3 "$ROOT_DIR/tests/smoke-recovery.py" "${@:2}"
        ;;
    guest-fixture)
        python3 "$ROOT_DIR/tests/build-guest-fixture.py" "${@:2}"
        ;;
    state-disk)
        python3 "$ROOT_DIR/tools/state_disk.py" "${@:2}"
        ;;
    persistence-smoke)
        python3 "$ROOT_DIR/tests/smoke-persistence.py" "${@:2}"
        ;;
    audit-crash-smoke)
        python3 "$ROOT_DIR/tests/smoke-audit-crash.py" "${@:2}"
        ;;
    ui-preferences-smoke)
        python3 "$ROOT_DIR/tests/smoke-ui-preferences.py" "${@:2}"
        ;;
    vm-bundle)
        python3 "$ROOT_DIR/tools/vm_bundle.py" "${@:2}"
        ;;
    auth-vm-bundle)
        python3 "$ROOT_DIR/tools/auth_vm_bundle.py" "${@:2}"
        ;;
    all)
        bash "$ROOT_DIR/tests/lint-bootstrap.sh"
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
