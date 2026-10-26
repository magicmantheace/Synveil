#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Host-side release packager for the bootable A/B updater.

Produces the release media the guest agent (veil-core/src/ab.rs)
verifies and stages: synveil.release.json (schema
synveil.ab-release/v2, the same field set tools/ab_update.py
verifies), synveil.release.sig (raw Ed25519 over the exact metadata
bytes) and synveil.release.bin (the generation UKI), on a small GPT
disk whose single ext4 partition carries the AB release PARTUUID.

Signing uses the host openssl, exactly like the host transaction
core. The private key lives only in the caller-chosen trust
directory (under out/, git-ignored); it is never written into a
disk image, a log or this repository. The current key is the
development key synveil-dev-2026-01.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PARTUUID = "ab000000-0000-4000-8000-0000000000f1"
KEY_ID = "synveil-dev-2026-01"
MEDIA_SIZE_MB = 160


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_keypair(trust_dir):
    """Create the development publisher keypair once; return
    (private_key_path, public_key_hex)."""
    trust_dir = Path(trust_dir)
    trust_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(trust_dir, 0o700)
    private = trust_dir / "publisher.key"
    public_pem = trust_dir / "publisher.pub.pem"
    if not private.is_file():
        run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)])
        os.chmod(private, 0o600)
        run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public_pem)])
    der = subprocess.check_output(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER"])
    # SubjectPublicKeyInfo for Ed25519 ends with the raw 32-byte key.
    public_hex = der[-32:].hex()
    (trust_dir / "publisher.json").write_text(json.dumps({
        "schema": "synveil.ab-trust/v1",
        "key_id": KEY_ID,
        "development": True,
        "public_key_hex": public_hex,
    }, indent=2) + "\n")
    return private, public_hex


def build_metadata(payload, version, generation_id, key_id=KEY_ID,
                   state_format=1, min_reader_state_format=1,
                   rollback_breaking=False, issued_at=None, expires_at=None):
    payload = Path(payload)
    now = int(time.time())
    return {
        "schema": "synveil.ab-release/v2",
        "version": version,
        "bytes": payload.stat().st_size,
        "sha256": sha256(payload),
        "generation_id": generation_id,
        "key_id": key_id,
        "issued_at": issued_at if issued_at is not None else now - 3600,
        "expires_at": expires_at if expires_at is not None else now + 30 * 86400,
        "state_format": state_format,
        "min_reader_state_format": min_reader_state_format,
        "rollback_breaking": rollback_breaking,
    }


def sign_metadata(private, metadata_bytes):
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(metadata_bytes)
        meta_path = handle.name
    sig_path = meta_path + ".sig"
    try:
        run(["openssl", "pkeyutl", "-sign", "-inkey", str(private),
             "-rawin", "-in", meta_path, "-out", sig_path])
        return Path(sig_path).read_bytes()
    finally:
        os.unlink(meta_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def verify_metadata(public_pem, metadata_bytes, signature):
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(metadata_bytes)
        meta_path = handle.name
    sig_path = meta_path + ".sig"
    try:
        Path(sig_path).write_bytes(signature)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_pem),
             "-rawin", "-in", meta_path, "-sigfile", sig_path],
            capture_output=True)
        return result.returncode == 0
    finally:
        os.unlink(meta_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def build_media(payload, metadata, signature, out_image):
    """Write a GPT release disk: one ext4 partition with the three
    release files at its root.

    Output safety (review correction, 2026-10-26): the output image
    and both staging paths are created EXCLUSIVELY. An existing
    output — file, symlink, device, anything — is refused, never
    truncated; an existing staging path is refused, never deleted;
    the parent must be an existing real directory, not a symlink.
    Callers that legitimately rebuild their own outputs remove them
    first, explicitly, at the call site (tools/native_ab.py does).
    On failure the staging paths created by this call are left in
    place as evidence; the next call refuses until an operator
    removes them."""
    import shutil
    out_image = Path(out_image)
    parent = out_image.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError(
            f"release output parent is not an existing directory: {parent}")
    if os.path.lexists(out_image):
        raise ValueError(
            f"release output already exists, refusing to overwrite: {out_image}")
    stage = parent / (out_image.stem + "-stage")
    fs_image = parent / (out_image.stem + "-fs.img")
    for staging in (stage, fs_image):
        if os.path.lexists(staging):
            raise ValueError(
                f"release staging path already exists, refusing to touch it: {staging}")
    stage.mkdir()
    (stage / "synveil.release.json").write_bytes(
        json.dumps(metadata, indent=2).encode() + b"\n")
    (stage / "synveil.release.sig").write_bytes(signature)
    shutil.copyfile(payload, stage / "synveil.release.bin")
    with open(out_image, "xb") as handle:
        handle.truncate(MEDIA_SIZE_MB * 1024 * 1024)
    run(["sgdisk", "--clear",
         "--new=1:2048:0", "--typecode=1:8300",
         "--change-name=1:Synveil Release",
         "--partition-guid=1:" + RELEASE_PARTUUID, str(out_image)])
    info = subprocess.check_output(["sgdisk", "-i", "1", str(out_image)], text=True)
    first = int([line for line in info.splitlines()
                 if "First sector:" in line][0].split(":", 1)[1].split()[0])
    with open(fs_image, "xb") as handle:
        handle.truncate((MEDIA_SIZE_MB - 2) * 1024 * 1024)
    run(["mkfs.ext4", "-F", "-q", "-L", "SYNVEIL_RELEASE", "-b", "4096", str(fs_image)])
    for name in ("synveil.release.json", "synveil.release.sig", "synveil.release.bin"):
        run(["debugfs", "-w", "-R", f"write {stage / name} /{name}", str(fs_image)],
            capture_output=True)
    with open(fs_image, "rb") as source, open(out_image, "r+b") as disk:
        disk.seek(first * 512)
        shutil.copyfileobj(source, disk)
    fs_image.unlink()
    shutil.rmtree(stage)
    return out_image


def package(payload, version, generation_id, trust_dir, out_image,
            corrupt_signature=False, **meta_kwargs):
    private, _public_hex = ensure_keypair(trust_dir)
    metadata = build_metadata(payload, version, generation_id, **meta_kwargs)
    metadata_bytes = json.dumps(metadata, indent=2).encode() + b"\n"
    signature = sign_metadata(private, metadata_bytes)
    public_pem = Path(trust_dir) / "publisher.pub.pem"
    if not verify_metadata(public_pem, metadata_bytes, signature):
        raise ValueError("self-verification of the release signature failed")
    if corrupt_signature:
        signature = bytes([signature[0] ^ 0x01]) + signature[1:]
    build_media(payload, metadata, signature, out_image)
    return {
        "media": str(out_image),
        "media_sha256": sha256(out_image),
        "version": version,
        "generation_id": generation_id,
        "payload_sha256": metadata["sha256"],
        "payload_bytes": metadata["bytes"],
        "key_id": metadata["key_id"],
        "corrupt_signature": corrupt_signature,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--trust-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--corrupt-signature", action="store_true")
    args = parser.parse_args()
    receipt = package(args.payload, args.version, args.generation_id,
                      args.trust_dir, args.out, corrupt_signature=args.corrupt_signature)
    print(json.dumps(receipt, indent=2))
