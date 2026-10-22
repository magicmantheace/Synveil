#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""VM-only signed A/B trial updates to a pre-existing *regular-file* GPT disk.

No physical-device paths, mounting, formatting, firmware, or boot flags.
Release public keys in the fixture directory are explicitly not production
trust anchors. A/B guest boot, true power-cut durability, and service health
are NOT verified by this host-only implementation.
"""
import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import uuid
import struct
import subprocess
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from ab_image_prototype import EFI_TYPE, LINUX_TYPE, NAME, SECTOR  # noqa: E402

MAX_JSON = 8192
MAX_RELEASE = 64 * 1024 * 1024
CONTROL_SCHEMA = "synveil.vm-ab-control/v1"
RELEASE_SCHEMA = "synveil.vm-ab-release/v1"
HEADER = struct.Struct("<8sIIIIQQQQ16sQIII")
ENTRY = struct.Struct("<16s16sQQQ72s")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class UpdateError(ValueError):
    pass


class InterruptedTrial(UpdateError):
    """Injected *host fixture only* interruption after a selected durable cut."""


def regular_fd(path, *, writable=False, max_bytes=None):
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_size < 1 or (max_bytes is not None and info.st_size > max_bytes)):
        os.close(fd)
        raise UpdateError("fixture input must be a bounded single-link regular file")
    return fd


def read_small(path):
    fd = regular_fd(path, max_bytes=MAX_JSON)
    try:
        with os.fdopen(fd, "rb") as inp:
            return inp.read(MAX_JSON + 1)
    except Exception:
        raise


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("duplicate metadata field")
        result[key] = value
    return result


def json_small(path):
    try:
        return json.loads(read_small(path), object_pairs_hook=pairs_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("unreadable metadata") from exc


def digest_valid(s):
    return isinstance(s, str) and HEX.fullmatch(s) is not None


def _header(fd, at, sectors):
    raw = os.pread(fd, SECTOR, at * SECTOR)
    if len(raw) != SECTOR or raw[:8] != b"EFI PART":
        raise UpdateError("missing GPT header")
    h = HEADER.unpack(raw[:HEADER.size])
    if (h[1] != 0x10000 or h[2] != 92 or h[5] != at or
            h[6] != (sectors - 1 if at == 1 else 1) or
            h[11] != 128 or h[12] != 128 or
            h[10] != (2 if at == 1 else sectors - 33) or
            zlib.crc32(raw[:16] + b"\0" * 4 + raw[20:92]) & 0xffffffff != h[3]):
        raise UpdateError("invalid GPT header or CRC")
    entries = os.pread(fd, 128 * 128, h[10] * SECTOR)
    if len(entries) != 128 * 128 or zlib.crc32(entries) & 0xffffffff != h[13]:
        raise UpdateError("corrupt GPT partition table")
    return h, entries


def verified_layout(fd, meta):
    if (not isinstance(meta, dict) or meta.get("schema") != "synveil.file-only-ab-gpt/v1" or
            meta.get("bootable") is not False or meta.get("physical_device_accessed") is not False or
            type(meta.get("total_bytes")) is not int or
            meta["total_bytes"] != os.fstat(fd).st_size or
            meta["total_bytes"] % SECTOR or
            not isinstance(meta.get("partitions"), list) or len(meta["partitions"]) != 6):
        raise UpdateError("unsafe or incomplete GPT fixture metadata")
    sectors = meta["total_bytes"] // SECTOR
    if sectors < 20480:
        raise UpdateError("too small GPT fixture")
    primary, entries = _header(fd, 1, sectors)
    backup, back_entries = _header(fd, sectors - 1, sectors)
    if (entries != back_entries or primary[9] != backup[9] or
            primary[7:9] != backup[7:9] or
            str(uuid.UUID(bytes_le=primary[9])) != meta.get("disk_guid")):
        raise UpdateError("GPT copies or disk GUID disagree")
    mbr = os.pread(fd, SECTOR, 0)
    if len(mbr) != SECTOR or mbr[510:] != b"\x55\xaa" or mbr[450] != 0xee:
        raise UpdateError("missing protective MBR")
    result = {}
    observed_guids = set()
    for i, (name, record) in enumerate(zip(("ESP", "SYSTEM_A", "SYSTEM_B", "STATE", "DATA", "RECOVERY"), meta["partitions"])):
        row = ENTRY.unpack_from(entries, i * ENTRY.size)
        try:
            disk_name = row[5].decode("utf-16le").rstrip("\0")
            part_guid = str(uuid.UUID(bytes_le=row[1]))
            part_type = uuid.UUID(bytes_le=row[0])
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("invalid GPT partition entry") from exc
        if (not isinstance(record, dict) or disk_name != name or record.get("name") != name or
                part_guid != record.get("partuuid") or row[2] != record.get("first_lba") or
                row[3] != record.get("last_lba") or row[2] > row[3] or
                row[3] > primary[8] or row[2] < primary[7] or
                part_type != (EFI_TYPE if name == "ESP" else LINUX_TYPE)):
            raise UpdateError("GPT identity/bounds do not match pinned fixture")
        if part_guid in observed_guids:
            raise UpdateError("duplicated GPT partition GUID")
        observed_guids.add(part_guid)
        result[name] = (row[2] * SECTOR, (row[3] - row[2] + 1) * SECTOR)
    intervals = sorted((offset, offset + length) for offset, length in result.values())
    if any(intervals[i][1] > intervals[i + 1][0] for i in range(5)):
        raise UpdateError("overlapping GPT partitions")
    return result


def validate_control(c, slots):
    if not isinstance(c, dict) or set(c) != {"schema", "confirmed", "trial"} or c["schema"] != CONTROL_SCHEMA:
        raise UpdateError("invalid control schema")
    confirmed = c["confirmed"]
    if not isinstance(confirmed, dict) or set(confirmed) != {"slot", "version", "bytes", "sha256"}:
        raise UpdateError("invalid confirmed slot")
    if (confirmed["slot"] not in ("A", "B") or
            type(confirmed["version"]) is not int or confirmed["version"] < 1 or
            type(confirmed["bytes"]) is not int or
            not 0 < confirmed["bytes"] <= slots["SYSTEM_" + confirmed["slot"]][1] or
            not digest_valid(confirmed["sha256"])):
        raise UpdateError("invalid confirmed slot identity")
    trial = c["trial"]
    if trial is not None:
        if not isinstance(trial, dict) or set(trial) != {"slot", "version", "bytes", "sha256", "attempts", "awaiting_health"}:
            raise UpdateError("invalid trial slot")
        if (trial["slot"] != ("B" if confirmed["slot"] == "A" else "A") or
                type(trial["version"]) is not int or trial["version"] <= confirmed["version"] or
                type(trial["bytes"]) is not int or
                not 0 < trial["bytes"] <= slots["SYSTEM_" + trial["slot"]][1] or
                not digest_valid(trial["sha256"]) or
                type(trial["attempts"]) is not int or not 0 <= trial["attempts"] <= 3 or
                type(trial["awaiting_health"]) is not bool):
            raise UpdateError("invalid trial slot metadata")
    return c


def check_region(fd, region, record):
    offset, capacity = region
    length = record["bytes"]
    if length > capacity:
        raise UpdateError("payload exceeds partition")
    hasher = hashlib.sha256()
    for cursor in range(0, length, 1024 * 1024):
        chunk = os.pread(fd, min(1024 * 1024, length - cursor), offset + cursor)
        if not chunk:
            raise UpdateError("short system partition read")
        hasher.update(chunk)
    return hmac.compare_digest(hasher.hexdigest(), record["sha256"])


def discard_stale_temporary(path):
    """Ignore an incomplete journal replacement, never trust its contents."""
    temporary = path.with_name(path.name + ".next")
    try:
        info = temporary.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UpdateError("unsafe interrupted control temporary")
    temporary.unlink()


def atomic_control(path, state):
    discard_stale_temporary(path)
    data = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(data) > MAX_JSON:
        raise UpdateError("control metadata too large")
    temporary = path.with_name(path.name + ".next")
    fd = os.open(temporary, os.O_WRONLY | os.O_EXCL | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def signed_release(directory, name):
    manifest = directory / (name + ".release.json")
    signature = directory / (name + ".release.sig")
    public = directory / (name + ".fixture-trust.pem")
    payload = directory / (name + ".release.bin")
    raw = read_small(manifest)
    data = json.loads(raw, object_pairs_hook=pairs_unique)
    if (not isinstance(data, dict) or set(data) != {"schema", "version", "bytes", "sha256"} or
            data["schema"] != RELEASE_SCHEMA or type(data["version"]) is not int or
            data["version"] < 1 or type(data["bytes"]) is not int or
            not 0 < data["bytes"] <= MAX_RELEASE or not digest_valid(data["sha256"])):
        raise UpdateError("invalid release manifest")
    # All four paths must be single-link regular files, not devices or FIFOs.
    for path, bound in ((signature, 256), (public, 8192)):
        fd = regular_fd(path, max_bytes=bound)
        os.close(fd)
    release_fd = regular_fd(payload, max_bytes=MAX_RELEASE)
    try:
        if os.fstat(release_fd).st_size != data["bytes"]:
            raise UpdateError("release byte count mismatch")
        hasher = hashlib.sha256()
        with os.fdopen(os.dup(release_fd), "rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
        if not hmac.compare_digest(hasher.hexdigest(), data["sha256"]):
            raise UpdateError("release payload hash mismatch")
        try:
            verify = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public),
                 "-rawin", "-in", str(manifest), "-sigfile", str(signature)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError("signature verifier unavailable") from exc
        if verify.returncode:
            raise UpdateError("release signature verification failed")
        return data, release_fd
    except BaseException:
        os.close(release_fd)
        raise


def operate(directory, name, action, *, interrupt=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise UpdateError("invalid fixture name")
    if not directory.is_dir() or directory.is_symlink() or directory.resolve() != directory.absolute():
        raise UpdateError("unsafe fixture directory")
    # Restrict every operation to this explicitly named pre-existing image.
    disk = directory / (name + ".raw")
    layout = directory / (name + ".layout.json")
    control_path = directory / (name + ".control.json")
    fd = regular_fd(disk, writable=action in ("initialize-fixture", "stage"))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        slots = verified_layout(fd, json_small(layout))
        if action == "initialize-fixture":
            # Dev-only bootstrap: an unsigned test baseline is NOT an owner OS.
            if control_path.exists() or control_path.is_symlink():
                raise UpdateError("fixture already initialized")
            source = regular_fd(directory / (name + ".base.bin"),
                                max_bytes=MAX_RELEASE)
            try:
                length = os.fstat(source).st_size
                off, capacity = slots["SYSTEM_A"]
                if length > capacity:
                    raise UpdateError("fixture baseline exceeds A slot")
                if os.pread(fd, min(length, 65536), off).strip(b"\0"):
                    raise UpdateError("refusing nonempty uninitialized slot A")
                digest = hashlib.sha256()
                pos = 0
                while pos < length:
                    chunk = os.pread(source, min(1024 * 1024, length - pos), pos)
                    if not chunk:
                        raise UpdateError("fixture baseline read shortened")
                    digest.update(chunk)
                    written = 0
                    while written < len(chunk):
                        n = os.pwrite(fd, chunk[written:], off + pos + written)
                        if n <= 0:
                            raise UpdateError("short initial slot write")
                        written += n
                    pos += len(chunk)
                os.fsync(fd)
                baseline = {"slot": "A", "version": 1,
                            "bytes": length, "sha256": digest.hexdigest()}
                if not check_region(fd, (off, capacity), baseline):
                    raise UpdateError("baseline byte verification failed")
                atomic_control(control_path, {"schema": CONTROL_SCHEMA,
                                              "confirmed": baseline, "trial": None})
                return {"fixture_initialized": True, "confirmed_slot": "A",
                        "bootable": False}
            finally:
                os.close(source)
        state = validate_control(json_small(control_path), slots)
        confirmed = state["confirmed"]
        if not check_region(fd, slots["SYSTEM_" + confirmed["slot"]], confirmed):
            raise UpdateError("confirmed system slot corruption: refusing operation")
        if action == "stage":
            if state["trial"] is not None:
                raise UpdateError("trial already pending; decide or roll back before staging")
            release, release_fd = signed_release(directory, name)
            try:
                if release["version"] <= confirmed["version"]:
                    raise UpdateError("release version rollback refused")
                inactive = "B" if confirmed["slot"] == "A" else "A"
                offset, capacity = slots["SYSTEM_" + inactive]
                if release["bytes"] > capacity:
                    raise UpdateError("signed release exceeds inactive slot")
                cursor = 0
                while cursor < release["bytes"]:
                    chunk = os.pread(release_fd, min(1024 * 1024, release["bytes"] - cursor), cursor)
                    if not chunk:
                        raise UpdateError("release file changed during staging")
                    done = 0
                    while done < len(chunk):
                        count = os.pwrite(fd, chunk[done:], offset + cursor + done)
                        if count <= 0:
                            raise UpdateError("short inactive-slot write")
                        done += count
                    cursor += len(chunk)
                    if interrupt == "during-copy":
                        os.fsync(fd)
                        raise InterruptedTrial("interrupted after partial inactive slot write")
                os.fsync(fd)
                if interrupt == "after-slot-sync":
                    raise InterruptedTrial("interrupted before candidate activation")
                if not check_region(fd, (offset, capacity), release):
                    raise UpdateError("inactive-slot write verification failed")
                state["trial"] = {**release, "slot": inactive, "attempts": 3,
                                  "awaiting_health": False}
                del state["trial"]["schema"]
                atomic_control(control_path, state)
                if interrupt == "after-state-sync":
                    raise InterruptedTrial("interrupted after candidate record persisted")
                return {"staged_slot": inactive, "trial_attempts": 3}
            finally:
                os.close(release_fd)
        if action == "boot-trial":
            trial = state["trial"]
            if trial is None:
                return {"selected_slot": confirmed["slot"], "reason": "confirmed"}
            if trial["attempts"] == 0 or not check_region(fd, slots["SYSTEM_" + trial["slot"]], trial):
                state["trial"] = None
                atomic_control(control_path, state)
                return {"selected_slot": confirmed["slot"], "reason": "fallback"}
            trial["attempts"] -= 1
            trial["awaiting_health"] = True
            atomic_control(control_path, state)  # Durable attempt consumed BEFORE boot.
            return {"selected_slot": trial["slot"], "reason": "trial", "remaining": trial["attempts"]}
        if action == "confirm":
            trial = state["trial"]
            if trial is None or not trial["awaiting_health"]:
                raise UpdateError("cannot confirm without recorded trial boot")
            if not check_region(fd, slots["SYSTEM_" + trial["slot"]], trial):
                raise UpdateError("cannot confirm corrupted candidate")
            state["confirmed"] = {k: trial[k] for k in ("slot", "version", "bytes", "sha256")}
            state["trial"] = None
            atomic_control(control_path, state)
            return {"confirmed_slot": state["confirmed"]["slot"], "note": "operator-fixture-only, no guest health evidence"}
        if action == "rollback":
            state["trial"] = None
            atomic_control(control_path, state)
            return {"selected_slot": confirmed["slot"], "reason": "manual-fixture-rollback"}
        raise UpdateError("unknown action")
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--action", required=True, choices=("initialize-fixture", "stage", "boot-trial", "confirm", "rollback"))
    args = parser.parse_args()
    if (ROOT / "out").is_symlink():
        parser.error("unsafe output parent")
    try:
        result = operate(ROOT / "out/install-prototype", args.name, args.action)
    except (UpdateError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({**result, "guest_boot_verified": False,
                      "production_signature_authority": False,
                      "physical_device_written": False, "efi_boot_selection_changed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
