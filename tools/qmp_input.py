#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Constrained QEMU QMP keyboard injector for isolated Synveil VM smoke tests.

This module is a *host-side test helper*, not a guest service or AI action.
The QMP socket grants complete emulator control and must stay private.
"""
import json
import re
from pathlib import Path
import socket
import time

# QEMU QKeyCode enumerators. Only short allowlisted sequences for isolated
# settings and demo-terminal guest smoke tests are permitted.
# Restrict password typing to a fresh, test-only 128-bit lowercase hex
# value; QMP never receives an actual user credential.
ALLOWED_KEYS = frozenset(("right", "left", "up", "down", "ret", "esc"))
ALLOWED_TEST_KEYS = ALLOWED_KEYS | frozenset("0123456789abcdef")


class QMPError(RuntimeError):
    pass


def _read_reply(reader, *, greeting=False):
    for _ in range(32):
        line = reader.readline()
        if not line or len(line) > 65536:
            raise QMPError("QMP closed connection or emitted oversized response")
        try:
            reply = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise QMPError("malformed QMP response") from exc
        if not isinstance(reply, dict):
            raise QMPError("invalid QMP response shape")
        if greeting and "QMP" in reply:
            return reply
        if "error" in reply:
            raise QMPError(f"QMP rejected command: {reply['error']}")
        if not greeting and "return" in reply:
            return reply
        if "event" not in reply:
            raise QMPError("unexpected QMP response")
    raise QMPError("too many asynchronous QMP events")


def emit_command(sock, reader, command):
    sock.sendall((json.dumps(command, separators=(",", ":")) + "\n").encode())
    return _read_reply(reader)


def press(sock, reader, key, allowed=ALLOWED_KEYS):
    if key not in allowed:
        raise ValueError("unsupported QMP test key")
    event = {
        "type": "key",
        "data": {"down": True, "key": {"type": "qcode", "data": key}}
    }
    emit_command(sock, reader, {"execute": "input-send-event",
                                "arguments": {"events": [event]}})
    event["data"]["down"] = False
    emit_command(sock, reader, {"execute": "input-send-event",
                                "arguments": {"events": [event]}})


def send_keys(socket_path: Path, keys: tuple[str, ...], *, timeout=5.0,
              allowed=ALLOWED_KEYS):
    """Send a fixed, fully allowlisted test sequence over private host QMP."""
    if not keys or any(key not in allowed for key in keys):
        raise ValueError("unsupported QMP test sequence")
    socket_path = Path(socket_path)
    if not socket_path.is_absolute():
        raise ValueError("QMP socket must use an absolute path")
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        while True:
            try:
                sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
                sock.connect(str(socket_path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise QMPError("QMP socket unavailable before deadline")
                time.sleep(0.05)
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        with sock.makefile("rb") as reader:
            _read_reply(reader, greeting=True)
            emit_command(sock, reader, {"execute": "qmp_capabilities"})
            for key in keys:
                if time.monotonic() >= deadline:
                    raise QMPError("QMP keyboard sequence exceeded deadline")
                press(sock, reader, key, allowed=allowed)
                time.sleep(0.075)



def settings_toggle(socket_path: Path, *, timeout=5.0):
    """Use guest input to change two display settings."""
    return send_keys(socket_path, ("right", "right", "right", "right",
                                   "ret", "right", "up"), timeout=timeout)


def open_demo_terminal(socket_path: Path, *, timeout=5.0):
    """Select Terminal and confirm, never opening privileged debug terminal."""
    return send_keys(socket_path, ("right", "right", "right", "ret", "ret"),
                     timeout=timeout)


def type_test_secret(socket_path: Path, secret: str, *, timeout=15.0):
    """Type only a fresh test-generated 128-bit lowercase hex password."""
    if not isinstance(secret, str) or re.fullmatch(r"[a-f0-9]{32}", secret) is None:
        raise ValueError("QMP test secrets must be 128-bit lowercase hexadecimal")
    return send_keys(socket_path, (*secret, "ret"), timeout=timeout,
                     allowed=ALLOWED_TEST_KEYS)



def switch_to_login_vt(socket_path: Path, *, timeout=5.0):
    """Send only Ctrl+Alt+F2 to test authenticated VT lease revocation.

    All keycodes and the target VT are fixed: callers cannot send commands,
    arbitrary chords, passwords, or synthetic events through this helper.
    """
    socket_path = Path(socket_path)
    if not socket_path.is_absolute():
        raise ValueError("QMP socket must use an absolute path")
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        while True:
            try:
                sock.settimeout(max(0.1, min(1.0, deadline - time.monotonic())))
                sock.connect(str(socket_path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise QMPError("QMP socket unavailable before deadline")
                time.sleep(0.05)
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        with sock.makefile("rb") as reader:
            _read_reply(reader, greeting=True)
            emit_command(sock, reader, {"execute": "qmp_capabilities"})
            keys = ("ctrl", "alt", "f2")
            events = [
                {"type": "key",
                 "data": {"down": True, "key": {"type": "qcode", "data": key}}}
                for key in keys
            ]
            events.extend(
                {"type": "key",
                 "data": {"down": False, "key": {"type": "qcode", "data": key}}}
                for key in reversed(keys)
            )
            emit_command(sock, reader, {"execute": "input-send-event",
                                        "arguments": {"events": events}})

def logout_user_desktop(socket_path: Path, *, timeout=5.0):
    """Exit the authenticated UID1000 desktop with a single Escape key.

    No arbitrary shell text or command injection is possible through this
    test operation; the guest must be on the top-level user dashboard.
    """
    return send_keys(socket_path, ("esc",), timeout=timeout)
