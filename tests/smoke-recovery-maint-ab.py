#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QEMU acceptance for ADR 0010 tier 2 on the A/B substrate: an
authenticated maintenance session restores the known-good generation.

Uses the A/B proof build (tools/native_ab.py, out/ab-proof). All work
happens on a disposable copy of the proof disk:

  1. provision     a normal boot provisions the control record
                   (confirmed = A v1) from the boot hook
  2. enroll        the synthetic owner credential is enrolled onto
                   the disk's STATE partition through the developer
                   fixture image (test-only route, as in
                   smoke-recovery-maint.py)
  3. fixture setup slot B is populated with the v2 generation and
                   the control record's slot map is extended to name
                   it -- the exact record shape a staged, confirmed
                   update to v2 followed by an owner rollback to v1
                   and its confirmation would leave behind (slots A
                   and B both recorded, confirmed = A, no trial).
                   A known-content owner file is placed on DATA.
  4. maint-denied  maintenance boot (ESP fallback replaced by
                   maintenance.efi on the copy): three wrong
                   passwords are denied and the console locks
  5. maint-restore maintenance boot: authentication succeeds,
                   status reports confirmed A v1 / no trial, the
                   restore action arms B v2 as a trial through
                   veil-ab's typed rollback (postcondition verified
                   in veil-recovery), exit locks the console
  6. restored boot with the normal ESP fallback restored, the boot
                   hook hands over to the trial and v2 boots
  7. host checks   the DATA owner file is byte-identical, the
                   control record shows the armed trial, and the
                   maintenance audit log on STATE records auth-ok
                   and the restore action

Evidence logs and a verdict JSON are written under
out/recovery-maint-ab/. QEMU evidence only; no hardware is claimed.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
AB_OUT = ROOT / "out" / "ab-proof"
OUT = ROOT / "out" / "recovery-maint-ab"
BOOT_TIMEOUT = 300

_spec = importlib.util.spec_from_file_location(
    "smoke_recovery_maint", ROOT / "tests" / "smoke-recovery-maint.py")
maint_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(maint_smoke)
SECRET = maint_smoke.SECRET
WRONG = maint_smoke.WRONG


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def run(*argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True,
                          text=True, **kwargs)


def partition_span(disk, number):
    info = run("sgdisk", "-i", str(number), str(disk)).stdout
    first = size = None
    for line in info.splitlines():
        if "First sector:" in line:
            first = int(line.split(":", 1)[1].split()[0])
        if "Partition size:" in line:
            size = int(line.split(":", 1)[1].split()[0])
    if first is None or size is None:
        raise RuntimeError(f"partition {number} span not found")
    return first, size


def extract_partition(disk, number, dest):
    first, size = partition_span(disk, number)
    run("dd", f"if={disk}", f"of={dest}", "bs=512", f"skip={first}",
        f"count={size}", "conv=notrunc", "status=none")
    return dest


def write_partition(disk, number, image):
    first, _size = partition_span(disk, number)
    run("dd", f"if={image}", f"of={disk}", "bs=512", f"seek={first}",
        "conv=notrunc", "status=none")


def replay_journal(image):
    """Replay any pending ext4 journal left by a killed QEMU boot.

    Host edits made with debugfs on an image whose journal still
    holds the pre-edit directory/inode transactions are silently
    reverted when the guest mounts the filesystem and replays that
    journal (observed in attempt 2: the control record's slots.B
    edit and the DATA probe file both vanished in the guest, while
    the journal-free FAT ESP edit persisted). Replaying the journal
    on the extracted copy first makes host edits and reads see the
    same state the guest will see.
    """
    result = subprocess.run(["e2fsck", "-fy", str(image)],
                            capture_output=True, text=True)
    if result.returncode not in (0, 1, 2):
        raise RuntimeError(f"e2fsck {image}: {result.stdout} {result.stderr}")


def debugfs_read(image, guest_path):
    result = subprocess.run(["debugfs", "-R", f"cat {guest_path}",
                             str(image)], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"debugfs cat {guest_path}: {result.stderr!r}")
    return result.stdout


def debugfs_write(image, host_file, guest_path):
    subprocess.run(["debugfs", "-w", "-R", f"rm {guest_path}", str(image)],
                   capture_output=True)
    run("debugfs", "-w", "-R", f"write {host_file} {guest_path}", str(image))


def ovmf_paths():
    pairs = [("/usr/share/OVMF/OVMF_CODE_4M.fd",
              "/usr/share/OVMF/OVMF_VARS_4M.fd"),
             ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
             ("/usr/share/edk2/ovmf/OVMF_CODE.fd",
              "/usr/share/edk2/ovmf/OVMF_VARS.fd")]
    for code, vars_ in pairs:
        if Path(code).is_file() and Path(vars_).is_file():
            return code, vars_
    raise RuntimeError("OVMF firmware pair not found")


class Boot:
    """One QEMU process on the A/B disk copy; serial both ways."""

    def __init__(self, name, disk, vars_file):
        self.name = name
        self.log_path = OUT / f"{name}.log"
        self.lines = []
        self._lock = threading.Lock()
        code, _ = ovmf_paths()
        self.process = subprocess.Popen(
            ["qemu-system-x86_64", "-nodefaults",
             "-machine", "q35,i8042=off,vmport=off",
             "-m", "768M", "-smp", "2", "-display", "none",
             "-serial", "stdio", "-monitor", "none",
             "-device", "virtio-vga", "-device", "qemu-xhci",
             "-device", "usb-kbd", "-device", "usb-tablet",
             "-net", "none", "-accel", "tcg",
             "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
             "-drive", f"if=pflash,format=raw,unit=1,file={vars_file}",
             "-drive", f"if=none,id=main,file={disk},format=raw,readonly=off",
             "-device", "virtio-blk-pci,drive=main,bootindex=1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        with self.log_path.open("wb") as log:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.rstrip(b"\r\n")
                log.write(line + b"\n")
                log.flush()
                with self._lock:
                    self.lines.append(line.decode("utf-8", "replace"))

    def seen(self, marker, since=0):
        with self._lock:
            return any(marker in line for line in self.lines[since:])

    def wait(self, marker, timeout=BOOT_TIMEOUT, since=0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.seen(marker, since):
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(0.2)
        return self.seen(marker, since)

    def send(self, text):
        self.process.stdin.write((text + "\n").encode())
        self.process.stdin.flush()

    def transcript(self):
        with self._lock:
            return "\n".join(self.lines)

    def stop(self):
        try:
            os.killpg(self.process.pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, 9)
            self.process.wait(timeout=5)


def probe_locked(boot):
    before = len(boot.lines)
    boot.send(r"printf '\n%s%s\n' SYNVEIL_ SERIAL_UNLOCK_BYPASS")
    boot.send("exec /bin/sh")
    # Split marker: console echo of the probe can never produce it
    # contiguously; only real shell execution could.
    boot.send("id; echo SYNVEIL_MAINT_PW''NED")
    time.sleep(4)
    window = "\n".join(boot.lines[before:])
    return ("SYNVEIL_SERIAL_UNLOCK_BYPASS" not in window
            and "SYNVEIL_MAINT_PWNED" not in window
            and "uid=0" not in window
            and "Recovery/bootstrap shell" not in window)


def slot_identity(slot, generation):
    return {
        "schema": "synveil.slot-generation/v1",
        "slot": slot,
        "version": generation["version"],
        "bytes": generation["uki_bytes"],
        "sha256": generation["uki_sha256"],
        "state_format": generation["state_format"],
        "generation_id": generation["generation_id"],
        "min_reader_state_format": 1,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    receipt = json.loads((AB_OUT / "build-receipt.json").read_text())
    results = {}
    disk = OUT / "work-disk.img"
    shutil.copyfile(receipt["disk"]["path"], disk)
    vars_file = OUT / "vars.fd"
    _code, vars_template = ovmf_paths()
    shutil.copyfile(vars_template, vars_file)
    generations = {int(k): v for k, v in receipt["generations"].items()}

    # 1. Provision on a normal boot. (The provisioning boot returns
    #    from the hook right after saving the record; the
    #    CONFIRMED_BOOT marker belongs to later boots.)
    boot = Boot("provision", disk, vars_file)
    ok = boot.wait("SYNVEIL_AB_PROVISIONED slot=A version=1")
    time.sleep(10)
    boot.stop()
    results["provision"] = ok

    # 2. Enroll the synthetic credential onto this disk's STATE.
    enroll_out = maint_smoke.OUT / "fixtures" / "enroll"
    if not (enroll_out / "synveil-x86_64.img").is_file():
        enroll_out = maint_smoke.build_fixture("enroll", maint=False)
    boot = maint_smoke.SerialBoot("enroll-ab", enroll_out, disk)
    results["enroll"] = maint_smoke.enroll(boot)
    boot.stop()

    # 3. Fixture setup on the host: slot B populated, control record
    #    extended, owner file placed on DATA.
    state = extract_partition(disk, 4, OUT / "state.img")
    replay_journal(state)
    control = json.loads(debugfs_read(state, "/lib/synveil/ab-control.json"))
    gen_b = slot_identity("B", generations[2])
    control["slots"]["B"] = {k: gen_b[k] for k in (
        "slot", "version", "bytes", "sha256", "state_format",
        "generation_id", "min_reader_state_format")}
    control_file = OUT / "ab-control.json"
    control_file.write_text(json.dumps(control, indent=2) + "\n")
    debugfs_write(state, control_file, "/lib/synveil/ab-control.json")
    write_partition(disk, 4, state)
    check = extract_partition(disk, 4, OUT / "state-check.img")
    written = json.loads(debugfs_read(check, "/lib/synveil/ab-control.json"))
    if (written.get("slots") or {}).get("B") is None:
        raise RuntimeError("control write-back did not persist on the disk")

    slot_b = extract_partition(disk, 3, OUT / "slot-b.img")
    run("mmd", "-i", str(slot_b), "::EFI")
    run("mmd", "-i", str(slot_b), "::EFI/synveil")
    gen_json = OUT / "gen-b.json"
    gen_json.write_text(json.dumps(gen_b, indent=2) + "\n")
    run("mcopy", "-o", "-i", str(slot_b), generations[2]["uki"],
        "::EFI/synveil/gen.efi")
    run("mcopy", "-o", "-i", str(slot_b), str(gen_json),
        "::EFI/synveil/gen.json")
    write_partition(disk, 3, slot_b)

    owner_file = OUT / "owner-restore-probe.txt"
    owner_content = b"synveil owner data probe: restore must not touch this file\n"
    owner_file.write_bytes(owner_content)
    data = extract_partition(disk, 5, OUT / "data.img")
    replay_journal(data)
    debugfs_write(data, owner_file, "/owner-restore-probe.txt")
    write_partition(disk, 5, data)
    check = extract_partition(disk, 5, OUT / "data-check.img")
    if debugfs_read(check, "/owner-restore-probe.txt") != owner_content:
        raise RuntimeError("DATA write-back did not persist on the disk")
    owner_sha_before = sha256_bytes(
        debugfs_read(data, "/owner-restore-probe.txt"))

    # ESP: save the normal fallback, install the maintenance entry.
    esp = extract_partition(disk, 1, OUT / "esp.img")
    run("mcopy", "-i", str(esp), "::EFI/BOOT/BOOTX64.EFI",
        str(OUT / "bootx64-orig.efi"))
    run("mcopy", "-o", "-i", str(esp), receipt["maintenance"]["uki"],
        "::EFI/BOOT/BOOTX64.EFI")
    write_partition(disk, 1, esp)

    # 4. Maintenance boot, wrong passwords only.
    boot = Boot("maint-denied", disk, vars_file)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT")
    for attempt in (1, 2, 3):
        boot.send(WRONG)
        ok = ok and boot.wait(
            f"SYNVEIL_RECOVERY_MAINT_DENIED attempt={attempt}", 180)
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and "SYNVEIL_RECOVERY_MAINT_AUTH_OK" not in boot.transcript()
    ok = ok and probe_locked(boot)
    results["maint-denied"] = ok
    boot.stop()
    # Did the denied maintenance boot leave the fixture state intact?
    check = extract_partition(disk, 4, OUT / "state-after-denied.img")
    replay_journal(check)
    intact = json.loads(debugfs_read(check, "/lib/synveil/ab-control.json"))
    results["fixture-survives-denied-boot"] = (
        (intact.get("slots") or {}).get("B") is not None)

    # 5. Maintenance boot: authenticate and restore.
    boot = Boot("maint-restore", disk, vars_file)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT")
    boot.send(SECRET)
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_AUTH_OK")
    boot.send("status")
    ok = ok and boot.wait(
        "SYNVEIL_RECOVERY_MAINT_AB confirmed_slot=A confirmed_version=1")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_AB trial=none")
    boot.send("restore")
    ok = ok and boot.wait("SYNVEIL_AB_ROLLBACK result=trial slot=B version=2")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_RESTORE_OK slot=B version=2")
    boot.send("exit")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_DONE")
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and probe_locked(boot)
    ok = ok and SECRET not in boot.transcript()
    results["maint-restore"] = ok
    boot.stop()

    # 6. Normal boot: the hook hands over to the restored trial.
    esp = extract_partition(disk, 1, OUT / "esp.img")
    run("mcopy", "-o", "-i", str(esp), str(OUT / "bootx64-orig.efi"),
        "::EFI/BOOT/BOOTX64.EFI")
    write_partition(disk, 1, esp)
    boot = Boot("restored-boot", disk, vars_file)
    ok = boot.wait("SYNVEIL_AB_TRIAL_HANDOVER slot=B version=2")
    ok = ok and boot.wait("SYNVEIL_AB_CHAIN_LOAD slot=B version=2")
    ok = ok and boot.wait("SYNVEIL_AB_TRIAL_BOOT slot=B version=2")
    time.sleep(5)
    boot.stop()
    results["restored-boot"] = ok

    # 7. Host checks: DATA byte-intact, control record, audit log.
    data = extract_partition(disk, 5, OUT / "data-after.img")
    replay_journal(data)
    owner_sha_after = sha256_bytes(
        debugfs_read(data, "/owner-restore-probe.txt"))
    results["data-intact"] = owner_sha_after == owner_sha_before
    state = extract_partition(disk, 4, OUT / "state-after.img")
    replay_journal(state)
    control_after = json.loads(
        debugfs_read(state, "/lib/synveil/ab-control.json"))
    trial = control_after.get("trial") or {}
    results["control-trial-armed"] = (
        trial.get("slot") == "B" and trial.get("version") == 2)
    audit_log = debugfs_read(
        state, "/log/synveil/recovery-maintenance.log").decode(
            "utf-8", "replace")
    (OUT / "recovery-maintenance.log").write_text(audit_log)
    results["audit-log"] = ("event=auth-ok" in audit_log
                            and "name=restore result=armed" in audit_log)

    verdict = {"schema": "synveil.recovery-maint-ab-smoke/v1",
               "source_commit": receipt["source_commit"],
               "disk_sha256": receipt["disk"]["sha256"],
               "cases": results,
               "owner_file_sha256": owner_sha_before,
               "physical_hardware_tested": False}
    (OUT / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
    for name, result in results.items():
        print(f"RECOVERY_MAINT_AB_CASE {name}={'PASS' if result else 'FAIL'}")
    passed = all(results.values())
    print("SYNVEIL_RECOVERY_MAINT_AB_SMOKE_PASS" if passed
          else "SYNVEIL_RECOVERY_MAINT_AB_SMOKE_FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
