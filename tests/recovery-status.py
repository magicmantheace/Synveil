#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host security checks for the noninteractive, read-only recovery status path.

Guest validation is still required before calling this independent recovery.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build/rootfs/recovery-status"
INIT = ROOT / "build/rootfs/init"
ROOTFS = ROOT / "build/scripts/rootfs.sh"


class ReadOnlyRecoveryTests(unittest.TestCase):
    def test_bounded_output_and_no_arguments(self):
        result = subprocess.run(["sh", str(SCRIPT)], capture_output=True,
                                text=True, timeout=3, check=True)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[1], "SYNVEIL_RECOVERY_STATUS_V1")
        self.assertEqual(lines[-1], "SYNVEIL_RECOVERY_READ_ONLY")
        self.assertEqual(len(lines), 7)
        for prefix in ("STATE", "CORE", "SEAT", "FRAMEBUFFER"):
            self.assertEqual(sum(line.startswith("SYNVEIL_RECOVERY_" + prefix + "_")
                                 for line in lines), 1)
        self.assertEqual(result.stderr, "")
        denied = subprocess.run(["sh", str(SCRIPT), "--shell"],
                                capture_output=True, text=True, timeout=3)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, "")

    def test_mount_state_only_counts_ext4_at_var(self):
        source = SCRIPT.read_text()
        with tempfile.TemporaryDirectory() as temp:
            mounts = Path(temp) / "mounts"
            shadow = Path(temp) / "recovery-status"
            shadow.write_text(source.replace("/proc/mounts", str(mounts)))
            for content, expected in (
                ("/dev/vdb /var ext4 rw,relatime 0 0\n", "MOUNTED"),
                ("/dev/vdb /var ext3 rw 0 0\n", "NOT_MOUNTED"),
                ("/dev/vdb /var-backup ext4 rw 0 0\n", "NOT_MOUNTED"),
                ("", "NOT_MOUNTED"),
            ):
                with self.subTest(content=content):
                    mounts.write_text(content)
                    output = subprocess.check_output(["sh", str(shadow)], text=True,
                                                     timeout=3)
                    self.assertIn("SYNVEIL_RECOVERY_STATE_" + expected, output)

    def test_boot_mode_precedes_state_mount_and_services_and_locks_console(self):
        source = INIT.read_text()
        start = source.index("# BEGIN READ_ONLY_RECOVERY")
        end = source.index("# END READ_ONLY_RECOVERY")
        guarded = source[start:end]
        self.assertIn("synveil.recovery=status", guarded)
        self.assertIn("/usr/libexec/synveil/recovery-status", guarded)
        self.assertIn("serial_lock_forever", guarded)
        self.assertNotIn("/bin/sh", guarded)
        self.assertNotIn("boot_failure_recovery", guarded)
        self.assertLess(start, source.index("/usr/libexec/synveil/mount-state"))
        self.assertLess(start, source.index("/usr/libexec/synveil/start-core"))
        self.assertLess(start, source.index("/usr/libexec/synveil/start-seat"))
        self.assertLess(start, source.index("/usr/libexec/synveil/start-desktop"))
        self.assertIn('install -D -m 0755 "$ROOT_DIR/build/rootfs/recovery-status"',
                      ROOTFS.read_text())
        subprocess.run(["sh", "-n", str(INIT)], check=True)
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)


    def test_recovery_status_installs_into_a_fresh_rootfs(self):
        # Execute the real shell fragment: a malformed escaped line break
        # passes shell -n but breaks the full native rootfs build.
        lines = ROOTFS.read_text().splitlines()
        index = next(i for i, line in enumerate(lines)
                     if 'install -D -m 0755 "$ROOT_DIR/build/rootfs/recovery-status"' in line)
        command = "\n".join(lines[index:index + 2]) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "rootfs"
            result = subprocess.run(
                ["bash", "-euc", command],
                env={"ROOT_DIR": str(ROOT), "ROOTFS_DIR": str(destination),
                     "PATH": "/usr/bin:/bin"},
                capture_output=True, text=True, timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = destination / "usr/libexec/synveil/recovery-status"
            self.assertEqual(installed.read_bytes(), SCRIPT.read_bytes())
            self.assertEqual(installed.stat().st_mode & 0o777, 0o755)

if __name__ == "__main__":
    unittest.main()
