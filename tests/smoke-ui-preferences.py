#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Prove the framebuffer menu loads saved preferences after an independent QEMU boot.

The first boot seeds only a home file through serial recovery. Real QMP input
must change and save settings in the framebuffer menu. The second boot proves
that the graphical launcher accepts and loads those settings as well as the
home file; the C host tests separately exercise the atomic settings writer.
"""
import argparse
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, settings_toggle  # noqa: E402
BOOT = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_STATE_READY",
        b"SYNVEIL_DESKTOP_READY", b"SYNVEIL_HOME_READY"}
ERRORS = {b"SYNVEIL_STATE_UNAVAILABLE", b"SYNVEIL_DISPLAY_UNAVAILABLE",
          b"SYNVEIL_HOME_UNAVAILABLE"}
MARKER = b"SYNVEIL_UI_PREFS_WRITE_OK"
SEEDED = b"SYNVEIL_HOME_SEEDED"
LOADED = b"SYNVEIL_SETTINGS_LOADED"
HOME = b"SYNVEIL_UI_PREFS_HOME_OK"

# The first boot writes only the demo home from the serial recovery shell.
# Display settings MUST be saved by the actual framebuffer menu after
# QMP sends the same keyboard events as a user. No pre-seeded settings.
WRITE = (
    "umask 077; "
    "printf '%s' synveil-home-v1 > /home/synveil/roundtrip && "
    "chown 1000:1000 /home/synveil/roundtrip && "
    "sync && printf '\\n%s%s\\n' SYNVEIL_ HOME_SEEDED\n"
)

VERIFY = (
    "i=0; while [ \"$i\" -lt 50 ]; do "
    "if grep -Fxq 'contrast=1' /var/lib/synveil/desktop-pref.conf && "
    "grep -Fxq 'cursor_scale=1' /var/lib/synveil/desktop-pref.conf; then "
    "sync; printf '\\n%s%s\\n' SYNVEIL_ UI_PREFS_WRITE_OK; break; fi; "
    "i=$((i+1)); sleep 0.1; done\n"
)


READ_HOME = (
    "if [ \"$(cat /home/synveil/roundtrip)\" = synveil-home-v1 ] && "
    "[ \"$(stat -c %u:%g /home/synveil/roundtrip)\" = 1000:1000 ]; then "
    "printf '\\n%s%s\\n' SYNVEIL_ UI_PREFS_HOME_OK; fi\n"
)


def round_trip(state: Path, timeout: float, phase: int, logfile: Path) -> bool:
    monitor = tempfile.TemporaryDirectory(prefix="synveil-qmp-")
    qmp_path = Path(monitor.name) / "monitor.sock"
    launch_env = {**os.environ, "SYNVEIL_QEMU_STATE_DISK": str(state),
                  "SYNVEIL_QEMU_VIDEO": "1"}
    if phase == 1:
        launch_env["SYNVEIL_QEMU_QMP_SOCKET"] = str(qmp_path)
    process = subprocess.Popen(
        ["bash", str(ROOT / "build/scripts/qemu.sh")],
        env=launch_env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    required = BOOT | ({LOADED} if phase == 2 else set())
    seen = set()
    pending = b""
    issued = False
    keyboard_sent = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector, logfile.open("wb") as evidence:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                available = selector.select(min(0.2, max(0, deadline - time.monotonic())))
                if not available:
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
                        seen = {s for s in seen if s.startswith(b"SYNVEIL_")}
                if seen & ERRORS:
                    return False
                if phase == 2 and not issued and required <= seen:
                    process.stdin.write(READ_HOME.encode())
                    process.stdin.flush()
                    issued = True
                if phase == 2 and issued and HOME in seen:
                    return True
                if phase == 1 and not issued and required <= seen:
                    process.stdin.write(WRITE.encode())
                    process.stdin.flush()
                    issued = True
                if phase == 1 and issued and not keyboard_sent and SEEDED in seen:
                    try:
                        settings_toggle(qmp_path, timeout=6)
                    except (QMPError, OSError, ValueError) as exc:
                        evidence.write(("[synveil-smoke] QMP input failed: " +
                                        str(exc) + "\n").encode())
                        return False
                    process.stdin.write(VERIFY.encode())
                    process.stdin.flush()
                    keyboard_sent = True
                if phase == 1 and keyboard_sent and MARKER in seen:
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
        monitor.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--disk", type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    out = Path(os.environ.get("SYNVEIL_OUT_DIR", ROOT / "out"))
    state = args.disk or out / "synveil-state.ext4"
    if not state.is_file() or state.is_symlink():
        parser.error("a QEMU ext4 state disk is required (bash build.sh state-disk)")
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for phase, name in ((1, "write"), (2, "load")):
        log = logs / f"qemu-ui-preferences-{name}.log"
        if not round_trip(state, args.timeout, phase, log):
            print(f"[synveil] UI preferences QEMU {name} failed: {log}")
            print(log.read_text(errors="replace")[-14000:])
            return 1
    print("[synveil] keyboard-saved UI settings and non-root home survived two QEMU boots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
