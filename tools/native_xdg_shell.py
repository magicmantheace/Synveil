#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Pinned xdg-shell C protocol generation; no native compositor integration."""
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
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "build/manifests/native-xdg-shell.json"
OUT = ROOT / "out/native-xdg-shell"
WAYLAND_OUT = ROOT / "out/native-wayland-scanner"
PREFIX = "wayland-protocols-1.49"
URL = ("https://gitlab.freedesktop.org/wayland/wayland-protocols/-/"
       "releases/1.49/downloads/wayland-protocols-1.49.tar.xz")
REQUIRED = ("xdg_wm_base", "xdg_surface", "xdg_toplevel")


def pin():
    data = json.loads(PIN.read_text())
    item = data.get("release", {})
    if (data.get("schema") != "synveil.xdg-shell/v1" or
        data.get("target") != "x86_64-synveil-linux-gnu" or
        item.get("version") != "1.49" or
        item.get("filename") != PREFIX + ".tar.xz" or
        item.get("url") != URL or
        not re.fullmatch("[0-9a-f]{128}", item.get("sha512", ""))):
        raise ValueError("untrusted xdg-shell protocol pin")
    return item


def digest(path, algo="sha512"):
    h = hashlib.new(algo)
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def member_safe(item):
    name = PurePosixPath(item.name)
    if (name.is_absolute() or ".." in name.parts or
        not name.parts or name.parts[0] != PREFIX):
        return False
    if item.isfile() or item.isdir():
        return True
    if not item.issym() or not item.linkname or item.linkname.startswith("/"):
        return False
    resolved = posixpath.normpath(posixpath.join(
        posixpath.dirname(item.name), item.linkname))
    return resolved.startswith(PREFIX + "/") and resolved != item.name


def xdg_xml(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1048576:
        raise ValueError("missing or unsafe xdg-shell.xml")
    root = ET.parse(path).getroot()
    if root.tag != "protocol" or root.get("name") != "xdg_shell":
        raise ValueError("unexpected Wayland protocol")
    interfaces = {item.get("name"): item for item in root.findall("interface")}
    if not all(k in interfaces for k in REQUIRED):
        raise ValueError("missing window protocol interfaces")
    if not any(e.get("name") == "configure" for e in
               interfaces["xdg_toplevel"].findall("event")):
        raise ValueError("no xdg_toplevel configure event")


def extract_archive(archive, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("existing source staging")
    destination.mkdir(mode=0o700)
    try:
        with tarfile.open(archive, "r:xz") as tar:
            entries = tar.getmembers()
            if not entries or len(entries) > 10000 or any(
                    not member_safe(item) for item in entries):
                raise ValueError("unsafe protocol archive entry")
            tar.extractall(destination, filter="data")
        xml = destination / PREFIX / "stable/xdg-shell/xdg-shell.xml"
        xdg_xml(xml)
        return xml
    except BaseException:
        shutil.rmtree(destination)
        raise


def fetch_verified(info, dest):
    file = dest / info["filename"]
    fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            req = urllib.request.Request(URL, headers={
                "User-Agent": "Synveil-xdg-shell-source/0.1"})
            with urllib.request.urlopen(req, timeout=45) as response:
                remaining = 4 * 1024 * 1024 + 1
                while remaining:
                    buf = response.read(min(65536, remaining))
                    if not buf:
                        break
                    output.write(buf)
                    remaining -= len(buf)
        meta = file.lstat()
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or
            not 0 < meta.st_size <= 4 * 1024 * 1024 or
            digest(file) != info["sha512"]):
            raise ValueError("xdg-shell upstream digest mismatch")
    except BaseException:
        file.unlink(missing_ok=True)
        raise
    return file


def generate(xml, dest):
    scanner = WAYLAND_OUT / "stage/usr/bin/wayland-scanner"
    receipt = WAYLAND_OUT / "validation.json"
    if (not scanner.is_file() or scanner.is_symlink() or
        not scanner.stat().st_mode & stat.S_IXUSR or
        not receipt.is_file() or receipt.is_symlink()):
        raise ValueError("private Wayland scanner missing")
    evidence = json.loads(receipt.read_text())
    if (evidence.get("result") != "wayland-host-scanner-staged" or
        evidence.get("version") != "1.26.0" or
        evidence.get("scanner_sha256") != digest(scanner, "sha256")):
        raise ValueError("scanner provenance mismatch")
    dest.mkdir(mode=0o700)
    checksums = {}
    for role, command, suffix in (
        ("client", "client-header", "h"),
        ("server", "server-header", "h"),
        ("code", "private-code", "c"),
    ):
        target = dest / ("xdg-shell-" + role + "." + suffix)
        subprocess.run([str(scanner), command, str(xml), str(target)],
                       check=True, timeout=20)
        payload = target.read_bytes()
        if (not 4000 < len(payload) < 3 * 1024 * 1024 or
            any((name + "_interface").encode() not in payload
                for name in REQUIRED)):
            raise ValueError("incomplete " + role + " xdg-shell generation")
        checksums[role] = digest(target, "sha256")
    return checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    opt = parser.add_mutually_exclusive_group(required=True)
    opt.add_argument("--check-lock", action="store_true")
    opt.add_argument("--generate", action="store_true")
    opt.add_argument("--generate-existing-scanner", action="store_true")
    args = parser.parse_args()
    info = pin()
    if args.check_lock:
        print("SOURCE_PINNED_XDG_SHELL_OK")
        return 0
    if os.geteuid() == 0:
        raise SystemExit("refusing root protocol generation")
    if (ROOT / "out").is_symlink() or OUT.is_symlink() or OUT.exists():
        raise ValueError("existing/symlinked xdg protocol stage")
    OUT.mkdir(parents=True, mode=0o700)
    xml = extract_archive(fetch_verified(info, OUT), OUT / "source")
    if args.generate_existing_scanner:
        # Reuse the same already-verified target-build scanner: generate()
        # verifies its receipt, version, binary checksum and fixed location.
        if not WAYLAND_OUT.is_dir() or WAYLAND_OUT.is_symlink():
            raise ValueError("expected private staged scanner is missing")
    else:
        if WAYLAND_OUT.exists() or WAYLAND_OUT.is_symlink():
            raise ValueError("Wayland scanner staging already exists")
        subprocess.run([sys.executable, str(ROOT / "tools/native_wayland.py"),
                        "--build-scanner"], cwd=ROOT, check=True)
    hashes = generate(xml, OUT / "generated")
    proof = {"result": "xdg-shell-generated", "version": info["version"],
             "source_sha512": info["sha512"],
             "interfaces": list(REQUIRED),
             "generated_sha256": hashes, "native_compositor_integrated": False,
             "native_boot_image_changed": False}
    (OUT / "validation.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
