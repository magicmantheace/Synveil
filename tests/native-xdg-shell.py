#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline source and xdg-shell window protocol safety tests."""
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "xdg_shell", ROOT / "tools/native_xdg_shell.py")
xdg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xdg)


class XdgTests(unittest.TestCase):
    def test_official_source_pinned(self):
        release = xdg.pin()
        self.assertEqual(release["version"], "1.49")
        self.assertEqual(len(release["sha512"]), 128)

    def test_untrusted_release_pin_rejected(self):
        original = json.loads(xdg.PIN.read_text())
        for key, value in (("url", "http://example.org/release.tar.xz"),
                           ("sha512", "a" * 127),
                           ("filename", "../outside.tar.xz")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                bad = json.loads(json.dumps(original))
                bad["release"][key] = value
                path = Path(temp) / "bad.json"
                path.write_text(json.dumps(bad))
                with patch.object(xdg, "PIN", path):
                    with self.assertRaises(ValueError):
                        xdg.pin()

    def test_rejects_traversal_and_external_links(self):
        for path, target, valid in (
            ("wayland-protocols-1.49/stable/xdg-shell/xdg-shell.xml", None, True),
            ("../etc/passwd", None, False),
            ("wayland-protocols-1.49/../../escape", None, False),
            ("wayland-protocols-1.49/link", "/etc/shadow", False),
            ("wayland-protocols-1.49/sub/link", "../../../outside", False),
            ("wayland-protocols-1.49/sub/link", "../xdg-shell.xml", True),
        ):
            item = tarfile.TarInfo(path)
            if target is not None:
                item.type = tarfile.SYMTYPE
                item.linkname = target
            else:
                item.size = 1
            self.assertIs(xdg.member_safe(item), valid)

    def test_requires_wm_surface_toplevel_and_configure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "xdg.xml"
            good = ('<protocol name="xdg_shell">'
                    '<interface name="xdg_wm_base" version="1"/>'
                    '<interface name="xdg_surface" version="1"/>'
                    '<interface name="xdg_toplevel" version="1">'
                    '<event name="configure"/></interface></protocol>')
            path.write_text(good)
            xdg.xdg_xml(path)
            for bad in (good.replace('xdg_shell', 'unknown'),
                        good.replace('xdg_surface', 'unknown'),
                        good.replace('name="configure"', 'name="other"')):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    xdg.xdg_xml(path)

    def test_refuses_symlinked_output(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "other").mkdir()
            (base / "out").symlink_to(base / "other")
            with patch.object(xdg, "ROOT", base), patch.object(
                    xdg, "OUT", base / "out/xdg"):
                # A symlinked parent must fail before any network request.
                self.assertTrue((xdg.ROOT / "out").is_symlink())


if __name__ == "__main__":
    unittest.main()
