#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""GitHub-Actions-free host validation. Guest checks require explicit opt-in."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    "native-preflight", "native-build", "native-rootfs", "native-startup",
    "seat-supervisor", "native-smoke", "native-recovery", "qemu-lock",
    "native-protocol-probe", "native-guest-fixture", "graphical-login-fixture",
    "display-build", "ui-preferences-smoke-fixture", "qmp-input",
    "state-mount-safety", "desktop-lifetime", "desktop-supervisor",
    "qemu-options", "offline-validation-fixture",
)
SHELL_FILES = (
    ("bash", "build/scripts/qemu.sh"),
    ("bash", "build/scripts/rootfs.sh"),
    ("bash", "tests/smoke-boot.sh"),
    ("sh", "build/rootfs/init"),
    ("sh", "build/rootfs/start-core"),
    ("sh", "build/rootfs/start-seat"),
    ("sh", "build/rootfs/start-desktop"),
    ("sh", "build/rootfs/mount-state"),
)


def planned_checks(skip_rust=False, guest=False):
    """Return shell-free argv vectors; no GitHub Actions or remote calls."""
    commands = [("bash", "build.sh", "lint")]
    commands += [("python3", f"tests/{name}.py") for name in FIXTURES]
    commands += [(shell, "-n", path) for shell, path in SHELL_FILES]
    if not skip_rust:
        commands += [
            ("cargo", "fmt", "--all", "--check"),
            ("cargo", "test", "--workspace", "--all-targets", "--locked", "--offline"),
            ("cargo", "clippy", "--workspace", "--all-targets", "--locked",
             "--offline", "--", "-D", "warnings"),
        ]
    if guest:
        commands += [
            ("bash", "build.sh", "native-smoke"),
            ("bash", "build.sh", "recovery-smoke", "--case", "seat-locked",
             "--timeout", "60"),
            ("bash", "build.sh", "guest-fixture", "--case", "graphical-login"),
        ]
    return commands


def run_checks(commands, out, timeout=900):
    """Collect all failures and blockers without silently skipping them."""
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CARGO_NET_OFFLINE"] = "true"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    records = []
    for idx, command in enumerate(commands, 1):
        logfile = out / f"{idx:02d}.log"
        started = time.monotonic()
        reason = ""
        result = None
        if not shutil.which(command[0]):
            reason = f"missing executable: {command[0]}"
        else:
            try:
                with logfile.open("wb") as log:
                    result = subprocess.run(
                        command, cwd=ROOT, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=timeout,
                        check=False,
                    ).returncode
            except subprocess.TimeoutExpired:
                reason = "timed out"
            except OSError as error:
                reason = "unable to start: " + type(error).__name__
        status = "passed" if result == 0 else "blocked" if result is None else "failed"
        records.append({
            "argv": list(command), "status": status, "exit_code": result,
            "reason": reason, "seconds": round(time.monotonic() - started, 3),
            "log": logfile.name if logfile.is_file() else None,
            "log_sha256": (hashlib.sha256(logfile.read_bytes()).hexdigest()
                           if logfile.is_file() else None),
        })
        print(f"[synveil-local] {status}: {' '.join(command)}", flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest", action="store_true",
                        help="explicitly run QEMU tests using existing dev image")
    parser.add_argument("--skip-rust", action="store_true",
                        help="partial checks only; never reports overall PASS")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.guest and not (ROOT / "out/synveil-x86_64.img").is_file():
        parser.error("--guest requires an existing developer-QEMU image")
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        revision = "unknown"
    out = args.output or ROOT / "out/offline-validation" / revision[:12]
    checks = run_checks(planned_checks(args.skip_rust, args.guest), out, args.timeout)
    values = [item["status"] for item in checks]
    status = ("failed" if "failed" in values else
              "blocked" if "blocked" in values or args.skip_rust else "passed")
    report = {
        "schema": "synveil.offline-validation/v1",
        "commit": revision, "status": status, "scope": "guest" if args.guest else "host",
        "rust_skipped": args.skip_rust, "github_actions_called": False,
        "checks": checks,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"[synveil-local] {status}: {out / 'report.json'}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
