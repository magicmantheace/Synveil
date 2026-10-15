#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host process fixtures for abrupt emulator stop and retained-digest gating."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AuditCrashTests(unittest.TestCase):
    def fixture(self, mode, *, real_cut=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tools").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            for name in ("smoke-audit-crash.py", "smoke-persistence.py"):
                shutil.copyfile(ROOT / "tests" / name, root / "tests" / name)
            (root / "tools/state_disk.py").write_text(
                "def create_state_disk(path, size_mib):\n"
                "    assert size_mib == 64\n"
                "    path.write_bytes(b'private new disk')\n"
            )
            if not real_cut:
                # Control-flow fixtures do not impersonate real signal evidence.
                with (root / "tests/smoke-persistence.py").open("a") as stream:
                    stream.write("\ndef power_cut(process): return True\n")
            caller = root / "caller.ext4"
            caller.write_bytes(b'never touched')
            wrapper = root / "build/scripts/qemu.sh"
            wrapper.write_text('''#!/bin/bash
name=qemu-system-x86_64
[ "$CRASH_MODE" != wrong-emulator ] || name=unrelated-process
bash -c 'exec -a "$1" python3 "$(dirname "$2")/console.py"' fixture "$name" "$0" <&0 &
child=$!
trap 'kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM
wait "$child"
status=$?
[ "$CRASH_MODE" != early-exit ] || exit 0
exit "$status"
''')
            (root / "build/scripts/console.py").write_text('''import os, pathlib, sys, time
mode = os.environ['CRASH_MODE']
disk = pathlib.Path(os.environ['SYNVEIL_QEMU_STATE_DISK'])
assert 'synveil-audit-crash-' in str(disk)
round_path = disk.with_suffix('.round')
first = not round_path.exists()
round_path.write_text('started')
print('DISK=' + str(disk), flush=True)
if mode == 'no-state':
    print('SYNVEIL_BOOT_OK', flush=True)
    time.sleep(30)
print('SYNVEIL_BOOT_OK\\nSYNVEIL_STATE_READY\\nSYNVEIL_CORE_READY', flush=True)
command = ''
marker = 'AUDIT_CRASH_WRITE_OK' if first else 'AUDIT_CRASH_READ_OK'
while marker not in command:
    line = sys.stdin.readline()
    if not line: sys.exit(1)
    command += line
if mode == 'echo':
    print(command, flush=True)
    sys.exit(0)
if first:
    assert 'veil-core --once' in command and 'wait "$helper"' in command
    assert 'persistent write failed' in command and 'veil-audit export' in command
    assert 'sync &&' not in command
    if mode != 'no-digest':
        digest = 'bad' if mode == 'bad-digest' else 'a' * 128
        print('SYNVEIL_AUDIT_CRASH_DIGEST=' + digest, flush=True)
        if mode == 'duplicate-digest': print('SYNVEIL_AUDIT_CRASH_DIGEST=' + digest, flush=True)
    print('SYNVEIL_AUDIT_CRASH_WRITE_OK', flush=True)
    if mode == 'early-exit': sys.exit(0)
    time.sleep(30)
else:
    assert command.count('--digest ' + 'a' * 128) == 2
    assert '/journal' in command and '/copies/snapshot' in command
    assert 'synctl --json status' in command
    if mode == 'lost-data': sys.exit(1)
    print('SYNVEIL_AUDIT_CRASH_READ_OK', flush=True)
    time.sleep(30)
''')
            run = subprocess.run(
                ["python3", str(root / "tests/smoke-audit-crash.py"), "--timeout", "0.8"],
                env={**os.environ, "CRASH_MODE": mode, "SYNVEIL_OUT_DIR": str(root / "out"),
                     "SYNVEIL_QEMU_STATE_DISK": str(caller)},
                capture_output=True, text=True, timeout=8,
            )
            logs = list((root / "out/logs").glob("*.log"))
            content = "\n".join(p.read_text() for p in logs)
            self.assertEqual(caller.read_bytes(), b'never touched')
            for line in content.splitlines():
                if line.startswith('DISK='):
                    self.assertFalse(Path(line.split('=', 1)[1]).parent.exists())
            return run.returncode, content, run.stderr

    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true",
                         "real process identity/signals run in CI; local executor proxies processes")
    def test_actual_emulator_child_is_killed_and_reaped_before_second_boot(self):
        code, log, error = self.fixture("pass", real_cut=True)
        self.assertEqual(code, 0, log + error)
        self.assertIn('emulator SIGKILL and wrapper reap confirmed', log)
        self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_retained_digest_and_private_disk_are_used_for_both_boots(self):
        code, log, error = self.fixture("pass")
        self.assertEqual(code, 0, log + error)
        self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_missing_or_ambiguous_evidence_and_failed_second_boot_are_refused(self):
        for mode in ("no-state", "echo", "no-digest", "bad-digest", "duplicate-digest", "lost-data"):
            with self.subTest(mode=mode):
                self.assertNotEqual(self.fixture(mode)[0], 0)

    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true",
                         "real process identity/signals run in CI; local executor proxies processes")
    def test_wrong_child_and_normal_exit_do_not_count_as_power_cut(self):
        for mode in ("wrong-emulator", "early-exit"):
            with self.subTest(mode=mode):
                self.assertNotEqual(self.fixture(mode, real_cut=True)[0], 0)

    def test_digest_input_and_generated_shell_are_bounded(self):
        spec = importlib.util.spec_from_file_location("crash_smoke", ROOT / "tests/smoke-audit-crash.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for digest in ("", "x" * 128, "A" * 128, "a" * 127, "a; echo bad"):
            with self.assertRaises(ValueError):
                module.read_command(digest)
        for command in (module.WRITE.replace('; ', ';\n'), module.read_command('a' * 128)):
            self.assertLess(max(map(len, command.splitlines())), 1024)
            result = subprocess.run(['sh', '-n'], input=command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
