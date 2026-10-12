#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test Linux parent-death lifetime for the disposable demo terminal chain."""
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def alive(pid):
    try:
        # Defunct descendants cannot retain a running interactive shell.
        status = Path(f"/proc/{pid}/stat").read_text()
        state = status[status.rfind(")") + 2]
        return state != "Z"
    except FileNotFoundError:
        return False


class DemoTerminalLifetimeTests(unittest.TestCase):
    def test_parent_death_kills_execed_helper_and_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "desktop-lifetime"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-lifetime.c"), "-o", str(program)],
                check=True, timeout=15,
            )
            subprocess.run([str(program), "--wrong-parent"], check=True, timeout=3)
            parent = subprocess.Popen([str(program), "--chain"], stdout=subprocess.PIPE,
                                      start_new_session=True)
            leaf = None
            try:
                ready, _, _ = select.select([parent.stdout], [], [], 3)
                self.assertTrue(ready, "guarded descendant never initialized")
                line = parent.stdout.readline().decode().strip()
                self.assertRegex(line, r"^LEAF [0-9]+$")
                leaf = int(line.split()[1])
                self.assertTrue(alive(leaf))
                os.kill(parent.pid, signal.SIGKILL)
                parent.wait(timeout=3)
                deadline = time.monotonic() + 3
                while alive(leaf) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(alive(leaf), "orphaned demo shell survived display death")
            finally:
                try:
                    os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if leaf is not None and alive(leaf):
                    os.kill(leaf, signal.SIGKILL)
                parent.wait(timeout=3)
                parent.stdout.close()


if __name__ == "__main__":
    unittest.main()
