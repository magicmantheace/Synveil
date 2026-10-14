#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Archive only a verified, pristine authenticated QEMU preview; never user state."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import tarfile

from auth_vm_bundle import LAUNCHER, ROOT, check_private_regular, sha256, validate_bundle

FILES = {
    "synveil-x86_64.img": 0o400,
    "fixture.json": 0o400,
    "validation.json": 0o400,
    "VM-BUNDLE.json": 0o400,
    "SHA256SUMS": 0o400,
    "synveil-state.ext4": 0o600,
    "run-vm.sh": 0o700,
    "README.md": 0o644,
}


class HashingReader:
    """Measure the exact source bytes consumed by tar, with bounded buffering."""
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()
        self.bytes = 0

    def read(self, size):
        data = self.source.read(size)
        self.digest.update(data)
        self.bytes += len(data)
        return data


def check_open_member(source, mode: int, snapshot) -> None:
    meta = os.fstat(source.fileno())
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or
            stat.S_IMODE(meta.st_mode) != mode or meta.st_nlink != 1 or
            (meta.st_dev, meta.st_ino, meta.st_size) != snapshot):
        raise ValueError("archive member inode or permissions changed")


def archive_bundle(bundle: Path, fixture: Path, output: Path, revision: str) -> None:
    validate_bundle(bundle, fixture, revision)
    if {path.name for path in bundle.iterdir()} != set(FILES):
        raise ValueError("unexpected authenticated bundle file inventory")
    for name, mode in FILES.items():
        check_private_regular(bundle / name, mode)
    if (bundle / "run-vm.sh").read_text() != LAUNCHER:
        raise ValueError("authenticated launcher differs from approved offline launcher")
    # Immutable members are anchored to the already verified inventory. The
    # mutable disk must match its creation-time digest, never a later snapshot.
    checksums = (bundle / "SHA256SUMS").read_bytes()
    expected = {name: digest for digest, name in
                (line.split("  ", 1) for line in checksums.decode().splitlines())}
    expected["SHA256SUMS"] = hashlib.sha256(checksums).hexdigest()
    expected["synveil-state.ext4"] = json.loads(
        (bundle / "VM-BUNDLE.json").read_text())["blank_state_sha256"]
    expected["run-vm.sh"] = hashlib.sha256(LAUNCHER.encode()).hexdigest()
    expected["README.md"] = sha256(bundle / "README.md")
    snapshots = {}
    for name in FILES:
        meta = (bundle / name).stat()
        snapshots[name] = (meta.st_dev, meta.st_ino, meta.st_size)
    validate_bundle(bundle, fixture, revision)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as archive:
                    directory = tarfile.TarInfo("auth-vm")
                    directory.type = tarfile.DIRTYPE
                    directory.mode = 0o700
                    archive.addfile(directory)
                    for name, mode in FILES.items():
                        path = bundle / name
                        member = tarfile.TarInfo("auth-vm/" + name)
                        member.mode = mode
                        member.size = snapshots[name][2]
                        source_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                        with os.fdopen(source_fd, "rb") as source:
                            check_open_member(source, mode, snapshots[name])
                            reader = HashingReader(source)
                            archive.addfile(member, reader)
                            check_open_member(source, mode, snapshots[name])
                            if (reader.bytes != member.size or
                                    reader.digest.hexdigest() != expected[name]):
                                raise ValueError("archive member changed while copying")
            raw.flush()
            os.fsync(raw.fileno())
        # Catch a changed state/image/receipt before retaining an archive.
        validate_bundle(bundle, fixture, revision)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "out/auth-vm")
    parser.add_argument("--fixture", type=Path,
                        default=ROOT / "out/guest-fixtures/graphical-onboard")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    try:
        archive_bundle(args.bundle, args.fixture, args.output, revision)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print("[synveil] archived authenticated QEMU preview with pristine state and private modes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
