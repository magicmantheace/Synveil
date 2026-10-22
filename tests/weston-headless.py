#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host-only safety checks for Weston compositor preflight."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "weston_probe", HERE / "tools/weston_headless_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class WestonTests(unittest.TestCase):
    def test_requires_configured_toplevel(self):
        good = ("xdg_wm_base@9.get_xdg_surface(new id xdg_surface@12)\n"
                "xdg_surface@12.get_toplevel(new id xdg_toplevel@13)\n"
                "xdg_toplevel@13.configure(800, 600)\n")
        self.assertTrue(probe.has_toplevel(good))
        for bad in ("", "xdg_wm_base@9", good.replace("get_toplevel", "get_popup"),
                    good.replace(".configure(", ".pong(")):
            self.assertFalse(probe.has_toplevel(bad))

    def test_private_socket_parent_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private"
            path.mkdir(mode=0o700)
            self.assertTrue(probe.secure_runtime(path))
            path.chmod(0o755)
            self.assertFalse(probe.secure_runtime(path))
            path.chmod(0o700)
            link = Path(temp) / "link"
            link.symlink_to(path)
            self.assertFalse(probe.secure_runtime(link))

    def test_refuses_root(self):
        with patch.object(probe.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "root"):
                probe.probe(35)

    def test_bounded_timeout(self):
        with patch.object(probe.os, "geteuid", return_value=1000):
            for value in (-1, 1, 1000):
                with self.assertRaises(ValueError):
                    probe.probe(value)

    def test_headless_only(self):
        self.assertEqual(probe.CLIENTS, ("weston-terminal", "weston-simple-shm"))
        self.assertIn("--backend=headless", probe.OPTIONS)
        self.assertIn("--renderer=pixman", probe.OPTIONS)
        self.assertNotIn("--backend=drm", probe.OPTIONS)


if __name__ == "__main__":
    unittest.main()
