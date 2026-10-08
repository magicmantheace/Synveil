#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Check native target build prerequisites without installing or compiling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "build/manifests/rust-bootstrap.json"


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def verify_rust(identity: str, sysroot: Path, pin: dict) -> dict:
    fields = dict(line.split(": ", 1) for line in identity.splitlines() if ": " in line)
    if fields.get("release") != pin["rustc_release"]:
        raise ValueError("Rust release does not match the bootstrap pin")
    if not fields.get("commit-hash", "").startswith(pin["rustc_commit_prefix"]):
        raise ValueError("Rust compiler commit does not match the bootstrap pin")
    if fields.get("host") != pin["host"]:
        raise ValueError("native bootstrap currently requires an x86_64 GNU/Linux host")
    manifest = sysroot / "lib/rustlib/multirust-channel-manifest.toml"
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    source = data["pkg"]["rust-src"]["target"]["*"]
    if (source.get("xz_hash"), source.get("xz_url")) != (
        pin["rust_src"]["sha256"], pin["rust_src"]["url"]
    ):
        raise ValueError("installed rust-src metadata does not match the source archive pin")
    library = sysroot / "lib/rustlib/src/rust/library"
    for name in ("Cargo.toml", "core/Cargo.toml", "alloc/Cargo.toml", "std/Cargo.toml"):
        if not (library / name).is_file():
            raise ValueError("matching rust-src is missing; install the pinned rust-src component")
    return {"compiler": fields, "source_directory": str(library), "source_archive": pin["rust_src"]}


def verify_c_toolchain(toolchain: Path, sysroot: Path, target: str, pin: dict, runner=run) -> dict:
    if target != pin["c_target"]:
        raise ValueError("unsupported native C target")
    compiler = toolchain / "bin" / (target + "-gcc")
    if not compiler.is_file():
        raise ValueError("Synveil target GCC missing; build the Phase 1 toolchain first")
    if runner(str(compiler), "-dumpmachine") != target:
        raise ValueError("GCC target does not match Synveil")
    actual = Path(runner(str(compiler), "-print-sysroot")).resolve()
    if actual != sysroot.resolve():
        raise ValueError("GCC sysroot differs from the requested isolated sysroot")
    for name in ("usr/lib64/crt1.o", "usr/lib64/Scrt1.o", "usr/lib64/crti.o",
                 "usr/lib64/crtn.o", "usr/lib64/libc.a", "usr/include/features.h"):
        if not (sysroot / name).is_file():
            raise ValueError("incomplete target glibc sysroot: " + name)
    # Stage-1 GCC stores its unwind objects in libgcc.a; never use host libgcc_s.
    runtime = Path(runner(str(compiler), "-print-libgcc-file-name")).resolve()
    if not runtime.is_file() or not runtime.is_relative_to(toolchain.resolve()):
        raise ValueError("target libgcc is missing or outside the Synveil toolchain")
    return {"compiler": str(compiler), "sysroot": str(actual), "libgcc": str(runtime)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-dir", required=True, type=Path)
    parser.add_argument("--sysroot", required=True, type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        if pin["schema"] != "synveil.rust-bootstrap/v1":
            raise ValueError("unsupported Rust bootstrap pin schema")
        target = verify_c_toolchain(args.toolchain_dir.resolve(), args.sysroot.resolve(), args.target, pin)
        identity = run("rustup", "run", pin["channel"], "rustc", "--version", "--verbose")
        rust_root = Path(run("rustup", "run", pin["channel"], "rustc", "--print", "sysroot"))
        rust = verify_rust(identity, rust_root, pin)
        print(json.dumps({"schema": "synveil.native-preflight/v1", "target": target, "rust": rust}, indent=2))
        print("[native-check] prerequisites passed; archive/source and link audits remain required", file=sys.stderr)
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print("[native-check] " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
