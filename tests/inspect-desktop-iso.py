#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host-only ISO boot-structure inspection tests, without invoking QEMU."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "inspect_desktop_iso", ROOT / "tools/inspect_desktop_iso.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PROOF = """Drive current: -indev 'synveil-desktop-preview-amd64.hybrid.iso'
Boot record  : El Torito , MBR isohybrid cyl-align-on GPT APM
El Torito catalog  : 134  1
El Torito images   :   N  Pltf  B   Emul  Ld_seg  Hdpt  Ldsiz         LBA
El Torito boot img :   1  BIOS  y   none  0x0000  0x00      4        1799
El Torito boot img :   2  UEFI  y   none  0x0000  0x00   6656         135
"""


class IsoInspectionTests(unittest.TestCase):
    def test_detects_actual_uefi_boot_record(self):
        result = mod.parse_el_torito(PROOF)
        self.assertTrue(result["uefi_boot_entry_verified"])
        self.assertEqual(result["bootable_uefi_entries"], [2])
        self.assertEqual(result["bootable_bios_entries"], [1])
        self.assertFalse(result["physical_boot_tested"])
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["qemu_guest_boot_tested"])

    def test_fails_closed_on_bios_only_inactive_or_no_records(self):
        for text in (
            PROOF.replace("UEFI  y", "UEFI  n"),
            PROOF.replace("UEFI  y", "BIOS  y"),
            "El Torito images : 0\n",
            PROOF.replace("UEFI  y", "UEFI  n").replace("BIOS  y", "BIOS  n"),
            PROOF + "El Torito boot img :   2  UEFI  y   none  0x00\n",
        ):
            with self.subTest(value=text[-80:]), self.assertRaises(mod.ImageInspectionError):
                mod.parse_el_torito(text)

    def test_rejects_symlinks_and_does_not_call_xorriso(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "real.iso"
            original.write_bytes(b"x" * (1024 * 1024))
            symlink = root / "link.iso"
            symlink.symlink_to(original)
            with patch.object(mod.subprocess, "run") as runner:
                with self.assertRaises(mod.ImageInspectionError):
                    mod.inspect(symlink)
                runner.assert_not_called()
            self.assertEqual(original.stat().st_size, 1024 * 1024)

    def test_inspect_calls_read_only_command_for_regular_image(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "source.iso"
            image.write_bytes(b"data" * (262144))
            with patch.object(mod.subprocess, "run") as runner:
                runner.return_value = subprocess.CompletedProcess([], 0, PROOF, "")
                result = mod.inspect(image)
            self.assertTrue(result["uefi_boot_entry_verified"])
            argv = runner.call_args.args[0]
            self.assertEqual(argv, ["xorriso", "-indev", str(image),
                                    "-report_el_torito", "plain"])
            self.assertNotIn("-outdev", argv)

    def test_non_regular_image_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(mod.ImageInspectionError):
                mod.inspect(root)
            small = root / "tiny.iso"
            small.write_bytes(b"bad")
            with self.assertRaises(mod.ImageInspectionError):
                mod.inspect(small)


if __name__ == "__main__":
    unittest.main()
