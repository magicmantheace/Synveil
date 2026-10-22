#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise signed A/B writes and interruptions on disposable regular files."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))
import ab_image_prototype as ab  # noqa: E402
import ab_vm_update as update  # noqa: E402

SMALL = (("ESP", 2), ("SYSTEM_A", 4), ("SYSTEM_B", 4),
         ("STATE", 2), ("DATA", 4), ("RECOVERY", 2))


class VMUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "fixture"
        self.name = "a-b-test"
        self.meta = ab.create_image(self.dir, name=self.name, total_mib=24, specs=SMALL)
        self.raw = self.dir / (self.name + ".raw")
        self.control = self.dir / (self.name + ".control.json")
        self.base = b"baseline-native-image\x00" * 512
        (self.dir / (self.name + ".base.bin")).write_bytes(self.base)
        self.assertTrue(update.operate(self.dir, self.name, "initialize-fixture")
                        ["fixture_initialized"])
        self.priv = Path(self.tmp.name) / "fixture-private-key.pem"
        self.pub = self.dir / (self.name + ".fixture-trust.pem")
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519",
                        "-out", str(self.priv)], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.priv), "-pubout",
                        "-out", str(self.pub)], check=True, capture_output=True)
        self.payload = b"candidate-full-system-image\xff\x00" * 2048
        self.sign(version=2)

    def sign(self, *, version, payload=None):
        if payload is not None:
            self.payload = payload
        (self.dir / (self.name + ".release.bin")).write_bytes(self.payload)
        m = {"schema": update.RELEASE_SCHEMA, "version": version,
             "bytes": len(self.payload), "sha256": hashlib.sha256(self.payload).hexdigest()}
        manifest = self.dir / (self.name + ".release.json")
        manifest.write_text(json.dumps(m, sort_keys=True))
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(self.priv),
                        "-rawin", "-in", str(manifest), "-out",
                        str(self.dir / (self.name + ".release.sig"))],
                       check=True, capture_output=True)

    def run_action(self, action, **kwargs):
        return update.operate(self.dir, self.name, action, **kwargs)

    def state(self):
        return json.loads(self.control.read_text())

    def region(self, name, count):
        part = next(p for p in self.meta["partitions"] if p["name"] == name)
        with self.raw.open("rb") as f:
            f.seek(part["first_lba"] * 512)
            return f.read(count)

    def test_initial_fixture_rejects_repeat_and_nonempty_slot(self):
        with self.assertRaisesRegex(update.UpdateError, "already initialized"):
            self.run_action("initialize-fixture")
        self.assertEqual(self.region("SYSTEM_A", len(self.base)), self.base)
        self.assertFalse(self.raw.is_symlink())

    def test_stale_control_temporary_recovers_without_accepting_untrusted_content(self):
        leftover = self.control.with_name(self.control.name + ".next")
        leftover.write_text('{"schema":"malicious"}')
        self.run_action("stage")
        self.assertFalse(leftover.exists())
        self.assertEqual(self.state()["confirmed"]["slot"], "A")
        self.run_action("rollback")
        leftover.symlink_to(self.control)
        with self.assertRaisesRegex(update.UpdateError, "unsafe interrupted"):
            self.run_action("stage")

    def test_signed_stage_and_rollback_preserve_confirmed_and_user_partitions(self):
        before = self.raw.read_bytes()
        stage = self.run_action("stage")
        self.assertEqual(stage, {"staged_slot": "B", "trial_attempts": 3})
        self.assertEqual(self.region("SYSTEM_A", len(self.base)), self.base)
        self.assertEqual(self.region("SYSTEM_B", len(self.payload)), self.payload)
        self.assertEqual(self.state()["confirmed"]["slot"], "A")
        self.assertEqual(self.state()["trial"]["attempts"], 3)
        self.assertEqual(self.run_action("rollback")["selected_slot"], "A")
        self.assertIsNone(self.state()["trial"])
        # Verify every byte outside the inactive B partition remained untouched.
        part = self.meta["partitions"][2]
        start, end = part["first_lba"] * 512, (part["last_lba"] + 1) * 512
        after = self.raw.read_bytes()
        self.assertEqual(before[:start], after[:start])
        self.assertEqual(before[end:], after[end:])

    def test_three_consumed_attempts_then_fallback_without_confirmation(self):
        self.run_action("stage")
        for remaining in (2, 1, 0):
            decision = self.run_action("boot-trial")
            self.assertEqual(decision["selected_slot"], "B")
            self.assertEqual(decision["remaining"], remaining)
        self.assertEqual(self.run_action("boot-trial"),
                         {"selected_slot": "A", "reason": "fallback"})
        self.assertIsNone(self.state()["trial"])

    def test_operator_confirmation_requires_recorded_trial(self):
        self.run_action("stage")
        with self.assertRaisesRegex(update.UpdateError, "recorded trial"):
            self.run_action("confirm")
        self.run_action("boot-trial")
        confirmed = self.run_action("confirm")
        self.assertEqual(confirmed["confirmed_slot"], "B")
        self.assertEqual(self.state()["confirmed"]["version"], 2)
        self.assertIsNone(self.state()["trial"])
        self.sign(version=3, payload=b"third-revision" * 256)
        self.assertEqual(self.run_action("stage")["staged_slot"], "A")
        self.assertEqual(self.region("SYSTEM_B", len(self.payload))[:10], b"candidate-")

    def test_partial_write_and_post_sync_interruptions_do_not_mark_bad_slot_active(self):
        for cut in ("during-copy", "after-slot-sync"):
            with self.subTest(cut=cut):
                with self.assertRaises(update.InterruptedTrial):
                    self.run_action("stage", interrupt=cut)
                self.assertIsNone(self.state()["trial"])
                self.assertEqual(self.run_action("boot-trial")["selected_slot"], "A")
                self.assertEqual(self.region("SYSTEM_A", len(self.base)), self.base)
                self.run_action("stage")
                self.run_action("rollback")
        with self.assertRaises(update.InterruptedTrial):
            self.run_action("stage", interrupt="after-state-sync")
        self.assertEqual(self.state()["trial"]["attempts"], 3)
        self.assertEqual(self.run_action("boot-trial")["selected_slot"], "B")

    def test_reject_unsigned_tampered_mismatched_and_rollback_release(self):
        sig = self.dir / (self.name + ".release.sig")
        sig.write_bytes(b"X" * 64)
        with self.assertRaisesRegex(update.UpdateError, "signature verification"):
            self.run_action("stage")
        self.sign(version=2)
        payload = self.dir / (self.name + ".release.bin")
        payload.write_bytes(self.payload + b"tampered")
        with self.assertRaisesRegex(update.UpdateError, "byte count mismatch"):
            self.run_action("stage")
        self.sign(version=1)
        with self.assertRaisesRegex(update.UpdateError, "rollback refused"):
            self.run_action("stage")
        self.assertIsNone(self.state()["trial"])

    def test_corrupt_candidate_falls_back_and_never_confirms(self):
        self.run_action("stage")
        part = self.meta["partitions"][2]
        with self.raw.open("r+b") as file:
            file.seek(part["first_lba"] * 512 + 2)
            file.write(b"CORRUPT")
        decision = self.run_action("boot-trial")
        self.assertEqual(decision["selected_slot"], "A")
        self.assertIsNone(self.state()["trial"])
        self.assertEqual(self.region("SYSTEM_A", len(self.base)), self.base)

    def test_confirmed_corruption_refuses_every_operation(self):
        part = self.meta["partitions"][1]
        with self.raw.open("r+b") as file:
            file.seek(part["first_lba"] * 512)
            file.write(b"BROKEN")
        with self.assertRaisesRegex(update.UpdateError, "confirmed system slot corruption"):
            self.run_action("stage")
        with self.assertRaises(update.UpdateError):
            self.run_action("boot-trial")

    def test_gpt_identity_change_refuses_write_without_modifying_state(self):
        initial = self.control.read_bytes()
        with self.raw.open("r+b") as file:
            file.seek(512)
            file.write(b"NOT GPT")
        with self.assertRaisesRegex(update.UpdateError, "GPT"):
            self.run_action("stage")
        self.assertEqual(self.control.read_bytes(), initial)

    def test_image_trust_control_and_payload_reject_symlinks_and_devices(self):
        payload = self.dir / (self.name + ".release.bin")
        outside = Path(self.tmp.name) / "outside"
        outside.write_bytes(self.payload)
        payload.unlink()
        payload.symlink_to(outside)
        with self.assertRaises(OSError):
            self.run_action("stage")
        payload.unlink()
        payload.write_bytes(self.payload)
        image_link = self.dir / (self.name + ".raw")
        image_link.rename(self.dir / "original.raw")
        image_link.symlink_to(self.dir / "original.raw")
        with self.assertRaises(OSError):
            self.run_action("stage")
        image_link.unlink()
        (self.dir / "original.raw").rename(image_link)
        os.mkfifo(self.dir / "fake-fifo")
        with self.assertRaises(update.UpdateError):
            update.regular_fd(self.dir / "fake-fifo")

    def test_invalid_control_no_trial_overwrite_and_duplicate_metadata(self):
        self.run_action("stage")
        with self.assertRaisesRegex(update.UpdateError, "trial already pending"):
            self.run_action("stage")
        self.run_action("rollback")
        self.control.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(update.UpdateError, "duplicate"):
            self.run_action("boot-trial")


if __name__ == "__main__":
    unittest.main()
