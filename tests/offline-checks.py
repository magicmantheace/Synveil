#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host-only fixtures for Synveil local validation without GitHub Actions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("offline_checks", ROOT / "tools/offline_checks.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class OfflineValidationTests(unittest.TestCase):
    def test_plans_are_different_and_do_not_call_remote_ci(self):
        quick, host, guest = (checker.checks(x) for x in ("quick", "host", "guest"))
        self.assertLess(len(quick), len(host))
        self.assertLess(len(host), len(guest))
        self.assertNotIn("qemu-boot", [step[0] for step in quick])
        self.assertIn("qemu-graphical-login", [step[0] for step in guest])
        for step in guest:
            self.assertNotIn("gh", step)
            self.assertNotIn("curl", step)

    def test_manifest_digest_and_commit_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "synveil-x86_64.img"
            image.write_bytes(b"synthetic image")
            manifest = root / "manifests" / "build.json"
            manifest.parent.mkdir()
            data = {"schema": "synveil.build/v1",
                    "git": {"commit": "SHA", "dirty": False},
                    "artifacts": {image.name: {"bytes": image.stat().st_size,
                    "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}}}
            manifest.write_text(json.dumps(data))
            self.assertTrue(checker.guest_image_matches(image, "SHA", True))
            self.assertFalse(checker.guest_image_matches(image, "other", True))
            self.assertFalse(checker.guest_image_matches(image, "SHA", False))
            data["git"]["dirty"] = True
            manifest.write_text(json.dumps(data))
            self.assertFalse(checker.guest_image_matches(image, "SHA", True))
            data["git"]["dirty"] = False
            manifest.write_text(json.dumps(data))
            image.write_bytes(b"changed image!")
            self.assertFalse(checker.guest_image_matches(image, "SHA", True))

    def test_failed_or_missing_command_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "step.log"
            for cmd, expected in (
                ([sys.executable, "-c", "raise SystemExit(0)"], "pass"),
                ([sys.executable, "-c", "raise SystemExit(2)"], "fail"),
                (["/nonexistent-synveil-offline-check"], "blocked"),
            ):
                status, _, _ = checker.run_check(cmd, log, 2, {})
                self.assertEqual(status, expected)

    def test_receipt_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "results.json"
            checker.store(file, {"status": "incomplete"})
            checker.store(file, {"status": "fail"})
            self.assertEqual(json.loads(file.read_text())["status"], "fail")


if __name__ == "__main__":
    unittest.main()
