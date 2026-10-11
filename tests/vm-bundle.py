#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise developer VM bundle integrity, exclusivity and rollback."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from vm_bundle import create_bundle, verify_build  # noqa: E402

REV = "a" * 40


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "out"
        self.out.mkdir()
        (self.out / "manifests").mkdir()
        self.rootfs = self.root / "rootfs"
        required = (
            "usr/bin/synveil-display", "usr/bin/synveil-input",
            "usr/bin/synveil-vt", "etc/synveil/desktop-autostart",
            "home/synveil",
        )
        for file in required:
            p = self.rootfs / file
            p.parent.mkdir(parents=True, exist_ok=True)
            if file.endswith("home/synveil"):
                p.mkdir()
            else:
                p.write_bytes(b"test-archive-entry")
        passwd = self.rootfs / "etc/passwd"
        passwd.parent.mkdir(parents=True, exist_ok=True)
        passwd.write_text("root:x:0:0::/root:/bin/sh\n"
                          "synveil:x:1000:1000:Demo:/home/synveil:/bin/sh\n")
        image = self.out / "synveil-x86_64.img"
        image.write_bytes(b"stand-in UEFI image")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        manifest = {
            "schema": "synveil.build/v1",
            "git": {"commit": REV, "dirty": False},
            "artifacts": {image.name: {"sha256": digest}},
        }
        (self.out / "manifests/build.json").write_text(json.dumps(manifest))
        (self.out / "manifests/kernel.config").write_text(
            "CONFIG_DRM_VIRTIO_GPU=y\n"
            "CONFIG_DRM_FBDEV_EMULATION=y\n"
            "CONFIG_FB_DEVICE=y\n"
            "CONFIG_INPUT_EVDEV=y\n"
            "CONFIG_USB_HID=y\n"
        )
        self.destination = self.root / "bundle"

    def test_creates_private_distinct_disks_and_checksums(self):
        with patch("state_disk.subprocess.run") as formatter:
            create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        formatter.assert_called_once()
        self.assertEqual((self.destination / "synveil-state.ext4").stat().st_size,
                         64 * 1024 * 1024)
        self.assertEqual((self.destination / "synveil-state.ext4").stat().st_mode & 0o777,
                         0o600)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.destination / "synveil-x86_64.img").stat().st_mode & 0o777,
                         0o400)
        manifest = json.loads((self.destination / "VM-BUNDLE.json").read_text())
        self.assertEqual(manifest["git_revision"], REV)
        self.assertFalse(manifest["user_install_validated"])
        checksums = (self.destination / "SHA256SUMS").read_text()
        self.assertIn("synveil-x86_64.img", checksums)
        self.assertNotIn("synveil-state.ext4", checksums)
        launch = (self.destination / "run-vm.sh").read_text()
        self.assertIn("SYNVEIL_QEMU_NETWORK=0", launch)
        self.assertIn("SYNVEIL_QEMU_STATE_DISK=", launch)

    def test_refuses_existing_directory_without_mutation(self):
        self.destination.mkdir()
        (self.destination / "important").write_text("keep me")
        with patch("state_disk.subprocess.run") as formatter:
            with self.assertRaises(FileExistsError):
                create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        formatter.assert_not_called()
        self.assertEqual((self.destination / "important").read_text(), "keep me")

    def test_rejects_tampered_or_stale_boot_image(self):
        with self.assertRaises(ValueError):
            verify_build(self.out, self.rootfs, "b" * 40)
        (self.out / "synveil-x86_64.img").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            verify_build(self.out, self.rootfs, REV)
        self.assertFalse(self.destination.exists())

    def test_rejects_missing_desktop_account_and_kernel(self):
        (self.rootfs / "etc/passwd").write_text("root:x:0:0::/root:/bin/sh\n")
        with self.assertRaises(ValueError):
            verify_build(self.out, self.rootfs, REV)
        (self.rootfs / "etc/passwd").write_text(
            "synveil:x:1000:1000:Demo:/home/synveil:/bin/sh\n")
        (self.out / "manifests/kernel.config").write_text("CONFIG_DRM_VIRTIO_GPU=y\n")
        with self.assertRaises(ValueError):
            verify_build(self.out, self.rootfs, REV)

    def test_formatter_failure_cleans_only_new_bundle(self):
        with patch("state_disk.subprocess.run", side_effect=RuntimeError("mkfs failed")):
            with self.assertRaises(RuntimeError):
                create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.out / "synveil-x86_64.img").exists())


if __name__ == "__main__":
    unittest.main()
