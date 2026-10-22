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
from qmp_input import QMPError, type_test_secret, capture_test_scanout, open_user_files  # noqa: E402

BOOT = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
        b"SYNVEIL_SEAT_READY", b"SYNVEIL_SERIAL_RECOVERY_LOCKED"}
DENIED = {b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
          b"SYNVEIL_LOGIN_INPUT_UNAVAILABLE"}


def wait_for_visible_scanout(qmp: Path, private_dir: Path, stem: str,
                             *, seconds: float = 6.0, fingerprint=False):
    """Require actual validated RGB scanout, with bounded video-refresh slack.

    A serial READY marker cannot pass this check. Each QMP screenshot is
    examined once and destroyed immediately, even if malformed or blank.
    No screenshot is taken while an input sequence is being typed.
    """
    deadline = time.monotonic() + seconds
    for attempt in range(16):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        filename = private_dir / f"{stem}-{attempt}.ppm"
        proof = capture_test_scanout(
            qmp, filename, timeout=min(2.0, max(1.0, remaining)),
            fingerprint=fingerprint,
        )
        if proof:
            return proof
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))
    return False


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
        # A deliberately mismatching second entry must never provision the
        # account. Retry from the first masked password, then reauthenticate.
        incorrect = secret[:-1] + ("0" if secret[-1] != "0" else "1")
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
                        evidence.write(line.replace(secret.encode(), b"[REDACTED]")
                                       .replace(incorrect.encode(), b"[REDACTED]") + b"\n")
                        evidence.flush()
                        if b"Recovery/bootstrap shell" in line:
                            return False
                        if line.startswith(b"SYNVEIL_"):
                            seen.add(line)
                    if seen & DENIED:
                        return False
                    if b"SYNVEIL_ENROLL_FAILED" in seen and phase != "waiting-wrong-rejection":
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
                        # After cold reboot, deny a deliberately wrong
                        # credential *before* accepting the persisted owner
                        # verifier. First-boot enrollment has its separate
                        # mismatched-confirmation challenge below.
                        # The rebooted graphical login must actually draw to
                        # the QEMU framebuffer, not merely echo a serial
                        # status marker. Capture *before* typing passwords;
                        # the image is checked and immediately discarded.
                        if not fresh:
                            if not wait_for_visible_scanout(
                                qmp, Path(temp), "native-reboot-login"
                            ):
                                evidence.write(b"[synveil] native graphical scanout missing on reboot after bounded refresh\n")
                                return False
                        try:
                            type_test_secret(qmp, secret if fresh else incorrect,
                                             timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "first-password" if fresh else "reboot-wrong-password"
                    if phase == "reboot-wrong-password":
                        # A session opening for the wrong credential must
                        # fail immediately, never be mistaken for success.
                        if b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                            return False
                        if b"SYNVEIL_GRAPHICAL_AUTH_DENIED" in seen:
                            seen.discard(b"SYNVEIL_GRAPHICAL_AUTH_DENIED")
                            try:
                                type_test_secret(qmp, secret, timeout=18)
                            except (QMPError, OSError, ValueError):
                                return False
                            phase = "logging-in"
                    if phase == "first-password" and b"SYNVEIL_ENROLL_CONFIRM_READY" in seen:
                        seen.discard(b"SYNVEIL_ENROLL_CONFIRM_READY")
                        try:
                            type_test_secret(qmp, incorrect, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "waiting-wrong-rejection"
                    if (phase == "waiting-wrong-rejection" and
                        b"SYNVEIL_ENROLL_FAILED" in seen):
                        seen.discard(b"SYNVEIL_ENROLL_FAILED")
                        try:
                            type_test_secret(qmp, secret, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "retry-first-password"
                    if (phase == "retry-first-password" and
                        b"SYNVEIL_ENROLL_CONFIRM_READY" in seen):
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
                    if phase == "logging-in" and b"SYNVEIL_GRAPHICAL_AUTH_DENIED" in seen:
                        # No second fallthrough: the correct saved password
                        # must authenticate on the independent cold boot.
                        return False
                    if phase == "logging-in" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        # A serial marker alone could be emitted while the
                        # seat is still on the login VT. Require independent
                        # RGB QMP scanout after the guest reports success.
                        # Never capture while any credential is being typed.
                        time.sleep(0.6)
                        frame_name = ("native-first-authenticated" if fresh
                                      else "native-reboot-authenticated")
                        baseline = wait_for_visible_scanout(
                            qmp, Path(temp), frame_name, fingerprint=True
                        )
                        if not baseline:
                            evidence.write(b"[synveil] no visible graphical session after login\n")
                            return False
                        # A framebuffer dashboard is not considered interactive
                        # merely because a generic nonblank image was captured.
                        # The test sends only fixed keys to the *authenticated*
                        # UID1000 Files tile and requires a different real RGB
                        # framebuffer. No private filenames are logged.
                        try:
                            open_user_files(qmp, timeout=12)
                        except (QMPError, OSError, ValueError):
                            evidence.write(b"[synveil] native Files navigation input failed\n")
                            return False
                        changed = False
                        for attempt in range(12):
                            proof = capture_test_scanout(
                                qmp, Path(temp) / f"native-files-{attempt}.ppm",
                                timeout=2, fingerprint=True
                            )
                            if proof and proof != baseline:
                                changed = True
                                break
                            time.sleep(0.2)
                        if not changed:
                            evidence.write(b"[synveil] native Files view did not alter graphical scanout\n")
                            return False
                        evidence.write(b"[synveil] authenticated graphical session, RGB scanout and interactive Files tile verified\n")
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
