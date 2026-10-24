#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Source-verified cross build of libffi; private staging, not a native image."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "build/manifests/native-libffi.json"
STAGE_ROOT = ROOT / "out/native-libffi"
TOOLCHAIN = ROOT / "build/toolchain/out"
TARGET = "x86_64-synveil-linux-gnu"
VERSION = "3.5.2"
DIRNAME = "libffi-" + VERSION
FILENAME = DIRNAME + ".tar.gz"
URL = "https://github.com/libffi/libffi/releases/download/v3.5.2/" + FILENAME
LIMIT = 16 * 1024 * 1024


def source_pin():
    data = json.loads(LOCK.read_text())
    pin = data.get("libffi", {})
    if (data.get("schema") != "synveil.native-libffi/v1" or
        data.get("target") != TARGET or pin.get("version") != VERSION or
        pin.get("url") != URL or pin.get("filename") != FILENAME or
        re.fullmatch(r"[a-f0-9]{64}", pin.get("sha256", "")) is None):
        raise ValueError("untrusted libffi source pin")
    return pin


def checksum(path):
    sha = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def new_stage():
    if ((ROOT / "out").is_symlink() or
            STAGE_ROOT.exists() or STAGE_ROOT.is_symlink()):
        raise ValueError("existing or symlinked libffi output")
    STAGE_ROOT.mkdir(mode=0o700, parents=True)
    STAGE_ROOT.chmod(0o700)
    return STAGE_ROOT


def download_verified(pin, folder):
    dst = folder / FILENAME
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as writer:
            request = urllib.request.Request(URL,
                headers={"User-Agent": "Synveil-native-libffi/0.1"})
            with urllib.request.urlopen(request, timeout=50) as source:
                remaining = LIMIT + 1
                while remaining:
                    data = source.read(min(65536, remaining))
                    if not data:
                        break
                    writer.write(data)
                    remaining -= len(data)
            writer.flush()
            os.fsync(writer.fileno())
        meta = dst.lstat()
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or
            not 0 < meta.st_size <= LIMIT or checksum(dst) != pin["sha256"]):
            raise ValueError("libffi upstream SHA256/size/type mismatch")
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst


def safe_member(member):
    name = PurePosixPath(member.name)
    if (name.is_absolute() or ".." in name.parts or not name.parts or
        name.parts[0] != DIRNAME):
        return False
    if member.isfile() or member.isdir():
        return True
    if not member.issym() or not member.linkname or member.linkname.startswith("/"):
        return False
    target = posixpath.normpath(posixpath.join(
        posixpath.dirname(member.name), member.linkname))
    return target.startswith(DIRNAME + "/") and target != member.name


def extract(archive, destination):
    if destination.is_symlink() or destination.exists():
        raise ValueError("refusing existing extraction target")
    destination.mkdir(mode=0o700)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if not members or len(members) > 20000:
                raise ValueError("invalid release member count")
            for member in members:
                if not safe_member(member):
                    raise ValueError("unsafe source entry " + repr(member.name) +
                                     " type=" + repr(member.type))
            tar.extractall(destination, filter="data")
        root = destination / DIRNAME
        if not (root / "configure").is_file() or not (root / "Makefile.am").is_file():
            raise ValueError("libffi release configure layout missing")
        return root
    except BaseException:
        shutil.rmtree(destination)
        raise


def target_tools():
    root = TOOLCHAIN
    sysroot = root / TARGET / "sysroot"
    marker = root / "SYNVEIL-TOOLCHAIN"
    tools = {k:root / "bin" / (TARGET + "-" + k)
             for k in ("gcc","ar","ranlib","nm","readelf")}
    if (root.is_symlink() or sysroot.is_symlink() or
        not marker.is_file() or
        not marker.read_text().startswith("target=" + TARGET + "\n") or
        not (sysroot / "usr/include/stdio.h").is_file() or
        any(not v.is_file() or v.is_symlink() for v in tools.values())):
        raise ValueError("source-built Synveil cross toolchain required")
    return tools, sysroot


def build_target(source, stage_root, pin):
    tools, sysroot = target_tools()
    dest = stage_root / "stage"
    build = stage_root / "build"
    build.mkdir(mode=0o700)
    env = os.environ.copy()
    for name in ("CFLAGS","LDFLAGS","CPPFLAGS","CPATH","LIBRARY_PATH",
                 "PKG_CONFIG_PATH","CXXFLAGS"):
        env.pop(name, None)
    env.update(CC=str(tools["gcc"]), AR=str(tools["ar"]),
               RANLIB=str(tools["ranlib"]), NM=str(tools["nm"]),
               CFLAGS="--sysroot=" + str(sysroot) + " -O2",
               LDFLAGS="--sysroot=" + str(sysroot),
               PKG_CONFIG_PATH="", LC_ALL="C")
    build_triple = subprocess.check_output(
        ["gcc", "-dumpmachine"], text=True).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.+-]+", build_triple):
        raise ValueError("invalid host compiler identity")
    subprocess.run([str(source / "configure"),
        "--build=" + build_triple, "--host=" + TARGET,
        "--prefix=/usr", "--libdir=/usr/lib64",
        "--includedir=/usr/include", "--disable-shared",
        "--enable-static", "--disable-multi-os-directory"],
        cwd=build, env=env, check=True)
    subprocess.run(["make", "-j4"], cwd=build, env=env, check=True)
    subprocess.run(["make", "install", "DESTDIR=" + str(dest)],
                   cwd=build, env=env, check=True)
    lib = dest / "usr/lib64/libffi.a"
    headers = list(dest.rglob("ffi.h"))
    if (not lib.is_file() or lib.is_symlink() or not headers or
        not any(h.is_file() and not h.is_symlink() for h in headers)):
        raise ValueError("target libffi.a or ffi.h missing from staged output")
    if not subprocess.check_output([str(tools["ar"]), "t", str(lib)]).strip():
        raise ValueError("empty libffi target archive")
    header_dir = next(h.parent for h in headers if h.is_file() and not h.is_symlink())
    smoke = stage_root / "ffi-link-smoke.c"
    exe = stage_root / "ffi-link-smoke"
    smoke.write_text(
        "#include <ffi.h>\n"
        "static int add(int x, int y) { return x + y; }\n"
        "int main(void) {\n"
        "  ffi_cif cif;\n"
        "  ffi_type *types[2] = { &ffi_type_sint32, &ffi_type_sint32 };\n"
        "  int x=1, y=2, result=0;\n"
        "  void *args[2] = { &x, &y };\n"
        "  if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_sint32, types) != FFI_OK) return 1;\n"
        "  ffi_call(&cif, FFI_FN(add), &result, args);\n"
        "  return result == 3 ? 0 : 2;\n"
        "}\n")
    subprocess.run([str(tools["gcc"]), "--sysroot=" + str(sysroot),
                    "-I" + str(header_dir), str(smoke), str(lib),
                    "-o", str(exe)], env=env, check=True)
    hdr = subprocess.check_output([str(tools["readelf"]), "-h", str(exe)], text=True)
    program = subprocess.check_output([str(tools["readelf"]), "-l", str(exe)], text=True)
    if ("Advanced Micro Devices X86-64" not in hdr or
        "/lib64/ld-linux-x86-64.so.2" not in program):
        raise ValueError("linked smoke is not a Synveil ELF target")
    receipt = {
        "result": "native-libffi-staged",
        "version": pin["version"], "target": TARGET,
        "verified_upstream_sha256": pin["sha256"],
        "target_static_archive_sha256": checksum(lib),
        "target_link_smoke": "ELF verified, not executed",
        "native_boot_image_changed": False,
        "compositor_integrated": False
    }
    (stage_root / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-lock", action="store_true")
    modes.add_argument("--verify-source", action="store_true")
    modes.add_argument("--build", action="store_true")
    args = parser.parse_args()
    pin = source_pin()
    if args.check_lock:
        print("LIBFFI_SOURCE_LOCK_OK")
        return 0
    if os.geteuid() == 0:
        raise SystemExit("refusing root source staging")
    folder = new_stage()
    archive = download_verified(pin, folder)
    print("LIBFFI_UPSTREAM_SHA256_VERIFIED")
    source = extract(archive, folder / "source")
    print("LIBFFI_UPSTREAM_EXTRACT_VERIFIED")
    if args.verify_source:
        return 0
    return build_target(source, folder, pin)


if __name__ == "__main__":
    raise SystemExit(main())
