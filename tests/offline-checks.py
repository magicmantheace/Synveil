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
from unittest.mock import patch

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
        expected_scenarios = {
            "crash", "supervision", "observe", "desktop",
            "desktop-terminal", "state-missing", "state-corrupt",
            "seat-crash", "seat-locked",
        }
        actual_scenarios = {
            step[step.index("--case") + 1] for step in guest
            if "recovery-smoke" in step
        }
        self.assertTrue(expected_scenarios <= actual_scenarios)
        self.assertEqual(len(guest), len({entry[0] for entry in guest}))
        self.assertTrue({
            "qemu-seat-crash", "qemu-persistence", "qemu-ui-preferences",
        } <= checker.GUEST_WITH_FRESH_STATE)
        self.assertIn("absent", {step[-1] for step in guest})
        self.assertIn("protocol", {step[-1] for step in guest})
        for step in guest:
            self.assertNotIn("gh", step)
            self.assertNotIn("curl", step)

    def test_guest_state_does_not_inherit_operator_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            logfile = base / "case.log"
            calls = []

            def fake_run(command, log, timeout, env):
                calls.append((command, log, timeout, dict(env)))
                return "pass", 0, 0.01

            environment = {"SYNVEIL_QEMU_STATE_DISK": "/home/owner/private.ext4"}
            with patch.object(checker.shutil, "which", return_value="/sbin/mkfs.ext4"), \
                 patch.object(checker, "run_check", side_effect=fake_run):
                result = checker.run_guest_with_fresh_state(
                    "qemu-seat-crash", ["bash", "build.sh", "recovery-smoke"],
                    logfile, base, environment, 90,
                )
            self.assertEqual(result[0], "pass")
            self.assertEqual(len(calls), 2)
            self.assertIn("state_disk.py", calls[0][0][1])
            self.assertEqual(calls[1][3]["SYNVEIL_QEMU_STATE_DISK"],
                             str(base / "qemu-seat-crash-state.ext4"))
            self.assertEqual(environment["SYNVEIL_QEMU_STATE_DISK"],
                             "/home/owner/private.ext4")

    def test_missing_ext4_formatter_blocks_guest_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            logfile = Path(tmp) / "blocked.log"
            with patch.object(checker.shutil, "which", return_value=None), \
                 patch.object(checker, "run_check") as mocked:
                status, code, _ = checker.run_guest_with_fresh_state(
                    "qemu-seat-crash", ["bash", "build.sh", "recovery-smoke"],
                    logfile, Path(tmp), {}, 90,
                )
            self.assertEqual((status, code), ("blocked", None))
            self.assertIn("mkfs.ext4", logfile.read_text())
            mocked.assert_not_called()

    def test_state_preparation_failure_is_not_reported_as_guest_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            logfile = Path(tmp) / "blocked.log"
            with patch.object(checker.shutil, "which", return_value="/sbin/mkfs.ext4"), \
                 patch.object(checker, "run_check", return_value=("fail", 1, 0.01)) as mocked:
                status, code, _ = checker.run_guest_with_fresh_state(
                    "qemu-seat-crash", ["bash", "build.sh", "recovery-smoke"],
                    logfile, Path(tmp), {}, 90,
                )
            self.assertEqual((status, code), ("blocked", None))
            self.assertEqual(mocked.call_count, 1)
            self.assertIn("state-setup.log", logfile.read_text())

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
