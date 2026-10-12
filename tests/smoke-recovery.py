#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Kill the guest core and require commands to work in its recovery shell."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, open_demo_terminal  # noqa: E402

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
STATE_RECOVERY = ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
                   b"SYNVEIL_STATE_UNAVAILABLE"},
                  b"SYNVEIL_STATE_READY", b"SYNVEIL_STATE_RECOVERY_OK",
                  "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null && "
                  "printf '%s' shell-alive > /tmp/state-recovery-probe && "
                  "[ \"$(cat /tmp/state-recovery-probe)\" = shell-alive ]; then "
                  "printf '\\n%s%s\\n' SYNVEIL_ STATE_RECOVERY_OK; fi\n")
TERMINAL_LIFETIME = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
     b"SYNVEIL_VT_READY", b"SYNVEIL_DESKTOP_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_TERMINAL_LIFETIME_OK",
    "desktop=$(pidof synveil-display); terminal=; "
    "for retry in $(seq 1 50); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "if [ -n \"$desktop\" ] && [ -n \"$terminal\" ] && "
    "kill -KILL $desktop; then "
    "for retry in $(seq 1 50); do "
    "if ! kill -0 $terminal 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ TERMINAL_LIFETIME_OK; fi; break; fi; "
    "sleep 0.1; done; fi\n"
)
ENROLL_RECOVERY = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
     b"SYNVEIL_SEAT_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_ENROLL_SMOKE_OK",
    "if [ ! -e /var/lib/synveil/auth/credential.phc ]; then "
    "secret=$(cat /proc/sys/kernel/random/uuid); "
    "if [ -n \"$secret\" ] && "
    "printf '%s' \"$secret\" | /usr/sbin/veil-enroll --password-stdin >/tmp/veil-enroll.log 2>&1 && "
    "grep -Fxq SYNVEIL_CREDENTIAL_ENROLLED /tmp/veil-enroll.log && "
    "[ \"$(stat -c '%a:%u:%h' /var/lib/synveil/auth/credential.phc)\" = 600:0:1 ] && "
    "[ \"$(stat -c '%a:%u' /var/lib/synveil/auth)\" = 700:0 ] && "
    "old=$(sha256sum /var/lib/synveil/auth/credential.phc) && "
    "! printf '%s' \"$secret\" | /usr/sbin/veil-enroll --password-stdin >/dev/null 2>&1; then "
    "if [ \"$(sha256sum /var/lib/synveil/auth/credential.phc)\" = \"$old\" ] && "
    "[ -S /run/synveil/veil-seat.sock ]; then "
    "printf '%s' \"$secret\" | /usr/sbin/veil-seat --login-stdin > /tmp/seat-client.log 2>&1 & "
    "holder=$!; terminal=; "
    "for retry in $(seq 1 100); do "
    "for status in /proc/[0-9]*/status; do "
    "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
    "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
    "pid=$(basename $(dirname \"$status\")); "
    "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
    "terminal=$pid; break; fi; fi; done; "
    "[ -n \"$terminal\" ] && break; sleep 0.1; done; "
    "if [ -n \"$terminal\" ] && "
    "grep -Fxq SYNVEIL_SEAT_ACTIVE /tmp/seat-client.log; then "
    "kill -TERM \"$holder\" && wait \"$holder\" 2>/dev/null || true; "
    "for retry in $(seq 1 60); do "
    "if ! kill -0 \"$terminal\" 2>/dev/null || "
    "grep -q '^State:.*Z' /proc/$terminal/status 2>/dev/null; then "
    "if timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ ENROLL_SMOKE_OK; fi; break; fi; "
    "sleep 0.1; done; fi; fi; fi; fi\n"
)
ENROLL_REBOOT = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY"},
    b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_ENROLL_REBOOT_OK",
    "if [ -s /var/lib/synveil/auth/credential.phc ] && "
    "[ \"$(stat -c '%a:%u:%h' /var/lib/synveil/auth/credential.phc)\" = 600:0:1 ] && "
    "grep -q '^[$]argon2id[$]v=19[$]m=65536,t=3,p=1[$]' "
    "/var/lib/synveil/auth/credential.phc; then "
    "printf '\\n%s%s\\n' SYNVEIL_ ENROLL_REBOOT_OK; fi\n"
)
SEAT_LOCKED = (
    {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_SEAT_READY"},
    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_SEAT_LOCKED_OK",
    "if [ -S /run/synveil/veil-seat.sock ] && "
    "[ \"$(stat -c '%a:%u' /run/synveil/veil-seat.sock)\" = 600:0 ] && "
    "[ ! -e /var/lib/synveil/auth/credential.phc ] && "
    "pidof veil-seat >/dev/null && "
    "! printf '%s' guest-no-default-login | /usr/sbin/veil-seat --login-stdin "
    ">/tmp/seat-denied.log 2>&1 && "
    "grep -q 'login denied' /tmp/seat-denied.log && "
    "timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
    "printf '\\n%s%s\\n' SYNVEIL_ SEAT_LOCKED_OK; fi\n"
)
SCENARIOS = {
    "seat-locked": SEAT_LOCKED,
    "enrolled-reboot": ENROLL_REBOOT,
    "enroll": ENROLL_RECOVERY,
    "desktop-terminal": TERMINAL_LIFETIME,
    "state-missing": STATE_RECOVERY,
    "state-corrupt": STATE_RECOVERY,
    "desktop": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
                 b"SYNVEIL_DESKTOP_READY"},
                b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_DESKTOP_RECOVERY_OK",
                "old=$(pidof synveil-display); "
                "if [ -n \"$old\" ] && kill -KILL $old; then "
                "for retry in $(seq 1 45); do "
                "current=$(pidof synveil-display); "
                "if [ -n \"$current\" ] && [ \"$current\" != \"$old\" ] && "
                "timeout -s KILL 2 /usr/bin/synctl --json status >/dev/null; then "
                "printf '\\n%s%s\\n' SYNVEIL_ DESKTOP_RECOVERY_OK; break; fi; "
                "sleep 0.1; done; fi\n"),
    "demo": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY",
              b"SYNVEIL_VT_READY", b"SYNVEIL_DESKTOP_READY"},
             b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_DEMO_OK",
             "if [ -x /usr/bin/synveil-vt ] && "
             "grep -q '^synveil:x:1000:1000:' /etc/passwd; then "
             "/usr/bin/synveil-vt --demo-terminal >/tmp/demo-terminal.log 2>&1 & "
             "for retry in $(seq 1 30); do "
             "for status in /proc/[0-9]*/status; do "
             "if grep -Eq '^Uid:[[:space:]]*1000[[:space:]]+1000' \"$status\" && "
             "grep -Eq '^Gid:[[:space:]]*1000' \"$status\" && "
             "grep -Eq '^NoNewPrivs:[[:space:]]*1$' \"$status\"; then "
             "pid=$(basename $(dirname \"$status\")); "
             "if [ \"$(readlink /proc/$pid/fd/0)\" = /dev/tty1 ]; then "
             "printf '\\n%s%s\\n' SYNVEIL_ DEMO_OK; "
             "break 2; fi; fi; done; "
             "sleep 0.1; done; fi\n"),
    "supervision": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                    b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_SUPERVISION_OK",
                    "is_alive() { kill -0 \"$1\" 2>/dev/null && "
                    "! grep -q '^State:.*Z' /proc/$1/status 2>/dev/null; }; "
                    "supervisor=; worker=; "
                    "for parent in $(pidof veil-core); do "
                    "child=$(cat /proc/$parent/task/$parent/children 2>/dev/null); "
                    "if [ -n \"$child\" ]; then supervisor=$parent; worker=$child; break; fi; done; "
                    "restarted=; "
                    "if [ -n \"$supervisor\" ] && kill -KILL $worker; then "
                    "for retry in $(seq 1 40); do "
                    "child=$(cat /proc/$supervisor/task/$supervisor/children 2>/dev/null); "
                    "if [ -n \"$child\" ] && [ \"$child\" != \"$worker\" ] && "
                    "timeout -s KILL 1 /usr/bin/synctl --json status; then restarted=1; break; fi; "
                    "sleep 0.1; done; fi; "
                    "if [ \"$restarted\" = 1 ]; then "
                    "for round in 1 2 3; do "
                    "child=$(cat /proc/$supervisor/task/$supervisor/children 2>/dev/null); "
                    "[ -n \"$child\" ] && kill -KILL $child; sleep 0.5; done; "
                    "for retry in $(seq 1 20); do "
                    "is_alive $supervisor || break; sleep 0.1; done; "
                    "if ! is_alive $supervisor && "
                    "! timeout -s KILL 2 /usr/bin/synctl --json status; then "
                    "printf '%s' shell-alive > /tmp/supervision-probe && "
                    "[ \"$(cat /tmp/supervision-probe)\" = shell-alive ] && "
                    "printf '\\n%s%s\\n' SYNVEIL_ SUPERVISION_OK; fi; fi\n"),
    "crash": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
              b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_RECOVERY_OK", COMMAND),
    "absent": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_UNAVAILABLE"},
               b"SYNVEIL_CORE_READY", b"SYNVEIL_ABSENT_OK",
               "if [ ! -e /usr/sbin/veil-core ] && ! pidof veil-core; then "
               "printf '%s' shell-alive > /tmp/absent-probe && "
               "[ \"$(cat /tmp/absent-probe)\" = shell-alive ] && "
               "printf '\\n%s%s\\n' SYNVEIL_ ABSENT_OK; fi\n"),
    "observe": ({b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY"},
                b"SYNVEIL_CORE_UNAVAILABLE", b"SYNVEIL_OBSERVE_OK",
                "if timeout -s KILL 2 /usr/bin/synctl --json observe > /tmp/synveil-observe.json && "
                "grep -Fq 'synveil.observe/v1' /tmp/synveil-observe.json && "
                "grep -Fq 'total_bytes' /tmp/synveil-observe.json && "
                "grep -Fq 'total_ticks' /tmp/synveil-observe.json && "
                "grep -Fq 'total_threads' /tmp/synveil-observe.json && "
                "grep -Fq 'average_1m' /tmp/synveil-observe.json; then "
                "printf '\\n%s%s\\n' SYNVEIL_ OBSERVE_OK; fi\n"),
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
    # These cases use only developer QEMU state devices, never host disks.
    guest_env = dict(os.environ)
    corrupt_disk = None
    qmp_directory = None
    if case == "desktop-terminal":
        qmp_directory = tempfile.TemporaryDirectory(prefix="synveil-qmp-")
        qmp_socket = Path(qmp_directory.name) / "monitor.sock"
        guest_env["SYNVEIL_QEMU_QMP_SOCKET"] = str(qmp_socket)
        guest_env["SYNVEIL_QEMU_VIDEO"] = "1"
    if case == "state-missing":
        guest_env.pop("SYNVEIL_QEMU_STATE_DISK", None)
    elif case == "state-corrupt":
        corrupt_disk = tempfile.TemporaryDirectory(prefix="synveil-invalid-ext4-")
        path = Path(corrupt_disk.name) / "invalid.ext4"
        with path.open("wb") as state:
            state.truncate(16 * 1024 * 1024)
        guest_env["SYNVEIL_QEMU_STATE_DISK"] = str(path)
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        env=guest_env,
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
                    if case == "desktop-terminal":
                        seen.discard(b"SYNVEIL_DESKTOP_RESTARTING")
                        try:
                            open_demo_terminal(qmp_socket, timeout=6)
                        except (QMPError, OSError, ValueError) as error:
                            evidence.write(("[synveil-smoke] demo QMP failed: " +
                                            str(error) + "\n").encode())
                            return False
                    # BusyBox's line editor caps each input line. Keep compound
                    # test commands intact, but place shell statements on lines.
                    script = command.replace("; ", ";\n") + "# SYNVEIL_TEST_COMMAND_END\n"
                    process.stdin.write(script.encode())
                    process.stdin.flush()
                    sent = True
                if sent and marker in seen and (
                    case not in ("desktop", "desktop-terminal") or
                    b"SYNVEIL_DESKTOP_RESTARTING" in seen
                ):
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
        if corrupt_disk is not None:
            corrupt_disk.cleanup()
        if qmp_directory is not None:
            qmp_directory.cleanup()


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
    if not passed:
        out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
        name = "recovery" if args.case == "crash" else args.case
        path = out / f"logs/qemu-{name}-smoke.log"
        if path.exists():
            print(path.read_text(errors="replace")[-16000:])
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
