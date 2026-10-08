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
    def run_smoke(self, console, native="1"):
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

    def test_invalid_mode_fails(self):
        self.assertNotEqual(self.run_smoke("SYNVEIL_BOOT_OK\n", "yes"), 0)


if __name__ == "__main__":
    unittest.main()
