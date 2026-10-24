#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Isolated GPT image-file verification; never accesses devices or needs root."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zlib

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/ab_image_prototype.py"
spec = importlib.util.spec_from_file_location("ab_image_prototype", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SMALL = (
    ("ESP", 2), ("SYSTEM_A", 4), ("SYSTEM_B", 4),
    ("STATE", 2), ("DATA", 4), ("RECOVERY", 2),
)
HEADER = struct.Struct("<8sIIIIQQQQ16sQIII")
ENTRY = struct.Struct("<16s16sQQQ72s")


def read_gpt(path, meta):
    with path.open("rb") as f:
        size = path.stat().st_size
        sectors = size // 512
        f.seek(0)
        mbr = f.read(512)
        assert mbr[510:512] == b"\x55\xaa"
        assert mbr[450] == 0xEE

        def header_at(lba):
            f.seek(lba * 512)
            raw = f.read(512)
            assert len(raw) == 512
            fields = HEADER.unpack(raw[:92])
            assert fields[0] == b"EFI PART"
            assert fields[2] == 92
            crc = fields[3]
            canonical = raw[:16] + b"\0" * 4 + raw[20:92]
            assert (zlib.crc32(canonical) & 0xffffffff) == crc
            assert uuid.UUID(bytes_le=fields[9]) == uuid.UUID(meta["disk_guid"])
            f.seek(fields[10] * 512)
            table = f.read(fields[11] * fields[12])
            assert (zlib.crc32(table) & 0xffffffff) == fields[13]
            parts = []
            for i in range(6):
                entry = ENTRY.unpack(table[i * 128:(i + 1) * 128])
                name = entry[5].decode("utf-16le").rstrip("\0")
                parts.append((name, str(uuid.UUID(bytes_le=entry[1])),
                              entry[2], entry[3], entry[0]))
            return fields, parts, table

        a, a_parts, a_table = header_at(1)
        b, b_parts, b_table = header_at(sectors - 1)
        assert a[5] == 1 and a[6] == sectors - 1 and a[10] == 2
        assert b[5] == sectors - 1 and b[6] == 1
        assert b[10] == sectors - 33
        assert a_table == b_table and a_parts == b_parts
        assert (a[7], a[8]) == (34, sectors - 34)
        assert a[13] == b[13]
        for (name, partuuid, start, end, type_guid), expected in zip(
                a_parts, meta["partitions"]):
            assert name == expected["name"]
            assert partuuid == expected["partuuid"]
            assert start == expected["first_lba"]
            assert end == expected["last_lba"]
            assert start % 2048 == 0
            assert end > start and end <= a[8]
            want_type = mod.EFI_TYPE if name == "ESP" else mod.LINUX_TYPE
            assert uuid.UUID(bytes_le=type_guid) == want_type


class AbFileImageTests(unittest.TestCase):
    def test_real_file_has_both_crc_valid_gpt_headers_and_six_partitions(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "out/install-prototype"
            meta = mod.create_image(out, name="vm-a-b", total_mib=24,
                                    specs=SMALL)
            self.assertEqual(meta["schema"], "synveil.file-only-ab-gpt/v1")
            self.assertEqual(meta["total_bytes"], 24 * 1024 * 1024)
            self.assertFalse(meta["bootable"])
            for key in ("filesystems_created", "efi_boot_files_installed",
                        "owner_credentials_enrolled", "physical_device_accessed"):
                self.assertFalse(meta[key])
            image = out / "vm-a-b.raw"
            stored = json.loads((out / "vm-a-b.layout.json").read_text())
            self.assertEqual(meta, stored)
            self.assertEqual(image.stat().st_mode & 0o777, 0o600)
            read_gpt(image, meta)
            self.assertEqual(len({p["partuuid"] for p in meta["partitions"]}), 6)

    def test_non_destructive_conflict_and_symlink_refusals(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "out/install-prototype"
            first = mod.create_image(out, name="only-once", total_mib=24,
                                     specs=SMALL)
            image = out / "only-once.raw"
            mtime = image.stat().st_mtime_ns
            with self.assertRaises(mod.PrototypeError):
                mod.create_image(out, name="only-once", total_mib=24,
                                 specs=SMALL)
            self.assertEqual(image.stat().st_mtime_ns, mtime)
            self.assertEqual(first["disk_guid"],
                             json.loads((out / "only-once.layout.json").read_text())
                             ["disk_guid"])
            (out / "another.raw").symlink_to(image)
            with self.assertRaises(mod.PrototypeError):
                mod.create_image(out, name="another", total_mib=24, specs=SMALL)
            outside = Path(root) / "other"
            outside.mkdir()
            link = Path(root) / "linked"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(mod.PrototypeError):
                mod.create_image(link, name="no", total_mib=24, specs=SMALL)
            self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_layout_names_and_sizes_fail_closed(self):
        for name in ("/dev/sda", "../win", "", "A", "ab.raw", "z" * 50):
            with self.subTest(name=name), self.assertRaises(mod.PrototypeError):
                mod.create_image(Path("/a/path/that/will/not/exist"),
                                 name=name, total_mib=24, specs=SMALL)
        for bad_size in (True, None, 0, 12, 100000):
            with self.subTest(size=bad_size), self.assertRaises(mod.PrototypeError):
                mod.build_layout(bad_size, SMALL)
        for bad_parts in (
            tuple(reversed(SMALL)),
            SMALL[:-1],
            SMALL[:1] + (("SYSTEM_A", 0),) + SMALL[2:],
            SMALL[:1] + (("SYSTEM_A", 99),) + SMALL[2:],
        ):
            with self.subTest(parts=bad_parts), self.assertRaises(mod.PrototypeError):
                mod.build_layout(24, bad_parts)

    def test_cli_refuses_physical_path_args_and_invalid_slugs(self):
        # CLI has no --output, --device, --disk, format or write-to-drive option.
        for args in (
            ["--name", "/dev/sda"],
            ["--name", "safe", "--output", "/dev/sda"],
            ["--name", "safe", "--device", "/dev/sda"],
        ):
            result = subprocess.run([sys.executable, str(SCRIPT), *args],
                                    text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
