#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create ONLY an unprivileged, file-backed Debian live /home test volume.

Not a Synveil installer. No drive selection, no mount, no root requirement,
no host filesystem reformatting. Refuses any path outside the configured
desktop-live developer output and will not replace an existing image.
"""
import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "out" / "desktop-live"
MARKER = ".synveil-desktop-live-prototype"
BASENAME = "home-persistence.img"
LABEL = "synvmhome"
SIZE_MIB = 256


class HomeImageError(ValueError):
    pass


def create_home_image(*, folder: Path = PARENT, mkfs="mkfs.ext4") -> Path:
    if (folder.is_symlink() or not folder.is_dir() or
            not (folder / MARKER).is_file() or (folder / MARKER).is_symlink()):
        raise HomeImageError("developer ISO must be configured in a real output directory")
    if folder.parent.is_symlink():
        raise HomeImageError("symlinked out directory refused")
    img = folder / BASENAME
    if img.exists() or img.is_symlink():
        raise HomeImageError("refusing to replace existing home volume")
    with tempfile.TemporaryDirectory(prefix="synveil-home-seed-") as temp:
        seed = Path(temp)
        # live-boot uses the root persistence.conf to bind-mount only /home;
        # system software, /etc, /boot, EFI and passwords are NEVER writable
        # through this volume.
        (seed / "persistence.conf").write_text("/home\n", encoding="utf-8")
        if not (seed / "persistence.conf").is_file():
            raise HomeImageError("persistence seed creation failed")
        descriptor = os.open(
            img, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise HomeImageError("new volume must be one regular file")
            os.ftruncate(descriptor, SIZE_MIB * 1024 * 1024)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # The argument is the fixed, freshly O_EXCL-created regular file.
        # Unlike passing a device, mke2fs -d populates without mounting.
        try:
            subprocess.run(
                [mkfs, "-q", "-F", "-L", LABEL, "-d", str(seed), str(img)],
                check=True, timeout=90, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            # Preserve any partially formatted ordinary file for diagnosis.
            raise HomeImageError("file-only ext4 image creation failed") from None
    final = img.stat()
    if (not stat.S_ISREG(final.st_mode) or final.st_nlink != 1 or
            final.st_size != SIZE_MIB * 1024 * 1024 or final.st_mode & 0o077):
        raise HomeImageError("invalid volume after filesystem creation")
    return img


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", required=True,
                        help="Opt-in to making a single new VM home file")
    args = parser.parse_args()
    if not args.create:
        parser.error("--create is required")
    try:
        file = create_home_image()
    except HomeImageError as exc:
        parser.error(str(exc))
    print(f"[desktop-home] VM-only untrusted /home volume: {file}")
    print("[desktop-home] not an installer, owner account or OS update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
