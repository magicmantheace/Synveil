#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_native import verify_bundle, install_bundle  # noqa: E402


class NativeRootfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.rootfs = self.root / "rootfs"
        self.lock = self.root / "Cargo.lock"
        self.lock.write_bytes(b"fixture lock")
        self.pin = {"rust_src": {"sha256": "pinned-source"}}
        self.manifest = {"schema": "synveil.native-build/v1", "git_commit": "current",
                         "git_dirty": False, "cargo_lock_sha256": hashlib.sha256(self.lock.read_bytes()).hexdigest(),
                         "rust": {"source_verification": {"sha256": "pinned-source", "verified_files": 2}},
                         "artifacts": {}}
        for name in ("veil-core", "synctl"):
            data = (name + " fixture").encode()
            (self.bundle / name).write_bytes(data)
            self.manifest["artifacts"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                                 "link_inputs": ["audited fixture"], "link_map_sha256": "fixture-map"}
        self.save_manifest()

    def save_manifest(self):
        (self.bundle / "build.json").write_text(json.dumps(self.manifest))

    def check(self):
        return verify_bundle(self.bundle, "current", self.lock, self.pin)

    def test_matching_bundle(self):
        self.assertEqual(set(self.check()[1]), {"veil-core", "synctl"})

    def test_install_paths_and_modes(self):
        install_bundle(self.bundle, self.rootfs, "current", self.lock, self.pin)
        self.assertEqual((self.rootfs / "usr/sbin/veil-core").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.rootfs / "usr/bin/synctl").read_bytes(), (self.bundle / "synctl").read_bytes())
        self.assertTrue((self.rootfs / "usr/share/synveil/native-build.json").is_file())

    def test_stale_revision(self):
        self.manifest["git_commit"] = "old"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "current clean Git"):
            self.check()

    def test_dirty_revision(self):
        self.manifest["git_dirty"] = True
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "current clean Git"):
            self.check()

    def test_changed_dependency_lock(self):
        self.lock.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "dependency lock"):
            self.check()

    def test_changed_source_pin(self):
        self.pin["rust_src"]["sha256"] = "new-pin"
        with self.assertRaisesRegex(ValueError, "Rust source verification"):
            self.check()

    def test_tampered_binary(self):
        (self.bundle / "synctl").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "differs from its audited manifest"):
            self.check()

    def test_missing_audit(self):
        self.manifest["artifacts"]["synctl"]["link_inputs"] = []
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "link audit evidence"):
            self.check()

    def test_destination_symlink_escape(self):
        self.rootfs.mkdir()
        (self.rootfs / "usr").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "escapes"):
            install_bundle(self.bundle, self.rootfs, "current", self.lock, self.pin)
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
