#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Graphical A/B update controls acceptance in QEMU (Priority 2).

Drives the A/B proof disk assembled by tools/native_ab.py through
the core update flow with the session's update GUI as the actor:
every update action in G1–G3 is a pointer click on the GUI's
buttons (located by screenshot pixels) — no terminal command is
issued for them. The agent's serial markers and the host-side
control record are the evidence the clicks had the claimed effect,
and the GUI's own session log (read back from the DATA partition)
records its clicks, actions and rendered states.

  G0  provision + enroll + login (the agent-driven baseline from
      the A/B proof); the GUI is present in the session, banner
      normal, and its log lands in the home directory
  G1  GUI: stage v2 from release media -> Restart -> trial boot ->
      login -> health passed -> Confirm. Confirmed becomes v2;
      credential + known file survive
  G2  GUI: Roll back (arms the retained v1) -> Restart -> trial
      boot -> login -> Confirm. Confirmed returns to v1; survival
  G3  GUI: stage from corrupted-signature media is refused by the
      agent; the GUI banner turns to the error state, no trial is
      created, the confirmed generation is untouched

The remaining A/B scenarios (failing candidate, interrupted stage,
both-slots-corrupt) stay agent-driven in tests/smoke-native-ab.py;
this driver covers the core path through the GUI, as the Priority 2
handoff specifies. QEMU evidence only; no physical hardware.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from qmp_input import QMPError, type_test_secret  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "smoke_native_ab", ROOT / "tests" / "smoke-native-ab.py")
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

OUT = ab.OUT
EVIDENCE = OUT / "gui-evidence"


def find_gui(boot, timeout=240):
    """Poll frames until the update GUI window is visible."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        rect = ab.gui_window_rect(*frame)
        if rect:
            return rect
        time.sleep(0.5)
    return None


def wait_banner(boot, kind, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            frame = ab.next_frame(boot)
        except (ValueError, OSError):
            time.sleep(0.5)
            continue
        rect = ab.gui_window_rect(*frame)
        if rect and ab.gui_banner_kind(*frame, rect) == kind:
            return True
        time.sleep(0.5)
    return False


def click_until(boot, button, marker, timeout=420, since=0):
    """Click a GUI button until the agent's marker lands.

    A click on a disabled button is a no-op in the GUI (state gates
    every button), so retrying while the state settles — e.g. the
    Confirm button enabling once the status shows health passed —
    is safe: at most one real action can be submitted, and the
    marker proves it happened.
    """
    index = ab.GUI_BUTTONS.index(button)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if boot.seen(marker, since=since):
            return True
        try:
            frame = ab.next_frame(boot)
            rect = ab.gui_window_rect(*frame)
        except (ValueError, OSError):
            rect = None
        if rect:
            ab.gui_click(boot, rect, index)
        if boot.wait(marker, 20, since=since):
            return True
    return boot.seen(marker, since=since)


def replayed_image(image):
    """Copy a partition image and replay its ext4 journal, so a
    post-quit read sees the guest's final state. A QEMU quit leaves
    the last seconds of writes in the journal; plain debugfs reads
    then return a stale control record (or unparseable bytes) even
    though the guest's serial markers prove the writes happened —
    the same hazard tests/smoke-native-ab.py's reader comments on.
    The replay runs on a scratch copy; the evidence disk is never
    modified."""
    target = image.with_suffix(".replayed.img")
    shutil.copyfile(image, target)
    subprocess.run(["e2fsck", "-y", str(target)], capture_output=True)
    return target


def replayed_state_artifacts(disk, workdir):
    """ab.state_artifacts, but read from journal-replayed images."""
    state = replayed_image(ab.extract_partition(disk, 4, workdir))
    data = replayed_image(ab.extract_partition(disk, 5, workdir))
    control_raw = ab.ext4_read(state, "/lib/synveil/ab-control.json")
    control = None
    if control_raw:
        try:
            control = json.loads(control_raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            control = None
    pref = ab.ext4_read(state, "/lib/synveil/desktop-pref.conf")
    credential = ab.ext4_read(state, "/lib/synveil/auth/credential.phc")
    known = ab.ext4_read(data, "/synveil/ab-known-file")
    import hashlib
    return {
        "control": control,
        "pref_sha256": hashlib.sha256(pref).hexdigest() if pref else None,
        "pref_text": pref.decode("utf-8", "replace") if pref else None,
        "credential_sha256": hashlib.sha256(credential).hexdigest() if credential else None,
        "known_file": known.decode("utf-8", "replace") if known else None,
    }


def data_read(disk, workdir, path):
    data = replayed_image(ab.extract_partition(disk, 5, workdir))
    return ab.ext4_read(data, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=600,
                        help="per-boot marker timeout budget")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("A/B GUI smoke requires a non-root runner")
    build_receipt = json.loads((OUT / "build-receipt.json").read_text())
    source_disk = OUT / "synveil-ab.img"
    if ab.sha256(source_disk) != build_receipt["disk"]["sha256"]:
        parser.error("A/B disk differs from its build receipt")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results = {}
    secret = secrets.token_hex(16)

    with tempfile.TemporaryDirectory(prefix="synveil-ab-gui-") as temp:
        work = Path(temp)
        disk = work / "synveil-ab.img"
        shutil.copyfile(source_disk, disk)
        vars_file = work / "OVMF_VARS.fd"
        _code, vars_template = ab.ovmf_paths()
        shutil.copyfile(vars_template, vars_file)

        def record(name, ok, **fields):
            results[name] = {"pass": bool(ok), "gui_driven": True, **fields}
            print(f"[synveil-ab-gui] {name}: {'PASS' if ok else 'FAIL'}",
                  flush=True)
            return bool(ok)

        # ---- G0: provision + enroll + login; GUI present in session
        boot = ab.Boot(work, "g0-provision", disk, vars_file)
        boot.log_path = EVIDENCE / "g0-provision.log"
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_PROVISIONED", args.timeout)
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
        if ok:
            ok = ab.login(boot, secret)
        gui_window = False
        gui_banner = False
        if ok:
            # Generous budgets: the session's GUI appears only after
            # the agent answers, and a loaded host slows the guest.
            # (One G0 run missed the 240 s window-find while the
            # GUI's own log later proved it had started; the two
            # halves are recorded separately so a recurrence is
            # diagnosable from the receipt alone.)
            gui_window = find_gui(boot, timeout=420) is not None
            if gui_window:
                gui_banner = wait_banner(boot, "normal", timeout=180)
        gui_seen = gui_window and gui_banner
        known_file_made = False
        if ok:
            known_file_made = ab.terminal_command(boot, "known-file")
            ok = known_file_made
            time.sleep(5)  # let the fixture's sync land before quit
        boot.quit()
        artifacts = replayed_state_artifacts(disk, work)
        gui_log = data_read(disk, work, "/synveil/update-gui.log")
        gui_log = gui_log.decode("utf-8", "replace") if gui_log else ""
        # Semantic gate (review correction, 2026-10-26): the GUI
        # evidence and the baseline artifacts are pass conditions,
        # not decorations — a missing GUI log or known file fails.
        ok = (ok and gui_seen
              and "SYNVEIL_UPDATE_GUI_READY" in gui_log
              and "SYNVEIL_UPDATE_GUI_STATUS" in gui_log
              and bool(artifacts["credential_sha256"])
              and artifacts["known_file"] == ab.EXPECTED_KNOWN_FILE)
        record("G0-provision-enroll-gui-present", ok,
               gui_window_seen=gui_window,
               gui_banner_normal=gui_banner,
               gui_log_ready="SYNVEIL_UPDATE_GUI_READY" in gui_log,
               gui_log_status="SYNVEIL_UPDATE_GUI_STATUS" in gui_log,
               credential_recorded=bool(artifacts["credential_sha256"]),
               known_file=artifacts["known_file"])
        baseline = artifacts
        if not results["G0-provision-enroll-gui-present"]["pass"]:
            return finish(build_receipt, results, 1)

        # ---- G1: stage, restart, trial, confirm — all via the GUI
        boot = ab.Boot(work, "g1-gui-stage-confirm", disk, vars_file,
                       extra_drives=[OUT / "release-v2.img"])
        boot.log_path = EVIDENCE / "g1-gui-stage-confirm.log"
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout)
        if ok:
            ok = ab.login(boot, secret)
        if ok:
            ok = wait_banner(boot, "normal", timeout=240)
        staged_at = len(boot.lines)
        if ok:
            ok = click_until(boot, "stage", "SYNVEIL_AB_STAGED",
                             timeout=600, since=staged_at)
        if ok:
            ok = wait_banner(boot, "trial", timeout=180)
        if ok:
            # The Restart click's proof is the trial hand-over on the
            # next boot; the GUI itself is gone once the guest reboots.
            ok = click_until(boot, "restart", "SYNVEIL_AB_TRIAL_HANDOVER",
                             timeout=300, since=staged_at)
        if ok:
            ok = boot.wait("SYNVEIL_AB_TRIAL_BOOT", args.timeout, since=staged_at)
        if ok:
            ok = ab.login(boot, secret, since=staged_at)
        if ok:
            ok = ab.desktop_color_proof(boot, 2)
        if ok:
            ok = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=staged_at)
        if ok:
            ok = wait_banner(boot, "trial", timeout=240)
        if ok:
            ok = click_until(boot, "confirm", "SYNVEIL_AB_CONFIRMED ",
                             timeout=420, since=staged_at)
        if ok:
            ok = wait_banner(boot, "normal", timeout=180)
        boot.quit()
        artifacts = replayed_state_artifacts(disk, work)
        control = artifacts["control"] or {}
        gui_log = data_read(disk, work, "/synveil/update-gui.log")
        gui_log = gui_log.decode("utf-8", "replace") if gui_log else ""
        gui_clicks = {name: f"SYNVEIL_UPDATE_GUI_CLICK action={name}" in gui_log
                      for name in ("stage", "reboot", "confirm")}
        gui_actions_ok = {
            name: f"SYNVEIL_UPDATE_GUI_ACTION action={name} ok=1" in gui_log
            for name in ("stage", "reboot", "confirm")}
        # Semantic gate: confirmed generation, strict survival, and
        # the GUI's own click/action evidence are all required.
        ok = (ok and ab.confirmed_matches(control, 2)
              and ab.strict_survival(artifacts, baseline)
              and all(gui_clicks.values())
              and all(gui_actions_ok.values()))
        record("G1-gui-stage-trial-confirm", ok,
               confirmed=(control.get("confirmed") or {}),
               gui_clicks=gui_clicks,
               gui_actions_ok=gui_actions_ok,
               survival={
                   "credential_same": artifacts["credential_sha256"] == baseline["credential_sha256"],
                   "known_file_same": artifacts["known_file"] == baseline["known_file"],
               })
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- G2: roll back to v1 via the GUI, then confirm it
        boot = ab.Boot(work, "g2-gui-rollback", disk, vars_file)
        boot.log_path = EVIDENCE / "g2-gui-rollback.log"
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout)
        if ok:
            ok = ab.login(boot, secret)
        if ok:
            ok = wait_banner(boot, "normal", timeout=240)
        mark = len(boot.lines)
        if ok:
            ok = click_until(boot, "rollback",
                             "SYNVEIL_AB_ROLLBACK result=trial slot=",
                             timeout=300, since=mark)
        if ok:
            ok = wait_banner(boot, "trial", timeout=180)
        if ok:
            ok = click_until(boot, "restart", "SYNVEIL_AB_TRIAL_BOOT",
                             timeout=300, since=mark)
        if ok:
            ok = ab.login(boot, secret, since=mark)
        if ok:
            ok = ab.desktop_color_proof(boot, 1)
        if ok:
            ok = boot.wait("SYNVEIL_AB_HEALTH_PASSED", 600, since=mark)
        if ok:
            ok = click_until(boot, "confirm", "SYNVEIL_AB_CONFIRMED ",
                             timeout=420, since=mark)
        if ok:
            ok = wait_banner(boot, "normal", timeout=180)
        boot.quit()
        artifacts = replayed_state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and ab.confirmed_matches(control, 1)
              and ab.strict_survival(artifacts, baseline))
        record("G2-gui-rollback-confirm", ok,
               confirmed=(control.get("confirmed") or {}),
               survival={
                   "credential_same": artifacts["credential_sha256"] == baseline["credential_sha256"],
                   "known_file_same": artifacts["known_file"] == baseline["known_file"],
               })
        if not ok:
            return finish(build_receipt, results, 1)

        # ---- G3: corrupted-signature release refused, shown in GUI
        boot = ab.Boot(work, "g3-gui-badsig", disk, vars_file,
                       extra_drives=[OUT / "release-v2-badsig.img"])
        boot.log_path = EVIDENCE / "g3-gui-badsig.log"
        boot.start(secret)
        ok = boot.wait("SYNVEIL_AB_CONFIRMED_BOOT", args.timeout)
        if ok:
            ok = ab.login(boot, secret)
        if ok:
            ok = wait_banner(boot, "normal", timeout=240)
        mark = len(boot.lines)
        if ok:
            ok = click_until(
                boot, "stage",
                "SYNVEIL_AB_ACTION_FAILED action=stage "
                "error=release signature verification failed",
                timeout=420, since=mark)
        if ok:
            ok = wait_banner(boot, "error", timeout=240)
        if ok:
            ok = not boot.seen("SYNVEIL_AB_STAGED", since=mark)
        boot.quit()
        artifacts = replayed_state_artifacts(disk, work)
        control = artifacts["control"] or {}
        ok = (ok and control.get("trial") is None
              and ab.confirmed_matches(control, 1))
        record("G3-gui-badsig-refused", ok,
               trial=control.get("trial"),
               confirmed=(control.get("confirmed") or {}))
        if not ok:
            return finish(build_receipt, results, 1)

    return finish(build_receipt, results, 0 if all(
        entry["pass"] for entry in results.values()) else 1)


def validate_gui_results(results):
    """Re-derive the GUI verdict from the recorded fields alone
    (review correction, 2026-10-26): a results dict whose recorded
    confirmed generation, survival flags or GUI click/action
    evidence contradict the scenario expectations yields
    violations, and finish() emits no demonstrated claim. The first
    GUI run's receipt is the canonical bad example: G1 'confirmed'
    recording version 1, G0 known_file null, the confirm click
    absent — all under pass flags. Exercised without QEMU by
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
        slot, generation_id = ab.GENERATION_SLOTS[version]
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

    g0 = need("G0-provision-enroll-gui-present")
    if g0 is not None:
        if g0.get("gui_window_seen") is not True:
            violations.append("G0: GUI window not seen")
        if g0.get("gui_banner_normal") is not True:
            violations.append("G0: GUI banner never read normal")
        if g0.get("gui_log_ready") is not True or g0.get("gui_log_status") is not True:
            violations.append("G0: GUI session log evidence missing")
        if g0.get("credential_recorded") is not True:
            violations.append("G0: credential not recorded")
        if g0.get("known_file") != ab.EXPECTED_KNOWN_FILE:
            violations.append("G0: known file missing or wrong content")
    g1 = need("G1-gui-stage-trial-confirm")
    confirmed_ok("G1-gui-stage-trial-confirm", g1, 2)
    survival_ok("G1-gui-stage-trial-confirm", g1)
    if g1 is not None:
        for section in ("gui_clicks", "gui_actions_ok"):
            values = g1.get(section) or {}
            for action in ("stage", "reboot", "confirm"):
                if values.get(action) is not True:
                    violations.append(f"G1: {section}.{action} is not true")
    g2 = need("G2-gui-rollback-confirm")
    confirmed_ok("G2-gui-rollback-confirm", g2, 1)
    survival_ok("G2-gui-rollback-confirm", g2)
    g3 = need("G3-gui-badsig-refused")
    confirmed_ok("G3-gui-badsig-refused", g3, 1)
    if g3 is not None and g3.get("trial") is not None:
        violations.append("G3: trial present after refused stage")
    return violations


def finish(build_receipt, results, code):
    try:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        source_commit = build_receipt["source_commit"]
    violations = validate_gui_results(results)
    demonstrated = code == 0 and not violations
    receipt = {
        "schema": "synveil.ab-gui-qemu-proof/v1",
        "source_commit": source_commit,
        "build_receipt_commit": build_receipt["source_commit"],
        "disk_sha256": build_receipt["disk"]["sha256"],
        "generations": {v: {"uki_sha256": g["uki_sha256"], "bytes": g["uki_bytes"]}
                        for v, g in build_receipt["generations"].items()},
        "publisher": build_receipt["publisher"],
        "scenarios": results,
        "gate_violations": violations,
        "gui_driven_scenarios": ["G1-gui-stage-trial-confirm",
                                 "G2-gui-rollback-confirm",
                                 "G3-gui-badsig-refused"],
        "agent_driven_scenarios": "tests/smoke-native-ab.py (nine scenarios)",
        "graphical_update_controls_demonstrated": demonstrated,
        "firmware_variables_written": False,
        "physical_hardware_tested": False,
    }
    (OUT / "gui-smoke-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n")
    print("SYNVEIL_AB_GUI_QEMU_PROOF_" + ("PASS" if demonstrated else "INCOMPLETE"))
    return 0 if demonstrated else 1


if __name__ == "__main__":
    raise SystemExit(main())
