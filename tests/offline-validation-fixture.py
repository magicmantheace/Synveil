#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-network tests for the standalone validation runner."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("validate_offline", ROOT / "tools/validate_offline.py")
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


class OfflineTests(unittest.TestCase):
    def test_host_plan_does_not_run_qemu_or_contact_github(self):
        plan = offline.planned_checks()
        self.assertIn(("cargo", "fmt", "--all", "--check"), plan)
        self.assertIn(("python3", "tests/graphical-login-fixture.py"), plan)
        self.assertIn(("sh", "-n", "build/rootfs/start-seat"), plan)
        self.assertFalse(any("guest-fixture" in arg for cmd in plan for arg in cmd))

    def test_guest_and_partial_modes_have_explicit_scope(self):
        plan = offline.planned_checks(skip_rust=True, guest=True)
        self.assertFalse(any(cmd[0] == "cargo" for cmd in plan))
        self.assertIn(("bash", "build.sh", "guest-fixture",
                       "--case", "graphical-login"), plan)

    def test_failures_and_missing_tools_cannot_be_marked_passed(self):
        commands = [("python3", "-c", "raise SystemExit(0)"),
                    ("python3", "-c", "raise SystemExit(3)"),
                    ("synveil-nonexistent-validator-executable", "--version")]
        with tempfile.TemporaryDirectory() as temp:
            result = offline.run_checks(commands, Path(temp), timeout=5)
            self.assertEqual([item["status"] for item in result],
                             ["passed", "failed", "blocked"])
            self.assertTrue(result[0]["log_sha256"])
            self.assertEqual(result[1]["exit_code"], 3)
            self.assertIsNone(result[2]["exit_code"])


if __name__ == "__main__":
    unittest.main()
