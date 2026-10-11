#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise smoke verdicts with an isolated console-producing QEMU fixture."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeSmokeTests(unittest.TestCase):
    def run_smoke(self, console, native="1", display="0", input_required="0", network="0", vt="0", desktop="0"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "build/scripts").mkdir(parents=True)
            for name in ("tests/smoke-boot.sh", "build/scripts/common.sh"):
                shutil.copyfile(ROOT / name, root / name)
            (root / "build/scripts/qemu.sh").write_text(
                '#!/usr/bin/env bash\nprintf "%s" "$FIXTURE_CONSOLE"\nexit 124\n'
            )
            result = subprocess.run(
                ["bash", str(root / "tests/smoke-boot.sh")],
                env={**os.environ, "SYNVEIL_OUT_DIR": str(root / "out"),
                     "SYNVEIL_SMOKE_REQUIRE_NATIVE": native,
                     "SYNVEIL_SMOKE_REQUIRE_DISPLAY": display,
                     "SYNVEIL_SMOKE_REQUIRE_INPUT": input_required,
                     "SYNVEIL_SMOKE_REQUIRE_NETWORK": network,
                     "SYNVEIL_SMOKE_REQUIRE_VT": vt,
                     "SYNVEIL_SMOKE_REQUIRE_DESKTOP": desktop,
                     "FIXTURE_CONSOLE": console},
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode

    def test_native_status_and_boot_pass(self):
        self.assertEqual(self.run_smoke("SYNVEIL_CORE_READY\nSYNVEIL_BOOT_OK\n"), 0)

    def test_serial_crlf_passes(self):
        self.assertEqual(self.run_smoke("SYNVEIL_CORE_READY\r\nSYNVEIL_BOOT_OK\r\n"), 0)

    def test_boot_without_status_fails(self):
        self.assertNotEqual(self.run_smoke("SYNVEIL_BOOT_OK\n"), 0)

    def test_status_without_boot_fails(self):
        self.assertNotEqual(self.run_smoke("SYNVEIL_CORE_READY\n"), 0)

    def test_unavailable_overrides_ready(self):
        self.assertNotEqual(self.run_smoke(
            "SYNVEIL_CORE_READY\nSYNVEIL_CORE_UNAVAILABLE\nSYNVEIL_BOOT_OK\n"), 0)

    def test_embedded_marker_fails(self):
        self.assertNotEqual(self.run_smoke(
            "echo SYNVEIL_CORE_READY\nSYNVEIL_BOOT_OK\n"), 0)

    def test_baseline_boot_does_not_require_native(self):
        self.assertEqual(self.run_smoke("SYNVEIL_BOOT_OK\n", "0"), 0)

    def test_graphics_input_probe_requires_both_devices(self):
        console = "SYNVEIL_CORE_READY\nSYNVEIL_DISPLAY_READY\nSYNVEIL_BOOT_OK\n"
        self.assertNotEqual(self.run_smoke(console, display="1", input_required="1"), 0)
        self.assertEqual(self.run_smoke(
            console + "SYNVEIL_INPUT_READY\n", display="1", input_required="1"), 0)
        self.assertNotEqual(self.run_smoke(
            console + "SYNVEIL_INPUT_UNAVAILABLE\n", display="1", input_required="1"), 0)

    def test_invalid_input_requirement_fails(self):
        self.assertNotEqual(self.run_smoke("SYNVEIL_BOOT_OK\n", input_required="yes"), 0)

    def test_network_marker_requires_opt_in(self):
        console = "SYNVEIL_CORE_READY\nSYNVEIL_BOOT_OK\n"
        self.assertEqual(self.run_smoke(console), 0)
        self.assertNotEqual(self.run_smoke(console, network="1"), 0)
        self.assertEqual(self.run_smoke(
            console + "SYNVEIL_NETWORK_READY\n", network="1"), 0)
        self.assertNotEqual(self.run_smoke(
            console + "SYNVEIL_NETWORK_UNAVAILABLE\n", network="1"), 0)

    def test_virtual_terminal_requires_guest_presence(self):
        console = "SYNVEIL_CORE_READY\nSYNVEIL_BOOT_OK\n"
        self.assertNotEqual(self.run_smoke(console, vt="1"), 0)
        self.assertEqual(self.run_smoke(console + "SYNVEIL_VT_READY\n", vt="1"), 0)
        self.assertNotEqual(self.run_smoke(console + "SYNVEIL_VT_UNAVAILABLE\n", vt="1"), 0)

    def test_desktop_marker_is_required_when_enabled(self):
        console = "SYNVEIL_CORE_READY\nSYNVEIL_BOOT_OK\n"
        self.assertNotEqual(self.run_smoke(console, desktop="1"), 0)
        self.assertEqual(self.run_smoke(
            console + "SYNVEIL_DESKTOP_READY\n", desktop="1"), 0)
        self.assertNotEqual(self.run_smoke(
            console + "echo SYNVEIL_DESKTOP_READY\n", desktop="1"), 0)

    def test_invalid_mode_fails(self):
        self.assertNotEqual(self.run_smoke("SYNVEIL_BOOT_OK\n", "yes"), 0)


if __name__ == "__main__":
    unittest.main()
