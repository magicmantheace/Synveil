#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Explicitly install pinned bootstrap Rust and authenticate its library sources."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

from native_preflight import ROOT, PIN, run, verify_rust, verify_source_archive


def main() -> int:
    pin = json.loads(PIN.read_text())
    subprocess.run(["rustup", "toolchain", "install", pin["channel"], "--profile", "minimal",
                    "--component", "rust-src", "--no-self-update"], check=True)
    destination = ROOT / ".cache/sources/archives" / ("rust-src-" + pin["channel"] + ".tar.xz")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(".part")
        urllib.request.urlretrieve(pin["rust_src"]["url"], temporary)
        temporary.replace(destination)
    identity = run("rustup", "run", pin["channel"], "rustc", "--version", "--verbose")
    sysroot = Path(run("rustup", "run", pin["channel"], "rustc", "--print", "sysroot"))
    evidence = verify_rust(identity, sysroot, pin)
    evidence["source_verification"] = verify_source_archive(
        destination, Path(evidence["source_directory"]), pin["rust_src"]["sha256"]
    )
    logs = ROOT / "out/logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "native-rust-inputs.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("[native] pinned Rust source inputs authenticated")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print("[native] Rust input preparation failed: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
