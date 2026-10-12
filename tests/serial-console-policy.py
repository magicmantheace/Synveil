#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host fixtures for the immutable authenticated-build serial-root boundary."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "build/rootfs/init"


class SerialRecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = self.root / "auth-desktop"
        self.debug = self.root / "developer-serial-recovery"
        self.source = INIT.read_text()
        assert "# BEGIN SERIAL_GUARD" in self.source
        assert "# END SERIAL_GUARD" in self.source

    def verdict(self):
        # Run the *same* actual shell function as guest /init, with only its
        # immutable marker paths redirected into a private test directory.
        body = self.source.split("# BEGIN SERIAL_GUARD", 1)[1]
        body = body.split("# END SERIAL_GUARD", 1)[0]
        body = body.replace("/etc/synveil/auth-desktop", str(self.auth))
        body = body.replace("/etc/synveil/developer-serial-recovery", str(self.debug))
        script = body + "\nif serial_locked; then echo LOCKED; else echo DEVELOPER; fi\n"
        return subprocess.check_output(["sh", "-c", script], text=True).strip()

    def test_default_boot_is_explicit_developer_recovery(self):
        self.assertEqual(self.verdict(), "DEVELOPER")

    def test_authenticated_preview_denies_unprotected_root_serial(self):
        self.auth.touch()
        self.assertEqual(self.verdict(), "LOCKED")

    def test_explicit_initramfs_debug_override_is_developer_only(self):
        self.auth.touch()
        self.debug.touch()
        self.assertEqual(self.verdict(), "DEVELOPER")

    def test_unsafe_symlink_override_never_unlocks(self):
        self.auth.touch()
        self.debug.symlink_to(self.root / "missing")
        self.assertEqual(self.verdict(), "LOCKED")

    def test_broken_auth_marker_symlink_still_fails_closed(self):
        self.auth.symlink_to(self.root / "missing")
        self.assertEqual(self.verdict(), "LOCKED")

    def test_early_mount_failure_and_normal_boot_share_guard(self):
        self.assertLess(self.source.index("# BEGIN SERIAL_GUARD"),
                        self.source.index("mount -t proc"))
        self.assertIn("mount -t proc proc /proc || {", self.source)
        self.assertIn("mount -t sysfs sysfs /sys || {", self.source)
        self.assertGreaterEqual(self.source.count("    boot_failure_recovery"), 2)
        final = self.source.index("Recovery/bootstrap shell (DEVELOPER VM ONLY)")
        self.assertLess(self.source.index("if serial_locked; then\n    serial_lock_forever"), final)
        subprocess.run(["sh", "-n", str(INIT)], check=True)


if __name__ == "__main__":
    unittest.main()
