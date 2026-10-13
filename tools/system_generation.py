#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Read-only A/B boot planning; never access physical devices or write state."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import uuid

SCHEMA = "synveil.install-layout/v1"
MAX_BYTES = 8192
MAX_STAGED_IMAGE_BYTES = 16 * 1024 * 1024 * 1024
FIELDS = {"schema", "efi_partuuid", "slots", "state_partuuid",
          "data_partuuid", "confirmed_slot", "candidate_slot",
          "candidate_attempts_remaining"}


class LayoutError(ValueError):
    """Untrusted manifest cannot produce a boot decision."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LayoutError("duplicate JSON field")
        result[key] = value
    return result


def read_layout(path: Path) -> dict:
    # O_NOFOLLOW and fstat prevent a substituted symlink/device.
    # A FIFO must fail immediately instead of blocking before fstat.
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise LayoutError("layout must be a small regular file")
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise LayoutError("oversized system layout")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LayoutError("invalid JSON layout") from error
    return validate_layout(data)


def partuuid(value):
    if not isinstance(value, str):
        raise LayoutError("PARTUUID must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise LayoutError("invalid PARTUUID") from error
    if str(parsed) != value or parsed.int == 0:
        raise LayoutError("PARTUUID must be canonical lowercase and nonzero")
    return value


def validate_layout(data):
    if not isinstance(data, dict) or set(data) != FIELDS or data["schema"] != SCHEMA:
        raise LayoutError("missing, extra, or unsupported layout fields")
    slots = data["slots"]
    if not isinstance(slots, dict) or set(slots) != {"A", "B"}:
        raise LayoutError("exactly two system slots are required")
    identities = [partuuid(data[field]) for field in (
        "efi_partuuid", "state_partuuid", "data_partuuid")]
    for name in ("A", "B"):
        slot = slots[name]
        if not isinstance(slot, dict) or set(slot) != {"partuuid", "image_sha256"}:
            raise LayoutError("invalid system slot")
        identities.append(partuuid(slot["partuuid"]))
        digest = slot["image_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LayoutError("invalid system image digest")
    if len(identities) != len(set(identities)):
        raise LayoutError("partition identities must be unique")
    confirmed = data["confirmed_slot"]
    candidate = data["candidate_slot"]
    attempts = data["candidate_attempts_remaining"]
    if confirmed not in ("A", "B") or (
        candidate is not None and candidate not in ("A", "B")
    ) or candidate == confirmed:
        raise LayoutError("invalid boot slot roles")
    if type(attempts) is not int or not 0 <= attempts <= 3:
        raise LayoutError("invalid trial attempt count")
    if candidate is None and attempts != 0:
        raise LayoutError("attempts require a trial candidate")
    return data


def plan_boot(layout):
    # Advisory ONLY: no disk/image identity has actually been verified.
    data = validate_layout(layout)
    confirmed = data["confirmed_slot"]
    candidate = data["candidate_slot"]
    remaining = data["candidate_attempts_remaining"]
    if candidate is not None and remaining:
        selected, reason, after = candidate, "candidate_trial", remaining - 1
    elif candidate is not None:
        selected, reason, after = confirmed, "candidate_exhausted", 0
    else:
        selected, reason, after = confirmed, "confirmed", 0
    return {
        "schema": "synveil.boot-decision/v1",
        "selected_slot": selected,
        "confirmed_slot": confirmed,
        "reason": reason,
        "proposed_attempts_remaining": after,
        "advisory_only": True,
        "image_verification_performed": False,
        "disk_mutations_performed": False,
    }


def staged_image_sha256(path: Path) -> str:
    """Hash a single explicitly named, non-linked regular *host file*.

    Never open block devices, pipes, symlinks, or directories. These bytes
    are staging artifacts, not trusted partition or signed-boot evidence.
    """
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                not 0 < info.st_size <= MAX_STAGED_IMAGE_BYTES):
            raise LayoutError("staged image must be a bounded unlinked regular file")
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise LayoutError("staged image truncated while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
        if source.read(1):
            raise LayoutError("staged image changed while hashing")
        after = os.fstat(source.fileno())
        if (after.st_size != info.st_size or
                after.st_mtime_ns != info.st_mtime_ns or
                after.st_ctime_ns != info.st_ctime_ns or
                after.st_ino != info.st_ino):
            raise LayoutError("staged image changed during verification")
        return digest.hexdigest()


def plan_staged_boot(layout: dict, images: dict) -> dict:
    """Recommend a verified *staged file*, never an actual boot target.

    The last confirmed image must be present and match before a trial or
    fallback can be suggested. A corrupt candidate falls back to the
    confirmed image, but only as an offline recommendation.
    """
    data = validate_layout(layout)
    if not isinstance(images, dict) or set(images) != {"A", "B"}:
        raise LayoutError("both staged A/B image paths required")
    matches = {}
    for slot in ("A", "B"):
        path = images[slot]
        if not isinstance(path, Path):
            raise LayoutError("staged image path must be a Path")
        observed = staged_image_sha256(path)
        expected = data["slots"][slot]["image_sha256"]
        matches[slot] = hmac.compare_digest(observed, expected)
    confirmed = data["confirmed_slot"]
    if not matches[confirmed]:
        raise LayoutError("confirmed system image failed staged hash verification")
    result = plan_boot(data)
    selected = result["selected_slot"]
    if not matches[selected]:
        result["selected_slot"] = confirmed
        result["reason"] = "candidate_staged_image_mismatch"
        result["proposed_attempts_remaining"] = 0
    result.update({
        "staged_image_hashes_checked": True,
        "staged_image_matches": matches,
        "image_verification_performed": False,
        "boot_image_authenticity_verified": False,
        "partition_identity_verified": False,
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--staged-slot-a", type=Path)
    parser.add_argument("--staged-slot-b", type=Path)
    args = parser.parse_args()
    try:
        layout = read_layout(args.manifest)
        if (args.staged_slot_a is None) != (args.staged_slot_b is None):
            raise LayoutError("both staged slots required together")
        if args.staged_slot_a is not None:
            decision = plan_staged_boot(
                layout, {"A": args.staged_slot_a, "B": args.staged_slot_b}
            )
        else:
            decision = plan_boot(layout)
    except (OSError, LayoutError) as error:
        parser.error(str(error))
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
