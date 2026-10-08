#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test linker separation and build audits without compiling a toolchain."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_build  # noqa: E402
from native_build import audit_elf, audit_map, build_environment  # noqa: E402
from native_rustc_wrapper import compiler_arguments  # noqa: E402


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.map = self.work / "link.map"
        self.env = {"SYNVEIL_NATIVE_GCC": "/synveil/bin/gcc", "SYNVEIL_NATIVE_MAP_DIR": str(self.work)}

    def test_host_linker(self):
        args = compiler_arguments(["--crate-name", "build_script_build", "--crate-type", "bin"], self.env)
        self.assertEqual(args[-2:], ["-C", "linker=cc"])
        self.assertFalse(any("-Map=" in arg for arg in args))

    def test_target_linker_and_map(self):
        args = compiler_arguments(["--crate-name", "veil_core", "--crate-type", "bin",
                                   "--target", "x86_64-unknown-linux-gnu", "-C", "linker=host-gcc"], self.env)
        self.assertIn("linker=/synveil/bin/gcc", args)
        self.assertEqual(args[-1], "link-arg=-Wl,-Map=" + str(self.work / "veil_core.map"))

    def test_target_library_no_link_map(self):
        args = compiler_arguments(["--crate-name", "std", "--crate-type", "rlib", "--target", "target"], self.env)
        self.assertEqual(args[-1], "linker=/synveil/bin/gcc")

    def test_rustc_queries_unchanged(self):
        self.assertEqual(compiler_arguments(["--print", "sysroot"], {}), ["--print", "sysroot"])

    def test_wrapper_executable_entrypoint(self):
        output = subprocess.check_output([str(ROOT / "tools/native_rustc_wrapper.py"),
                                          sys.executable, "-c", "print('WRAPPER_EXEC_OK')"], text=True)
        self.assertEqual(output.strip(), "WRAPPER_EXEC_OK")

    def test_environment_controls(self):
        inherited = {"RUSTFLAGS": "-L/host", "CARGO_ENCODED_RUSTFLAGS": "host", "RUSTC": "/host/rustc",
                     "LIBRARY_PATH": "/host/lib", "CPATH": "/host/include", "CARGO_HOME": "/cache/cargo",
                     "RUSTUP_HOME": "/cache/rustup"}
        env = build_environment(inherited, Path("/synveil/bin/gcc"), self.work, "target")
        for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "LIBRARY_PATH", "CPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["RUSTUP_HOME"], "/cache/rustup")
        self.assertEqual(env["CARGO_HOME"], "/cache/cargo")
        self.assertEqual(env["CARGO_PROFILE_RELEASE_PANIC"], "abort")
        self.assertIn("+crt-static", env["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS"])

    def write_map(self, extra=""):
        self.map.write_text("LOAD " + str(self.work / "libc.a") + "\nLOAD " +
                            str(self.work / "libstd-hash.rlib") + "\n" + extra)

    def test_source_build_inputs(self):
        self.write_map()
        self.assertEqual(len(audit_map(self.map, [self.work], ROOT)), 2)

    def test_host_input_rejected(self):
        self.write_map("LOAD /usr/lib/libgcc_s.so\n")
        with self.assertRaisesRegex(ValueError, "outside Synveil"):
            audit_map(self.map, [self.work], ROOT)

    def test_symlink_escape_rejected(self):
        (self.work / "escape.o").symlink_to(self.root / "host.o")
        self.write_map("LOAD " + str(self.work / "escape.o") + "\n")
        with self.assertRaisesRegex(ValueError, "outside Synveil"):
            audit_map(self.map, [self.work], ROOT)

    def test_empty_map_rejected(self):
        self.map.write_text("no LOAD inputs\n")
        with self.assertRaisesRegex(ValueError, "no LOAD entries"):
            audit_map(self.map, [self.work], ROOT)

    def test_missing_std_rejected(self):
        self.map.write_text("LOAD " + str(self.work / "libc.a") + "\n")
        with self.assertRaisesRegex(ValueError, "rebuilt Rust std"):
            audit_map(self.map, [self.work], ROOT)

    def test_static_elf(self):
        audit_elf("Class: ELF64\nMachine: Advanced Micro Devices X86-64", "LOAD", "no dynamic section")

    def test_shared_elf_rejected(self):
        for program, dynamic in (("INTERP", ""), ("LOAD", "(NEEDED) libc.so.6")):
            with self.assertRaisesRegex(ValueError, "shared runtime"):
                audit_elf("ELF64 X86-64", program, dynamic)

    def test_wrong_architecture_rejected(self):
        with self.assertRaisesRegex(ValueError, "not x86_64"):
            audit_elf("ELF64 AArch64", "LOAD", "")

    def invoke_builder(self, dynamic=""):
        toolchain = self.root / "toolchain"
        toolchain.mkdir()
        runtime = toolchain / "libgcc.a"
        runtime.write_bytes(b"source-built runtime fixture")
        evidence = {"target": {"compiler": str(toolchain / "target-gcc"), "libgcc": str(runtime)},
                    "rust": {"source_verification": {"sha256": "fixture"}}}
        argv = ["native_build.py", "--toolchain-dir", str(toolchain), "--sysroot", str(toolchain),
                "--target", "target", "--work-dir", str(self.work), "--out-dir", str(self.root / "out")]

        def fake_cargo(command, **kwargs):
            self.assertIn("--locked", command)
            self.assertIn("-Zbuild-std=std,panic_abort", command)
            self.assertEqual(kwargs["env"]["CARGO_PROFILE_RELEASE_PANIC"], "abort")
            cargo_dir = Path(command[command.index("--target-dir") + 1])
            target = command[command.index("--target") + 1]
            release = cargo_dir / target / "release"
            release.mkdir(parents=True)
            for name in ("veil-core", "synctl"):
                (release / name).write_bytes(b"fixture target executable")
                link_map = cargo_dir.parent / "maps" / (name.replace("-", "_") + ".map")
                link_map.write_text("LOAD " + str(toolchain / "libc.a") + "\nLOAD " +
                                    str(cargo_dir / "libstd-fixture.rlib") + "\n")

        def fake_run(*args):
            if "-h" in args:
                return "ELF64 X86-64"
            if "-l" in args:
                return "LOAD"
            if "-d" in args:
                return dynamic
            if "--porcelain" in args:
                return ""
            return "fixture-commit"

        with patch.object(sys, "argv", argv), patch.object(native_build.subprocess, "check_output", return_value=json.dumps(evidence)), \
                patch.object(native_build.subprocess, "run", side_effect=fake_cargo), patch.object(native_build, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return native_build.main()

    def test_builder_publishes_only_audited_artifacts(self):
        self.assertEqual(self.invoke_builder(), 0)
        manifest = json.loads((self.root / "out/native/build.json").read_text())
        self.assertEqual(set(manifest["artifacts"]), {"veil-core", "synctl"})
        self.assertEqual(manifest["schema"], "synveil.native-build/v1")

    def test_builder_failure_does_not_publish(self):
        self.assertEqual(self.invoke_builder("(NEEDED) libc.so.6"), 1)
        self.assertFalse((self.root / "out/native").exists())


if __name__ == "__main__":
    unittest.main()
