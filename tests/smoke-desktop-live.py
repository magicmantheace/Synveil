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
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, _read_reply, emit_command  # noqa: E402

PASS = b"SYNVEIL_QEMU_DESKTOP_MULTIWINDOW_PASS"
FAIL = b"SYNVEIL_QEMU_DESKTOP_PROBE_FAILED"
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


def run(iso: Path, evidence: Path, timeout: float) -> bool:
    iso = iso.resolve(strict=True)
    if not iso.is_file() or iso.stat().st_size < 1024 * 1024:
        raise ValueError("expected a nonempty ISO file artifact")
    if evidence.exists() or evidence.is_symlink():
        raise ValueError("evidence path already exists; refusing overwrite")
    if timeout < 30 or timeout > 900:
        raise ValueError("timeout must be between 30 and 900 seconds")
    code, vars_base = find_ovmf()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    vars_file = evidence / "OVMF_VARS.fd"
    shutil.copyfile(vars_base, vars_file)
    qmp = evidence / "qmp.sock"
    serial = evidence / "serial.log"
    stderr = evidence / "qemu.stderr.log"
    screenshot = evidence / "desktop.ppm"
    command = [
        "qemu-system-x86_64", "-accel", "tcg", "-machine", "q35",
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
    with stderr.open("wb") as errors:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errors, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        result = False
        try:
            while time.monotonic() < deadline:
                output = serial.read_bytes() if serial.is_file() else b""
                status = probe_status(output)
                if status == "failed":
                    print("[desktop-qemu] guest window manager probe failed", file=sys.stderr)
                    break
                if status == "passed":
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
    args = parser.parse_args()
    try:
        okay = run(args.iso, args.evidence, args.timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
