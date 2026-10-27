#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Independent transfer checks; these do not boot a guest or certify media."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ab_release

spec = importlib.util.spec_from_file_location(
    "muse_browser_smoke", ROOT / "tests/smoke-native-browser.py")
browser = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = browser
spec.loader.exec_module(browser)


class StoredEvidence(unittest.TestCase):
    def test_browser_receipts_pass_semantic_validator(self):
        for directory in ("browser-qemu-proof-2026-10-27",
                          "browser-privacy-proof-2026-10-27"):
            with self.subTest(directory=directory):
                base = ROOT / "docs/validation" / directory
                receipt = json.loads((base / "browser-receipt.json").read_text())
                self.assertEqual(browser.validate_browser_results(
                    receipt["results"]), (True, []))
                self.assertFalse(receipt["physical_boot_validated"])

@unittest.skipUnless(all(shutil.which(tool) for tool in
                         ("sgdisk", "mkfs.ext4", "debugfs")),
                     "real GPT/ext4 tools required")
class LargeReleaseMedia(unittest.TestCase):
    def test_payload_larger_than_old_media_survives_packaging(self):
        # The browser UKI exceeded the old fixed 160 MiB media.
        # Exercise actual GPT/ext4 packaging and independently read
        # back the finished partition, never a physical device.
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            payload = base / "large.efi"
            with payload.open("wb") as handle:
                handle.write(b"SYNVEIL LARGE PAYLOAD\n")
                handle.seek(192 * 1024 * 1024 - 32)
                handle.write(b"tail-check" * 3 + b"!!")
            media = ab_release.build_media(payload, {"version": 2},
                                           b"x" * 64, base / "release.img")
            info = subprocess.check_output(
                ["sgdisk", "-i", "1", str(media)], text=True)
            first = int(next(line for line in info.splitlines()
                             if "First sector:" in line).split(":", 1)[1].split()[0])
            partition = base / "partition.ext4"
            with media.open("rb") as source, partition.open("wb") as dest:
                source.seek(first * 512)
                shutil.copyfileobj(source, dest)
            extracted = base / "extracted.efi"
            subprocess.run(["debugfs", "-R",
                            f"dump /synveil.release.bin {extracted}",
                            str(partition)], check=True, capture_output=True)
            self.assertEqual(extracted.stat().st_size, payload.stat().st_size)
            self.assertEqual(ab_release.sha256(extracted), ab_release.sha256(payload))


if __name__ == "__main__":
    unittest.main()
