#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-network, no-privilege tests for the opt-in multiwindow desktop preview."""
import importlib.util
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
        for name in ("packages.list.chroot", "live-config.conf", "lightdm.conf",
                     "guest-proof-session.sh", "guest-proof-relay.sh",
                     "guest-proof.desktop", "guest-proof.service"):
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
        self.assertIn("console=ttyS0,115200n8", opts)
        self.assertIn("console=tty0", opts)
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

    def test_proof_is_session_only_and_reports_actual_window_operations(self):
        self.assertEqual(self.invoke("configure").returncode, 0)
        base = self.root / "out/desktop-live/config/includes.chroot"
        session = base / "usr/local/bin/synveil-guest-proof-session"
        reporter = base / "usr/local/libexec/synveil-guest-proof-relay"
        autostart = base / "etc/xdg/autostart/synveil-guest-proof.desktop"
        service = base / "etc/systemd/system/synveil-guest-proof.service"
        wants = base / "etc/systemd/system/multi-user.target.wants/synveil-guest-proof.service"
        for p in (session, reporter):
            self.assertTrue(p.is_file())
            self.assertTrue(p.stat().st_mode & 0o111)
            subprocess.run(["sh", "-n", str(p)], check=True)
        self.assertIn("Exec=/usr/local/bin/synveil-guest-proof-session",
                      autostart.read_text())
        self.assertIn("ExecStart=/usr/local/libexec/synveil-guest-proof-relay",
                      service.read_text())
        self.assertTrue(wants.is_symlink())
        self.assertEqual(wants.resolve(), service.resolve())
        text = session.read_text()
        for operation in ("wmctrl -ir", "xwininfo -id", "xdotool windowminimize",
                          "xprop -id", "thunar", "xfce4-terminal"):
            self.assertIn(operation, text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS", text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_PROBE_FAILED", text)
        relay_text = reporter.read_text()
        self.assertIn("SYNVEIL_QEMU_RELAY_STARTED", relay_text)
        self.assertIn("SYNVEIL_QEMU_DESKTOP_PROBE_STARTED", relay_text)

    def test_qemu_status_requires_exact_guest_marker_and_real_scanout(self):
        spec = importlib.util.spec_from_file_location(
            "desktop_smoke", ROOT / "tests/smoke-desktop-live.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.probe_status(b"EFI loading\n"), "pending")
        self.assertEqual(module.probe_status(b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n"),
                         "passed")
        self.assertEqual(module.probe_status(
            b"SYNVEIL_QEMU_DESKTOP_PROBE_FAILED\n"
            b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS\n"), "failed")
        self.assertEqual(module.probe_status(
            b"X-SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS-Y\n"), "pending")
        image = self.root / "screen.ppm"
        # Deterministic RGB gradient, 320x200, not a single flat color.
        pixels = bytes((n % 256 for n in range(320 * 200 * 3)))
        image.write_bytes(b"P6\n320 200\n255\n" + pixels)
        self.assertTrue(module.screenshot_is_nonblank(image))
        image.write_bytes(b"P6\n320 200\n255\n" + bytes(320 * 200 * 3))
        self.assertFalse(module.screenshot_is_nonblank(image))

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(self.root / "tools/desktop_live.sh")],
                       check=True)


if __name__ == "__main__":
    unittest.main()
