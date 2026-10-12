#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Two QEMU boots: enroll via locked-serial framebuffer, then reauthenticate."""
import argparse
import os
from pathlib import Path
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, type_test_secret  # noqa: E402

BOOT = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
        b"SYNVEIL_SEAT_READY", b"SYNVEIL_SERIAL_RECOVERY_LOCKED"}
DENIED = {b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER", b"SYNVEIL_ENROLL_FAILED",
          b"SYNVEIL_LOGIN_INPUT_UNAVAILABLE"}


def guest_boot(out: Path, secret: str, *, fresh: bool, timeout: float) -> bool:
    output = out / "logs" / ("onboard-first.log" if fresh else "onboard-reboot.log")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synveil-onboard-qmp-") as temp:
        qmp = Path(temp) / "monitor.sock"
        env = {**os.environ, "SYNVEIL_QEMU_VIDEO": "1",
               "SYNVEIL_QEMU_QMP_SOCKET": str(qmp)}
        process = subprocess.Popen(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        phase = "boot"
        seen = set()
        pending = b""
        try:
            with selectors.DefaultSelector() as selector, output.open("wb") as evidence:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    events = selector.select(min(0.2, max(0.0, deadline - time.monotonic())))
                    if not events:
                        if process.poll() is not None:
                            break
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        break
                    pending += data
                    if len(pending) > 65536:
                        return False
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        line = line.rstrip(b"\r")
                        evidence.write(line.replace(secret.encode(), b"[REDACTED]") + b"\n")
                        evidence.flush()
                        if b"Recovery/bootstrap shell" in line:
                            return False
                        if line.startswith(b"SYNVEIL_"):
                            seen.add(line)
                    if seen & DENIED:
                        return False
                    if not fresh and b"SYNVEIL_ENROLL_READY" in seen:
                        return False
                    if phase == "boot" and BOOT <= seen:
                        if fresh:
                            if b"SYNVEIL_ENROLL_READY" not in seen:
                                continue
                        else:
                            if b"SYNVEIL_LOGIN_READY" not in seen:
                                continue
                        try:
                            type_test_secret(qmp, secret, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "first-password" if fresh else "logging-in"
                    if phase == "first-password" and b"SYNVEIL_ENROLL_CONFIRM_READY" in seen:
                        try:
                            type_test_secret(qmp, secret, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "confirmation"
                    if (phase == "confirmation" and
                        b"SYNVEIL_ENROLL_COMPLETED" in seen and
                        b"SYNVEIL_LOGIN_READY" in seen):
                        try:
                            type_test_secret(qmp, secret, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "logging-in"
                    if phase == "logging-in" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        evidence.write(b"[synveil] authenticated graphical session reached\n")
                        return True
                evidence.write(("[synveil] incomplete: " + phase + "\n").encode())
                return False
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=105)
    args = parser.parse_args()
    if args.timeout < 20:
        parser.error("QEMU onboarding timeout must be at least 20 seconds")
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    disk = Path(os.environ["SYNVEIL_QEMU_STATE_DISK"])
    if not disk.is_file() or disk.is_symlink():
        parser.error("onboarding requires a fresh isolated QEMU state disk")
    secret = secrets.token_hex(16)
    if not guest_boot(out, secret, fresh=True, timeout=args.timeout):
        print("[synveil] locked-serial graphical first-user enrollment failed")
        return 1
    if not guest_boot(out, secret, fresh=False, timeout=args.timeout):
        print("[synveil] rebooted graphical password login failed")
        return 1
    print("[synveil] locked-serial graphical enrollment survived independent QEMU reboot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
