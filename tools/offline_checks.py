#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Offline host and opt-in guest validation; never invokes GitHub Actions.

Run from a Synveil checkout. Results are tied to its Git HEAD and saved
under out/local-validation; a host pass is not a QEMU or installer pass.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HOST = (
    ("bootstrap", "bash", "tests/lint-bootstrap.sh"),
    ("native-preflight", "python3", "tests/native-preflight.py"),
    ("native-build", "python3", "tests/native-build.py"),
    ("native-rootfs", "python3", "tests/native-rootfs.py"),
    ("native-startup", "python3", "tests/native-startup.py"),
    ("seat-supervisor", "python3", "tests/seat-supervisor.py"),
    ("serial-console-policy", "python3", "tests/serial-console-policy.py"),
    ("read-only-recovery-status", "python3", "tests/recovery-status.py"),
    ("desktop-marker-policy", "python3", "tests/desktop-marker-policy.py"),
    ("serial-lock-smoke-fixture", "python3", "tests/serial-lock-smoke-fixture.py"),
    ("initramfs-profile", "python3", "tests/initramfs-profile.py"),
    ("auth-vm-bundle", "python3", "tests/auth-vm-bundle.py"),
    ("offline-runner-fixtures", "python3", "tests/offline-checks.py"),
    ("native-smoke-fixtures", "python3", "tests/native-smoke.py"),
    ("native-recovery-fixtures", "python3", "tests/native-recovery.py"),
    ("qemu-lock", "python3", "tests/qemu-lock.py"),
    ("protocol-fixtures", "python3", "tests/native-protocol-probe.py"),
    ("guest-fixtures", "python3", "tests/native-guest-fixture.py"),
    ("graphical-login-fixtures", "python3", "tests/graphical-login-fixture.py"),
    ("graphical-onboard-fixtures", "python3", "tests/graphical-onboard-fixture.py"),
    ("core-health", "python3", "tests/core-health.py"),
    ("network-diag", "python3", "tests/network-diag.py"),
    ("display-build", "python3", "tests/display-build.py"),
    ("desktop-lifetime", "python3", "tests/desktop-lifetime.py"),
    ("state-mount", "python3", "tests/state-mount-safety.py"),
    ("qmp-input", "python3", "tests/qmp-input.py"),
    ("qemu-options", "python3", "tests/qemu-options.py"),
)
RUST = (
    ("rustfmt", "cargo", "fmt", "--all", "--check"),
    ("rust-tests", "cargo", "test", "--workspace", "--all-targets",
     "--locked", "--offline"),
    ("clippy", "cargo", "clippy", "--workspace", "--all-targets",
     "--locked", "--offline", "--", "-D", "warnings"),
)
# The guest level is a combined *developer image* gate, not installation
# certification. Reuse the project's real QEMU scenarios rather than
# treating a successful login fixture alone as proof of recovery.
GUEST = (
    ("qemu-boot", "bash", "build.sh", "native-smoke"),
    ("qemu-core-crash", "bash", "build.sh", "recovery-smoke",
     "--case", "crash", "--timeout", "90"),
    ("qemu-core-supervision", "bash", "build.sh", "recovery-smoke",
     "--case", "supervision", "--timeout", "90"),
    ("qemu-observer", "bash", "build.sh", "recovery-smoke",
     "--case", "observe", "--timeout", "90"),
    ("qemu-desktop-restart", "bash", "build.sh", "recovery-smoke",
     "--case", "desktop", "--timeout", "90"),
    ("qemu-desktop-terminal", "bash", "build.sh", "recovery-smoke",
     "--case", "desktop-terminal", "--timeout", "90"),
    ("qemu-state-missing", "bash", "build.sh", "recovery-smoke",
     "--case", "state-missing", "--timeout", "90"),
    ("qemu-state-corrupt", "bash", "build.sh", "recovery-smoke",
     "--case", "state-corrupt", "--timeout", "90"),
    ("qemu-persistence", "bash", "build.sh", "persistence-smoke",
     "--timeout", "90"),
    ("qemu-ui-preferences", "bash", "build.sh", "ui-preferences-smoke",
     "--timeout", "90"),
    ("qemu-seat-crash", "bash", "build.sh", "recovery-smoke",
     "--case", "seat-crash", "--timeout", "120"),
    ("qemu-locked-seat", "bash", "build.sh", "recovery-smoke",
     "--case", "seat-locked", "--timeout", "90"),
    ("qemu-missing-core", "bash", "build.sh", "guest-fixture",
     "--case", "absent"),
    ("qemu-protocol", "bash", "build.sh", "guest-fixture",
     "--case", "protocol"),
    ("qemu-serial-locked", "bash", "build.sh", "guest-fixture",
     "--case", "graphical-locked"),
    ("qemu-read-only-recovery", "bash", "build.sh", "guest-fixture",
     "--case", "recovery-status"),
    ("qemu-graphical-login", "bash", "build.sh", "guest-fixture",
     "--case", "graphical-login"),
    ("qemu-graphical-onboard", "bash", "build.sh", "guest-fixture",
     "--case", "graphical-onboard"),
)


# These scenarios *need* writable state. Never inherit a disk supplied by
# the operator: that could contain private credentials or persistent data.
# Each check receives a brand-new 128 MiB QEMU disk owned by its receipt.
GUEST_WITH_FRESH_STATE = frozenset((
    "qemu-seat-crash", "qemu-persistence", "qemu-ui-preferences",
))

# Only the GUEST entries boot the built native image, so only they are
# subject to the image-provenance gate in main(). Membership in this set —
# not a "qemu-" name prefix — decides that: the HOST list also contains
# qemu-lock and qemu-options, which are host fixtures driving fake
# launchers/emulators and must run at every level, image or not.
GUEST_CHECK_NAMES = frozenset(entry[0] for entry in GUEST)


def checks(level):
    if level not in ("quick", "host", "guest"):
        raise ValueError("invalid offline test level")
    return list(HOST) + (list(RUST) if level != "quick" else []) + (
        list(GUEST) if level == "guest" else []
    )


def git_identity():
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=ROOT, text=True, timeout=5).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT, timeout=5
        )
        diff = subprocess.check_output(["git", "diff", "HEAD", "--binary"],
                                       cwd=ROOT, timeout=5)
        return head, not bool(dirty), hashlib.sha256(diff).hexdigest()
    except (OSError, subprocess.SubprocessError):
        return "unknown", False, "unknown"


def guest_image_matches(image, head, clean):
    """Never certify a stale, dirty or modified native image."""
    if not clean or head == "unknown" or not image.is_file():
        return False
    manifest = image.parent / "manifests" / "build.json"
    try:
        data = json.loads(manifest.read_text())
        if data.get("schema") != "synveil.build/v1":
            return False
        if data.get("git", {}).get("commit") != head:
            return False
        if data.get("git", {}).get("dirty") is not False:
            return False
        entry = data.get("artifacts", {}).get(image.name, {})
        if entry.get("bytes") != image.stat().st_size:
            return False
        digest = hashlib.sha256()
        with image.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""):
                digest.update(chunk)
        return digest.hexdigest() == entry.get("sha256")
    except (OSError, ValueError, TypeError):
        return False


def store(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_check(command, logfile, timeout, environment):
    started = time.monotonic()
    with logfile.open("wb") as output:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except OSError as error:
            output.write(("unable to start: " + str(error) + "\n").encode())
            return "blocked", None, round(time.monotonic() - started, 3)
        try:
            code = process.wait(timeout=timeout)
            status = "pass" if code == 0 else "fail"
        except subprocess.TimeoutExpired:
            status, code = "timeout", None
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return status, code, round(time.monotonic() - started, 3)


def run_guest_with_fresh_state(name, command, logfile, receipt_dir, environment, timeout):
    """Isolate persistent guest scenarios from any operator-supplied disk."""
    if shutil.which("mkfs.ext4") is None:
        logfile.write_text("BLOCKED: mkfs.ext4 is required for an isolated guest state disk\n")
        return "blocked", None, 0.0
    image = receipt_dir / (name + "-state.ext4")
    setup_log = receipt_dir / (name + "-state-setup.log")
    setup_command = [
        sys.executable, str(ROOT / "tools/state_disk.py"),
        "--path", str(image), "--size-mib", "128",
    ]
    setup_status, _, seconds = run_check(setup_command, setup_log, 60, environment)
    if setup_status != "pass":
        logfile.write_text(
            "BLOCKED: could not create an isolated state disk; see " +
            setup_log.name + "\n"
        )
        return "blocked", None, seconds
    guest_env = {**environment, "SYNVEIL_QEMU_STATE_DISK": str(image)}
    return run_check(command, logfile, timeout, guest_env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=("quick", "host", "guest"),
                        default="quick")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    steps = checks(args.level)
    if args.plan:
        for entry in steps:
            print(" ".join(entry))
        return 0
    head, clean, diff_hash = git_identity()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "out" / "local-validation" / (
        timestamp + "-" + head[:8] + "-" + str(os.getpid())
    )
    output.mkdir(parents=True, exist_ok=False)
    receipt_path = output / "results.json"
    receipt = {
        "schema": "synveil.offline-validation/v1",
        "git_commit": head,
        "clean_tracked_checkout": clean,
        "tracked_diff_sha256": diff_hash,
        "level": args.level,
        "status": "incomplete",
        "guest_validated": False,
        "checks": [],
    }
    store(receipt_path, receipt)
    # Do not allow Rustup/Cargo to install anything in an offline pass.
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "CARGO_NET_OFFLINE": "true", "RUSTUP_AUTO_INSTALL": "0"}
    if args.level == "guest":
        # Do not mount the user's disk, even if they have an existing
        # SYNVEIL_QEMU_STATE_DISK setting in the calling shell.
        env.pop("SYNVEIL_QEMU_STATE_DISK", None)
        env["SYNVEIL_QEMU_VIDEO"] = "1"
        env["SYNVEIL_QEMU_NETWORK"] = "1"
        env["SYNVEIL_SMOKE_REQUIRE_DISPLAY"] = "1"
        env["SYNVEIL_SMOKE_REQUIRE_DESKTOP"] = "1"
        env["SYNVEIL_SMOKE_REQUIRE_INPUT"] = "1"
        env["SYNVEIL_SMOKE_REQUIRE_VT"] = "1"
        env["SYNVEIL_SMOKE_REQUIRE_NETWORK"] = "1"
    image = Path(env.get("SYNVEIL_OUT_DIR", str(ROOT / "out"))) / "synveil-x86_64.img"
    guest_matched = guest_image_matches(image, head, clean) if args.level == "guest" else False
    for i, entry in enumerate(steps, start=1):
        name, *command = entry
        logfile = output / f"{i:02d}-{name}.log"
        if name in GUEST_CHECK_NAMES and not guest_matched:
            status, code, elapsed = "blocked", None, 0
            logfile.write_text(
                "BLOCKED: QEMU image or clean build manifest does not match this Git commit\n"
            )
        elif command[0] == "cargo" and shutil.which("cargo") is None:
            status, code, elapsed = "blocked", None, 0
            logfile.write_text("Rust toolchain absent; Rust validation not run\n")
        else:
            limit = max(args.timeout, 2400) if name == "qemu-graphical-login" else args.timeout
            if name in GUEST_WITH_FRESH_STATE:
                status, code, elapsed = run_guest_with_fresh_state(
                    name, command, logfile, output, env, limit
                )
            else:
                status, code, elapsed = run_check(command, logfile, limit, env)
        receipt["checks"].append({
            "name": name, "command": command, "status": status,
            "exit_code": code, "seconds": elapsed, "log": logfile.name,
        })
        store(receipt_path, receipt)
        print(f"[offline] {name}: {status}", flush=True)
    receipt["status"] = ("pass" if all(
        item["status"] == "pass" for item in receipt["checks"]
    ) else "fail")
    receipt["guest_validated"] = (
        args.level == "guest" and receipt["status"] == "pass"
        and guest_matched
    )
    store(receipt_path, receipt)
    print(f"[offline] {receipt['status']} — {receipt_path}")
    if args.level != "guest":
        print("[offline] Host checks do NOT prove a bootable authenticated desktop")
    if not clean:
        print("[offline] Warning: not clean-commit validation evidence")
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
