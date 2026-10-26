#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bootable A/B update/rollback acceptance in QEMU.

Drives the disk assembled by tools/native_ab.py through the full
proof sequence from docs/AB_UPDATER.md, on a private copy of the
disk with a persistent OVMF variable store (one VARS file per
scenario run, kept across the run's boots; the updater itself
writes no firmware variables — slot selection is by kexec
chain-loading inside the guest, see docs/AB_UPDATER.md):

  S0  provision on first boot, graphical enrollment + login,
      a user setting change (if the greeter exposes it), a known
      user file in the DATA-backed home
  S1  stage signed v2 from release media; on reboot the entry
      generation's hook hands over (chain-load) to the v2 trial
  S2  v2 trial boots, the agent's health checks pass, and the
      operator confirms with the typed confirm action (two-key
      confirmation, docs/AB_UPDATER.md); login, desktop background
      change and all survival artifacts verified
  S3  owner-requested rollback to v1, trial + confirmation again
  S4  failing candidate v3: three unhealthy trial boots exhaust
      attempts, firmware falls back to v1, trial discarded
  S5  corrupted-signature release refused; confirmed slot untouched
  S6  power cut mid-staging: no trial recorded, v1 intact, and a
      later stage still succeeds (left pending, then discarded)
  S7  slot images corrupted on a branch copy while the ESP
      fallback copy stays intact: the hook's digest check fails
      closed into the recovery diagnostics, no system boots

Every scenario writes its serial log under out/ab-proof/smoke-
evidence/ and the run writes a machine-readable receipt
(out/ab-proof/smoke-receipt.json) with the source commit, image and
generation identities from the build receipt. QEMU evidence only;
no physical hardware is claimed.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import (  # noqa: E402
    QMPError, capture_test_scanout, exit_user_terminal, logout_user_desktop,
    open_user_terminal, send_keys, settings_toggle, type_ab_command,
    type_test_secret)

_spec = importlib.util.spec_from_file_location(
    "smoke_native_weston", ROOT / "tests" / "smoke-native-weston.py")
weston_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(weston_smoke)

OUT = ROOT / "out" / "ab-proof"
EVIDENCE = OUT / "smoke-evidence"
GEN_COLORS = {1: (0x10, 0x18, 0x20), 2: (0x0B, 0x3D, 0x2E)}
BOOT_OK = ("SYNVEIL_BOOT_OK", "SYNVEIL_CORE_READY", "SYNVEIL_STATE_READY",
           "SYNVEIL_SEAT_READY")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ovmf_paths():
    pairs = [("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
             ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
             ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
             ("/usr/share/edk2/x64/OVMF_CODE.fd", "/usr/share/edk2/x64/OVMF_VARS.fd")]
    for code, vars_ in pairs:
        if Path(code).is_file() and Path(vars_).is_file():
            return code, vars_
    raise RuntimeError("OVMF firmware pair not found")


class Boot:
    """One QEMU process; guest reboots stay inside it."""

    def __init__(self, workdir, name, disk, vars_file, extra_drives=()):
        self.dir = Path(workdir)
        self.name = name
        self.frame_base = (abs(hash(name)) % 500) * 1000 + 1000
        self.frame_seq = 0
        self.qmp = self.dir / f"{name}.sock"
        self.log_path = EVIDENCE / f"{name}.log"
        self.lines = []
        self._lock = threading.Lock()
        self._secret = None
        code, _vars_template = ovmf_paths()
        argv = ["qemu-system-x86_64", "-nodefaults",
                "-machine", "q35,i8042=off,vmport=off",
                "-m", "768M", "-smp", "2", "-display", "none",
                "-serial", "stdio", "-monitor", "none",
                "-device", "virtio-vga", "-device", "qemu-xhci",
                "-device", "usb-kbd", "-device", "usb-tablet",
                "-net", "none", "-accel", "tcg",
                "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
                "-drive", f"if=pflash,format=raw,unit=1,file={vars_file}",
                "-drive", f"if=none,id=main,file={disk},format=raw,readonly=off",
                "-device", "virtio-blk-pci,drive=main,bootindex=1",
                "-qmp", f"unix:{self.qmp},server=on,wait=off"]
        for index, drive in enumerate(extra_drives):
            argv += ["-drive", f"if=none,id=rel{index},file={drive},format=raw,readonly=on",
                     "-device", f"virtio-blk-pci,drive=rel{index}"]
        self.argv = argv
        self.process = None

    def start(self, secret):
        self._secret = secret
        self.process = subprocess.Popen(
            self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        with self.log_path.open("wb") as log:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.rstrip(b"\r\n")
                if self._secret:
                    line = line.replace(self._secret.encode(), b"[REDACTED]")
                log.write(line + b"\n")
                log.flush()
                with self._lock:
                    self.lines.append(line.decode("utf-8", "replace"))

    def seen(self, marker, since=0):
        with self._lock:
            return any(line.startswith(marker) for line in self.lines[since:])

    def count(self, marker):
        with self._lock:
            return sum(1 for line in self.lines if line.startswith(marker))

    def wait(self, marker, timeout, since=0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.seen(marker, since):
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.seen(marker, since)

    def wait_count(self, marker, count, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count(marker) >= count:
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.count(marker) >= count

    def qmp_execute(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.qmp))
            reader = sock.makefile("rb")
            reader.readline()
            sock.sendall(b'{"execute":"qmp_capabilities"}\n')
            reader.readline()
            sock.sendall((json.dumps({"execute": command}) + "\n").encode())
            return reader.readline()

    def quit(self):
        try:
            self.qmp_execute("quit")
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def login(boot, secret, timeout=420, since=0):
    """Wait for the graphical login, prove scanout, authenticate.

    `since` is the caller's line index for the start of the
    current guest boot cycle. One Boot object spans several guest
    boots (reboots stay inside one QEMU process), so a post-reboot
    login MUST pass its cycle mark: an unanchored wait would be
    satisfied by the PREVIOUS boot's LOGIN_READY / AUTH_ACTIVE
    lines and the secret would be typed into a guest that has not
    reached its greeter yet. First-cycle logins use the default.
    """
    anchor = since
    if not boot.wait("SYNVEIL_LOGIN_READY", timeout, since=anchor):
        save_failure_frame(boot, "login-ready")
        return False
    for _ in range(10):
        if capture_test_scanout(boot.qmp, boot.dir / "scanout.ppm", timeout=2):
            break
        time.sleep(0.3)
    else:
        save_failure_frame(boot, "login-scanout")
        return False
    try:
        type_test_secret(boot.qmp, secret, timeout=18)
    except (QMPError, OSError, ValueError):
        return False
    if not boot.wait("SYNVEIL_GRAPHICAL_AUTH_ACTIVE", 60, since=anchor):
        # The first type may have raced the greeter's input focus;
        # the prompt is certainly up now (LOGIN_READY was seen in
        # this boot cycle), so type the secret once more.
        try:
            type_test_secret(boot.qmp, secret, timeout=18)
        except (QMPError, OSError, ValueError):
            save_failure_frame(boot, "login-auth")
            return False
        if not boot.wait("SYNVEIL_GRAPHICAL_AUTH_ACTIVE", 60, since=anchor):
            save_failure_frame(boot, "login-auth")
            return False
    time.sleep(5)  # let the tile dashboard present before key input
    return True


def desktop_color_proof(boot, version, timeout=120):
    """The running generation's Weston background dominates the frame
    and the other generation's background does not."""
    target = GEN_COLORS[version]
    other = GEN_COLORS[2 if version == 1 else 1]
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        count += 1
        try:
            width, height, data = weston_smoke.screenshot(boot.qmp, boot.dir, count)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        def fraction(rgb):
            hits = 0
            total = 0
            for i in range(0, len(data) - 2, 3 * 7):
                total += 1
                if (abs(data[i] - rgb[0]) <= 2 and abs(data[i + 1] - rgb[1]) <= 2
                        and abs(data[i + 2] - rgb[2]) <= 2):
                    hits += 1
            return hits / max(total, 1)
        if fraction(target) >= 0.03 and fraction(target) >= 4 * max(fraction(other), 0.001):
            return True
        time.sleep(0.5)
    save_failure_frame(boot, f"color-v{version}")
    return False


def save_failure_frame(boot, tag):
    """Preserve one scanout frame in the evidence dir for diagnosis."""
    try:
        width, height, data = next_frame(boot)
        ppm = EVIDENCE / f"{boot.name}-{tag}.ppm"
        with ppm.open("wb") as handle:
            handle.write(f"P6\n{width} {height}\n255\n".encode())
            handle.write(bytes(data))
    except Exception:
        pass


def next_frame(boot):
    boot.frame_seq = getattr(boot, "frame_seq", 0) + 1
    return weston_smoke.screenshot(
        boot.qmp, boot.dir, boot.frame_base + boot.frame_seq)


# The update GUI's pixel identity (src/desktop-wayland/update-window.c):
# a purple title bar, a paper-white body, and a state banner whose
# color is the coarse on-screen state. The GUI smoke and the
# terminal focus helper both locate the window by these colors.
GUI_TITLE = (0x5B, 0x2E, 0x6E)
GUI_CLOSE = (0xA8, 0x44, 0x55)
GUI_PAPER = (0xF2, 0xF0, 0xEB)
GUI_BANNERS = {
    "normal": (0x2E, 0x7D, 0x4F),
    "trial": (0xB0, 0x7A, 0x1E),
    "error": (0xA8, 0x2A, 0x2A),
    "disabled": (0x6E, 0x6E, 0x6E),
    "down": (0x4A, 0x4A, 0x55),
}
# Button geometry inside the window (update-window.c): five buttons,
# 128x36, 10px gaps from x=16, anchored 92px above the bottom edge.
GUI_BUTTONS = ("stage", "restart", "confirm", "rollback", "refresh")


def _px(data, width, x, y):
    off = (y * width + x) * 3
    return data[off], data[off + 1], data[off + 2]


def _near(rgb, target, tol=4):
    return all(abs(a - b) <= tol for a, b in zip(rgb, target))


def gui_window_rect(width, height, data):
    """Locate the update GUI window: (x, y, w, h) or None."""
    for y in range(0, height - 200):
        x = 0
        while x < width:
            if _near(_px(data, width, x, y), GUI_TITLE):
                start = x
                while x < width and _near(_px(data, width, x, y), GUI_TITLE):
                    x += 1
                if x - start >= 300:
                    # The close box finishes the title bar's row.
                    w = x - start
                    probe = x
                    while probe < width and _near(
                            _px(data, width, probe, y), GUI_CLOSE):
                        probe += 1
                    if probe > x:
                        w += probe - x
                    # Height: the left edge stays title/banner/paper
                    # until the window's bottom.
                    interior = set(GUI_BANNERS.values()) | {GUI_PAPER, GUI_TITLE}
                    bottom = y
                    for row in range(y + 1, height):
                        if any(_near(_px(data, width, start + 2, row), c)
                               for c in interior):
                            bottom = row
                        else:
                            break
                    return start, y, w, bottom - y + 1
            else:
                x += 1
    return None


def gui_banner_kind(width, height, data, rect):
    """Sample the GUI's state banner: a GUI_BANNERS key or None."""
    x, y, w, _h = rect
    rgb = _px(data, width, x + w // 2, y + 30 + 26)
    for kind, color in GUI_BANNERS.items():
        if _near(rgb, color, tol=6):
            return kind
    return None


def gui_click(boot, rect, index):
    """Click button `index` of the GUI window, raising it first."""
    x, y, w, h = rect
    width, height, _data = next_frame(boot)
    # A press on the title bar raises the window without moving it.
    weston_smoke.pointer(boot.qmp, width, height, x + 60, y + 14, button=True)
    weston_smoke.pointer(boot.qmp, width, height, x + 60, y + 14, button=False)
    time.sleep(0.4)
    bx = x + 16 + index * 138 + 64
    by = y + h - 92 + 18
    weston_smoke.pointer(boot.qmp, width, height, bx, by, button=True)
    weston_smoke.pointer(boot.qmp, width, height, bx, by, button=False)
    time.sleep(0.4)


def close_gui_window(boot, rect):
    x, y, w, _h = rect
    width, height, _data = next_frame(boot)
    weston_smoke.pointer(boot.qmp, width, height, x + w - 14, y + 14, button=True)
    weston_smoke.pointer(boot.qmp, width, height, x + w - 14, y + 14, button=False)
    time.sleep(0.8)


def focus_terminal(boot, timeout=240):
    """Focus the session's terminal window, closing a covering
    window first if needed (the joined smoke's proven sequence).

    The Weston session opens a dashboard/client window, a terminal,
    and (on A/B generations) the update GUI; the terminal interior
    is found by its dark canvas. Covering windows are closed in
    turn — the GUI first (it is auxiliary by design), then the
    first window by its proven Escape close.
    """
    deadline = time.monotonic() + timeout
    closed_cover = False
    closed_gui = False
    while time.monotonic() < deadline:
        try:
            frame = next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        width, height, data = frame
        try:
            tx, ty = weston_smoke.terminal_point(width, height, data)
        except RuntimeError:
            tx = None
        if tx is not None:
            weston_smoke.pointer(boot.qmp, width, height, tx, ty, button=True)
            weston_smoke.pointer(boot.qmp, width, height, tx, ty, button=False)
            time.sleep(0.4)
            return True
        if not closed_gui:
            rect = gui_window_rect(width, height, data)
            if rect:
                close_gui_window(boot, rect)
                closed_gui = True
                time.sleep(1.0)
                continue
            closed_gui = True
        if not closed_cover:
            window = weston_smoke.original_window(width, height, data)
            if window:
                x, y, _w, _h = window
                weston_smoke.pointer(boot.qmp, width, height,
                                     x + 30, y + 14, button=True)
                weston_smoke.pointer(boot.qmp, width, height,
                                     x + 30, y + 14, button=False)
                try:
                    send_keys(boot.qmp, ("esc",), timeout=5)
                except (QMPError, OSError, ValueError):
                    pass
                closed_cover = True
                time.sleep(1.0)
        time.sleep(0.5)
    return False


def terminal_command(boot, command_id, wait_marker=None, timeout=300):
    """Run one fixed A/B command in the session terminal.

    The terminal stays open across commands in a boot (closing it
    would end the session); focus is re-asserted by clicking its
    interior before typing.
    """
    if not focus_terminal(boot):
        save_failure_frame(boot, f"cmd-{command_id}-focus")
        return False
    try:
        type_ab_command(boot.qmp, command_id, timeout=30)
    except (QMPError, OSError, ValueError):
        save_failure_frame(boot, f"cmd-{command_id}-input")
        return False
    if wait_marker is None:
        time.sleep(2)
        return True
    ok = boot.wait(wait_marker, timeout)
    if not ok:
        save_failure_frame(boot, f"cmd-{command_id}-wait")
    return ok


def partition_offset(disk, number):
    info = subprocess.check_output(["sgdisk", "-i", str(number), str(disk)], text=True)
    first = int([line for line in info.splitlines()
                 if "First sector:" in line][0].split(":", 1)[1].split()[0])
    last = int([line for line in info.splitlines()
                if "Last sector:" in line][0].split(":", 1)[1].split()[0])
    return first * 512, (last - first + 1) * 512


def extract_partition(disk, number, workdir):
    offset, size = partition_offset(disk, number)
    target = Path(workdir) / f"part{number}.img"
    with open(disk, "rb") as source, open(target, "wb") as dest:
        source.seek(offset)
        shutil.copyfileobj(source, dest, size)
    return target


def ext4_read(image, path):
    result = subprocess.run(["debugfs", "-R", f"cat {path}", str(image)],
                            capture_output=True)
    if result.returncode != 0:
        return None
    return result.stdout


EXPECTED_KNOWN_FILE = "synveil-ab-proof-file\n"
GENERATION_SLOTS = {1: ("A", "synveil-gen-v1"), 2: ("B", "synveil-gen-v2")}


def replayed_image(image):
    """Copy a partition image and replay its ext4 journal, so a
    post-quit read sees the guest's final state. A QEMU quit leaves
    the last seconds of writes in the journal; plain debugfs then
    returns a stale control record (or garbage) even though the
    guest's serial markers prove the writes happened. The Pass B
    receipt recorded exactly that staleness — S2/S3 confirmed
    values inverted by one scenario — and the old gate never
    checked the values (review correction, 2026-10-26). The replay
    runs on a scratch copy; the evidence disk is never modified."""
    target = image.with_suffix(".replayed.img")
    shutil.copyfile(image, target)
    subprocess.run(["e2fsck", "-y", str(target)], capture_output=True)
    return target


def state_artifacts(disk, workdir):
    """Host-side view of STATE (partition 4) and DATA (partition 5),
    read from journal-replayed copies."""
    state = replayed_image(extract_partition(disk, 4, workdir))
    data = replayed_image(extract_partition(disk, 5, workdir))
    control_raw = ext4_read(state, "/lib/synveil/ab-control.json")
    control = None
    if control_raw:
        try:
            control = json.loads(control_raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            (EVIDENCE / "ab-control-unparsed.bin").write_bytes(control_raw)
    pref = ext4_read(state, "/lib/synveil/desktop-pref.conf")
    credential = ext4_read(state, "/lib/synveil/auth/credential.phc")
    known = ext4_read(data, "/synveil/ab-known-file")
    return {
        "control": control,
        "pref_sha256": hashlib.sha256(pref).hexdigest() if pref else None,
        "pref_text": pref.decode("utf-8", "replace") if pref else None,
        "credential_sha256": hashlib.sha256(credential).hexdigest() if credential else None,
        "known_file": known.decode("utf-8", "replace") if known else None,
    }


def confirmed_matches(control, version):
    """The control record's confirmed entry is exactly the expected
    generation: version, slot and generation id all agree."""
    slot, generation_id = GENERATION_SLOTS[version]
    confirmed = (control or {}).get("confirmed") or {}
    return (confirmed.get("version") == version
            and confirmed.get("slot") == slot
            and confirmed.get("generation_id") == generation_id)


def strict_survival(artifacts, baseline):
    """Credential and known file survived, with both sides present:
    a missing artifact on either side is a failure, never a match
    (None == None passed the old gate)."""
    return (bool(artifacts["credential_sha256"])
            and bool(baseline["credential_sha256"])
            and artifacts["credential_sha256"] == baseline["credential_sha256"]
            and artifacts["known_file"] == EXPECTED_KNOWN_FILE
            and baseline["known_file"] == EXPECTED_KNOWN_FILE)


def validate_results(results):
    """Re-derive the verdict from the recorded fields alone.

    finish() trusts nothing the scenarios claimed: a results dict
    whose recorded values contradict the scenario expectations —
    the defective Pass B receipt is the canonical example (S2
    'v2 confirmed' recording version 1/slot A, S0 known_file null,
    all pass flags true) — produces violations here, and no
    headline claim is emitted. Exercised without QEMU by
    tests/smoke-gates.py."""
    violations = []

    def need(name):
        entry = results.get(name)
        if entry is None:
            violations.append(f"{name}: scenario missing")
            return None
        if entry.get("pass") is not True:
            violations.append(f"{name}: pass flag is not true")
        return entry

    def confirmed_ok(name, entry, version):
        if entry is None:
            return
        slot, generation_id = GENERATION_SLOTS[version]
        confirmed = entry.get("confirmed") or {}
        if (confirmed.get("version") != version
                or confirmed.get("slot") != slot
                or confirmed.get("generation_id") != generation_id):
            violations.append(
                f"{name}: confirmed is {confirmed.get('version')}/"
                f"{confirmed.get('slot')}/{confirmed.get('generation_id')}, "
                f"expected {version}/{slot}/{generation_id}")

    def survival_ok(name, entry):
        if entry is None:
            return
        survival = entry.get("survival") or {}
        for key in ("credential_same", "known_file_same"):
            if survival.get(key) is not True:
                violations.append(f"{name}: survival.{key} is not true")

    s0 = need("S0-provision-enroll")
    if s0 is not None:
        if s0.get("credential_recorded") is not True:
            violations.append("S0-provision-enroll: credential not recorded")
        if s0.get("known_file") != EXPECTED_KNOWN_FILE:
            violations.append(
                f"S0-provision-enroll: known_file is {s0.get('known_file')!r}, "
                f"expected {EXPECTED_KNOWN_FILE!r}")
        if s0.get("home_mounted") is not True:
            violations.append("S0-provision-enroll: home not mounted from DATA")
    need("S1-stage-v2")
    s2 = need("S2-trial-v2-confirmed")
    confirmed_ok("S2-trial-v2-confirmed", s2, 2)
    survival_ok("S2-trial-v2-confirmed", s2)
    s3 = need("S3-owner-rollback-v1")
    confirmed_ok("S3-owner-rollback-v1", s3, 1)
    survival_ok("S3-owner-rollback-v1", s3)
    s4 = need("S4-failing-candidate-fallback")
    confirmed_ok("S4-failing-candidate-fallback", s4, 1)
    survival_ok("S4-failing-candidate-fallback", s4)
    if s4 is not None and s4.get("trial_after_fallback") is not None:
        violations.append("S4-failing-candidate-fallback: trial not cleared")
    s5 = need("S5-invalid-signature-refused")
    confirmed_ok("S5-invalid-signature-refused", s5, 1)
    if s5 is not None and s5.get("trial") is not None:
        violations.append("S5-invalid-signature-refused: trial present")
    s6 = need("S6-interrupted-stage")
    confirmed_ok("S6-interrupted-stage", s6, 1)
    s6b = need("S6b-stage-after-interrupt")
    if s6b is not None and s6b.get("trial") is not None:
        violations.append("S6b-stage-after-interrupt: trial present")
    need("S7-corrupt-slots-fail-closed")
    return violations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=600,
                        help="per-boot marker timeout budget")
    parser.add_argument("--stop-after", default=None,
                        help="diagnostic: stop after the named scenario")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("A/B smoke requires a non-root runner")
    build_receipt = json.loads((OUT / "build-receipt.json").read_text())
    source_disk = OUT / "synveil-ab.img"
    if sha256(source_disk) != build_receipt["disk"]["sha256"]:
        parser.error("A/B disk differs from its build receipt")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results = {}
    secret = secrets.token_hex(16)

    with tempfile.TemporaryDirectory(prefix="synveil-ab-") as temp:
        work = Path(temp)
        disk = work / "synveil-ab.img"
        shutil.copyfile(source_disk, disk)
        vars_file = work / "OVMF_VARS.fd"
        _code, vars_template = ovmf_paths()
        shutil.copyfile(vars_template, vars_file)

        def record(name, ok, **fields):
            results[name] = {"pass": bool(ok), **fields}
            print(f"[synveil-ab] {name}: {'PASS' if ok else 'FAIL'}", flush=True)
            return bool(ok)

        # ---- S0: provision + enroll + login + setting + known file
        boot = Boot(work, "s0-provision", disk, vars_file)
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_PROVISIONED", args.timeout)
        home_mounted = boot.seen("SYNVEIL_AB_HOME_MOUNTED")
        if ok:
            enrolled = boot.wait("SYNVEIL_ENROLL_READY", 240)
            if enrolled:
                try:
                    type_test_secret(boot.qmp, secret, timeout=18)
                    if boot.wait("SYNVEIL_ENROLL_CONFIRM_READY", 60):
                        type_test_secret(boot.qmp, secret, timeout=18)
                    enrolled = boot.wait("SYNVEIL_ENROLL_COMPLETED", 120)
                except (QMPError, OSError, ValueError):
                    enrolled = False
            ok = enrolled
        setting_changed = False
        if ok:
            ok = login(boot, secret)
        # NOTE: no user-facing settings surface is reachable in the
        # QEMU boot target's profile (the greeter takes only a
        # password; the Weston session has no settings UI in this
        # milestone) — probed directly with captured frames during
        # development. The saved-setting survival leg is therefore
        # recorded from whatever pref state exists on STATE, and
        # the limitation is stated in the validation record; the
        # gated survival evidence is the credential + known file.
        known_file_made = False
        if ok:
            known_file_made = terminal_command(boot, "known-file")
            ok = known_file_made
            time.sleep(5)  # let the fixture's sync land before quit
        boot.quit()
        artifacts = state_artifacts(disk, work)
        # Semantic gate: the artifacts S0 claims must actually be
        # on STATE/DATA with the exact expected content.
        ok = (ok and home_mounted
              and bool(artifacts["credential_sha256"])
              and artifacts["known_file"] == EXPECTED_KNOWN_FILE)
        record("S0-provision-enroll", ok,
               home_mounted=home_mounted,
               setting_changed=setting_changed,
               pref_text=artifacts["pref_text"],
               credential_recorded=bool(artifacts["credential_sha256"]),
               known_file=artifacts["known_file"])
        baseline = artifacts
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- S1+S2: stage v2, trial boot, health confirmation
        boot = Boot(work, "s1-stage-v2", disk, vars_file,
                    extra_drives=[OUT / "release-v2.img"])
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout)
        if ok:
            ok = login(boot, secret)
        if ok:
            ok = terminal_command(boot, "stage", "SYNVEIL_AB_STAGED", timeout=420)
        record("S1-stage-v2", ok)
        if ok:
            staged_at = len(boot.lines)
            terminal_command(boot, "reboot")
            # The reboot loads the entry generation (fallback = v1);
            # its hook accounts the trial and chain-loads v2.
            ok = boot.wait("SYNVEIL_AB_TRIAL_HANDOVER", args.timeout, since=staged_at)
            if ok:
                ok = boot.wait("SYNVEIL_AB_TRIAL_BOOT", args.timeout, since=staged_at)
            if ok:
                ok = login(boot, secret, since=staged_at)
            if ok:
                ok = desktop_color_proof(boot, 2)
            if ok:
                # Two-key confirmation: health passing is announced
                # by the agent but promotes nothing on its own; the
                # operator's typed confirm action does.
                ok = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=staged_at)
            if ok:
                ok = terminal_command(boot, "confirm",
                                      "SYNVEIL_AB_CONFIRMED ", timeout=300)
        boot.quit()
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and confirmed_matches(control, 2)
              and strict_survival(artifacts, baseline))
        record("S2-trial-v2-confirmed", ok,
               confirmed=(control.get("confirmed") or {}),
               survival={
                   "credential_same": artifacts["credential_sha256"] == baseline["credential_sha256"],
                   "pref_same": artifacts["pref_sha256"] == baseline["pref_sha256"],
                   "known_file_same": artifacts["known_file"] == baseline["known_file"],
               })
        if not ok:
            return finish(build_receipt, results, 1)
        after_update = artifacts
        if args.stop_after == "S2":
            return finish(build_receipt, results, 0)

        # ---- S3: owner rollback to v1
        boot = Boot(work, "s3-rollback-v1", disk, vars_file)
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout) and login(boot, secret)
        if ok:
            ok = terminal_command(boot, "rollback",
                                  "SYNVEIL_AB_ROLLBACK result=trial slot=",
                                  timeout=180)
        if ok:
            mark = len(boot.lines)
            terminal_command(boot, "reboot")
            ok = boot.wait("SYNVEIL_AB_TRIAL_BOOT", args.timeout, since=mark)
            if ok:
                ok = login(boot, secret, since=mark)
            if ok:
                ok = desktop_color_proof(boot, 1)
            if ok:
                ok = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=mark)
            if ok:
                ok = terminal_command(boot, "confirm",
                                      "SYNVEIL_AB_CONFIRMED ", timeout=300)
        boot.quit()
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and confirmed_matches(control, 1)
              and strict_survival(artifacts, baseline))
        record("S3-owner-rollback-v1", ok,
               confirmed=(control.get("confirmed") or {}),
               survival={
                   "credential_same": artifacts["credential_sha256"] == baseline["credential_sha256"],
                   "pref_same": artifacts["pref_sha256"] == baseline["pref_sha256"],
                   "known_file_same": artifacts["known_file"] == baseline["known_file"],
               })
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- S4: failing candidate v3 exhausts attempts -> fallback
        boot = Boot(work, "s4-failing-v3", disk, vars_file,
                    extra_drives=[OUT / "release-v3.img"])
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout) and login(boot, secret)
        if ok:
            ok = terminal_command(boot, "stage", "SYNVEIL_AB_STAGED", timeout=420)
        if ok:
            mark = len(boot.lines)
            terminal_command(boot, "reboot")
            ok = boot.wait_count("SYNVEIL_AB_HEALTH_FAILED", 3, 3000)
            if ok:
                ok = boot.wait("SYNVEIL_AB_TRIAL_EXHAUSTED", 600, since=mark)
            if ok:
                # Exhaustion clears the trial in the entry
                # generation's hook; the confirmed generation boots.
                ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout, since=mark)
            if ok:
                ok = login(boot, secret, since=mark)
        boot.quit()
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and confirmed_matches(control, 1)
              and control.get("trial") is None
              and strict_survival(artifacts, baseline))
        record("S4-failing-candidate-fallback", ok,
               confirmed=(control.get("confirmed") or {}),
               trial_after_fallback=control.get("trial"),
               survival={
                   "credential_same": artifacts["credential_sha256"] == baseline["credential_sha256"],
                   "known_file_same": artifacts["known_file"] == baseline["known_file"],
               })
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- S5: invalid signature refused
        boot = Boot(work, "s5-badsig", disk, vars_file,
                    extra_drives=[OUT / "release-v2-badsig.img"])
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout) and login(boot, secret)
        if ok:
            ok = terminal_command(boot, "stage", "SYNVEIL_AB_ACTION_FAILED", timeout=420)
            ok = ok and boot.seen("SYNVEIL_AB_ACTION_FAILED action=stage "
                                  "error=release signature verification failed")
            ok = ok and not boot.seen("SYNVEIL_AB_STAGED")
        boot.quit()
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and control.get("trial") is None
              and confirmed_matches(control, 1))
        record("S5-invalid-signature-refused", ok,
               trial=control.get("trial"),
               confirmed=(control.get("confirmed") or {}))
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- S6: power cut mid-staging
        boot = Boot(work, "s6-interrupt", disk, vars_file,
                    extra_drives=[OUT / "release-v2.img"])
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout) and login(boot, secret)
        if ok:
            # Drive the stage from the terminal, but cut power the
            # moment the copy begins.
            if not focus_terminal(boot):
                ok = False
            else:
                cutter = threading.Thread(target=lambda: (
                    boot.wait("SYNVEIL_AB_STAGE_COPYING", 300), boot.quit()))
                cutter.start()
                try:
                    type_ab_command(boot.qmp, "stage", timeout=30)
                except (QMPError, OSError, ValueError):
                    pass
                cutter.join(timeout=360)
                ok = not boot.seen("SYNVEIL_AB_STAGED")
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = ok and confirmed_matches(control, 1)
        record("S6-interrupted-stage", ok,
               trial=control.get("trial"),
               confirmed=(control.get("confirmed") or {}))
        if not ok:
            return finish(build_receipt, results, 1)

        # S6b: the system still boots its confirmed generation and a
        # fresh stage succeeds afterwards (trial left pending, then
        # discarded by an owner rollback decision).
        boot = Boot(work, "s6b-recover-stage", disk, vars_file,
                    extra_drives=[OUT / "release-v2.img"])
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout) and login(boot, secret)
        if ok:
            ok = terminal_command(boot, "stage", "SYNVEIL_AB_STAGED", timeout=420)
        if ok:
            ok = terminal_command(boot, "rollback",
                                  "SYNVEIL_AB_ROLLBACK result=trial-discarded",
                                  timeout=180)
        boot.quit()
        artifacts = state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = ok and control.get("trial") is None
        record("S6b-stage-after-interrupt", ok, trial=control.get("trial"))
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- S7: slot images corrupt -> fail closed to diagnostics
        branch = work / "synveil-ab-recovery.img"
        shutil.copyfile(disk, branch)
        for number in (2, 3):
            part = extract_partition(branch, number, work)
            target = Path(work) / f"slot{number}-gen.json"
            subprocess.run(["mcopy", "-i", str(part), "::/EFI/synveil/gen.json",
                            str(target)], capture_output=True)
            if not target.is_file():
                continue  # empty slot: nothing to corrupt
            identity = json.loads(target.read_text())
            zeros = Path(work) / f"zeros{number}.bin"
            zeros.write_bytes(b"\0" * identity["bytes"])
            subprocess.run(["mdel", "-i", str(part), "::/EFI/synveil/gen.efi"], check=True)
            subprocess.run(["mcopy", "-i", str(part), str(zeros), "::/EFI/synveil/gen.efi"], check=True)
            offset, _size = partition_offset(branch, number)
            with open(part, "rb") as source, open(branch, "r+b") as dest:
                dest.seek(offset)
                shutil.copyfileobj(source, dest)
        boot = Boot(work, "s7-recovery", branch, vars_file)
        boot.start(secret)
        # The ESP fallback copy of v1 still loads, but its hook must
        # refuse to run the corrupt confirmed slot as the system.
        ok = boot.wait("SYNVEIL_AB_FAIL_CLOSED", 600)
        ok = ok and boot.wait("SYNVEIL_RECOVERY_STATUS_V1", 120)
        ok = ok and not boot.seen("SYNVEIL_LOGIN_READY")
        boot.quit()
        record("S7-corrupt-slots-fail-closed", ok)

    return finish(build_receipt, results, 0 if all(
        entry["pass"] for entry in results.values()) else 1)


def finish(build_receipt, results, code):
    try:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        source_commit = build_receipt["source_commit"]
    # The verdict is re-derived from the recorded fields, never
    # from the caller's code alone: inconsistent receipts cannot
    # pass (review correction, 2026-10-26).
    violations = validate_results(results)
    demonstrated = code == 0 and not violations
    receipt = {
        "schema": "synveil.ab-qemu-proof/v1",
        "source_commit": source_commit,
        "build_receipt_commit": build_receipt["source_commit"],
        "disk_sha256": build_receipt["disk"]["sha256"],
        "generations": {v: {"uki_sha256": g["uki_sha256"], "bytes": g["uki_bytes"]}
                        for v, g in build_receipt["generations"].items()},
        "publisher": build_receipt["publisher"],
        "scenarios": results,
        "gate_violations": violations,
        "bootable_update_rollback_demonstrated": demonstrated,
        "https_browsing_in_synveil": False,
        "persistent_downloads_in_synveil": False,
        "physical_hardware_tested": False,
    }
    (OUT / "smoke-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("SYNVEIL_AB_QEMU_PROOF_" + ("PASS" if demonstrated else "INCOMPLETE"))
    return 0 if demonstrated else 1


if __name__ == "__main__":
    raise SystemExit(main())
