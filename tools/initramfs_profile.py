#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Constrained read-only scanner for Synveil's uncompressed newc initramfs.

The Linux EFI kernel embeds this cpio stream (INITRAMFS_SOURCE). A staging
directory alone is not evidence of what was passed into the kernel build.
No extraction, symlink following, shell invocation, or file writes occur here.
"""
from pathlib import Path

MAGIC = b"070701"
HEADER_BYTES = 110
MAX_NAME_BYTES = 4096
MAX_ENTRY_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 200000


def scan_entries(path: Path):
    """Return immutable entry mode/owner metadata from a bounded newc archive."""
    entries = {}
    with path.open("rb") as stream:
        count = 0
        while count < MAX_ENTRIES:
            header = stream.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES or header[:6] != MAGIC:
                raise ValueError("malformed or truncated newc initramfs header")
            try:
                fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
            except ValueError as error:
                raise ValueError("invalid newc header field") from error
            filesize, namesize = fields[6], fields[11]
            if not 1 <= namesize <= MAX_NAME_BYTES or filesize > MAX_ENTRY_BYTES:
                raise ValueError("unsafe newc record length")
            raw_name = stream.read(namesize)
            if len(raw_name) != namesize or raw_name[-1:] != b"\0":
                raise ValueError("invalid newc entry terminator")
            try:
                name = raw_name[:-1].decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("non-UTF8 initramfs filename") from error
            padding = (-(HEADER_BYTES + namesize)) % 4
            if len(stream.read(padding)) != padding:
                raise ValueError("truncated newc name padding")
            if name == "TRAILER!!!":
                if filesize != 0:
                    raise ValueError("invalid newc trailer size")
                # cpio may pad to an archive block boundary, never permit an
                # unexpected second archive or nonzero trailing instructions.
                tail = stream.read(512)
                if len(tail) > 511 or any(tail):
                    raise ValueError("invalid padding after newc trailer")
                return entries
            while name.startswith("./"):
                name = name[2:]
            if (not name or name.startswith("/") or
                    ".." in name.split("/") or "\0" in name or
                    name in entries):
                raise ValueError("unsafe or duplicate initramfs path")
            entries[name] = {"mode": fields[1], "uid": fields[2], "gid": fields[3]}
            stream.seek(filesize + ((-filesize) % 4), 1)
            count += 1
    raise ValueError("missing newc trailer or excessive entries")


def scan_names(path: Path):
    """Return entry names from one bounded newc archive, or fail closed."""
    return set(scan_entries(path))


def inspect_authenticated_archive(path: Path):
    """Only the locked-serial, first-owner QEMU guest can be packaged.

    File metadata is taken from the actual cpio payload, not the rootfs
    staging tree. No symlink, missing marker or debug override is permitted.
    """
    records = scan_entries(path)
    required = {
        "init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
        "usr/sbin/veil-enroll", "usr/sbin/veil-seat",
        "etc/synveil/auth-desktop", "etc/synveil/desktop-autostart",
    }
    if not required <= records.keys():
        raise ValueError("embedded authenticated initramfs missing required components")
    if "etc/synveil/developer-serial-recovery" in records:
        raise ValueError("embedded authenticated initramfs enables root serial recovery")
    for marker in ("etc/synveil/auth-desktop", "etc/synveil/desktop-autostart"):
        value = records[marker]
        if (value["mode"] & 0o177777) != 0o100644 or
                value["uid"] != 0 or value["gid"] != 0):
            raise ValueError("unsafe embedded authenticated marker owner or mode")
    return records


def inspect_demo_archive(path: Path):
    """An ordinary developer bundle must never contain auth/debug markers."""
    entries = scan_names(path)
    forbidden = {
        "etc/synveil/auth-desktop",
        "etc/synveil/developer-serial-recovery",
    }
    if forbidden & entries:
        raise ValueError("authenticated preview/serial override in embedded initramfs")
    # Scan existence, not just authentication flags: a swapped archive may
    # omit its root startup or the intended developer launcher altogether.
    required = {
        "init", "usr/bin/synveil-display", "usr/bin/synveil-vt",
        "etc/synveil/desktop-autostart",
    }
    if not required <= entries:
        raise ValueError("embedded initramfs missing developer desktop components")
    return entries
