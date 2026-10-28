#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Joined image acceptance: two QEMU boots of the authenticated Weston
image (tools/native_joined.py), state on the image's own partition.

Boot 1 enrolls through the graphical login (including a mismatched
confirmation that must be rejected), logs in, and proves the Weston
desktop behind the authenticated session: two visible windows, real
pointer move/resize, Escape closing the focused window, an independent
terminal executing `exit`, and the session ending back at the locked
graphical login. Boot 2 is a cold boot of the same image file: no
enrollment may be offered, a wrong password must be denied, and the
correct password must reach the same desktop — proving the credential
and state persisted on storage the image itself provides. No separate
state disk is attached in either boot.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import (  # noqa: E402
    QMPError, type_test_secret, capture_test_scanout, send_keys,
    ALLOWED_TERMINAL_RETURN)

_spec = importlib.util.spec_from_file_location(
    "smoke_native_weston", ROOT / "tests" / "smoke-native-weston.py")
weston_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(weston_smoke)

IMAGE_DIR = ROOT / "out" / "native-joined"
BOOT = {b"SYNVEIL_BOOT_OK", b"SYNVEIL_CORE_READY", b"SYNVEIL_STATE_READY",
        b"SYNVEIL_SEAT_READY", b"SYNVEIL_SERIAL_RECOVERY_LOCKED"}
DENIED = {b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
          b"SYNVEIL_LOGIN_INPUT_UNAVAILABLE"}


def weston_proof(qmp: Path, folder: Path, *, full: bool) -> bool:
    """Pixel-level Weston session proof, mirroring the fixture smoke's
    evidence standard on the joined image. Returns True once both
    initial windows have closed (the session is ending)."""
    count = 0

    def frame():
        nonlocal count
        count += 1
        return weston_smoke.screenshot(qmp, folder, count)

    def valid_frame(timeout_s):
        # Before Weston presents its first frame (and briefly while it
        # repaints) the scanout is blank and the weston helper raises;
        # that is "not ready", not a proof failure. Poll for a real
        # frame instead of aborting the whole acceptance.
        end = time.monotonic() + timeout_s
        while True:
            try:
                return frame()
            except ValueError:
                if time.monotonic() >= end:
                    raise
                time.sleep(0.25)

    deadline = time.monotonic() + 120
    initial = None
    while time.monotonic() < deadline:
        try:
            width, height, data = frame()
        except ValueError:
            time.sleep(0.25)
            continue
        window = weston_smoke.original_window(width, height, data)
        if window and weston_smoke.terminal_pixels_visible(width, height, data, window):
            initial = (width, height, window)
            break
        time.sleep(0.25)
    if initial is None:
        return False
    width, height, (x, y, w, h) = initial
    dx = -80 if x > 100 else 80
    dy = -60 if y > 90 else 60
    weston_smoke.drag(qmp, width, height, (x + 30, y + 14),
                      (x + 30 + dx, y + 14 + dy))
    time.sleep(0.3)
    frame_data = valid_frame(10)
    moved = weston_smoke.original_window(*frame_data)
    if not moved or abs(moved[0] - x) < 30 or abs(moved[1] - y) < 20:
        return False
    x, y, w, h = moved
    if full:
        dx = 64 if width - x - w > 90 else -64
        dy = 48 if height - y - h > 70 else -48
        weston_smoke.drag(qmp, width, height, (x + w - 10, y + h - 10),
                          (x + w - 10 + dx, y + h - 10 + dy))
        time.sleep(0.3)
        frame_data = valid_frame(10)
        resized = weston_smoke.original_window(*frame_data)
        if not resized or abs(resized[2] - w) < 20 or abs(resized[3] - h) < 20:
            return False
        x, y, w, h = resized
    weston_smoke.pointer(qmp, width, height, x + 30, y + 14, button=True)
    weston_smoke.pointer(qmp, width, height, x + 30, y + 14, button=False)
    send_keys(qmp, ('esc',), timeout=5)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if weston_smoke.original_window(*frame()) is None:
                break
        except ValueError:
            break  # blank scanout: the window is gone with the desktop
        time.sleep(0.25)
    else:
        return False
    frame_data = valid_frame(10)
    try:
        tx, ty = weston_smoke.terminal_point(*frame_data)
    except RuntimeError:
        return False
    weston_smoke.pointer(qmp, frame_data[0], frame_data[1], tx, ty, button=True)
    weston_smoke.pointer(qmp, frame_data[0], frame_data[1], tx, ty, button=False)
    send_keys(qmp, ('e', 'x', 'i', 't', 'ret'), timeout=5,
              allowed=ALLOWED_TERMINAL_RETURN)
    # Both initial windows are now closed, so the session script ends
    # the session; the caller confirms teardown by the graphical
    # login's serial relock marker.
    time.sleep(3)
    return True


def guest_boot(work: Path, secret: str, *, fresh: bool, timeout: float,
               evidence_dir: Path) -> bool:
    output = evidence_dir / ("joined-first.log" if fresh else "joined-reboot.log")
    with tempfile.TemporaryDirectory(prefix="sjq-") as temp:
        qmp = Path(temp) / "monitor.sock"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("SYNVEIL_")}
        env.update(SYNVEIL_OUT_DIR=str(work), SYNVEIL_QEMU_VIDEO="1",
                   SYNVEIL_QEMU_DISPLAY="none", SYNVEIL_QEMU_NETWORK="0",
                   SYNVEIL_QEMU_MEMORY="768M",
                   SYNVEIL_QEMU_IMAGE_WRITABLE="1",
                   SYNVEIL_QEMU_QMP_SOCKET=str(qmp))
        process = subprocess.Popen(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        deadline = time.monotonic() + timeout
        phase = "boot"
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
                    if not fresh and b"SYNVEIL_ENROLL_READY" in seen:
                        return False
                    if phase == "boot" and BOOT <= seen:
                        if fresh:
                            if b"SYNVEIL_ENROLL_READY" not in seen:
                                continue
                            secret_to_type = secret
                        else:
                            if b"SYNVEIL_LOGIN_READY" not in seen:
                                continue
                            scanout_ok = False
                            for _attempt in range(10):
                                if capture_test_scanout(
                                        qmp, Path(temp) / "reboot-login.ppm",
                                        timeout=2):
                                    scanout_ok = True
                                    break
                                time.sleep(0.3)
                            if not scanout_ok:
                                evidence.write(b"[synveil] no graphical scanout at reboot login\n")
                                return False
                            secret_to_type = incorrect
                        try:
                            type_test_secret(qmp, secret_to_type, timeout=18)
                        except (QMPError, OSError, ValueError):
                            return False
                        phase = "first-password" if fresh else "wrong-password"
                    if phase == "wrong-password":
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
                        phase = "waiting-mismatch-rejection"
                    if (phase == "waiting-mismatch-rejection" and
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
                        return False
                    if phase == "logging-in" and b"SYNVEIL_GRAPHICAL_AUTH_ACTIVE" in seen:
                        # Authenticated. Prove the Weston desktop with
                        # real scanout and input before accepting.
                        time.sleep(0.6)
                        if not weston_proof(qmp, Path(temp), full=fresh):
                            evidence.write(b"[synveil] weston desktop proof failed\n")
                            return False
                        phase = "awaiting-relock"
                    if phase == "awaiting-relock" and b"SYNVEIL_LOGIN_RELOCKED" in seen:
                        evidence.write(b"[synveil] session ended and graphical login relocked\n")
                        return True
                evidence.write(("[synveil] incomplete: " + phase + "\n").encode())
                return False
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=360)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("joined smoke requires a non-root runner")
    if args.timeout < 120:
        parser.error("joined smoke timeout must be at least 120 seconds per boot")
    proof = json.loads((IMAGE_DIR / "validation.json").read_text())
    source_image = IMAGE_DIR / "synveil-x86_64.img"
    if (proof.get("schema") != "synveil.native-joined-image/v1" or
            proof.get("boot_target") != "qemu" or
            source_image.is_symlink() or not source_image.is_file() or
            hashlib.sha256(source_image.read_bytes()).hexdigest()
            != proof.get("image_sha256")):
        parser.error("joined image missing or changed since build")
    evidence_dir = IMAGE_DIR / "smoke-evidence"
    evidence_dir.mkdir(exist_ok=True)
    secret = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="synveil-joined-") as temp:
        work = Path(temp)
        # Boot a private copy: the built artifact stays pristine and the
        # copy's digest must match the build record before any boot.
        target = work / "synveil-x86_64.img"
        with source_image.open("rb") as source, target.open("xb") as dest:
            shutil.copyfileobj(source, dest)
        if hashlib.sha256(target.read_bytes()).hexdigest() != proof["image_sha256"]:
            print("[synveil] joined image copy digest mismatch")
            return 1
        if not guest_boot(work, secret, fresh=True, timeout=args.timeout,
                          evidence_dir=evidence_dir):
            print("[synveil] joined first boot (enroll + weston session) failed")
            return 1
        if not guest_boot(work, secret, fresh=False, timeout=args.timeout,
                          evidence_dir=evidence_dir):
            print("[synveil] joined cold reboot (persisted login + weston) failed")
            return 1
    result = {"schema": "synveil.native-joined-guest/v1",
              "source_commit": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "image_sha256": proof["image_sha256"],
              "enrollment_graphical": True,
              "mismatched_confirmation_rejected": True,
              "wrong_password_denied_after_cold_reboot": True,
              "weston_desktop_after_login": True,
              "pointer_move_resize_keyboard": True,
              "session_end_relocks_login": True,
              "state_on_image_provided_partition": True,
              "separate_state_disk_used": False,
              "physical_hardware_tested": False}
    (IMAGE_DIR / "guest-validation.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print("SYNVEIL_NATIVE_JOINED_QEMU_ENROLL_LOGIN_WESTON_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
