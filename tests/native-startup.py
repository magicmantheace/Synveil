#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Run the real startup helper with isolated fake binaries, without root/socket access."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = self.root / "core"
        self.ctl = self.root / "ctl"
        self.pid = self.root / "pid"
        script = (ROOT / "build/rootfs/start-core").read_text()
        script = script.replace("/usr/sbin/veil-core", str(self.core)).replace("/usr/bin/synctl", str(self.ctl))
        script = script.replace("/run/synveil/veil-core.sock", str(self.root / "core.sock"))
        self.script = self.root / "start-core"
        self.script.write_text(script)
        self.addCleanup(self.stop_core)

    def stop_core(self):
        if self.pid.exists():
            try:
                os.kill(int(self.pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def binary(self, path, code):
        path.write_text("#!/bin/sh\n" + code + "\n")
        path.chmod(0o755)

    def live_core(self):
        self.binary(self.core, f'exec >/dev/null 2>&1\necho $$ >"{self.pid}"\nexec sleep 30')

    def run_helper(self):
        result = subprocess.run(["sh", str(self.script)], capture_output=True, text=True, timeout=9)
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def test_missing_binaries(self):
        self.assertIn("SYNVEIL_CORE_UNAVAILABLE", self.run_helper())

    def test_not_executable(self):
        self.binary(self.core, "exit 0")
        self.core.chmod(0o644)
        self.binary(self.ctl, "exit 0")
        self.assertIn("SYNVEIL_CORE_UNAVAILABLE", self.run_helper())

    def test_core_exits_immediately(self):
        self.binary(self.core, "exit 7")
        self.binary(self.ctl, "exit 1")
        self.assertIn("SYNVEIL_CORE_UNAVAILABLE", self.run_helper())

    def test_ready_core(self):
        self.live_core()
        self.binary(self.ctl, "echo '{\"state\":\"ready\"}'")
        self.assertIn("SYNVEIL_CORE_READY", self.run_helper())

    def test_status_failure(self):
        self.live_core()
        self.binary(self.ctl, "exit 1")
        self.assertIn("SYNVEIL_CORE_UNAVAILABLE", self.run_helper())

    def test_hanging_status_is_bounded(self):
        self.live_core()
        self.binary(self.ctl, "exec sleep 30")
        self.assertIn("SYNVEIL_CORE_UNAVAILABLE", self.run_helper())


if __name__ == "__main__":
    unittest.main()
