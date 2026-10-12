#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-QEMU fixture for the staged graphical password/lease smoke protocol."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GraphicalLoginFixtureTests(unittest.TestCase):
    def simulate(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tools").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            shutil.copyfile(ROOT / "tests/smoke-graphical-login.py",
                            root / "tests/smoke-graphical-login.py")
            (root / "tools/qmp_input.py").write_text(
                "import os, re\n"
                "class QMPError(RuntimeError): pass\n"
                "def type_test_secret(path, value, *, timeout):\n"
                "    assert re.fullmatch(r'[0-9a-f]{32}', value)\n"
                "    with open(os.environ['QMP_SIGNALS'], 'a') as stream:\n"
                "        stream.write(value + '\\n')\n"
            )
            (root / "build/scripts/qemu.sh").write_text(
                '#!/bin/sh\nexec python3 "$(dirname "$0")/console.py"\n'
            )
            (root / "build/scripts/console.py").write_text('''import os, pathlib, re, sys, time
mode = os.environ['FIXTURE_MODE']
signal = pathlib.Path(os.environ['QMP_SIGNALS'])
if mode == 'no-ready':
    print('SYNVEIL_BOOT_OK', flush=True)
    sys.exit(0)
for marker in ('SYNVEIL_BOOT_OK', 'SYNVEIL_CORE_READY', 'SYNVEIL_STATE_READY',
               'SYNVEIL_SEAT_READY', 'SYNVEIL_LOGIN_READY'):
    print(marker, flush=True)
private = sys.stdin.readline()
assert private.startswith('stty -echo')
if mode == 'echo':
    print(private, flush=True)
    sys.exit(0)
print('SYNVEIL_INPUT_PRIVATE', flush=True)
enroll = sys.stdin.readline()
assert 'veil-enroll --password-stdin' in enroll
assert re.search(r"printf '%s' '[0-9a-f]{32}'", enroll)
print('SYNVEIL_GRAPHICAL_ENROLLED', flush=True)
def wait_for(n):
    for _ in range(80):
        if signal.exists() and len(signal.read_text().splitlines()) >= n:
            return
        time.sleep(0.02)
    sys.exit(1)
wait_for(1)
print('SYNVEIL_GRAPHICAL_AUTH_DENIED', flush=True)
check = sys.stdin.readline()
assert 'WRONG_PASSWORD_LOCKED' in check
print('SYNVEIL_WRONG_PASSWORD_LOCKED', flush=True)
wait_for(2)
print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
script = ''
while not script.endswith('# SYNVEIL_TEST_COMMAND_END\\n'):
    line = sys.stdin.readline()
    if not line:
        break
    script += line
assert 'NoNewPrivs' in script and '/dev/tty1' in script
assert 'kill -KILL' in script and 'synctl --json status' in script
print('SYNVEIL_GRAPHICAL_LOGIN_CLEANED', flush=True)
''')
            disk = root / "state.ext4"
            disk.write_bytes(b"fake fixture state disk")
            signals = root / "qmp-signals"
            out = root / "out"
            env = {**os.environ,
                   "SYNVEIL_QEMU_STATE_DISK": str(disk),
                   "SYNVEIL_OUT_DIR": str(out),
                   "QMP_SIGNALS": str(signals),
                   "FIXTURE_MODE": mode}
            result = subprocess.run(
                ["python3", str(root / "tests/smoke-graphical-login.py"),
                 "--timeout", "2"], env=env, capture_output=True,
                text=True, timeout=5
            )
            log = (out / "logs/qemu-graphical-login-smoke.log").read_text()
            secrets = signals.read_text().splitlines() if signals.exists() else []
            return result.returncode, log, secrets

    def test_script_requires_wrong_password_then_correct_login_and_cleanup(self):
        code, log, secrets = self.simulate("pass")
        self.assertEqual(code, 0, log)
        self.assertEqual(len(secrets), 2)
        self.assertNotEqual(secrets[0], secrets[1])
        for secret in secrets:
            self.assertRegex(secret, r"^[a-f0-9]{32}$")
            self.assertNotIn(secret, log)
        self.assertIn("SYNVEIL_GRAPHICAL_LOGIN_CLEANED", log)

    def test_missing_guest_markers_fail(self):
        self.assertNotEqual(self.simulate("no-ready")[0], 0)

    def test_echoed_commands_cannot_spoof_private_secret_handshake(self):
        self.assertNotEqual(self.simulate("echo")[0], 0)


if __name__ == "__main__":
    unittest.main()
