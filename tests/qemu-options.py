#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QEMU CLI selection tests using a fake emulator and firmware files."""
import fcntl
import os
from pathlib import Path
import selectors
import signal
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
        # Rejected QEMU launches must not accidentally reuse captured
        # arguments from an earlier successful invocation.
        self.args_file.unlink(missing_ok=True)
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
        env.pop("SYNVEIL_QEMU_QMP_SOCKET", None)
        env.update(extra or {})
        result = subprocess.run(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            env=env, capture_output=True, text=True, timeout=10,
        )
        args = self.args_file.read_text().splitlines() if self.args_file.exists() else []
        return result, args

    def test_serial_stdin_is_preserved_for_background_emulator(self):
        qemu = self.bin / "qemu-system-x86_64"
        qemu.write_text('#!/bin/sh\nread -r line || exit 9\n[ "$line" = hello ] || exit 10\n')
        env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "SYNVEIL_OUT_DIR": str(self.out),
            "SYNVEIL_OVMF_CODE": str(self.firmware),
            "SYNVEIL_OVMF_VARS": str(self.variables),
            "SYNVEIL_QEMU_TCG": "1",
        }
        result = subprocess.run(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            env=env, input="hello\n", capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shutdown_waits_for_emulator_to_release_writable_disk(self):
        qemu = self.bin / "qemu-system-x86_64"
        qemu.write_text("""#!/usr/bin/env python3
import fcntl
import os
import signal
import sys
import time

with open(os.environ["SYNVEIL_TEST_DISK"], "w") as disk:
    fcntl.flock(disk, fcntl.LOCK_EX)
    def terminate(_signum, _frame):
        time.sleep(0.5)
        sys.exit(0)
    signal.signal(signal.SIGTERM, terminate)
    print("READY", flush=True)
    while True:
        time.sleep(0.05)
""")
        disk_path = self.work / "state-disk"
        qemu.chmod(0o755)
        env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "SYNVEIL_OUT_DIR": str(self.out),
            "SYNVEIL_OVMF_CODE": str(self.firmware),
            "SYNVEIL_OVMF_VARS": str(self.variables),
            "SYNVEIL_QEMU_TCG": "1",
            "SYNVEIL_TEST_DISK": str(disk_path),
        }
        # Exercise both termination of the launcher alone and the full
        # smoke-test process group.  Neither may release its parent before
        # the emulator finishes its delayed, cooperative exit.
        for kill_group in (False, True):
            with self.subTest(kill_group=kill_group):
                process = subprocess.Popen(
                    ["bash", str(ROOT / "build/scripts/qemu.sh")],
                    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=5), "emulator did not start")
                        self.assertEqual(process.stdout.readline().strip(), b"READY")
                    if kill_group:
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    self.assertEqual(process.wait(timeout=5), 143)
                    # flock is a distinct process-safe lock.  The fake guest
                    # still holds it for 500 ms after receiving SIGTERM.
                    with disk_path.open("r+") as disk:
                        fcntl.flock(disk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(disk, fcntl.LOCK_UN)
                finally:
                    # Clean up a hypothetical faulty wrapper's orphan too.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                    process.stdin.close()
                    process.stdout.close()

    def test_default_remains_headless_and_offline(self):
        result, args = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-display") + 1], "none")
        self.assertIn("-net", args)
        self.assertIn("none", args)
        self.assertEqual(args[args.index("-machine") + 1], "q35")
        self.assertNotIn("virtio-vga", args)
        self.assertNotIn("virtio-net-pci,netdev=synveil_net", args)

    def test_graphics_and_network_devices_are_opt_in(self):
        result, args = self.invoke({"SYNVEIL_QEMU_DISPLAY": "gtk",
                                    "SYNVEIL_QEMU_NETWORK": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-display") + 1], "gtk")
        self.assertEqual(args[args.index("-machine") + 1], "q35,i8042=off,vmport=off")
        self.assertEqual(args.count("usb-kbd"), 1)
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
        self.assertEqual(args[args.index("-machine") + 1], "q35,i8042=off,vmport=off")
        self.assertIn("-net", args)

    def test_optional_qmp_requires_private_parent_and_remains_disabled_by_default(self):
        result, args = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("-qmp", args)

        private = self.work / "private"
        private.mkdir(mode=0o700)
        endpoint = private / "monitor.sock"
        result, args = self.invoke({"SYNVEIL_QEMU_QMP_SOCKET": str(endpoint)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[args.index("-qmp") + 1],
                         f"unix:{endpoint},server=on,wait=off")

        insecure = self.work / "public"
        insecure.mkdir(mode=0o755)
        result, args = self.invoke({"SYNVEIL_QEMU_QMP_SOCKET": str(insecure / "monitor.sock")})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])
        result, args = self.invoke({"SYNVEIL_QEMU_QMP_SOCKET": "relative.sock"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])
        endpoint.write_bytes(b"stale")
        result, args = self.invoke({"SYNVEIL_QEMU_QMP_SOCKET": str(endpoint)})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])

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
