#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Install a current, audited native bundle into a Synveil rootfs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from native_preflight import ROOT, PIN, run


def verify_bundle(bundle: Path, commit: str, cargo_lock: Path, pin: dict) -> tuple[dict, dict]:
    manifest = json.loads((bundle / "build.json").read_text())
    if manifest.get("schema") != "synveil.native-build/v1":
        raise ValueError("unsupported native build manifest")
    if manifest.get("git_commit") != commit or manifest.get("git_dirty") is not False:
        raise ValueError("native bundle must come from the current clean Git revision")
    if manifest.get("cargo_lock_sha256") != hashlib.sha256(cargo_lock.read_bytes()).hexdigest():
        raise ValueError("native dependency lock differs from the current checkout")
    source = manifest.get("rust", {}).get("source_verification", {})
    if source.get("sha256") != pin["rust_src"]["sha256"] or source.get("verified_files", 0) < 1:
        raise ValueError("native bundle lacks matching Rust source verification")
    if set(manifest.get("artifacts", {})) != {"veil-core", "synctl"}:
        raise ValueError("native bundle must contain both core and CLI artifacts")
    contents = {}
    for name, artifact in manifest["artifacts"].items():
        binary = bundle / name
        if binary.is_symlink():
            raise ValueError("native bundle binary cannot be a symlink")
        data = binary.read_bytes()
        if len(data) != artifact.get("bytes") or hashlib.sha256(data).hexdigest() != artifact.get("sha256"):
            raise ValueError("native binary differs from its audited manifest: " + name)
        if not artifact.get("link_inputs") or not artifact.get("link_map_sha256"):
            raise ValueError("native bundle lacks target link audit evidence")
        contents[name] = data
    return manifest, contents


def install_bundle(bundle: Path, rootfs: Path, commit: str, cargo_lock: Path, pin: dict) -> None:
    manifest, contents = verify_bundle(bundle, commit, cargo_lock, pin)
    paths = {"veil-core": "usr/sbin/veil-core", "synctl": "usr/bin/synctl"}
    identity = rootfs / "usr/share/synveil/native-build.json"
    for path in [rootfs / target for target in paths.values()] + [identity]:
        if not path.resolve().is_relative_to(rootfs.resolve()) or path.is_symlink():
            raise ValueError("native rootfs destination escapes its root")
    for name, target in paths.items():
        path = rootfs / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents[name])
        path.chmod(0o755)
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text(json.dumps(manifest, indent=2) + "\n")
    identity.chmod(0o644)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--rootfs", type=Path)
    args = parser.parse_args()
    try:
        pin = json.loads(PIN.read_text())
        commit = run("git", "-C", str(ROOT), "rev-parse", "HEAD")
        if run("git", "-C", str(ROOT), "status", "--porcelain"):
            raise ValueError("native packaging requires a clean checkout")
        if args.rootfs is None:
            verify_bundle(args.bundle, commit, ROOT / "Cargo.lock", pin)
        else:
            install_bundle(args.bundle, args.rootfs, commit, ROOT / "Cargo.lock", pin)
        print("[native-rootfs] native bundle identity and artifact hashes verified")
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print("[native-rootfs] " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
