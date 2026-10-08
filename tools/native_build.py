#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build source-backed static Rust binaries, then audit their target links."""
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

from native_preflight import ROOT, PIN, run


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        value = hashlib.file_digest(stream, "sha256")
    return value.hexdigest()


def audit_map(path: Path, allowed_roots: list[Path], cwd: Path) -> list[str]:
    inputs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("LOAD "):
            continue
        item = Path(line[5:].strip())
        if not item.is_absolute():
            item = cwd / item
        item = item.resolve()
        if not any(item.is_relative_to(root.resolve()) for root in allowed_roots):
            raise ValueError("target link input outside Synveil build roots: " + str(item))
        inputs.append(str(item))
    if not inputs:
        raise ValueError("target link map has no LOAD entries")
    if not any(Path(item).name == "libc.a" for item in inputs):
        raise ValueError("target link map does not contain static glibc")
    if not any(Path(item).name.startswith("libstd-") for item in inputs):
        raise ValueError("target link map does not contain rebuilt Rust std")
    return sorted(set(inputs))


def audit_elf(headers: str, program: str, dynamic: str) -> None:
    if "ELF64" not in headers or "X86-64" not in headers.upper():
        raise ValueError("native artifact is not x86_64 ELF64")
    if "INTERP" in program or "(NEEDED)" in dynamic:
        raise ValueError("native artifact unexpectedly requires shared runtime libraries")


def build_environment(inherited: dict, compiler: Path, work: Path, target: str) -> dict:
    env = dict(inherited)
    for key in list(env):
        if (key.startswith("CARGO_") and key != "CARGO_HOME") or key in (
            "RUSTFLAGS", "RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
            "RUSTDOC", "RUSTDOCFLAGS", "RUSTUP_TOOLCHAIN",
            "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "GCC_EXEC_PREFIX"
        ):
            env.pop(key)
    runtime = work / "runtime"
    env.update({
        "RUSTC_WRAPPER": str(ROOT / "tools/native_rustc_wrapper.py"),
        "SYNVEIL_NATIVE_GCC": str(compiler), "SYNVEIL_NATIVE_MAP_DIR": str(work / "maps"),
        "CARGO_PROFILE_RELEASE_PANIC": "abort", "TMPDIR": str(work / "tmp"),
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS":
            "-C target-feature=+crt-static -C link-arg=-L" + str(runtime),
        "CC_x86_64_unknown_linux_gnu": str(compiler),
        "AR_x86_64_unknown_linux_gnu": str(compiler.parent / (target + "-ar")),
    })
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-dir", required=True, type=Path)
    parser.add_argument("--sysroot", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--rust-source-archive", type=Path)
    args = parser.parse_args()
    try:
        pin = json.loads(PIN.read_text())
        preflight = [sys.executable, str(ROOT / "tools/native_preflight.py"),
                     "--toolchain-dir", str(args.toolchain_dir), "--sysroot", str(args.sysroot),
                     "--target", args.target]
        if args.rust_source_archive:
            preflight += ["--rust-source-archive", str(args.rust_source_archive)]
        evidence = json.loads(subprocess.check_output(preflight, text=True, cwd=ROOT))
        compiler = Path(evidence["target"]["compiler"])
        runtime = Path(evidence["target"]["libgcc"])
        parent = args.work_dir.resolve() / "native"
        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="build-", dir=parent))
        if any(character.isspace() or character == "," for character in str(work)):
            raise ValueError("native work directory cannot contain whitespace or commas")
        for name in ("runtime", "maps", "tmp"):
            (work / name).mkdir()
        # Bootstrap GCC puts the unwind objects in libgcc.a instead of libgcc_eh.a.
        (work / "runtime/libgcc_eh.a").symlink_to(runtime)
        env = build_environment(os.environ, compiler, work, args.target)
        command = ["rustup", "run", pin["channel"], "cargo", "build", "--release", "--locked",
                   "--target", pin["rust_target"], "--target-dir", str(work / "cargo"),
                   "-Zbuild-std=std,panic_abort", "-Zbuild-std-features=compiler-builtins-mem"]
        log = work / "cargo-build.log"
        print("[native] build log: " + str(log), flush=True)
        with log.open("w") as output:
            subprocess.run(command, cwd=ROOT, env=env, stdout=output,
                           stderr=subprocess.STDOUT, check=True)
        readelf = str(compiler.parent / (args.target + "-readelf"))
        artifacts = {}
        binaries = {}
        for name in ("veil-core", "synctl"):
            binary = work / "cargo" / pin["rust_target"] / "release" / name
            audit_elf(run(readelf, "-h", str(binary)), run(readelf, "-l", str(binary)),
                      run(readelf, "-d", str(binary)))
            link_map = work / "maps" / (name.replace("-", "_") + ".map")
            inputs = audit_map(link_map, [args.toolchain_dir, args.sysroot, work], ROOT)
            artifacts[name] = {"sha256": sha256(binary), "bytes": binary.stat().st_size,
                               "link_map": str(link_map), "link_map_sha256": sha256(link_map),
                               "link_inputs": inputs}
            binaries[name] = binary
        destination = args.out_dir.resolve() / "native"
        destination.mkdir(parents=True, exist_ok=True)
        evidence.update({"schema": "synveil.native-build/v1", "git_commit": run("git", "-C", str(ROOT), "rev-parse", "HEAD"),
                         "git_dirty": bool(run("git", "-C", str(ROOT), "status", "--porcelain")),
                         "cargo_lock_sha256": sha256(ROOT / "Cargo.lock"),
                         "libgcc_sha256": sha256(runtime), "command": command,
                         "artifacts": artifacts, "build_log": str(log)})
        for name, binary in binaries.items():
            shutil.copy2(binary, destination / name)
        (destination / "build.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print("[native] audited binaries ready: " + str(destination))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print("[native] " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
