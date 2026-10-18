#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compile the syscall tracer and verify real stopped-child boundaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("before-unlink", "after-unlink", "unlink-synced", "partial-completion",
          "completion-written", "completion-synced")
WORKER = r'''
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 10) return 2;
    char *root = strdup(argv[3]), *copies = strdup(argv[7]);
    *strrchr(root, '/') = 0;
    *strrchr(copies, '/') = 0;
    int directory = open(root, O_RDONLY | O_DIRECTORY);
    int copydir = open(copies, O_RDONLY | O_DIRECTORY);
    int receipt = openat(copydir, ".", O_TMPFILE | O_RDWR, 0600);
    const char *fault = getenv("SYNVEIL_TEST_CUT_WORKER_FAULT");
    if (directory < 0 || copydir < 0 || receipt < 0) return 3;
    if (fault && !strcmp(fault, "early-exit")) return 4;
    char source[64];
    snprintf(source, sizeof(source), "/proc/self/fd/%d", receipt);
    if (write(receipt, "intent\n", 7) != 7 || fsync(receipt)) return 5;
    if (fault && !strcmp(fault, "wrong-publication")) {
        if (linkat(receipt, "", copydir, "release.json", AT_EMPTY_PATH)) return 5;
    } else if (linkat(AT_FDCWD, source, copydir, "release.json", AT_SYMLINK_FOLLOW)) return 5;
    if (!(fault && !strcmp(fault, "missing-sync")) && fsync(copydir)) return 6;
    if (unlinkat(directory, "journal.1", 0) || fsync(directory)) return 7;
    if (write(receipt, "completion\n", 11) != 11 || fsync(receipt)) return 8;
    return 0;
}
'''


class CutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.probe = root / "probe"
        cls.worker = root / "worker"
        source = root / "worker.c"
        source.write_text(WORKER)
        for source, output in [(ROOT / "tests/audit-release-cut.c", cls.probe),
                               (source, cls.worker)]:
            subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(source), "-o", str(output)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def arguments(self, phase, root):
        return [str(self.probe), phase, str(self.worker), str(root), "a" * 128]

    def test_invalid_inputs_never_start_a_worker(self):
        for args in [[], ["unknown", "/none", "/none", "a" * 128],
                     ["before-unlink", "/none", "/none", "A" * 128]]:
            result = subprocess.run([str(self.probe), *args], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")

    def check(self, phase, fault=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "copies").mkdir()
            archive = root / "journal.1"
            archive.write_bytes(b"preserved archive")
            process = subprocess.Popen(self.arguments(phase, root), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, start_new_session=True,
                                       env={**os.environ, "SYNVEIL_TEST_CUT_WORKER_FAULT": fault or ""})
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), "checkpoint timeout")
                    output = os.read(process.stdout.fileno(), 4096)
                if not output:
                    code = process.wait(timeout=3)
                    if code == 77 and os.environ.get("GITHUB_ACTIONS") != "true":
                        self.skipTest("local process policy blocks ptrace")
                    if fault:
                        self.assertEqual(code, 1)
                        return
                    self.fail(f"missing checkpoint: exit {code}")
                self.assertIsNone(fault, "a refused operation emitted a checkpoint")
                self.assertEqual(output, b"\nSYNVEIL_RELEASE_GUEST_CUT=" + phase.encode() + b"\n")
                self.assertIsNone(process.poll(), "tracer must hold its live child")
                self.assertEqual(archive.exists(), phase == "before-unlink")
                receipt = (root / "copies/release.json").read_bytes()
                tail = (b"c" if phase == "partial-completion" else
                        b"completion\n" if phase.startswith("completion-") else b"")
                self.assertEqual(receipt, b"intent\n" + tail)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()

    def test_all_six_real_syscall_boundaries(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.check(phase)

    def test_failure_or_missing_authorization_sync_cannot_emit_checkpoint(self):
        for fault in ("early-exit", "missing-sync", "wrong-publication"):
            with self.subTest(fault=fault):
                self.check("before-unlink", fault)


class NativeCutTests(CutTests):
    def test_actual_cli_publication_and_all_six_cuts(self):
        for phase in PHASES:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                root.chmod(0o700)
                copies = root / "copies"
                copies.mkdir(mode=0o700)
                record = json.dumps({
                    "schema": "synveil.audit/v1", "component": "veil-core", "pid": 1,
                    "sequence": 1, "unix_time_ms": None, "version": "test",
                    "git_revision": None, "event": "fixture", "fields": {},
                }).encode() + b"\n"
                for path in (root / "journal", root / "journal.1"):
                    path.write_bytes(record)
                    path.chmod(0o600)
                snapshot = copies / "archive"
                subprocess.run([NATIVE, "export", "--path", str(root / "journal.1"),
                                "--output", str(snapshot)], check=True, capture_output=True, timeout=5)
                digest = hashlib.blake2b(record).hexdigest()
                process = subprocess.Popen([str(self.probe), phase, NATIVE, str(root), digest],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=True)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(5), "actual CLI checkpoint timeout")
                        output = os.read(process.stdout.fileno(), 4096)
                    self.assertEqual(output, b"\nSYNVEIL_RELEASE_GUEST_CUT=" + phase.encode() + b"\n")
                    self.assertIsNone(process.poll())
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                    process.stdout.close()
                    process.stderr.close()
                self.assertEqual((root / "journal.1").exists(), phase == "before-unlink")
                receipt = copies / "release.json"
                saved = receipt.read_bytes()
                intent, tail = saved.split(b"\n", 1)
                self.assertEqual(json.loads(intent)["digest"], digest)
                if phase.startswith("completion-"):
                    self.assertTrue(tail.endswith(b"\n"))
                    self.assertEqual(json.loads(tail)["released"], True)
                else:
                    self.assertEqual(tail, b"{" if phase == "partial-completion" else b"")
                # The tracer can be reaped just before its killed tracee closes
                # the inherited scope lock. Retry that contention only.
                for attempt in range(100):
                    result = subprocess.run([NATIVE, "inspect-release", "--path", str(root / "journal"),
                                             "--snapshot", str(snapshot), "--authorization", str(receipt),
                                             "--digest", digest], capture_output=True, timeout=5)
                    if result.returncode == 0 or b"WouldBlock" not in result.stderr or attempt == 99:
                        break
                    time.sleep(0.01)
                result.check_returncode()
                expected = ("completion-recorded" if phase.startswith("completion-") else
                            "incomplete-completion" if phase == "partial-completion" else "authorization-only")
                self.assertEqual(json.loads(result.stdout)["state"], expected)
                self.assertEqual(receipt.read_bytes(), saved)
                self.assertEqual(snapshot.read_bytes(), record)
                self.assertEqual((root / "journal").read_bytes(), record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-binary", type=Path)
    arguments = parser.parse_args()
    NATIVE = str(arguments.native_binary.resolve()) if arguments.native_binary else None
    suite = (unittest.TestSuite([NativeCutTests("test_actual_cli_publication_and_all_six_cuts")])
             if NATIVE else unittest.defaultTestLoader.loadTestsFromTestCase(CutTests))
    result = unittest.TextTestRunner().run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
