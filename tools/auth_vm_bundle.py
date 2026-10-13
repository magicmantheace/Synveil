#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create a private, offline authenticated QEMU bundle from validated guest proof.

This deliberately cannot install to a physical disk, edit an existing VM,
or reuse the synthetic credential state disk from the QEMU integration test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from initramfs_profile import inspect_authenticated_archive
from state_disk import create_state_disk

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ("synveil-initramfs.cpio", "synveil-x86_64.efi", "synveil-x86_64.img")
GUEST_CASE = "graphical-onboard"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked artifact: {path}")


def verify_authenticated_fixture(fixture: Path, revision: str) -> dict:
    if fixture.is_symlink() or not fixture.is_dir():
        raise ValueError("authenticated QEMU fixture directory unavailable")
    manifest_path = fixture / "manifests/fixture.json"
    proof_path = fixture / "manifests/validation.json"
    require_regular(manifest_path)
    require_regular(proof_path)
    manifest = json.loads(manifest_path.read_text())
    proof = json.loads(proof_path.read_text())
    if manifest.get("case") != GUEST_CASE or manifest.get("git_commit") != revision:
        raise ValueError("wrong QEMU scenario or stale source revision")
    if (proof.get("schema") != "synveil.guest-proof/v1" or
            proof.get("case") != GUEST_CASE or
            proof.get("git_commit") != revision or
            proof.get("fixture_sha256") != sha256(manifest_path) or
            proof.get("result") != "passed" or
            proof.get("independent_qemu_boots") != 2 or
            proof.get("locked_serial") is not True or
            proof.get("first_user_enrollment") is not True or
            proof.get("persistent_authentication") is not True):
        raise ValueError("no matching successful independent QEMU onboarding proof")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("missing QEMU artifact inventory")
    for name in ARTIFACTS:
        file = fixture / name
        require_regular(file)
        item = artifacts.get(name)
        if not isinstance(item, dict) or item.get("bytes") != file.stat().st_size or (
            item.get("sha256") != sha256(file)
        ):
            raise ValueError(f"invalid authenticated QEMU artifact: {name}")
    # Verify the exact guest cpio (not a staging tree) is an authenticated
    # root-owned image without an unauthenticated developer serial override.
    inspect_authenticated_archive(fixture / "synveil-initramfs.cpio")
    return manifest


LAUNCHER = """#!/bin/sh
# Offline authenticated QEMU preview. NOT a physical disk installer.
set -eu
: "${SYNVEIL_REPO_ROOT:?set SYNVEIL_REPO_ROOT to the Synveil checkout}"
bundle="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -L "$bundle" ] || [ "$(stat -c '%a' "$bundle")" != 700 ] ||
   [ "$(stat -c '%u' "$bundle")" != "$(id -u)" ]; then
    echo "[synveil-auth-vm] private owner-owned bundle mode 0700 required" >&2
    exit 1
fi
for file in synveil-x86_64.img fixture.json validation.json SHA256SUMS VM-BUNDLE.json; do
    if [ ! -f "$bundle/$file" ] || [ -L "$bundle/$file" ]; then
        echo "[synveil-auth-vm] missing or symlinked immutable artifact: $file" >&2
        exit 1
    fi
done
if [ ! -f "$bundle/synveil-state.ext4" ] ||
   [ -L "$bundle/synveil-state.ext4" ] ||
   [ "$(stat -c '%u' "$bundle/synveil-state.ext4")" != "$(id -u)" ] ||
   [ "$(stat -c '%a' "$bundle/synveil-state.ext4")" != 600 ]; then
    echo "[synveil-auth-vm] state disk must be private, regular, mode 0600" >&2
    exit 1
fi
if ! (cd "$bundle" && sha256sum --status --check SHA256SUMS); then
    echo "[synveil-auth-vm] immutable image checksum mismatch" >&2
    exit 1
fi
# The image's QEMU evidence applies to a specific Synveil revision.
# Do not silently execute a different or modified checkout's QEMU runner.
expected="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["git_revision"])' "$bundle/VM-BUNDLE.json")" || exit 1
actual="$(git -C "$SYNVEIL_REPO_ROOT" rev-parse HEAD 2>/dev/null)" || exit 1
dirty="$(git -C "$SYNVEIL_REPO_ROOT" status --porcelain --untracked-files=no 2>/dev/null)" || exit 1
if [ "$expected" != "$actual" ] || [ -n "$dirty" ]; then
    echo "[synveil-auth-vm] checkout revision or tracked worktree differs from validated image" >&2
    exit 1
fi
# Never inherit developer QMP control, serial override or host networking.
unset SYNVEIL_QEMU_QMP_SOCKET SYNVEIL_DEV_SERIAL_ROOTFS SYNVEIL_AUTH_DESKTOP_ROOTFS
export SYNVEIL_OUT_DIR="$bundle"
export SYNVEIL_QEMU_STATE_DISK="$bundle/synveil-state.ext4"
export SYNVEIL_QEMU_DISPLAY=gtk
export SYNVEIL_QEMU_VIDEO=1
export SYNVEIL_QEMU_NETWORK=0
exec bash "$SYNVEIL_REPO_ROOT/build.sh" qemu
"""


def create_authenticated_bundle(fixture: Path, dest: Path,
                                revision: str, size_mib: int = 256) -> Path:
    manifest = verify_authenticated_fixture(fixture, revision)
    if not 64 <= size_mib <= 16384:
        raise ValueError("state disk must be between 64 and 16384 MiB")
    # Exclusive creation is essential: never delete/overwrite user VM data.
    dest.mkdir(mode=0o700, exist_ok=False)
    try:
        shutil.copyfile(fixture / "synveil-x86_64.img", dest / "synveil-x86_64.img")
        (dest / "synveil-x86_64.img").chmod(0o400)
        for source, target in (("fixture.json", "fixture.json"),
                               ("validation.json", "validation.json")):
            shutil.copyfile(fixture / "manifests" / source, dest / target)
            (dest / target).chmod(0o400)
        # A *brand-new* empty disk; NEVER include the synthetic QEMU test
        # state disk, which contains the test-only Argon2id credential.
        create_state_disk(dest / "synveil-state.ext4", size_mib)
        metadata = {
            "schema": "synveil.auth-vm-bundle/v1",
            "git_revision": manifest["git_commit"],
            "target": "x86_64-qemu-uefi",
            "desktop": "experimental-authenticated-framebuffer",
            "state_disk_mib": size_mib,
            "independent_qemu_boots_validated": 2,
            "physical_install_allowed": False,
            "human_test_validated": False,
            "signed_release": False,
        }
        (dest / "VM-BUNDLE.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (dest / "VM-BUNDLE.json").chmod(0o400)
        immutable = ("synveil-x86_64.img", "fixture.json", "validation.json",
                     "VM-BUNDLE.json")
        (dest / "SHA256SUMS").write_text("".join(
            f"{sha256(dest / name)}  {name}\n" for name in immutable
        ))
        (dest / "SHA256SUMS").chmod(0o400)
        launcher = dest / "run-vm.sh"
        launcher.write_text(LAUNCHER)
        launcher.chmod(0o700)
        (dest / "README.md").write_text(
            "# Synveil authenticated test VM (experimental)\n\n"
            "This is a QEMU-only authenticated framebuffer preview, NOT a\n"
            "physical installer, secure-boot image, compositor or signed release.\n"
            "Only use it for offline VM experiments, never on a physical disk.\n\n"
            "Start from a Linux Synveil checkout with QEMU/OVMF installed:\n\n"
            '    SYNVEIL_REPO_ROOT="$PWD" ./out/auth-vm/run-vm.sh\n\n'
            "On a fresh state disk, set a local password twice on the graphics\n"
            "screen, then log in as the fixed nonroot Synveil demo account.\n"
            "The VM state disk is freshly formatted, not the synthetic credential\n"
            "disk used by CI. Never share this state disk after adding passwords.\n"
            "The launcher checks private permissions and SHA256 integrity.\n"
            "These checks do not establish authenticity; no release is signed.\n"
            "The boot image is immutable and networking is disabled by default.\n"
            "There is no tested owner password reset/recovery mechanism yet.\n"
            "Deleting the state disk destroys saved credentials and user files.\n"
        )
    except BaseException:
        shutil.rmtree(dest)
        raise
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path,
                        default=ROOT / "out/guest-fixtures/graphical-onboard")
    parser.add_argument("--dest", type=Path, default=ROOT / "out/auth-vm")
    parser.add_argument("--size-mib", type=int, default=256)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    try:
        create_authenticated_bundle(args.fixture, args.dest, revision, args.size_mib)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"[synveil] offline authenticated QEMU preview created: {args.dest}")
    print("[synveil] no signed release or physical installation implied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
