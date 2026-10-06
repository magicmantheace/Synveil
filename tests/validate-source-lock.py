#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Validate invariants of the pinned Phase 1 source lock."""

from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "build" / "manifests" / "sources.json"
REQUIRED = {"linux", "binutils", "gcc", "glibc", "busybox"}
DIGEST_LENGTH = {"sha256": 64, "sha512": 128}


def fail(message: str) -> None:
    raise SystemExit(f"[source-lock] {message}")


def main() -> int:
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    if data.get("schema") != "synveil.sources/v1":
        fail("unexpected schema")

    sources = data.get("sources")
    if not isinstance(sources, dict):
        fail("sources must be an object")

    missing = REQUIRED - sources.keys()
    if missing:
        fail("missing required sources: " + ", ".join(sorted(missing)))

    for name, spec in sorted(sources.items()):
        if not isinstance(spec, dict):
            fail(f"{name}: source definition must be an object")
        for field in ("version", "filename", "url", "digest"):
            if not spec.get(field):
                fail(f"{name}: missing {field}")

        parsed = urlparse(spec["url"])
        if parsed.scheme != "https" or not parsed.netloc:
            fail(f"{name}: source URL must be absolute HTTPS")

        if Path(parsed.path).name != spec["filename"]:
            fail(f"{name}: filename does not match URL basename")

        digest = spec["digest"]
        algorithm = str(digest.get("algorithm", "")).lower()
        value = str(digest.get("value", "")).lower()
        expected_length = DIGEST_LENGTH.get(algorithm)
        if expected_length is None:
            fail(f"{name}: unsupported digest algorithm {algorithm!r}")
        if len(value) != expected_length or not re.fullmatch(r"[0-9a-f]+", value):
            fail(f"{name}: malformed {algorithm} digest")

    print(f"[source-lock] validated {len(sources)} pinned sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
