#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host simulation of state-mount path checks, without privileged mounts.

Only fixed test paths and fake mount/chown commands are substituted; the
actual ext4 and UID behavior still requires the separate QEMU guest tests.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOUNT_STATE = ROOT / "build/rootfs/mount-state"


class StateMountSafetyTests(unittest.TestCase):
    def setUp(self):
        # Pin a conventional umask for the fixture and the script process it
        # spawns: mount-state correctly rejects group/world-writable
        # privileged directories, and with an ambient umask such as 0007 the
        # fixture's own directories (and the script's mkdir results) come
        # out 0770 and fail that check for environmental reasons only.
        self._umask = os.umask(0o022)
        self.addCleanup(os.umask, self._umask)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.var = self.root / "var"
        self.home = self.root / "home/synveil"
        self.var.mkdir()
        self.home.mkdir(parents=True)
        self.disk = self.root / "disk"
        self.disk.touch()
        self.calls = self.root / "calls"
        fakebin = self.root / "fakebin"
        fakebin.mkdir()
        for name in ("mount", "umount", "chown"):
            command = fakebin / name
            command.write_text(
                "#!/bin/sh\n"
                f"printf '{name} %s\\n' \"$*\" >> \"$SYNVEIL_TEST_CALLS\"\n"
                "case \"$*\" in\n"
                "  *remount*) [ \"$SYNVEIL_FAIL_REMOUNT\" != 1 ] ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n"
            )
            command.chmod(0o755)
        self.env = {**os.environ, "PATH": str(fakebin) + ":" + os.environ["PATH"],
                    "SYNVEIL_TEST_CALLS": str(self.calls)}
        source = MOUNT_STATE.read_text()
        source = source.replace("state=/dev/vdb", f"state={self.disk}")
        source = source.replace('[ ! -b "$state" ]', '[ ! -f "$state" ]')
        source = source.replace('0:0', f'{os.getuid()}:{os.getgid()}')
        source = source.replace('/var/lib/synveil/home/synveil', '__PERSIST_HOME__')
        source = source.replace('/var', str(self.var))
        source = source.replace('/home/synveil', str(self.home))
        source = source.replace('__PERSIST_HOME__', str(self.var / 'lib/synveil/home/synveil'))
        self.test_script = self.root / "mount-state-under-test"
        self.test_script.write_text(source)

    def boot(self, **env):
        result = subprocess.run(["sh", str(self.test_script)],
                                env={**self.env, **env}, capture_output=True,
                                text=True, timeout=4)
        return result, self.calls.read_text() if self.calls.exists() else ""

    def test_private_fresh_state_and_demo_home(self):
        result, calls = self.boot()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SYNVEIL_STATE_READY", result.stdout)
        self.assertIn("SYNVEIL_HOME_READY", result.stdout)
        self.assertEqual((self.var / "lib/synveil").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.var / "tmp").stat().st_mode & 0o7777, 0o1777)
        self.assertIn("mount -o bind", calls)

    def test_symlinked_privileged_parent_never_changes_target(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o777)
        outside.chmod(0o777)
        (self.var / "lib").symlink_to(outside, target_is_directory=True)
        result, calls = self.boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe state directory", result.stdout)
        self.assertNotIn("SYNVEIL_STATE_READY", result.stdout)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o777)
        self.assertIn("umount", calls)
        self.assertNotIn("chown", calls)

    def test_world_writable_parent_is_rejected_not_chmodded(self):
        parent = self.var / "log"
        parent.mkdir()
        parent.chmod(0o777)
        result, _ = self.boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o777)

    def test_symlinked_home_is_not_chowned_or_bound(self):
        source = self.var / "lib/synveil/home"
        source.mkdir(parents=True, mode=0o700)
        (self.var / "lib").chmod(0o700)
        (self.var / "lib/synveil").chmod(0o700)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (source / "synveil").symlink_to(elsewhere, target_is_directory=True)
        result, calls = self.boot()
        self.assertEqual(result.returncode, 0)
        self.assertIn("SYNVEIL_HOME_UNAVAILABLE", result.stdout)
        self.assertIn("SYNVEIL_STATE_READY", result.stdout)
        self.assertNotIn("chown", calls)
        self.assertNotIn("mount -o bind", calls)

    def test_failed_restrictive_remount_does_not_claim_home(self):
        result, calls = self.boot(SYNVEIL_FAIL_REMOUNT="1")
        self.assertEqual(result.returncode, 0)
        self.assertIn("SYNVEIL_HOME_UNAVAILABLE", result.stdout)
        self.assertNotIn("SYNVEIL_HOME_READY", result.stdout)
        self.assertIn("umount", calls)


if __name__ == "__main__":
    unittest.main()
