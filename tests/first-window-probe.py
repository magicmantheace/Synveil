#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("window_probe", ROOT / "tools/first_window_probe.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def test_protocol_must_configure_and_attach_real_buffer(self):
        s = ("xdg_wm_base@4.get_xdg_surface(new id xdg_surface@7)\n"
             "xdg_surface@7.get_toplevel(new id xdg_toplevel@9)\n"
             "xdg_surface@7.configure(1)\nxdg_surface@7.ack_configure(1)\n"
             "wl_surface@5.attach(wl_buffer@11,0,0)\nwl_surface@5.commit()\n"
             "SYNVEIL_XDG_TOPLEVEL_SHM_SUBMITTED")
        self.assertTrue(mod.valid_trace(s))
        for piece in mod.NEEDED:
            self.assertFalse(mod.valid_trace(s.replace(piece, "REMOVED")))

    def test_nonroot_and_bounded_timeout(self):
        with patch.object(mod.os, "geteuid", return_value=0):
            with self.assertRaises(ValueError):
                mod.run(Path("none"))
        with patch.object(mod.os, "geteuid", return_value=1000):
            with self.assertRaises(ValueError):
                mod.run(Path("none"), 1)


if __name__ == "__main__":
    unittest.main()
