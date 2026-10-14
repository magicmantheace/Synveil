#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Boot the actual packaged authenticated VM image with a private copy of its blank state.

This separate QEMU pass tests the packaged image, not the guest build fixture.
The bundle's pristine disk is never enrolled or changed by CI. A verified blank
disk is eligible for the preview download only after this guest test succeeds.
The production launcher remains offline/GTK with no QMP monitor.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from auth_vm_bundle import sha256, validate_bundle  # noqa: E402



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
