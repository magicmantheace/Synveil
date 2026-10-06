#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Fetch and verify Synveil's pinned bootstrap source archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "build" / "manifests" / "sources.json"
CACHE = Path(os.environ.get("SYNVEIL_SOURCE_CACHE", ROOT / ".cache" / "sources"))


def load_lock() -> dict:
    with LOCK.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != "synveil.sources/v1":
        raise SystemExit(f"unsupported source lock schema: {data.get('schema')!r}")
    return data


def source(data: dict, name: str) -> dict:
    try:
        return data["sources"][name]
    except KeyError:
        valid = ", ".join(sorted(data["sources"]))
        raise SystemExit(f"unknown source {name!r}; valid names: {valid}")


def digest_file(path: Path, algorithm: str) -> str:
    try:
        h = hashlib.new(algorithm)
    except ValueError as exc:
        raise SystemExit(f"unsupported digest algorithm {algorithm!r}") from exc
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_one(spec: dict, path: Path) -> None:
    expected = spec["digest"]["value"].lower()
    algorithm = spec["digest"]["algorithm"].lower()
    actual = digest_file(path, algorithm)
    if actual != expected:
        raise SystemExit(
            f"digest mismatch for {path.name}: expected {algorithm}:{expected}, got {actual}"
        )


def fetch_one(name: str, spec: dict) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    destination = CACHE / spec["filename"]
    if destination.exists():
        verify_one(spec, destination)
        print(f"[source] verified cached {name} {spec['version']}", file=sys.stderr)
        return destination

    fd, tmp_name = tempfile.mkstemp(prefix=spec["filename"] + ".", suffix=".part", dir=CACHE)
    os.close(fd)
    tmp = Path(tmp_name)
    request = urllib.request.Request(
        spec["url"],
        headers={"User-Agent": "Synveil-bootstrap/0.1 (+https://github.com/magicmantheace/Synveil)"},
    )
    try:
        print(f"[source] downloading {name} {spec['version']}", file=sys.stderr)
        with urllib.request.urlopen(request) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        verify_one(spec, tmp)
        tmp.replace(destination)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch")
    fetch.add_argument("names", nargs="*")

    verify = sub.add_parser("verify")
    verify.add_argument("names", nargs="*")

    path = sub.add_parser("path")
    path.add_argument("name")

    version = sub.add_parser("version")
    version.add_argument("name")

    sub.add_parser("dump")

    args = parser.parse_args()
    data = load_lock()

    if args.command == "dump":
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if args.command == "version":
        print(source(data, args.name)["version"])
        return 0

    if args.command == "path":
        spec = source(data, args.name)
        destination = CACHE / spec["filename"]
        if not destination.exists():
            destination = fetch_one(args.name, spec)
        else:
            verify_one(spec, destination)
        print(destination)
        return 0

    names = args.names or sorted(data["sources"])
    for name in names:
        spec = source(data, name)
        destination = CACHE / spec["filename"]
        if args.command == "fetch":
            fetch_one(name, spec)
        else:
            if not destination.exists():
                raise SystemExit(f"missing cached source: {destination}")
            verify_one(spec, destination)
            print(f"[source] verified {name} {spec['version']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
