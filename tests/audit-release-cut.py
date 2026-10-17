#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compile the syscall tracer and verify real stopped-child boundaries."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
#define _GNU_SOURCE
#include <fcntl.h>
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
    if (write(receipt, "intent\n", 7) != 7 || fsync(receipt) ||
        linkat(receipt, "", copydir, "release.json", AT_EMPTY_PATH)) return 5;
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
                self.assertEqual(receipt, b"intent\n" + (b"c" if phase == "partial-completion" else b""))
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()

    def test_all_four_real_syscall_boundaries(self):
        for phase in ("before-unlink", "after-unlink", "unlink-synced", "partial-completion"):
            with self.subTest(phase=phase):
                self.check(phase)

    def test_failure_or_missing_authorization_sync_cannot_emit_checkpoint(self):
        for fault in ("early-exit", "missing-sync"):
            with self.subTest(fault=fault):
                self.check("before-unlink", fault)


if __name__ == "__main__":
    unittest.main()
