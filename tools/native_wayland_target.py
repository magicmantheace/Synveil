#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Cross-stage native libwayland client/server using pinned libffi and scanner.

Developer-only build prerequisite. Does not install to native rootfs or disks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import native_libffi as ffi  # noqa: E402
import native_wayland as wayland  # noqa: E402

OUT = ROOT / "out/native-wayland-target"
TOOLCHAIN = ROOT / "build/toolchain/out"
TARGET = "x86_64-synveil-linux-gnu"

# These are link probes, not runtime guest execution.
CLIENT_LINK_SOURCE = (
    "#include <wayland-client-core.h>\n"
    "int main(void) {\n"
    "  struct wl_display *d = wl_display_connect_to_fd(-1);\n"
    "  if (d) wl_display_disconnect(d);\n"
    "  return 0;\n"
    "}\n"
)
SERVER_LINK_SOURCE = (
    "#include <wayland-server-core.h>\n"
    "int main(void) {\n"
    "  struct wl_display *d = wl_display_create();\n"
    "  if (!d) return 1;\n"
    "  wl_display_destroy(d);\n"
    "  return 0;\n"
    "}\n"
)


def prerequisites():
    wp = wayland.release_spec()
    fp = ffi.source_pin()
    if wp["version"] != "1.26.0" or fp["version"] != "3.5.2":
        raise ValueError("unreviewed Wayland or libffi source version")
    return wp, fp


def root_bound_output():
    if (ROOT / "out").is_symlink() or OUT.exists() or OUT.is_symlink():
        raise ValueError("target Wayland stage exists or is symlinked")
    OUT.mkdir(parents=True, mode=0o700)
    OUT.chmod(0o700)


def target_toolchain():
    paths, sysroot = ffi.target_tools()
    if not shutil.which("meson") or not shutil.which("ninja"):
        raise ValueError("Meson/Ninja unavailable")
    if not shutil.which("pkg-config"):
        raise ValueError("pkg-config unavailable")
    return paths, sysroot


def find_one_pc(folder, filename):
    matches = list(folder.rglob(filename))
    if (len(matches) != 1 or not matches[0].is_file() or
        matches[0].is_symlink()):
        raise ValueError("missing or ambiguous dependency pkg-config entry: " + filename)
    return matches[0]


def machine_files(stage, cc, sysroot, ffi_pc_dir, scanner_pc_dir, ffi_stage):
    cross = stage / "cross.ini"
    native = stage / "native.ini"
    cross.write_text("\n".join([
        "[binaries]",
        "c = " + repr(str(cc["gcc"])),
        "ar = " + repr(str(cc["ar"])),
        "strip = " + repr(str(TOOLCHAIN / "bin" / (TARGET + "-strip"))),
        "pkg-config = 'pkg-config'",
        "[host_machine]", "system = 'linux'", "cpu_family = 'x86_64'",
        "cpu = 'x86_64'", "endian = 'little'",
        "[properties]", "needs_exe_wrapper = true",
        # Meson's sys_root rewrites -I/-L paths from libffi.pc (prefix=/usr).
        # It must point at the *staged libffi* image, not the compiler libc
        # sysroot; target GCC remains explicitly bound to native glibc below.
        "sys_root = " + repr(str(ffi_stage)),
        "pkg_config_libdir = " + repr([str(ffi_pc_dir)]),
        "[built-in options]",
        "c_args = " + repr(["--sysroot=" + str(sysroot)]),
        "c_link_args = " + repr(["--sysroot=" + str(sysroot)])
    ]) + "\n")
    native.write_text("\n".join([
        "[binaries]", "pkg-config = 'pkg-config'",
        # Restrict the build-machine scanner to its private pkg-config file.
        # Cross-only sys_root must NEVER rewrite this host executable path.
        "[properties]", "pkg_config_libdir = " + repr([str(scanner_pc_dir)]),
        "[built-in options]",
        "pkg_config_path = " + repr([str(scanner_pc_dir)])
    ]) + "\n")
    return cross, native


def scanner_pc_override(folder, actual_scanner):
    # Upstream's .pc contains /usr/bin/wayland-scanner; never look in system
    # /usr/bin and accidentally use a host distribution version instead.
    if (not actual_scanner.is_file() or actual_scanner.is_symlink() or
        not (actual_scanner.stat().st_mode & 0o100)):
        raise ValueError("source-built pinned scanner missing")
    folder.mkdir(mode=0o700)
    pc = folder / "wayland-scanner.pc"
    pc.write_text(
        "prefix=/usr\n"
        "Name: Wayland Scanner\n"
        "Description: Verified native build-time protocol generator\n"
        "Version: 1.26.0\n"
        "wayland_scanner=" + str(actual_scanner) + "\n"
    )
    return pc


def isolated_pkgconfig_env(base):
    """Meson derives separate build/host pkg-config roots from machine files.

    Process-wide PKG_CONFIG_SYSROOT_DIR would rewrite the *host scanner*
    path with the libffi target staging prefix, breaking or contaminating
    the build-machine executable lookup.
    """
    env = dict(base)
    for key in ("CFLAGS", "CXXFLAGS", "LDFLAGS", "CPPFLAGS", "CPATH",
                "LIBRARY_PATH", "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR",
                "PKG_CONFIG_SYSROOT_DIR", "PKG_CONFIG_SYSTEM_INCLUDE_PATH",
                "PKG_CONFIG_SYSTEM_LIBRARY_PATH"):
        env.pop(key, None)
    env["PKG_CONFIG_PATH"] = ""
    return env


def verify_private_scanner_lookup(scanner, scanner_pc_dir):
    """Fail before a costly Meson configure if host scanner is misresolved."""
    if not shutil.which("pkg-config"):
        raise ValueError("pkg-config not available for host scanner preflight")
    env = isolated_pkgconfig_env(os.environ)
    env["PKG_CONFIG_LIBDIR"] = str(scanner_pc_dir)
    output = subprocess.check_output(
        ["pkg-config", "--variable=wayland_scanner", "wayland-scanner"],
        env=env, text=True, timeout=10).strip()
    if output != str(scanner) or not scanner.is_file() or scanner.is_symlink():
        raise ValueError("private host scanner was rewritten or substituted")
    return output


def build_native_wayland(stage, wp, fp):
    tools, sysroot = target_toolchain()
    source = wayland.DEST / "src" / wayland.SRC_ROOT
    scanner = wayland.DEST / "stage/usr/bin/wayland-scanner"
    ffi_stage = ffi.STAGE_ROOT / "stage"
    ffi_pc = find_one_pc(ffi_stage, "libffi.pc")
    verified_ffi = ffi.STAGE_ROOT / "validation.json"
    verified_scanner = wayland.DEST / "validation.json"
    if (not source.is_dir() or source.is_symlink() or
        not verified_ffi.is_file() or not verified_scanner.is_file()):
        raise ValueError("native Wayland prerequisites missing")
    ffi_receipt = json.loads(verified_ffi.read_text())
    scanner_receipt = json.loads(verified_scanner.read_text())
    if (ffi_receipt.get("result") != "native-libffi-staged" or
        ffi_receipt.get("verified_upstream_sha256") != fp["sha256"] or
        scanner_receipt.get("result") != "wayland-host-scanner-staged" or
        scanner_receipt.get("source_sha512") != wp["sha512"]):
        raise ValueError("dependency source receipts not trustworthy")
    scanner_pc = scanner_pc_override(stage / "native-pc", scanner)
    verify_private_scanner_lookup(scanner, scanner_pc.parent)
    cross, native = machine_files(stage, tools, sysroot,
                                  ffi_pc.parent, scanner_pc.parent, ffi_stage)
    # The cross machine file scopes the target sysroot and target libffi.
    # The native machine file scopes the host scanner. Process-global
    # PKG_CONFIG_SYSROOT_DIR would contaminate native scanner paths.
    env = isolated_pkgconfig_env(os.environ)
    build = stage / "build"
    result = stage / "stage"
    subprocess.run(["meson", "setup", str(build), str(source),
        "--cross-file", str(cross), "--native-file", str(native),
        "--prefix=/usr", "--libdir=lib64", "--buildtype=release",
        "--default-library=static", "--wrap-mode=nodownload",
        "-Dlibraries=true", "-Dscanner=false", "-Dtests=false",
        "-Ddocumentation=false", "-Ddtd_validation=false"],
        env=env, check=True)
    subprocess.run(["meson", "compile", "-C", str(build), "-j", "4"],
                   env=env, check=True)
    subprocess.run(["meson", "install", "-C", str(build),
                    "--destdir", str(result)], env=env, check=True)
    server = result / "usr/lib64/libwayland-server.a"
    client = result / "usr/lib64/libwayland-client.a"
    if (not server.is_file() or server.is_symlink() or not client.is_file() or
        client.is_symlink()):
        raise ValueError("Wayland target client/server static archives missing")
    for lib in (server, client):
        if not subprocess.check_output([str(tools["ar"]), "t", str(lib)]).strip():
            raise ValueError("empty target library " + lib.name)
    ffi_archive = ffi_stage / "usr/lib64/libffi.a"
    ffi_header = list(ffi_stage.rglob("ffi.h"))
    if not ffi_archive.is_file() or len(ffi_header) != 1:
        raise ValueError("source-built target libffi link input unavailable")
    # Link client and server *independently* against Synveil glibc/libffi.
    # Combining the client/server headers and archives in one executable can
    # conceal symbol conflicts and does not verify each ABI in isolation.
    readelf = TOOLCHAIN / "bin" / (TARGET + "-readelf")
    smoke_hashes = {}
    for kind, code, archive in (
        ("client", CLIENT_LINK_SOURCE, client),
        ("server", SERVER_LINK_SOURCE, server),
    ):
        smoke = stage / ("wayland-" + kind + "-link-smoke.c")
        target = stage / ("wayland-" + kind + "-link-smoke")
        smoke.write_text(code)
        subprocess.run([str(tools["gcc"]), "--sysroot=" + str(sysroot),
            "-I" + str(result / "usr/include"),
            "-I" + str(ffi_header[0].parent), str(smoke),
            str(archive), str(ffi_archive),
            "-lm", "-lrt", "-ldl", "-pthread", "-o", str(target)],
            env=env, check=True)
        h = subprocess.check_output([str(readelf), "-h", str(target)], text=True)
        p = subprocess.check_output([str(readelf), "-l", str(target)], text=True)
        if ("Advanced Micro Devices X86-64" not in h or
            "/lib64/ld-linux-x86-64.so.2" not in p):
            raise ValueError(kind + " test link is not a native Synveil ELF")
        smoke_hashes[kind] = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt = {
        "result": "native-wayland-client-server-staged",
        "target": TARGET, "version": wp["version"],
        "wayland_source_sha512": wp["sha512"],
        "ffi_source_sha256": fp["sha256"],
        "server_archive_sha256": hashlib.sha256(server.read_bytes()).hexdigest(),
        "client_archive_sha256": hashlib.sha256(client.read_bytes()).hexdigest(),
        "linked_target_elf_sha256": smoke_hashes,
        "native_boot_image_changed": False,
        "native_compositor_integrated": False
    }
    (stage / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--build", action="store_true")
    args = ap.parse_args()
    wp, fp = prerequisites()
    if args.plan:
        print("WAYLAND_TARGET_PLAN_PINNED_" + wp["version"] + "_LIBFFI_" + fp["version"])
        return 0
    if os.geteuid() == 0:
        raise SystemExit("refusing root native graphics build")
    target_toolchain()
    root_bound_output()
    for filename, opt in (("native_libffi.py", "--build"),
                          ("native_wayland.py", "--build-scanner")):
        subprocess.run([sys.executable, str(TOOLS / filename), opt],
                       cwd=ROOT, check=True)
    build_native_wayland(OUT, wp, fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
