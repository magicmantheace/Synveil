#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline integrity, extraction and target-authority tests for libffi."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_libffi", ROOT / "tools/native_libffi.py")
ffi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ffi)


class NativeLibffiTests(unittest.TestCase):
    def test_exact_upstream_lock(self):
        self.assertEqual(ffi.source_pin()["version"], "3.5.2")
        self.assertEqual(len(ffi.source_pin()["sha256"]), 64)

    def test_modified_sources_rejected(self):
        good = json.loads(ffi.LOCK.read_text())
        for k,v in (("filename", "../libffi-3.5.2.tar.gz"),
                    ("url", "http://github.com/libffi/libffi"),
                    ("url", "https://example.com/libffi.tar.gz"),
                    ("sha256", "f" * 63)):
            with self.subTest(k=k), tempfile.TemporaryDirectory() as root:
                altered = json.loads(json.dumps(good))
                altered["libffi"][k] = v
                lock = Path(root) / "lock.json"
                lock.write_text(json.dumps(altered))
                with patch.object(ffi, "LOCK", lock):
                    with self.assertRaises(ValueError):
                        ffi.source_pin()

    def test_only_new_private_stages(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            with patch.object(ffi, "ROOT", base), patch.object(
                    ffi, "STAGE_ROOT", base / "out/native-libffi"):
                folder = ffi.new_stage()
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                with self.assertRaises(ValueError):
                    ffi.new_stage()
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "real").mkdir()
            (base / "out").symlink_to(base / "real")
            with patch.object(ffi, "ROOT", base), patch.object(
                    ffi, "STAGE_ROOT", base / "out/native-libffi"):
                with self.assertRaises(ValueError):
                    ffi.new_stage()

    def test_tar_traversal_and_links(self):
        for name, link in (("../evil", None),
                           ("libffi-3.5.2/../../escape", None),
                           ("libffi-3.5.2/link", "/etc/shadow"),
                           ("libffi-3.5.2/x/link", "../../../../outside")):
            with self.subTest(name=name, link=link), tempfile.TemporaryDirectory() as root:
                archive = Path(root) / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    info = tarfile.TarInfo(name)
                    if link:
                        info.type = tarfile.SYMTYPE
                        info.linkname = link
                        tar.addfile(info)
                    else:
                        info.size = 1
                        tar.addfile(info, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    ffi.extract(archive, Path(root) / "unpack")
                self.assertFalse((Path(root) / "escape").exists())

    def test_internal_source_symlink_allowed_but_outside_rejected(self):
        item = tarfile.TarInfo("libffi-3.5.2/docs/file")
        item.type = tarfile.SYMTYPE
        item.linkname = "../README.md"
        self.assertTrue(ffi.safe_member(item))
        item.linkname = "../../../README.md"
        self.assertFalse(ffi.safe_member(item))

    def test_corrupted_upstream_digest_cleans_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            data = b"not real libffi"
            p = {"sha256": hashlib.sha256(data).hexdigest()}
            with patch.object(ffi.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                self.assertEqual(ffi.download_verified(p, folder).read_bytes(), data)
            (folder / ffi.FILENAME).unlink()
            p["sha256"] = "0" * 64
            with patch.object(ffi.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaises(ValueError):
                    ffi.download_verified(p, folder)
            self.assertFalse((folder / ffi.FILENAME).exists())

    def test_target_compiler_cannot_fall_back_to_host(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(ffi, "TOOLCHAIN", Path(root)):
                with self.assertRaises(ValueError):
                    ffi.target_tools()


if __name__ == "__main__":
    unittest.main()
