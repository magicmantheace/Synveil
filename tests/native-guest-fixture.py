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
            seat = baseline / "usr/sbin/veil-seat"
            seat.write_bytes(b"unchanged native seat")
            display = baseline / "usr/bin/synveil-display"
            display.parent.mkdir(parents=True)
            display.write_bytes(b"unchanged native display")
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
                    self.assertEqual((tree / "usr/sbin/veil-core").exists(),
                                     case not in ("absent", "recovery-status"))
                    self.assertEqual((tree / "usr/sbin/veil-seat").exists(),
                                     case != "recovery-status")
                    self.assertEqual((tree / "usr/bin/synveil-display").exists(),
                                     case != "recovery-status")
                    self.assertEqual((tree / "usr/libexec/synveil-test/protocol-probe").exists(),
                                     case == "protocol")
                    self.assertEqual((tree / "usr/libexec/synveil-test/audit-release-cut").exists(),
                                     case == "audit-release-crash")
                    self.assertEqual((tree / "etc/synveil/auth-desktop").exists(),
                                     case in ("graphical-login", "graphical-locked", "graphical-onboard",
                                              "recovery-status"))
                    self.assertEqual((tree / "etc/synveil/developer-serial-recovery").exists(),
                                     case == "graphical-login")
                    kwargs["stdout"].write(b"fixture cpio")
                elif command[-1].endswith("kernel.sh"):
                    self.assertEqual(kwargs["env"].get("SYNVEIL_KERNEL_TEST_RECOVERY_STATUS"),
                                     "1" if case == "recovery-status" else None)
                    self.assertEqual(kwargs["env"]["SYNVEIL_KERNEL_SEED_WORK"],
                                     str(root / "work"))
                    self.assertNotEqual(kwargs["env"]["SYNVEIL_WORK_DIR"],
                                        kwargs["env"]["SYNVEIL_KERNEL_SEED_WORK"])
                    output = Path(kwargs["env"]["SYNVEIL_OUT_DIR"])
                    (output / "manifests").mkdir()
                    (output / "synveil-x86_64.efi").write_bytes(b"fixture kernel")
                elif command[-1].endswith("image.sh"):
                    output = Path(kwargs["env"]["SYNVEIL_OUT_DIR"])
                    (output / "synveil-x86_64.img").write_bytes(b"fixture disk")
                else:
                    if case == "audit-release-crash":
                        self.assertIn("smoke-audit-release-crash.py", command[1])
                    elif case == "graphical-login":
                        self.assertIn("smoke-graphical-login.py", command[1])
                    elif case == "graphical-onboard":
                        self.assertIn("smoke-graphical-onboard.py", command[1])
                        self.assertEqual(kwargs["env"]["SYNVEIL_QEMU_VIDEO"], "1")
                        self.assertIn("SYNVEIL_QEMU_STATE_DISK", kwargs["env"])
                    elif case == "graphical-locked":
                        self.assertIn("smoke-serial-locked.py", command[1])
                    elif case == "recovery-status":
                        self.assertIn("smoke-serial-locked.py", command[1])
                        self.assertIn("--recovery-status", command)
                    else:
                        self.assertIn("smoke-recovery.py", command[1])
                        self.assertEqual(command[command.index("--case") + 1], case)
                return subprocess.CompletedProcess(command, 0)

            env = {"SYNVEIL_ROOTFS_DIR": str(baseline), "SYNVEIL_WORK_DIR": str(root / "work"),
                   "SYNVEIL_OUT_DIR": str(out)}
            with patch.dict(os.environ, env), patch.object(sys, "argv", ["fixture", "--case", case]), \
                    patch.object(fixture, "create_state_disk",
                                 side_effect=lambda path, size: path.write_bytes(b"synthetic state")), \
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
                    proof_path = out / "guest-fixtures" / case / "manifests/validation.json"
                    if case == "audit-release-crash":
                        proof = json.loads(proof_path.read_text())
                        self.assertEqual(proof["independent_qemu_boots"], 12)
                        self.assertEqual(len(proof["release_cuts"]), 6)
                        self.assertTrue(proof["host_page_cache_survives"])
                        self.assertFalse(proof["physical_power_loss"])
                    elif case == "graphical-onboard":
                        proof = json.loads(proof_path.read_text())
                        self.assertEqual(proof["result"], "passed")
                        self.assertEqual(proof["independent_qemu_boots"], 2)
                        self.assertTrue(proof["locked_serial"])
                    else:
                        self.assertFalse(proof_path.exists())
            self.assertEqual(core.read_bytes(), b"unchanged native core")
            self.assertEqual(seat.read_bytes(), b"unchanged native seat")
            self.assertEqual(display.read_bytes(), b"unchanged native display")
            self.assertEqual(original.read_bytes(), b"unchanged native image")
            self.assertFalse((baseline / "usr/libexec/synveil-test").exists())
            self.assertFalse((baseline / "etc/synveil/auth-desktop").exists())
            self.assertFalse((baseline / "etc/synveil/developer-serial-recovery").exists())
            if fail_probe:
                self.assertEqual(len(calls), 1)

    def test_recovery_status_runs_without_core_seat_or_display(self):
        self.check("recovery-status")

    def test_locked_preview_does_not_stage_developer_serial_override(self):
        self.check("graphical-locked")

    def test_graphical_onboarding_never_stages_root_serial_recovery(self):
        self.check("graphical-onboard")

    def test_graphical_login_fixture_is_isolated_from_regular_vm(self):
        self.check("graphical-login")

    def test_absent_fixture_preserves_baseline(self):
        self.check("absent")

    def test_release_cut_fixture_never_stages_tracer_in_baseline(self):
        self.check("audit-release-crash")

    def test_protocol_fixture_preserves_baseline(self):
        self.check("protocol")

    def test_failed_probe_build_stops_before_packing(self):
        self.check("protocol", True)


if __name__ == "__main__":
    unittest.main()
