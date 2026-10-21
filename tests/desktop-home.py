#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Non-privileged test fixtures for a fixed-file ext4 live-home prototype."""
import importlib.util
from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools/create_desktop_home_image.py"
RUNNER = ROOT / "tests/smoke-desktop-live.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


home = load(SRC, "test_vm_home")
smoke = load(RUNNER, "test_vm_home_runner")


class DesktopHomeImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name) / "out/desktop-live"
        self.dir.mkdir(parents=True)
        (self.dir / home.MARKER).write_text("VM only")
        self.mkfs = Path(temp.name) / "fake-mkfs"
        self.log = Path(temp.name) / "mkfs-args.txt"
        self.mkfs.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$@" > "$TEST_MKFS_ARGS"\n'
            'exit 0\n'
        )
        self.mkfs.chmod(0o700)

    def create(self):
        with patch.dict(os.environ, {"TEST_MKFS_ARGS": str(self.log)}):
            return home.create_home_image(folder=self.dir, mkfs=str(self.mkfs))

    def test_creates_exactly_one_regular_private_sparse_file_and_home_only_seed(self):
        image = self.create()
        self.assertEqual(image.name, "home-persistence.img")
        self.assertEqual(image.stat().st_size, 256 * 1024 * 1024)
        self.assertEqual(image.stat().st_mode & 0o777, 0o600)
        self.assertTrue(stat.S_ISREG(image.stat().st_mode))
        args = self.log.read_text().splitlines()
        self.assertEqual(args[:5], ["-q", "-F", "-L", "synvmhome", "-d"])
        self.assertEqual(args[-1], str(image))
        seed = Path(args[5])
        self.assertEqual(seed.name.startswith("synveil-home-seed-"), True)
        self.assertFalse(seed.exists())  # temporary seed is cleaned
        self.assertIn('write_text("/home\\n"', SRC.read_text())
        self.assertNotIn(" / union", SRC.read_text())
        before = image.stat().st_mtime_ns
        with self.assertRaises(home.HomeImageError):
            self.create()
        self.assertEqual(image.stat().st_mtime_ns, before)

    def test_missing_marker_and_symlinked_paths_fail_closed(self):
        (self.dir / home.MARKER).unlink()
        with self.assertRaises(home.HomeImageError):
            self.create()
        (self.dir / home.MARKER).write_text("restored")
        other = self.dir.parent / "other"
        other.mkdir()
        symlink = self.dir.parent / "alias"
        symlink.symlink_to(self.dir, target_is_directory=True)
        with self.assertRaises(home.HomeImageError):
            home.create_home_image(folder=symlink, mkfs=str(self.mkfs))
        self.assertFalse((other / home.BASENAME).exists())
        # A symlink masquerading as the new file cannot be followed.
        victim = self.dir.parent / "victim"
        victim.write_text("unchanged")
        (self.dir / home.BASENAME).symlink_to(victim)
        with self.assertRaises(home.HomeImageError):
            self.create()
        self.assertEqual(victim.read_text(), "unchanged")

    def test_runner_rejects_block_devices_or_arbitrary_paths_before_qemu(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = Path(tmp) / "valid-test-file.iso"
            iso.write_bytes(b"fake" * 262145)
            with self.assertRaisesRegex(ValueError, "non-prototype"):
                smoke.run(iso, Path(tmp) / "evidence", 40,
                          home_disk=Path("/dev/sda"), home_stage="write")
            with self.assertRaisesRegex(ValueError, "requires explicit"):
                smoke.run(iso, Path(tmp) / "evidence", 40,
                          home_disk=None, home_stage="read")

    def test_real_guest_proof_requires_distinct_write_then_recovered_markers(self):
        script = (ROOT / "experiments/desktop-live/guest-proof-session.sh").read_text()
        relay = (ROOT / "experiments/desktop-live/guest-proof-relay.sh").read_text()
        for marker in ("SYNVEIL_QEMU_HOME_WRITTEN",
                       "SYNVEIL_QEMU_HOME_RECOVERED",
                       "SYNVEIL_QEMU_HOME_PROOF_FAILED"):
            self.assertIn(marker, script)
            self.assertIn(marker, relay)
        self.assertIn("mountpoint -q /home", script)
        self.assertIn("sync \"$probe\"", script)
        self.assertIn("HOME_WRITTEN", RUNNER.read_text())
        for code in ("guest-proof-session.sh", "guest-proof-relay.sh"):
            subprocess.run(["sh", "-n", str(ROOT / "experiments/desktop-live" / code)],
                           check=True)


if __name__ == "__main__":
    unittest.main()
