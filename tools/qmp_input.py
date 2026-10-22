#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Constrained QEMU QMP keyboard injector for isolated Synveil VM smoke tests.

This module is a *host-side test helper*, not a guest service or AI action.
The QMP socket grants complete emulator control and must stay private.
"""
import hashlib
import json
import re
import os
import stat
from pathlib import Path
import socket
import time

# QEMU QKeyCode enumerators. Only short allowlisted sequences for isolated
# settings and demo-terminal guest smoke tests are permitted.
# Restrict password typing to a fresh, test-only 128-bit lowercase hex
# value; QMP never receives an actual user credential.
ALLOWED_KEYS = frozenset(("right", "left", "up", "down", "ret", "esc"))
ALLOWED_TEST_KEYS = ALLOWED_KEYS | frozenset("0123456789abcdef")
ALLOWED_TERMINAL_RETURN = ALLOWED_KEYS | frozenset("exit")


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


def _hold_keys(sock, reader, keys):
    def edge(down):
        ordered = keys if down else reversed(keys)
        events = [{"type": "key", "data": {"down": down,
                   "key": {"type": "qcode", "data": key}}} for key in ordered]
        return {"execute": "input-send-event", "arguments": {"events": events}}

    try:
        emit_command(sock, reader, edge(True))
        # A QMP reply does not mean the USB keyboard has polled its report.
        # Keep the full chord down until at least one guest poll can observe it.
        time.sleep(0.075)
    finally:
        # Attempt release even if the hold or key-down acknowledgement failed.
        emit_command(sock, reader, edge(False))


def press(sock, reader, key, allowed=ALLOWED_KEYS):
    if key not in allowed:
        raise ValueError("unsupported QMP test key")
    _hold_keys(sock, reader, (key,))


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



def verified_ppm_test_frame(path: Path) -> bool:
    """Reject a blank, oversized, malformed or truncated QMP P6 screenshot.

    This proves there is scanout *in addition to* guest-side login/status
    evidence; screen content is not itself an authentication decision.
    """
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_size > 32 * 1024 * 1024):
            return False
        with path.open("rb") as stream:
            header = stream.read(120)
            match = re.match(
                rb"\AP6[ \t\r\n]+([0-9]{1,5})[ \t\r\n]+"
                rb"([0-9]{1,5})[ \t\r\n]+255[ \t\r\n]", header)
            if match is None:
                return False
            width, height = (int(match.group(1)), int(match.group(2)))
            if not (320 <= width <= 4096 and 200 <= height <= 2160):
                return False
            pixel_bytes = width * height * 3
            if info.st_size != match.end() + pixel_bytes:
                return False
            stream.seek(match.end())
            sample = stream.read(min(pixel_bytes, 1024 * 1024))
            # A single solid RGB color (e.g. uniform blue) has several
            # distinct byte values. Compare whole pixel triplets instead;
            # require enough changed pixels to show actual UI content.
            if len(sample) < 3 or not any(sample):
                return False
            base = sample[:3]
            differences = 0
            for i in range(3, len(sample) - 2, 3):
                if sample[i:i + 3] != base:
                    differences += 1
                    if differences >= 64:
                        return True
            return False
    except (OSError, ValueError):
        return False


def capture_test_scanout(socket_path: Path, output: Path, *, timeout=8.0,
                         fingerprint=False):
    """QMP screendump inside a private, automatically deleted host test file.

    With fingerprint=True, return SHA256 of a *validated, nonblank* complete
    RGB frame, or None. Otherwise retain the historical strict boolean API.
    Neither screenshots nor their digests are printed, saved or archived.
    QMP is test-only and is never used with a real owner's password.
    """
    socket_path = Path(socket_path)
    output = Path(output)
    if (not socket_path.is_absolute() or not output.is_absolute() or
            output.parent.is_symlink() or not output.parent.is_dir() or
            (output.parent.stat().st_mode & 0o077) != 0 or
            output.exists() or output.is_symlink()):
        raise ValueError("QMP framebuffer proof requires an unused private path")
    if not (1 <= timeout <= 30) or type(fingerprint) is not bool:
        raise ValueError("invalid QMP screenshot options")
    # Reserve the name exclusively before QMP writes, then remove the empty
    # regular file: QMP's screendump requires a pathname it may create.
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    output.unlink()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
            with sock.makefile("rb") as reader:
                _read_reply(reader, greeting=True)
                emit_command(sock, reader, {"execute": "qmp_capabilities"})
                emit_command(sock, reader, {
                    "execute": "screendump",
                    "arguments": {"filename": str(output)}
                })
        if not verified_ppm_test_frame(output):
            return None if fingerprint else False
        if fingerprint:
            # Read only the already size-bounded, structurally verified frame.
            with output.open("rb") as frame:
                return hashlib.file_digest(frame, "sha256").hexdigest()
        return True
    except (OSError, QMPError):
        return None if fingerprint else False
    finally:
        # A native login image can show private user/account details;
        # screenshots remain only in ephemeral private host temp storage.
        output.unlink(missing_ok=True)


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
            _hold_keys(sock, reader, ("ctrl", "alt", "f2"))


def open_user_terminal(socket_path: Path, *, timeout=5.0):
    """Navigate to the fixed non-root terminal tile inside an active desktop."""
    return send_keys(socket_path, ("right", "right", "right", "ret", "ret"),
                     timeout=timeout)


def open_user_files(socket_path: Path, *, timeout=8.0):
    """Open the sixth tile in the authenticated UID1000 home dashboard.

    The test sends only five Right keys and Enter; no arbitrary paths,
    file names, shell input, or privileged instructions cross this channel.
    """
    return send_keys(socket_path, ("right",) * 5 + ("ret",), timeout=timeout)


def exit_user_terminal(socket_path: Path, *, timeout=5.0):
    """Issue only the literal 'exit' built-in, never arbitrary shell input."""
    return send_keys(socket_path, ("e", "x", "i", "t", "ret"),
                     timeout=timeout, allowed=ALLOWED_TERMINAL_RETURN)


def logout_user_desktop(socket_path: Path, *, timeout=5.0):
    """Exit the authenticated UID1000 desktop with a single Escape key.

    No arbitrary shell text or command injection is possible through this
    test operation; the guest must be on the top-level user dashboard.
    """
    return send_keys(socket_path, ("esc",), timeout=timeout)
