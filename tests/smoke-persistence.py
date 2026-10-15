#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Prove that /var survives two independently launched QEMU boot sessions."""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_STATE_READY"}
FORBIDDEN = b"SYNVEIL_STATE_UNAVAILABLE"


def power_cut(process) -> bool:
    """Kill only the wrapper's QEMU child; wait for the wrapper to reap it."""
    try:
        children = []
        entries = list(Path("/proc").iterdir())
        if len(entries) > 4096:
            return False
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                status = (entry / "status").read_text()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            if any(line.split() == ["PPid:", str(process.pid)] for line in status.splitlines()):
                children.append(entry.name)
        if len(children) != 1:
            return False
        child = int(children[0])
        descriptor = os.pidfd_open(child)
        try:
            arguments = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
            if (os.getpgid(child) != process.pid or not arguments[0] or
                    Path(os.fsdecode(arguments[0])).name != "qemu-system-x86_64"):
                return False
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        finally:
            os.close(descriptor)
        # qemu.sh waits for its emulator child and returns its shell status.
        return process.wait(timeout=3) == 128 + signal.SIGKILL
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def boot_round(disk: Path, marker: bytes, command: str, timeout: float, log: Path,
               *, abrupt=False, required=REQUIRED) -> bool:
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        env={**os.environ, "SYNVEIL_QEMU_STATE_DISK": str(disk)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
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
                        seen = {line for line in seen if line.startswith(b"SYNVEIL_")}
                if FORBIDDEN in seen:
                    return False
                if not sent and required <= seen:
                    # Assemble marker at runtime; don't accept console-echoed text.
                    process.stdin.write(command.encode())
                    process.stdin.flush()
                    sent = True
                if sent and marker in seen:
                    if abrupt:
                        if not power_cut(process):
                            return False
                        evidence.write(b"[synveil-smoke] emulator SIGKILL and wrapper reap confirmed\n")
                        evidence.flush()
                    return True
                if len(pending) > 65536:
                    return False
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
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--disk", type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    disk = args.disk or out / "synveil-state.ext4"
    if not disk.is_file() or disk.is_symlink():
        parser.error("create a new QEMU state disk first: bash build.sh state-disk")
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    rounds = (
        (b"SYNVEIL_PERSIST_WRITE_OK",
         "printf '%s' synveil-persistence-v1 > /var/lib/synveil/roundtrip && "
         "sync && test -s /var/log/synveil/core-audit.jsonl && "
         "[ \"$(stat -c '%a:%u:%h' /var/log/synveil/core-audit.jsonl)\" = 600:0:1 ] && "
         "grep -Fq '\"schema\":\"synveil.audit/v1\"' /var/log/synveil/core-audit.jsonl && "
         "/usr/sbin/veil-audit check && "
         "printf '\\n%s%s\\n' SYNVEIL_ PERSIST_WRITE_OK\n",
         logs / "qemu-persistence-write.log"),
        (b"SYNVEIL_PERSIST_READ_OK",
         "if [ \"$(cat /var/lib/synveil/roundtrip)\" = synveil-persistence-v1 ] && "
         "/usr/sbin/veil-audit check; then "
         "printf '\\n%s%s\\n' SYNVEIL_ PERSIST_READ_OK; fi\n",
         logs / "qemu-persistence-read.log"),
    )
    for marker, command, log in rounds:
        if not boot_round(disk, marker, command, args.timeout, log):
            print(f"[synveil] QEMU persistence check failed: {log}")
            print(log.read_text(errors="replace")[-12000:])
            return 1
    print("[synveil] QEMU state survived two independent guest boots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
