#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QMP-drive graphical login, VT-switch auto-lock, logout and crash cleanup.

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
from qmp_input import (QMPError, type_test_secret, logout_user_desktop,
                       open_user_terminal, exit_user_terminal,
                       switch_to_login_vt)  # noqa: E402

REQUIRED = {
    b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
    b"SYNVEIL_STATE_READY", b"SYNVEIL_SEAT_READY", b"SYNVEIL_LOGIN_READY",
    b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
}
RETURN_FAILURES = {
    b"SYNVEIL_USER_RETURN_FAILED_IDENTITY_MISSING",
    b"SYNVEIL_USER_RETURN_FAILED_SHELL_ALIVE",
    b"SYNVEIL_USER_RETURN_FAILED_DASHBOARD_UNAVAILABLE",
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
    "login_display=; for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*0[[:space:]]+0' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if tr '\\000' ' ' < /proc/$pid/cmdline | "
    "grep -Fq 'synveil-display --login'; then "
    "login_display=$pid; break; fi; fi; done; "
    "if [ -n \"$terminal\" ] && [ -n \"$login_display\" ] && "
    "kill -KILL \"$login_display\"; then "
    "for retry in $(seq 1 70); do "
    "if ! kill -0 \"$terminal\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ GRAPHICAL_LOGIN_CLEANED; fi; break; fi; "
    "sleep 0.1; done; fi\n"
)


# Wait until the actual non-root *graphical* user session owns tty1 before
# sending QMP navigation or logout. ACTIVE precedes VT activation and exec,
# so a blind QMP send could hit the wrong terminal or login surface.
CHECK_TTY = (
    "ready=0; for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^Gid:[[:space:]]*1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ] && "
    "tr '\\000' ' ' < /proc/$pid/cmdline | grep -Fq -- '--session'; then "
    "ready=1; break; fi; fi; done; "
    "[ \"$ready\" = 1 ] && break; sleep 0.1; done; "
    "[ \"$ready\" = 1 ] && printf '\\n%s\\n' SYNVEIL_AUTH_TTY_READY\n"
)

# Confirm QMP opened a *child* UID1000 interactive shell of the fixed user
# dashboard rather than an unauthenticated root/developer terminal.
# Hard-kill only the running non-root dashboard while its tty is raw.
# A following independent login must still be able to open a terminal.
CRASH_USER = (
    "user_session=; for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if tr '\\000' ' ' < /proc/$pid/cmdline | "
    "grep -Fq -- 'synveil-display --session'; then "
    "user_session=$pid; break; fi; fi; done; "
    "if [ -n \"$user_session\" ] && kill -KILL \"$user_session\"; then "
    "printf '\\n%s\\n' SYNVEIL_USER_DASHBOARD_KILLED; fi\n"
)

# A newly forked interactive shell may flush queued tty input during
# initialization. Confirm the privileged-boundary identity still holds
# after a short stabilization window before sending QMP exit keystrokes.
CHECK_USER_TERMINAL = (
    "user_session=; for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if tr '\\000' ' ' < /proc/$pid/cmdline | "
    "grep -Fq -- 'synveil-display --session'; then "
    "user_session=$pid; break; fi; fi; done; "
    "shell_pid=; for retry in $(seq 1 100); do "
    "if [ -n \"$user_session\" ]; then "
    "for child in $(cat /proc/$user_session/task/$user_session/children 2>/dev/null); do "
    "if [ \"$(cat /proc/$child/comm 2>/dev/null)\" = sh ] && "
    "grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' "
    "/proc/$child/status 2>/dev/null && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' "
    "/proc/$child/status 2>/dev/null; then shell_pid=$child; break; fi; done; fi; "
    "[ -n \"$shell_pid\" ] && break; sleep 0.1; done; "
    "if [ -n \"$user_session\" ] && [ -n \"$shell_pid\" ]; then "
    "sleep 0.5; "
    "if kill -0 \"$shell_pid\" 2>/dev/null && "
    "grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' "
    "\"/proc/$shell_pid/status\" 2>/dev/null && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' "
    "\"/proc/$shell_pid/status\" 2>/dev/null && "
    "[ \"$(readlink /proc/$shell_pid/fd/0)\" = /dev/tty1 ]; then "
    "printf '\\n%s\\n' SYNVEIL_USER_TERMINAL_READY; fi; fi\n"
)

# A foreground shell exit must return to the SAME graphical user process.
CHECK_USER_RETURNED = (
    "returned=; "
    "if [ -n \"$user_session\" ] && [ -n \"$shell_pid\" ]; then "
    "for retry in $(seq 1 100); do "
    "if ! kill -0 \"$shell_pid\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$shell_pid/status 2>/dev/null; then "
    "if kill -0 \"$user_session\" 2>/dev/null && "
    "! grep -q '^State:.*Z' /proc/$user_session/status 2>/dev/null && "
    "tr '\\000' ' ' < /proc/$user_session/cmdline | "
    "grep -Fq -- 'synveil-display --session'; then "
    "returned=1; printf '\\n%s\\n' SYNVEIL_USER_TERMINAL_RETURNED; fi; break; fi; "
    "sleep 0.1; done; fi; "
    "if [ \"$returned\" != 1 ]; then "
    "reason=DASHBOARD_UNAVAILABLE; "
    "if [ -z \"$user_session\" ] || [ -z \"$shell_pid\" ]; then reason=IDENTITY_MISSING; "
    "elif kill -0 \"$shell_pid\" 2>/dev/null && "
    "! grep -q '^State:.*Z' /proc/$shell_pid/status 2>/dev/null; then reason=SHELL_ALIVE; fi; "
    "printf '\\n%s%s%s\\n' SYNVEIL_ USER_RETURN_FAILED_ \"$reason\"; fi\n"
)

# A terminated session must not leave any *living* UID1000 terminal.
# Exclude zombies, which can remain temporarily awaiting init reaping.
CHECK_RELOCK = (
    "active=0; for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "! grep -Eq '^State:[[:space:]]*Z' \"$status\"; then active=1; fi; done; "
    "[ \"$active\" = 0 ] && printf '\\n%s\\n' SYNVEIL_RELOCK_NO_USER_SHELL\n"
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
                    if RETURN_FAILURES & seen:
                        evidence.write(("[synveil] terminal return failed in phase: " +
                                        phase + "\n").encode())
                        return False
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
                            switch_to_login_vt(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] VT switch: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "switching-vt"
                    if phase == "switching-vt" and b"SYNVEIL_LOGIN_RELOCKED" in seen:
                        process.stdin.write(CHECK_RELOCK.encode())
                        process.stdin.flush()
                        phase = "verifying-vt-relock"
                    if phase == "verifying-vt-relock" and b"SYNVEIL_RELOCK_NO_USER_SHELL" in seen:
                        # The old graphical user session must die on VT switch
                        # without relying on normal logout or a GUI crash.
                        seen.discard(b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE")
                        seen.discard(b"SYNVEIL_LOGIN_RELOCKED")
                        seen.discard(b"SYNVEIL_AUTH_TTY_READY")
                        seen.discard(b"SYNVEIL_RELOCK_NO_USER_SHELL")
                        try:
                            type_test_secret(qmp_socket, secret, timeout=17)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] second login: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "logging-in-again"
                    if phase == "logging-in-again" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        process.stdin.write(CHECK_TTY.encode())
                        process.stdin.flush()
                        phase = "waiting-for-second-tty"
                    if phase == "waiting-for-second-tty" and b"SYNVEIL_AUTH_TTY_READY" in seen:
                        try:
                            open_user_terminal(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] open user terminal: " +
                                            str(error) + "\n").encode())
                            return False
                        process.stdin.write(CHECK_USER_TERMINAL.encode())
                        process.stdin.flush()
                        phase = "checking-user-terminal"
                    if phase == "checking-user-terminal" and b"SYNVEIL_USER_TERMINAL_READY" in seen:
                        try:
                            exit_user_terminal(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] return from user terminal: " +
                                            str(error) + "\n").encode())
                            return False
                        process.stdin.write(CHECK_USER_RETURNED.encode())
                        process.stdin.flush()
                        phase = "checking-user-return"
                    if phase == "checking-user-return" and b"SYNVEIL_USER_TERMINAL_RETURNED" in seen:
                        try:
                            logout_user_desktop(qmp_socket, timeout=8)
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
                            evidence.write(("[synveil] third login: " +
                                            str(error) + "\n").encode())
                            return False
                        seen.discard(b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE")
                        seen.discard(b"SYNVEIL_AUTH_TTY_READY")
                        seen.discard(b"SYNVEIL_LOGIN_RELOCKED")
                        seen.discard(b"SYNVEIL_RELOCK_NO_USER_SHELL")
                        phase = "logging-in-third-time"
                    if phase == "logging-in-third-time" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        process.stdin.write(CHECK_TTY.encode())
                        process.stdin.flush()
                        phase = "waiting-for-third-tty"
                    if phase == "waiting-for-third-tty" and b"SYNVEIL_AUTH_TTY_READY" in seen:
                        # Kill UID1000 display in raw tty mode; the root login
                        # must re-lock before an independent fourth login.
                        process.stdin.write(CRASH_USER.encode())
                        process.stdin.flush()
                        phase = "crashing-user-desktop"
                    if (phase == "crashing-user-desktop" and
                        b"SYNVEIL_USER_DASHBOARD_KILLED" in seen and
                        b"SYNVEIL_LOGIN_RELOCKED" in seen):
                        process.stdin.write(CHECK_RELOCK.encode())
                        process.stdin.flush()
                        phase = "checking-user-crash-relock"
                    if (phase == "checking-user-crash-relock" and
                        b"SYNVEIL_RELOCK_NO_USER_SHELL" in seen):
                        for marker in (b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE",
                                       b"SYNVEIL_AUTH_TTY_READY",
                                       b"SYNVEIL_USER_TERMINAL_READY",
                                       b"SYNVEIL_USER_TERMINAL_RETURNED"):
                            seen.discard(marker)
                        try:
                            type_test_secret(qmp_socket, secret, timeout=17)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] fourth login after user crash: " +
                                            str(error) + "\n").encode())
                            return False
                        phase = "logging-in-fourth-time"
                    if phase == "logging-in-fourth-time" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        process.stdin.write(CHECK_TTY.encode())
                        process.stdin.flush()
                        phase = "waiting-for-fourth-tty"
                    if phase == "waiting-for-fourth-tty" and b"SYNVEIL_AUTH_TTY_READY" in seen:
                        try:
                            open_user_terminal(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] terminal after user crash: " +
                                            str(error) + "\n").encode())
                            return False
                        process.stdin.write(CHECK_USER_TERMINAL.encode())
                        process.stdin.flush()
                        phase = "checking-post-crash-terminal"
                    if (phase == "checking-post-crash-terminal" and
                        b"SYNVEIL_USER_TERMINAL_READY" in seen):
                        try:
                            exit_user_terminal(qmp_socket, timeout=8)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil] post-crash terminal exit: " +
                                            str(error) + "\n").encode())
                            return False
                        process.stdin.write(CHECK_USER_RETURNED.encode())
                        process.stdin.flush()
                        phase = "checking-post-crash-return"
                    if (phase == "checking-post-crash-return" and
                        b"SYNVEIL_USER_TERMINAL_RETURNED" in seen):
                        # Destroy root graphical login while a *different*
                        # user process is active; surviving UID1000 tasks
                        # would violate the broker's socket-lease contract.
                        seen.discard(b"SYNVEIL_LOGIN_READY")
                        script = CLEANUP.replace("; ", ";\n") + "# SYNVEIL_TEST_COMMAND_END\n"
                        process.stdin.write(script.encode())
                        process.stdin.flush()
                        phase = "cleanup"
                    if (phase == "cleanup" and
                        b"SYNVEIL_GRAPHICAL_LOGIN_CLEANED" in seen and
                        b"SYNVEIL_LOGIN_READY" in seen):
                        return True
                evidence.write(("[synveil] incomplete graphical-login phase: " +
                                phase + "\n").encode())
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
