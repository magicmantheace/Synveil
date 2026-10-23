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

    def test_resize_requires_changed_size_then_restored_buffer(self):
        base = "\n".join(mod.NEEDED)
        cycle = ("\nSYNVEIL_XDG_SHM_SIZE 400 240\n"
                 "SYNVEIL_XDG_SHM_SIZE 1024 736\n"
                 "SYNVEIL_XDG_SHM_SIZE 400 240\n"
                 "SYNVEIL_XDG_RESIZE_CYCLE_COMPLETE\n"
                 ".set_maximized()\n.unset_maximized()")
        self.assertTrue(mod.valid_trace(base + cycle, exercise_resize=True))
        for bad in (cycle.replace("1024 736", "400 240"),
                    cycle.replace("SYNVEIL_XDG_RESIZE_CYCLE_COMPLETE", ""),
                    cycle.replace(".unset_maximized()", ""),
                    cycle.replace("SYNVEIL_XDG_SHM_SIZE 400 240\n", "")):
            self.assertFalse(mod.valid_trace(base + bad, exercise_resize=True))

    def test_glibc_loader_is_explicit_and_cannot_fallback(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            base = Path(root)
            program = base / "window"
            loader = base / "ld-linux-x86-64.so.2"
            lib = base / "target-lib64"
            program.write_bytes(b"fake")
            loader.write_bytes(b"fake")
            lib.mkdir()
            self.assertEqual(mod.target_command(program), [str(program)])
            with self.assertRaises(ValueError):
                mod.target_command(program, loader=loader)
            with self.assertRaises(ValueError):
                mod.target_command(program, library_path=str(lib))
            self.assertEqual(
                mod.target_command(program, loader=loader, library_path=str(lib)),
                [str(loader), "--library-path", str(lib), str(program)])
            with self.assertRaises(ValueError):
                mod.target_command(program, loader=base / "not-real",
                                   library_path=str(lib))
            with self.assertRaises(ValueError):
                mod.target_command(program, loader=loader,
                                   library_path=str(base / "no-libs"))

    def test_nonroot_and_bounded_timeout(self):
        with patch.object(mod.os, "geteuid", return_value=0):
            with self.assertRaises(ValueError):
                mod.run(Path("none"))
        with patch.object(mod.os, "geteuid", return_value=1000):
            with self.assertRaises(ValueError):
                mod.run(Path("none"), 1)


if __name__ == "__main__":
    unittest.main()
