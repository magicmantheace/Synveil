#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Read-only A/B boot planning; never access physical devices or write state."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import uuid

SCHEMA = "synveil.install-layout/v1"
MAX_BYTES = 8192
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
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        decision = plan_boot(read_layout(args.manifest))
    except (OSError, LayoutError) as error:
        parser.error(str(error))
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
