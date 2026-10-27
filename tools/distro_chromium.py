#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Distribution-Chromium stage: pinned Debian snapshot in,
sealed browser payload out.

The owner's direction (2026-10-26) is a prebuilt distribution
Chromium — not a from-source build (out of scope for this build
system) and not the lightweight fallback engine (NetSurf, kept in
tools/native_browser.py). This builder:

  1. resolves the install set (chromium, ca-certificates,
     libnss3-tools) with apt against snapshot.debian.org at the
     timestamp pinned in build/manifests/distro-chromium.json,
     starting from an EMPTY dpkg status so the closure is the
     full runtime closure, never the host's installed set;
  2. verifies the resolved closure equals the manifest's pin set
     exactly (name + version), and every downloaded .deb's sha256
     and size equal both the manifest pin and the snapshot's own
     Packages index entry — a moved snapshot, a substituted
     package, or an extra package all fail the build;
  3. extracts the .debs and prunes to the real payload: the
     DT_NEEDED graph rooted at the Chromium binaries, the named
     dlopen sets (NSS modules, gdk-pixbuf loaders, gio modules,
     mesa DRI), and the data trees the seal allows. The
     distribution's libc and system directories never enter the
     payload — the image runs the project's own glibc;
  4. generates what maintainer scripts would have produced
     (the CA bundle, the GLib schema cache) with recorded,
     deterministic content;
  5. seals the payload (tools/distro_chromium_assets.py) and
     writes validation.json.

Output identities: Chromium 156.0.8078.47-1~deb13u1 (Debian 13),
snapshot 20261024T000000Z. The .deb cache and staging live under
out/distro-chromium/ and are never committed.
"""
import argparse
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import distro_chromium_assets as assets
import native_libffi as ffi

OUT = ROOT / "out" / "distro-chromium"
LOCK = ROOT / "build" / "manifests" / "distro-chromium.json"

# Libraries the baseline rootfs already provides at compatible or
# newer versions (project glibc 2.44 vs Debian 13's 2.41; GCC 16
# runtime vs Debian's GCC 14): the graph treats these sonames as
# satisfied so the distribution copies are pruned.
BASELINE_SONAMES = {
    "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0",
    "librt.so.1", "libresolv.so.2", "libgcc_s.so.1", "libstdc++.so.6",
    "ld-linux-x86-64.so.2",
}
# dlopen-only payload members the DT_NEEDED graph cannot see.
FORCE_KEEP_DIRS = (
    "usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0",
    "usr/lib/x86_64-linux-gnu/gio",
    "usr/lib/x86_64-linux-gnu/gtk-3.0",
    "usr/lib/x86_64-linux-gnu/dri",
    "usr/lib/x86_64-linux-gnu/ossl-modules",
)
FORCE_KEEP_NAMES = {
    "libsoftokn3.so", "libnssckbi.so", "libnssdbm3.so",
    "libfreebl3.so", "libfreeblpriv3.so",
}
GRAPH_ROOTS = (
    "usr/lib/chromium/chromium",
    "usr/lib/chromium/chrome_crashpad_handler",
    "usr/bin/certutil",
)


def source_pin():
    data = json.loads(LOCK.read_text())
    if data.get("schema") != "synveil.distro-chromium/v1" \
            or data.get("target") != ffi.TARGET:
        raise ValueError("unreviewed distro-chromium pin manifest")
    pins = data.get("packages") or []
    if len(pins) < 100 or not data.get("chromium_version"):
        raise ValueError("distro-chromium pin manifest looks truncated")
    return data


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def resolve_closure(pin):
    """apt update + download-only against the pinned snapshot,
    from an empty dpkg status. Returns the .deb paths."""
    apt = OUT / "apt"
    (apt / "state/lists/partial").mkdir(parents=True, exist_ok=True)
    (apt / "cache/archives/partial").mkdir(parents=True, exist_ok=True)
    (apt / "etc").mkdir(parents=True, exist_ok=True)
    (apt / "status").touch()
    sources = apt / "sources.list"
    sources.write_text("".join(
        f"deb [trusted=yes] {a['url']} {a['suite']} main\n"
        for a in pin["archives"]))
    opts = [
        "-o", f"Dir::State={apt}/state",
        "-o", f"Dir::Cache={apt}/cache",
        "-o", f"Dir::Etc::sourcelist={sources}",
        "-o", "Dir::Etc::sourceparts=-",
        "-o", f"Dir::Etc::dpkg={apt}/etc",
        "-o", f"Dir::State::status={apt}/status",
        "-o", "APT::Architecture=amd64",
        "-o", "Acquire::Languages=none",
        "-o", "APT::Sandbox::User=root",
    ]
    env = dict(os.environ, LC_ALL="C")
    run(["apt-get", *opts, "update"], env=env,
        stdout=subprocess.DEVNULL)
    run(["apt-get", *opts, "-y", "--download-only",
         "--no-install-recommends", "install", *pin["install"]], env=env,
        stdout=subprocess.DEVNULL)
    return sorted((apt / "cache/archives").glob("*.deb")), apt


def packages_index(apt):
    """filename -> pin-shaped entry, from the fetched indices."""
    index = {}
    for path in (apt / "state/lists").glob("*_Packages*"):
        raw = path.read_bytes()
        if path.suffix == ".xz":
            raw = lzma.decompress(raw)
        elif path.suffix == ".gz":
            raw = gzip.decompress(raw)
        for stanza in raw.decode("utf-8", "replace").split("\n\n"):
            fields, key = {}, None
            for line in stanza.splitlines():
                if line.startswith(" ") and key:
                    fields[key] += "\n" + line
                elif ": " in line:
                    key, _, value = line.partition(": ")
                    fields[key] = value
            if "Package" in fields and "SHA256" in fields:
                index[fields["Filename"]] = {
                    "package": fields["Package"],
                    "version": fields["Version"],
                    "filename": fields["Filename"],
                    "sha256": fields["SHA256"],
                    "size": int(fields["Size"]),
                }
    return index


def verify_closure(pin, debs, apt):
    index = packages_index(apt)
    # Identity is content (package, version, sha256, size): the
    # same .deb can appear under pool/main and pool/updates paths
    # in different archives, and the filename is not the identity.
    pins = {(p["package"], p["version"], p["sha256"], p["size"])
            for p in pin["packages"]}
    seen = {}
    for deb in debs:
        data = deb.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        entry = next((e for e in index.values()
                      if e["sha256"] == sha and e["size"] == len(data)),
                     None)
        if entry is None:
            raise ValueError(f"{deb.name}: not in the snapshot index")
        identity = (entry["package"], entry["version"], sha, len(data))
        if identity not in pins:
            raise ValueError(f"{deb.name}: closure drifted from the "
                             f"pin manifest ({identity})")
        seen[entry["filename"]] = entry
    if len(seen) != len(pins):
        raise ValueError("closure does not equal the pin manifest")
    return seen


def extract_all(debs, raw):
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    for deb in debs:
        run(["dpkg-deb", "-x", str(deb), str(raw)],
            stdout=subprocess.DEVNULL)


def _elf_needed(readelf, path):
    try:
        out = subprocess.check_output(
            [str(readelf), "-d", str(path)], text=True,
            stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None  # not a dynamic ELF
    needed = []
    soname = None
    for line in out.splitlines():
        if "NEEDED" in line:
            needed.append(line.split("[", 1)[1].rstrip("]\n"))
        if "SONAME" in line:
            soname = line.split("[", 1)[1].rstrip("]\n")
    return needed, soname


def prune(raw, staging, readelf):
    """Copy the payload subset of raw into staging."""
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    multiarch = raw / "usr/lib/x86_64-linux-gnu"
    # soname -> raw path, over the two library trees
    providers = {}
    for tree in (raw / "usr/lib/chromium", multiarch):
        for path in tree.rglob("*"):
            if path.is_file() and not path.is_symlink():
                info = _elf_needed(readelf, path)
                if info and info[1]:
                    providers[info[1]] = path
                providers.setdefault(path.name, path)
    keep = set()

    def keep_file(path):
        if path in keep or not path.exists():
            return
        keep.add(path)
        info = _elf_needed(readelf, path) if path.is_file() \
            and not path.is_symlink() else None
        if info:
            for soname in info[0]:
                if soname in BASELINE_SONAMES:
                    continue
                target = providers.get(soname)
                if target is not None:
                    keep_file(target)

    for relative in GRAPH_ROOTS:
        keep_file(raw / relative)
    for path in (raw / "usr/lib/chromium").rglob("*"):
        if path.is_file() and (path.suffix == ".so" or ".so." in path.name):
            keep_file(path)
    for dirname in FORCE_KEEP_DIRS:
        for path in (raw / dirname).rglob("*"):
            if path.is_file():
                keep_file(path)
    for path in multiarch.iterdir():
        if path.name in FORCE_KEEP_NAMES and path.exists():
            keep_file(path)
    # Data trees and binaries wholesale (minus docs/man/info).
    copied = 0
    for path in sorted(raw.rglob("*")):
        relative = path.relative_to(raw).as_posix()
        if path.is_dir():
            continue
        if relative.startswith(("usr/share/doc/", "usr/share/man/",
                                "usr/share/info/", "usr/share/locale/")):
            continue
        in_kept_libs = path in keep
        # The browser's own tree carries non-ELF runtime resources
        # (icudtl.dat, *.pak, snapshot blobs, locales, the
        # SwiftShader ICD json) the DT_NEEDED graph cannot see;
        # excluding the lib prefixes from data copying dropped
        # them and the browser died at ICU init. Non-ELF files
        # under usr/lib/chromium/ are data, copied wholesale.
        in_browser_data = relative.startswith("usr/lib/chromium/") \
            and not path.is_symlink() \
            and _elf_needed(readelf, path) is None
        in_data = any(relative.startswith(prefix)
                      for prefix in assets.DATA
                      if prefix not in ("usr/lib/chromium/",
                                        "usr/lib/x86_64-linux-gnu/")) \
            or relative in assets.BINARIES or relative in assets.GENERATED \
            or in_browser_data
        if not (in_kept_libs or in_data):
            continue
        dest = staging / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            dest.symlink_to(path.readlink())
        else:
            shutil.copy2(path, dest)
        copied += 1
    # Symlinks into kept library files (soname links) whose targets
    # were kept: recreate any the copy step skipped.
    for path in sorted(multiarch.rglob("*")):
        if not path.is_symlink():
            continue
        target = (path.parent / path.readlink())
        if target in keep:
            dest = staging / path.relative_to(raw).as_posix()
            if not dest.exists() and not dest.is_symlink():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(path.readlink())
                copied += 1
    return copied


def generate_derived(staging):
    """What the packages' maintainer scripts would have made."""
    notes = []
    mozilla = staging / "usr/share/ca-certificates/mozilla"
    bundle = staging / "etc/ssl/certs/ca-certificates.crt"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with bundle.open("wb") as out:
        for cert in sorted(mozilla.glob("*.crt")):
            out.write(cert.read_bytes())
    bundle.chmod(0o644)  # the browser (uid 1000) must read its trust bundle
    notes.append("ca-certificates.crt generated from the sealed "
                 "mozilla bundle (sorted concatenation)")
    schemas = staging / "usr/share/glib-2.0/schemas"
    if schemas.is_dir() and any(schemas.glob("*.gschema.xml")):
        tool = ROOT / "out/native-browser/build/host/bin/glib-compile-schemas"
        if not tool.is_file():
            tool = Path(shutil.which("glib-compile-schemas") or "")
        if not tool.is_file():
            raise ValueError("no glib-compile-schemas available for "
                             "the payload schema cache")
        run([str(tool), str(schemas)])
        compiled = schemas / "gschemas.compiled"
        if compiled.is_file():
            compiled.chmod(0o644)
        notes.append("gschemas.compiled generated for the payload")
    return notes


def build():
    pin = source_pin()
    if os.geteuid() == 0:
        raise ValueError("refusing privileged distro-chromium build")
    OUT.mkdir(parents=True, mode=0o700, exist_ok=True)
    debs, apt = resolve_closure(pin)
    seen = verify_closure(pin, debs, apt)
    tools, _sysroot = ffi.target_tools()
    raw = OUT / "staging-raw"
    staging = OUT / "staging"
    extract_all(debs, raw)
    copied = prune(raw, staging, tools["readelf"])
    notes = generate_derived(staging)
    entries = assets.seal(staging)
    if len(entries) < 200:
        raise ValueError(f"payload seal implausibly small: {len(entries)}")
    # The payload is system data the unprivileged browser reads:
    # nothing in it may be sealed without other-read (a generated
    # bundle once landed 0660 under the workspace umask and the
    # browser could not read its own trust store).
    unreadable = [name for name, e in entries.items()
                  if e["kind"] == "file" and not int(e["mode"], 8) & 0o004]
    if unreadable:
        raise ValueError("payload files not world-readable: "
                         + ", ".join(sorted(unreadable)[:5]))
    payload_bytes = sum(e.get("size", 0) for e in entries.values())
    (OUT / "assets.json").write_text(
        json.dumps(entries, indent=1, sort_keys=True) + "\n")
    validation = {
        "schema": "synveil.distro-chromium-stage/v1",
        "target": ffi.TARGET,
        "snapshot": pin["snapshot"],
        "chromium_version": pin["chromium_version"],
        "deb_packages": len(seen),
        "deb_bytes": sum(p["size"] for p in pin["packages"]),
        "payload_files": len(entries),
        "payload_bytes": payload_bytes,
        "payload_files_copied": copied,
        "assets_sha256": assets.seal_digest(entries),
        "derived": notes,
        "glibc": "baseline project glibc (distribution libc pruned)",
        "marker": "SYNVEIL_DISTRO_CHROMIUM_STAGED_NOT_BOOTED",
    }
    (OUT / "validation.json").write_text(
        json.dumps(validation, indent=1, sort_keys=True) + "\n")
    print(validation["marker"])
    print(f"chromium={pin['chromium_version']} debs={len(seen)} "
          f"payload_files={len(entries)} "
          f"payload_mb={payload_bytes // 1048576}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", required=True)
    parser.parse_args()
    build()


if __name__ == "__main__":
    main()
