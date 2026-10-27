#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build the bootable A/B proof: real generations, the ADR 0011
six-partition disk, and signed release media.

This builder mirrors tools/native_joined.py (it reuses the joined
build's verified baseline rootfs and its helpers) and adds only the
A/B generation overlay: the ab-system marker, the pinned publisher
trust record, the generation stamp, the start-update launcher, the
two guarded init hooks, one observable change per version (the
Weston background color), and — for the failing candidate only — a
veil-seat stub that makes the real health path fail.

The disk is a regular file assembled with sgdisk/mkfs/mtools in the
same style as build/scripts/image.sh. Nothing here touches a
physical device. The publisher private key is created under
out/ab-proof/trust/ (git-ignored) by tools/ab_release.py and never
enters an image; generations carry only the public key.

Boot selection needs no firmware writes: the ESP fallback file is
a plain copy of generation A, and whichever generation the
firmware loads, its init hook converges to the desired slot by
chain-loading it with kexec_file_load (CONFIG_KEXEC_FILE=y).
BootOrder, BootNext and Boot#### are never written.

The generations are installed-system images, not test fixtures:
the same overlay and agent would ship in a real A/B install. The
host fixture core (tools/ab_update.py) remains a separate,
file-only model and is never mixed into these images.

With --browser --boot-target hardware --out <dir> the same builder
produces the owner test-media disk: the production browser overlay
(no test CA, no test hosts, no fixture start page) on the hardware
kernel (tty0 console, simpledrm framebuffer, USB/AHCI/NVMe
storage). The qemu-target proof builds remain the update/browser
acceptance route; the hardware disk is accepted by the framebuffer
harness (tests/smoke-native-joined-hardware.py --ab-receipt).
See docs/PHYSICAL_BOOT.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ab_release
import distro_chromium_assets as assets
import native_joined as joined

OUT = ROOT / "out" / "ab-proof"
TRUST_DIR = OUT / "trust"
STATE_PARTUUID = joined.STATE_PARTUUID

ESP_PARTUUID = "ab000000-0000-4000-8000-0000000000e5"
SYSTEM_A_PARTUUID = "ab000000-0000-4000-8000-00000000000a"
SYSTEM_B_PARTUUID = "ab000000-0000-4000-8000-00000000000b"
DATA_PARTUUID = "ab000000-0000-4000-8000-0000000000da"
RECOVERY_PARTUUID = "ab000000-0000-4000-8000-0000000000ec"

ESP_SIZE_MB = 256
SLOT_SIZE_MB = 224
STATE_SIZE_MB = 256
DATA_SIZE_MB = 128
RECOVERY_SIZE_MB = 224

GENERATIONS = [
    {"version": 1, "background": "0xFF101820", "seat_stub": False,
     "generation_id": "synveil-gen-v1"},
    {"version": 2, "background": "0xFF0B3D2E", "seat_stub": False,
     "generation_id": "synveil-gen-v2"},
    {"version": 3, "background": "0xFF3D0B0B", "seat_stub": True,
     "generation_id": "synveil-gen-v3"},
]

INIT_HOOK_AFTER_STATE = """
# A/B update accounting (tools/native_ab.py overlay): runs before any
# service starts; inert on generations without the ab-system marker.
# Trial exhaustion reboots to the confirmed slot from inside veil-ab.
if [ -f /etc/synveil/ab-system ] && [ -x /usr/sbin/veil-ab ]; then
    /usr/sbin/veil-ab boot-hook || echo SYNVEIL_AB_HOOK_UNAVAILABLE
fi
"""

INIT_AGENT_AFTER_CORE = """
# A/B update agent (tools/native_ab.py overlay): health confirmation
# and the typed-action update socket; marked generations only.
if [ -f /etc/synveil/ab-system ] && [ -x /usr/libexec/synveil/start-update ]; then
    /usr/libexec/synveil/start-update &
fi
"""

STATE_ANCHOR = """/usr/libexec/synveil/mount-state || echo SYNVEIL_STATE_UNAVAILABLE
fi
"""
CORE_ANCHOR = """        echo SYNVEIL_CORE_UNAVAILABLE
    }
fi
"""


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_commit():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def ensure_joined_baseline(rebuild):
    baseline_rootfs = ROOT / "out" / "native-joined" / "work" / "rootfs"
    if rebuild and (ROOT / "out" / "native-joined").exists():
        shutil.rmtree(ROOT / "out" / "native-joined")
    if not (baseline_rootfs / "usr/sbin/veil-ab").is_file():
        if baseline_rootfs.exists():
            raise ValueError(
                "joined baseline rootfs predates veil-ab; rerun with --rebuild-joined")
        run([sys.executable, str(ROOT / "tools/native_joined.py"),
             "--build", "--boot-target", "qemu"], cwd=ROOT)
    if not (baseline_rootfs / "usr/sbin/veil-ab").is_file():
        raise ValueError("joined baseline rootfs lacks usr/sbin/veil-ab")
    return baseline_rootfs


def patch_init(rootfs):
    init = rootfs / "init"
    text = init.read_text()
    if "veil-ab boot-hook" in text:
        raise ValueError("init already carries the A/B overlay")
    if text.count(STATE_ANCHOR) != 1 or text.count(CORE_ANCHOR) != 1:
        raise ValueError("init anchors for the A/B overlay not found exactly once")
    text = text.replace(STATE_ANCHOR, STATE_ANCHOR + INIT_HOOK_AFTER_STATE, 1)
    text = text.replace(CORE_ANCHOR, CORE_ANCHOR + INIT_AGENT_AFTER_CORE, 1)
    init.write_text(text)
    init.chmod(0o755)


def overlay_generation(rootfs, spec, commit, public_hex):
    config = rootfs / "etc/synveil"
    (config / "ab-system").write_text("")
    (config / "ab-system").chmod(0o644)
    (config / "publisher.json").write_text(json.dumps({
        "schema": "synveil.ab-trust/v1",
        "key_id": ab_release.KEY_ID,
        "development": True,
        "public_key_hex": public_hex,
    }, indent=2) + "\n")
    (config / "publisher.json").chmod(0o644)
    (config / "generation.json").write_text(json.dumps({
        "schema": "synveil.generation/v1",
        "version": spec["version"],
        "commit": commit,
        "state_format": 1,
        "generation_id": spec["generation_id"],
    }, indent=2) + "\n")
    (config / "generation.json").chmod(0o644)
    launcher = rootfs / "usr/libexec/synveil/start-update"
    shutil.copyfile(ROOT / "build/rootfs/start-update", launcher)
    launcher.chmod(0o755)
    weston_ini = config / "weston.ini"
    text = weston_ini.read_text()
    if text.count("background-color=0xFF101820") != 1:
        raise ValueError("weston.ini background anchor missing")
    weston_ini.write_text(text.replace(
        "background-color=0xFF101820",
        "background-color=" + spec["background"], 1))
    if spec["seat_stub"]:
        seat = rootfs / "usr/sbin/veil-seat"
        seat.write_text("#!/bin/sh\n"
                        "# Deliberately failing seat for the A/B failing-\n"
                        "# candidate generation (tools/native_ab.py): the\n"
                        "# session never starts, so health confirmation\n"
                        "# must fail and the trial must exhaust.\n"
                        "exit 1\n")
        seat.chmod(0o755)
    patch_init(rootfs)


def browser_stage():
    """Verify the distro-Chromium stage's identity and return
    (staging_tree, assets, validation_receipt). Same discipline
    as the joined build's Weston stage check: the stage is only
    ever consumed through its sealed, digest-verified asset list
    (tools/distro_chromium.py)."""
    bout = ROOT / "out" / "distro-chromium"
    receipt = json.loads(joined.regular(bout / "validation.json").read_text())
    if (receipt.get("schema") != "synveil.distro-chromium-stage/v1" or
            receipt.get("target") != joined.ffi.TARGET):
        raise ValueError("distro chromium stage identity mismatch")
    staging = bout / "staging"
    entries = json.loads(joined.regular(bout / "assets.json").read_text())
    if assets.seal_digest(entries) != receipt.get("assets_sha256"):
        raise ValueError("distro chromium seal digest mismatch")
    if not assets.verified(staging, entries):
        raise ValueError("distro chromium staging does not match its seal")
    return staging, entries, receipt


def copy_browser_asset(source, destination, name, identity):
    """Sealed-payload copy with the standing rule: never replace
    a file the generation rootfs already owns unless the bytes
    are identical. Packaged modes are applied from the seal."""
    relative = Path(name)
    if not assets.allowed(name):
        raise ValueError("forbidden browser asset")
    target = destination / relative
    parent = destination
    for part in relative.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ValueError("asset destination parent is a symlink")
        parent.mkdir(exist_ok=True)
        # The workspace umask (0007) would leave 0770 root:root
        # parents the browser's uid cannot traverse (this exact
        # failure hid /etc/chromium.d from the launcher wrapper).
        parent.chmod(0o755)
    if identity["kind"] == "symlink":
        if target.is_symlink() and \
                target.readlink().as_posix() == identity["target"]:
            return "identical"
        if target.exists() or target.is_symlink():
            raise ValueError("refusing to replace rootfs entry: " + name)
        target.symlink_to(identity["target"])
        return "copied"
    data = (source / relative).read_bytes()
    if hashlib.sha256(data).hexdigest() != identity["sha256"]:
        raise ValueError("sealed asset bytes changed: " + name)
    if target.exists() or target.is_symlink():
        if target.is_file() and not target.is_symlink() and \
                target.read_bytes() == data:
            return "identical"
        raise ValueError("refusing to replace rootfs file: " + name)
    with target.open("xb") as output:
        output.write(data)
    target.chmod(int(identity["mode"], 8))
    return "copied"


def check_link_closure(rootfs, tools, added):
    """Every DT_NEEDED soname of every ELF the browser stage added
    must resolve inside the finished generation rootfs (baseline
    files + copied assets together, subdirectories included —
    e.g. PulseAudio's private library directory). A library the
    loader cannot find is a browser that fails at exec — catch
    it at build time, on the host, where the failure is cheap."""
    libdirs = [rootfs / "usr/lib", rootfs / "usr/lib64",
               rootfs / "lib", rootfs / "lib64",
               rootfs / "usr/lib/weston",
               rootfs / "usr/lib/x86_64-linux-gnu",
               rootfs / "usr/lib/chromium"]
    present = set()
    for folder in libdirs:
        if folder.is_dir():
            for entry in folder.rglob("*"):
                present.add(entry.name)
    missing = {}
    checked = 0
    for relative in added:
        path = rootfs / relative
        result = subprocess.run([str(tools["readelf"]), "-d", str(path)],
                                capture_output=True, text=True)
        if "NEEDED" not in result.stdout:
            continue
        checked += 1
        for line in result.stdout.splitlines():
            if "NEEDED" not in line:
                continue
            soname = line.split("[", 1)[1].rstrip("]\n")
            if soname not in present:
                missing.setdefault(soname, []).append(relative)
    if missing:
        raise ValueError("browser link closure incomplete: " +
                         json.dumps(missing, indent=1))
    return checked


def browser_icon_png():
    """A deterministic 48x48 RGB PNG: the files window's teal
    with a paper-white disc — the panel launcher's icon. Generated,
    never a binary blob in Git."""
    import struct
    import zlib
    width = height = 48
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            dx, dy = x - 23.5, y - 23.5
            if dx * dx + dy * dy <= 14.0 * 14.0:
                row += bytes((0xF2, 0xF0, 0xEB))
            else:
                row += bytes((0x1D, 0x5C, 0x57))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def overlay_browser(rootfs, stage, *, test_ca, test_host, start_url):
    """Install the Priority 3/4 payload into one generation rootfs:
    networking scripts, the sealed Chromium assets, the launcher
    wrapper and panel entry, the session markers, and — only when
    the caller provisions them for a proof build — the test CA and
    test endpoint. Returns the build-record section. Production
    builds pass no test material: the trust bundle is then exactly
    the distribution's sealed bundle, and the browser opens on
    the start page the build was given (or its own default)."""
    target, selected, stage_receipt = stage
    tools, _sysroot = joined.ffi.target_tools()
    counts = {"copied": 0, "identical": 0}
    added = []
    for name, identity in sorted(selected.items()):
        outcome = copy_browser_asset(target, rootfs, name, identity)
        counts[outcome] += 1
        if outcome == "copied" and identity["kind"] == "file":
            added.append(name)
    closure_files = check_link_closure(rootfs, tools, added)

    runtime = rootfs / "usr/libexec/synveil"
    for script in ("configure-network", "udhcpc-script"):
        destination = runtime / script
        shutil.copyfile(ROOT / "build/rootfs" / script, destination)
        destination.chmod(0o755)
    wrapper = rootfs / "usr/bin/synveil-browser"
    shutil.copyfile(ROOT / "build/rootfs/synveil-browser", wrapper)
    wrapper.chmod(0o755)

    # No managed policy is installed: the browser ships
    # stock-configured (owner decision, 2026-10-27 — the
    # synveil-privacy managed policy was removed in full).

    config = rootfs / "etc/synveil"
    for marker in ("browser", "files-app"):
        entry = config / marker
        entry.write_text("")
        entry.chmod(0o644)
    icon = rootfs / "usr/share/icons/synveil-browser.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    with icon.open("xb") as handle:
        handle.write(browser_icon_png())
    icon.chmod(0o644)
    weston_ini = config / "weston.ini"
    weston_ini.write_text(weston_ini.read_text() +
                          "\n[launcher]\n"
                          "icon=/usr/share/icons/synveil-browser.png\n"
                          "path=/usr/bin/synveil-browser\n")

    hosts = rootfs / "etc/hosts"
    hosts_text = "127.0.0.1 localhost\n"
    # No name-layer connection blocking: the browser ships
    # stock-configured (owner decision, 2026-10-27 — the
    # earlier vendor null-routing regime was removed in
    # full). The hosts file carries localhost and, for proof
    # builds only, the fixture host below.
    if test_host:
        hosts_text += f"{test_host[0]} {test_host[1]}\n"
    hosts.write_text(hosts_text)
    hosts.chmod(0o644)
    record = {
        "stage_schema": stage_receipt["schema"],
        "stage_validation_sha256": sha256(ROOT / "out" / "distro-chromium"
                                          / "validation.json"),
        "chromium_version": stage_receipt["chromium_version"],
        "deb_packages": stage_receipt["deb_packages"],
        "payload_files": stage_receipt["payload_files"],
        "payload_bytes": stage_receipt["payload_bytes"],
        "assets_copied": counts["copied"],
        "assets_identical_to_baseline": counts["identical"],
        "link_closure_files_checked": closure_files,
        "test_ca_sha256": None,
        "test_host": None,
        "start_url": None,
    }
    if test_ca is not None:
        # Proof provisioning, documented as such: the run-local
        # test CA is APPENDED to the sealed distribution bundle
        # (the generations then trust the distribution roots plus
        # this one CA) and shipped alone for the launcher's NSS
        # seed. The CA file itself is never committed; the build
        # receipt records its sha256 and the driver refuses a
        # mismatch.
        ca_bytes = Path(test_ca).read_bytes()
        digest = subprocess.run(
            ["openssl", "x509", "-hash", "-noout"],
            input=ca_bytes, capture_output=True)
        if digest.returncode != 0:
            raise ValueError("test CA is not a parseable certificate")
        bundle = rootfs / "etc/ssl/certs/ca-certificates.crt"
        with bundle.open("ab") as handle:
            handle.write(ca_bytes)
        share = rootfs / "usr/share/synveil"
        share.mkdir(parents=True, exist_ok=True)
        share.chmod(0o755)
        proof_ca = share / "proof-ca.pem"
        with proof_ca.open("xb") as handle:
            handle.write(ca_bytes)
        proof_ca.chmod(0o644)
        record["test_ca_sha256"] = hashlib.sha256(ca_bytes).hexdigest()
    if test_host:
        record["test_host"] = {"address": test_host[0], "name": test_host[1]}
    if start_url:
        entry = config / "browser-start-url"
        entry.write_text(start_url + "\n")
        entry.chmod(0o644)
        record["start_url"] = start_url
    return record


def build_kernel(out_dir, rootfs, recovery=False, boot_target="qemu"):
    """Pack rootfs into a cpio and relink the seed kernel into a UKI."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    env = joined.base_env(out_dir, out_dir / "work")
    env.update(SYNVEIL_KERNEL_SEED_WORK=str(ROOT / "build/work"),
               SYNVEIL_BOOT_TARGET=boot_target, TMPDIR=str(tmp))
    if recovery:
        env["SYNVEIL_KERNEL_TEST_RECOVERY_STATUS"] = "1"
    with (out_dir / "synveil-initramfs.cpio").open("wb") as archive:
        run(["bash", "-o", "pipefail", "-c",
             "find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0"],
            cwd=rootfs, stdout=archive, env=env)
    run(["bash", str(ROOT / "build.sh"), "kernel"], cwd=ROOT, env=env)
    return out_dir / "synveil-x86_64.efi"


def build_generations(commit, public_hex, baseline_rootfs, browser=None,
                      boot_target="qemu"):
    generations = {}
    browser_record = None
    for spec in GENERATIONS:
        gdir = OUT / f"gen-v{spec['version']}"
        if gdir.exists():
            shutil.rmtree(gdir)
        gdir.mkdir(parents=True)
        rootfs = gdir / "rootfs"
        shutil.copytree(baseline_rootfs, rootfs, symlinks=True)
        overlay_generation(rootfs, spec, commit, public_hex)
        if browser is not None:
            record = overlay_browser(rootfs, browser["stage"],
                                     test_ca=browser["test_ca"],
                                     test_host=browser["test_host"],
                                     start_url=browser["start_url"])
            if browser_record is None:
                browser_record = record
        uki = build_kernel(gdir, rootfs, boot_target=boot_target)
        generations[spec["version"]] = {
            "version": spec["version"],
            "generation_id": spec["generation_id"],
            "uki": str(uki),
            "uki_sha256": sha256(uki),
            "uki_bytes": uki.stat().st_size,
            "background": spec["background"],
            "seat_stub": spec["seat_stub"],
            "state_format": 1,
        }
        print(f"SYNVEIL_AB_GENERATION_BUILT version={spec['version']} "
              f"sha256={generations[spec['version']]['uki_sha256']}")
    rdir = OUT / "recovery"
    if rdir.exists():
        shutil.rmtree(rdir)
    rdir.mkdir(parents=True)
    rootfs = rdir / "rootfs"
    shutil.copytree(baseline_rootfs, rootfs, symlinks=True)
    uki = build_kernel(rdir, rootfs, recovery=True,
                       boot_target=boot_target)
    recovery = {"uki": str(uki), "uki_sha256": sha256(uki),
                "uki_bytes": uki.stat().st_size}
    print(f"SYNVEIL_AB_RECOVERY_BUILT sha256={recovery['uki_sha256']}")
    return generations, recovery, browser_record


def fat_partition_image(size_mb, files):
    """files: {guest_path: host_path}. Returns a temp FAT32 image path."""
    import tempfile
    handle, name = tempfile.mkstemp(suffix=".img")
    os.close(handle)
    image = Path(name)
    with open(image, "wb") as fh:
        fh.truncate(size_mb * 1024 * 1024)
    run(["mkfs.vfat", "-F", "32", "-n", "SYNVEIL", str(image)],
        stdout=subprocess.DEVNULL)
    created = set()
    for guest in files:
        parts = Path(guest).parts[:-1]
        for depth in range(1, len(parts) + 1):
            directory = "/".join(parts[:depth])
            if directory not in created:
                run(["mmd", "-i", str(image), "::" + directory])
                created.add(directory)
    for guest, host in files.items():
        run(["mcopy", "-i", str(image), str(host), "::" + guest])
    return image


def write_partition(disk, number, source_image):
    info = subprocess.check_output(["sgdisk", "-i", str(number), str(disk)], text=True)
    first = int([line for line in info.splitlines()
                 if "First sector:" in line][0].split(":", 1)[1].split()[0])
    with open(source_image, "rb") as source, open(disk, "r+b") as target:
        target.seek(first * 512)
        shutil.copyfileobj(source, target)
    Path(source_image).unlink()


def slot_identity(slot, generation):
    return {
        "schema": "synveil.slot-generation/v1",
        "slot": slot,
        "version": generation["version"],
        "bytes": generation["uki_bytes"],
        "sha256": generation["uki_sha256"],
        "state_format": generation["state_format"],
        "generation_id": generation["generation_id"],
        "min_reader_state_format": 1,
    }


def assemble_disk(generations, recovery):
    disk = OUT / "synveil-ab.img"
    total_mb = (ESP_SIZE_MB + SLOT_SIZE_MB * 2 + STATE_SIZE_MB +
                DATA_SIZE_MB + RECOVERY_SIZE_MB + 8)
    with open(disk, "wb") as fh:
        fh.truncate(total_mb * 1024 * 1024)
    parts = [
        (1, ESP_SIZE_MB, "EF00", "Synveil EFI", ESP_PARTUUID),
        (2, SLOT_SIZE_MB, "0700", "Synveil System A", SYSTEM_A_PARTUUID),
        (3, SLOT_SIZE_MB, "0700", "Synveil System B", SYSTEM_B_PARTUUID),
        (4, STATE_SIZE_MB, "8300", "Synveil State", STATE_PARTUUID),
        (5, DATA_SIZE_MB, "8300", "Synveil Data", DATA_PARTUUID),
        (6, RECOVERY_SIZE_MB, "0700", "Synveil Recovery", RECOVERY_PARTUUID),
    ]
    args = ["sgdisk", "--clear"]
    for number, size, typecode, name, guid in parts:
        args += [f"--new={number}:0:+{size}M", f"--typecode={number}:{typecode}",
                 f"--change-name={number}:{name}", f"--partition-guid={number}:{guid}"]
    args.append(str(disk))
    run(args)
    gen_a = generations[1]
    identity_a = OUT / "gen-a.json"
    identity_a.write_text(json.dumps(slot_identity("A", gen_a), indent=2) + "\n")
    esp = fat_partition_image(ESP_SIZE_MB, {
        "EFI/BOOT/BOOTX64.EFI": gen_a["uki"],
        "EFI/synveil/recovery.efi": recovery["uki"],
    })
    write_partition(disk, 1, esp)
    slot_a = fat_partition_image(SLOT_SIZE_MB, {
        "EFI/synveil/gen.efi": gen_a["uki"],
        "EFI/synveil/gen.json": identity_a,
    })
    write_partition(disk, 2, slot_a)
    write_partition(disk, 3, fat_partition_image(SLOT_SIZE_MB, {}))
    # State: a fresh, empty filesystem, exactly like the joined image.
    info = subprocess.check_output(["sgdisk", "-i", "4", str(disk)], text=True)
    first = int([line for line in info.splitlines()
                 if "First sector:" in line][0].split(":", 1)[1].split()[0])
    run(["mkfs.ext4", "-F", "-q", "-L", "SYNVEIL_STATE", "-b", "4096",
         "-E", f"offset={first * 512}", str(disk), str(STATE_SIZE_MB * 256)])
    write_partition(disk, 5, _ext4_image(DATA_SIZE_MB))
    write_partition(disk, 6, fat_partition_image(RECOVERY_SIZE_MB, {}))
    identity_a.unlink()
    return disk


def _ext4_image(size_mb):
    import tempfile
    handle, name = tempfile.mkstemp(suffix=".img")
    os.close(handle)
    image = Path(name)
    with open(image, "wb") as fh:
        fh.truncate(size_mb * 1024 * 1024)
    run(["mkfs.ext4", "-F", "-q", "-b", "4096", str(image)])
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", required=True)
    parser.add_argument("--rebuild-joined", action="store_true")
    parser.add_argument("--browser", action="store_true",
                        help="build the browser proof disk (Priority 3/4) "
                             "under out/browser-proof instead: generations "
                             "additionally carry networking, the sealed "
                             "browser stage and the file-access marker")
    parser.add_argument("--browser-test-ca",
                        help="PEM CA certificate installed as the "
                             "generations' trust bundle (proof builds "
                             "only; recorded in the build receipt)")
    parser.add_argument("--browser-test-host", nargs=2,
                        metavar=("ADDRESS", "NAME"),
                        help="extra /etc/hosts entry for the proof endpoint")
    parser.add_argument("--browser-start-url",
                        help="URL the session's browser opens at start")
    parser.add_argument("--boot-target", choices=("qemu", "hardware"),
                        default="qemu",
                        help="kernel boot target for every generation "
                             "and the recovery image: qemu (default; "
                             "serial console, virtio) or hardware "
                             "(tty0 console, simpledrm framebuffer, "
                             "USB/AHCI/NVMe storage — the owner "
                             "test-media kernel)")
    parser.add_argument("--out", default=None,
                        help="output directory (default: out/ab-proof, "
                             "or out/browser-proof with --browser)")
    args = parser.parse_args()
    global OUT, TRUST_DIR, ESP_SIZE_MB, SLOT_SIZE_MB, DATA_SIZE_MB, \
        RECOVERY_SIZE_MB
    browser = None
    if args.out:
        OUT = Path(args.out)
        if not OUT.is_absolute():
            OUT = ROOT / OUT
        TRUST_DIR = OUT / "trust"
    if args.browser:
        if not args.out:
            OUT = ROOT / "out" / "browser-proof"
            TRUST_DIR = OUT / "trust"
        # The Chromium payload grows a generation rootfs from
        # ~124 MB to ~560 MB (uncompressed cpio UKI): the browser
        # proof disk's ESP/slots/recovery must hold a whole UKI
        # each, and DATA gains room for a real browser profile.
        # The plain A/B proof keeps its established layout.
        ESP_SIZE_MB = 768
        SLOT_SIZE_MB = 768
        DATA_SIZE_MB = 384
        RECOVERY_SIZE_MB = 768
        browser = {
            "stage": browser_stage(),
            "test_ca": args.browser_test_ca,
            "test_host": tuple(args.browser_test_host)
                         if args.browser_test_host else None,
            "start_url": args.browser_start_url,
        }
    OUT.mkdir(parents=True, exist_ok=True)
    commit = git_commit()
    _private, public_hex = ab_release.ensure_keypair(TRUST_DIR)
    baseline_rootfs = ensure_joined_baseline(args.rebuild_joined)
    generations, recovery, browser_record = build_generations(
        commit, public_hex, baseline_rootfs, browser=browser,
        boot_target=args.boot_target)
    disk = assemble_disk(generations, recovery)
    releases = {}
    # build_media creates outputs exclusively and refuses existing
    # paths; this build owns its output directory, so remove its own
    # prior release outputs (and any staging leftovers) explicitly.
    for old_label in ("release-v2", "release-v3", "release-v2-badsig"):
        for suffix in (".img", "-fs.img"):
            (OUT / f"{old_label}{suffix}").unlink(missing_ok=True)
        stale_stage = OUT / f"{old_label}-stage"
        if stale_stage.is_dir():
            shutil.rmtree(stale_stage)
    for version, corrupt in ((2, False), (3, False), (2, True)):
        label = f"release-v{version}" + ("-badsig" if corrupt else "")
        releases[label] = ab_release.package(
            generations[version]["uki"], version,
            generations[version]["generation_id"],
            TRUST_DIR, OUT / f"{label}.img", corrupt_signature=corrupt)
        print(f"SYNVEIL_AB_RELEASE_BUILT {label}")
    receipt = {
        "schema": "synveil.ab-proof-build/v1",
        "source_commit": commit,
        "boot_target": args.boot_target,
        "publisher": {"key_id": ab_release.KEY_ID, "development": True,
                      "public_key_hex": public_hex},
        "generations": {str(k): v for k, v in generations.items()},
        "recovery": recovery,
        "disk": {"path": str(disk), "sha256": sha256(disk),
                 "bytes": disk.stat().st_size,
                 "layout": [
                     {"number": 1, "name": "ESP", "partuuid": ESP_PARTUUID, "size_mb": ESP_SIZE_MB},
                     {"number": 2, "name": "SYSTEM_A", "partuuid": SYSTEM_A_PARTUUID, "size_mb": SLOT_SIZE_MB},
                     {"number": 3, "name": "SYSTEM_B", "partuuid": SYSTEM_B_PARTUUID, "size_mb": SLOT_SIZE_MB},
                     {"number": 4, "name": "STATE", "partuuid": STATE_PARTUUID, "size_mb": STATE_SIZE_MB},
                     {"number": 5, "name": "DATA", "partuuid": DATA_PARTUUID, "size_mb": DATA_SIZE_MB},
                     {"number": 6, "name": "RECOVERY", "partuuid": RECOVERY_PARTUUID, "size_mb": RECOVERY_SIZE_MB},
                 ]},
        "releases": releases,
        "browser": browser_record,
        "physical_boot_validated": False,
    }
    (OUT / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("SYNVEIL_AB_PROOF_BUILT_NOT_BOOT_VALIDATED")


if __name__ == "__main__":
    main()
