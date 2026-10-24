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

    def test_wayland_126_numeric_hash_object_ids_preserve_all_requirements(self):
        native = ("xdg_wm_base#6.get_xdg_surface(new id xdg_surface#9)\n"
                  "xdg_surface#9.get_toplevel(new id xdg_toplevel#10)\n"
                  "xdg_surface#9.configure(1)\nxdg_surface#9.ack_configure(1)\n"
                  "wl_surface#8.attach(wl_buffer#7,0,0)\nwl_surface#8.commit()\n"
                  "SYNVEIL_XDG_TOPLEVEL_SHM_SUBMITTED")
        self.assertTrue(mod.valid_trace(native))
        self.assertTrue(mod.valid_trace(native.replace("#", "@")))
        for missing in ("get_xdg_surface", "get_toplevel", ".configure(",
                        "ack_configure(", ".attach(", ".commit(",
                        "SYNVEIL_XDG_TOPLEVEL_SHM_SUBMITTED"):
            self.assertFalse(mod.valid_trace(native.replace(missing, "REMOVED")))
        self.assertFalse(mod.valid_trace(native.replace("xdg_wm_base#6", "xdg_wm_base#invalid")))

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
                [str(loader), "--inhibit-cache", "--library-path", str(lib), str(program)])
            with self.assertRaises(ValueError):
                mod.target_command(program, loader=base / "not-real",
                                   library_path=str(lib))
            with self.assertRaises(ValueError):
                mod.target_command(program, loader=loader,
                                   library_path=str(base / "no-libs"))

    def test_target_loader_must_not_use_host_cache_or_symlink_escape(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            for name in ("libc.so.6", "ld-linux-x86-64.so.2"):
                (target / name).write_bytes(b"target fixture")
            good = ("linux-vdso.so.1 (0x1234)\n"
                    f"libc.so.6 => {target}/libc.so.6 (0x2345)\n"
                    f"/lib64/ld-linux-x86-64.so.2 => {target}/ld-linux-x86-64.so.2 (0x3456)\n")
            mod.validate_target_libraries(good, str(target))
            host_lib = root / "host.so"
            host_lib.write_bytes(b"host fixture")
            for bad in (good.replace(str(target / "libc.so.6"), str(host_lib)),
                        good.replace("libc.so.6 =>", "other.so =>"),
                        good + "libm.so.6 => not found\n"):
                with self.assertRaises(ValueError):
                    mod.validate_target_libraries(bad, str(target))
            (target / "libc.so.6").unlink()
            (target / "libc.so.6").symlink_to(host_lib)
            with self.assertRaises(ValueError):
                mod.validate_target_libraries(good, str(target))

    def test_host_loader_injection_is_removed(self):
        env = {"PATH": "/bin", "LD_PRELOAD": "host.so", "LD_AUDIT": "audit.so",
               "LD_LIBRARY_PATH": "/host", "GLIBC_TUNABLES": "bad",
               "GCONV_PATH": "/host", "LOCPATH": "/host", "NLSPATH": "/host"}
        self.assertEqual(mod.clean_loader_environment(env), {"PATH": "/bin"})

    def test_nonroot_and_bounded_timeout(self):
        with patch.object(mod.os, "geteuid", return_value=0):
            with self.assertRaises(ValueError):
                mod.run(Path("none"))
        with patch.object(mod.os, "geteuid", return_value=1000):
            with self.assertRaises(ValueError):
                mod.run(Path("none"), 1)


if __name__ == "__main__":
    unittest.main()
