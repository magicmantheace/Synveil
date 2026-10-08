#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test fixture staging and baseline isolation without rebuilding Linux."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guest_fixture", ROOT / "tests/build-guest-fixture.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class GuestFixtureTests(unittest.TestCase):
    def check(self, case, fail_probe=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            core = baseline / "usr/sbin/veil-core"
            core.parent.mkdir(parents=True)
            core.write_bytes(b"unchanged native core")
            out = root / "out"
            out.mkdir()
            original = out / "synveil-x86_64.img"
            original.write_bytes(b"unchanged native image")
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                if command[0].endswith("-gcc"):
                    if fail_probe:
                        raise subprocess.CalledProcessError(1, command)
                    Path(command[-1]).write_bytes(b"fixture probe")
                elif "cpio" in command[-1]:
                    tree = Path(kwargs["cwd"])
                    self.assertEqual((tree / "usr/sbin/veil-core").exists(), case != "absent")
                    self.assertEqual((tree / "usr/libexec/synveil-test/protocol-probe").exists(),
                                     case == "protocol")
                    kwargs["stdout"].write(b"fixture cpio")
                elif command[-1].endswith("kernel.sh"):
                    output = Path(kwargs["env"]["SYNVEIL_OUT_DIR"])
                    (output / "manifests").mkdir()
                    (output / "synveil-x86_64.efi").write_bytes(b"fixture kernel")
                elif command[-1].endswith("image.sh"):
                    output = Path(kwargs["env"]["SYNVEIL_OUT_DIR"])
                    (output / "synveil-x86_64.img").write_bytes(b"fixture disk")
                else:
                    self.assertIn("smoke-recovery.py", command[1])
                    self.assertEqual(command[command.index("--case") + 1], case)
                return subprocess.CompletedProcess(command, 0)

            env = {"SYNVEIL_ROOTFS_DIR": str(baseline), "SYNVEIL_WORK_DIR": str(root / "work"),
                   "SYNVEIL_OUT_DIR": str(out)}
            with patch.dict(os.environ, env), patch.object(sys, "argv", ["fixture", "--case", case]), \
                    patch.object(fixture.subprocess, "run", side_effect=run), \
                    patch.object(fixture.subprocess, "check_output", return_value="fixture-commit\n"):
                if fail_probe:
                    with self.assertRaises(subprocess.CalledProcessError):
                        fixture.main()
                else:
                    self.assertEqual(fixture.main(), 0)
                    data = json.loads((out / "guest-fixtures" / case / "manifests/fixture.json").read_text())
                    self.assertEqual(data["case"], case)
                    self.assertEqual(data["git_commit"], "fixture-commit")
                    self.assertEqual(len(data["artifacts"]), 3)
            self.assertEqual(core.read_bytes(), b"unchanged native core")
            self.assertEqual(original.read_bytes(), b"unchanged native image")
            self.assertFalse((baseline / "usr/libexec/synveil-test").exists())
            if fail_probe:
                self.assertEqual(len(calls), 1)

    def test_absent_fixture_preserves_baseline(self):
        self.check("absent")

    def test_protocol_fixture_preserves_baseline(self):
        self.check("protocol")

    def test_failed_probe_build_stops_before_packing(self):
        self.check("protocol", True)


if __name__ == "__main__":
    unittest.main()
