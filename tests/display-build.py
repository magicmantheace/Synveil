#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compile and exercise framebuffer layout checks on the Linux build host."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DisplaySourceTests(unittest.TestCase):
    def test_compiles_and_rejects_out_of_bounds_framebuffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-display"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "src/veil-display/main.c"), "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable), "--self-test"], check=True, timeout=3)


if __name__ == "__main__":
    unittest.main()
