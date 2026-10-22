#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compile and exercise framebuffer layout checks on the Linux build host."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DisplaySourceTests(unittest.TestCase):
    def test_login_grab_waits_for_releases_and_rechecks_input_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "login-keyboard-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-keyboard-grab.c"), "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_versioned_preferences_are_private_atomic_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "desktop-preferences-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "tests/display-preferences.c"),
                 str(ROOT / "src/veil-display/preferences.c"),
                 "-I", str(ROOT / "src/veil-display"), "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_authenticated_home_browser_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "files-browser-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-files.c"),
                 str(ROOT / "src/veil-display/files.c"), "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_authenticated_dashboard_disables_same_uid_ptrace(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-seat-privacy-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-seat-privacy.c"), "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_user_terminal_cannot_inherit_framebuffer_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-mapping-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-mapping.c"), "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_developer_virtual_terminal_requires_explicit_manual_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-vt"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "src/veil-display/vt.c"), "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable), "--self-test"], check=True, timeout=3)
            # Neither a bare shell request nor an unconfirmed request is valid.
            result = subprocess.run([str(executable), "--developer-terminal"],
                                    capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)

    def test_input_device_classifier_and_restricted_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-input"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "src/veil-display/input.c"), "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable), "--self-test"], check=True, timeout=3)

    def test_first_user_enrollment_helpers_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "display-enroll-test"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(ROOT / "src/veil-display"),
                 str(ROOT / "tests/display-enroll.c"),
                 str(ROOT / "src/veil-display/enroll.c"),
                 "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable)], check=True, timeout=3)

    def test_read_only_psi_status_parsing_and_missing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            executable = folder / "psi-test"
            sample = folder / "cpu.pressure"
            sample.write_text(
                "some avg10=1.25 avg60=0.50 avg300=0.02 total=12345\n"
            )
            symlink = folder / "link"
            symlink.symlink_to(sample)
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "src/veil-display/pressure_diag.c"),
                 str(ROOT / "tests/display-pressure.c"),
                 "-I", str(ROOT / "src/veil-display"),
                 "-o", str(executable)],
                check=True, timeout=15,
            )
            subprocess.run([str(executable), str(sample), str(symlink)],
                           check=True, timeout=3)

    def test_compiles_and_rejects_out_of_bounds_framebuffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "synveil-display"
            subprocess.run(
                ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(ROOT / "src/veil-display/main.c"),
                 str(ROOT / "src/veil-display/preferences.c"),
                 str(ROOT / "src/veil-display/core_health.c"),
                 str(ROOT / "src/veil-display/network_diag.c"),
                 str(ROOT / "src/veil-display/pressure_diag.c"),
                 str(ROOT / "src/veil-display/enroll.c"),
                 str(ROOT / "src/veil-display/files.c"),
                 "-o", str(executable)],
                check=True,
            )
            subprocess.run([str(executable), "--self-test"], check=True, timeout=3)
            # Even when the host runs as UID 1000, an unleased process
            # with no inherited framebuffer must not start a user desktop.
            denied = subprocess.run([str(executable), "--session"],
                                    close_fds=True, capture_output=True, timeout=3)
            self.assertNotEqual(denied.returncode, 0)


if __name__ == "__main__":
    unittest.main()
