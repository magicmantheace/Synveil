#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Write a machine-readable manifest for the current Synveil build."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
LOCK = ROOT / "build" / "manifests" / "sources.json"
VERSION = ROOT / "VERSION"


def run(*args: str) -> str | None:
    try:
        return subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    with LOCK.open("r", encoding="utf-8") as fh:
        sources = json.load(fh)

    artifacts = {}
    for name in (
        "synveil-x86_64.efi",
        "synveil-x86_64.img",
        "synveil-initramfs.cpio",
        "synveil-initramfs.cpio.zst",
        "synveil-rootfs.tar.zst",
    ):
        path = OUT / name
        if path.is_file():
            artifacts[name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }

    git_sha = run("git", "rev-parse", "HEAD")
    dirty = run("git", "status", "--porcelain")
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        epoch = run("git", "show", "-s", "--format=%ct", "HEAD")

    manifest = {
        "schema": "synveil.build/v1",
        "version": VERSION.read_text(encoding="utf-8").strip(),
        "target": os.environ.get("SYNVEIL_TARGET", "x86_64-synveil-linux-gnu"),
        "git": {
            "commit": git_sha,
            "dirty": bool(dirty) if dirty is not None else None,
        },
        "build": {
            "source_date_epoch": int(epoch) if epoch and epoch.isdigit() else None,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "host": {
                "system": platform.system(),
                "machine": platform.machine(),
            },
        },
        "sources": sources["sources"],
        "artifacts": artifacts,
    }

    destination = OUT / "manifests" / "build.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
