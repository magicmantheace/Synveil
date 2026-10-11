#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QEMU CLI selection tests using a fake emulator and firmware files."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QemuOptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.out = self.work / "out"
        self.out.mkdir()
        (self.out / "synveil-x86_64.img").write_bytes(b"fixture")
        self.firmware = self.work / "CODE.fd"
        self.firmware.write_bytes(b"firmware")
        self.variables = self.work / "VARS.fd"
        self.variables.write_bytes(b"variables")
        self.bin = self.work / "bin"
        self.bin.mkdir()
        qemu = self.bin / "qemu-system-x86_64"
        qemu.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$SYNVEIL_TEST_QEMU_ARGS"\n')
        qemu.chmod(0o755)
        self.args_file = self.work / "args"

    def invoke(self, extra=None):
        env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "SYNVEIL_OUT_DIR": str(self.out),
            "SYNVEIL_OVMF_CODE": str(self.firmware),
            "SYNVEIL_OVMF_VARS": str(self.variables),
            "SYNVEIL_QEMU_TCG": "1",
            "SYNVEIL_TEST_QEMU_ARGS": str(self.args_file),
        }
        env.pop("SYNVEIL_QEMU_DISPLAY", None)
        env.pop("SYNVEIL_QEMU_VIDEO", None)
        env.pop("SYNVEIL_QEMU_NETWORK", None)
        env.pop("SYNVEIL_QEMU_STATE_DISK", None)
        env.update(extra or {})
        result = subprocess.run(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            env=env, capture_output=True, text=True, timeout=10,
        )
        args = self.args_file.read_text().splitlines() if self.args_file.exists() else []
        return result, args

    def test_default_remains_headless_and_offline(self):
        result, args = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-display") + 1], "none")
        self.assertIn("-net", args)
        self.assertIn("none", args)
        self.assertNotIn("virtio-vga", args)
        self.assertNotIn("virtio-net-pci,netdev=synveil_net", args)

    def test_graphics_and_network_devices_are_opt_in(self):
        result, args = self.invoke({"SYNVEIL_QEMU_DISPLAY": "gtk",
                                    "SYNVEIL_QEMU_NETWORK": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-display") + 1], "gtk")
        for device in ("virtio-vga", "qemu-xhci", "usb-kbd", "usb-tablet"):
            self.assertIn(device, args)
        self.assertIn("user,id=synveil_net", args)
        self.assertIn("virtio-net-pci,netdev=synveil_net", args)
        self.assertNotIn("-net", args)

    def test_headless_graphics_probe_can_use_virtual_video(self):
        result, args = self.invoke({"SYNVEIL_QEMU_VIDEO": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-display") + 1], "none")
        self.assertIn("virtio-vga", args)
        self.assertIn("-net", args)

    def test_rejects_invalid_video_before_launch(self):
        result, args = self.invoke({"SYNVEIL_QEMU_VIDEO": "yes"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])

    def test_rejects_invalid_display_before_launch(self):
        result, args = self.invoke({"SYNVEIL_QEMU_DISPLAY": "remote"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SYNVEIL_QEMU_DISPLAY must be", result.stderr)
        self.assertEqual(args, [])

    def test_rejects_invalid_network_before_launch(self):
        result, args = self.invoke({"SYNVEIL_QEMU_NETWORK": "yes"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])


if __name__ == "__main__":
    unittest.main()
