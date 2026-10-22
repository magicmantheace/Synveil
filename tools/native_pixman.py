#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verified Pixman source for isolated native cross-toolchain staging only."""
import argparse
import hashlib
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "build/manifests/native-graphics.json"
OUT = ROOT / "out/native-desktop"
TC = ROOT / "build/toolchain/out"
TARGET = "x86_64-synveil-linux-gnu"
URL = "https://cairographics.org/releases/pixman-0.46.4.tar.xz"


def pin():
    data = json.loads(LOCK.read_text())
    p = data.get("pixman", {})
    if (data.get("schema") != "synveil.native-graphics/v1" or
        data.get("target") != TARGET or p.get("version") != "0.46.4" or
        p.get("filename") != "pixman-0.46.4.tar.xz" or p.get("url") != URL or
        re.fullmatch("[a-f0-9]{128}", p.get("sha512", "")) is None):
        raise ValueError("untrusted native graphics pin")
    return p


def file_hash(path, alg):
    hasher = hashlib.new(alg)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def only_new_output():
    if (ROOT / "out").is_symlink() or OUT.is_symlink() or OUT.exists():
        raise ValueError("unsafe native graphics staging destination")
    OUT.mkdir(parents=True, mode=0o700)
    return OUT


def verified_archive(p, out):
    target = out / p["filename"]
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as dest:
            req = urllib.request.Request(URL, headers={"User-Agent": "Synveil-native/1"})
            with urllib.request.urlopen(req, timeout=45) as src:
                remaining = 16 * 1024 * 1024 + 1
                while remaining > 0:
                    block = src.read(min(remaining, 65536))
                    if not block:
                        break
                    dest.write(block)
                    remaining -= len(block)
        st = target.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or
            st.st_size > 16 * 1024 * 1024 or
            file_hash(target, "sha512") != p["sha512"]):
            raise ValueError("Pixman digest/size verification failed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def safe_archive_member(item):
    """Refuse any archive entry or link which can reach outside the source root.

    A published Pixman tarball contains internal symlinks in .gitlab-ci.d.
    This check treats their targets as relative to the link's parent and
    normalizes .. before confirming the same fixed archive root.
    """
    name = PurePosixPath(item.name)
    root = "pixman-0.46.4"
    if (name.is_absolute() or ".." in name.parts or not name.parts or
        name.parts[0] != root):
        return False
    if item.isfile() or item.isdir():
        return True
    if not item.issym() or not item.linkname or item.linkname.startswith("/"):
        return False
    # A relative link target must stay under the *same* source root.
    # This also refuses self-references to '.', and never permits a link to
    # the root directory itself as a future parent of another archive item.
    target = posixpath.normpath(
        posixpath.join(posixpath.dirname(item.name), item.linkname)
    )
    return target.startswith(root + "/") and target != item.name


def extract(source, folder):
    folder.mkdir(mode=0o700)
    try:
        with tarfile.open(source, "r:xz") as tar:
            entries = tar.getmembers()
            if not entries or len(entries) > 12000:
                raise ValueError("unexpected Pixman tar")
            for item in entries:
                if not safe_archive_member(item):
                    raise ValueError(f"unsafe archive entry: {item.name!r} type={item.type!r}")
            tar.extractall(folder, filter="data")
        result = folder / "pixman-0.46.4"
        if not (result / "meson.build").is_file():
            raise ValueError("Meson source missing")
        return result
    except BaseException:
        shutil.rmtree(folder)
        raise


def cross_build(p, source, out):
    sysroot = TC / TARGET / "sysroot"
    marker = TC / "SYNVEIL-TOOLCHAIN"
    bins = {x: TC / "bin" / (TARGET + "-" + x) for x in ("gcc", "ar", "strip")}
    if (TC.is_symlink() or sysroot.is_symlink() or
        not marker.is_file() or not marker.read_text().startswith("target=" + TARGET + "\n") or
        not (sysroot / "usr/include/stdio.h").is_file() or
        any(not x.is_file() or x.is_symlink() for x in bins.values())):
        raise ValueError("Synveil source-built cross toolchain required")
    if not shutil.which("meson") or not shutil.which("ninja"):
        raise ValueError("Meson and Ninja are required")
    cross = out / "cross.ini"
    cross.write_text("\n".join([
        "[binaries]", "c = " + repr(str(bins["gcc"])),
        "ar = " + repr(str(bins["ar"])),
        "strip = " + repr(str(bins["strip"])),
        "[host_machine]", "system = 'linux'", "cpu_family = 'x86_64'",
        "cpu = 'x86_64'", "endian = 'little'",
        "[properties]", "needs_exe_wrapper = true", "sys_root = " + repr(str(sysroot)),
        "[built-in options]", "c_args = " + repr(["--sysroot=" + str(sysroot)]),
        "c_link_args = " + repr(["--sysroot=" + str(sysroot)])]) + "\n")
    env = os.environ.copy()
    for k in ("CFLAGS", "LDFLAGS", "CPATH", "CPPFLAGS", "LIBRARY_PATH"):
        env.pop(k, None)
    env["PKG_CONFIG_PATH"] = ""
    env["PKG_CONFIG_LIBDIR"] = ":".join(str(sysroot / x) for x in (
        "usr/lib64/pkgconfig", "usr/lib/pkgconfig", "usr/share/pkgconfig"))
    env["PKG_CONFIG_SYSROOT_DIR"] = str(sysroot)
    b, stage = out / "meson-build", out / "stage"
    subprocess.run(["meson", "setup", str(b), str(source), "--cross-file", str(cross),
        "--prefix=/usr", "--libdir=lib64", "--default-library=static",
        "--buildtype=release", "--wrap-mode=nodownload", "-Dtests=disabled",
        "-Ddemos=disabled", "-Dgtk=disabled", "-Dlibpng=disabled",
        "-Dopenmp=disabled"], env=env, check=True)
    subprocess.run(["meson", "compile", "-C", str(b), "-j", "4"], env=env, check=True)
    subprocess.run(["meson", "install", "-C", str(b), "--destdir", str(stage)],
                   env=env, check=True)
    lib = stage / "usr/lib64/libpixman-1.a"
    if not lib.is_file() or not (stage / "usr/include/pixman-1/pixman.h").is_file():
        raise ValueError("missing target Pixman static library")
    if not subprocess.check_output([str(bins["ar"]), "t", str(lib)]).strip():
        raise ValueError("empty target library")
    # The target-specific archive also has to link into a real x86_64 Synveil
    # executable using its own libc, not the host runner's native compiler.
    smoke_source = out / "pixman-link-smoke.c"
    smoke_executable = out / "pixman-link-smoke"
    smoke_source.write_text(
        "#include <pixman.h>\n"
        "int main(void) {\n"
        "  pixman_color_t color = {65535, 0, 0, 65535};\n"
        "  pixman_image_t *p = pixman_image_create_solid_fill(&color);\n"
        "  if (!p) return 1;\n"
        "  pixman_image_unref(p);\n"
        "  return 0;\n"
        "}\n"
    )
    subprocess.run([str(bins["gcc"]), "--sysroot=" + str(sysroot),
                    "-I" + str(stage / "usr/include/pixman-1"),
                    str(smoke_source), str(lib), "-lm", "-pthread",
                    "-o", str(smoke_executable)], env=env, check=True)
    readelf = TC / "bin" / (TARGET + "-readelf")
    if not readelf.is_file() or readelf.is_symlink():
        raise ValueError("Synveil target ELF verifier missing")
    headers = subprocess.check_output(
        [str(readelf), "-h", str(smoke_executable)], text=True
    )
    program = subprocess.check_output(
        [str(readelf), "-l", str(smoke_executable)], text=True
    )
    if ("Machine:" not in headers or "Advanced Micro Devices X86-64" not in headers
            or "/lib64/ld-linux-x86-64.so.2" not in program):
        raise ValueError("Pixman smoke executable is not a Synveil target ELF")
    receipt = {"result": "native-pixman-staged", "target": TARGET,
               "source_sha512": p["sha512"], "library_sha256": file_hash(lib, "sha256"),
               "compositor_integrated": False, "boot_image_changed": False}
    (out / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-lock", action="store_true")
    modes.add_argument("--verify-source", action="store_true")
    modes.add_argument("--build", action="store_true")
    a = ap.parse_args()
    p = pin()
    if a.check_lock:
        print("NATIVE_PIXMAN_PIN_OK")
        return 0
    if os.geteuid() == 0:
        raise SystemExit("refusing privileged Pixman builder")
    out = only_new_output()
    source = verified_archive(p, out)
    print("NATIVE_PIXMAN_SHA512_VERIFIED")
    # Extract in the cheap provenance gate so source-layout failures cannot
    # squander a full pinned GCC/glibc rebuild before being diagnosed.
    verified_tree = extract(source, out / "src")
    print("NATIVE_PIXMAN_ARCHIVE_EXTRACTION_VERIFIED")
    if a.verify_source:
        return 0
    return cross_build(p, verified_tree, out)


if __name__ == "__main__":
    raise SystemExit(main())
