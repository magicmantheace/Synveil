#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""No-QEMU fixture for the staged graphical password/lease smoke protocol."""
import os
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GraphicalLoginFixtureTests(unittest.TestCase):
    def test_return_shell_classifies_live_shell_missing_identity_and_lost_dashboard(self):
        spec = importlib.util.spec_from_file_location("graphical_return", ROOT / "tests/smoke-graphical-login.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for mode, marker in (
            ('missing', 'SYNVEIL_USER_RETURN_FAILED_IDENTITY_MISSING'),
            ('alive', 'SYNVEIL_USER_RETURN_FAILED_SHELL_ALIVE'),
            ('lost', 'SYNVEIL_USER_RETURN_FAILED_DASHBOARD_UNAVAILABLE'),
            ('returned', 'SYNVEIL_USER_TERMINAL_RETURNED'),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for pid in ('123', '456'):
                    (root / pid).mkdir()
                    (root / pid / 'status').write_text('State: S\n')
                    (root / pid / 'cmdline').write_bytes(b'synveil-display\x00--session\x00')
                variables = 'user_session=123; shell_pid=456; '
                if mode == 'missing':
                    variables = 'user_session=; shell_pid=; '
                # Model process liveness only; parsing reads actual fixture files.
                live = '123|456' if mode == 'alive' else '123' if mode == 'returned' else 'never'
                command = ('kill() { case "$2" in ' + live + ') return 0;; *) return 1;; esac; }; ' + variables +
                           module.CHECK_USER_RETURNED.replace('/proc/', str(root) + '/').replace('$(seq 1 100)', '1'))
                result = subprocess.run(['sh'], input=command, text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), marker)

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
                "def open_user_terminal(path, *, timeout):\n"
                "    with open(os.environ['QMP_SIGNALS'], 'a') as stream:\n"
                "        stream.write('TERM_OPEN\\n')\n"
                "def exit_user_terminal(path, *, timeout):\n"
                "    with open(os.environ['QMP_SIGNALS'], 'a') as stream:\n"
                "        stream.write('TERM_EXIT\\n')\n"
                "def logout_user_desktop(path, *, timeout):\n"
                "    with open(os.environ['QMP_SIGNALS'], 'a') as stream:\n"
                "        stream.write('ESC_LOGOUT\\n')\n"
                "def switch_to_login_vt(path, *, timeout):\n"
                "    with open(os.environ['QMP_SIGNALS'], 'a') as stream:\n"
                "        stream.write('VT_SWITCH\\n')\n"
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
               'SYNVEIL_SEAT_READY', 'SYNVEIL_LOGIN_READY',
               'SYNVEIL_SERIAL_RECOVERY_DEVELOPER'):
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
check_tty = sys.stdin.readline()
assert 'AUTH_TTY_READY' in check_tty and 'NoNewPrivs' in check_tty
print('SYNVEIL_AUTH_TTY_READY', flush=True)
wait_for(3)
assert signal.read_text().splitlines()[2] == 'VT_SWITCH'
if mode == 'no-vt-relock':
    sys.exit(0)
print('SYNVEIL_LOGIN_RELOCKED', flush=True)
check_relock = sys.stdin.readline()
assert 'RELOCK_NO_USER_SHELL' in check_relock
print('SYNVEIL_RELOCK_NO_USER_SHELL', flush=True)
wait_for(4)
assert signal.read_text().splitlines()[3] == signal.read_text().splitlines()[1]
print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
check_tty = sys.stdin.readline()
assert 'AUTH_TTY_READY' in check_tty and 'NoNewPrivs' in check_tty
print('SYNVEIL_AUTH_TTY_READY', flush=True)
wait_for(5)
assert signal.read_text().splitlines()[4] == 'TERM_OPEN'
user_terminal = sys.stdin.readline()
assert 'SYNVEIL_USER_TERMINAL_READY' in user_terminal
assert 'NoNewPrivs' in user_terminal and '1000' in user_terminal
if mode == 'no-user-terminal':
    sys.exit(0)
print('SYNVEIL_USER_TERMINAL_READY', flush=True)
wait_for(6)
assert signal.read_text().splitlines()[5] == 'TERM_EXIT'
check_return = sys.stdin.readline()
assert 'SYNVEIL_USER_TERMINAL_RETURNED' in check_return
if mode == 'no-user-return':
    sys.exit(0)
if mode in ('return-shell-alive', 'return-dashboard-unavailable', 'return-identity-missing'):
    reason = mode.removeprefix('return-').replace('-', '_').upper()
    print('SYNVEIL_USER_RETURN_FAILED_' + reason, flush=True)
    # Later success text cannot override an explicit failed check.
    print('SYNVEIL_USER_TERMINAL_RETURNED', flush=True)
    time.sleep(5)
    sys.exit(1)
print('SYNVEIL_USER_TERMINAL_RETURNED', flush=True)
wait_for(7)
assert signal.read_text().splitlines()[6] == 'ESC_LOGOUT'
if mode == 'no-relock':
    sys.exit(0)
print('SYNVEIL_LOGIN_RELOCKED', flush=True)
check_relock = sys.stdin.readline()
assert 'RELOCK_NO_USER_SHELL' in check_relock
print('SYNVEIL_RELOCK_NO_USER_SHELL', flush=True)
wait_for(8)
assert signal.read_text().splitlines()[7] == signal.read_text().splitlines()[1]
print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
check_tty = sys.stdin.readline()
assert 'AUTH_TTY_READY' in check_tty and 'NoNewPrivs' in check_tty
print('SYNVEIL_AUTH_TTY_READY', flush=True)
crash = sys.stdin.readline()
assert 'SYNVEIL_USER_DASHBOARD_KILLED' in crash
assert 'kill -KILL' in crash and '--session' in crash
print('SYNVEIL_USER_DASHBOARD_KILLED', flush=True)
if mode == 'no-user-crash-relock':
    sys.exit(0)
print('SYNVEIL_LOGIN_RELOCKED', flush=True)
check_relock = sys.stdin.readline()
assert 'RELOCK_NO_USER_SHELL' in check_relock
print('SYNVEIL_RELOCK_NO_USER_SHELL', flush=True)
wait_for(9)
assert signal.read_text().splitlines()[8] == signal.read_text().splitlines()[1]
print('SYNVEIL_GRAPHICAL_AUTH_ACTIVE', flush=True)
check_tty = sys.stdin.readline()
assert 'AUTH_TTY_READY' in check_tty and 'NoNewPrivs' in check_tty
print('SYNVEIL_AUTH_TTY_READY', flush=True)
wait_for(10)
assert signal.read_text().splitlines()[9] == 'TERM_OPEN'
terminal = sys.stdin.readline()
assert 'SYNVEIL_USER_TERMINAL_READY' in terminal
assert 'NoNewPrivs' in terminal
if mode == 'no-post-crash-terminal':
    sys.exit(0)
print('SYNVEIL_USER_TERMINAL_READY', flush=True)
wait_for(11)
assert signal.read_text().splitlines()[10] == 'TERM_EXIT'
returned = sys.stdin.readline()
assert 'SYNVEIL_USER_TERMINAL_RETURNED' in returned
if mode == 'no-post-crash-return':
    sys.exit(0)
if mode == 'post-crash-return-failed':
    print('SYNVEIL_USER_RETURN_FAILED_SHELL_ALIVE', flush=True)
    print('SYNVEIL_USER_TERMINAL_RETURNED', flush=True)
    time.sleep(5)
    sys.exit(1)
print('SYNVEIL_USER_TERMINAL_RETURNED', flush=True)
script = ''
while not script.endswith('# SYNVEIL_TEST_COMMAND_END\\n'):
    line = sys.stdin.readline()
    if not line:
        break
    script += line
assert 'NoNewPrivs' in script and '/dev/tty1' in script
assert 'kill -KILL' in script and 'synctl --json status' in script
print('SYNVEIL_GRAPHICAL_LOGIN_CLEANED', flush=True)
if mode != 'no-gui-restart':
    print('SYNVEIL_LOGIN_READY', flush=True)
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
                 "--timeout", "4"], env=env, capture_output=True,
                text=True, timeout=7
            )
            log = (out / "logs/qemu-graphical-login-smoke.log").read_text()
            secrets = signals.read_text().splitlines() if signals.exists() else []
            return result.returncode, log, secrets

    def test_script_requires_wrong_password_then_correct_login_and_cleanup(self):
        code, log, secrets = self.simulate("pass")
        self.assertEqual(code, 0, log)
        self.assertEqual(len(secrets), 11)
        self.assertNotEqual(secrets[0], secrets[1])
        self.assertEqual(secrets[2], "VT_SWITCH")
        self.assertEqual(secrets[3], secrets[1])
        self.assertEqual(secrets[4], "TERM_OPEN")
        self.assertEqual(secrets[5], "TERM_EXIT")
        self.assertEqual(secrets[6], "ESC_LOGOUT")
        self.assertEqual(secrets[7], secrets[1])
        self.assertEqual(secrets[8], secrets[1])
        self.assertEqual(secrets[9], "TERM_OPEN")
        self.assertEqual(secrets[10], "TERM_EXIT")
        for secret in (secrets[0], secrets[1], secrets[3], secrets[7], secrets[8]):
            self.assertRegex(secret, r"^[a-f0-9]{32}$")
            self.assertNotIn(secret, log)
        self.assertIn("SYNVEIL_USER_TERMINAL_READY", log)
        self.assertIn("SYNVEIL_USER_TERMINAL_RETURNED", log)
        self.assertIn("SYNVEIL_LOGIN_RELOCKED", log)
        self.assertIn("SYNVEIL_RELOCK_NO_USER_SHELL", log)
        self.assertIn("SYNVEIL_USER_DASHBOARD_KILLED", log)
        self.assertIn("SYNVEIL_GRAPHICAL_LOGIN_CLEANED", log)

    def test_hard_killed_user_session_must_relock(self):
        self.assertNotEqual(self.simulate("no-user-crash-relock")[0], 0)

    def test_new_login_after_crash_must_reopen_a_user_terminal(self):
        self.assertNotEqual(self.simulate("no-post-crash-terminal")[0], 0)

    def test_new_login_after_crash_must_return_to_dashboard(self):
        self.assertNotEqual(self.simulate("no-post-crash-return")[0], 0)

    def test_missing_child_user_shell_evidence_fails(self):
        self.assertNotEqual(self.simulate("no-user-terminal")[0], 0)

    def test_missing_return_to_same_dashboard_evidence_fails(self):
        self.assertNotEqual(self.simulate("no-user-return")[0], 0)

    def test_explicit_return_failures_stop_before_later_success_or_logout(self):
        for reason in ('shell-alive', 'dashboard-unavailable', 'identity-missing'):
            with self.subTest(reason=reason):
                code, log, signals = self.simulate('return-' + reason)
                self.assertNotEqual(code, 0)
                self.assertIn('terminal return failed in phase: checking-user-return', log)
                self.assertEqual(len(signals), 6)

    def test_post_crash_return_failure_stops_before_cleanup(self):
        code, log, signals = self.simulate('post-crash-return-failed')
        self.assertNotEqual(code, 0)
        self.assertIn('terminal return failed in phase: checking-post-crash-return', log)
        self.assertEqual(len(signals), 11)

    def test_root_login_gui_restart_is_required_after_crash(self):
        self.assertNotEqual(self.simulate("no-gui-restart")[0], 0)

    def test_missing_vt_relock_evidence_fails(self):
        self.assertNotEqual(self.simulate("no-vt-relock")[0], 0)

    def test_missing_relock_evidence_fails(self):
        self.assertNotEqual(self.simulate("no-relock")[0], 0)

    def test_missing_guest_markers_fail(self):
        self.assertNotEqual(self.simulate("no-ready")[0], 0)

    def test_echoed_commands_cannot_spoof_private_secret_handshake(self):
        self.assertNotEqual(self.simulate("echo")[0], 0)


if __name__ == "__main__":
    unittest.main()
