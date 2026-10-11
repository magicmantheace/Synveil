#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""State disk creation never replaces or damages an existing file."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from state_disk import create_state_disk  # noqa: E402


class StateDiskTests(unittest.TestCase):
    def test_creates_private_raw_image_with_expected_formatter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.ext4"
            with patch("state_disk.subprocess.run") as run:
                create_state_disk(path, 64)
            self.assertEqual(path.stat().st_size, 64 * 1024 * 1024)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            run.assert_called_once_with(
                ["mkfs.ext4", "-F", "-q", "-L", "SYNVEIL_STATE", str(path)], check=True)

    def test_existing_content_remains_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing"
            path.write_bytes(b"important")
            with patch("state_disk.subprocess.run") as run:
                with self.assertRaises(FileExistsError):
                    create_state_disk(path, 64)
            run.assert_not_called()
            self.assertEqual(path.read_bytes(), b"important")

    def test_symlink_target_remains_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"important")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                create_state_disk(link, 64)
            self.assertEqual(target.read_bytes(), b"important")

    def test_rejects_invalid_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            for size in (0, 63, 16385, -1):
                with self.assertRaises(ValueError):
                    create_state_disk(path, size)
            self.assertFalse(path.exists())

    def test_failed_mkfs_removes_new_partial_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            with patch("state_disk.subprocess.run",
                       side_effect=subprocess.CalledProcessError(1, "mkfs.ext4")):
                with self.assertRaises(subprocess.CalledProcessError):
                    create_state_disk(path, 64)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
