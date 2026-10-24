#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise developer VM bundle integrity, exclusivity and rollback."""
import hashlib
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from vm_bundle import create_bundle, verify_build  # noqa: E402

REV = "a" * 40

def synthetic_newc(names):
    """Tiny valid newc archive, with zero-byte regular-file records."""
    blob = bytearray()
    for name in (*names, "TRAILER!!!"):
        raw = name.encode() + b"\0"
        fields = [1, 0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(raw), 0]
        blob.extend(b"070701" + b"".join(f"{field:08x}".encode() for field in fields))
        blob.extend(raw)
        blob.extend(b"\0" * ((-len(blob)) % 4))
    blob.extend(b"\0" * ((-len(blob)) % 512))
    return bytes(blob)



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
        archive = self.out / "synveil-rootfs.tar.zst"
        archive.write_bytes(b"fixture rootfs archive")
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.cpio = self.out / "synveil-initramfs.cpio"
        self.cpio.write_bytes(synthetic_newc([
            "./init", "./usr/bin/synveil-display",
            "./usr/bin/synveil-vt",
            "./etc/synveil/desktop-autostart",
        ]))
        manifest = {
            "schema": "synveil.build/v1",
            "git": {"commit": REV, "dirty": False},
            "artifacts": {
                image.name: {"sha256": digest},
                archive.name: {"sha256": archive_digest},
                self.cpio.name: {
                    "sha256": hashlib.sha256(self.cpio.read_bytes()).hexdigest(),
                    "bytes": self.cpio.stat().st_size,
                },
            },
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

    def test_refuses_hardware_boot_profile(self):
        manifest_path = self.out / "manifests/build.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["boot_target"] = "hardware"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "hardware boot target"):
            verify_build(self.out, self.rootfs, REV)

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
        subprocess.run(["sh", "-n", str(self.destination / "run-vm.sh")],
                       check=True)

    def _launch_with_fake_repo(self, original_root=None):
        """Run bundle launcher against a stub checkout, no QEMU installed."""
        repo = self.root / "fake-repo"
        repo.mkdir(exist_ok=True)
        stub = repo / "build.sh"
        stub.write_text('#!/bin/sh\nprintf "%s" "$SYNVEIL_QEMU_NETWORK" > "$VM_CALL_LOG"\n')
        stub.chmod(0o755)
        logged = self.root / "launched"
        logged.unlink(missing_ok=True)
        env = {**os.environ, "SYNVEIL_REPO_ROOT": str(repo),
               "VM_CALL_LOG": str(logged)}
        result = subprocess.run(["sh", str(self.destination / "run-vm.sh")],
                                capture_output=True, text=True, env=env, timeout=4)
        return result, logged

    def test_bundle_verifies_integrity_and_privacy_before_launch(self):
        with patch("state_disk.subprocess.run"):
            create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        result, logged = self._launch_with_fake_repo()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(logged.read_text(), "0")
        image = self.destination / "synveil-x86_64.img"
        image.chmod(0o600)
        image.write_bytes(b"modified")
        result, logged = self._launch_with_fake_repo()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(logged.exists())
        image.write_bytes(b"stand-in UEFI image")
        image.chmod(0o400)
        state = self.destination / "synveil-state.ext4"
        state.chmod(0o644)
        result, logged = self._launch_with_fake_repo()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(logged.exists())
        state.chmod(0o600)
        self.destination.chmod(0o755)
        result, logged = self._launch_with_fake_repo()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(logged.exists())

    def test_refuses_existing_directory_without_mutation(self):
        self.destination.mkdir()
        (self.destination / "important").write_text("keep me")
        with patch("state_disk.subprocess.run") as formatter:
            with self.assertRaises(FileExistsError):
                create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        formatter.assert_not_called()
        self.assertEqual((self.destination / "important").read_text(), "keep me")

    def test_refuses_symlink_destination_without_touching_target(self):
        target = self.root / "existing"
        target.mkdir()
        (target / "important").write_text("keep me")
        self.destination.symlink_to(target)
        with self.assertRaises(FileExistsError):
            create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        self.assertEqual((target / "important").read_text(), "keep me")

    def test_rejects_tampered_rootfs_archive(self):
        archive = self.out / "synveil-rootfs.tar.zst"
        archive.write_bytes(b"wrong archive")
        with self.assertRaises(ValueError):
            verify_build(self.out, self.rootfs, REV)

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

    def test_rejects_embedded_serial_override_even_if_staging_is_clean(self):
        content = synthetic_newc([
            "init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
            "etc/synveil/desktop-autostart",
            "etc/synveil/developer-serial-recovery",
        ])
        self.cpio.write_bytes(content)
        manifest = self.out / "manifests/build.json"
        data = json.loads(manifest.read_text())
        data["artifacts"][self.cpio.name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "embedded initramfs"):
            verify_build(self.out, self.rootfs, REV)

    def test_rejects_modified_embedded_initramfs(self):
        self.cpio.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "manifest verification"):
            verify_build(self.out, self.rootfs, REV)

    def test_rejects_authenticated_preview_and_serial_root_bypass(self):
        directory = self.rootfs / "etc/synveil"
        for name in ("auth-desktop", "developer-serial-recovery"):
            marker = directory / name
            marker.touch()
            with self.assertRaisesRegex(ValueError, "cannot be packaged"):
                verify_build(self.out, self.rootfs, REV)
            marker.unlink()
        forbidden = directory / "developer-serial-recovery"
        forbidden.symlink_to("missing")
        with self.assertRaisesRegex(ValueError, "cannot be packaged"):
            verify_build(self.out, self.rootfs, REV)
        forbidden.unlink()
        self.assertFalse(self.destination.exists())

    def test_formatter_failure_cleans_only_new_bundle(self):
        with patch("state_disk.subprocess.run", side_effect=RuntimeError("mkfs failed")):
            with self.assertRaises(RuntimeError):
                create_bundle(self.out, self.rootfs, self.destination, REV, 64)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.out / "synveil-x86_64.img").exists())


if __name__ == "__main__":
    unittest.main()
