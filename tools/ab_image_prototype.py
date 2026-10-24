#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create an *unformatted, unbootable* A/B GPT layout in an ordinary file.

This is a VM-only prototype, not an installer. It refuses all device paths,
never mounts or formats a filesystem, never updates firmware, and never
discovers disks. CLI paths are fixed under out/install-prototype.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import uuid
import zlib

REPO = Path(__file__).resolve().parents[1]
SECTOR = 512
ALIGN = 2048                         # 1 MiB
ENTRIES = 128
ENTRY_BYTES = 128
ENTRY_SECTORS = ENTRIES * ENTRY_BYTES // SECTOR
DEFAULT_SIZE_MIB = 6144
DEFAULT_PARTS = (
    ("ESP", 256),
    ("SYSTEM_A", 1536),
    ("SYSTEM_B", 1536),
    ("STATE", 512),
    ("DATA", 1536),
    ("RECOVERY", 256),
)
EFI_TYPE = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
LINUX_TYPE = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
NAME = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")


class PrototypeError(ValueError):
    pass


def _align(lba):
    return ((lba + ALIGN - 1) // ALIGN) * ALIGN


def _header(*, at_lba, backup_lba, usable_start, usable_end, disk_id,
            table_lba, table_crc):
    header = struct.pack(
        "<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0,
        at_lba, backup_lba, usable_start, usable_end, disk_id.bytes_le,
        table_lba, ENTRIES, ENTRY_BYTES, table_crc,
    )
    crc = zlib.crc32(header) & 0xFFFFFFFF
    return header[:16] + struct.pack("<I", crc) + header[20:]


def _part_entry(name, part_id, start, end):
    label = name.encode("utf-16le")
    if len(label) > 72:
        raise PrototypeError("partition label too long")
    part_type = EFI_TYPE if name == "ESP" else LINUX_TYPE
    return struct.pack(
        "<16s16sQQQ72s", part_type.bytes_le, part_id.bytes_le,
        start, end, 0, label.ljust(72, b"\x00"),
    )


def _protective_mbr(sectors):
    mbr = bytearray(SECTOR)
    # One protective 0xEE entry. No active boot partition, no MBR filesystem.
    mbr[446:462] = struct.pack(
        "<B3sB3sII", 0, b"\x00\x02\x00", 0xEE,
        b"\xff\xff\xff", 1, min(sectors - 1, 0xFFFFFFFF),
    )
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


def build_layout(total_mib=DEFAULT_SIZE_MIB, specs=DEFAULT_PARTS):
    if (type(total_mib) is not int or not 12 <= total_mib <= 32768 or
            not isinstance(specs, tuple) or len(specs) != 6):
        raise PrototypeError("invalid bounded VM disk size or partition set")
    if tuple(name for name, _ in specs) != (
        "ESP", "SYSTEM_A", "SYSTEM_B", "STATE", "DATA", "RECOVERY"
    ):
        raise PrototypeError("exactly six canonical partitions required")
    sectors = total_mib * 2048
    usable_start = 2 + ENTRY_SECTORS
    usable_end = sectors - ENTRY_SECTORS - 2
    next_lba = _align(usable_start)
    disk_id = uuid.uuid4()
    partitions = []
    for name, mib in specs:
        if type(mib) is not int or mib < 1 or mib > 8192:
            raise PrototypeError("unbounded partition size")
        first = _align(next_lba)
        last = first + (mib * 2048) - 1
        if last > usable_end:
            raise PrototypeError("partitions exceed the virtual disk")
        partitions.append({
            "name": name, "partuuid": str(uuid.uuid4()),
            "first_lba": first, "last_lba": last,
            "size_mib": mib, "filesystem_created": False,
        })
        next_lba = last + 1
    # Ensure the recovery partition has room before backup GPT.
    if partitions[-1]["last_lba"] > usable_end:
        raise PrototypeError("invalid backup GPT reserve")
    table = b"".join(_part_entry(
        p["name"], uuid.UUID(p["partuuid"]), p["first_lba"], p["last_lba"],
    ) for p in partitions)
    table = table.ljust(ENTRIES * ENTRY_BYTES, b"\0")
    crc = zlib.crc32(table) & 0xFFFFFFFF
    primary = _header(
        at_lba=1, backup_lba=sectors - 1,
        usable_start=usable_start, usable_end=usable_end, disk_id=disk_id,
        table_lba=2, table_crc=crc,
    )
    secondary = _header(
        at_lba=sectors - 1, backup_lba=1,
        usable_start=usable_start, usable_end=usable_end, disk_id=disk_id,
        table_lba=sectors - ENTRY_SECTORS - 1, table_crc=crc,
    )
    metadata = {
        "schema": "synveil.file-only-ab-gpt/v1",
        "role": "vm-unbootable-raw-partition-layout",
        "disk_guid": str(disk_id),
        "total_bytes": sectors * SECTOR,
        "sector_bytes": SECTOR,
        "partitions": partitions,
        "protective_mbr": True,
        "primary_gpt_crc32": f"{struct.unpack_from('<I', primary, 16)[0]:08x}",
        "secondary_gpt_crc32": f"{struct.unpack_from('<I', secondary, 16)[0]:08x}",
        "entries_crc32": f"{crc:08x}",
        "filesystems_created": False,
        "efi_boot_files_installed": False,
        "owner_credentials_enrolled": False,
        "release_signature_verified": False,
        "physical_device_accessed": False,
        "bootable": False,
    }
    return metadata, _protective_mbr(sectors), table, primary, secondary


def create_image(out: Path, *, name: str, total_mib=DEFAULT_SIZE_MIB,
                 specs=DEFAULT_PARTS) -> dict:
    if not isinstance(name, str) or NAME.fullmatch(name) is None:
        raise PrototypeError("name must be a short lowercase identifier, not a path")
    if out.is_symlink():
        raise PrototypeError("refusing symlinked prototype output directory")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    if out.is_symlink() or not out.is_dir():
        raise PrototypeError("unsafe prototype directory")
    img = out / f"{name}.raw"
    manifest = out / f"{name}.layout.json"
    if img.exists() or img.is_symlink() or manifest.exists() or manifest.is_symlink():
        raise PrototypeError("existing VM image or manifest; refusing replacement")
    meta, mbr, entries, primary, secondary = build_layout(total_mib, specs)
    sectors = meta["total_bytes"] // SECTOR
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    # Never accept a file argument from the caller, and never open /dev.
    descriptor = os.open(img, flags, 0o600)
    with os.fdopen(descriptor, "wb", buffering=0) as dst:
        info = os.fstat(dst.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PrototypeError("image target is not a newly created regular file")
        os.ftruncate(dst.fileno(), meta["total_bytes"])
        dst.write(mbr)
        dst.write(primary.ljust(SECTOR, b"\0"))
        dst.write(entries)
        dst.seek((sectors - ENTRY_SECTORS - 1) * SECTOR)
        dst.write(entries)
        dst.write(secondary.ljust(SECTOR, b"\0"))
        os.fsync(dst.fileno())
    meta["image_file"] = img.name
    # Manifest also uses exclusive creation, without following a preexisting
    # path. A partial failure leaves the image for diagnosis, never overwrites.
    fd = os.open(manifest, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        json.dump(meta, target, sort_keys=True, indent=2)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True,
                        help="File-only VM image slug under out/install-prototype")
    args = parser.parse_args()
    if (REPO / "out").is_symlink():
        parser.error("refusing symlinked out directory")
    try:
        metadata = create_image(REPO / "out" / "install-prototype", name=args.name)
    except (PrototypeError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(metadata, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
