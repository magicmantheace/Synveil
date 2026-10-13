#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Boot the actual packaged authenticated VM image with a private copy of its blank state.

This separate QEMU pass tests the packaged image, not the guest build fixture.
The bundle's pristine disk is never enrolled, changed, or published by CI.
The production launcher remains offline/GTK with no QMP monitor.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from auth_vm_bundle import sha256, verify_authenticated_fixture  # noqa: E402


def check_private_regular(path: Path, mode: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked packaged file: {path.name}")
    if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != mode:
        raise ValueError(f"unsafe owner/permissions for {path.name}")


def validate_bundle(bundle: Path, fixture: Path, revision: str) -> Path:
    if (bundle.is_symlink() or not bundle.is_dir() or
            bundle.stat().st_uid != os.getuid() or
            stat.S_IMODE(bundle.stat().st_mode) != 0o700):
        raise ValueError("bundle must be an owner-owned private directory")
    source = verify_authenticated_fixture(fixture, revision)
    for name in ("synveil-x86_64.img", "fixture.json", "validation.json",
                 "VM-BUNDLE.json", "SHA256SUMS"):
        check_private_regular(bundle / name, 0o400)
    check_private_regular(bundle / "synveil-state.ext4", 0o600)
    image = bundle / "synveil-x86_64.img"
    if sha256(image) != source["artifacts"]["synveil-x86_64.img"]["sha256"]:
        raise ValueError("packaged VM image differs from validated guest image")
    if (bundle / "fixture.json").read_bytes() != (
            fixture / "manifests/fixture.json").read_bytes():
        raise ValueError("packaged fixture manifest differs from guest proof")
    if (bundle / "validation.json").read_bytes() != (
            fixture / "manifests/validation.json").read_bytes():
        raise ValueError("packaged validation receipt differs from guest proof")
    metadata = json.loads((bundle / "VM-BUNDLE.json").read_text())
    if (metadata.get("git_revision") != revision or
            metadata.get("physical_install_allowed") is not False or
            metadata.get("signed_release") is not False):
        raise ValueError("unexpected authenticated VM package metadata")
    entries = (bundle / "SHA256SUMS").read_text().splitlines()
    expected_names = {"synveil-x86_64.img", "fixture.json",
                      "validation.json", "VM-BUNDLE.json"}
    checked = set()
    for line in entries:
        if "  " not in line:
            raise ValueError("invalid bundle checksum format")
        digest, name = line.split("  ", 1)
        if name not in expected_names or name in checked:
            raise ValueError("unexpected or duplicate immutable checksum")
        if len(digest) != 64 or digest != sha256(bundle / name):
            raise ValueError(f"immutable checksum mismatch: {name}")
        checked.add(name)
    if checked != expected_names:
        raise ValueError("incomplete immutable checksum manifest")
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "out/auth-vm")
    parser.add_argument("--fixture", type=Path,
                        default=ROOT / "out/guest-fixtures/graphical-onboard")
    parser.add_argument("--timeout", type=int, default=105)
    args = parser.parse_args()
    if args.timeout < 20:
        parser.error("QEMU boot timeout must be at least 20 seconds")
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        image = validate_bundle(args.bundle, args.fixture, revision)
        original_disk = args.bundle / "synveil-state.ext4"
        disk_digest = sha256(original_disk)
        with tempfile.TemporaryDirectory(prefix="synveil-packaged-qemu-") as folder:
            scratch = Path(folder)
            out = scratch / "out"
            out.mkdir(mode=0o700)
            # Resolve the exact bundle image; do not rebuild or restage it.
            (out / "synveil-x86_64.img").symlink_to(image.resolve())
            private_disk = scratch / "state.ext4"
            shutil.copyfile(original_disk, private_disk)
            private_disk.chmod(0o600)
            env = {**os.environ,
                   "SYNVEIL_OUT_DIR": str(out),
                   "SYNVEIL_QEMU_STATE_DISK": str(private_disk),
                   "SYNVEIL_QEMU_DISPLAY": "none",
                   "SYNVEIL_QEMU_VIDEO": "1",
                   "SYNVEIL_QEMU_NETWORK": "0"}
            env.pop("SYNVEIL_QEMU_QMP_SOCKET", None)
            env.pop("SYNVEIL_DEV_SERIAL_ROOTFS", None)
            env.pop("SYNVEIL_AUTH_DESKTOP_ROOTFS", None)
            result = subprocess.run(
                ["python3", str(ROOT / "tests/smoke-graphical-onboard.py"),
                 "--timeout", str(args.timeout)],
                env=env, check=False, timeout=args.timeout * 2 + 40,
            )
            # Keep only the synthetic, redacted serial transcripts for CI
            # diagnostics; never archive an enrolled state disk.
            evidence_dir = ROOT / "out/logs"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            for name in ("onboard-first.log", "onboard-reboot.log"):
                transcript = out / "logs" / name
                if transcript.is_file():
                    shutil.copyfile(transcript, evidence_dir / ("auth-vm-" + name))
            if result.returncode != 0:
                raise ValueError("separate packaged-image QEMU onboarding failed")
        if sha256(original_disk) != disk_digest:
            raise ValueError("package's blank credential state was modified")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        print(f"[synveil] packaged QEMU smoke FAILED: {error}", file=sys.stderr)
        return 1
    print("[synveil] packaged QEMU image independently passed two-boot onboarding")
    print("[synveil] distributable blank state disk preserved unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
