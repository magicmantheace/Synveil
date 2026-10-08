#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build an isolated guest test image without changing the native rootfs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("absent", "protocol"), required=True)
    args = parser.parse_args()
    work = Path(os.environ.get("SYNVEIL_WORK_DIR", ROOT / "build/work")).resolve()
    rootfs = Path(os.environ.get("SYNVEIL_ROOTFS_DIR", work / "rootfs")).resolve()
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out")).resolve()
    if not (rootfs / "usr/sbin/veil-core").is_file():
        parser.error("build a native rootfs first")
    fixture_work = work / "guest-fixtures"
    fixture_work.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=args.case + "-", dir=fixture_work))
    tree = staging / "rootfs"
    shutil.copytree(rootfs, tree, symlinks=True)
    fixture_out = out / "guest-fixtures" / args.case
    fixture_out.mkdir(parents=True, exist_ok=True)
    if args.case == "absent":
        (tree / "usr/sbin/veil-core").unlink()
    else:
        toolchain = Path(os.environ.get("SYNVEIL_TOOLCHAIN_DIR", ROOT / "build/toolchain/out"))
        target = os.environ.get("SYNVEIL_TARGET", "x86_64-synveil-linux-gnu")
        sysroot = os.environ.get("SYNVEIL_SYSROOT", str(toolchain / target / "sysroot"))
        probe = tree / "usr/libexec/synveil-test/protocol-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(toolchain / "bin" / (target + "-gcc")),
                        "--sysroot=" + sysroot, "-static", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/protocol-probe.c"), "-o", str(probe)], check=True)
    with (fixture_out / "synveil-initramfs.cpio").open("wb") as packed:
        subprocess.run(["bash", "-o", "pipefail", "-c",
                        "find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0"],
                       cwd=tree, stdout=packed, check=True)
    env = {**os.environ, "SYNVEIL_OUT_DIR": str(fixture_out),
           "SYNVEIL_WORK_DIR": str(staging / "build")}
    for name in ("kernel", "image"):
        subprocess.run(["bash", str(ROOT / "build/scripts" / (name + ".sh"))],
                       env=env, check=True)
    identity = {"case": args.case, "git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "artifacts": {}}
    for name in ("synveil-initramfs.cpio", "synveil-x86_64.efi", "synveil-x86_64.img"):
        path = fixture_out / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        identity["artifacts"][name] = {"sha256": digest, "bytes": path.stat().st_size}
    (fixture_out / "manifests/fixture.json").write_text(json.dumps(identity, indent=2) + "\n")
    subprocess.run(["python3", str(ROOT / "tests/smoke-recovery.py"),
                    "--case", args.case, "--timeout", "60"], env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
