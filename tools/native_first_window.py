#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Cross-link Synveil's real first xdg_toplevel client with pinned target libs.

No compositor runtime, rootfs installation, physical disk or firmware access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_libffi as ffi
import native_wayland as wayland
import native_wayland_target as wt
import native_xdg_shell as xdg

TARGET = "x86_64-synveil-linux-gnu"
STAGE = ROOT / "out/native-wayland-target"
PROTO = ROOT / "out/native-xdg-shell"
WINDOW = ROOT / "src/desktop-wayland/first-window.c"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checked_file(path, *, description):
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise ValueError(description + " missing or unsafe")
    return path


def validated_inputs():
    """Require exact receipts and bytes from each independently pinned build."""
    wp, fp = wt.prerequisites()
    xdg_pin = xdg.pin()
    if xdg_pin["version"] != "1.49":
        raise ValueError("unreviewed xdg-shell protocol version")
    target_receipt = json.loads(checked_file(
        STAGE / "validation.json", description="target Wayland receipt").read_text())
    if (target_receipt.get("result") != "native-wayland-client-server-staged" or
        target_receipt.get("target") != TARGET or
        target_receipt.get("wayland_source_sha512") != wp["sha512"] or
        target_receipt.get("ffi_source_sha256") != fp["sha256"] or
        target_receipt.get("native_boot_image_changed") is not False):
        raise ValueError("target Wayland and libffi build identity mismatch")
    proto_receipt = json.loads(checked_file(
        PROTO / "validation.json", description="xdg-shell receipt").read_text())
    if (proto_receipt.get("result") != "xdg-shell-generated" or
        proto_receipt.get("version") != "1.49" or
        proto_receipt.get("source_sha512") != xdg_pin["sha512"] or
        proto_receipt.get("interfaces") != list(xdg.REQUIRED) or
        proto_receipt.get("native_boot_image_changed") is not False):
        raise ValueError("pinned xdg-shell interface provenance mismatch")
    header = checked_file(
        PROTO / "generated/xdg-shell-client.h", description="xdg client header")
    code = checked_file(
        PROTO / "generated/xdg-shell-code.c", description="xdg interface source")
    hashes = proto_receipt.get("generated_sha256", {})
    if (hashes.get("client") != sha256(header) or
        hashes.get("code") != sha256(code)):
        raise ValueError("generated xdg-shell inputs changed after source proof")
    client = checked_file(
        STAGE / "stage/usr/lib64/libwayland-client.a",
        description="native libwayland-client.a")
    library = checked_file(
        ffi.STAGE_ROOT / "stage/usr/lib64/libffi.a",
        description="native libffi.a")
    if (sha256(client) != target_receipt.get("client_archive_sha256") or
        sha256(library) != json.loads(checked_file(
            ffi.STAGE_ROOT / "validation.json", description="ffi receipt").read_text()
        ).get("target_static_archive_sha256")):
        raise ValueError("target dependency archive hash mismatch")
    includes = STAGE / "stage/usr/include"
    ffi_headers = list((ffi.STAGE_ROOT / "stage").rglob("ffi.h"))
    if (not includes.is_dir() or includes.is_symlink() or
        len(ffi_headers) != 1 or ffi_headers[0].is_symlink()):
        raise ValueError("target Wayland/libffi header boundaries invalid")
    checked_file(WINDOW, description="original Synveil app source")
    return header, code, client, library, includes, ffi_headers[0].parent, wp, fp


def cross_link():
    if os.geteuid() == 0:
        raise ValueError("refusing privileged graphics linker")
    (_, code, client, library, includes, ffi_headers, wp, fp) = validated_inputs()
    tools, sysroot = ffi.target_tools()
    readelf = ffi.TOOLCHAIN / "bin" / (TARGET + "-readelf")
    checked_file(readelf, description="Synveil target readelf")
    output = STAGE / "first-window-target"
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to overwrite prior first-window ELF")
    env = wt.isolated_pkgconfig_env(os.environ)
    for key in ("CC", "CFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH"):
        env.pop(key, None)
    subprocess.run([
        str(tools["gcc"]), "--sysroot=" + str(sysroot),
        "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-I" + str(includes),
        "-I" + str(ffi_headers),
        "-I" + str(code.parent),
        str(WINDOW), str(code), str(client), str(library),
        "-lm", "-lrt", "-ldl", "-pthread", "-o", str(output)
    ], check=True, env=env, timeout=120)
    hdr = subprocess.check_output([str(readelf), "-h", str(output)],
                                  text=True, timeout=15)
    elf = subprocess.check_output([str(readelf), "-l", str(output)],
                                  text=True, timeout=15)
    if ("Advanced Micro Devices X86-64" not in hdr or
        "/lib64/ld-linux-x86-64.so.2" not in elf):
        output.unlink(missing_ok=True)
        raise ValueError("first window is not a native Synveil target ELF")
    receipt = {
        "result": "synveil-native-first-window-cross-linked",
        "target": TARGET, "wayland_version": wp["version"],
        "ffi_version": fp["version"], "xdg_shell_version": "1.49",
        "source_sha256": sha256(WINDOW),
        "native_first_window_sha256": sha256(output),
        "native_compositor_running": False,
        "boot_image_changed": False, "owner_hardware_modified": False
    }
    (STAGE / "first-window-validation.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--build", action="store_true")
    opts = parser.parse_args()
    wt.prerequisites()
    if xdg.pin()["version"] != "1.49":
        raise ValueError("unexpected protocol version")
    if opts.plan:
        print("SYNVEIL_NATIVE_FIRST_WINDOW_LINK_PLAN_PINNED")
        return 0
    cross_link()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
