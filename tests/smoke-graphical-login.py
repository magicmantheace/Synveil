#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QMP-drive a fresh test credential through the *actual* graphical login.

The host secret is generated independently for every VM run and is never
embedded in a Synveil image, source artifact, or recorded console log.
Only developer-QEMU localhost serial/QMP controls are used.
"""
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
from qmp_input import QMPError, type_test_secret, exit_authenticated_terminal  # noqa: E402

REQUIRED = {
    b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
    b"SYNVEIL_STATE_READY", b"SYNVEIL_SEAT_READY", b"SYNVEIL_LOGIN_READY",
    b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
}

# Pure guest-side process, privilege, VT and crash-cleanup checks. No
# password strings or hashes are ever copied back through these assertions.
CLEANUP = (
    "terminal=; "
    "for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^Gid:[[:space:]]*1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "display=$(pidof synveil-display); "
    "if [ -n \"$terminal\" ] && [ -n \"$display\" ] && "
    "kill -KILL $display; then "
    "for retry in $(seq 1 70); do "
    "if ! kill -0 \"$terminal\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ GRAPHICAL_LOGIN_CLEANED; fi; break; fi; "
    "sleep 0.1; done; fi\n"
)


# Wait until the actual non-root interactive shell owns tty1 before
# sending the fixed QMP "exit" sequence. ACTIVE is emitted earlier than
# the terminal finishes VT activation, so a blind QMP send would race it.
CHECK_TTY = (
    "ready=0; for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^Gid:[[:space:]]*1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "ready=1; break; fi; fi; done; "
    "[ \"$ready\" = 1 ] && break; sleep 0.1; done; "
    "[ \"$ready\" = 1 ] && printf '\\n%s\\n' SYNVEIL_AUTH_TTY_READY\\n"
)

# A terminated session must not leave any *living* UID1000 terminal.
# Exclude zombies, which can remain temporarily awaiting init reaping.
CHECK_RELOCK = (
    "active=0; for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "! grep -Eq '^State:[[:space:]]*Z' \"$status\"; then active=1; fi; done; "
    "[ \"$active\" = 0 ] && printf '\\n%s\\n' SYNVEIL_RELOCK_NO_USER_SHELL\\n"
)

def smoke(timeout_seconds: float) -> bool:
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    log = out / "logs/qemu-graphical-login-smoke.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    state = Path(os.environ["SYNVEIL_QEMU_STATE_DISK"])
    if not state.is_file() or state.is_symlink():
        raise ValueError("graphical-login smoke needs a private QEMU state file")
    secret = secrets.token_hex(16)  # 128-bit test-only secret, not an account password
    incorrect = secrets.token_hex(16)
    while incorrect == secret:
        incorrect = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="synveil-auth-qmp-") as temp:
        qmp_socket = Path(temp) / "monitor.sock"
        env = {**os.environ, "SYNVEIL_QEMU_QMP_SOCKET": str(qmp_socket),
               "SYNVEIL_QEMU_VIDEO": "1"}
        process = subprocess.Popen(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        pending = b""
        seen = set()
        phase = "boot"
        try:
            with selectors.DefaultSelector() as selector, log.open("wb") as evidence:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    events = selector.select(min(0.2, max(0.0, deadline - time.monotonic())))
                    if not events:
                        if process.poll() is not None:
                            break
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending += chunk
                    if len(pending) > 65536:
                        return False
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        line = line.rstrip(b"\r")
                        # Redact even if the guest's terminal unexpectedly
                        # echoed a secret across multiple read() chunks.
                        evidence.write(line.replace(secret.encode(), b"[REDACTED]")
                                       .replace(incorrect.encode(), b"[REDACTED]") + b"\n")
                        evidence.flush()
                        if line.startswith(b"SYNVEIL_"):
                            seen.add(line)
                    if phase == "boot" and REQUIRED <= seen:
                        # Guest confirms echo is disabled *before* the host
                        # sends any test credential bytes to serial.
                        process.stdin.write(
                            b"stty -echo && printf '\\n%s\\n' SYNVEIL_INPUT_PRIVATE\n"
                        )
                        process.stdin.flush()
                        phase = "private"
                    if phase == "private" and b"SYNVEIL_INPUT_PRIVATE" in seen:
                        cmd = (
                            "printf '%s' '" + secret +
                            "' | /usr/sbin/veil-enroll --password-stdin "
                            ">/tmp/graphical-enroll.log 2>&1 && "
                            "grep -Fxq SYNVEIL_CREDENTIAL_ENROLLED "
                            "/tmp/graphical-enroll.log && "
                            "printf '\\n%s\\n' SYNVEIL_GRAPHICAL_ENROLLED\n"
                        )
                        process.stdin.write(cmd.encode())
                        process.stdin.flush()
                        phase = "enrolling"
                    if phase == "enrolling" and b"SYNVEIL_GRAPHICAL_ENROLLED" in seen:
                        try:
                            type_test_secret(qmp_socket, incorrect, timeout=17)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] QMP login: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "denying"
                    if phase == "denying" and b"SYNVEIL_GRAPHICAL_AUTH_DENIED" in seen:
                        # No non-root shell may exist before the correct secret.
                        process.stdin.write(
                            b"if ! grep -l '^Uid:[[:space:]]*1000[[:space:]]*1000' "
                            b"/proc/[0-9]*/status >/dev/null 2>&1; then "
                            b"printf '\\n%s\\n' SYNVEIL_WRONG_PASSWORD_LOCKED; fi\n"
                        )
                        process.stdin.flush()
                        phase = "denial-check"
                    if phase == "denial-check" and b"SYNVEIL_WRONG_PASSWORD_LOCKED" in seen:
                        try:
                            type_test_secret(qmp_socket, secret, timeout=17)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] valid QMP login: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "logging-in"
                    if phase == "logging-in" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        process.stdin.write(CHECK_TTY.encode())
                        process.stdin.flush()
                        phase = "waiting-for-tty"
                    if phase == "waiting-for-tty" and b"SYNVEIL_AUTH_TTY_READY" in seen:
                        try:
                            exit_authenticated_terminal(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] normal logout: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "logging-out"
                    if phase == "logging-out" and b"SYNVEIL_LOGIN_RELOCKED" in seen:
                        process.stdin.write(CHECK_RELOCK.encode())
                        process.stdin.flush()
                        phase = "verifying-relock"
                    if phase == "verifying-relock" and b"SYNVEIL_RELOCK_NO_USER_SHELL" in seen:
                        try:
                            type_test_secret(qmp_socket, secret, timeout=17)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] second login: " +
                                            str(error) + "\n").encode())
                            return False
                        # Clear the first ACTIVE from the marker set so this
                        # phase cannot be advanced by stale serial evidence.
                        seen.discard(b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE")
                        phase = "logging-in-again"
                    if phase == "logging-in-again" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        # Re-verify UID, NoNewPrivs and tty1 after the second
                        # login; forcibly kill the UI to prove lease cleanup.
                        script = CLEANUP.replace("; ", ";\n") + "# SYNVEIL_TEST_COMMAND_END\n"
                        process.stdin.write(script.encode())
                        process.stdin.flush()
                        phase = "cleanup"
                    if phase == "cleanup" and b"SYNVEIL_GRAPHICAL_LOGIN_CLEANED" in seen:
                        return True
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
    parser.add_argument("--timeout", type=float, default=150.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    passed = smoke(args.timeout)
    print("[synveil] isolated QEMU graphical-login " +
          ("passed" if passed else "failed"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
