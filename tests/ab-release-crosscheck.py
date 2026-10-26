#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Cross-check: releases packaged by tools/ab_release.py (the exact
bytes the guest agent verifies in-crate) must verify under the host
transaction core tools/ab_update.py, and the guest trust record the
packager writes must carry the pinned key in the guest's shape.
This is the format-interop evidence between the host model and the
installed-system implementation; the guest-side Ed25519
verification itself is covered by the veil-core unit tests and the
QEMU proof.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ab_release
import ab_update


class ReleaseCrosscheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.trust = base / "trust"
        self.payload = base / "gen.efi"
        self.payload.write_bytes(b"UKI-BYTES" * 4096)
        self.private, self.public_hex = ab_release.ensure_keypair(self.trust)
        # Host-core trust directory: PEM + the 3-field record the
        # host core pins (the guest record adds the raw public key).
        self.host_trust = base / "host-trust"
        self.host_trust.mkdir()
        subprocess.run(["openssl", "pkey", "-in", str(self.private),
                        "-pubout", "-out", str(self.host_trust / "publisher.pem")],
                       check=True)
        (self.host_trust / "publisher.json").write_text(json.dumps({
            "schema": "synveil.ab-trust/v1",
            "key_id": ab_release.KEY_ID,
            "development": True,
        }) + "\n")

    def stage_dir(self, metadata, signature):
        directory = Path(self.tmp.name) / "release"
        directory.mkdir(exist_ok=True)
        (directory / "synveil.release.json").write_bytes(
            json.dumps(metadata, indent=2).encode() + b"\n")
        (directory / "synveil.release.sig").write_bytes(signature)
        (directory / "synveil.release.bin").write_bytes(self.payload.read_bytes())
        return directory

    def test_packager_output_verifies_in_host_core(self):
        metadata = ab_release.build_metadata(self.payload, 2, "crosscheck-gen-2")
        metadata_bytes = json.dumps(metadata, indent=2).encode() + b"\n"
        signature = ab_release.sign_metadata(self.private, metadata_bytes)
        directory = self.stage_dir(metadata, signature)
        trust = ab_update.load_trust(self.host_trust)
        data, fd = ab_update.signed_release(
            directory, "synveil", trust, metadata["issued_at"] + 60)
        os.close(fd)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["sha256"], metadata["sha256"])

    def test_corrupt_signature_rejected_by_host_core(self):
        metadata = ab_release.build_metadata(self.payload, 2, "crosscheck-gen-2")
        metadata_bytes = json.dumps(metadata, indent=2).encode() + b"\n"
        signature = bytearray(ab_release.sign_metadata(self.private, metadata_bytes))
        signature[10] ^= 0x01
        directory = self.stage_dir(metadata, bytes(signature))
        trust = ab_update.load_trust(self.host_trust)
        with self.assertRaises(ab_update.UpdateError):
            ab_update.signed_release(
                directory, "synveil", trust, metadata["issued_at"] + 60)

    def test_guest_trust_record_shape(self):
        record = json.loads((self.trust / "publisher.json").read_text())
        self.assertEqual(record, {
            "schema": "synveil.ab-trust/v1",
            "key_id": "synveil-dev-2026-01",
            "development": True,
            "public_key_hex": self.public_hex,
        })
        self.assertEqual(len(self.public_hex), 64)


class BuildMediaSafety(unittest.TestCase):
    """Review correction (2026-10-26): build_media used to truncate
    an existing output in place and delete a pre-existing staging
    directory. Every refusal below must leave the victim's bytes
    bit-identical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.payload = self.base / "gen.efi"
        self.payload.write_bytes(b"UKI-BYTES" * 1024)
        self.metadata = {"schema": "synveil.ab-release/v2", "version": 2}
        self.signature = b"\x01" * 64

    def build(self, out):
        return ab_release.build_media(
            self.payload, self.metadata, self.signature, out)

    def test_existing_output_refused_bytes_unchanged(self):
        out = self.base / "rel.img"
        out.write_bytes(b"VICTIM" * 1000)
        with self.assertRaises(ValueError):
            self.build(out)
        self.assertEqual(out.read_bytes(), b"VICTIM" * 1000)

    def test_symlink_output_refused_victim_unchanged(self):
        victim = self.base / "victim.img"
        victim.write_bytes(b"PRECIOUS" * 500)
        out = self.base / "rel.img"
        out.symlink_to(victim)
        with self.assertRaises(ValueError):
            self.build(out)
        self.assertEqual(victim.read_bytes(), b"PRECIOUS" * 500)
        self.assertTrue(out.is_symlink())

    def test_existing_stage_dir_refused_never_deleted(self):
        stage = self.base / "rel-stage"
        stage.mkdir()
        sentinel = stage / "sentinel.txt"
        sentinel.write_text("do not delete")
        with self.assertRaises(ValueError):
            self.build(self.base / "rel.img")
        self.assertEqual(sentinel.read_text(), "do not delete")
        self.assertFalse((self.base / "rel.img").exists())

    def test_existing_fs_staging_refused_bytes_unchanged(self):
        fs = self.base / "rel-fs.img"
        fs.write_bytes(b"STAGING" * 100)
        with self.assertRaises(ValueError):
            self.build(self.base / "rel.img")
        self.assertEqual(fs.read_bytes(), b"STAGING" * 100)
        self.assertFalse((self.base / "rel.img").exists())

    def test_symlink_parent_refused(self):
        real = self.base / "real"
        real.mkdir()
        link = self.base / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build(link / "rel.img")
        self.assertEqual(list(real.iterdir()), [])

    def test_missing_parent_refused(self):
        with self.assertRaises(ValueError):
            self.build(self.base / "nope" / "rel.img")

    def test_fresh_output_builds(self):
        out = self.build(self.base / "rel.img")
        self.assertTrue(out.is_file())
        self.assertEqual(out.stat().st_size,
                         ab_release.MEDIA_SIZE_MB * 1024 * 1024)
        # Staging paths created by the call are cleaned up on success.
        self.assertFalse((self.base / "rel-stage").exists())
        self.assertFalse((self.base / "rel-fs.img").exists())


if __name__ == "__main__":
    unittest.main()
