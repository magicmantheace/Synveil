#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Read-only UEFI El Torito inspection for the experimental desktop ISO.

This validates boot *media structure*, not a successful OS boot, Secure Boot,
login, or the presence of a working window manager.
"""
import argparse
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


class ImageInspectionError(ValueError):
    pass


def parse_el_torito(output: str) -> dict:
    """Reject images lacking an actual bootable UEFI El Torito entry."""
    if not isinstance(output, str) or len(output) > 262144:
        raise ImageInspectionError("invalid xorriso output")
    records = re.findall(
        r"^El Torito boot img\s*:\s*(\d+)\s+(UEFI|BIOS)\s+([yn])\s+",
        output, re.MULTILINE,
    )
    if not records or len({(number, platform) for number, platform, _ in records}) != len(records):
        raise ImageInspectionError("missing or duplicate El Torito entries")
    uefi = [int(n) for n, platform, active in records
            if platform == "UEFI" and active == "y"]
    if not uefi:
        raise ImageInspectionError("no bootable UEFI El Torito entry")
    return {
        "schema": "synveil.desktop-iso-inspection/v1",
        "uefi_boot_entry_verified": True,
        "bootable_uefi_entries": uefi,
        "bootable_bios_entries": [
            int(n) for n, platform, active in records
            if platform == "BIOS" and active == "y"
        ],
        "physical_boot_tested": False,
        "qemu_guest_boot_tested": False,
        "signature_verified": False,
    }


def inspect(iso: Path) -> dict:
    # No disk-device probing and no following symlinks to private paths.
    if iso.is_symlink():
        raise ImageInspectionError("symlink ISO refused")
    metadata = iso.stat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            not 1024 * 1024 <= metadata.st_size <= 16 * 1024 ** 3):
        raise ImageInspectionError("expected a bounded regular ISO file")
    result = subprocess.run(
        ["xorriso", "-indev", str(iso), "-report_el_torito", "plain"],
        text=True, capture_output=True, timeout=40, check=False,
    )
    if result.returncode:
        raise ImageInspectionError("xorriso did not successfully inspect image")
    return parse_el_torito(result.stdout + "\n" + result.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.iso), sort_keys=True))
    except (OSError, subprocess.TimeoutExpired, ImageInspectionError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
