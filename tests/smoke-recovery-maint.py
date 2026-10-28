#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""QEMU acceptance for ADR 0010 tier 2: authenticated maintenance.

Substrate: two fixture images staged from the native rootfs (the
baseline tree is never modified):

  enroll  developer serial shell image (test-only enrollment route;
          the secret is generated host-side for this fixture, typed
          with console echo disabled, and never enters any image)
  maint   auth-profile image whose kernel carries
          synveil.recovery=maint, booting veil-recovery

Cases (each an independent QEMU boot on a disposable state disk):
  maint-ok       correct password authenticates; status is bounded;
                 restore on a non-A/B substrate is refused, not
                 improvised; an unknown command is rejected; exit
                 locks the console and serial probes get no shell
  maint-wrong    three wrong passwords are denied, then the console
                 locks; probes get no shell
  maint-absent   no credential record: denied reason=no-credential
                 before any prompt; console locks
  maint-corrupt  a credential record that is not a valid PHC record:
                 denied reason=corrupt-credential; console locks

The password must never appear in any maint transcript (console
echo is off while it is read). Evidence logs and a verdict JSON are
written under out/recovery-maint-smoke/.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from state_disk import create_state_disk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "recovery-maint-smoke"
SECRET = "synveil-maint-fixture-7f3a9c51"
WRONG = "synveil-maint-wrong-1b2c3d"
BOOT_TIMEOUT = 240


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_fixture(case, maint):
    work = ROOT / "build" / "work"
    rootfs = work / "rootfs"
    if not (rootfs / "usr/sbin/veil-recovery").is_file():
        raise RuntimeError("build a native rootfs containing veil-recovery first")
    staging = OUT / "staging" / case
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    tree = staging / "rootfs"
    shutil.copytree(rootfs, tree, symlinks=True)
    if maint:
        marker = tree / "etc/synveil/auth-desktop"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
        marker.chmod(0o644)
        debug = tree / "etc/synveil/developer-serial-recovery"
        if debug.exists() or debug.is_symlink():
            raise RuntimeError("unexpected developer recovery marker")
    else:
        debug = tree / "etc/synveil/developer-serial-recovery"
        debug.parent.mkdir(parents=True, exist_ok=True)
        debug.touch(exist_ok=False)
        debug.chmod(0o644)
    fixture_out = OUT / "fixtures" / case
    fixture_out.mkdir(parents=True, exist_ok=True)
    with (fixture_out / "synveil-initramfs.cpio").open("wb") as packed:
        subprocess.run(["bash", "-o", "pipefail", "-c",
                        "find . -print0 | sort -z | cpio --null -o "
                        "--format=newc --owner=0:0"],
                       cwd=tree, stdout=packed, check=True)
    env = {**os.environ, "SYNVEIL_OUT_DIR": str(fixture_out),
           "SYNVEIL_WORK_DIR": str(staging / "build"),
           "SYNVEIL_KERNEL_SEED_WORK": str(work)}
    if maint:
        env["SYNVEIL_KERNEL_TEST_RECOVERY_MAINT"] = "1"
    for name in ("kernel", "image"):
        subprocess.run(["bash", str(ROOT / "build/scripts" / (name + ".sh"))],
                       env=env, check=True)
    identity = {"case": case, "git_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "artifacts": {}}
    for name in ("synveil-initramfs.cpio", "synveil-x86_64.efi",
                 "synveil-x86_64.img"):
        path = fixture_out / name
        identity["artifacts"][name] = {"sha256": sha256(path),
                                       "bytes": path.stat().st_size}
    (fixture_out / "fixture.json").write_text(
        json.dumps(identity, indent=2) + "\n")
    return fixture_out


class SerialBoot:
    """One QEMU process over build/scripts/qemu.sh, serial both ways."""

    def __init__(self, name, fixture_out, state_disk=None):
        self.name = name
        self.log_path = OUT / f"{name}.log"
        self.lines = []
        self._lock = threading.Lock()
        env = {**os.environ, "SYNVEIL_OUT_DIR": str(fixture_out),
               "SYNVEIL_QEMU_VIDEO": "1"}
        if state_disk is not None:
            env["SYNVEIL_QEMU_STATE_DISK"] = str(state_disk)
        self.process = subprocess.Popen(
            ["bash", str(ROOT / "build/scripts/qemu.sh")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=env, start_new_session=True)
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

    def count(self, marker):
        with self._lock:
            return sum(1 for line in self.lines if marker in line)

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
    """After the console locks, serial input must get no shell."""
    before = len(boot.lines)
    boot.send(r"printf '\n%s%s\n' SYNVEIL_ SERIAL_UNLOCK_BYPASS")
    boot.send("exec /bin/sh")
    # The marker is split in the typed line so console echo of the
    # probe itself can never produce it; only real shell execution
    # (which would join the quotes away) could print it contiguously.
    boot.send("id; echo SYNVEIL_MAINT_PW''NED")
    time.sleep(4)
    window = "\n".join(boot.lines[before:])
    return ("SYNVEIL_SERIAL_UNLOCK_BYPASS" not in window
            and "SYNVEIL_MAINT_PWNED" not in window
            and "uid=0" not in window
            and "Recovery/bootstrap shell" not in window)


def enroll(boot, corrupt_after=False):
    """Developer-fixture enrollment of the synthetic credential."""
    if not boot.wait("Recovery/bootstrap shell"):
        return False
    boot.send("stty -echo")
    time.sleep(1)
    boot.send(f"printf '%s' '{SECRET}' | /usr/sbin/veil-enroll --password-stdin")
    if not boot.wait("SYNVEIL_CREDENTIAL_ENROLLED", 120):
        return False
    boot.send("stty echo")
    time.sleep(1)
    if corrupt_after:
        boot.send("printf '%s' 'not-a-phc-record' > "
                  "/var/lib/synveil/auth/credential.phc")
        boot.send("echo SYNVEIL_FIXTURE_CORRUPTED")
        if not boot.wait("SYNVEIL_FIXTURE_CORRUPTED", 60):
            return False
    boot.send("poweroff -f")
    time.sleep(3)
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    enroll_out = build_fixture("enroll", maint=False)
    maint_out = build_fixture("maint", maint=True)

    # --- maint-ok (+ the enrollment boot that seeds its disk) ---
    disk = OUT / "state-ok.img"
    disk.unlink(missing_ok=True)
    create_state_disk(disk, 128)
    boot = SerialBoot("enroll-ok", enroll_out, disk)
    results["enroll-ok"] = enroll(boot)
    boot.stop()
    boot = SerialBoot("maint-ok", maint_out, disk)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_V1")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT")
    boot.send(SECRET)
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_AUTH_OK")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_READY")
    boot.send("status")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_STATUS_BEGIN")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_AB confirmed_slot=none")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_STATUS_END")
    boot.send("restore")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_RESTORE_REFUSED")
    boot.send("rm -rf /")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_REJECTED")
    boot.send("exit")
    ok = ok and boot.wait("SYNVEIL_RECOVERY_MAINT_DONE")
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and probe_locked(boot)
    ok = ok and SECRET not in boot.transcript()
    results["maint-ok"] = ok
    boot.stop()

    # --- maint-wrong ---
    boot = SerialBoot("maint-wrong", maint_out, disk)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT")
    for attempt in (1, 2, 3):
        boot.send(WRONG)
        ok = ok and boot.wait(
            f"SYNVEIL_RECOVERY_MAINT_DENIED attempt={attempt}", 120)
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and "SYNVEIL_RECOVERY_MAINT_AUTH_OK" not in boot.transcript()
    ok = ok and probe_locked(boot)
    ok = ok and WRONG not in boot.transcript()
    results["maint-wrong"] = ok
    boot.stop()

    # --- maint-absent ---
    disk2 = OUT / "state-absent.img"
    disk2.unlink(missing_ok=True)
    create_state_disk(disk2, 128)
    boot = SerialBoot("maint-absent", maint_out, disk2)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_DENIED reason=no-credential")
    ok = ok and "SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT" not in boot.transcript()
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and probe_locked(boot)
    results["maint-absent"] = ok
    boot.stop()

    # --- maint-corrupt ---
    disk3 = OUT / "state-corrupt.img"
    disk3.unlink(missing_ok=True)
    create_state_disk(disk3, 128)
    boot = SerialBoot("enroll-corrupt", enroll_out, disk3)
    results["enroll-corrupt"] = enroll(boot, corrupt_after=True)
    boot.stop()
    boot = SerialBoot("maint-corrupt", maint_out, disk3)
    ok = boot.wait("SYNVEIL_RECOVERY_MAINT_DENIED reason=corrupt-credential")
    ok = ok and "SYNVEIL_RECOVERY_MAINT_PASSWORD_PROMPT" not in boot.transcript()
    ok = ok and boot.wait("SYNVEIL_SERIAL_RECOVERY_LOCKED")
    ok = ok and probe_locked(boot)
    results["maint-corrupt"] = ok
    boot.stop()

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                     cwd=ROOT, text=True).strip()
    verdict = {"schema": "synveil.recovery-maint-smoke/v1",
               "source_commit": commit,
               "cases": results,
               "secret_in_image": False,
               "physical_hardware_tested": False}
    (OUT / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
    for name, result in results.items():
        print(f"RECOVERY_MAINT_CASE {name}={'PASS' if result else 'FAIL'}")
    passed = all(results.values())
    print("SYNVEIL_RECOVERY_MAINT_SMOKE_PASS" if passed
          else "SYNVEIL_RECOVERY_MAINT_SMOKE_FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
