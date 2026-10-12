#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-network checks for the single canonical offline validation runner."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("offline_checks", ROOT / "tools/offline_checks.py")
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


class OfflineChecksTests(unittest.TestCase):
    def test_levels_do_not_claim_missing_guest_or_rust_checks(self):
        quick = offline.checks("quick")
        host = offline.checks("host")
        guest = offline.checks("guest")
        self.assertTrue(all(cmd[1] != "cargo" for cmd in quick))
        self.assertTrue(any(cmd[0] == "rustfmt" for cmd in host))
        self.assertFalse(any(cmd[0].startswith("qemu-") for cmd in host))
        self.assertTrue(any(cmd[0] == "qemu-graphical-login" for cmd in guest))
        self.assertIn(("seat-supervisor", "python3", "tests/seat-supervisor.py"), quick)
        self.assertRaises(ValueError, offline.checks, "not-a-real-tier")

    def test_failure_and_missing_executable_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "check.log"
            status, code, _ = offline.run_check(
                ["python3", "-c", "raise SystemExit(0)"], log, 4, os.environ
            )
            self.assertEqual((status, code), ("pass", 0))
            status, code, _ = offline.run_check(
                ["python3", "-c", "raise SystemExit(4)"], log, 4, os.environ
            )
            self.assertEqual((status, code), ("fail", 4))
            status, code, _ = offline.run_check(
                ["synveil-nonexistent-offline-tool"], log, 4, os.environ
            )
            self.assertEqual((status, code), ("blocked", None))


if __name__ == "__main__":
    unittest.main()
