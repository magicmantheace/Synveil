#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Hardware-profile joined image acceptance in QEMU/OVMF.

The hardware-profile image (tools/native_joined.py --boot-target
hardware) pins its console to tty0 (CONFIG_CMDLINE console=tty0), so
the serial-marker state machine of tests/smoke-native-joined.py
cannot observe it: the guest emits nothing on the serial port. This
driver therefore synchronizes on the framebuffer scanout itself,
using the deterministic layout of the graphical auth screens
(src/veil-display/main.c auth_render): a dark background, a top band,
and title/subtitle/footer text bands at fixed fractional positions.
Screens are identified *relationally* (the enrollment, confirmation
and login bands differ from each other and are stable across
repaints), never by pinned hashes, so the test does not depend on a
particular GOP resolution or font rasterization.

Two device variants are supported via --variant: "virtio" (the
original acceptance configuration: virtio-vga + virtio-blk) and
combinations selecting the firmware-framebuffer display path
(--graphics vga: standard VGA, for which the hardware kernel has no
native DRM driver, so the display runs through simpledrm on the EFI
framebuffer) and/or USB boot storage (--image-usb: the whole image
attached over emulated USB storage). Each variant writes its own
guest-validation JSON and evidence directory; the default variant
keeps the historical file names.

Three boots of private copies of the hardware image:

1. Fresh: early-boot frames must show a live tty0 text console on the
   UEFI framebuffer before the greeter takes over; graphical
   enrollment (with a mismatched confirmation visibly rejected and a
   SETUP RETRY indication), login, the Weston desktop proof (pointer
   move/resize, keyboard input, both windows closed), and session-end
   relock to the same login screen.
2. Cold reboot of the same copy: the first stable screen must be the
   boot-1 login screen (no enrollment is offered again), a wrong
   password is visibly denied, the correct password reaches the
   desktop, and a hard power cut during the session (the emulator is
   killed — build/scripts/qemu.sh runs with -no-reboot, so an
   in-guest reset would end the emulator instead) returns the guest
   to the same login screen with the credential intact.
3. A copy whose state partition filesystem is destroyed host-side
   before boot: the guest must offer the login screen (never
   enrollment), deny the test password, never reach a desktop, and
   never print a shell banner or developer marker on serial.

Serial output is captured for every boot: it may contain firmware
(OVMF) lines only. Any guest shell banner, developer-recovery marker
or guest boot marker on serial fails the run — the production image
has no serial console and no developer serial recovery.

Nothing here validates physical hardware: QEMU's GOP framebuffer,
virtio storage and USB input stand in for the TG01's RTX 3060 GOP,
USB SSD and HID devices. The record states that explicitly.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, type_test_secret, send_keys, \
    ALLOWED_TERMINAL_RETURN  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "smoke_native_weston", ROOT / "tests" / "smoke-native-weston.py")
weston_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(weston_smoke)

_spec2 = importlib.util.spec_from_file_location(
    "smoke_native_joined", ROOT / "tests" / "smoke-native-joined.py")
joined_smoke = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(joined_smoke)

IMAGE_DIR = ROOT / "out" / "native-joined-hardware"
STATE_PARTUUID = "7c9e2d41-8b3a-4f5e-a6d1-2e8c4b7f9053"

# QEMU device configuration for this run, set by main() from
# --graphics/--image-usb before any Guest is constructed.
QEMU_GRAPHICS = "virtio"
QEMU_IMAGE_USB = False

# auth_render colors (src/veil-display/main.c): background, top band,
# muted text (subtitle and the denied/retry line).
BG = (12, 21, 34)
BAND = (21, 51, 78)
MUTED = (134, 171, 190)
DENIED_MIN_PIXELS = 100

FORBIDDEN_SERIAL = (b"Recovery/bootstrap shell",
                    b"SYNVEIL_SERIAL_RECOVERY_DEVELOPER",
                    b"SYNVEIL_BOOT_OK")


def band_digest(data, width, y0, y1):
    return hashlib.md5(data[y0 * width * 3:y1 * width * 3]).hexdigest()


def count_color(data, width, height, color, x0, x1, y0, y1):
    found = 0
    for y in range(max(0, y0), min(height, y1)):
        base = y * width * 3
        for x in range(max(0, x0), min(width, x1)):
            off = base + x * 3
            if tuple(data[off:off + 3]) == color:
                found += 1
    return found


def signature(width, height, data):
    """Structural signature of one framebuffer frame."""
    total = width * height
    bg = count_color(data, width, height, BG, 0, width, 0, height)
    strip = height // 11 + 2
    band = count_color(data, width, height, BAND, 0, width, 0, strip)
    x_denied = int(500 * width / 1280)
    denied = count_color(data, width, height, MUTED, int(30 * width / 1280),
                         x_denied, height * 3 // 5 - 6, height * 3 // 5 + 30)
    return {
        "width": width, "height": height,
        "bg_frac": bg / total,
        "band_frac": band / (width * strip),
        "title": band_digest(data, width, height // 4 - 6, height // 4 + 44),
        "subtitle": band_digest(data, width, height // 3 - 6,
                                height // 3 + 30),
        "footer": band_digest(data, width, height * 4 // 5 - 6,
                              height * 4 // 5 + 30),
        "denied_px": denied,
    }


def is_auth(sig):
    return sig["bg_frac"] > 0.75 and sig["band_frac"] > 0.9


def same_screen(a, b):
    return (a["title"] == b["title"] and a["subtitle"] == b["subtitle"]
            and a["footer"] == b["footer"])


class Guest:
    """One QEMU process booting a private image copy, scanout-driven."""

    def __init__(self, work: Path, evidence: Path):
        self.work = work
        self.evidence = evidence
        self.serial = bytearray()
        self._lock = threading.Lock()
        self._frame_no = 0
        self.process = None
        self.qmp = None

    def __enter__(self):
        # Directly under TMPDIR, not inside the image work dir:
        # build/scripts/qemu.sh caps the QMP socket path at 100 chars.
        temp = tempfile.TemporaryDirectory(prefix="sjh-")
        self._temp = temp
        qmp = Path(temp.name) / "monitor.sock"
        self.qmp = qmp
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("SYNVEIL_")}
        env.update(SYNVEIL_OUT_DIR=str(self.work),
                   SYNVEIL_QEMU_VIDEO="1",
                   SYNVEIL_QEMU_GRAPHICS=QEMU_GRAPHICS,
                   SYNVEIL_QEMU_IMAGE_USB="1" if QEMU_IMAGE_USB else "0",
                   SYNVEIL_QEMU_DISPLAY="none", SYNVEIL_QEMU_NETWORK="0",
                   SYNVEIL_QEMU_MEMORY="768M",
                   SYNVEIL_QEMU_IMAGE_WRITABLE="1",
                   SYNVEIL_QEMU_QMP_SOCKET=str(qmp))
        self.process = subprocess.Popen(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        self._drain = threading.Thread(target=self._pump, daemon=True)
        self._drain.start()
        return self

    def _pump(self):
        stream = self.process.stdout
        handle = self.evidence.open("wb")
        try:
            while True:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    break
                with self._lock:
                    self.serial += chunk
                    if len(self.serial) > 4 * 1024 * 1024:
                        del self.serial[:2 * 1024 * 1024]
                handle.write(chunk.replace(b"\r", b""))
                handle.flush()
        finally:
            handle.close()

    def __exit__(self, *exc):
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
        self._temp.cleanup()
        return False

    def frame(self):
        self._frame_no += 1
        path = Path(self._temp.name) / f"frame-{self._frame_no}.ppm"
        weston_smoke.qmp_command(self.qmp, {
            "execute": "screendump", "arguments": {"filename": str(path)}})
        try:
            return weston_smoke.pixels(path)
        finally:
            path.unlink(missing_ok=True)

    def sig(self):
        width, height, data = self.frame()
        return signature(width, height, data)

    def wait_for(self, describe, predicate, deadline_s):
        """Poll the scanout until predicate(sig) holds; return the sig."""
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError(f"guest exited while waiting for {describe}")
            try:
                sig = self.sig()
            except Exception:  # noqa: BLE001 - blank/transitioning scanout
                time.sleep(0.5)
                continue
            if predicate(sig):
                return sig
            time.sleep(0.8)
        raise RuntimeError(f"timed out waiting for {describe}")

    def wait_stable_auth(self, deadline_s):
        """Two consecutive identical auth-screen signatures."""
        end = time.monotonic() + deadline_s
        previous = None
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError("guest exited before the auth screen")
            try:
                sig = self.sig()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            if is_auth(sig):
                if previous is not None and same_screen(previous, sig):
                    return sig
                previous = sig
            time.sleep(1.2)
        raise RuntimeError("auth screen never became stable")

    def wait_desktop(self, deadline_s):
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            try:
                width, height, data = self.frame()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            window = weston_smoke.original_window(width, height, data)
            if window and weston_smoke.terminal_pixels_visible(
                    width, height, data, window):
                return True
            time.sleep(0.8)
        return False

    def hard_stop(self):
        """Simulate a power cut: SIGKILL, no guest shutdown at all."""
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=8)

    def serial_bytes(self):
        with self._lock:
            return bytes(self.serial)

    def check_serial(self):
        blob = self.serial_bytes()
        for marker in FORBIDDEN_SERIAL:
            if marker in blob:
                raise RuntimeError(f"forbidden serial content: {marker!r}")


def corrupt_state_partition(image: Path):
    """Zero the start of the image's state partition (GPT identity)."""
    with image.open("r+b") as handle:
        handle.seek(512)
        header = handle.read(92)
        if header[:8] != b"EFI PART":
            raise RuntimeError("image has no GPT header")
        entry_lba = int.from_bytes(header[72:80], "little")
        entry_count = int.from_bytes(header[80:84], "little")
        entry_size = int.from_bytes(header[84:88], "little")
        target = uuid.UUID(STATE_PARTUUID).bytes_le
        found = []
        for index in range(entry_count):
            handle.seek(entry_lba * 512 + index * entry_size)
            entry = handle.read(entry_size)
            if entry[16:32] == target:
                first = int.from_bytes(entry[32:40], "little")
                last = int.from_bytes(entry[40:48], "little")
                found.append((first, last))
        if len(found) != 1:
            raise RuntimeError("state partition not unique in image GPT")
        first, last = found[0]
        if (last - first + 1) * 512 < 65536:
            raise RuntimeError("state partition too small to corrupt")
        handle.seek(first * 512)
        handle.write(b"\0" * 65536)


def boot_one_fresh(work, evidence_dir, secret, wrong, timeout):
    """Boot 1: console evidence, enrollment, login, desktop, relock."""
    facts = {}
    with Guest(work, evidence_dir / "hardware-first.log") as guest:
        # Pre-greeter frames: the tty0 text console on the UEFI
        # framebuffer must be live and changing (kernel/boot text),
        # and none of those frames may be the auth screen yet.
        early = set()
        end = time.monotonic() + timeout
        enroll = None
        while time.monotonic() < end:
            if guest.process.poll() is not None:
                raise RuntimeError("guest exited during first boot")
            try:
                sig = guest.sig()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            if is_auth(sig):
                enroll = guest.wait_stable_auth(30)
                break
            early.add((sig["title"], sig["subtitle"]))
            time.sleep(0.8)
        if enroll is None:
            raise RuntimeError("enrollment screen never appeared")
        facts["early_console_distinct_frames"] = len(early)
        if len(early) < 2:
            raise RuntimeError("no live tty0 console frames before greeter")
        facts["resolution"] = [enroll["width"], enroll["height"]]
        # Stage 1 -> stage 2: only the subtitle band may change.
        type_test_secret(guest.qmp, secret, timeout=18)
        confirm = guest.wait_for(
            "enrollment confirmation stage",
            lambda s: is_auth(s) and s["title"] == enroll["title"]
            and s["subtitle"] != enroll["subtitle"], 60)
        # Mismatched confirmation: visibly rejected, back at stage 1
        # with the retry indication in the denied band.
        type_test_secret(guest.qmp, wrong, timeout=18)
        guest.wait_for(
            "mismatch rejection",
            lambda s: is_auth(s) and s["subtitle"] == enroll["subtitle"]
            and s["denied_px"] > DENIED_MIN_PIXELS, 60)
        facts["mismatched_confirmation_rejected"] = True
        # Correct pair: stage 2 again, then the login screen (title
        # and footer both change).
        type_test_secret(guest.qmp, secret, timeout=18)
        guest.wait_for(
            "confirmation stage after retry",
            lambda s: is_auth(s) and s["subtitle"] == confirm["subtitle"], 60)
        type_test_secret(guest.qmp, secret, timeout=18)
        login = guest.wait_for(
            "login screen after enrollment",
            lambda s: is_auth(s) and s["title"] != enroll["title"]
            and s["footer"] != enroll["footer"], 60)
        login = guest.wait_stable_auth(30)
        if not same_screen(login, guest.sig()):
            raise RuntimeError("login screen not stable after enrollment")
        # Login -> Weston desktop proof -> session end relocks.
        type_test_secret(guest.qmp, secret, timeout=18)
        if not joined_smoke.weston_proof(guest.qmp, Path(guest._temp.name),
                                         full=True):
            raise RuntimeError("weston desktop proof failed (boot 1)")
        guest.wait_for("relock after session end",
                       lambda s: is_auth(s) and same_screen(s, login), 120)
        facts["login_screen"] = {k: login[k] for k in
                                 ("title", "subtitle", "footer")}
        facts["enroll_screen"] = {k: enroll[k] for k in
                                  ("title", "subtitle", "footer")}
        guest.check_serial()
    return facts


def boot_two_reboot(work, evidence_dir, secret, wrong, login_screen, timeout):
    """Boot 2: cold reboot, wrong-password denial, power-cut recovery."""
    with Guest(work, evidence_dir / "hardware-reboot.log") as guest:
        first = guest.wait_stable_auth(timeout)
        if not same_screen(first, login_screen):
            raise RuntimeError(
                "cold reboot did not return to the boot-1 login screen")
        type_test_secret(guest.qmp, wrong, timeout=18)
        guest.wait_for(
            "wrong password denial",
            lambda s: is_auth(s) and s["denied_px"] > DENIED_MIN_PIXELS
            and s["title"] == login_screen["title"], 60)
        # Correct password -> desktop, then cut the power mid-session.
        type_test_secret(guest.qmp, secret, timeout=18)
        if not guest.wait_desktop(120):
            raise RuntimeError("desktop did not appear after cold reboot")
        guest.check_serial()
        guest.hard_stop()
    # Cold boot after the power cut, same image copy: the login screen
    # must return and the credential must have survived uncleanly.
    with Guest(work, evidence_dir / "hardware-powercut.log") as guest:
        back = guest.wait_stable_auth(timeout)
        if not same_screen(back, login_screen):
            raise RuntimeError(
                "power cut did not return to the boot-1 login screen")
        type_test_secret(guest.qmp, secret, timeout=18)
        if not joined_smoke.weston_proof(guest.qmp, Path(guest._temp.name),
                                         full=False):
            raise RuntimeError("weston desktop proof failed (boot 2)")
        guest.wait_for("relock after power-cut-recovery session",
                       lambda s: is_auth(s) and same_screen(s, login_screen),
                       120)
        guest.check_serial()


def boot_three_broken_state(work, evidence_dir, secret, login_screen, timeout):
    """Boot 3: destroyed state filesystem -> locked login, no desktop."""
    with Guest(work, evidence_dir / "hardware-broken-state.log") as guest:
        screen = guest.wait_stable_auth(timeout)
        if screen["title"] != login_screen["title"]:
            raise RuntimeError(
                "broken-state boot offered enrollment or an unknown screen")
        type_test_secret(guest.qmp, secret, timeout=18)
        guest.wait_for(
            "denial without a credential store",
            lambda s: is_auth(s) and s["denied_px"] > DENIED_MIN_PIXELS, 60)
        # Twenty more seconds: no desktop may ever appear.
        end = time.monotonic() + 20
        while time.monotonic() < end:
            width, height, data = guest.frame()
            if weston_smoke.original_window(width, height, data):
                raise RuntimeError("desktop appeared without state")
            time.sleep(1.0)
        guest.check_serial()


def main() -> int:
    global QEMU_GRAPHICS, QEMU_IMAGE_USB
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--graphics", choices=("virtio", "vga"),
                        default="virtio",
                        help="QEMU display device: virtio-vga (default) "
                             "or standard VGA (simpledrm path)")
    parser.add_argument("--image-usb", action="store_true",
                        help="attach the whole boot image over emulated "
                             "USB storage instead of virtio-blk")
    parser.add_argument("--variant", default=None,
                        help="label for this configuration's evidence "
                             "files (default: derived from the options)")
    args = parser.parse_args()
    QEMU_GRAPHICS = args.graphics
    QEMU_IMAGE_USB = args.image_usb
    variant = args.variant or (
        "virtio" if args.graphics == "virtio" and not args.image_usb
        else "-".join(part for part in
                      (args.graphics if args.graphics != "virtio" else "",
                       "usb" if args.image_usb else "") if part))
    if os.geteuid() == 0:
        parser.error("hardware joined smoke requires a non-root runner")
    if args.timeout < 180:
        parser.error("timeout must be at least 180 seconds per boot")
    proof = json.loads((IMAGE_DIR / "validation.json").read_text())
    source_image = IMAGE_DIR / "synveil-x86_64.img"
    if (proof.get("schema") != "synveil.native-joined-image/v1" or
            proof.get("boot_target") != "hardware" or
            source_image.is_symlink() or not source_image.is_file() or
            hashlib.sha256(source_image.read_bytes()).hexdigest()
            != proof.get("image_sha256")):
        parser.error("hardware joined image missing or changed since build")
    evidence_dir = IMAGE_DIR / (
        "smoke-evidence" if variant == "virtio"
        else f"smoke-evidence-{variant}")
    evidence_dir.mkdir(exist_ok=True)
    secret = secrets.token_hex(16)
    wrong = secret[:-1] + ("0" if secret[-1] != "0" else "1")
    with tempfile.TemporaryDirectory(prefix="synveil-joined-hw-") as temp:
        base = Path(temp)
        work = base / "work"
        work.mkdir()
        target = work / "synveil-x86_64.img"
        with source_image.open("rb") as source, target.open("xb") as dest:
            shutil.copyfileobj(source, dest)
        if hashlib.sha256(target.read_bytes()).hexdigest() \
                != proof["image_sha256"]:
            print("[synveil] hardware image copy digest mismatch")
            return 1
        facts = boot_one_fresh(work, evidence_dir, secret, wrong,
                               args.timeout)
        boot_two_reboot(work, evidence_dir, secret, wrong,
                        facts["login_screen"], args.timeout)
        broken = base / "broken"
        broken.mkdir()
        broken_image = broken / "synveil-x86_64.img"
        with source_image.open("rb") as source, \
                broken_image.open("xb") as dest:
            shutil.copyfileobj(source, dest)
        corrupt_state_partition(broken_image)
        boot_three_broken_state(broken, evidence_dir, secret,
                                facts["login_screen"], args.timeout)
    if args.graphics == "vga":
        graphics_note = (
            "QEMU standard VGA GOP stands in for the TG01 RTX 3060 "
            "firmware GOP. The hardware kernel has no native driver "
            "for this adapter (CONFIG_DRM_BOCHS unset, no virtio GPU "
            "present), so the display ran through simpledrm on the "
            "EFI framebuffer; physical RTX 3060 behavior is unproven")
    else:
        graphics_note = (
            "QEMU GOP framebuffer via virtio-vga stands in for the "
            "TG01 RTX 3060 firmware GOP; the kernel has no nouveau or "
            "NVIDIA driver, so physical display behavior is unproven")
    if args.image_usb:
        storage_note = (
            "The complete image booted from emulated USB storage "
            "(qemu-xhci + usb-storage); the owner's physical USB SSD "
            "and HP firmware USB boot behavior are unproven")
    else:
        storage_note = (
            "virtio-blk stands in for the owner's USB SSD; this "
            "image's boot from emulated USB storage is recorded by "
            "the usb variant of this acceptance")
    result = {
        "schema": "synveil.native-joined-hardware-guest/v1",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "image_sha256": proof["image_sha256"],
        "boot_target": "hardware",
        "variant": variant,
        "qemu_graphics_device": "VGA" if args.graphics == "vga"
                                else "virtio-vga",
        "qemu_boot_storage": "usb-storage" if args.image_usb
                             else "virtio-blk-pci",
        "qemu_uefi_graphical_session": True,
        "framebuffer_resolution": facts["resolution"],
        "tty0_console_frames_before_greeter":
            facts["early_console_distinct_frames"],
        "enrollment_graphical": True,
        "mismatched_confirmation_rejected": True,
        "wrong_password_denied_after_cold_reboot": True,
        "weston_desktop_after_login": True,
        "pointer_move_resize_keyboard": True,
        "session_end_relocks_login": True,
        "hard_power_cut_returns_to_login_and_credential_survives": True,
        "broken_state_fails_closed_to_locked_login": True,
        "state_on_image_provided_partition": True,
        "separate_state_disk_used": False,
        "serial_contains_no_guest_console_or_shell": True,
        "screen_signatures_md5_bands": {
            "enroll": facts["enroll_screen"], "login": facts["login_screen"]},
        "physical_hardware_tested": False,
        "qemu_only_limitations": [
            graphics_note,
            storage_note,
            "USB keyboard/tablet are QEMU devices; TG01 HID behavior "
            "is unproven",
        ],
    }
    result_name = ("guest-validation.json" if variant == "virtio"
                   else f"guest-validation-{variant}.json")
    (IMAGE_DIR / result_name).write_text(
        json.dumps(result, indent=2) + "\n")
    print("SYNVEIL_NATIVE_JOINED_HARDWARE_QEMU_ENROLL_LOGIN_WESTON_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
