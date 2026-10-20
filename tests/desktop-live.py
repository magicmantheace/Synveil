#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-network, no-privilege tests for the opt-in multiwindow desktop preview."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DesktopLiveProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "checkout"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "experiments/desktop-live").mkdir(parents=True)
        shutil.copy2(ROOT / "tools/desktop_live.sh", self.root / "tools/desktop_live.sh")
        for name in ("packages.list.chroot", "live-config.conf", "lightdm.conf"):
            shutil.copy2(ROOT / "experiments/desktop-live" / name,
                         self.root / "experiments/desktop-live" / name)
        self.bin = self.root / "fake-bin"
        self.bin.mkdir()
        (self.bin / "lb").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" >> "$TEST_LB_LOG"\n'
        )
        (self.bin / "id").write_text(
            '#!/bin/sh\n[ "$1" = -u ] || exit 3\nprintf "%s\\n" "$TEST_UID"\n'
        )
        for name in ("lb", "id"):
            (self.bin / name).chmod(0o755)
        self.log = self.root / "lb-commands"
        self.env = {**os.environ,
                    "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "TEST_LB_LOG": str(self.log),
                    "TEST_UID": "1000"}

    def invoke(self, *args, uid="1000"):
        return subprocess.run(
            ["bash", str(self.root / "tools/desktop_live.sh"), *args],
            env={**self.env, "TEST_UID": uid},
            capture_output=True, text=True, timeout=10,
        )

    def test_explicit_configuration_creates_only_directory_files(self):
        result = self.invoke("configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / "out/desktop-live"
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        packages = (out / "config/package-lists/synveil-desktop.list.chroot").read_text()
        for p in ("xfwm4", "xfce4-panel", "xfce4-terminal", "thunar",
                  "xfce4-settings", "lightdm", "network-manager"):
            self.assertIn(p + "\n", packages)
        self.assertNotIn("calamares\n", packages)
        args = self.log.read_text().splitlines()
        for required in ("config", "trixie", "amd64", "iso-hybrid", "none",
                         "main contrib non-free-firmware"):
            self.assertIn(required, args)
        opts = args[args.index("--bootappend-live") + 1]
        self.assertIn("live-config.noroot", opts)
        self.assertIn("boot=live components", opts)
        failsafe = args[args.index("--bootappend-live-failsafe") + 1]
        self.assertIn("nomodeset", failsafe)
        self.assertIn("live-config.noroot", failsafe)
        self.assertTrue((out / ".synveil-desktop-live-prototype").exists())
        self.assertFalse(any(out.rglob("*.iso")))
        self.assertFalse((self.root / "out/synveil-x86_64.img").exists())

    def test_refuses_existing_tree_and_symlink_without_modification(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        previous = self.log.read_bytes()
        duplicate = self.invoke("configure")
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.log.read_bytes(), previous)
        shutil.rmtree(self.root / "out/desktop-live")
        victim = self.root / "unrelated"
        victim.mkdir()
        (victim / "keep").write_text("SAFE")
        (self.root / "out/desktop-live").symlink_to(victim, target_is_directory=True)
        denied = self.invoke("configure")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual((victim / "keep").read_text(), "SAFE")

    def test_symlink_output_parent_is_rejected(self):
        victim = self.root / "unrelated"
        victim.mkdir()
        (self.root / "out").symlink_to(victim, target_is_directory=True)
        denied = self.invoke("configure")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(list(victim.iterdir()), [])

    def test_build_is_separate_explicit_and_requires_privileges(self):
        self.assertNotEqual(self.invoke("build", uid="0").returncode, 0)
        self.assertEqual(self.invoke("configure").returncode, 0)
        before = self.log.read_text()
        rejected = self.invoke("build", uid="1000")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.log.read_text(), before)
        accepted = self.invoke("build", uid="0")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.log.read_text().splitlines()[-1], "build")
        # The fake live-build never touches disks or creates a boot ISO.
        self.assertFalse(any((self.root / "out").rglob("*.iso")))

    def test_existing_iso_is_not_overwritten(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        iso = self.root / "out/desktop-live/synveil-desktop-preview-amd64.hybrid.iso"
        iso.write_bytes(b"already exists")
        result = self.invoke("build", uid="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(iso.read_bytes(), b"already exists")

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(self.root / "tools/desktop_live.sh")],
                       check=True)


if __name__ == "__main__":
    unittest.main()
