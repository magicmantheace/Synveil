#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Constrained QEMU QMP keyboard injector for isolated Synveil VM smoke tests.

This module is a *host-side test helper*, not a guest service or AI action.
The QMP socket grants complete emulator control and must stay private.
"""
import json
from pathlib import Path
import socket
import time

# QEMU QKeyCode enumerators. Only the short sequence needed by the desktop
# settings UI can be emitted by this helper.
ALLOWED_KEYS = frozenset(("right", "left", "up", "down", "ret", "esc"))


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


def press(sock, reader, key):
    if key not in ALLOWED_KEYS:
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


def settings_toggle(socket_path: Path, *, timeout=5.0):
    """Use real guest input: Settings tile, contrast toggle, pointer enlargement."""
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
            for key in ("right", "right", "right", "right", "ret", "right", "up"):
                if time.monotonic() >= deadline:
                    raise QMPError("QMP keyboard sequence exceeded deadline")
                press(sock, reader, key)
                time.sleep(0.075)
