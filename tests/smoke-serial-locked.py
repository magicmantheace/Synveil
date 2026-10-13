#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Refuse a root serial command in an explicitly locked graphical VM.

This is a negative security test. It runs only against a disposable
graphical-locked fixture, never a physical device or an owner installation.
"""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
    b"SYNVEIL_LOGIN_READY", b"SYNVEIL_SERIAL_RECOVERY_LOCKED",
}
FORBIDDEN = {
    b"SYNVEIL_SERIAL_UNLOCK_BYPASS",
    b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
}
RECOVERY_REQUIRED = {
    b"SYNVEIL_RECOVERY_STATUS_V1",
    b"SYNVEIL_RECOVERY_STATE_NOT_MOUNTED",
    b"SYNVEIL_RECOVERY_CORE_UNAVAILABLE",
    b"SYNVEIL_RECOVERY_SEAT_UNAVAILABLE",
    b"SYNVEIL_RECOVERY_READ_ONLY",
    b"SYNVEIL_SERIAL_RECOVERY_LOCKED",
}
RECOVERY_FORBIDDEN = FORBIDDEN | {
    b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_SEAT_READY",
    b"SYNVEIL_LOGIN_READY", b"SYNVEIL_DESKTOP_READY",
    b"SYNVEIL_STATE_READY",
}
# String concatenation makes an echoed serial input distinguishable from the
# line that an actual privileged shell would print when executing it.
PROBE = "printf '\\n%s%s\\n' SYNVEIL_ SERIAL_UNLOCK_BYPASS\n"
OBSERVATION_SECONDS = 3.0


def smoke(timeout_seconds: float, *, recovery_status: bool = False) -> bool:
    required = RECOVERY_REQUIRED if recovery_status else REQUIRED
    forbidden = RECOVERY_FORBIDDEN if recovery_status else FORBIDDEN
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    log = out / "logs/qemu-serial-locked-smoke.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["SYNVEIL_QEMU_VIDEO"] = "1"
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )
    seen = set()
    pending = b""
    deadline = time.monotonic() + timeout_seconds
    injected_at = None
    try:
        with selectors.DefaultSelector() as selector, log.open("wb") as evidence:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if injected_at is not None and (
                    time.monotonic() - injected_at >= OBSERVATION_SECONDS
                ):
                    return not bool(forbidden & seen)
                events = selector.select(0.1)
                if not events:
                    if process.poll() is not None:
                        return False
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    return False
                evidence.write(chunk)
                evidence.flush()
                pending += chunk
                if len(pending) > 65536:
                    return False
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    line = line.rstrip(b"\r")
                    if b"Recovery/bootstrap shell" in line:
                        return False
                    seen.add(line)
                if forbidden & seen:
                    return False
                if injected_at is None and required <= seen:
                    process.stdin.write(PROBE.encode())
                    process.stdin.flush()
                    injected_at = time.monotonic()
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--recovery-status", action="store_true")
    args = parser.parse_args()
    if args.timeout <= OBSERVATION_SECONDS:
        parser.error("timeout must allow the observation window")
    passed = smoke(args.timeout, recovery_status=args.recovery_status)
    print("[synveil] serial-root lock smoke " + ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
