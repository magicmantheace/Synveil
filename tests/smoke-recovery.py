#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Kill the guest core and require commands to work in its recovery shell."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (
    "core_pid=$(pidof veil-core); "
    "if [ -n \"$core_pid\" ] && kill -KILL $core_pid; then "
    "for retry in 1 2 3 4 5 6 7 8 9 10; do "
    "kill -0 $core_pid 2>/dev/null || break; sleep 0.1; done; "
    "if ! kill -0 $core_pid 2>/dev/null && "
    "! timeout -s KILL 2 /usr/bin/synctl --json status; then "
    "mkdir -p /tmp/recovery-check && "
    "printf '%s' shell-alive > /tmp/recovery-check/probe && "
    "[ \"$(cat /tmp/recovery-check/probe)\" = shell-alive ] && "
    "printf '\\n%s%s\\n' SYNVEIL_ RECOVERY_OK; fi; fi\n"
)
SCENARIOS = {
    "crash": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
              b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_RECOVERY_OK", COMMAND),
    "absent": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_UNAVAILABLE"},
               b"SYNVEIL_CORE_READY", b"SYNVEIL_ABSENT_OK",
               "if [ ! -e /usr/sbin/veil-core ] && ! pidof veil-core; then "
               "printf '%s' shell-alive > /tmp/absent-probe && "
               "[ \"$(cat /tmp/absent-probe)\" = shell-alive ] && "
               "printf '\\n%s%s\\n' SYNVEIL_ ABSENT_OK; fi\n"),
    "protocol": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                 b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_PROTOCOL_OK",
                 "if timeout -s KILL 10 /usr/libexec/synveil-test/protocol-probe && "
                 "timeout -s KILL 2 /usr/bin/synctl --json status; then "
                 "printf '\\n%s%s\\n' SYNVEIL_ PROTOCOL_OK; fi\n"),
}


def smoke(timeout_seconds, case="crash"):
    required, forbidden, marker, command = SCENARIOS[case]
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    log = out / "logs" / ("qemu-recovery-smoke.log" if case == "crash"
                          else f"qemu-{case}-smoke.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_seconds
    seen = set()
    pending = b""
    sent = False
    try:
        with selectors.DefaultSelector() as selector, log.open("wb") as evidence:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(min(0.2, max(0, deadline - time.monotonic())))
                if not events:
                    if process.poll() is not None:
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                evidence.write(chunk)
                evidence.flush()
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    seen.add(line.rstrip(b"\r"))
                    if len(seen) > 4096:
                        seen = {value for value in seen if value.startswith(b"SYNVEIL_")}
                if forbidden in seen:
                    return False
                if not sent and required <= seen:
                    seen.discard(marker)
                    process.stdin.write(command.encode())
                    process.stdin.flush()
                    sent = True
                if sent and marker in seen:
                    return True
                if len(pending) > 65536:
                    return False
        return False
    finally:
        # Terminate QEMU and its shell wrapper together, including on timeout.
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
    parser.add_argument("--case", choices=SCENARIOS, default="crash")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("SYNVEIL_SMOKE_TIMEOUT", "60")))
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    passed = smoke(args.timeout, args.case)
    print(f"[synveil] QEMU {args.case} smoke " + ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
