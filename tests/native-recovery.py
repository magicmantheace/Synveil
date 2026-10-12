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
    def fixture(self, mode, case="crash"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools/qmp_input.py").write_text(
                "class QMPError(RuntimeError): pass\n"
                "def open_demo_terminal(path, *, timeout): return None\n"
            )
            shutil.copyfile(ROOT / "tests/smoke-recovery.py", root / "tests/smoke-recovery.py")
            (root / "build/scripts/qemu.sh").write_text(
                'exec python3 "$(dirname "$0")/console.py"\n'
            )
            (root / "build/scripts/console.py").write_text('''import os, sys, time
mode = os.environ['FIXTURE_MODE']
case = os.environ['FIXTURE_CASE']
if mode == 'no-ready':
    print('SYNVEIL_BOOT_OK', flush=True)
    sys.exit(0)
ready = 'SYNVEIL_CORE_UNAVAILABLE' if case == 'absent' else 'SYNVEIL_CORE_READY'
print(ready + '\\r\\nSYNVEIL_BOOT_OK', flush=True)
if case in ('state-missing', 'state-corrupt'):
    print('SYNVEIL_STATE_UNAVAILABLE', flush=True)
if case in ('enroll', 'enrolled-reboot'):
    print('SYNVEIL_STATE_READY', flush=True)
if case in ('demo', 'desktop', 'desktop-terminal'):
    print('SYNVEIL_VT_READY\\nSYNVEIL_DESKTOP_READY', flush=True)
command = ''
while not command.endswith('# SYNVEIL_TEST_COMMAND_END\\n'):
    line = sys.stdin.readline()
    if not line:
        break
    command += line
if mode == 'pass':
    if case == 'crash':
        assert 'kill -KILL' in command and 'synctl --json status' in command
        assert 'shell-alive' in command
    elif case == 'absent':
        assert '! -e /usr/sbin/veil-core' in command and 'shell-alive' in command
    elif case == 'protocol':
        assert 'protocol-probe' in command and 'synctl --json status' in command
    elif case == 'demo':
        assert 'synveil-vt --demo-terminal' in command
        assert 'NoNewPrivs' in command and '/dev/tty1' in command
    elif case == 'desktop':
        assert 'kill -KILL' in command and 'pidof synveil-display' in command
        assert 'synctl --json status' in command
        print('SYNVEIL_DESKTOP_RESTARTING', flush=True)
    elif case == 'enrolled-reboot':
        assert 'credential.phc' in command and 'argon2id' in command
    elif case == 'enroll':
        assert 'veil-enroll --password-stdin' in command
        assert 'sha256sum' in command and '600:0:1' in command
    elif case == 'desktop-terminal':
        assert 'kill -KILL' in command and 'pidof synveil-display' in command
        assert 'NoNewPrivs' in command and '/dev/tty1' in command
        print('SYNVEIL_DESKTOP_RESTARTING', flush=True)
    elif case in ('state-missing', 'state-corrupt'):
        assert 'synctl --json status' in command
        assert '/tmp/state-recovery-probe' in command
    elif case == 'observe':
        assert 'synctl --json observe' in command
        assert 'synveil.observe/v1' in command
        assert 'total_threads' in command and 'average_1m' in command
    else:
        assert '/children' in command and 'restarted=1' in command
        assert 'for round in 1 2 3' in command and 'shell-alive' in command
        assert max(map(len, command.splitlines())) < 1024
    marker = {'crash': 'RECOVERY', 'absent': 'ABSENT', 'protocol': 'PROTOCOL',
              'observe': 'OBSERVE', 'demo': 'DEMO',
              'desktop': 'DESKTOP_RECOVERY', 'desktop-terminal': 'TERMINAL_LIFETIME',
              'enroll': 'ENROLL_SMOKE', 'enrolled-reboot': 'ENROLL_REBOOT',
              'state-missing': 'STATE_RECOVERY',
              'state-corrupt': 'STATE_RECOVERY', 'supervision': 'SUPERVISION'}[case]
    print('SYNVEIL_' + marker + '_OK', flush=True)
elif mode == 'echo':
    print(command, flush=True)
elif mode == 'unavailable':
    print('SYNVEIL_CORE_UNAVAILABLE\\nSYNVEIL_RECOVERY_OK', flush=True)
elif mode == 'timeout':
    time.sleep(30)
''')
            result = subprocess.run(
                ["python3", str(root / "tests/smoke-recovery.py"), "--timeout", "0.5", "--case", case],
                env={**os.environ, "FIXTURE_MODE": mode, "FIXTURE_CASE": case,
                     "SYNVEIL_OUT_DIR": str(root / "out")},
                capture_output=True, text=True, timeout=5,
            )
            name = "recovery" if case == "crash" else case
            log = (root / f"out/logs/qemu-{name}-smoke.log").read_text()
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

    def test_absent_core_shell_passes(self):
        self.assertEqual(self.fixture("pass", "absent")[0], 0)

    def test_absent_core_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "absent")[0], 0)

    def test_protocol_probe_and_status_pass(self):
        self.assertEqual(self.fixture("pass", "protocol")[0], 0)

    def test_protocol_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "protocol")[0], 0)

    def test_observe_ipc_passes(self):
        self.assertEqual(self.fixture("pass", "observe")[0], 0)

    def test_observe_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "observe")[0], 0)

    def test_non_root_demo_terminal_passes_only_strict_identity(self):
        self.assertEqual(self.fixture("pass", "demo")[0], 0)

    def test_demo_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "demo")[0], 0)

    def test_desktop_restart_retains_serial_core(self):
        self.assertEqual(self.fixture("pass", "desktop")[0], 0)

    def test_desktop_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "desktop")[0], 0)

    def test_display_crash_closes_the_non_root_demo_terminal(self):
        self.assertEqual(self.fixture("pass", "desktop-terminal")[0], 0)
        self.assertNotEqual(self.fixture("echo", "desktop-terminal")[0], 0)

    def test_enrollment_reboot_probe_requires_persisted_phc(self):
        self.assertEqual(self.fixture("pass", "enrolled-reboot")[0], 0)
        self.assertNotEqual(self.fixture("echo", "enrolled-reboot")[0], 0)

    def test_enrollment_fixture_requires_private_record_and_no_replacement(self):
        self.assertEqual(self.fixture("pass", "enroll")[0], 0)
        self.assertNotEqual(self.fixture("echo", "enroll")[0], 0)

    def test_no_state_disk_keeps_independent_recovery(self):
        self.assertEqual(self.fixture("pass", "state-missing")[0], 0)
        self.assertNotEqual(self.fixture("echo", "state-missing")[0], 0)

    def test_non_ext4_state_disk_keeps_independent_recovery(self):
        self.assertEqual(self.fixture("pass", "state-corrupt")[0], 0)
        self.assertNotEqual(self.fixture("echo", "state-corrupt")[0], 0)

    def test_supervision_recovery_passes(self):
        self.assertEqual(self.fixture("pass", "supervision")[0], 0)

    def test_supervision_echo_cannot_pass(self):
        self.assertNotEqual(self.fixture("echo", "supervision")[0], 0)


if __name__ == "__main__":
    unittest.main()
