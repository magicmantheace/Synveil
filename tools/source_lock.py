#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Materialize and verify Synveil's pinned bootstrap sources.

Official release archives are the default transport. A source may also define
an exact GitHub commit fallback for build environments that cannot reach the
upstream archive host. Both transports are pinned independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "build" / "manifests" / "sources.json"
CACHE = Path(os.environ.get("SYNVEIL_SOURCE_CACHE", ROOT / ".cache" / "sources"))
TRANSPORT = os.environ.get("SYNVEIL_SOURCE_TRANSPORT", "auto").lower()
VALID_TRANSPORTS = {"auto", "archive", "git"}


def load_lock() -> dict:
    with LOCK.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != "synveil.sources/v1":
        raise SystemExit(f"unsupported source lock schema: {data.get('schema')!r}")
    if TRANSPORT not in VALID_TRANSPORTS:
        raise SystemExit(
            "SYNVEIL_SOURCE_TRANSPORT must be one of: "
            + ", ".join(sorted(VALID_TRANSPORTS))
        )
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


def archive_path(spec: dict) -> Path:
    return CACHE / "archives" / spec["filename"]


def git_path(name: str, spec: dict) -> Path:
    fallback = spec["git_fallback"]
    return CACHE / "git" / f"{name}-{fallback['commit'][:16]}"


def verify_archive(spec: dict, path: Path) -> None:
    expected = spec["digest"]["value"].lower()
    algorithm = spec["digest"]["algorithm"].lower()
    actual = digest_file(path, algorithm)
    if actual != expected:
        raise SystemExit(
            f"digest mismatch for {path.name}: "
            f"expected {algorithm}:{expected}, got {actual}"
        )


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def verify_git(name: str, spec: dict, path: Path) -> None:
    fallback = spec.get("git_fallback")
    if not fallback:
        raise SystemExit(f"{name}: no Git fallback is defined")
    if not path.is_dir() or not (path / ".git").exists():
        raise SystemExit(f"{name}: missing cached Git checkout: {path}")
    try:
        actual = git_output("-C", str(path), "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"{name}: could not inspect cached Git checkout: {exc}") from exc
    expected = fallback["commit"].lower()
    if actual.lower() != expected:
        raise SystemExit(
            f"{name}: cached Git revision mismatch: expected {expected}, got {actual}"
        )


def fetch_archive(name: str, spec: dict) -> Path:
    destination = archive_path(spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_archive(spec, destination)
        print(
            f"[source] verified cached archive {name} {spec['version']}",
            file=sys.stderr,
        )
        return destination

    fd, tmp_name = tempfile.mkstemp(
        prefix=spec["filename"] + ".",
        suffix=".part",
        dir=destination.parent,
    )
    os.close(fd)
    tmp = Path(tmp_name)
    request = urllib.request.Request(
        spec["url"],
        headers={
            "User-Agent": (
                "Synveil-bootstrap/0.1 "
                "(+https://github.com/magicmantheace/Synveil)"
            )
        },
    )
    try:
        print(
            f"[source] downloading upstream archive {name} {spec['version']}",
            file=sys.stderr,
        )
        with urllib.request.urlopen(request, timeout=60) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        verify_archive(spec, tmp)
        tmp.replace(destination)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return destination


def fetch_git(name: str, spec: dict) -> Path:
    fallback = spec.get("git_fallback")
    if not fallback:
        raise SystemExit(f"{name}: no Git fallback is defined")

    destination = git_path(name, spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        verify_git(name, spec, destination)
        print(
            f"[source] verified cached Git fallback {name} "
            f"{fallback['commit'][:12]}",
            file=sys.stderr,
        )
        return destination

    tmp = Path(
        tempfile.mkdtemp(
            prefix=f".{name}-",
            suffix=".git-part",
            dir=destination.parent,
        )
    )
    try:
        print(
            f"[source] fetching Git fallback {name} {spec['version']} "
            f"at {fallback['commit'][:12]}",
            file=sys.stderr,
        )
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        subprocess.run(
            ["git", "-C", str(tmp), "remote", "add", "origin", fallback["repository"]],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp),
                "fetch",
                "-q",
                "--depth=1",
                "--no-tags",
                "origin",
                fallback["ref"],
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp), "checkout", "-q", "--detach", "FETCH_HEAD"],
            check=True,
        )
        verify_git(name, spec, tmp)
        tmp.replace(destination)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return destination


def materialize(name: str, spec: dict) -> Path:
    if TRANSPORT == "archive":
        return fetch_archive(name, spec)
    if TRANSPORT == "git":
        return fetch_git(name, spec)

    archive = archive_path(spec)
    if archive.exists():
        verify_archive(spec, archive)
        return archive

    checkout = git_path(name, spec) if spec.get("git_fallback") else None
    if checkout and checkout.exists():
        verify_git(name, spec, checkout)
        return checkout

    try:
        return fetch_archive(name, spec)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if not spec.get("git_fallback"):
            raise
        print(
            f"[source] upstream transport unavailable for {name}: {exc}; "
            "using pinned Git fallback",
            file=sys.stderr,
        )
        return fetch_git(name, spec)


def verify_materialized(name: str, spec: dict) -> Path:
    if TRANSPORT == "archive":
        path = archive_path(spec)
        if not path.exists():
            raise SystemExit(f"missing cached archive: {path}")
        verify_archive(spec, path)
        return path

    if TRANSPORT == "git":
        path = git_path(name, spec)
        verify_git(name, spec, path)
        return path

    archive = archive_path(spec)
    if archive.exists():
        verify_archive(spec, archive)
        return archive
    if spec.get("git_fallback"):
        path = git_path(name, spec)
        if path.exists():
            verify_git(name, spec, path)
            return path
    raise SystemExit(f"{name}: no cached source is available to verify")


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
        print(materialize(args.name, source(data, args.name)))
        return 0

    names = args.names or sorted(data["sources"])
    for name in names:
        spec = source(data, name)
        if args.command == "fetch":
            materialize(name, spec)
        else:
            verified = verify_materialized(name, spec)
            print(
                f"[source] verified {name} {spec['version']}: {verified}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
