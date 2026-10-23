#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

bash_files=(
    build.sh
    build/scripts/common.sh
    build/scripts/doctor.sh
    build/scripts/toolchain.sh
    build/scripts/rootfs.sh
    build/scripts/kernel.sh
    build/scripts/image.sh
    build/scripts/qemu.sh
    tools/desktop_live.sh
    build/rootfs/mount-state
    tests/smoke-boot.sh
    tests/inspect-image.sh
    tests/source-extraction.sh
)

for file in "${bash_files[@]}"; do
    bash -n "$file"
done

sh -n build/rootfs/init
sh -n build/rootfs/start-desktop
sh -n build/rootfs/start-core
sh -n build/rootfs/start-seat
sh -n build/rootfs/mount-state
sh -n build/rootfs/configure-network
sh -n build/rootfs/udhcpc-script
python3 -m py_compile tools/native_first_window.py tests/native-first-window.py tools/first_window_probe.py tests/first-window-probe.py tools/native_xdg_shell.py tests/native-xdg-shell.py tools/native_wayland_target.py tests/native-wayland-target.py tools/native_libffi.py tests/native-libffi.py tools/native_wayland.py tests/native-wayland.py tools/native_pixman.py tests/native-pixman.py tests/smoke-graphical-onboard.py tests/graphical-onboard-fixture.py tools/auth_vm_bundle.py tests/auth-vm-bundle.py tools/offline_checks.py tools/source_lock.py tools/build_manifest.py tools/state_disk.py tests/smoke-persistence.py tests/smoke-ui-preferences.py tools/qmp_input.py tools/vm_bundle.py tests/vm-bundle.py
python3 -m json.tool build/manifests/sources.json >/dev/null
python3 tools/source_lock.py dump >/dev/null
python3 tools/offline_checks.py --level quick --plan >/dev/null
python3 tools/offline_checks.py --level guest --plan >/dev/null
python3 tests/offline-checks.py
python3 tests/validate-source-lock.py
bash tests/source-extraction.sh
python3 tests/build-lock.py
python3 tests/state-disk.py
python3 tests/audit-crash-smoke.py
python3 tests/vm-bundle.py
python3 tests/auth-vm-bundle.py
python3 tests/network-config.py
python3 tests/qemu-options.py
python3 tests/kernel-seed.py
python3 tests/desktop-live.py
python3 tests/desktop-home.py
python3 tests/inspect-desktop-iso.py
python3 tests/desktop-supervisor.py
python3 tests/serial-console-policy.py
python3 tests/serial-lock-smoke-fixture.py
python3 tests/graphical-onboard-fixture.py
python3 tests/initramfs-profile.py
python3 tests/display-build.py
python3 tests/ui-preferences-smoke-fixture.py
python3 tests/qmp-input.py
python3 tests/native-pixman.py
python3 tests/native-wayland.py
python3 tests/native-libffi.py
python3 tests/native-wayland-target.py
python3 tests/native-xdg-shell.py
python3 tests/first-window-probe.py
python3 tests/native-first-window.py
python3 tools/native_first_window.py --plan
python3 tools/native_xdg_shell.py --check-lock
python3 tools/native_wayland_target.py --plan
python3 tools/native_libffi.py --check-lock
python3 tools/native_wayland.py --check-lock
python3 tools/native_pixman.py --check-lock

printf '[lint] bootstrap scripts, Python tools, and source manifest passed syntax checks\n'
