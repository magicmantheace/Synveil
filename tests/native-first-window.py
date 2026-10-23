#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Reject host or tampered artifacts when linking the first native app."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_first_window", ROOT / "tools/native_first_window.py")
win = importlib.util.module_from_spec(spec)
spec.loader.exec_module(win)


class NativeWindowTests(unittest.TestCase):
    def test_pinned_source_and_protocol_identity(self):
        self.assertEqual(win.wt.prerequisites()[0]["version"], "1.26.0")
        self.assertEqual(win.xdg.pin()["version"], "1.49")
        self.assertIn("wl_surface_attach", win.WINDOW.read_text())
        self.assertIn("xdg_surface_ack_configure", win.WINDOW.read_text())

    def test_absent_target_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(win, "STAGE", Path(folder)):
                with self.assertRaises(ValueError):
                    win.validated_inputs()

    def test_stale_or_host_target_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            r = root / "validation.json"
            for identity in ("x86_64-linux-gnu", win.TARGET):
                r.write_text(json.dumps({
                    "result": "native-wayland-client-server-staged",
                    "target": identity,
                    "wayland_source_sha512": "fake",
                    "ffi_source_sha256": "fake",
                    "native_boot_image_changed": False
                }))
                with patch.object(win, "STAGE", root):
                    with self.assertRaises(ValueError):
                        win.validated_inputs()

    def test_missing_target_headers_and_source_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                win.checked_file(root / "missing", description="test")
            (root / "file").write_text("nonempty")
            self.assertEqual(win.checked_file(
                root / "file", description="test"), root / "file")
            (root / "link").symlink_to(root / "file")
            with self.assertRaises(ValueError):
                win.checked_file(root / "link", description="test")

    def test_root_cannot_link(self):
        with patch.object(win.os, "geteuid", return_value=0):
            with self.assertRaises(ValueError):
                win.cross_link()


if __name__ == "__main__":
    unittest.main()
