#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Cheap offline guards for future Wayland target library cross-stage."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wayland_cross", ROOT / "tools/native_wayland_target.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class WaylandTargetTests(unittest.TestCase):
    def test_dependency_pins_are_exactly_reviewed_pair(self):
        wp, ffi = bridge.prerequisites()
        self.assertEqual((wp["version"], ffi["version"]), ("1.26.0", "3.5.2"))

    def test_output_refuses_existing_and_symlinked_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(bridge, "ROOT", base), patch.object(
                    bridge, "OUT", base / "out/wayland"):
                bridge.root_bound_output()
                with self.assertRaises(ValueError):
                    bridge.root_bound_output()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "real").mkdir()
            (base / "out").symlink_to(base / "real")
            with patch.object(bridge, "ROOT", base), patch.object(
                    bridge, "OUT", base / "out/wayland"):
                with self.assertRaises(ValueError):
                    bridge.root_bound_output()

    def test_native_scanner_override_refuses_missing_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.assertRaises(ValueError):
                bridge.scanner_pc_override(base / "pc", base / "missing")

    def test_machine_files_never_reference_host_libffi(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cc = {name: base / "fake-cross" / name
                  for name in ("gcc", "ar")}
            cross, native = bridge.machine_files(
                base, cc, base / "target-sysroot",
                base / "private-ffi-pc", base / "private-scanner-pc")
            contents = cross.read_text()
            self.assertIn("pkg_config_libdir", contents)
            self.assertIn("private-ffi-pc", contents)
            self.assertIn("needs_exe_wrapper = true", contents)
            self.assertNotIn("/usr/lib/x86_64-linux-gnu/pkgconfig", contents)
            self.assertIn("private-scanner-pc", native.read_text())

    def test_ambiguously_named_libffi_pc_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a").mkdir()
            (base / "b").mkdir()
            (base / "a/libffi.pc").write_text("Name: fake")
            (base / "b/libffi.pc").write_text("Name: fake")
            with self.assertRaises(ValueError):
                bridge.find_one_pc(base, "libffi.pc")


if __name__ == "__main__":
    unittest.main()
