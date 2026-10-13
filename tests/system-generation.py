#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Test bounded, purely advisory A/B generation decisions."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/system_generation.py"
spec = importlib.util.spec_from_file_location("system_generation", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def layout():
    return {
        "schema": "synveil.install-layout/v1",
        "efi_partuuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "slots": {
            "A": {"partuuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                  "image_sha256": "a" * 64},
            "B": {"partuuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                  "image_sha256": "b" * 64},
        },
        "state_partuuid": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "data_partuuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        "confirmed_slot": "A",
        "candidate_slot": "B",
        "candidate_attempts_remaining": 2,
    }


class SystemGenerationTests(unittest.TestCase):
    def test_trial_exhaustion_and_confirmed_are_advisory(self):
        data = layout()
        trial = mod.plan_boot(data)
        self.assertEqual(trial["selected_slot"], "B")
        self.assertEqual(trial["proposed_attempts_remaining"], 1)
        self.assertTrue(trial["advisory_only"])
        self.assertFalse(trial["image_verification_performed"])
        self.assertFalse(trial["disk_mutations_performed"])
        self.assertEqual(data["candidate_attempts_remaining"], 2)
        data["candidate_attempts_remaining"] = 0
        expired = mod.plan_boot(data)
        self.assertEqual(expired["selected_slot"], "A")
        self.assertEqual(expired["reason"], "candidate_exhausted")
        data["candidate_slot"] = None
        self.assertEqual(mod.plan_boot(data)["reason"], "confirmed")

    def test_invalid_identities_and_image_digests_are_rejected(self):
        cases = []
        for field in ("efi_partuuid", "state_partuuid", "data_partuuid"):
            data = layout()
            data[field] = "/dev/vdb"
            cases.append(data)
        data = layout()
        data["slots"]["B"]["partuuid"] = data["slots"]["A"]["partuuid"]
        cases.append(data)
        data = layout()
        data["state_partuuid"] = data["efi_partuuid"]
        cases.append(data)
        data = layout()
        data["efi_partuuid"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        cases.append(data)
        data = layout()
        data["slots"]["A"]["image_sha256"] = "z" * 64
        cases.append(data)
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(mod.LayoutError):
                    mod.plan_boot(case)

    def test_invalid_role_or_metadata_shape_is_rejected(self):
        cases = []
        for candidate, remaining in [("A", 1), ("C", 1), (None, 1),
                                     ("B", -1), ("B", 4), ("B", True),
                                     ("B", "1")]:
            data = layout()
            data["candidate_slot"] = candidate
            data["candidate_attempts_remaining"] = remaining
            cases.append(data)
        for field in ("schema", "state_partuuid", "confirmed_slot"):
            data = layout()
            del data[field]
            cases.append(data)
        data = layout()
        data["extra"] = "dev:/wrong"
        cases.append(data)
        data = layout()
        data["slots"]["C"] = copy.deepcopy(data["slots"]["A"])
        cases.append(data)
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(mod.LayoutError):
                    mod.plan_boot(case)

    def test_read_only_bound_symlink_duplicate_and_corrupt_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "layout.json"
            original = json.dumps(layout()).encode()
            path.write_bytes(original)
            self.assertEqual(mod.read_layout(path), layout())
            self.assertEqual(path.read_bytes(), original)
            link = Path(folder) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                mod.read_layout(link)
            path.write_bytes(b" " * (mod.MAX_BYTES + 1))
            with self.assertRaises(mod.LayoutError):
                mod.read_layout(path)
            path.write_text('{"schema":0,"schema":1}')
            with self.assertRaises(mod.LayoutError):
                mod.read_layout(path)
            path.write_bytes(b"\xff")
            with self.assertRaises(mod.LayoutError):
                mod.read_layout(path)
            import os
            pipe = Path(folder) / "manifest-fifo"
            os.mkfifo(pipe)
            with self.assertRaises(mod.LayoutError):
                mod.read_layout(pipe)

    def test_staged_images_fall_back_only_to_matching_confirmed_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Path(folder) / "A.img"
            b = Path(folder) / "B.img"
            a.write_bytes(b"confirmed image bytes")
            b.write_bytes(b"candidate image bytes")
            data = layout()
            data["slots"]["A"]["image_sha256"] = hashlib.sha256(a.read_bytes()).hexdigest()
            data["slots"]["B"]["image_sha256"] = hashlib.sha256(b.read_bytes()).hexdigest()
            trial = mod.plan_staged_boot(data, {"A": a, "B": b})
            self.assertEqual(trial["selected_slot"], "B")
            self.assertEqual(trial["reason"], "candidate_trial")
            self.assertEqual(trial["staged_image_matches"], {"A": True, "B": True})
            self.assertFalse(trial["image_verification_performed"])
            self.assertFalse(trial["partition_identity_verified"])
            self.assertFalse(trial["boot_image_authenticity_verified"])
            b.write_bytes(b"corrupted candidate bytes")
            fallback = mod.plan_staged_boot(data, {"A": a, "B": b})
            self.assertEqual(fallback["selected_slot"], "A")
            self.assertEqual(fallback["reason"], "candidate_staged_image_mismatch")
            self.assertEqual(fallback["staged_image_matches"], {"A": True, "B": False})
            a.write_bytes(b"corrupted confirmed bytes")
            with self.assertRaisesRegex(mod.LayoutError, "confirmed system image"):
                mod.plan_staged_boot(data, {"A": a, "B": b})

    def test_staged_images_reject_symlinks_hardlinks_and_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "image"
            image.write_bytes(b"test image bytes")
            link = root / "symlink"
            link.symlink_to(image)
            with self.assertRaises(OSError):
                mod.staged_image_sha256(link)
            hardlink = root / "hardlink"
            hardlink.hardlink_to(image)
            with self.assertRaises(mod.LayoutError):
                mod.staged_image_sha256(image)
            hardlink.unlink()
            self.assertEqual(mod.staged_image_sha256(image),
                             hashlib.sha256(image.read_bytes()).hexdigest())
            image.write_bytes(b"")
            with self.assertRaises(mod.LayoutError):
                mod.staged_image_sha256(image)
            # A named pipe must not block or become a source of disk identity.
            pipe = root / "pipe"
            import os
            os.mkfifo(pipe)
            with self.assertRaises(mod.LayoutError):
                mod.staged_image_sha256(pipe)

    def test_cli_staged_image_checks_do_not_modify_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = root / "A.img", root / "B.img"
            a.write_bytes(b"a-v1")
            b.write_bytes(b"b-v2")
            data = layout()
            for slot, path in (("A", a), ("B", b)):
                data["slots"][slot]["image_sha256"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            manifest = root / "layout.json"
            manifest.write_text(json.dumps(data))
            command = [sys.executable, str(SCRIPT), "--manifest", str(manifest),
                       "--staged-slot-a", str(a), "--staged-slot-b", str(b)]
            result = subprocess.run(command, capture_output=True, text=True,
                                    check=True, timeout=5)
            decision = json.loads(result.stdout)
            self.assertTrue(decision["staged_image_hashes_checked"])
            self.assertEqual(decision["selected_slot"], "B")
            invalid = subprocess.run(command[:-2] + [str(a)],
                                     capture_output=True, text=True, timeout=5)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(a.read_bytes(), b"a-v1")
            self.assertEqual(b.read_bytes(), b"b-v2")
            self.assertEqual(manifest.read_text(), json.dumps(data))

    def test_cli_rejects_write_mode_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "layout.json"
            original = json.dumps(layout())
            path.write_text(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path)],
                capture_output=True, text=True, timeout=5, check=True,
            )
            self.assertEqual(json.loads(result.stdout)["selected_slot"], "B")
            denied = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(path), "--write"],
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
