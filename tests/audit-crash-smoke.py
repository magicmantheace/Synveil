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
    def fixture(self, mode, *, real_cut=False, rollover=False, archive_only=False, release=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tools").mkdir()
            shutil.copyfile(ROOT / "tools/audit_fixture.py", root / "tools/audit_fixture.py")
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
rollover = os.environ['CRASH_ROLLOVER'] == '1'
archive_only = os.environ['CRASH_ARCHIVE_ONLY'] == '1'
release = os.environ['CRASH_RELEASE'] == '1'
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
    assert 'sync &&' not in command.split('SYNVEIL_AUDIT_PATH=', 1)[1]
    if rollover or archive_only:
        if archive_only:
            assert '[ ! -e "$journal" ]' in command and '[ ! -L "$journal" ]' in command
            assert ' > "$journal.1"' in command
            assert 'sync &&' in command.split('SYNVEIL_AUDIT_PATH=', 1)[0]
        assert '4194304' in command and '"records":512,' in command
        assert 'sha256sum "$journal.1"' in command and 'copies/archive' in command
        if mode != 'no-archive-digest':
            digest = 'bad' if mode == 'bad-archive-digest' else 'b' * 128
            print('SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=' + digest, flush=True)
            if mode == 'mixed-archive-digest': print('SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=truncated', flush=True)
            if mode == 'duplicate-archive-digest':
                print('SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=' + digest, flush=True)
    if mode != 'no-digest':
        digest = 'bad' if mode == 'bad-digest' else 'a' * 128
        print('SYNVEIL_AUDIT_CRASH_DIGEST=' + digest, flush=True)
        if mode == 'duplicate-digest': print('SYNVEIL_AUDIT_CRASH_DIGEST=' + digest, flush=True)
        if mode == 'mixed-digest': print('SYNVEIL_AUDIT_CRASH_DIGEST=truncated', flush=True)
    print('SYNVEIL_AUDIT_CRASH_WRITE_OK', flush=True)
    if mode == 'early-exit': sys.exit(0)
    time.sleep(30)
else:
    assert command.count('--digest ' + 'a' * 128) == (3 if release else 2)
    assert '/journal' in command and '/copies/snapshot' in command
    assert 'synctl --json status' in command
    if rollover or archive_only:
        assert command.count('--digest ' + 'b' * 128) == (5 if release else 2)
        assert '/journal.1' in command and '/copies/archive' in command
    if release:
        assert 'release-archive --path' in command and '--authorization' in command
        assert 'inspect-release --path' in command and 'completion-recorded' in command
        assert 'not-inspected' in command
        assert 'authorized-before-unlink' in command and '[ ! -e ' in command
        assert 'synveil.audit-retention-release/v1' in command
    if mode in ('lost-data', 'lost-archive', 'release-refused'): sys.exit(1)
    print('SYNVEIL_AUDIT_CRASH_READ_OK', flush=True)
    time.sleep(30)
''')
            arguments = ["python3", str(root / "tests/smoke-audit-crash.py"), "--timeout", "0.8"]
            if rollover:
                arguments.append("--rollover")
            if archive_only:
                arguments.append("--archive-only")
            if release:
                arguments.append("--release-archive")
            run = subprocess.run(
                arguments,
                env={**os.environ, "CRASH_MODE": mode, "CRASH_ROLLOVER": str(int(rollover)),
                     "CRASH_ARCHIVE_ONLY": str(int(archive_only)), "CRASH_RELEASE": str(int(release)), "SYNVEIL_OUT_DIR": str(root / "out"),
                     "SYNVEIL_QEMU_STATE_DISK": str(caller)},
                capture_output=True, text=True, timeout=20,
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
        for rollover, archive_only in ((False, False), (True, False), (False, True)):
            with self.subTest(rollover=rollover, archive_only=archive_only):
                code, log, error = self.fixture("pass", real_cut=True, rollover=rollover, archive_only=archive_only)
                self.assertEqual(code, 0, log + error)
                self.assertIn('emulator SIGKILL and wrapper reap confirmed', log)
                self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_retained_digest_and_private_disk_are_used_for_both_boots(self):
        code, log, error = self.fixture("pass")
        self.assertEqual(code, 0, log + error)
        self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_rollover_requires_separate_archive_digest_and_four_verifications(self):
        code, log, error = self.fixture("pass", rollover=True)
        self.assertEqual(code, 0, log + error)
        self.assertIn('SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=', log)
        self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_archive_only_requires_absent_active_and_retains_both_digests(self):
        code, log, error = self.fixture("pass", archive_only=True)
        self.assertEqual(code, 0, log + error)
        self.assertIn('SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=', log)
        self.assertIn('SYNVEIL_AUDIT_CRASH_READ_OK', log)

    def test_release_requires_reboot_verification_and_rechecks_retained_bytes(self):
        code, log, error = self.fixture("pass", rollover=True, release=True)
        self.assertEqual(code, 0, log + error)
        for mode in ("release-refused", "lost-archive", "bad-archive-digest"):
            self.assertNotEqual(self.fixture(mode, rollover=True, release=True)[0], 0)

    def test_rollover_refuses_missing_ambiguous_or_lost_archive(self):
        for mode in ("no-archive-digest", "bad-archive-digest", "duplicate-archive-digest", "mixed-archive-digest", "lost-archive"):
            for archive_only in (False, True):
                with self.subTest(mode=mode, archive_only=archive_only):
                    self.assertNotEqual(self.fixture(mode, rollover=not archive_only, archive_only=archive_only)[0], 0)

    def test_missing_or_ambiguous_evidence_and_failed_second_boot_are_refused(self):
        for mode in ("no-state", "echo", "no-digest", "bad-digest", "duplicate-digest", "mixed-digest", "lost-data"):
            with self.subTest(mode=mode):
                self.assertNotEqual(self.fixture(mode)[0], 0)

    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true",
                         "real process identity/signals run in CI; local executor proxies processes")
    def test_wrong_child_and_normal_exit_do_not_count_as_power_cut(self):
        for mode in ("wrong-emulator", "early-exit"):
            with self.subTest(mode=mode):
                self.assertNotEqual(self.fixture(mode, real_cut=True)[0], 0)

    def test_rollover_shell_refuses_changed_history_and_failed_archive_export(self):
        spec = importlib.util.spec_from_file_location("rollover_execution", ROOT / "tests/smoke-audit-crash.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = [(a, m) for a in (False, True) for m in
                 ("pass", "archive-corrupt", "wrong-count", "archive-export-failed", "archive-copy-differs")]
        cases.append((True, "seed-sync-failed"))
        for archive_only, mode in cases:
            with self.subTest(mode=mode, archive_only=archive_only), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                scripts = {
                    "core": '''#!/usr/bin/env python3
import json, os, pathlib
path = pathlib.Path(os.environ['SYNVEIL_AUDIT_PATH'])
archive = path.with_name(path.name + '.1')
if os.environ['ARCHIVE_ONLY'] == '1':
    assert not path.exists() and not path.is_symlink()
    assert archive.exists()
    assert (path.parent / 'seed-synced').exists()
else:
    path.rename(archive)
original = archive.read_bytes()
if os.environ['ROLLOVER_MODE'] == 'archive-corrupt': archive.write_bytes(b'changed')
path.write_bytes(original.splitlines(keepends=True)[0])
path.chmod(0o600)
for event in ('request', 'shutdown'): print(json.dumps({'event': event}, separators=(',', ':')))
''',
                    "client": "#!/bin/sh\nexit 0\n",
                    "sync": '''#!/usr/bin/env python3
import os, pathlib, sys
if os.environ['ROLLOVER_MODE'] == 'seed-sync-failed': sys.exit(1)
store = pathlib.Path(os.environ['SYNC_STORE'])
for path in (store / 'journal.1', store):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
(store / 'seed-synced').touch()
''',
                    "inspector": '''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys
mode = os.environ['ROLLOVER_MODE']
args = sys.argv[2:]
path = pathlib.Path(args[args.index('--path') + 1])
data = path.read_bytes()
try:
    for line in data.splitlines(): json.loads(line)
except ValueError: sys.exit(1)
report = {'records': len(data.splitlines()), 'bytes': len(data)}
if sys.argv[1] == 'export':
    if path.name.endswith('.1') and mode == 'archive-export-failed': sys.exit(1)
    output = pathlib.Path(args[args.index('--output') + 1])
    output.write_bytes(data if mode != 'archive-copy-differs' or output.name != 'archive' else b'changed')
    output.chmod(0o600)
    report['digest'] = hashlib.blake2b(data).hexdigest()
elif sys.argv[1] == 'verify':
    if hashlib.blake2b(data).hexdigest() != args[args.index('--digest') + 1]: sys.exit(1)
if path.name.endswith('.1') and mode == 'wrong-count': report['records'] = 1
print(json.dumps(report, separators=(',', ':')))
''',
                }
                for name, content in scripts.items():
                    (root / name).write_text(content)
                    (root / name).chmod(0o700)
                command = module.write_command(not archive_only, archive_only).replace(
                    "/run/synveil-audit-crash-test", str(root / "logs")
                ).replace("/var/lib/synveil/audit-crash-test", str(root / "store")).replace(
                    "/usr/sbin/veil-core", str(root / "core")
                ).replace("/usr/sbin/veil-audit", str(root / "inspector")).replace(
                    "/usr/bin/synctl", str(root / "client")
                ).replace("600:0:1:4194304", f"600:{os.getuid()}:1:4194304").replace('; ', ';\n')
                result = subprocess.run(['sh'], input=command, text=True, capture_output=True,
                                        env={**os.environ, "ROLLOVER_MODE": mode, "ARCHIVE_ONLY": str(int(archive_only)),
                                             "PATH": str(root) + os.pathsep + os.environ['PATH'],
                                             "SYNC_STORE": str(root / "store")}, timeout=15)
                self.assertEqual('SYNVEIL_AUDIT_CRASH_WRITE_OK' in result.stdout,
                                 mode == "pass", result.stderr)
                if mode == "seed-sync-failed":
                    self.assertFalse((root / 'store/journal').exists())

    def test_retained_digest_rejects_malformed_conflicting_receipts(self):
        spec = importlib.util.spec_from_file_location("receipt_execution", ROOT / "tests/smoke-audit-crash.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for label in (b"DIGEST", b"ARCHIVE_DIGEST"):
            prefix = b"SYNVEIL_AUDIT_CRASH_" + label + b"="
            valid = prefix + b"a" * 128
            self.assertEqual(module.retained_digest(b"unrelated\n" + valid + b"\r\n", label), "a" * 128)
            for value in (b"", b"a" * 127, b"A" * 128, b"b" * 129, b"a" * 128 + b" ",
                          b"a" * 128 + b"\x00"):
                malformed = prefix + value
                for log in (malformed + b"\n", valid + b"\n" + malformed + b"\n",
                            malformed + b"\n" + valid + b"\n"):
                    with self.subTest(label=label, length=len(value), log=log):
                        with self.assertRaises(ValueError):
                            module.retained_digest(log, label)

    def test_digest_input_and_generated_shell_are_bounded(self):
        spec = importlib.util.spec_from_file_location("crash_smoke", ROOT / "tests/smoke-audit-crash.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for digest in ("", "x" * 128, "A" * 128, "a" * 127, "a; echo bad"):
            with self.assertRaises(ValueError):
                module.read_command(digest)
            with self.assertRaises(ValueError):
                module.read_command('a' * 128, digest)
        with self.assertRaises(ValueError):
            module.write_command(True, True)
        with self.assertRaises(ValueError):
            module.read_command('a' * 128, release_archive=True)
        for command in (module.read_command('a' * 128, 'b' * 128, True),
                        module.write_command(archive_only=True).replace('; ', ';\n'),
                        module.write_command().replace('; ', ';\n'),
                        module.write_command(True).replace('; ', ';\n'),
                        module.read_command('a' * 128),
                        module.read_command('a' * 128, 'b' * 128)):
            self.assertLess(max(map(len, command.splitlines())), 1024)
            result = subprocess.run(['sh', '-n'], input=command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
