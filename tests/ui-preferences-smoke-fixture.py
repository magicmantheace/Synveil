#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Fake-QEMU smoke harness: reject missing/echoed UI markers and home errors."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ui_preferences_smoke", REPO / "tests/smoke-ui-preferences.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

GUEST = """#!/usr/bin/env python3
import os, sys
mode = os.environ["SYNVEIL_TEST_MODE"]
phase = int(os.environ["SYNVEIL_TEST_PHASE"])
if mode == "missing-home":
    print("SYNVEIL_HOME_UNAVAILABLE", flush=True)
    sys.exit(0)
print("SYNVEIL_BOOT_OK", flush=True)
print("SYNVEIL_STATE_READY", flush=True)
print("SYNVEIL_HOME_READY", flush=True)
if phase == 2 and mode != "missing-load":
    print("SYNVEIL_SETTINGS_LOADED", flush=True)
print("SYNVEIL_DESKTOP_READY", flush=True)
if mode == "echo":
    print("echo SYNVEIL_UI_PREFS_WRITE_OK", flush=True)
    sys.exit(0)
line = sys.stdin.readline()
if mode == "success" and phase == 1:
    assert "/home/synveil/roundtrip" in line
    assert "desktop-pref.conf" not in line
    print("SYNVEIL_HOME_SEEDED", flush=True)
    verify = sys.stdin.readline()
    assert "desktop-pref.conf" in verify and "contrast=1" in verify
    print("SYNVEIL_UI_PREFS_WRITE_OK", flush=True)
elif mode == "success" and phase == 2:
    assert "stat -c" in line and "/home/synveil/roundtrip" in line
    print("SYNVEIL_UI_PREFS_HOME_OK", flush=True)
"""

class UIPreferencesSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "build/scripts").mkdir(parents=True)
        guest = self.root / "build/scripts/guest.py"
        guest.write_text(GUEST)
        wrapper = self.root / "build/scripts/qemu.sh"
        wrapper.write_text('#!/bin/sh\nexec python3 "$(dirname "$0")/guest.py"\n')
        wrapper.chmod(0o755)
        self.disk = self.root / "state.ext4"
        self.disk.write_bytes(b"fixture")
    
    def boot(self, phase, mode):
        path = self.root / f"phase-{phase}-{mode}.log"
        with patch.object(smoke, "ROOT", self.root), \
             patch.object(smoke, "settings_toggle") as keyboard, patch.dict(
            os.environ, {"SYNVEIL_TEST_MODE": mode, "SYNVEIL_TEST_PHASE": str(phase)}
        ):
            result = smoke.round_trip(self.disk, 1.5, phase, path)
            if mode == "success":
                self.assertEqual(keyboard.call_count, 1 if phase == 1 else 0)
        return result, path.read_text(errors="replace")

    def test_both_phases_require_real_markers(self):
        first, console = self.boot(1, "success")
        self.assertTrue(first, console)
        second, console = self.boot(2, "success")
        self.assertTrue(second, console)

    def test_errors_fail_closed(self):
        for phase in (1, 2):
            self.assertFalse(self.boot(phase, "missing-home")[0])
            self.assertFalse(self.boot(phase, "missing-load")[0] if phase == 2
                             else self.boot(phase, "echo")[0])

    def test_console_echo_is_not_a_valid_marker(self):
        ok, output = self.boot(1, "echo")
        self.assertFalse(ok)
        self.assertIn("echo SYNVEIL_UI_PREFS_WRITE_OK", output)


if __name__ == "__main__":
    unittest.main()
