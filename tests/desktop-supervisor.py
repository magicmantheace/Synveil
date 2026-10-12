#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bounded developer menu restart; keep serial shell recoverable."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "build/rootfs/start-desktop"


class DesktopSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "attempts"
        self.menu = self.root / "menu"
        self.menu.write_text(
            '#!/bin/sh\n'
            'printf "%s\\n" "$*" >> "$TEST_LOG"\n'
            'if [ -f "$TEST_FINISH" ]; then exit 0; fi\n'
            'if [ "$(wc -l < "$TEST_LOG")" -ge "$TEST_SUCCESS_AFTER" ]; then exit 0; fi\n'
            'exit 7\n')
        self.menu.chmod(0o755)
        self.auth_marker = self.root / "auth-desktop"
        self.helper = self.root / "start-desktop"
        self.helper.write_text(
            SUPERVISOR.read_text().replace("/etc/synveil/auth-desktop", str(self.auth_marker))
        )

    def invoke(self, **extra):
        env = {**os.environ,
               "SYNVEIL_DESKTOP_BINARY": str(self.menu),
               "SYNVEIL_DESKTOP_RETRY_DELAY": "0",
               "TEST_LOG": str(self.log),
               "TEST_FINISH": str(self.root / "finish"),
               "TEST_SUCCESS_AFTER": "100"}
        env.update(extra)
        return subprocess.run(["sh", str(self.helper)], env=env,
                              capture_output=True, text=True, timeout=5)

    def test_crash_budget_exhaustion_does_not_retry_forever(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.log.read_text().splitlines(), ["--menu"] * 3)
        self.assertEqual(result.stdout.count("SYNVEIL_DESKTOP_RESTARTING"), 2)
        self.assertEqual(result.stdout.count("SYNVEIL_DESKTOP_RESTART_EXHAUSTED"), 1)

    def test_clean_exit_stops_without_restart(self):
        (self.root / "finish").touch()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["--menu"])
        self.assertIn("SYNVEIL_DESKTOP_EXITED", result.stdout)
        self.assertNotIn("RESTARTING", result.stdout)

    def test_crashes_then_recovers_and_stops(self):
        result = self.invoke(TEST_SUCCESS_AFTER="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["--menu"] * 2)
        self.assertEqual(result.stdout.count("SYNVEIL_DESKTOP_RESTARTING"), 1)

    def test_explicit_auth_marker_selects_only_the_login_preview(self):
        self.auth_marker.touch()
        self.assertEqual(self.invoke(TEST_SUCCESS_AFTER="2").returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), ["--login"] * 2)

    def test_missing_binary_falls_back_without_spawning(self):
        result = self.invoke(SYNVEIL_DESKTOP_BINARY=str(self.root / "missing"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("SYNVEIL_DESKTOP_UNAVAILABLE", result.stdout)
        self.assertFalse(self.log.exists())

    def test_invalid_retry_delay_does_not_spawn(self):
        result = self.invoke(SYNVEIL_DESKTOP_RETRY_DELAY="-1")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
