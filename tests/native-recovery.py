#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Console fixtures test recovery harness gating, timeout, and echo rejection."""
import os
import importlib.util
import json
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
if case in ('enroll', 'enrolled-reboot', 'seat-crash', 'audit-rotation'):
    print('SYNVEIL_STATE_READY', flush=True)
if case in ('enroll', 'seat-locked', 'seat-crash'):
    print('SYNVEIL_SEAT_READY', flush=True)
if case in ('demo', 'desktop', 'desktop-terminal', 'seat-crash', 'enroll'):
    print('SYNVEIL_VT_READY\\nSYNVEIL_DESKTOP_READY', flush=True)
if case in ('seat-crash', 'enroll'):
    assert os.environ.get('SYNVEIL_QEMU_VIDEO') == '1'
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
        assert 'veil-seat --login-stdin' in command and 'kill -TERM' in command
        assert 'NoNewPrivs' in command and '/dev/tty1' in command
        assert 'sha256sum' in command and '600:0:1' in command
    elif case == 'seat-crash':
        print('SYNVEIL_SEAT_RESTARTING', flush=True)
        assert 'veil-enroll --password-stdin' in command
        assert 'veil-seat --login-stdin' in command
        assert 'NoNewPrivs' in command and '/dev/tty1' in command
        assert 'kill -KILL "$broker"' in command
        assert 'synctl --json status' in command
    elif case == 'seat-locked':
        assert 'veil-seat --login-stdin' in command
        assert '600:0' in command and 'login denied' in command
    elif case == 'desktop-terminal':
        assert 'kill -KILL' in command and 'pidof synveil-display' in command
        assert 'NoNewPrivs' in command and '/dev/tty1' in command
        print('SYNVEIL_DESKTOP_RESTARTING', flush=True)
    elif case in ('state-missing', 'state-corrupt'):
        assert 'synctl --json status' in command
        assert '/tmp/state-recovery-probe' in command
    elif case == 'audit-rotation':
        assert 'SYNVEIL_AUDIT_PATH' in command and 'veil-core --once' in command
        assert 'veil-audit check --path' in command and 'sha256sum' in command
        assert '4194304' in command and 'records' in command
        assert max(map(len, command.splitlines())) < 1024
    elif case == 'observe':
        assert 'synctl --json observe' in command
        assert 'synveil.observe/v1' in command
        assert 'total_threads' in command and 'average_1m' in command
        assert 'sectors_read_512b' in command and 'rx_bytes' in command
        assert 'pressure' in command and 'io' in command
    else:
        assert '/children' in command and 'restarted=1' in command
        assert 'for round in 1 2 3' in command and 'shell-alive' in command
        assert max(map(len, command.splitlines())) < 1024
    marker = {'crash': 'RECOVERY', 'absent': 'ABSENT', 'protocol': 'PROTOCOL',
              'audit-rotation': 'AUDIT_ROTATION', 'observe': 'OBSERVE', 'demo': 'DEMO',
              'desktop': 'DESKTOP_RECOVERY', 'desktop-terminal': 'TERMINAL_LIFETIME',
              'enroll': 'ENROLL_SMOKE', 'enrolled-reboot': 'ENROLL_REBOOT',
              'seat-locked': 'SEAT_LOCKED', 'seat-crash': 'SEAT_CRASH',
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

    def test_audit_rotation_requires_real_marker_and_ready_state(self):
        self.assertEqual(self.fixture("pass", "audit-rotation")[0], 0)
        self.assertNotEqual(self.fixture("echo", "audit-rotation")[0], 0)
        self.assertNotEqual(self.fixture("no-ready", "audit-rotation")[0], 0)

    def test_audit_rotation_seed_and_shell_command_are_valid(self):
        spec = importlib.util.spec_from_file_location("audit_rotation_smoke", ROOT / "tests/smoke-recovery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        seed = subprocess.check_output(["awk", module.AUDIT_SEED_AWK.replace("; ", ";\n")])
        self.assertEqual(len(seed), 4 * 1024 * 1024)
        lines = seed.splitlines(keepends=True)
        self.assertEqual(len(lines), 512)
        self.assertTrue(all(len(line) == 8192 for line in lines))
        value = json.loads(lines[0])
        self.assertEqual(value["schema"], "synveil.audit/v1")
        self.assertEqual(value["component"], "veil-core")
        self.assertEqual(len(value), 9)
        command = module.AUDIT_ROTATION[3]
        self.assertIn(module.AUDIT_SEED_AWK, command)
        result = subprocess.run(["sh", "-n"], input=command.replace("; ", ";\n"),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_audit_failure_gate_executes_and_rejects_lost_evidence(self):
        spec = importlib.util.spec_from_file_location("audit_failure_smoke", ROOT / "tests/smoke-recovery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Execute the normalized guest shell with stand-ins for target binaries.
        # This proves gate control flow, not native durability or QEMU behavior.
        for mode in ("pass", "capacity-corrupt", "tail-repair", "capacity-silent",
                     "tail-silent", "accept-tail", "helper-failed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                core = root / "core"
                core.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
journal = pathlib.Path(os.environ['SYNVEIL_AUDIT_PATH'])
mode = os.environ['AUDIT_FIXTURE_MODE']
archive = journal.with_name(journal.name + '.1')
if not archive.exists():
    journal.rename(archive)
    journal.write_text(archive.read_text().splitlines()[0] + '\\n')
    journal.chmod(0o600)
elif journal.stat().st_size == 4194304:
    if mode == 'capacity-corrupt': journal.write_text('lost history')
    if mode != 'capacity-silent':
        print('audit archive exists; retention capacity reached')
else:
    if mode == 'tail-repair':
        with journal.open('a') as stream: stream.write('\\n')
    if mode != 'tail-silent':
        print('persistent audit journal has an incomplete final record')
print(json.dumps({'event': 'request'}, separators=(',', ':')))
print(json.dumps({'event': 'shutdown'}, separators=(',', ':')))
sys.exit(1 if mode == 'helper-failed' else 0)
''')
                inspector = root / "inspector"
                inspector.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
data = pathlib.Path(sys.argv[-1]).read_bytes()
if not data.endswith(b'\\n'):
    sys.exit(0 if os.environ['AUDIT_FIXTURE_MODE'] == 'accept-tail' else 1)
try:
    for line in data.splitlines(): json.loads(line)
except ValueError: sys.exit(1)
print(json.dumps({'records': len(data.splitlines())}, separators=(',', ':')))
''')
                client = root / "client"
                client.write_text("#!/bin/sh\nexit 0\n")
                for executable in (core, inspector, client):
                    executable.chmod(0o700)
                command = module.AUDIT_ROTATION[3].replace(
                    "/var/log/synveil-audit-rotation-test", str(root / "journal-test")
                ).replace("/usr/sbin/veil-core", str(core)).replace(
                    "/usr/sbin/veil-audit", str(inspector)
                ).replace("/usr/bin/synctl", str(client)).replace(
                    "600:0:1", f"600:{os.getuid()}:1"
                ).replace("; ", ";\n")
                result = subprocess.run(["sh"], input=command, text=True,
                                        capture_output=True, timeout=15,
                                        env={**os.environ, "AUDIT_FIXTURE_MODE": mode})
                marker = "SYNVEIL_AUDIT_ROTATION_OK"
                self.assertEqual(marker in result.stdout, mode == "pass", result.stderr)

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

    def test_broker_crash_revokes_non_root_terminal(self):
        self.assertEqual(self.fixture("pass", "seat-crash")[0], 0)
        self.assertNotEqual(self.fixture("echo", "seat-crash")[0], 0)

    def test_non_enrolled_seat_denies_default_credentials(self):
        self.assertEqual(self.fixture("pass", "seat-locked")[0], 0)
        self.assertNotEqual(self.fixture("echo", "seat-locked")[0], 0)

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
