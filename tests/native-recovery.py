#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Console fixtures test recovery harness gating, timeout, and echo rejection."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def fixture(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            shutil.copyfile(ROOT / "tests/smoke-recovery.py", root / "tests/smoke-recovery.py")
            (root / "build/scripts/qemu.sh").write_text(
                'exec python3 "$(dirname "$0")/console.py"\n'
            )
            (root / "build/scripts/console.py").write_text('''import os, sys, time
mode = os.environ['FIXTURE_MODE']
if mode == 'no-ready':
    print('SYNVEIL_BOOT_OK', flush=True)
    sys.exit(0)
print('SYNVEIL_CORE_READY\\r\\nSYNVEIL_BOOT_OK', flush=True)
command = sys.stdin.readline()
if mode == 'pass':
    assert 'kill -KILL' in command and 'synctl --json status' in command
    assert 'shell-alive' in command
    print('SYNVEIL_RECOVERY_OK', flush=True)
elif mode == 'echo':
    print(command, flush=True)
elif mode == 'unavailable':
    print('SYNVEIL_CORE_UNAVAILABLE\\nSYNVEIL_RECOVERY_OK', flush=True)
elif mode == 'timeout':
    time.sleep(30)
''')
            result = subprocess.run(
                ["python3", str(root / "tests/smoke-recovery.py"), "--timeout", "0.5"],
                env={**os.environ, "FIXTURE_MODE": mode, "SYNVEIL_OUT_DIR": str(root / "out")},
                capture_output=True, text=True, timeout=5,
            )
            log = (root / "out/logs/qemu-recovery-smoke.log").read_text()
            return result.returncode, log

    def test_live_shell_passes(self):
        code, log = self.fixture("pass")
        self.assertEqual(code, 0)
        self.assertIn("SYNVEIL_RECOVERY_OK", log)

    def test_boot_without_core_fails(self):
        self.assertNotEqual(self.fixture("no-ready")[0], 0)

    def test_terminal_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo")[0], 0)

    def test_failed_startup_cannot_pass(self):
        self.assertNotEqual(self.fixture("unavailable")[0], 0)

    def test_hung_guest_times_out(self):
        self.assertNotEqual(self.fixture("timeout")[0], 0)


if __name__ == "__main__":
    unittest.main()
