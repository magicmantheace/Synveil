#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise actual desktop supervisor auth marker policy without root/QEMU."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "build/rootfs/start-desktop"


class DesktopMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / "auth-desktop"
        self.binary = self.root / "desktop"
        self.calls = self.root / "calls"
        self.binary.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\nexit 0\n'
        )
        self.binary.chmod(0o755)
        source = SOURCE.read_text().replace(
            "/etc/synveil/auth-desktop", str(self.marker)
        )
        self.helper = self.root / "start-desktop"
        self.helper.write_text(source)
        subprocess.run(["sh", "-n", str(self.helper)], check=True)

    def run_helper(self):
        return subprocess.run(
            ["sh", str(self.helper)],
            env={**os.environ, "TEST_CALLS": str(self.calls),
                 "SYNVEIL_DESKTOP_BINARY": str(self.binary),
                 "SYNVEIL_DESKTOP_RETRY_DELAY": "0"},
            capture_output=True, text=True, timeout=5,
        )

    def test_default_developer_profile_remains_explicit(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls.read_text(), "--menu\n")

    def test_valid_authenticated_marker_selects_login(self):
        self.marker.touch(mode=0o644)
        self.marker.chmod(0o644)
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls.read_text(), "--login\n")

    def test_invalid_markers_do_not_fall_back_to_unauthenticated_menu(self):
        for form in ("symlink", "broken-link", "directory", "writable"):
            with self.subTest(form=form):
                if self.marker.is_symlink() or self.marker.is_file():
                    self.marker.unlink()
                elif self.marker.is_dir():
                    self.marker.rmdir()
                if form == "symlink":
                    destination = self.root / "real"
                    destination.touch()
                    self.marker.symlink_to(destination)
                elif form == "broken-link":
                    self.marker.symlink_to(self.root / "missing")
                elif form == "directory":
                    self.marker.mkdir()
                else:
                    self.marker.touch()
                    self.marker.chmod(0o666)
                result = self.run_helper()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("SYNVEIL_DESKTOP_MARKER_UNSAFE", result.stdout)
                self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
