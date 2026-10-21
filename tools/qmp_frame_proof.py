#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bounded QMP RGB framebuffer proof for disposable native graphical VMs.

This is not a production compositor, screenshot service, or secret-capture
API. Accept a private QMP socket, refuse pre-existing output paths, require
a complete real P6 image and visible differences beyond a blank frame.
"""
from pathlib import Path
import os
import re
import socket
import stat

from qmp_input import QMPError, _read_reply, emit_command

MAX_IMAGE = 64 * 1024 * 1024
MAX_PIXELS = 4096 * 2160
MIN_DIFFERENT_PIXELS = 200


def validate_ppm(data: bytes) -> tuple[int, int]:
    """Validate one untruncated 24-bit PPM with meaningful nonblank content.

    QEMU screendump emits P6 format. Reject comments, malformed metadata,
    pathological dimensions, trailing payload and uniformly filled screens.
    """
    if not isinstance(data, bytes) or len(data) < 15 or len(data) > MAX_IMAGE:
        raise ValueError("invalid QMP framebuffer length")
    match = re.match(rb"\AP6\n([1-9][0-9]*) ([1-9][0-9]*)\n255\n", data)
    if match is None:
        raise ValueError("invalid 24-bit binary QMP PPM header")
    width = int(match.group(1))
    height = int(match.group(2))
    pixels = width * height
    if not 320 <= width <= 4096 or not 240 <= height <= 2160 or pixels > MAX_PIXELS:
        raise ValueError("invalid QMP framebuffer dimensions")
    offset = match.end()
    if len(data) != offset + pixels * 3:
        raise ValueError("incomplete or trailing QMP RGB data")
    # Nonuniform, nonblack signal: require at least a small genuine region
    # changed from a single repeated background color. Sampling a bounded
    # number of pixels avoids expensive whole-frame allocations.
    image = memoryview(data)[offset:]
    first = bytes(image[:3])
    distinct = 0
    nonblack = 0
    stride = max(1, pixels // 8192)
    for p in range(0, pixels, stride):
        rgb = bytes(image[p * 3:p * 3 + 3])
        if rgb != first:
            distinct += 1
        if any(rgb):
            nonblack += 1
    if distinct < MIN_DIFFERENT_PIXELS or nonblack < MIN_DIFFERENT_PIXELS:
        raise ValueError("QEMU framebuffer blank or near-uniform")
    return width, height


def capture_guest_frame(socket_path: Path, screenshot: Path) -> tuple[int, int]:
    """Obtain one QMP screendump, validate it, and leave no bad screenshot.

    Guest capture is successful only after QEMU actually writes a new image.
    The path is caller-provided private VM test evidence, never a device.
    """
    socket_path = Path(socket_path)
    screenshot = Path(screenshot)
    if not socket_path.is_absolute() or not screenshot.is_absolute():
        raise ValueError("QMP socket and screenshot must use absolute paths")
    if screenshot.suffix != ".ppm" or not screenshot.parent.is_dir() or \
            screenshot.parent.is_symlink() or screenshot.exists() or screenshot.is_symlink():
        raise ValueError("QMP screenshot must be a fresh private PPM path")
    info = screenshot.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("QMP screenshot directory must be private")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(8)
            sock.connect(str(socket_path))
            with sock.makefile("rb") as reader:
                _read_reply(reader, greeting=True)
                emit_command(sock, reader, {"execute": "qmp_capabilities"})
                emit_command(sock, reader, {
                    "execute": "screendump",
                    "arguments": {"filename": str(screenshot)}
                })
        if screenshot.is_symlink() or not screenshot.is_file():
            raise ValueError("QMP did not produce a regular framebuffer")
        saved = screenshot.stat()
        if (not stat.S_ISREG(saved.st_mode) or saved.st_nlink != 1 or
                saved.st_size > MAX_IMAGE):
            raise ValueError("unsafe QMP framebuffer output")
        dimensions = validate_ppm(screenshot.read_bytes())
        os.chmod(screenshot, 0o600)
        return dimensions
    except (OSError, QMPError, ValueError):
        # Evidence must never be considered successful after failure.
        if screenshot.is_file() and not screenshot.is_symlink():
            screenshot.unlink(missing_ok=True)
        raise
