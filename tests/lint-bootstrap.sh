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
    tests/smoke-boot.sh
)

for file in "${bash_files[@]}"; do
    bash -n "$file"
done

sh -n build/rootfs/init
python3 -m py_compile tools/source_lock.py tools/build_manifest.py
python3 -m json.tool build/manifests/sources.json >/dev/null
python3 tools/source_lock.py dump >/dev/null

printf '[lint] bootstrap scripts, Python tools, and source manifest passed syntax checks\n'
