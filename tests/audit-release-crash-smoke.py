#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Check reboot recovery decisions and host-retained digest/cut gating."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_smoke", ROOT / "tests/smoke-audit-release-crash.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
STUB = '''#!/usr/bin/env python3
import json, pathlib, sys
op, *arguments = sys.argv[1:]
args = dict(zip(arguments[::2], arguments[1::2]))
try:
    path = pathlib.Path(args['--path'])
    digest = args['--digest']
    if op == 'verify':
        assert path.read_bytes() == (b'a' if digest == 'a' * 128 else b'b')
    elif op == 'inspect-release':
        phase = pathlib.Path(args['--authorization']).read_text()
        assert phase in ('authorization-only', 'incomplete-completion', 'completion-recorded')
        print(json.dumps({'state': phase, 'archive_state': 'not-inspected'}, separators=(',', ':')))
    elif op == 'release-archive':
        archive = path.with_name('journal.1')
        assert archive.read_bytes() == b'b'
        with open(args['--authorization'], 'x') as receipt:
            receipt.write('fresh release')
        archive.unlink()
    else:
        raise AssertionError('unknown operation')
except (OSError, KeyError, AssertionError):
    sys.exit(1)
'''


class RecoveryTests(unittest.TestCase):
    def command(self, phase, archive, state, *, corrupt=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copies = root / 'copies'
            copies.mkdir()
            (root / 'journal').write_bytes(b'a')
            (copies / 'snapshot').write_bytes(b'a')
            (copies / 'archive').write_bytes(b'corrupt' if corrupt else b'b')
            receipt = copies / 'release.json'
            receipt.write_text(state)
            if archive:
                (root / 'journal.1').write_bytes(b'b')
            stub = root / 'veil-audit'
            stub.write_text(STUB)
            stub.chmod(0o755)
            command = smoke.read_command(phase, 'a' * 128, 'b' * 128)
            self.assertLess(max(map(len, command.splitlines())), 512)
            self.assertLess(len(command), 4096)
            syntax = subprocess.run(['sh', '-n'], input=command, text=True, capture_output=True)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            command = command.replace(smoke.STORE, str(root)).replace('/usr/sbin/veil-audit', str(stub))
            command = command.replace('/run/release-inspection.json', str(root / 'inspection.json'))
            result = subprocess.run(['sh'], input=command, text=True, capture_output=True, timeout=5)
            self.assertEqual(receipt.read_text(), state)
            self.assertEqual((root / 'journal').read_bytes(), b'a')
            self.assertEqual((copies / 'snapshot').read_bytes(), b'a')
            self.assertEqual((copies / 'archive').read_bytes(), b'corrupt' if corrupt else b'b')
            passed = smoke.READ_MARKER.decode() in result.stdout.splitlines()
            if passed:
                self.assertFalse((root / 'journal.1').exists())
                self.assertEqual((copies / 'recovered.json').exists(), archive)
            return passed

    def test_replayed_archive_presence_and_receipt_states(self):
        for phase, present, state in [
            ('before-unlink', True, 'authorization-only'),
            ('after-unlink', True, 'authorization-only'),
            ('after-unlink', False, 'authorization-only'),
            ('unlink-synced', False, 'authorization-only'),
            ('partial-completion', False, 'authorization-only'),
            ('partial-completion', False, 'incomplete-completion'),
            ('completion-written', False, 'authorization-only'),
            ('completion-written', False, 'incomplete-completion'),
            ('completion-written', False, 'completion-recorded'),
            ('completion-synced', False, 'completion-recorded'),
        ]:
            with self.subTest(phase=phase, present=present, state=state):
                self.assertTrue(self.command(phase, present, state))

    def test_missing_history_wrong_phase_or_corrupt_copy_never_emit_success(self):
        for phase, present, state, corrupt in [
            ('before-unlink', False, 'authorization-only', False),
            ('unlink-synced', True, 'authorization-only', False),
            ('partial-completion', True, 'incomplete-completion', False),
            ('partial-completion', False, 'completion-recorded', False),
            ('completion-written', True, 'completion-recorded', False),
            ('completion-synced', False, 'authorization-only', False),
            ('completion-synced', False, 'incomplete-completion', False),
            ('completion-synced', True, 'completion-recorded', False),
            ('after-unlink', False, 'invalid', False),
            ('after-unlink', False, 'authorization-only', True),
        ]:
            with self.subTest(phase=phase, present=present, state=state):
                self.assertFalse(self.command(phase, present, state, corrupt=corrupt))

    def test_invalid_digest_or_cut_refuses_command_construction(self):
        for phase, digest in [('unknown', 'a' * 128), ('before-unlink', 'A' * 128),
                              ('before-unlink', 'x; bad')]:
            with self.assertRaises(ValueError):
                smoke.read_command(phase, digest, 'b' * 128)

    def test_host_only_accepts_exact_cut_and_unique_retained_digests(self):
        for fault in (None, 'cut-failed', 'missing-digest', 'duplicate-digest', 'read-failed'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                calls = []
                disks = []

                def create(path, size):
                    self.assertEqual(size, 64)
                    disks.append(path)
                    path.touch()

                def boot(disk, marker, command, timeout, log, **kwargs):
                    calls.append((disk, marker, kwargs))
                    if kwargs.get('abrupt'):
                        phase = smoke.PHASES[(len(calls) - 1) // 2]
                        self.assertEqual(marker, b'SYNVEIL_RELEASE_GUEST_CUT=' + phase.encode())
                        self.assertIn('audit-release-cut ' + phase, command)
                        if fault == 'cut-failed':
                            return False
                        lines = [b'SYNVEIL_AUDIT_CRASH_DIGEST=' + b'a' * 128]
                        if fault != 'missing-digest':
                            lines.append(b'SYNVEIL_AUDIT_CRASH_ARCHIVE_DIGEST=' + b'b' * 128)
                        if fault == 'duplicate-digest':
                            lines.append(lines[-1])
                        log.write_bytes(b'\n'.join(lines) + b'\n')
                        return True
                    self.assertEqual(marker, smoke.READ_MARKER)
                    self.assertEqual(disk, calls[-2][0])
                    self.assertIn('--digest ' + 'a' * 128, command)
                    self.assertIn('--digest ' + 'b' * 128, command)
                    return fault != 'read-failed'

                with patch.dict(os.environ, {'SYNVEIL_OUT_DIR': directory}), \
                        patch.object(smoke.audit, 'create_state_disk', side_effect=create), \
                        patch.object(smoke.audit.persistence, 'boot_round', side_effect=boot):
                    self.assertEqual(smoke.smoke(1), fault is None)
                self.assertEqual(len(calls), 12 if fault is None else 2 if fault == 'read-failed' else 1)
                self.assertEqual(len(set(disks)), 6 if fault is None else 1)


if __name__ == '__main__':
    unittest.main()
