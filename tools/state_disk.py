#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create a new Synveil state disk image, never overwrite an existing path.

The image is GPT-partitioned: partition 1 starts at 1 MiB, carries the
project's fixed state identity (partition name "Synveil State" and the
PARTUUID below), and holds an ext4 filesystem labelled SYNVEIL_STATE.
Guests never trust a device name such as /dev/vdb1: mount-state,
veil-core, and veil-enroll all resolve the state device by this GPT
identity, so the same policy covers a separately attached state disk
and the state partition inside the install image.
"""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "out" / "synveil-state.ext4"

# Fixed GPT identity of the Synveil state partition. This is a role
# label, not a secret: whoever controls disk attachment already controls
# the machine. Authenticity of install media belongs to the signed
# installer/update milestone. Keep in sync with build/rootfs/mount-state,
# src/veil-core/src/credential.rs, and src/veil-display/enroll.c.
STATE_PARTUUID = "7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053"
STATE_PARTNAME = "Synveil State"
STATE_FS_LABEL = "SYNVEIL_STATE"
PARTITION_OFFSET_BYTES = 1024 * 1024


def create_state_disk(path: Path, size_mib: int) -> None:
    if not 64 <= size_mib <= 16384:
        raise ValueError("size must be between 64 and 16384 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            os.ftruncate(output.fileno(), size_mib * 1024 * 1024)
        subprocess.run(
            ["sgdisk", "-n", "1:2048:+" + str(size_mib - 2) + "M",
             "-t", "1:8300", "-c", "1:" + STATE_PARTNAME,
             "-u", "1:" + STATE_PARTUUID, str(path)],
            check=True)
        # Partition 1 starts at 1 MiB and is exactly (size_mib - 2) MiB:
        # sized explicitly because an end-relative partition would stop
        # at the backup GPT and end up smaller than the filesystem.
        fs_blocks = (size_mib - 2) * 256
        subprocess.run(
            ["mkfs.ext4", "-F", "-q", "-L", STATE_FS_LABEL, "-b", "4096",
             "-E", "offset=" + str(PARTITION_OFFSET_BYTES), str(path),
             str(fs_blocks)],
            check=True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--size-mib", type=int, default=256)
    args = parser.parse_args()
    try:
        create_state_disk(args.path, args.size_mib)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"[synveil] new Synveil state disk: {args.path} ({args.size_mib} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
