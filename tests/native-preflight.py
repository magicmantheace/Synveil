#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise prerequisite rejection without a compiler install or downloads."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from native_preflight import verify_c_toolchain, verify_rust  # noqa: E402


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pin = json.loads((ROOT / "build/manifests/rust-bootstrap.json").read_text())
        self.toolchain = self.root / "toolchain"
        self.sysroot = self.toolchain / "sysroot"
        self.runtime = self.toolchain / "lib/libgcc.a"
        for p in [self.toolchain / "bin" / (self.pin["c_target"] + "-gcc"), self.runtime]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        for name in ("usr/lib64/crt1.o", "usr/lib64/Scrt1.o", "usr/lib64/crti.o",
                     "usr/lib64/crtn.o", "usr/lib64/libc.a", "usr/include/features.h"):
            p = self.sysroot / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        self.outputs = {"-dumpmachine": self.pin["c_target"],
                        "-print-sysroot": str(self.sysroot),
                        "-print-libgcc-file-name": str(self.runtime)}
        self.rustroot = self.root / "rust"
        self.manifest = self.rustroot / "lib/rustlib/multirust-channel-manifest.toml"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text('[pkg.rust-src.target."*"]\nxz_hash = "' +
                                 self.pin["rust_src"]["sha256"] + '"\nxz_url = "' +
                                 self.pin["rust_src"]["url"] + '"\n')
        for name in ("Cargo.toml", "core/Cargo.toml", "alloc/Cargo.toml", "std/Cargo.toml"):
            p = self.rustroot / "lib/rustlib/src/rust/library" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        self.identity = ("release: " + self.pin["rustc_release"] + "\ncommit-hash: " +
                         self.pin["rustc_commit_prefix"] + "123\nhost: " + self.pin["host"])

    def check_c(self):
        return verify_c_toolchain(self.toolchain, self.sysroot, self.pin["c_target"],
                                 self.pin, runner=lambda *args: self.outputs[args[-1]])

    def check_rust(self):
        return verify_rust(self.identity, self.rustroot, self.pin)

    def test_isolated_toolchain(self):
        self.assertEqual(self.check_c()["sysroot"], str(self.sysroot))

    def test_wrong_gcc_target(self):
        self.outputs["-dumpmachine"] = "x86_64-linux-gnu"
        with self.assertRaisesRegex(ValueError, "GCC target"):
            self.check_c()

    def test_host_sysroot_rejected(self):
        self.outputs["-print-sysroot"] = "/"
        with self.assertRaisesRegex(ValueError, "sysroot differs"):
            self.check_c()

    def test_missing_libc(self):
        (self.sysroot / "usr/lib64/libc.a").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete target glibc"):
            self.check_c()

    def test_host_runtime_symlink_rejected(self):
        host = self.root / "host-libgcc.a"
        host.touch()
        self.runtime.unlink()
        self.runtime.symlink_to(host)
        with self.assertRaisesRegex(ValueError, "outside the Synveil toolchain"):
            self.check_c()

    def test_matching_rust_metadata(self):
        self.assertEqual(self.check_rust()["compiler"]["release"], self.pin["rustc_release"])

    def test_wrong_rust_release(self):
        self.identity = self.identity.replace(self.pin["rustc_release"], "1.99.0")
        with self.assertRaisesRegex(ValueError, "Rust release"):
            self.check_rust()

    def test_wrong_rust_commit(self):
        self.identity = self.identity.replace(self.pin["rustc_commit_prefix"], "deadbeef")
        with self.assertRaisesRegex(ValueError, "compiler commit"):
            self.check_rust()

    def test_source_pin_mismatch(self):
        self.manifest.write_text(self.manifest.read_text().replace(self.pin["rust_src"]["sha256"], "bad"))
        with self.assertRaisesRegex(ValueError, "source archive pin"):
            self.check_rust()

    def test_missing_source(self):
        (self.rustroot / "lib/rustlib/src/rust/library/std/Cargo.toml").unlink()
        with self.assertRaisesRegex(ValueError, "rust-src is missing"):
            self.check_rust()


if __name__ == "__main__":
    unittest.main()
