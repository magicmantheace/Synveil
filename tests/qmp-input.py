#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Mock QMP server: validate actual input-send-event protocol and boundaries."""
from pathlib import Path
import json
import socket
import tempfile
import threading
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, settings_toggle, press


class QMPTests(unittest.TestCase):
    def serve(self, listener, commands, bad=False):
        connection, _ = listener.accept()
        with connection, connection.makefile("rb") as reader:
            connection.sendall(b'{"QMP":{"version":{"qemu":{"major":1,"minor":0,"micro":0},"package":""},"capabilities":[]}}\n')
            while line := reader.readline():
                item = json.loads(line)
                commands.append(item)
                if bad:
                    connection.sendall(b'{"error":{"class":"GenericError","desc":"denied"}}\n')
                    break
                connection.sendall(b'{"return":{}}\n')

    def test_setting_navigation_uses_typed_qmp_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                settings_toggle(endpoint, timeout=6)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            self.assertEqual(commands[0]["execute"], "qmp_capabilities")
            messages = commands[1:]
            self.assertEqual(len(messages), 14)
            pressed = []
            for command in messages:
                self.assertEqual(command["execute"], "input-send-event")
                event = command["arguments"]["events"][0]
                self.assertEqual(event["type"], "key")
                self.assertEqual(event["data"]["key"]["type"], "qcode")
                if event["data"]["down"]:
                    pressed.append(event["data"]["key"]["data"])
            self.assertEqual(pressed, ["right"] * 4 + ["ret", "right", "up"])
    
    def test_error_and_unapproved_key_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                worker = threading.Thread(target=self.serve, args=(listener, [], True))
                worker.start()
                with self.assertRaises(QMPError):
                    settings_toggle(endpoint, timeout=3)
                worker.join(timeout=3)
            with self.assertRaises(ValueError):
                press(None, None, "ctrl-alt-delete")

    def test_non_absolute_paths_are_refused(self):
        with self.assertRaises(ValueError):
            settings_toggle(Path("relative.sock"))


if __name__ == "__main__":
    unittest.main()
