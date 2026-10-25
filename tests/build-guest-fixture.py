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
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from state_disk import create_state_disk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("absent", "protocol", "graphical-login", "graphical-locked", "graphical-onboard", "recovery-status", "audit-release-crash"), required=True)
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
    proof = fixture_out / "manifests/validation.json"
    # A previous success must never survive a rebuild that then fails.
    proof.unlink(missing_ok=True)
    if args.case == "absent":
        (tree / "usr/sbin/veil-core").unlink()
    elif args.case in ("graphical-login", "graphical-locked", "graphical-onboard", "recovery-status"):
        marker = tree / "etc/synveil/auth-desktop"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
        # The guest's marker policy requires mode 0644 exactly; a strict
        # host umask (e.g. 0007) would otherwise create it as 0660 and the
        # fixture would be rejected as unsafe in the guest.
        marker.chmod(0o644)
        debug = tree / "etc/synveil/developer-serial-recovery"
        if debug.exists() or debug.is_symlink():
            raise ValueError("unexpected developer recovery marker")
        # Only the synthetic enrollment fixture needs the unsafe serial
        # debug override. A locked-preview fixture must never receive it.
        if args.case == "graphical-login":
            debug.touch(exist_ok=False)
            debug.chmod(0o644)
        if args.case == "recovery-status":
            # Deliberately remove the protected services from the guest.
            for name in ("usr/sbin/veil-core", "usr/sbin/veil-seat",
                         "usr/bin/synveil-display"):
                (tree / name).unlink(missing_ok=True)
    else:
        toolchain = Path(os.environ.get("SYNVEIL_TOOLCHAIN_DIR", ROOT / "build/toolchain/out"))
        target = os.environ.get("SYNVEIL_TARGET", "x86_64-synveil-linux-gnu")
        sysroot = os.environ.get("SYNVEIL_SYSROOT", str(toolchain / target / "sysroot"))
        name = "audit-release-cut" if args.case == "audit-release-crash" else "protocol-probe"
        probe = tree / "usr/libexec/synveil-test" / name
        probe.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(toolchain / "bin" / (target + "-gcc")),
                        "--sysroot=" + sysroot, "-static", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests" / (name + ".c")), "-o", str(probe)], check=True)
    with (fixture_out / "synveil-initramfs.cpio").open("wb") as packed:
        subprocess.run(["bash", "-o", "pipefail", "-c",
                        "find . -print0 | sort -z | cpio --null -o --format=newc --owner=0:0"],
                       cwd=tree, stdout=packed, check=True)
    # The baseline kernel and source were built for this exact checkout
    # earlier in the same pipeline. Copy its objects into each fixture's
    # separate output tree; never replace the base kernel or skip boot tests.
    env = {**os.environ, "SYNVEIL_OUT_DIR": str(fixture_out),
           "SYNVEIL_WORK_DIR": str(staging / "build"),
           "SYNVEIL_KERNEL_SEED_WORK": str(work)}
    if args.case == "recovery-status":
        env["SYNVEIL_KERNEL_TEST_RECOVERY_STATUS"] = "1"
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
    if args.case == "recovery-status":
        subprocess.run(["python3", str(ROOT / "tests/smoke-serial-locked.py"),
                        "--timeout", "60", "--recovery-status"], env=env, check=True)
    elif args.case == "graphical-locked":
        env["SYNVEIL_QEMU_VIDEO"] = "1"
        subprocess.run(["python3", str(ROOT / "tests/smoke-serial-locked.py"),
                        "--timeout", "60"], env=env, check=True)
    elif args.case == "graphical-onboard":
        # No developer serial marker: the password must be entered using
        # the exclusive graphical keyboard, and persist across two distinct
        # QEMU processes using this *test-only* state disk.
        disk = fixture_out / "graphical-onboard-state.ext4"
        create_state_disk(disk, 128)
        env.update({"SYNVEIL_QEMU_STATE_DISK": str(disk),
                    "SYNVEIL_QEMU_VIDEO": "1"})
        subprocess.run(["python3", str(ROOT / "tests/smoke-graphical-onboard.py"),
                        "--timeout", "105"], env=env, check=True)
        # This receipt is created only AFTER both independent QEMU sessions
        # pass. Never publish or bundle the test-only enrolled state disk.
        proof.write_text(json.dumps({
            "schema": "synveil.guest-proof/v1",
            "case": "graphical-onboard",
            "git_commit": identity["git_commit"],
            "fixture_sha256": hashlib.sha256(
                (fixture_out / "manifests/fixture.json").read_bytes()
            ).hexdigest(),
            "result": "passed",
            "independent_qemu_boots": 2,
            "locked_serial": True,
            "first_user_enrollment": True,
            "persistent_authentication": True,
            "wrong_password_denied_after_cold_reboot": True,
            "nonblank_rgb_after_login_both_boots": True,
            "graphical_scanout_after_cold_reboot": True,
        }, indent=2) + "\n")
    elif args.case == "audit-release-crash":
        subprocess.run(["python3", str(ROOT / "tests/smoke-audit-release-crash.py"),
                        "--timeout", "75"], env=env, check=True)
        proof.write_text(json.dumps({
            "schema": "synveil.guest-proof/v1", "case": args.case,
            "git_commit": identity["git_commit"],
            "fixture_sha256": hashlib.sha256(
                (fixture_out / "manifests/fixture.json").read_bytes()).hexdigest(),
            "result": "passed", "independent_qemu_boots": 12,
            "release_cuts": ["before-unlink", "after-unlink", "unlink-synced", "partial-completion",
                             "completion-written", "completion-synced"],
            "host_page_cache_survives": True, "physical_power_loss": False,
        }, indent=2) + "\n")
    elif args.case == "graphical-login":
        # Separate, never-reused synthetic state volume and fresh test-only
        # credential. The baseline source/image and other VM disks are intact.
        disk = fixture_out / "graphical-login-state.ext4"
        create_state_disk(disk, 128)
        env.update({"SYNVEIL_QEMU_STATE_DISK": str(disk),
                    "SYNVEIL_QEMU_VIDEO": "1"})
        subprocess.run(["python3", str(ROOT / "tests/smoke-graphical-login.py"),
                        "--timeout", "150"], env=env, check=True)
    else:
        subprocess.run(["python3", str(ROOT / "tests/smoke-recovery.py"),
                        "--case", args.case, "--timeout", "60"], env=env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
