#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bounded root-owned seat startup independent of desktop and serial recovery."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SeatSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "attempts"
        self.binary = self.root / "seat"
        self.binary.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$TEST_LOG"\n'
            'if [ "$(wc -l < "$TEST_LOG")" -ge "$TEST_SUCCESS_AFTER" ]; then '
            'exit 0; fi\n'
            'exit 12\n'
        )
        self.binary.chmod(0o755)

    def run_helper(self, **kwargs):
        env = {**os.environ, "SYNVEIL_SEAT_BINARY": str(self.binary),
               "SYNVEIL_SEAT_RETRY_DELAY": "0",
               "TEST_LOG": str(self.log), "TEST_SUCCESS_AFTER": "100"}
        env.update(kwargs)
        return subprocess.run(
            ["sh", str(ROOT / "build/rootfs/start-seat")],
            env=env, text=True, capture_output=True, timeout=5,
        )

    def test_exit_without_retry(self):
        result = self.run_helper(TEST_SUCCESS_AFTER="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("SYNVEIL_SEAT_RESTARTING"), 0)
        self.assertEqual(self.log.read_text().splitlines(), [""])

    def test_one_failure_then_success(self):
        result = self.run_helper(TEST_SUCCESS_AFTER="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("SYNVEIL_SEAT_RESTARTING"), 1)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_three_failures_exhaust_without_endless_restart(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("SYNVEIL_SEAT_RESTARTING"), 2)
        self.assertEqual(result.stdout.count("SYNVEIL_SEAT_RESTART_EXHAUSTED"), 1)
        self.assertEqual(len(self.log.read_text().splitlines()), 3)

    def test_missing_binary_preserves_recovery(self):
        result = self.run_helper(SYNVEIL_SEAT_BINARY=str(self.root / "missing"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("SYNVEIL_SEAT_UNAVAILABLE", result.stdout)
        self.assertFalse(self.log.exists())

    def test_invalid_retry_delay_is_rejected(self):
        result = self.run_helper(SYNVEIL_SEAT_RETRY_DELAY="1000")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
