#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host fake-QMP checks: two genuine boots and no root serial bypass."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

QMP_FAKE = """import os, pathlib, re
class QMPError(RuntimeError): pass
def type_test_secret(path, value, *, timeout):
    assert re.fullmatch(r'[0-9a-f]{32}', value)
    slot = pathlib.Path(os.environ['QMP_SECRET_SLOT'])
    if slot.exists():
        assert slot.read_text() == value
    else:
        slot.write_text(value)
    with open(os.environ['QMP_SIGNALS'], 'a') as out:
        out.write('KEYS\\n')
"""

CONSOLE_FAKE = """import os, pathlib, sys, time
mode = os.environ['FAKE_MODE']
signal = pathlib.Path(os.environ['QMP_SIGNALS'])
bootfile = pathlib.Path(os.environ['QMP_BOOT_COUNT'])
boot = int(bootfile.read_text()) + 1 if bootfile.exists() else 1
bootfile.write_text(str(boot))
if mode == 'early-exit':
    print('SYNVEIL_BOOT_OK', flush=True)
    sys.exit(0)
for line in ('SYNVEIL_BOOT_OK', 'SYNVEIL_CORE_READY',
             'SYNVEIL_STATE_READY', 'SYNVEIL_SEAT_READY',
             'SYNVEIL_SERIAL_RECOVERY_LOCKED'):
    print(line, flush=True)
if mode == 'serial-bypass':
    print('SYNVEIL_SERIAL_RECOVERY_DEVELOPER', flush=True)
    sys.exit(0)
if boot == 1 or mode == 'second-enrollment':
    print('SYNVEIL_ENROLL_READY', flush=True)
else:
    print('SYNVEIL_LOGIN_READY', flush=True)
def wait_for(count):
    for attempt in range(200):
        if signal.exists() and len(signal.read_text().splitlines()) >= count:
            return True
        time.sleep(0.01)
    return False
if boot == 1:
    assert wait_for(1)
    print('SYNVEIL_ENROLL_CONFIRM_READY', flush=True)
    assert wait_for(2)
    if mode == 'missing-confirmation':
        sys.exit(0)
    print('SYNVEIL_ENROLL_COMPLETED', flush=True)
    print('SYNVEIL_LOGIN_READY', flush=True)
    assert wait_for(3)
    print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
else:
    assert wait_for(4)
    if mode == 'reboot-denied':
        print('SYNVEIL_GRAPHICAL_AUTH_DENIED', flush=True)
        sys.exit(0)
    print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
time.sleep(3)
"""


class GraphicalOnboardFixtures(unittest.TestCase):
    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as rootname:
            root = Path(rootname)
            for d in ("tests", "tools", "build/scripts", "out"):
                (root / d).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "tests/smoke-graphical-onboard.py",
                            root / "tests/smoke-graphical-onboard.py")
            (root / "tools/qmp_input.py").write_text(QMP_FAKE)
            (root / "build/scripts/qemu.sh").write_text(
                '#!/bin/sh\nexec python3 "$(dirname "$0")/console.py"\n')
            (root / "build/scripts/console.py").write_text(CONSOLE_FAKE)
            disk = root / "test-only-state.ext4"
            disk.write_bytes(b"state-disk-test-fixture")
            signal = root / "qmp-signals"
            env = {
                **os.environ,
                "SYNVEIL_OUT_DIR": str(root / "out"),
                "SYNVEIL_QEMU_STATE_DISK": str(disk),
                "QMP_SIGNALS": str(signal),
                "QMP_SECRET_SLOT": str(root / "secret-slot"),
                "QMP_BOOT_COUNT": str(root / "boot-count"),
                "FAKE_MODE": mode,
            }
            result = subprocess.run(
                ["python3", str(root / "tests/smoke-graphical-onboard.py"),
                 "--timeout", "20"],
                capture_output=True, text=True, env=env, timeout=28,
            )
            count = int((root / "boot-count").read_text())
            signals = signal.read_text().splitlines() if signal.exists() else []
            return result.returncode, count, signals

    def test_success_requires_two_distinct_boot_processes(self):
        code, count, signals = self.run_case("pass")
        self.assertEqual(code, 0)
        self.assertEqual(count, 2)
        self.assertEqual(signals, ["KEYS"] * 4)

    def test_early_boot_exit_never_passes(self):
        self.assertNotEqual(self.run_case("early-exit")[0], 0)

    def test_root_serial_shell_bypass_never_passes(self):
        self.assertNotEqual(self.run_case("serial-bypass")[0], 0)

    def test_missing_enrollment_confirmation_fails(self):
        self.assertNotEqual(self.run_case("missing-confirmation")[0], 0)

    def test_second_boot_must_not_offer_enrollment_again(self):
        self.assertNotEqual(self.run_case("second-enrollment")[0], 0)

    def test_reboot_login_denial_fails(self):
        self.assertNotEqual(self.run_case("reboot-denied")[0], 0)


if __name__ == "__main__":
    unittest.main()
