#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Negative fixture: an echoed command is not proof of root execution."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SerialLockedSmokeTests(unittest.TestCase):
    def fixture(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tests").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            shutil.copyfile(ROOT / "tests/smoke-serial-locked.py",
                            root / "tests/smoke-serial-locked.py")
            (root / "build/scripts/qemu.sh").write_text(
                '#!/bin/sh\nexec python3 "$(dirname "$0")/console.py"\n'
            )
            (root / "build/scripts/console.py").write_text('''import os, sys, time
mode = os.environ['FIXTURE_MODE']
for label in ('SYNVEIL_BOOT_OK', 'SYNVEIL_CORE_READY',
              'SYNVEIL_LOGIN_READY'):
    print(label, flush=True)
if mode == 'no-ready':
    time.sleep(2)
    sys.exit(0)
if mode == 'unlocked':
    print('SYNVEIL_SERIAL_RECOVERY_DEVELOPER', flush=True)
else:
    print('SYNVEIL_SERIAL_RECOVERY_LOCKED', flush=True)
probe = sys.stdin.readline()
if mode == 'unlocked':
    print('SYNVEIL_SERIAL_UNLOCK_BYPASS', flush=True)
elif mode == 'echo-only':
    print(probe, flush=True)
else:
    time.sleep(6)
''')
            env = {**os.environ, "FIXTURE_MODE": mode,
                   "SYNVEIL_OUT_DIR": str(root / "out")}
            result = subprocess.run(
                ["python3", str(root / "tests/smoke-serial-locked.py"),
                 "--timeout", "4"], env=env, capture_output=True,
                text=True, timeout=8
            )
            return result.returncode

    def test_locked_shell_does_not_execute_probe(self):
        self.assertEqual(self.fixture("locked"), 0)

    def test_echo_is_not_mistaken_for_executed_command(self):
        self.assertEqual(self.fixture("echo-only"), 0)

    def test_unlocked_root_console_fails(self):
        self.assertNotEqual(self.fixture("unlocked"), 0)

    def test_missing_boot_gate_fails(self):
        self.assertNotEqual(self.fixture("no-ready"), 0)


if __name__ == "__main__":
    unittest.main()
