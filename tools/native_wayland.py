#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Source-pinned Wayland host scanner prerequisite; not native Wayland libraries."""
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
LOCK = ROOT / "build/manifests/native-wayland.json"
DEST = ROOT / "out/native-wayland-scanner"
SRC_ROOT = "wayland-1.26.0"
URL = ("https://gitlab.freedesktop.org/wayland/wayland/-/releases/"
       "1.26.0/downloads/wayland-1.26.0.tar.xz")
TARGET = "x86_64-synveil-linux-gnu"
MAX_SIZE = 8 * 1024 * 1024


def release_spec():
    data = json.loads(LOCK.read_text())
    item = data.get("wayland", {})
    if (data.get("schema") != "synveil.native-wayland/v1" or
        data.get("target") != TARGET or data.get("component") != "host-scanner" or
        item.get("version") != "1.26.0" or
        item.get("filename") != SRC_ROOT + ".tar.xz" or
        item.get("url") != URL or
        not re.fullmatch("[0-9a-f]{128}", item.get("sha512", ""))):
        raise ValueError("untrusted Wayland release lock")
    return item


def archive_member_is_safe(item):
    name = PurePosixPath(item.name)
    if (name.is_absolute() or ".." in name.parts or not name.parts or
        name.parts[0] != SRC_ROOT):
        return False
    if item.isfile() or item.isdir():
        return True
    if not item.issym() or not item.linkname or item.linkname.startswith("/"):
        return False
    target = posixpath.normpath(posixpath.join(posixpath.dirname(item.name),
                                             item.linkname))
    return target.startswith(SRC_ROOT + "/") and target != item.name


def create_output():
    if (ROOT / "out").is_symlink() or DEST.exists() or DEST.is_symlink():
        raise ValueError("refusing existing or symlinked Wayland staging")
    DEST.mkdir(parents=True, mode=0o700)
    DEST.chmod(0o700)
    return DEST


def sha_file(path, algo):
    hasher = hashlib.new(algo)
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(data)
    return hasher.hexdigest()


def fetch_source(item, out):
    archive = out / item["filename"]
    fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as writer:
            request = urllib.request.Request(
                item["url"], headers={"User-Agent": "Synveil-Wayland-build/0.1"})
            with urllib.request.urlopen(request, timeout=45) as remote:
                remaining = MAX_SIZE + 1
                while remaining:
                    block = remote.read(min(131072, remaining))
                    if not block:
                        break
                    writer.write(block)
                    remaining -= len(block)
            writer.flush()
            os.fsync(writer.fileno())
        info = archive.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            not 0 < info.st_size <= MAX_SIZE or
            sha_file(archive, "sha512") != item["sha512"]):
            raise ValueError("Wayland release digest, type or length mismatch")
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    return archive


def extract_source(archive, out):
    out.mkdir(mode=0o700)
    try:
        with tarfile.open(archive, "r:xz") as tar:
            members = tar.getmembers()
            if not members or len(members) > 20000:
                raise ValueError("unsafe Wayland source member count")
            for item in members:
                if not archive_member_is_safe(item):
                    raise ValueError("unsafe Wayland entry " + repr(item.name))
            tar.extractall(out, filter="data")
        src = out / SRC_ROOT
        if not (src / "meson.build").is_file() or not (src / "meson_options.txt").is_file():
            raise ValueError("Wayland source layout unexpected")
        return src
    except BaseException:
        shutil.rmtree(out)
        raise


def generated_protocol_is_valid(source, *, kind):
    """Reject truncated/misaddressed protocol output, not just a running scanner."""
    if len(source) < 4096 or len(source) > 4 * 1024 * 1024:
        return False
    required = (b"wl_display_interface", b"wl_registry_interface",
                b"wl_compositor_interface")
    if not all(symbol in source for symbol in required):
        return False
    if kind == "code":
        return b"struct wl_interface" in source
    if kind in ("client", "server"):
        return b"wl_interface" in source
    return False


def generate_protocol_proof(scanner, source, out):
    """Run the actual pinned scanner on the pinned core Wayland XML."""
    protocol = source / "protocol/wayland.xml"
    if not protocol.is_file() or protocol.is_symlink():
        raise ValueError("canonical Wayland XML unavailable")
    generated = out / "generated-protocol"
    generated.mkdir(mode=0o700)
    hashes = {}
    for kind, mode, suffix in (
        ("client", "client-header", "h"),
        ("server", "server-header", "h"),
        ("code", "private-code", "c"),
    ):
        target = generated / ("wayland-" + kind + "." + suffix)
        subprocess.run([str(scanner), mode, str(protocol), str(target)],
                       check=True, timeout=25)
        payload = target.read_bytes()
        if not generated_protocol_is_valid(payload, kind=kind):
            raise ValueError("scanner generated incomplete " + kind + " protocol")
        hashes[kind] = sha_file(target, "sha256")
    return hashes


def build_host_scanner(src, out, item):
    for cmd in ("meson", "ninja"):
        if not shutil.which(cmd):
            raise ValueError("missing host scanner build command: " + cmd)
    env = os.environ.copy()
    for key in ("CFLAGS", "CXXFLAGS", "LDFLAGS", "CPPFLAGS",
                "PKG_CONFIG_PATH", "CPATH", "LIBRARY_PATH"):
        env.pop(key, None)
    build = out / "meson-build"
    stage = out / "stage"
    subprocess.run(["meson", "setup", str(build), str(src),
        "--prefix=/usr", "--buildtype=release", "--wrap-mode=nodownload",
        "-Dlibraries=false", "-Dscanner=true", "-Dtests=false",
        "-Ddocumentation=false", "-Ddtd_validation=false"], env=env, check=True)
    subprocess.run(["meson", "compile", "-C", str(build), "-j", "4"],
                   env=env, check=True)
    subprocess.run(["meson", "install", "-C", str(build), "--destdir", str(stage)],
                   env=env, check=True)
    scanner = stage / "usr/bin/wayland-scanner"
    if (not scanner.is_file() or scanner.is_symlink() or
        not (scanner.stat().st_mode & stat.S_IXUSR)):
        raise ValueError("host scanner binary absent")
    generated_hashes = generate_protocol_proof(scanner, src, out)
    # This is a host-tool build only. Native C libraries are not installed.
    if list(stage.rglob("libwayland-*.so*")):
        raise ValueError("unexpected target Wayland library in scanner stage")
    receipt = {
        "result": "wayland-host-scanner-staged",
        "version": item["version"],
        "source_sha512": item["sha512"],
        "scanner_sha256": sha_file(scanner, "sha256"),
        "generated_protocol_sha256": generated_hashes,
        "target_libraries_built": False,
        "native_boot_image_changed": False,
        "owner_device_modified": False
    }
    (out / "validation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-lock", action="store_true")
    mode.add_argument("--verify-source", action="store_true")
    mode.add_argument("--build-scanner", action="store_true")
    args = parser.parse_args()
    item = release_spec()
    if args.check_lock:
        print("WAYLAND_SOURCE_PIN_OK")
        return 0
    if os.geteuid() == 0:
        raise SystemExit("refusing privileged Wayland source preparation")
    out = create_output()
    archive = fetch_source(item, out)
    print("WAYLAND_SOURCE_SHA512_VERIFIED")
    src = extract_source(archive, out / "src")
    print("WAYLAND_SOURCE_EXTRACTION_VERIFIED")
    if args.verify_source:
        return 0
    build_host_scanner(src, out, item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
