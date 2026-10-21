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
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from auth_vm_bundle import create_authenticated_bundle, verify_authenticated_fixture  # noqa: E402
from archive_auth_vm import archive_bundle  # noqa: E402

REV = "a" * 40
REQUIRED = ("init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
            "usr/sbin/veil-enroll", "usr/sbin/veil-seat",
            "usr/libexec/synveil/recovery-status",
            "etc/synveil/desktop-autostart", "etc/synveil/auth-desktop")


def newc(names, overrides=None):
    overrides = overrides or {}
    blob = bytearray()
    for name in (*names, "TRAILER!!!"):
        raw = name.encode() + b"\0"
        executable = {"init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
                      "usr/sbin/veil-enroll", "usr/sbin/veil-seat",
                      "usr/libexec/synveil/recovery-status"}
        default_mode = 0o100755 if name in executable else 0o100644
        fields = [1, overrides.get(name, default_mode), 0, 0, 1, 0,
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
            "wrong_password_denied_after_cold_reboot": True,
            "graphical_scanout_after_cold_reboot": True,
            "nonblank_rgb_after_login_both_boots": True,
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
                           ("locked_serial", False), ("persistent_authentication", False),
                           ("wrong_password_denied_after_cold_reboot", False),
                           ("wrong_password_denied_after_cold_reboot", None),
                           ("graphical_scanout_after_cold_reboot", False),
                           ("nonblank_rgb_after_login_both_boots", False),
                           ("nonblank_rgb_after_login_both_boots", None),
                           ("graphical_scanout_after_cold_reboot", None)):
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

    def test_rejects_unsafe_seat_executable_from_verified_cpio(self):
        self.fixture.joinpath("synveil-initramfs.cpio").write_bytes(
            newc(REQUIRED, {"usr/sbin/veil-seat": 0o100644}))
        self.refresh_receipts()
        with self.assertRaisesRegex(ValueError, "unsafe embedded executable"):
            self.create()
        self.assertFalse(self.destination.exists())

    def test_rejects_missing_or_unsafe_recovery_executable(self):
        name = "usr/libexec/synveil/recovery-status"
        for names, overrides in (
            (tuple(n for n in REQUIRED if n != name), {}),
            (REQUIRED, {name: 0o120777}),
            (REQUIRED, {name: 0o100644}),
            (REQUIRED, {name: 0o100777}),
        ):
            with self.subTest(names=names, overrides=overrides):
                (self.fixture / "synveil-initramfs.cpio").write_bytes(
                    newc(names, overrides))
                self.refresh_receipts()
                with self.assertRaisesRegex(
                    ValueError, "missing required|unsafe embedded executable"
                ):
                    self.create()
                self.assertFalse(self.destination.exists())

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

    def test_launcher_rejects_mutated_packaged_receipts(self):
        self.create()
        for name in ("fixture.json", "validation.json", "VM-BUNDLE.json",
                     "SHA256SUMS"):
            with self.subTest(name=name):
                path = self.destination / name
                original = path.read_bytes()
                path.chmod(0o600)
                path.write_bytes(b"x" + original[1:])
                path.chmod(0o400)
                result, log = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("checksum", result.stderr)
                self.assertFalse(log.exists())
                path.chmod(0o600)
                path.write_bytes(original)
                path.chmod(0o400)

    def test_launcher_rejects_incomplete_or_duplicate_checksum_inventory(self):
        self.create()
        checksum = self.destination / "SHA256SUMS"
        original = checksum.read_text()
        lines = original.splitlines(keepends=True)
        for malformed in ("".join(lines[1:]),
                          "".join(lines[:-1]) + lines[0],
                          "".join(reversed(lines))):
            with self.subTest(malformed=malformed[:16]):
                checksum.chmod(0o600)
                checksum.write_text(malformed)
                checksum.chmod(0o400)
                result, log = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("checksum", result.stderr)
                self.assertFalse(log.exists())
        checksum.chmod(0o600)
        checksum.write_text(original)
        checksum.chmod(0o400)

    def test_formatter_failure_cleans_only_new_bundle(self):
        with patch("state_disk.subprocess.run", side_effect=RuntimeError("mkfs failed")):
            with self.assertRaises(RuntimeError):
                create_authenticated_bundle(self.fixture, self.destination, REV, 64)
        self.assertFalse(self.destination.exists())
        self.assertTrue((self.fixture / "synveil-x86_64.img").exists())

    def test_archive_preserves_private_modes_and_exact_inventory(self):
        self.create()
        output = self.root / "preview.tar.gz"
        archive_bundle(self.destination, self.fixture, output, REV)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with tarfile.open(output) as archive:
            members = archive.getmembers()
            self.assertEqual(len(members), 9)
            self.assertEqual(archive.getmember("auth-vm").mode, 0o700)
            self.assertEqual(archive.getmember("auth-vm/run-vm.sh").mode, 0o700)
            self.assertEqual(archive.getmember("auth-vm/synveil-state.ext4").mode, 0o600)
            self.assertEqual(archive.getmember("auth-vm/synveil-x86_64.img").mode, 0o400)
            self.assertTrue(all(member.isdir() or member.isfile() for member in members))
        target = self.root / "extracted"
        target.mkdir(mode=0o700)
        subprocess.run(["tar", "--extract", "--gzip", "--file", str(output),
                        "--same-permissions", "--no-same-owner", "--directory", str(target)],
                       check=True)
        self.assertEqual((target / "auth-vm").stat().st_mode & 0o777, 0o700)
        self.assertEqual((target / "auth-vm/run-vm.sh").stat().st_mode & 0o777, 0o700)
        self.assertEqual(sha(target / "auth-vm/synveil-state.ext4"),
                         sha(self.destination / "synveil-state.ext4"))
        second = self.root / "second.tar.gz"
        archive_bundle(self.destination, self.fixture, second, REV)
        self.assertEqual(sha(output), sha(second))

    def test_archive_refuses_changed_blank_state(self):
        self.create()
        disk = self.destination / "synveil-state.ext4"
        with disk.open("r+b") as stream:
            stream.write(b"enrolled-or-modified-state")
        output = self.root / "preview.tar.gz"
        with self.assertRaisesRegex(ValueError, "no longer blank"):
            archive_bundle(self.destination, self.fixture, output, REV)
        self.assertFalse(output.exists())

    def test_archive_refuses_extras_modified_launcher_and_replacement(self):
        self.create()
        output = self.root / "preview.tar.gz"
        extra = self.destination / "private-user-data"
        extra.write_bytes(b"do not distribute")
        with self.assertRaisesRegex(ValueError, "inventory"):
            archive_bundle(self.destination, self.fixture, output, REV)
        self.assertFalse(output.exists())
        extra.unlink()
        launcher = self.destination / "run-vm.sh"
        original = launcher.read_text()
        launcher.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaisesRegex(ValueError, "launcher differs"):
            archive_bundle(self.destination, self.fixture, output, REV)
        launcher.write_text(original)
        output.write_bytes(b"existing-user-file")
        with self.assertRaises(FileExistsError):
            archive_bundle(self.destination, self.fixture, output, REV)
        self.assertEqual(output.read_bytes(), b"existing-user-file")

    def test_archive_failure_removes_partial_output_only(self):
        self.create()
        output = self.root / "preview.tar.gz"
        with patch("archive_auth_vm.tarfile.TarFile.addfile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                archive_bundle(self.destination, self.fixture, output, REV)
        self.assertFalse(output.exists())
        self.assertTrue((self.destination / "synveil-state.ext4").exists())

    def test_archive_rejects_state_changed_during_copy_even_if_restored(self):
        self.create()
        disk = self.destination / "synveil-state.ext4"
        before = sha(disk)
        prefix = b"changed-during-copy"
        with disk.open("rb") as source:
            original = source.read(len(prefix))
        addfile = tarfile.TarFile.addfile

        def mutate_then_restore(archive, member, source=None):
            if member.name != "auth-vm/synveil-state.ext4":
                return addfile(archive, member, source)
            with disk.open("r+b") as target:
                target.write(prefix)
            try:
                return addfile(archive, member, source)
            finally:
                with disk.open("r+b") as target:
                    target.write(original)

        output = self.root / "preview.tar.gz"
        with patch("archive_auth_vm.tarfile.TarFile.addfile", new=mutate_then_restore):
            with self.assertRaisesRegex(ValueError, "changed while copying"):
                archive_bundle(self.destination, self.fixture, output, REV)
        self.assertFalse(output.exists())
        self.assertEqual(sha(disk), before)

    def test_archive_rejects_hardlinked_member_before_copy(self):
        self.create()
        fs_link = self.root / "shared-image"
        os.link(self.destination / "synveil-x86_64.img", fs_link)
        output = self.root / "preview.tar.gz"
        with self.assertRaisesRegex(ValueError, "inode or permissions changed"):
            archive_bundle(self.destination, self.fixture, output, REV)
        self.assertFalse(output.exists())
        self.assertEqual(fs_link.read_bytes(), b"synthetic EFI boot image")


if __name__ == "__main__":
    unittest.main()
