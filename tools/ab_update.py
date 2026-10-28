#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""A/B update transaction core v2 (docs/AB_UPDATER.md).

Same file-only safety envelope as tools/ab_vm_update.py — a named,
pre-existing regular-file GPT disk in out/install-prototype/; no
physical devices, mounting, firmware access or boot changes. What v2
adds over the v1 fixture is the trust and policy layer of the real
design:

* the publisher key is PINNED and provisioned from a trust directory
  that is separate from the release directory; a release that
  supplies its own key material is refused by construction;
* release metadata carries a validity window (issued/expiry), the
  signing key id, and a state-format migration window; replay and
  downgrade offers are refused even when validly signed;
* the control record keeps per-slot generation identities and a
  bounded history on the (fixture) state side, so owner rollback to
  a retained generation is a first-class action;
* a distinct both-slots-unusable outcome is reported, never silently
  papered over: state/data are untouched and the recovery entry is
  the documented path.

Guest boot selection (UEFI BootNext), early-init attempt accounting
and health confirmation live in the guest (Pass B); this module is
the transaction core they mirror, schema synveil.ab-control/v2.
"""
import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from ab_image_prototype import NAME  # noqa: E402
from ab_vm_update import (  # noqa: E402
    InterruptedTrial, MAX_RELEASE, UpdateError, atomic_control,
    check_region, digest_valid, json_small, pairs_unique, read_small,
    regular_fd, verified_layout,
)

CONTROL_SCHEMA = "synveil.ab-control/v2"
RELEASE_SCHEMA = "synveil.ab-release/v2"
TRUST_SCHEMA = "synveil.ab-trust/v1"
MAX_HISTORY = 32
TRIAL_ATTEMPTS = 3


def _generation_id_valid(value):
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and all(c.isalnum() or c == "-" for c in value))


def _is_int(value):
    return type(value) is int


def load_trust(trust_dir):
    """Load the independently provisioned publisher trust anchor.

    The trust directory is operator-provisioned out of band; nothing
    in a release can influence it. Returns (key_id, development, pem).
    """
    if not trust_dir.is_dir() or trust_dir.is_symlink():
        raise UpdateError("trust directory missing or unsafe")
    meta = json_small(trust_dir / "publisher.json")
    if (not isinstance(meta, dict) or set(meta) != {"schema", "key_id", "development"}
            or meta["schema"] != TRUST_SCHEMA
            or not isinstance(meta["key_id"], str) or not meta["key_id"]
            or type(meta["development"]) is not bool):
        raise UpdateError("invalid publisher trust record")
    pem_path = trust_dir / "publisher.pem"
    fd = regular_fd(pem_path, max_bytes=8192)
    os.close(fd)
    return meta["key_id"], meta["development"], pem_path


def signed_release(directory, name, trust, now):
    """Verify a v2 release against the PINNED key and policy window."""
    key_id, _development, pem_path = trust
    # A release directory carrying its own trust material is the v1
    # fixture's known flaw; in v2 it is an explicit refusal.
    for planted in ("publisher.pem", "publisher.json", "fixture-trust.pem"):
        if (directory / planted).exists() or (directory / planted).is_symlink():
            raise UpdateError("release directory carries trust material; refused")
    manifest = directory / (name + ".release.json")
    signature = directory / (name + ".release.sig")
    payload = directory / (name + ".release.bin")
    raw = read_small(manifest)
    data = json.loads(raw, object_pairs_hook=pairs_unique)
    fields = {"schema", "version", "bytes", "sha256", "key_id",
              "issued_at", "expires_at", "state_format",
              "min_reader_state_format", "rollback_breaking",
              "generation_id"}
    if (not isinstance(data, dict) or set(data) != fields
            or data["schema"] != RELEASE_SCHEMA
            or not _is_int(data["version"]) or data["version"] < 1
            or not _is_int(data["bytes"]) or not 0 < data["bytes"] <= MAX_RELEASE
            or not digest_valid(data["sha256"])
            or data["key_id"] != key_id
            or not _is_int(data["issued_at"]) or not _is_int(data["expires_at"])
            or not _is_int(data["state_format"]) or data["state_format"] < 1
            or not _is_int(data["min_reader_state_format"])
            or not 1 <= data["min_reader_state_format"] <= data["state_format"]
            or type(data["rollback_breaking"]) is not bool
            or not _generation_id_valid(data["generation_id"])):
        raise UpdateError("invalid release manifest")
    if now < data["issued_at"]:
        raise UpdateError("release metadata not yet valid")
    if now > data["expires_at"]:
        raise UpdateError("release metadata expired")
    if data["rollback_breaking"]:
        raise UpdateError("rollback-breaking releases are refused in this milestone")
    fd = regular_fd(signature, max_bytes=256)
    os.close(fd)
    release_fd = regular_fd(payload, max_bytes=MAX_RELEASE)
    try:
        if os.fstat(release_fd).st_size != data["bytes"]:
            raise UpdateError("release byte count mismatch")
        hasher = hashlib.sha256()
        with os.fdopen(os.dup(release_fd), "rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
        if not hmac.compare_digest(hasher.hexdigest(), data["sha256"]):
            raise UpdateError("release payload hash mismatch")
        try:
            verify = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pem_path),
                 "-rawin", "-in", str(manifest), "-sigfile", str(signature)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError("signature verifier unavailable") from exc
        if verify.returncode:
            raise UpdateError("release signature verification failed")
        return data, release_fd
    except BaseException:
        os.close(release_fd)
        raise


def _generation(record, slots, *, with_state_format):
    if not isinstance(record, dict):
        raise UpdateError("invalid generation record")
    keys = {"slot", "version", "bytes", "sha256"} | (
        {"state_format", "generation_id", "min_reader_state_format"}
        if with_state_format else set())
    if set(record) != keys:
        raise UpdateError("invalid generation record fields")
    if (record["slot"] not in ("A", "B")
            or not _is_int(record["version"]) or record["version"] < 1
            or not _is_int(record["bytes"])
            or not 0 < record["bytes"] <= slots["SYSTEM_" + record["slot"]][1]
            or not digest_valid(record["sha256"])
            or (with_state_format and (
                not _is_int(record["state_format"])
                or record["state_format"] < 1
                or not _generation_id_valid(record["generation_id"])
                or not _is_int(record["min_reader_state_format"])
                or not 1 <= record["min_reader_state_format"]
                <= record["state_format"]))):
        raise UpdateError("invalid generation record values")
    return record


def validate_control(c, slots):
    if (not isinstance(c, dict)
            or set(c) != {"schema", "confirmed", "trial", "slots", "history"}
            or c["schema"] != CONTROL_SCHEMA):
        raise UpdateError("invalid control schema")
    confirmed = _generation(c["confirmed"], slots, with_state_format=True)
    slot_map = c["slots"]
    if not isinstance(slot_map, dict) or set(slot_map) != {"A", "B"}:
        raise UpdateError("invalid slot map")
    for slot in ("A", "B"):
        if slot_map[slot] is not None:
            entry = _generation(slot_map[slot], slots, with_state_format=True)
            if entry["slot"] != slot:
                raise UpdateError("slot map identity mismatch")
    if slot_map[confirmed["slot"]] is None or \
            slot_map[confirmed["slot"]]["version"] != confirmed["version"]:
        raise UpdateError("confirmed generation missing from slot map")
    trial = c["trial"]
    if trial is not None:
        if (not isinstance(trial, dict)
                or set(trial) != {"slot", "version", "bytes", "sha256",
                                  "state_format", "generation_id",
                                  "min_reader_state_format",
                                  "attempts", "awaiting_health",
                                  "chain_in_flight"}):
            raise UpdateError("invalid trial record")
        _generation({k: trial[k] for k in
                     ("slot", "version", "bytes", "sha256", "state_format",
                      "generation_id", "min_reader_state_format")},
                    slots, with_state_format=True)
        if (trial["slot"] == confirmed["slot"]
                or not _is_int(trial["attempts"])
                or not 0 <= trial["attempts"] <= TRIAL_ATTEMPTS
                or type(trial["awaiting_health"]) is not bool
                or type(trial["chain_in_flight"]) is not bool):
            raise UpdateError("invalid trial values")
    history = c["history"]
    if (not isinstance(history, list) or len(history) > MAX_HISTORY
            or any(not isinstance(h, dict)
                   or set(h) != {"action", "slot", "version", "at", "result"}
                   for h in history)):
        raise UpdateError("invalid history")
    return c


def _history(state, action, slot, version, at, result):
    state["history"] = (state["history"] + [{
        "action": action, "slot": slot, "version": version,
        "at": at, "result": result}])[-MAX_HISTORY:]


def operate(directory, trust_dir, name, action, *, interrupt=None, now=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise UpdateError("invalid fixture name")
    if not directory.is_dir() or directory.is_symlink() \
            or directory.resolve() != directory.absolute():
        raise UpdateError("unsafe fixture directory")
    now = int(time.time()) if now is None else now
    disk = directory / (name + ".raw")
    layout = directory / (name + ".layout.json")
    control_path = directory / (name + ".control.json")
    writable = action in ("initialize-fixture", "stage")
    fd = regular_fd(disk, writable=writable)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        slots = verified_layout(fd, json_small(layout))
        if action == "initialize-fixture":
            if control_path.exists() or control_path.is_symlink():
                raise UpdateError("fixture already initialized")
            source = regular_fd(directory / (name + ".base.bin"),
                                max_bytes=MAX_RELEASE)
            try:
                length = os.fstat(source).st_size
                off, capacity = slots["SYSTEM_A"]
                if length > capacity:
                    raise UpdateError("fixture baseline exceeds A slot")
                if os.pread(fd, min(length, 65536), off).strip(b"\0"):
                    raise UpdateError("refusing nonempty uninitialized slot A")
                digest = hashlib.sha256()
                pos = 0
                while pos < length:
                    chunk = os.pread(source, min(1024 * 1024, length - pos), pos)
                    if not chunk:
                        raise UpdateError("fixture baseline read shortened")
                    digest.update(chunk)
                    written = 0
                    while written < len(chunk):
                        n = os.pwrite(fd, chunk[written:], off + pos + written)
                        if n <= 0:
                            raise UpdateError("short initial slot write")
                        written += n
                    pos += len(chunk)
                os.fsync(fd)
                baseline = {"slot": "A", "version": 1, "bytes": length,
                            "sha256": digest.hexdigest(), "state_format": 1,
                            "generation_id": name + "-gen-1",
                            "min_reader_state_format": 1}
                if not check_region(fd, (off, capacity), baseline):
                    raise UpdateError("baseline byte verification failed")
                state = {"schema": CONTROL_SCHEMA, "confirmed": baseline,
                         "trial": None,
                         "slots": {"A": dict(baseline), "B": None},
                         "history": []}
                _history(state, "initialize", "A", 1, now, "confirmed")
                atomic_control(control_path, state)
                return {"fixture_initialized": True, "confirmed_slot": "A",
                        "bootable": False}
            finally:
                os.close(source)
        state = validate_control(json_small(control_path), slots)
        confirmed = state["confirmed"]
        confirmed_ok = check_region(fd, slots["SYSTEM_" + confirmed["slot"]],
                                    confirmed)
        if action == "status":
            other = "B" if confirmed["slot"] == "A" else "A"
            other_rec = state["slots"][other]
            other_ok = other_rec is not None and check_region(
                fd, slots["SYSTEM_" + other], other_rec)
            if not confirmed_ok and not other_ok:
                return {"outcome": "both-slots-unusable",
                        "recovery": "boot the ESP recovery entry; STATE/DATA "
                                    "untouched; re-stage a known-good "
                                    "generation to recover",
                        "state_data_untouched": True}
            return {"outcome": "ok" if confirmed_ok else "confirmed-corrupt",
                    "confirmed_slot_ok": confirmed_ok,
                    "other_slot": other,
                    "other_slot_ok": bool(other_ok),
                    "trial_pending": state["trial"] is not None}
        if not confirmed_ok:
            raise UpdateError("confirmed system slot corruption: refusing operation")
        if action == "stage":
            if state["trial"] is not None:
                raise UpdateError("trial already pending; decide or roll back first")
            trust = load_trust(trust_dir)
            release, release_fd = signed_release(directory, name, trust, now)
            try:
                if release["version"] <= confirmed["version"]:
                    raise UpdateError(
                        "replay/downgrade refused: version not above confirmed")
                # Migration window: the candidate must read today's STATE
                # and its writes must stay readable by the confirmed
                # generation, so rollback remains safe after it has run.
                if release["min_reader_state_format"] > confirmed["state_format"]:
                    raise UpdateError(
                        "migration refused: candidate cannot read current state")
                if release["state_format"] < confirmed["state_format"]:
                    raise UpdateError(
                        "migration refused: candidate would strand newer state")
                # Rollback direction (the Pass A defect): after the
                # candidate runs, STATE will be at the candidate's
                # format, and the confirmed generation — the rollback
                # target — must still be able to read it. Its readable
                # range is [min_reader_state_format, state_format].
                if not (confirmed["min_reader_state_format"]
                        <= release["state_format"]
                        <= confirmed["state_format"]):
                    raise UpdateError(
                        "migration refused: confirmed generation could not "
                        "read state after candidate writes (rollback unsafe)")
                inactive = "B" if confirmed["slot"] == "A" else "A"
                offset, capacity = slots["SYSTEM_" + inactive]
                if release["bytes"] > capacity:
                    raise UpdateError("signed release exceeds inactive slot")
                cursor = 0
                while cursor < release["bytes"]:
                    chunk = os.pread(release_fd,
                                     min(1024 * 1024, release["bytes"] - cursor),
                                     cursor)
                    if not chunk:
                        raise UpdateError("release file changed during staging")
                    done = 0
                    while done < len(chunk):
                        count = os.pwrite(fd, chunk[done:],
                                          offset + cursor + done)
                        if count <= 0:
                            raise UpdateError("short inactive-slot write")
                        done += count
                    cursor += len(chunk)
                    if interrupt == "during-copy":
                        os.fsync(fd)
                        raise InterruptedTrial(
                            "interrupted after partial inactive slot write")
                os.fsync(fd)
                if interrupt == "after-slot-sync":
                    raise InterruptedTrial("interrupted before candidate activation")
                if not check_region(fd, (offset, capacity), release):
                    raise UpdateError("inactive-slot write verification failed")
                trial = {"slot": inactive, "version": release["version"],
                         "bytes": release["bytes"], "sha256": release["sha256"],
                         "state_format": release["state_format"],
                         "generation_id": release["generation_id"],
                         "min_reader_state_format":
                             release["min_reader_state_format"],
                         "attempts": TRIAL_ATTEMPTS, "awaiting_health": False,
                         "chain_in_flight": False}
                state["trial"] = trial
                # The slot map records what each slot currently holds.
                state["slots"][inactive] = {
                    k: trial[k] for k in
                    ("slot", "version", "bytes", "sha256", "state_format",
                     "generation_id", "min_reader_state_format")}
                _history(state, "stage", inactive, release["version"], now, "trial")
                atomic_control(control_path, state)
                if interrupt == "after-state-sync":
                    raise InterruptedTrial("interrupted after candidate record persisted")
                return {"staged_slot": inactive, "trial_attempts": TRIAL_ATTEMPTS,
                        "publisher_key_id": trust[0],
                        "publisher_development_key": trust[1]}
            finally:
                os.close(release_fd)
        if action == "boot-trial":
            trial = state["trial"]
            if trial is None:
                return {"selected_slot": confirmed["slot"], "reason": "confirmed"}
            if trial["attempts"] == 0 or not check_region(
                    fd, slots["SYSTEM_" + trial["slot"]], trial):
                state["trial"] = None
                _history(state, "trial-fallback", confirmed["slot"],
                         confirmed["version"], now, "confirmed")
                atomic_control(control_path, state)
                return {"selected_slot": confirmed["slot"], "reason": "fallback"}
            trial["attempts"] -= 1
            trial["awaiting_health"] = True
            atomic_control(control_path, state)  # Durable BEFORE selection.
            return {"selected_slot": trial["slot"], "reason": "trial",
                    "remaining": trial["attempts"]}
        if action == "confirm":
            trial = state["trial"]
            if trial is None or not trial["awaiting_health"]:
                raise UpdateError("cannot confirm without recorded trial boot")
            if not check_region(fd, slots["SYSTEM_" + trial["slot"]], trial):
                raise UpdateError("cannot confirm corrupted candidate")
            promoted = {k: trial[k] for k in
                        ("slot", "version", "bytes", "sha256", "state_format",
                         "generation_id", "min_reader_state_format")}
            state["confirmed"] = promoted
            state["slots"][promoted["slot"]] = dict(promoted)
            state["trial"] = None
            _history(state, "confirm", promoted["slot"], promoted["version"],
                     now, "confirmed")
            atomic_control(control_path, state)
            return {"confirmed_slot": promoted["slot"],
                    "note": "operator-fixture-only, no guest health evidence"}
        if action == "rollback":
            if state["trial"] is not None:
                trial = state["trial"]
                # A trial that already booted may have written STATE
                # at its own format; discarding it is safe only if the
                # confirmed generation can read that format.
                if trial["awaiting_health"] and not (
                        confirmed["min_reader_state_format"]
                        <= trial["state_format"]
                        <= confirmed["state_format"]):
                    raise UpdateError(
                        "rollback refused: trial may have advanced state "
                        "beyond the confirmed generation's readable range")
                state["trial"] = None
                _history(state, "rollback", confirmed["slot"],
                         confirmed["version"], now, "trial-discarded")
                atomic_control(control_path, state)
                return {"selected_slot": confirmed["slot"],
                        "reason": "pending-trial-discarded"}
            other = "B" if confirmed["slot"] == "A" else "A"
            retained = state["slots"][other]
            if retained is None:
                raise UpdateError("no retained generation to roll back to")
            if not check_region(fd, slots["SYSTEM_" + other], retained):
                raise UpdateError("retained generation is corrupt; rollback refused")
            # The retained generation must be able to read STATE as
            # the confirmed generation leaves it.
            if not (retained["min_reader_state_format"]
                    <= confirmed["state_format"]
                    <= retained["state_format"]):
                raise UpdateError(
                    "rollback refused: retained generation cannot read "
                    "the current state format")
            state["trial"] = {**retained, "attempts": TRIAL_ATTEMPTS,
                              "awaiting_health": False,
                              "chain_in_flight": False}
            _history(state, "rollback", other, retained["version"], now, "trial")
            atomic_control(control_path, state)
            return {"selected_slot": other, "reason": "owner-rollback-trial",
                    "version": retained["version"]}
        raise UpdateError("unknown action")
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--action", required=True,
                        choices=("initialize-fixture", "stage", "boot-trial",
                                 "confirm", "rollback", "status"))
    parser.add_argument("--trust-dir", type=Path, default=None,
                        help="independently provisioned publisher trust "
                             "directory (required for stage)")
    parser.add_argument("--now", type=int, default=None,
                        help="override current time (tests only)")
    args = parser.parse_args()
    if (ROOT / "out").is_symlink():
        parser.error("unsafe output parent")
    trust_dir = args.trust_dir
    if args.action == "stage" and trust_dir is None:
        parser.error("stage requires --trust-dir (pinned publisher trust)")
    try:
        result = operate(ROOT / "out/install-prototype", trust_dir,
                         args.name, args.action, now=args.now)
    except (UpdateError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({**result, "guest_boot_verified": False,
                      "efi_boot_selection_changed": False,
                      "physical_device_written": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
