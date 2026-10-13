#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Strict host checks for the separately packaged authenticated QEMU VM."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from auth_vm_bundle import create_authenticated_bundle, verify_authenticated_fixture  # noqa: E402

REV = "a" * 40
REQUIRED = ("init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
            "usr/sbin/veil-enroll", "usr/sbin/veil-seat",
            "etc/synveil/desktop-autostart", "etc/synveil/auth-desktop")


def newc(names, overrides=None):
    overrides = overrides or {}
    blob = bytearray()
    for name in (*names, "TRAILER!!!"):
        raw = name.encode() + b"\0"
        fields = [1, overrides.get(name, 0o100644), 0, 0, 1, 0,
                  0, 0, 0, 0, 0, len(raw), 0]
        blob.extend(b"070701" + b"".join(f"{value:08x}".encode() for value in fields))
        blob.extend(raw)
        blob.extend(b"\0" * (-len(blob) % 4))
    blob.extend(b"\0" * (-len(blob) % 512))
    return bytes(blob)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AuthenticatedBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = self.root / "graphical-onboard"
        (self.fixture / "manifests").mkdir(parents=True)
        (self.fixture / "synveil-x86_64.img").write_bytes(b"synthetic EFI boot image")
        (self.fixture / "synveil-x86_64.efi").write_bytes(b"synthetic EFI executable")
        (self.fixture / "synveil-initramfs.cpio").write_bytes(newc(REQUIRED))
        self.destination = self.root / "bundle"
        self.refresh_receipts()

    def refresh_receipts(self):
        artifacts = {}
        for name in ("synveil-x86_64.img", "synveil-x86_64.efi",
                     "synveil-initramfs.cpio"):
            path = self.fixture / name
            artifacts[name] = {"sha256": sha(path), "bytes": path.stat().st_size}
        path = self.fixture / "manifests/fixture.json"
        path.write_text(json.dumps({"case": "graphical-onboard", "git_commit": REV,
                                    "artifacts": artifacts}))
        (self.fixture / "manifests/validation.json").write_text(json.dumps({
            "schema": "synveil.guest-proof/v1", "case": "graphical-onboard",
            "git_commit": REV, "fixture_sha256": sha(path),
            "result": "passed", "independent_qemu_boots": 2,
            "locked_serial": True, "first_user_enrollment": True,
            "persistent_authentication": True,
        }))

    def create(self):
        with patch("state_disk.subprocess.run"):
            return create_authenticated_bundle(self.fixture, self.destination, REV, 64)

    def launch(self, *, revision=REV, dirty=False):
        fake_repo = self.root / "repo"
        fake_repo.mkdir(exist_ok=True)
        stub = fake_repo / "build.sh"
        stub.write_text(
            '#!/bin/sh\nif [ -z "$SYNVEIL_QEMU_QMP_SOCKET" ]; then '
            'qmp=none; else qmp=enabled; fi\n'
            'printf "%s %s %s" "$SYNVEIL_QEMU_NETWORK" "$SYNVEIL_QEMU_DISPLAY" '
            '"$qmp" > "$VM_CALL_LOG"\n')
        stub.chmod(0o755)
        fake_bin = self.root / "bin"
        fake_bin.mkdir(exist_ok=True)
        fake_git = fake_bin / "git"
        fake_git.write_text(
            '#!/bin/sh\nif [ "$3" = rev-parse ]; then '
            'printf "%s\\n" "$FAKE_HEAD"; '
            'elif [ "$3" = status ]; then printf "%s" "$FAKE_DIRTY"; '
            'else exit 1; fi\n')
        fake_git.chmod(0o755)
        log = self.root / "launched"
        log.unlink(missing_ok=True)
        env = {**os.environ, "SYNVEIL_REPO_ROOT": str(fake_repo),
               "PATH": str(fake_bin) + ":" + os.environ.get("PATH", ""),
               "FAKE_HEAD": revision, "FAKE_DIRTY": " M build.sh" if dirty else "",
               "VM_CALL_LOG": str(log), "SYNVEIL_QEMU_QMP_SOCKET": "/tmp/unsafe"}
        result = subprocess.run(
            ["sh", str(self.destination / "run-vm.sh")], env=env,
            capture_output=True, text=True, timeout=5,
        )
        return result, log

    def test_private_bundle_uses_clean_disk_and_offline_launcher(self):
        self.create()
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        state = self.destination / "synveil-state.ext4"
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.stat().st_size, 64 * 1024 * 1024)
        self.assertFalse((self.destination / "graphical-onboard-state.ext4").exists())
        metadata = json.loads((self.destination / "VM-BUNDLE.json").read_text())
        self.assertFalse(metadata["physical_install_allowed"])
        self.assertFalse(metadata["signed_release"])
        self.assertEqual(metadata["independent_qemu_boots_validated"], 2)
        self.assertNotIn("synveil-state.ext4",
                         (self.destination / "SHA256SUMS").read_text())
        self.assertEqual((self.destination / "synveil-x86_64.img").stat().st_mode & 0o777,
                         0o400)
        subprocess.run(["sh", "-n", str(self.destination / "run-vm.sh")], check=True)
        launched, log = self.launch()
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertEqual(log.read_text(), "0 gtk none")

    def test_rejects_mismatched_or_dirty_checkout_at_launch(self):
        self.create()
        for params in ({"revision": "b" * 40}, {"dirty": True}):
            result, log = self.launch(**params)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checkout revision", result.stderr)
            self.assertFalse(log.exists())

    def test_refuses_existing_destination_or_symlink(self):
        self.destination.mkdir()
        (self.destination / "keep").write_text("important")
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((self.destination / "keep").read_text(), "important")
        self.destination.rename(self.root / "old-bundle")
        self.destination.symlink_to(self.root / "old-bundle")
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual((self.root / "old-bundle/keep").read_text(), "important")

    def test_stale_or_missing_success_evidence_denied(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            verify_authenticated_fixture(self.fixture, "b" * 40)
        (self.fixture / "manifests/validation.json").unlink()
        with self.assertRaises(ValueError):
            self.create()
        self.assertFalse(self.destination.exists())

    def test_rejects_invalid_guest_proof_fields(self):
        path = self.fixture / "manifests/validation.json"
        for key, value in (("result", "failed"), ("independent_qemu_boots", 1),
                           ("locked_serial", False), ("persistent_authentication", False)):
            original = json.loads(path.read_text())
            original[key] = value
            path.write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError, "successful"):
                self.create()
            self.assertFalse(self.destination.exists())
            self.refresh_receipts()

    def test_rejects_modified_image_and_proof_mismatch(self):
        (self.fixture / "synveil-x86_64.img").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "invalid authenticated"):
            self.create()
        self.refresh_receipts()
        proof = self.fixture / "manifests/validation.json"
        data = json.loads(proof.read_text())
        data["fixture_sha256"] = "0" * 64
        proof.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "successful"):
            self.create()

    def test_embedded_serial_override_is_rejected(self):
        (self.fixture / "synveil-initramfs.cpio").write_bytes(newc(
            (*REQUIRED, "etc/synveil/developer-serial-recovery")))
        self.refresh_receipts()
        with self.assertRaisesRegex(ValueError, "root serial"):
            self.create()

    def test_wrong_or_symlinked_auth_marker_refused(self):
        (self.fixture / "synveil-initramfs.cpio").write_bytes(newc(
            tuple(x for x in REQUIRED if x != "etc/synveil/auth-desktop")))
        self.refresh_receipts()
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.create()
        (self.fixture / "synveil-initramfs.cpio").write_bytes(
            newc(REQUIRED, {"etc/synveil/auth-desktop": 0o120777}))
        self.refresh_receipts()
        with self.assertRaisesRegex(ValueError, "marker"):
            self.create()

    def test_launcher_rechecks_checksums_and_private_state(self):
        self.create()
        image = self.destination / "synveil-x86_64.img"
        image.chmod(0o600)
        image.write_bytes(b"tampered")
        result, log = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(log.exists())
        state = self.destination / "synveil-state.ext4"
        state.chmod(0o644)
        result, log = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(log.exists())
        self.destination.chmod(0o755)
        result, log = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(log.exists())

    def test_formatter_failure_cleans_only_new_bundle(self):
        with patch("state_disk.subprocess.run", side_effect=RuntimeError("mkfs failed")):
            with self.assertRaises(RuntimeError):
                create_authenticated_bundle(self.fixture, self.destination, REV, 64)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.fixture / "synveil-x86_64.img").exists())


if __name__ == "__main__":
    unittest.main()
