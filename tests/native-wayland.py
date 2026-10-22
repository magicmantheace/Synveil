#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline safety checks for isolated Wayland host scanner prerequisite."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_wayland", ROOT / "tools/native_wayland.py")
wayland = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wayland)


class WaylandSourceTests(unittest.TestCase):
    def test_pin_exact_official_release(self):
        pin = wayland.release_spec()
        self.assertEqual(pin["version"], "1.26.0")
        self.assertEqual(len(pin["sha512"]), 128)

    def test_modified_upstream_refusal(self):
        good = json.loads(wayland.LOCK.read_text())
        for field, value in (("sha512", "0" * 127),
                             ("url", "http://gitlab.freedesktop.org/wayland/wayland"),
                             ("filename", "../../other.tar.xz")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                changed = json.loads(json.dumps(good))
                changed["wayland"][field] = value
                lock = Path(tmp) / "lock.json"
                lock.write_text(json.dumps(changed))
                with patch.object(wayland, "LOCK", lock):
                    with self.assertRaises(ValueError):
                        wayland.release_spec()

    def test_symlink_and_parent_escape_refusal(self):
        for name, link, safe in (
            ("wayland-1.26.0/git-blame-ignore-revs", ".git-blame-ignore-revs", True),
            ("wayland-1.26.0/link", "/etc/passwd", False),
            ("wayland-1.26.0/sub/link", "../../../../etc/passwd", False),
            ("../outside", "wayland-1.26.0/file", False),
        ):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = link
                self.assertIs(wayland.archive_member_is_safe(member), safe)

    def test_unsafe_source_archive_is_never_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / "bad.tar.xz"
            with tarfile.open(archive, "w:xz") as tar:
                member = tarfile.TarInfo("wayland-1.26.0/../../escape")
                member.size = 1
                tar.addfile(member, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                wayland.extract_source(archive, base / "staged")
            self.assertFalse((base / "escape").exists())

    def test_scanner_protocol_requires_real_core_interfaces(self):
        valid = (b"/* synthetic header */\n"
                 b"struct wl_interface;\n"
                 b"extern const struct wl_interface wl_display_interface;\n"
                 b"extern const struct wl_interface wl_registry_interface;\n"
                 b"extern const struct wl_interface wl_compositor_interface;\n")
        padding = b"/* comment */\n" * 450
        self.assertTrue(wayland.generated_protocol_is_valid(
            valid + padding, kind="client"))
        self.assertTrue(wayland.generated_protocol_is_valid(
            valid + padding, kind="server"))
        self.assertTrue(wayland.generated_protocol_is_valid(
            valid + padding, kind="code"))
        for bad in (b"", valid, (valid + padding).replace(
                b"wl_registry_interface", b"wl_registry_interface_missing")):
            # The short payload and missing interface cannot establish code.
            if bad == valid + padding:
                continue
            if bad == valid:
                self.assertFalse(wayland.generated_protocol_is_valid(
                    bad, kind="client"))
            elif not bad:
                self.assertFalse(wayland.generated_protocol_is_valid(
                    bad, kind="code"))
        self.assertFalse(wayland.generated_protocol_is_valid(
            (valid + padding).replace(
                b"wl_registry_interface;", b"registry_dummy;"),
            kind="client"))

    def test_existing_and_symlinked_destination_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(wayland, "ROOT", base), patch.object(
                wayland, "DEST", base / "out/stage"):
                created = wayland.create_output()
                self.assertEqual(created.stat().st_mode & 0o777, 0o700)
                with self.assertRaises(ValueError):
                    wayland.create_output()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "actual").mkdir()
            (base / "out").symlink_to(base / "actual")
            with patch.object(wayland, "ROOT", base), patch.object(
                wayland, "DEST", base / "out/stage"):
                with self.assertRaises(ValueError):
                    wayland.create_output()


if __name__ == "__main__":
    unittest.main()
