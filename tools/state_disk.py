#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create a new QEMU ext4 state image, never overwrite an existing path."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "out" / "synveil-state.ext4"


def create_state_disk(path: Path, size_mib: int) -> None:
    if not 64 <= size_mib <= 16384:
        raise ValueError("size must be between 64 and 16384 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            os.ftruncate(output.fileno(), size_mib * 1024 * 1024)
        subprocess.run(["mkfs.ext4", "-F", "-q", "-L", "SYNVEIL_STATE", str(path)], check=True)
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
    print(f"[synveil] new QEMU state disk: {args.path} ({args.size_mib} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
