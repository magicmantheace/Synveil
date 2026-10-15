#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Mock QMP server: validate actual input-send-event protocol and boundaries."""
from pathlib import Path
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, settings_toggle, open_demo_terminal, press, send_keys, type_test_secret, logout_user_desktop, switch_to_login_vt, open_user_terminal, exit_user_terminal
import qmp_input


class QMPTests(unittest.TestCase):
    def test_key_press_survives_guest_usb_poll(self):
        state = {"down": False, "key": None}
        observed = []

        def acknowledge(sock, reader, command):
            event = command["arguments"]["events"][0]["data"]
            state.update(down=event["down"], key=event["key"]["data"])
            return {"return": {}}

        def guest_poll(delay):
            if delay >= 0.008 and state["down"]:
                observed.append(state["key"])

        with patch.object(qmp_input, "emit_command", side_effect=acknowledge), \
             patch.object(qmp_input.time, "sleep", side_effect=guest_poll):
            press(None, None, "right")
        self.assertEqual(observed, ["right"])
        self.assertFalse(state["down"])

    def test_chord_is_observable_and_releases_after_interrupted_hold(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                held = set()
                observed = []

                def acknowledge(sock, reader, command):
                    for event in command["arguments"]["events"]:
                        data = event["data"]
                        key = data["key"]["data"]
                        if data["down"]:
                            held.add(key)
                        else:
                            held.discard(key)

                def guest_poll(delay):
                    if delay >= 0.008:
                        observed.append(set(held))
                    if interrupted:
                        raise RuntimeError("interrupted hold")

                with patch.object(qmp_input, "emit_command", side_effect=acknowledge), \
                     patch.object(qmp_input.time, "sleep", side_effect=guest_poll):
                    if interrupted:
                        with self.assertRaisesRegex(RuntimeError, "interrupted hold"):
                            qmp_input._hold_keys(None, None, ("ctrl", "alt", "f2"))
                    else:
                        qmp_input._hold_keys(None, None, ("ctrl", "alt", "f2"))
                self.assertEqual(observed, [{"ctrl", "alt", "f2"}])
                self.assertFalse(held)

    def test_failed_key_down_acknowledgement_still_attempts_release(self):
        commands = []

        def acknowledge(sock, reader, command):
            commands.append(command)
            if command["arguments"]["events"][0]["data"]["down"]:
                raise QMPError("unknown delivery")

        with patch.object(qmp_input, "emit_command", side_effect=acknowledge):
            with self.assertRaisesRegex(QMPError, "unknown delivery"):
                press(None, None, "ret")
        self.assertEqual(len(commands), 2)
        self.assertFalse(commands[-1]["arguments"]["events"][0]["data"]["down"])

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
    
    def test_terminal_requires_two_enter_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                open_demo_terminal(endpoint, timeout=5)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            presses = [
                item["arguments"]["events"][0]["data"]["key"]["data"]
                for item in commands[1:]
                if item["arguments"]["events"][0]["data"]["down"]
            ]
            self.assertEqual(presses, ["right", "right", "right", "ret", "ret"])

    def test_test_only_hex_password_is_strictly_bounded(self):
        for secret in ("short", "not-a-real-password", "A" * 32, "a" * 40,
                       "a" * 31 + "/", "secret-from-user"):
            with self.assertRaises(ValueError):
                type_test_secret(Path("/unused.sock"), secret)
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                type_test_secret(endpoint, "ab01" * 8, timeout=15)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            pressed = [item["arguments"]["events"][0]["data"]["key"]["data"]
                       for item in commands[1:]
                       if item["arguments"]["events"][0]["data"]["down"]]
            self.assertEqual(pressed, list("ab01" * 8) + ["ret"])

    def test_user_terminal_commands_are_fixed_and_bounded(self):
        for helper, expected in (
            (open_user_terminal, ["right", "right", "right", "ret", "ret"]),
            (exit_user_terminal, ["e", "x", "i", "t", "ret"]),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                endpoint = Path(tmp) / "qmp.sock"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(endpoint))
                    listener.listen(1)
                    commands = []
                    worker = threading.Thread(target=self.serve, args=(listener, commands))
                    worker.start()
                    helper(endpoint, timeout=6)
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())
                keys = [
                    item["arguments"]["events"][0]["data"]["key"]["data"]
                    for item in commands[1:]
                    if item["arguments"]["events"][0]["data"]["down"]
                ]
                self.assertEqual(keys, expected)
        with self.assertRaises(ValueError):
            send_keys(Path("/unused.sock"), ("x", "y"), timeout=1)

    def test_fixed_logout_exits_desktop_without_shell_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                logout_user_desktop(endpoint, timeout=5)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            pressed = [item["arguments"]["events"][0]["data"]["key"]["data"]
                       for item in commands[1:]
                       if item["arguments"]["events"][0]["data"]["down"]]
            self.assertEqual(pressed, ["esc"])
            with self.assertRaises(ValueError):
                send_keys(endpoint, ("s", "u"), timeout=1)

    def test_fixed_vt_switch_chord_cannot_inject_any_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                switch_to_login_vt(endpoint, timeout=5)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            self.assertEqual(commands[0]["execute"], "qmp_capabilities")
            self.assertEqual(len(commands), 3)
            self.assertEqual(commands[1]["execute"], "input-send-event")
            self.assertEqual(commands[2]["execute"], "input-send-event")
            self.assertTrue(all(event["data"]["down"] for event in commands[1]["arguments"]["events"]))
            self.assertTrue(all(not event["data"]["down"] for event in commands[2]["arguments"]["events"]))
            events = commands[1]["arguments"]["events"] + commands[2]["arguments"]["events"]
            self.assertEqual(
                [(event["data"]["key"]["data"], event["data"]["down"])
                 for event in events],
                [("ctrl", True), ("alt", True), ("f2", True),
                 ("f2", False), ("alt", False), ("ctrl", False)]
            )
            for event in events:
                self.assertEqual(event["type"], "key")
                self.assertEqual(event["data"]["key"]["type"], "qcode")

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
            with self.assertRaises(ValueError):
                send_keys(endpoint, ("ctrl-alt-delete",), timeout=1)
            with self.assertRaises(ValueError):
                send_keys(endpoint, (), timeout=1)

    def test_non_absolute_paths_are_refused(self):
        with self.assertRaises(ValueError):
            settings_toggle(Path("relative.sock"))
        with self.assertRaises(ValueError):
            switch_to_login_vt(Path("relative.sock"))


if __name__ == "__main__":
    unittest.main()
