#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host fixtures for embedded-initramfs security marker inspection."""
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from initramfs_profile import inspect_authenticated_archive, inspect_demo_archive, scan_names  # noqa: E402

REQUIRED = (
    "./init", "./usr/bin/synveil-display", "./usr/bin/synveil-vt",
    "./etc/synveil/desktop-autostart",
)


def newc(names, overrides=None):
    data = bytearray()
    overrides = overrides or {}
    executable = {"init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
                  "usr/sbin/veil-enroll", "usr/sbin/veil-seat",
                  "usr/libexec/synveil/recovery-status"}
    for name in (*names, "TRAILER!!!"):
        raw = name.encode("utf-8") + b"\0"
        normalized = name.removeprefix("./")
        default_mode = 0o100755 if normalized in executable else 0o100644
        fields = (1, overrides.get(normalized, default_mode), 0, 0, 1, 0, 0, 0, 0, 0, 0, len(raw), 0)
        data.extend(b"070701")
        data.extend(b"".join(f"{field:08x}".encode() for field in fields))
        data.extend(raw)
        data.extend(b"\0" * ((-len(data)) % 4))
    data.extend(b"\0" * ((-len(data)) % 512))
    return bytes(data)


class InitramfsProfileTests(unittest.TestCase):
    def check(self, raw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "initramfs.cpio"
            path.write_bytes(raw)
            return inspect_demo_archive(path)

    def test_clean_dev_initramfs_is_accepted(self):
        self.assertIn("init", self.check(newc(REQUIRED)))
        self.assertIn("usr/bin/synveil-vt", self.check(newc(REQUIRED)))

    def test_authenticated_profile_requires_locked_root_and_regular_markers(self):
        mandatory = (*REQUIRED, "./etc/synveil/auth-desktop",
                     "./usr/sbin/veil-enroll", "./usr/sbin/veil-seat",
                     "./usr/libexec/synveil/recovery-status")
        self.assertIn("etc/synveil/auth-desktop",
                      inspect_authenticated_archive(self._archive(newc(mandatory))))
        for forbidden in ("./etc/synveil/developer-serial-recovery",):
            with self.assertRaisesRegex(ValueError, "root serial"):
                inspect_authenticated_archive(
                    self._archive(newc((*mandatory, forbidden))))
        for deleted in ("./usr/sbin/veil-enroll", "./usr/sbin/veil-seat",
                        "./etc/synveil/auth-desktop",
                        "./usr/libexec/synveil/recovery-status"):
            with self.assertRaises(ValueError):
                inspect_authenticated_archive(
                    self._archive(newc(tuple(x for x in mandatory if x != deleted))))

    def _archive(self, raw):
        # Used only inside this test's temporary fixture lifetime; its
        # caller immediately scans the returned path before it is reused.
        if not hasattr(self, "_local_temp"):
            self._local_temp = tempfile.TemporaryDirectory()
            self.addCleanup(self._local_temp.cleanup)
        path = Path(self._local_temp.name) / "auth.cpio"
        path.write_bytes(raw)
        return path

    def test_rejects_unsafe_authenticated_executables_and_parents(self):
        mandatory = (*REQUIRED, "./etc/synveil/auth-desktop",
                     "./usr/sbin/veil-enroll", "./usr/sbin/veil-seat",
                     "./usr/libexec/synveil/recovery-status")
        for pathname, mode in (
            ("usr/sbin/veil-seat", 0o120777),  # symlink
            ("usr/sbin/veil-enroll", 0o100644),  # not executable
            ("usr/bin/synveil-display", 0o100777),  # world-writable
            ("usr/libexec/synveil/recovery-status", 0o120777),
            ("usr/libexec/synveil/recovery-status", 0o100644),
        ):
            with self.subTest(pathname=pathname, mode=mode):
                with self.assertRaisesRegex(ValueError, "unsafe embedded executable"):
                    inspect_authenticated_archive(
                        self._archive(newc(mandatory, {pathname: mode})))
        with self.assertRaisesRegex(ValueError, "unsafe embedded executable parent"):
            inspect_authenticated_archive(
                self._archive(newc((*mandatory, "./usr"), {"usr": 0o120777})))

    def test_rejects_declared_payload_beyond_archive_length(self):
        archive = bytearray(newc(REQUIRED))
        # The first newc header stores its file size at offset 54.
        archive[54:62] = b"00010000"
        with self.assertRaisesRegex(ValueError, "truncated newc payload"):
            self.check(bytes(archive))

    def test_rejects_auth_and_developer_root_override_markers(self):
        for forbidden in ("etc/synveil/auth-desktop",
                          "etc/synveil/developer-serial-recovery"):
            with self.assertRaisesRegex(ValueError, "embedded initramfs"):
                self.check(newc((*REQUIRED, "./" + forbidden)))

    def test_rejects_missing_components_and_duplicate_entries(self):
        for raw in (newc(REQUIRED[:-1]),
                    newc((*REQUIRED, "init")),
                    newc((*REQUIRED, "../etc/synveil/auth-desktop"))):
            with self.assertRaises(ValueError):
                self.check(raw)

    def test_rejects_invalid_and_truncated_headers(self):
        blob = newc(REQUIRED)
        for mutated in (
            b"",
            b"BADMAGIC" + blob[8:],
            blob[:106],
            blob[:-512],
            blob + b"not zero padding",
        ):
            with self.assertRaises(ValueError):
                self.check(mutated)

    def test_rejects_aliases_and_unsafe_cpio_root(self):
        for alias in ("usr/./bin/synveil-vt",
                      "usr//sbin/veil-seat",
                      "usr/libexec/../libexec/synveil/recovery-status",
                      "etc/synveil/auth-desktop/"):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(ValueError, "unsafe initramfs path alias"):
                    self.check(newc((*REQUIRED, alias)))
        self.assertIn(".", self.check(newc((".", *REQUIRED),
                                            {".": 0o040755})))
        with self.assertRaisesRegex(ValueError, "unsafe initramfs root directory"):
            self.check(newc((".", *REQUIRED), {".": 0o040777}))

    def test_scanner_does_not_open_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cpio"
            path.write_bytes(newc(("init", "./anyordinary-file")))
            self.assertEqual(scan_names(path), {"init", "anyordinary-file"})


if __name__ == "__main__":
    unittest.main()
