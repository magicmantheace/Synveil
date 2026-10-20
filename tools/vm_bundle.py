#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Package an already-built, checksummed QEMU-only desktop VM.

This is not a physical installer. Existing paths are never replaced.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from state_disk import create_state_disk
from initramfs_profile import inspect_demo_archive

ROOT = Path(__file__).resolve().parents[1]
NEEDED_KERNEL = (
    "CONFIG_DRM_VIRTIO_GPU=y",
    "CONFIG_DRM_FBDEV_EMULATION=y",
    "CONFIG_FB_DEVICE=y",
    "CONFIG_INPUT_EVDEV=y",
    "CONFIG_USB_HID=y",
)
NEEDED_ROOTFS = (
    "usr/bin/synveil-display",
    "usr/bin/synveil-input",
    "usr/bin/synveil-vt",
    "etc/synveil/desktop-autostart",
    "home/synveil",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as opened:
        for block in iter(lambda: opened.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_build(out: Path, rootfs: Path, revision: str) -> dict:
    boot = out / "synveil-x86_64.img"
    manifest_file = out / "manifests/build.json"
    kernel_file = out / "manifests/kernel.config"
    for path in (boot, manifest_file, kernel_file):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or symlinked build artifact: {path}")
    manifest = json.loads(manifest_file.read_text())
    if manifest.get("schema") != "synveil.build/v1":
        raise ValueError("unrecognized build manifest")
    # Historic host fixtures lack this new field and represent QEMU images.
    # New builds record the actual kernel target to prevent accidental
    # promotion of a hardware bring-up image as the standard VM artifact.
    if manifest.get("boot_target", "qemu") != "qemu":
        raise ValueError("hardware boot target cannot be packaged as a QEMU bundle")
    git = manifest.get("git", {})
    if git.get("commit") != revision or git.get("dirty") is not False:
        raise ValueError("stale or dirty build: image must match current committed revision")
    expected = manifest.get("artifacts", {}).get(boot.name, {}).get("sha256")
    if not isinstance(expected, str) or sha256(boot) != expected:
        raise ValueError("EFI image failed SHA256 verification")
    missing = set(NEEDED_KERNEL) - set(kernel_file.read_text().splitlines())
    if missing:
        raise ValueError("missing required kernel options: " + ", ".join(sorted(missing)))
    archive = out / "synveil-rootfs.tar.zst"
    archived_hash = manifest.get("artifacts", {}).get(archive.name, {}).get("sha256")
    if (archive.is_symlink() or not archive.is_file() or
        not isinstance(archived_hash, str) or sha256(archive) != archived_hash):
        raise ValueError("staged rootfs archive failed SHA256 verification")
    # Verify the *embedded* boot payload, not only the editable staging
    # folder. A stale rootfs tree can disagree with the already-packed newc
    # archive that the kernel build actually consumes.
    initramfs = out / "synveil-initramfs.cpio"
    archived_cpio = manifest.get("artifacts", {}).get(initramfs.name, {})
    if (initramfs.is_symlink() or not initramfs.is_file() or
        archived_cpio.get("bytes") != initramfs.stat().st_size or
        archived_cpio.get("sha256") != sha256(initramfs)):
        raise ValueError("embedded initramfs failed manifest verification")
    inspect_demo_archive(initramfs)
    # Never accidentally distribute a gated authentication preview via
    # the old unauthenticated developer bundle or ship a root serial bypass.
    # These marker-bearing configurations are only for isolated guest tests.
    for forbidden in ("etc/synveil/auth-desktop",
                      "etc/synveil/developer-serial-recovery"):
        candidate = rootfs / forbidden
        if candidate.exists() or candidate.is_symlink():
            raise ValueError("authenticated preview/serial override cannot be "
                             "packaged as an ordinary developer VM bundle")
    for rel in NEEDED_ROOTFS:
        candidate = rootfs / rel
        expected_kind = candidate.is_dir() if rel == "home/synveil" else candidate.is_file()
        if candidate.is_symlink() or not expected_kind:
            raise ValueError(f"missing or invalid desktop staging file: {rel}")
    passwd = rootfs / "etc/passwd"
    if passwd.is_symlink() or not passwd.is_file():
        raise ValueError("missing staged passwd file")
    if not any(line.startswith("synveil:x:1000:1000:")
               for line in passwd.read_text().splitlines()):
        raise ValueError("missing unprivileged VM demo account")
    return manifest


def create_bundle(out: Path, rootfs: Path, dest: Path,
                  revision: str, size_mib: int = 256) -> Path:
    manifest = verify_build(out, rootfs, revision)
    # mkdir(exist_ok=False) fails for files, directories and dangling symlinks.
    dest.mkdir(mode=0o700, exist_ok=False)
    try:
        shutil.copyfile(out / "synveil-x86_64.img", dest / "synveil-x86_64.img")
        (dest / "synveil-x86_64.img").chmod(0o400)
        shutil.copyfile(out / "manifests/build.json", dest / "build.json")
        shutil.copyfile(out / "manifests/kernel.config", dest / "kernel.config")
        create_state_disk(dest / "synveil-state.ext4", size_mib)
        immutable = ("synveil-x86_64.img", "build.json", "kernel.config")
        checksums = {name: sha256(dest / name) for name in immutable}
        (dest / "SHA256SUMS").write_text(
            "".join(f"{checksum}  {name}\n"
                    for name, checksum in sorted(checksums.items()))
        )
        (dest / "VM-BUNDLE.json").write_text(json.dumps({
            "schema": "synveil.vm-bundle/v1",
            "git_revision": manifest["git"]["commit"],
            "boot_image_sha256": checksums["synveil-x86_64.img"],
            "state_disk_mib": size_mib,
            "target": "x86_64-qemu-uefi",
            "desktop": "developer-framebuffer",
            "user_install_validated": False,
        }, indent=2) + "\n")
        script = """#!/bin/sh
# Developer-only VM runner. All guest artifacts remain private/offline.
set -eu
: "${SYNVEIL_REPO_ROOT:?set SYNVEIL_REPO_ROOT to a Synveil checkout}"
bundle="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -L "$bundle" ] || [ "$(stat -c '%a' "$bundle")" != 700 ] ||
   [ "$(stat -c '%u' "$bundle")" != "$(id -u)" ]; then
    echo "[synveil-vm] expected a private owner-owned mode 0700 bundle" >&2
    exit 1
fi
for file in synveil-x86_64.img build.json kernel.config SHA256SUMS; do
    if [ ! -f "$bundle/$file" ] || [ -L "$bundle/$file" ]; then
        echo "[synveil-vm] missing or symlinked immutable artifact: $file" >&2
        exit 1
    fi
done
if [ ! -f "$bundle/synveil-state.ext4" ] ||
   [ -L "$bundle/synveil-state.ext4" ] ||
   [ "$(stat -c '%a' "$bundle/synveil-state.ext4")" != 600 ]; then
    echo "[synveil-vm] state disk missing, symlinked, or not mode 0600" >&2
    exit 1
fi
# Detect accidental post-packaging changes before any emulator process starts.
# This is integrity checking, NOT a digital signature or trusted boot.
if ! (cd "$bundle" && sha256sum --status --check SHA256SUMS); then
    echo "[synveil-vm] immutable artifact checksum mismatch; refusing to boot" >&2
    exit 1
fi
export SYNVEIL_OUT_DIR="$bundle"
export SYNVEIL_QEMU_STATE_DISK="$bundle/synveil-state.ext4"
export SYNVEIL_QEMU_DISPLAY=gtk
export SYNVEIL_QEMU_NETWORK=0
unset SYNVEIL_QEMU_QMP_SOCKET SYNVEIL_QEMU_VIDEO
exec bash "$SYNVEIL_REPO_ROOT/build.sh" qemu
"""
        launcher = dest / "run-vm.sh"
        launcher.write_text(script)
        launcher.chmod(0o700)
        (dest / "README.md").write_text(
            "# Synveil developer QEMU bundle\n\n"
            "NOT a hardware installer, signed release or authenticated desktop.\n"
            "The demo shell is non-root but unauthenticated; use only in a private VM.\n\n"
            "From the Synveil checkout: set SYNVEIL_REPO_ROOT to the checkout path,\n"
            "then run the bundle's run-vm.sh script. The launcher verifies\n"
            "private directory/state-disk permissions and immutable checksums\n"
            "before every boot. This detects accidental tampering, not hostile\n"
            "modification of both checksums and image; nothing is signed.\n"
            "It defaults to offline graphics (GTK). To enable networking, invoke\n"
            "build.sh qemu directly with SYNVEIL_OUT_DIR set to this bundle and\n"
            "SYNVEIL_QEMU_NETWORK=1. The EFI disk is read-only while the separate\n"
            "ext4 state disk is writable. Preserve the state disk for saved data.\n"
        )
    except BaseException:
        # Only remove the directory created exclusively by this invocation.
        shutil.rmtree(dest)
        raise
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    parser.add_argument("--rootfs", type=Path, default=ROOT / "build/work/rootfs")
    parser.add_argument("--dest", type=Path, default=ROOT / "out/dev-vm")
    parser.add_argument("--size-mib", type=int, default=256)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    try:
        create_bundle(args.out, args.rootfs, args.dest, revision, args.size_mib)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(f"[synveil] private, checksummed QEMU bundle created at {args.dest}")
    print("[synveil] no physical installation or user desktop validation implied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
