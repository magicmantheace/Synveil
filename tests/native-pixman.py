#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline pins, extraction, and filesystem safety of native Pixman staging."""
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
spec = importlib.util.spec_from_file_location("pixman_stage", ROOT / "tools/native_pixman.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class PixmanStagingTests(unittest.TestCase):
    def test_strict_upstream_source_pin(self):
        good = json.loads(native.LOCK.read_text())
        self.assertEqual(native.pin()["version"], "0.46.4")
        for field, value in (
            ("url", "https://fake.example/pixman-0.46.4.tar.xz"),
            ("url", "http://cairographics.org/releases/pixman-0.46.4.tar.xz"),
            ("sha512", "f" * 127),
            ("filename", "../pixman-0.46.4.tar.xz"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                modified = json.loads(json.dumps(good))
                modified["pixman"][field] = value
                tmp = Path(d) / "source.json"
                tmp.write_text(json.dumps(modified))
                with patch.object(native, "LOCK", tmp):
                    with self.assertRaises(ValueError):
                        native.pin()

    def test_existing_and_symlinked_output_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(native, "ROOT", root), patch.object(native, "OUT", root / "out/stage"):
                created = native.only_new_output()
                self.assertEqual(created.stat().st_mode & 0o777, 0o700)
                with self.assertRaises(ValueError):
                    native.only_new_output()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "actual").mkdir()
            (root / "out").symlink_to(root / "actual", target_is_directory=True)
            with patch.object(native, "ROOT", root), patch.object(native, "OUT", root / "out/stage"):
                with self.assertRaises(ValueError):
                    native.only_new_output()

    def test_hash_verification_refuses_corruption_and_cleans(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            data = b"synthetic-not-a-release"
            p = {"filename": "pixman-0.46.4.tar.xz",
                 "sha512": hashlib.sha512(data).hexdigest()}
            with patch.object(native.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                self.assertEqual(native.verified_archive(p, out).read_bytes(), data)
            (out / p["filename"]).unlink()
            p["sha512"] = "0" * 128
            with patch.object(native.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaises(ValueError):
                    native.verified_archive(p, out)
            self.assertFalse((out / p["filename"]).exists())

    def test_tar_traversal_and_links_refused(self):
        for entry_name, link in (
            ("../escape", False),
            ("pixman-0.46.4/../../escape", False),
            ("pixman-0.46.4/shortcut", True),
        ):
            with self.subTest(entry=entry_name), tempfile.TemporaryDirectory() as d:
                src = Path(d) / "test.tar.xz"
                with tarfile.open(src, "w:xz") as tar:
                    member = tarfile.TarInfo(entry_name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                        tar.addfile(member)
                    else:
                        member.size = 1
                        tar.addfile(member, io.BytesIO(b"x"))
                unpack = Path(d) / "src"
                with self.assertRaises(ValueError):
                    native.extract(src, unpack)
                self.assertFalse(unpack.exists())
                self.assertFalse((Path(d) / "escape").exists())

    def test_relative_internal_source_symlink_is_bounded(self):
        # A known-good internal link is allowed; a link to an outside file
        # remains forbidden even if the archive checksum is known.
        import tarfile
        def member(path, link):
            item = tarfile.TarInfo(path)
            item.type = tarfile.SYMTYPE
            item.linkname = link
            return item

        internal = member("pixman-0.46.4/.gitlab-ci.d/01-docker/target-env/linux-ppc.env",
                          "../linux.env")
        self.assertTrue(native.safe_archive_member(internal))
        for link in ("/etc/passwd", "../../../../../etc/passwd",
                     "../../../../../../escape", "linux-ppc.env"):
            self.assertFalse(native.safe_archive_member(
                member(internal.name, link)))
        self.assertFalse(native.safe_archive_member(
            member("pixman-0.46.4/.gitlab-ci.d/link", "../../../../escape")))
        self.assertFalse(native.safe_archive_member(
            member("pixman-0.46.4/link", "..")))
        self.assertFalse(native.safe_archive_member(
            member("../outside", "pixman-0.46.4/example")))

    def test_internal_symlink_extracts_under_verified_source_root(self):
        import tarfile
        import io
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / "ok.tar.xz"
            with tarfile.open(archive, "w:xz") as tar:
                data = b"project('pixman', 'c')\n"
                file = tarfile.TarInfo("pixman-0.46.4/meson.build")
                file.size = len(data)
                tar.addfile(file, io.BytesIO(data))
                link = tarfile.TarInfo("pixman-0.46.4/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "meson.build"
                tar.addfile(link)
            extracted = native.extract(archive, root / "extracted")
            self.assertEqual((extracted / "link").read_bytes(),
                             b"project('pixman', 'c')\n")

    def test_refuses_host_compiler_as_native(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(native, "TC", Path(d)):
                with self.assertRaises(ValueError):
                    native.cross_build(native.pin(), Path(d), Path(d))


if __name__ == "__main__":
    unittest.main()
