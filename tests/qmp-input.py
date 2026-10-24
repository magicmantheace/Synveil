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
from qmp_input import QMPError, settings_toggle, open_demo_terminal, press, send_keys, type_test_secret, logout_user_desktop, switch_to_login_vt, open_user_terminal, open_user_files, exit_user_terminal
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

    def test_graphical_ppm_parser_rejects_blank_truncated_and_unsafe_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "test-frame.ppm"
            header = b"P6\n320 200\n255\n"
            # Genuine UI content contains multiple differently colored
            # *pixels*. A solid nonblack RGB triplet must not qualify.
            good = (b"\x00\x14\xfa" * 160 +
                    b"\xff\xff\xff" * 160) * 200
            for data, expected in (
                (header + good, True),
                (header + b"\x00\x14\xfa" * (320 * 200), False),
                (header + b"\x00\x00\xff" * (320 * 200), False),
                (header + bytes(320 * 200 * 3), False),
                (header + good[:100], False),
                (b"P3\n320 200\n255\n" + good, False),
                (b"P6\n1 1\n255\n" + b"\x00\xfa\xdd", False),
                (header + good + b"junk", False),
            ):
                file.write_bytes(data)
                self.assertIs(qmp_input.verified_ppm_test_frame(file), expected)
            file.unlink()
            file.symlink_to(Path(tmp) / "missing")
            self.assertFalse(qmp_input.verified_ppm_test_frame(file))

    def test_readonly_qmp_scanout_uses_private_ephemeral_output_and_exact_command(self):
        def serve_capture(listener, commands, *, blank):
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as reader:
                connection.sendall(
                    b'{"QMP":{"version":{"qemu":{"major":1,"minor":0,"micro":0}}}}\n')
                while line := reader.readline():
                    item = json.loads(line)
                    commands.append(item)
                    if item.get("execute") == "screendump":
                        frame = Path(item["arguments"]["filename"])
                        frame.write_bytes(
                            b"P6\n320 200\n255\n" +
                            (((b"\x00\x11\xec" * 160 +
                                b"\xee\xee\xee" * 160) * 200)
                             if not blank else bytes(320 * 200 * 3))
                        )
                    connection.sendall(b'{"return":{}}\n')

        for blank in (False, True):
            with self.subTest(blank=blank), tempfile.TemporaryDirectory() as tmp:
                endpoint = Path(tmp) / "qmp.sock"
                output = Path(tmp) / "reboot-login.ppm"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(endpoint))
                    listener.listen(1)
                    commands = []
                    worker = threading.Thread(
                        target=serve_capture, args=(listener, commands),
                        kwargs={"blank": blank}
                    )
                    worker.start()
                    self.assertEqual(
                        qmp_input.capture_test_scanout(endpoint, output, timeout=5),
                        not blank
                    )
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())
                self.assertEqual([c["execute"] for c in commands],
                                 ["qmp_capabilities", "screendump"])
                self.assertFalse(output.exists())  # no password/owner screen retained
                output.write_bytes(b"do-not-overwrite")
                with self.assertRaises(ValueError):
                    qmp_input.capture_test_scanout(endpoint, output, timeout=2)
                self.assertEqual(output.read_bytes(), b"do-not-overwrite")

    def test_graphical_scanout_fingerprint_is_ephemeral_and_rejects_blank(self):
        def serve_frame(listener, commands, *, blank):
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as reader:
                connection.sendall(b'{"QMP":{"version":{"qemu":{"major":1}}}}\n')
                while line := reader.readline():
                    command = json.loads(line)
                    commands.append(command)
                    if command.get("execute") == "screendump":
                        path = Path(command["arguments"]["filename"])
                        raw = bytes(320 * 200 * 3) if blank else (
                            (b"\x10\x30\xfa" * 160 + b"\xf0\xf0\xf0" * 160) * 200
                        )
                        path.write_bytes(b"P6\n320 200\n255\n" + raw)
                    connection.sendall(b'{"return":{}}\n')

        for blank in (False, True):
            with self.subTest(blank=blank), tempfile.TemporaryDirectory() as tmp:
                endpoint = Path(tmp) / "qmp.sock"
                shot = Path(tmp) / "private.ppm"
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                    listener.bind(str(endpoint))
                    listener.listen(1)
                    commands = []
                    worker = threading.Thread(
                        target=serve_frame, args=(listener, commands),
                        kwargs={"blank": blank},
                    )
                    worker.start()
                    digest = qmp_input.capture_test_scanout(
                        endpoint, shot, timeout=5, fingerprint=True
                    )
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())
                if blank:
                    self.assertIsNone(digest)
                else:
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(all(c in "0123456789abcdef" for c in digest))
                self.assertFalse(shot.exists())
                self.assertEqual(
                    [c["execute"] for c in commands],
                    ["qmp_capabilities", "screendump"]
                )

    def test_graphical_screendump_refuses_public_or_symlink_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "private"
            private.mkdir(mode=0o700)
            public = Path(tmp) / "public"
            public.mkdir(mode=0o755)
            target = private / "target.ppm"
            link = private / "alias.ppm"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                qmp_input.capture_test_scanout(
                    Path(tmp) / "none.sock", public / "frame.ppm")
            with self.assertRaises(ValueError):
                qmp_input.capture_test_scanout(
                    Path(tmp) / "none.sock", link)
            self.assertFalse(target.exists())

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

    def test_private_files_tile_has_fixed_keyboard_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoint = Path(tmp) / "qmp.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                commands = []
                worker = threading.Thread(target=self.serve, args=(listener, commands))
                worker.start()
                open_user_files(endpoint)
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
            pressed = [
                c["arguments"]["events"][0]["data"]["key"]["data"]
                for c in commands[1:]
                if c["arguments"]["events"][0]["data"]["down"]
            ]
            self.assertEqual(pressed, ["right"] * 5 + ["ret"])

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
