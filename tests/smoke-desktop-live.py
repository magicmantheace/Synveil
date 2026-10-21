#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Disposable QEMU UEFI/XFCE multiwindow proof. No physical disk access.

A user-session script in the developer-only ISO actually opens two windows,
moves/resizes/maximizes/minimizes/restores one, and reports through a fixed
serial status bridge. A build-only or firmware-only screenshot cannot pass.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, _read_reply, emit_command, send_keys  # noqa: E402

PASS = b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS"
FAIL = b"SYNVEIL_QEMU_DESKTOP_PROBE_FAILED"
HOME_WRITTEN = b"SYNVEIL_QEMU_HOME_WRITTEN"
HOME_RECOVERED = b"SYNVEIL_QEMU_HOME_RECOVERED"
HOME_FAILED = b"SYNVEIL_QEMU_HOME_PROOF_FAILED"
LOGIN_LOCKED = b"SYNVEIL_QEMU_LOGIN_GREETER_LOCKED"
LOGIN_FAILED = b"SYNVEIL_QEMU_LOGIN_GREETER_FAILED"
PAM_READY = b"SYNVEIL_QEMU_TEST_AUTH_GREETER_READY"
PAM_ACTIVE = b"SYNVEIL_QEMU_TEST_AUTH_SESSION_ACTIVE"
PAM_FAILED = b"SYNVEIL_QEMU_TEST_AUTH_FAILED"
PAM_RELOCKED = b"SYNVEIL_QEMU_TEST_AUTH_RELOCKED"
PAM_RELOGIN_ACTIVE = b"SYNVEIL_QEMU_TEST_AUTH_RELOGIN_ACTIVE"


def locked_greeter_serial_markers(serial: bytes) -> set[bytes]:
    """Normalize only the known Linux getty login-prompt serial prefix.

    The greeter observer writes a fixed marker to the emulated ttyS0,
    which can share an unfinished 'synveil login: ' prompt. Never accept
    arbitrary suffix matches, terminal echoes or other untrusted text.
    """
    allowed = {LOGIN_LOCKED, LOGIN_FAILED, PAM_READY, PAM_ACTIVE, PAM_FAILED,
               PAM_RELOCKED, PAM_RELOGIN_ACTIVE,
               FAIL, PASS,
               HOME_WRITTEN, HOME_RECOVERED}
    found = set()
    for line in serial.splitlines():
        value = line.strip()
        if value.startswith(b"synveil login: "):
            value = value[len(b"synveil login: "):]
        if value in allowed:
            found.add(value)
    return found


OVMF_PAIRS = (
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
)


def probe_status(serial: bytes) -> str:
    lines = {line.strip() for line in serial.splitlines()}
    if FAIL in lines:
        return "failed"
    if PASS in lines:
        return "passed"
    return "pending"


def screenshot_is_nonblank(ppm: Path) -> bool:
    """Require actual RGB scanout with multiple values, not a QMP acknowledgement."""
    if not ppm.is_file() or ppm.stat().st_size > 24 * 1024 * 1024:
        return False
    raw = ppm.read_bytes()
    header = re.match(rb"P6\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+255\s", raw)
    if not header:
        return False
    width, height = map(int, header.groups())
    if not (320 <= width <= 4096 and 200 <= height <= 2160):
        return False
    pixels = raw[header.end():]
    if len(pixels) != width * height * 3:
        return False
    # Distinct values sampled across the whole surface rule out all-black or
    # single-color firmware screens; the serial proof establishes desktop use.
    return len(set(pixels[::max(1, len(pixels) // 4096)])) >= 12



def is_debian_live_boot_menu(ppm: Path) -> bool:
    """Recognize *only* the distinctive developer ISO's static Debian menu.

    A black background plus the gold Debian live-build logo in the upper
    left is not a desktop boot proof; it permits one harmless Return key.
    No OCR or shell input is used. Fail closed on unknown video output.
    """
    if not ppm.is_file() or ppm.stat().st_size > 24 * 1024 * 1024:
        return False
    raw = ppm.read_bytes()
    header = re.match(rb"P6\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+255\s", raw)
    if not header:
        return False
    width, height = map(int, header.groups())
    if not (640 <= width <= 2048 and 400 <= height <= 1600):
        return False
    data = raw[header.end():]
    if len(data) != width * height * 3:
        return False

    def rgb(x: int, y: int):
        start = (y * width + x) * 3
        return data[start], data[start + 1], data[start + 2]

    black = total = 0
    for y in range(0, height, 16):
        for x in range(0, width, 16):
            total += 1
            if max(rgb(x, y)) < 45:
                black += 1
    if black * 100 < 85 * total:
        return False
    gold = 0
    for y in range(40, 210, 4):
        for x in range(35, 200, 4):
            r, g, b = rgb(x, y)
            if r >= 135 and g >= 90 and b <= 210 and r >= b + 40 and g >= b + 10:
                gold += 1
    return gold >= 400


def advance_known_live_menu(qmp: Path, probe: Path) -> bool:
    """Read private VM video; send ONLY Enter if the known ISO menu matches."""
    capture_screenshot(qmp, probe)
    if not is_debian_live_boot_menu(probe):
        return False
    send_keys(qmp, ("ret",), timeout=8)
    return True


def find_ovmf():
    for code, vars_path in OVMF_PAIRS:
        if Path(code).is_file() and Path(vars_path).is_file():
            return Path(code), Path(vars_path)
    raise RuntimeError("matching OVMF CODE and VARS images not installed")


def capture_screenshot(qmp: Path, output: Path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(6)
        sock.connect(str(qmp))
        with sock.makefile("rb") as reader:
            _read_reply(reader, greeting=True)
            emit_command(sock, reader, {"execute": "qmp_capabilities"})
            emit_command(sock, reader, {
                "execute": "screendump", "arguments": {"filename": str(output)}
            })


def run(iso: Path, evidence: Path, timeout: float, *,
        home_disk: Path | None = None, home_stage: str = "none",
        locked_greeter: bool = False, pam_test: bool = False) -> bool:
    iso = iso.resolve(strict=True)
    if not iso.is_file() or iso.stat().st_size < 1024 * 1024:
        raise ValueError("expected a nonempty ISO file artifact")
    if evidence.exists() or evidence.is_symlink():
        raise ValueError("evidence path already exists; refusing overwrite")
    if timeout < 30 or timeout > 900:
        raise ValueError("timeout must be between 30 and 900 seconds")
    if pam_test and (locked_greeter or home_disk is not None or home_stage != "none"):
        raise ValueError("ephemeral PAM QEMU test never accepts a home disk or lock-gate mode")
    if locked_greeter and (home_disk is not None or home_stage != "none"):
        raise ValueError("locked-greeter VM cannot attach any persistent data disk")
    if home_stage not in ("none", "write", "read"):
        raise ValueError("invalid VM-only home stage")
    if (home_disk is None) != (home_stage == "none"):
        raise ValueError("home disk requires explicit write/read test stage")
    if home_disk is not None:
        # A future caller cannot accidentally pass the owner's block device.
        expected = ROOT / "out/desktop-live/home-persistence.img"
        if (home_disk.is_symlink() or home_disk.parent.is_symlink() or
                home_disk.absolute() != expected or not home_disk.is_file()):
            raise ValueError("refusing a non-prototype or linked home disk")
        disk_stat = home_disk.stat()
        if (not stat.S_ISREG(disk_stat.st_mode) or disk_stat.st_nlink != 1 or
                disk_stat.st_size != 256 * 1024 * 1024 or
                disk_stat.st_mode & 0o077):
            raise ValueError("unsafe VM-only home file")
    code, vars_base = find_ovmf()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    vars_file = evidence / "OVMF_VARS.fd"
    shutil.copyfile(vars_base, vars_file)
    qmp = evidence / "qmp.sock"
    serial = evidence / "serial.log"
    stderr = evidence / "qemu.stderr.log"
    screenshot = evidence / "desktop.ppm"
    authenticated_first_screen = evidence / "first-authenticated-session.ppm"
    relocked_screen = evidence / "relocked-greeter.ppm"
    menu_probe = evidence / "boot-menu-probe.ppm"
    command = [
        "qemu-system-x86_64", "-machine", "q35,accel=kvm:tcg",
        "-m", "3072", "-smp", "2", "-display", "none",
        "-device", "virtio-vga", "-device", "qemu-xhci",
        "-device", "usb-kbd", "-device", "usb-tablet",
        "-monitor", "none", "-qmp", f"unix:{qmp},server=on,wait=off",
        "-serial", f"file:{serial}", "-net", "none",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_file}",
        "-drive", f"file={iso},media=cdrom,readonly=on",
        "-boot", "order=d", "-no-reboot",
    ]
    ephemeral_secret = None
    if pam_test:
        # Generated per QEMU run, supplied by host virtual firmware, never
        # stored in the ISO or printed to logs. NOT production secret storage.
        ephemeral_secret = secrets.token_hex(16)
        command.extend(["-fw_cfg",
                        f"name=opt/synveil/ci-secret,string={ephemeral_secret}"])
    if home_disk is not None:
        # Only a single existing 256MiB regular file, not a device or loop.
        command.extend(["-drive",
                        f"if=virtio,format=raw,cache=writeback,file={home_disk}"])
    with stderr.open("wb") as errors:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errors, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        result = False
        menu_enter_sent = False
        next_menu_probe = time.monotonic() + 6
        pam_stage = "greeter"
        pam_stage_deadline = 0.0

        try:
            while time.monotonic() < deadline:
                output = serial.read_bytes() if serial.is_file() else b""
                status = probe_status(output)
                if pam_test:
                    markers = locked_greeter_serial_markers(output)
                    if PAM_FAILED in markers or FAIL in markers:
                        print("[desktop-qemu] test PAM guest reported failure",
                              file=sys.stderr)
                        break
                    if pam_stage == "greeter":
                        if PAM_ACTIVE in markers:
                            print("[desktop-qemu] auto-login before any password",
                                  file=sys.stderr)
                            break
                        if PAM_READY in markers:
                            # Completely wrong random test password must not
                            # start the session. No real user secret enters QMP.
                            wrong_secret = secrets.token_hex(16)
                            while wrong_secret == ephemeral_secret:
                                wrong_secret = secrets.token_hex(16)
                            from qmp_input import type_test_secret
                            type_test_secret(qmp, wrong_secret, timeout=20)
                            pam_stage = "wrong"
                            pam_stage_deadline = time.monotonic() + 9
                            print("[desktop-qemu] attempted wrong ephemeral PAM "
                                  "test password (value redacted)", flush=True)
                    elif pam_stage == "wrong":
                        if PAM_ACTIVE in markers:
                            print("[desktop-qemu] wrong test password opened session",
                                  file=sys.stderr)
                            break
                        if time.monotonic() >= pam_stage_deadline:
                            type_test_secret(qmp, ephemeral_secret, timeout=20)
                            pam_stage = "correct"
                            pam_stage_deadline = time.monotonic() + 65
                            print("[desktop-qemu] submitted valid ephemeral PAM "
                                  "test password (value redacted)", flush=True)
                    elif pam_stage == "correct":
                        if PAM_ACTIVE in markers:
                            capture_screenshot(qmp, authenticated_first_screen)
                            if (not screenshot_is_nonblank(authenticated_first_screen) or
                                    is_debian_live_boot_menu(authenticated_first_screen)):
                                print("[desktop-qemu] initial PAM graphical image invalid",
                                      file=sys.stderr)
                                break
                            pam_stage = "relock"
                            pam_stage_deadline = time.monotonic() + 135
                            print("[desktop-qemu] PAM password opened non-root GUI; "
                                  "now requiring real logout and return to LightDM",
                                  flush=True)
                        elif time.monotonic() >= pam_stage_deadline:
                            print("[desktop-qemu] correct PAM test password did "
                                  "not open non-root XFCE session", file=sys.stderr)
                            break
                    elif pam_stage == "relock":
                        if PAM_RELOGIN_ACTIVE in markers:
                            print("[desktop-qemu] session reactivated without second "
                                  "password", file=sys.stderr)
                            break
                        if PAM_RELOCKED in markers:
                            capture_screenshot(qmp, relocked_screen)
                            if (not screenshot_is_nonblank(relocked_screen) or
                                    is_debian_live_boot_menu(relocked_screen)):
                                print("[desktop-qemu] no visible graphical greeter "
                                      "after logout", file=sys.stderr)
                                break
                            wrong_secret = secrets.token_hex(16)
                            while wrong_secret == ephemeral_secret:
                                wrong_secret = secrets.token_hex(16)
                            type_test_secret(qmp, wrong_secret, timeout=20)
                            pam_stage = "relock-wrong"
                            pam_stage_deadline = time.monotonic() + 9
                            print("[desktop-qemu] guest logged out to LightDM; "
                                  "wrong second password submitted (redacted)",
                                  flush=True)
                        elif time.monotonic() >= pam_stage_deadline:
                            print("[desktop-qemu] session did not log out and relock",
                                  file=sys.stderr)
                            break
                    elif pam_stage == "relock-wrong":
                        if PAM_RELOGIN_ACTIVE in markers:
                            print("[desktop-qemu] wrong second password opened XFCE",
                                  file=sys.stderr)
                            break
                        if time.monotonic() >= pam_stage_deadline:
                            type_test_secret(qmp, ephemeral_secret, timeout=20)
                            pam_stage = "relogin"
                            pam_stage_deadline = time.monotonic() + 75
                            print("[desktop-qemu] valid second PAM password submitted "
                                  "(value redacted)", flush=True)
                    elif pam_stage == "relogin":
                        if PAM_RELOGIN_ACTIVE in markers:
                            capture_screenshot(qmp, screenshot)
                            result = (screenshot_is_nonblank(screenshot) and
                                      not is_debian_live_boot_menu(screenshot))
                            print("[desktop-qemu] confirmed logout/relock, denied "
                                  "wrong password and accepted second non-root "
                                  "XFCE login: " + ("PASS" if result else "FAIL"),
                                  flush=True)
                            break
                        if time.monotonic() >= pam_stage_deadline:
                            print("[desktop-qemu] second PAM session never started",
                                  file=sys.stderr)
                            break
                if locked_greeter:
                    markers = locked_greeter_serial_markers(output)
                    if (LOGIN_FAILED in markers or FAIL in markers or PASS in markers or
                            HOME_WRITTEN in markers or HOME_RECOVERED in markers):
                        print("[desktop-qemu] login-gate preauthentication violation",
                              file=sys.stderr)
                        break
                    if LOGIN_LOCKED in markers:
                        # A graphics greeter without a real account remains
                        # locked after Return; this is *denial* evidence,
                        # never proof of successful owner authentication.
                        if not qmp.exists():
                            break
                        send_keys(qmp, ("ret",), timeout=8)
                        time.sleep(12)
                        followup = serial.read_bytes() if serial.is_file() else b""
                        lines = locked_greeter_serial_markers(followup)
                        if (PASS in lines or FAIL in lines or LOGIN_FAILED in lines or
                                HOME_WRITTEN in lines or HOME_RECOVERED in lines):
                            print("[desktop-qemu] login gate unlocked unexpectedly",
                                  file=sys.stderr)
                            break
                        try:
                            capture_screenshot(qmp, screenshot)
                            result = (screenshot_is_nonblank(screenshot) and
                                      not is_debian_live_boot_menu(screenshot))
                        except (OSError, QMPError) as exc:
                            print(f"[desktop-qemu] login greeter scanout failed: {exc}",
                                  file=sys.stderr)
                        print("[desktop-qemu] locked LightDM greeter with "
                              "no usable password: " +
                              ("PASS" if result else "FAIL"), flush=True)
                        break
                if not locked_greeter and not pam_test and status == "failed":
                    print("[desktop-qemu] guest window manager probe failed", file=sys.stderr)
                    break
                if not locked_greeter and not pam_test and status == "passed":
                    lines = {line.strip() for line in output.splitlines()}
                    expected_home = {
                        "none": None, "write": HOME_WRITTEN,
                        "read": HOME_RECOVERED,
                    }[home_stage]
                    if HOME_FAILED in lines or (expected_home and
                            (expected_home not in lines or (
                                HOME_WRITTEN in lines and home_stage != "write"
                            ) or (
                                HOME_RECOVERED in lines and home_stage != "read"
                            ))):
                        print("[desktop-qemu] home persistence proof missing, "
                              "failed or returned wrong stage", file=sys.stderr)
                        break
                    if expected_home:
                        print("[desktop-qemu] verified separate home marker: " +
                              expected_home.decode("ascii"), flush=True)
                    if not qmp.exists():
                        print("[desktop-qemu] QMP unavailable despite guest proof",
                              file=sys.stderr)
                        break
                    try:
                        capture_screenshot(qmp, screenshot)
                        result = screenshot_is_nonblank(screenshot)
                    except (OSError, QMPError) as exc:
                        print(f"[desktop-qemu] screenshot capture failed: {exc}",
                              file=sys.stderr)
                    print("[desktop-qemu] guest interactions passed; real scanout "
                          + ("passed" if result else "failed"), flush=True)
                    break
                # UEFI/GRUB live-build menu defaults to waiting indefinitely.
                # Only the recognized gold-logo menu permits a single Return.
                # An unknown screen (including a login prompt) receives no keys.
                if (not menu_enter_sent and qmp.exists() and
                        time.monotonic() >= next_menu_probe):
                    next_menu_probe = time.monotonic() + 6
                    try:
                        menu_enter_sent = advance_known_live_menu(qmp, menu_probe)
                        if menu_enter_sent:
                            print("[desktop-qemu] recognized Debian live menu; "
                                  "sent one Return to boot default entry", flush=True)
                    except (OSError, QMPError) as exc:
                        print(f"[desktop-qemu] boot-menu probe deferred: {exc}",
                              file=sys.stderr)
                    finally:
                        menu_probe.unlink(missing_ok=True)
                if process.poll() is not None:
                    print(f"[desktop-qemu] QEMU exited {process.returncode} without "
                          "multiwindow proof", file=sys.stderr)
                    break
                time.sleep(2)
            else:
                print("[desktop-qemu] timed out before guest multiwindow proof",
                      file=sys.stderr)
        finally:
            # A guest missing its session marker must still yield *visual*
            # evidence. Capture before terminating QEMU, but never mistake
            # an ordinary firmware/login screenshot for a desktop pass.
            if not result and qmp.exists() and not screenshot.exists() and process.poll() is None:
                try:
                    capture_screenshot(qmp, screenshot)
                    print("[desktop-qemu] retained failure screenshot; "
                          "this does not establish graphical login", flush=True)
                except (OSError, QMPError) as exc:
                    print(f"[desktop-qemu] failure screenshot unavailable: {exc}",
                          file=sys.stderr)
            if serial.exists():
                serial_content = serial.read_bytes()
                for marker in (
                    b"SYNVEIL_QEMU_RELAY_STARTED",
                    b"SYNVEIL_QEMU_DESKTOP_PROBE_STARTED",
                    FAIL,
                ):
                    if marker in serial_content:
                        print("[desktop-qemu] diagnostic marker observed: " +
                              marker.decode("ascii"), flush=True)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    print("[desktop-qemu] " + ("PASS" if result else "FAIL"), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=360)
    parser.add_argument("--home-disk", type=Path,
                        help="fixed VM-only out/desktop-live/home-persistence.img")
    parser.add_argument("--home-stage", choices=("none", "write", "read"),
                        default="none")
    parser.add_argument("--locked-greeter", action="store_true",
                        help="separate VM-only no-autologin login denial proof")
    parser.add_argument("--pam-test", action="store_true",
                        help="CI-only ephemeral wrong/correct PAM login proof")
    args = parser.parse_args()
    try:
        okay = run(args.iso, args.evidence, args.timeout,
                   home_disk=args.home_disk, home_stage=args.home_stage,
                   locked_greeter=args.locked_greeter, pam_test=args.pam_test)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
