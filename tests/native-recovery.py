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
            shutil.copyfile(ROOT / "tools/audit_fixture.py", root / "tools/audit_fixture.py")
            (root / "tools/qmp_input.py").write_text(
                "class QMPError(RuntimeError): pass\n"
                "def open_demo_terminal(path, *, timeout): return None\n"
            )
            (root / "tools/state_disk.py").write_text(
                "def create_state_disk(path, size_mib):\n"
                "    assert size_mib == 64\n"
                "    path.write_bytes(b'disposable fixture')\n"
            )
            caller_disk = root / "caller.ext4"
            caller_disk.write_bytes(b'preserve caller image')
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
if case in ('enroll', 'enrolled-reboot', 'seat-crash', 'audit-rotation', 'audit-ext4') and mode != 'no-state':
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
    elif case == 'audit-ext4':
        path = os.environ['SYNVEIL_QEMU_STATE_DISK']
        assert 'synveil-audit-ext4-' in path and not path.endswith('caller.ext4')
        assert open(path, 'rb').read() == b'disposable fixture'
        print('AUDIT_TEST_DISK=' + path, flush=True)
        assert 'count=65536' in command and '/proc/mounts' in command
        assert '$3=="ext4"' in command and '/var/lib/synveil/audit-ext4-test' in command
        assert 'StorageFull' in command and 'audit_probe resumed' in command
        assert 'export-recovered.json' in command and 'veil-audit verify' in command
        assert 'persistent_audit' in command and '"configured": true' in command
        assert 'failed_writes' in command
        assert 'SYNVEIL_EXPECT_AUDIT_STATE' in command
        assert 'remount' not in command and 'umount' not in command
        assert max(map(len, command.splitlines())) < 1024
    elif case == 'audit-enospc':
        assert 'size=1048576' in command and 'count=2048' in command
        assert 'No space left on device' in command and 'audit_probe resumed' in command
        assert 'remount,ro' in command and 'remount,rw' in command
        assert 'Read-only file system' in command and 'audit_probe writable' in command
        assert 'StorageFull' in command and 'ReadOnlyFilesystem' in command
        assert 'export-recovered.json' in command and 'veil-audit verify' in command
        assert 'persistent_audit' in command and 'failed_writes' in command
        assert 'SYNVEIL_EXPECT_AUDIT_STATE' in command
        assert 'umount' in command and 'sha256sum' in command
        assert max(map(len, command.splitlines())) < 1024
    elif case == 'audit-rotation':
        assert 'SYNVEIL_AUDIT_PATH' in command and 'veil-core --once' in command
        assert 'veil-audit check --path' in command and 'sha256sum' in command
        assert 'veil-audit export --path' in command and 'veil-audit verify --path' in command
        assert '4194304' in command and 'records' in command
        assert 'persistent_audit' in command and 'failed_writes' in command
        assert 'SYNVEIL_EXPECT_AUDIT_STATE' in command
        assert max(map(len, command.splitlines())) < 1024
    elif case == 'observe':
        assert 'synctl --json observe' in command
        assert 'synveil.observe/v1' in command
        assert 'busy_basis_points' in command and 'sample_window_ms' in command
        assert 'logical_cpus' in command
        assert 'frequency' in command
        assert 'thermal' in command
        assert 'processes' in command and 'start_time_ticks' in command
        assert 'energy' in command
        assert 'total_threads' in command and 'average_1m' in command
        assert 'sectors_read_512b' in command and 'rx_bytes' in command
        assert 'pressure' in command and 'io' in command
    else:
        assert '/children' in command and 'restarted=1' in command
        assert 'for round in 1 2 3' in command and 'shell-alive' in command
        assert max(map(len, command.splitlines())) < 1024
    marker = {'crash': 'RECOVERY', 'absent': 'ABSENT', 'protocol': 'PROTOCOL',
              'audit-ext4': 'AUDIT_EXT4', 'audit-enospc': 'AUDIT_ENOSPC', 'audit-rotation': 'AUDIT_ROTATION', 'observe': 'OBSERVE', 'demo': 'DEMO',
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
                     "SYNVEIL_OUT_DIR": str(root / "out"),
                     "SYNVEIL_QEMU_STATE_DISK": str(caller_disk)},
                capture_output=True, text=True, timeout=5,
            )
            name = "recovery" if case == "crash" else case
            log = (root / f"out/logs/qemu-{name}-smoke.log").read_text()
            if case == "audit-ext4":
                self.assertEqual(caller_disk.read_bytes(), b'preserve caller image')
                for line in log.splitlines():
                    if line.startswith("AUDIT_TEST_DISK="):
                        self.assertFalse(Path(line.split("=", 1)[1]).parent.exists())
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

    def test_audit_ext4_requires_disposable_disk_and_real_marker(self):
        self.assertEqual(self.fixture("pass", "audit-ext4")[0], 0)
        self.assertNotEqual(self.fixture("echo", "audit-ext4")[0], 0)
        self.assertNotEqual(self.fixture("no-ready", "audit-ext4")[0], 0)
        self.assertNotEqual(self.fixture("no-state", "audit-ext4")[0], 0)

    def test_audit_enospc_requires_real_marker_and_healthy_boot(self):
        self.assertEqual(self.fixture("pass", "audit-enospc")[0], 0)
        self.assertNotEqual(self.fixture("echo", "audit-enospc")[0], 0)
        self.assertNotEqual(self.fixture("no-ready", "audit-enospc")[0], 0)

    def test_audit_enospc_seed_is_page_aligned_and_shell_valid(self):
        spec = importlib.util.spec_from_file_location("audit_enospc_smoke", ROOT / "tests/smoke-recovery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        seed = subprocess.check_output(["awk", module.AUDIT_ENOSPC_SEED_AWK.replace("; ", ";\n")])
        self.assertEqual(len(seed), 4096)
        self.assertEqual(len(seed.splitlines()), 1)
        self.assertEqual(json.loads(seed)["schema"], "synveil.audit/v1")
        result = subprocess.run(["sh", "-n"], input=module.AUDIT_ENOSPC[3].replace("; ", ";\n"),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_enospc_gate_rejects_false_exhaustion_and_failed_recovery(self):
        spec = importlib.util.spec_from_file_location("audit_enospc_execution", ROOT / "tests/smoke-recovery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modes = ("pass", "no-exhaustion", "full-write", "no-warning",
                     "resume-failed", "unmount-failed", "ro-write", "ro-silent",
                     "rw-failed", "ro-remount-failed", "rw-remount-failed",
                     "full-export-succeeds", "full-export-partial", "wrong-full-error",
                     "readonly-export-succeeds", "readonly-export-partial",
                     "recovered-export-fails", "recovered-copy-differs",
                     "wrong-audit-state", "zero-audit-failures",
                     "healthy-audit-failures", "wrong-audit-config")
        ext4_modes = tuple(mode for mode in modes if not mode.startswith(("ro-", "rw-", "readonly-"))
                           and mode != "unmount-failed") + ("wrong-fstype", "wrong-device", "missing-mount",
                                                           "wrong-fill-error", "main-unhealthy")
        for case, mode in ([("audit-enospc", mode) for mode in modes] +
                           [("audit-ext4", mode) for mode in ext4_modes]):
            with self.subTest(case=case, mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scripts = {
                    "mount": '''#!/usr/bin/env python3
import os, pathlib, sys
mode = os.environ['ENOSPC_MODE']
marker = pathlib.Path(sys.argv[-1]).parent / 'readonly'
if 'remount,ro' in sys.argv:
    if mode == 'ro-remount-failed': sys.exit(1)
    marker.touch()
elif 'remount,rw' in sys.argv:
    if mode == 'rw-remount-failed': sys.exit(1)
    marker.unlink()
''',
                    "umount": '#!/bin/sh\n[ "$ENOSPC_MODE" != unmount-failed ]\n',
                    "client": '''#!/bin/sh
[ "$ENOSPC_MODE" != main-unhealthy ] || [ "$1" = --socket ] || exit 1
state=${SYNVEIL_EXPECT_AUDIT_STATE:-healthy}
failed=0
configured=true
if [ "$state" = degraded ]; then failed=3; fi
if [ "$ENOSPC_MODE" = wrong-audit-state ]; then
    if [ "$state" = healthy ]; then state=degraded; else state=healthy; fi
fi
if [ "$ENOSPC_MODE" = zero-audit-failures ]; then failed=0; fi
if [ "$ENOSPC_MODE" = healthy-audit-failures ] && [ "$state" = healthy ]; then failed=3; fi
if [ "$ENOSPC_MODE" = wrong-audit-config ]; then configured=false; fi
printf '{"persistent_audit": {"configured": %s, "state": "%s", "failed_writes": %s}}\n' "$configured" "$state" "$failed"
''',

                    "dd": '''#!/usr/bin/env python3
import os, pathlib, sys
path = next(arg[3:] for arg in sys.argv if arg.startswith('of='))
pathlib.Path(path).write_bytes(b'fixture filler')
print('Permission denied' if os.environ['ENOSPC_MODE'] == 'wrong-fill-error' else 'No space left on device', file=sys.stderr)
sys.exit(0 if os.environ['ENOSPC_MODE'] == 'no-exhaustion' else 1)
''',
                    "core": '''#!/usr/bin/env python3
import json, os, pathlib
journal = pathlib.Path(os.environ['SYNVEIL_AUDIT_PATH'])
mode = os.environ['ENOSPC_MODE']
if (journal.parent.parent / 'readonly').exists():
    if mode == 'ro-write':
        with journal.open('ab') as stream: stream.write(b'changed')
    if mode != 'ro-silent':
        print('[synveil-audit] persistent write failed: Read-only file system')
elif journal.with_name('filler').exists():
    if mode == 'full-write': journal.write_text('lost evidence')
    if mode != 'no-warning':
        print('[synveil-audit] persistent write failed: No space left on device')
else:
    if mode == 'resume-failed' or (mode == 'rw-failed' and journal.stat().st_size > 4096):
        print('[synveil-audit] persistent write failed')
    else:
        original = journal.read_bytes()
        with journal.open('ab') as stream: stream.write(original)
for event in ('request', 'shutdown'):
    print(json.dumps({'event': event}, separators=(',', ':')))
''',
                    "inspector": '''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys
mode = os.environ['ENOSPC_MODE']
arguments = sys.argv[2:]
path = pathlib.Path(arguments[arguments.index('--path') + 1])
data = path.read_bytes()
try:
    for line in data.splitlines(): json.loads(line)
except ValueError: sys.exit(1)
report = {'records': len(data.splitlines()), 'schema': 'fixture'}
if sys.argv[1] == 'export':
    output = pathlib.Path(arguments[arguments.index('--output') + 1])
    full = path.with_name('filler').exists()
    readonly = (path.parent.parent / 'readonly').exists()
    if full or readonly:
        prefix = 'full' if full else 'readonly'
        if mode == prefix + '-export-succeeds':
            output.write_bytes(data)
            sys.exit(0)
        if mode == prefix + '-export-partial': output.write_bytes(b'partial')
        kind = 'StorageFull' if full else 'ReadOnlyFilesystem'
        if mode == 'wrong-full-error' and full: kind = 'PermissionDenied'
        print('operation refused (' + kind + ')', file=sys.stderr)
        sys.exit(1)
    if mode == 'recovered-export-fails': sys.exit(1)
    if mode == 'recovered-copy-differs': data = data.replace(b'fixture', b'changed')
    output.write_bytes(data)
    output.chmod(0o600)
    report['digest'] = hashlib.blake2b(data).hexdigest()
elif sys.argv[1] == 'verify':
    if hashlib.blake2b(data).hexdigest() != arguments[arguments.index('--digest') + 1]:
        sys.exit(1)
print(json.dumps(report, separators=(',', ':')))
''',
                }
                for name, content in scripts.items():
                    (root / name).write_text(content)
                    (root / name).chmod(0o700)
                mounts = root / "mounts"
                device = "/dev/wrong" if mode == "wrong-device" else "/dev/vdb"
                fstype = "tmpfs" if mode == "wrong-fstype" else "ext4"
                mounts.write_text("" if mode == "missing-mount" else f"{device} /var {fstype} rw 0 0\n")
                command = module.SCENARIOS[case][3].replace(
                    "/var/lib/synveil/audit-ext4-test", str(root / "test/store")
                ).replace("/run/synveil-audit-ext4-test", str(root / "test")).replace(
                    "/proc/mounts", str(mounts)
                ).replace(
                    "/run/synveil-audit-enospc-test", str(root / "test")
                ).replace("mount -t tmpfs", str(root / "mount") + " -t tmpfs").replace(
                    "mount -o remount", str(root / "mount") + " -o remount"
                ).replace(
                    'umount "$store"', str(root / "umount") + ' "$store"'
                ).replace("dd if=", str(root / "dd") + " if=").replace(
                    "/usr/sbin/veil-core", str(root / "core")
                ).replace("/usr/sbin/veil-audit", str(root / "inspector")).replace(
                    "/usr/bin/synctl", str(root / "client")
                ).replace("; ", ";\n")
                result = subprocess.run(["sh"], input=command, text=True,
                                        capture_output=True, timeout=15,
                                        env={**os.environ, "ENOSPC_MODE": mode})
                self.assertEqual(module.SCENARIOS[case][2].decode() in result.stdout,
                                 mode == "pass", result.stderr)

    def test_audit_failure_gate_executes_and_rejects_lost_evidence(self):
        spec = importlib.util.spec_from_file_location("audit_failure_smoke", ROOT / "tests/smoke-recovery.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Execute the normalized guest shell with stand-ins for target binaries.
        # This proves gate control flow, not native durability or QEMU behavior.
        for mode in ("pass", "capacity-corrupt", "tail-repair", "capacity-silent",
                     "tail-silent", "accept-tail", "helper-failed", "export-failed",
                     "verify-accepts-tamper", "export-overwrites", "wrong-audit-state",
                     "zero-audit-failures", "healthy-audit-failures", "wrong-audit-config"):
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
import hashlib, json, os, pathlib, sys
mode = os.environ['AUDIT_FIXTURE_MODE']
arguments = sys.argv[2:]
path = pathlib.Path(arguments[arguments.index('--path') + 1])
data = path.read_bytes()
if not data.endswith(b'\\n'):
    sys.exit(0 if mode == 'accept-tail' else 1)
try:
    for line in data.splitlines(): json.loads(line)
except ValueError: sys.exit(1)
report = {'records': len(data.splitlines())}
if sys.argv[1] == 'export':
    if mode == 'export-failed': sys.exit(1)
    output = pathlib.Path(arguments[arguments.index('--output') + 1])
    if output.exists() and mode != 'export-overwrites': sys.exit(1)
    output.write_bytes(data)
    output.chmod(0o600)
    report['digest'] = hashlib.blake2b(data).hexdigest()
elif sys.argv[1] == 'verify':
    expected = arguments[arguments.index('--digest') + 1]
    if hashlib.blake2b(data).hexdigest() != expected and mode != 'verify-accepts-tamper':
        sys.exit(1)
print(json.dumps(report, separators=(',', ':')))
''')
                client = root / "client"
                client.write_text('''#!/bin/sh
state=${SYNVEIL_EXPECT_AUDIT_STATE:-healthy}
failed=0
configured=true
if [ "$state" = degraded ]; then failed=3; fi
if [ "$AUDIT_FIXTURE_MODE" = wrong-audit-state ]; then
    if [ "$state" = healthy ]; then state=degraded; else state=healthy; fi
fi
if [ "$AUDIT_FIXTURE_MODE" = zero-audit-failures ]; then failed=0; fi
if [ "$AUDIT_FIXTURE_MODE" = healthy-audit-failures ] && [ "$state" = healthy ]; then failed=3; fi
if [ "$AUDIT_FIXTURE_MODE" = wrong-audit-config ]; then configured=false; fi
printf '{"persistent_audit": {"configured": %s, "state": "%s", "failed_writes": %s}}\n' "$configured" "$state" "$failed"
''')
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
