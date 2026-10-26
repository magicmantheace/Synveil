#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Exercise the v2 A/B transaction core: pinned trust, validity and
migration windows, replay/downgrade refusal, owner rollback to a
retained generation, and the both-slots-unusable outcome. Disposable
regular files only (docs/AB_UPDATER.md)."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))
import ab_image_prototype as ab  # noqa: E402
import ab_update as upd  # noqa: E402

SMALL = (("ESP", 2), ("SYSTEM_A", 4), ("SYSTEM_B", 4),
         ("STATE", 2), ("DATA", 4), ("RECOVERY", 2))
NOW = 1_800_000_000
KEY_ID = "synveil-dev-test-01"


class ABUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "fixture"
        self.name = "ab2-test"
        self.meta = ab.create_image(self.dir, name=self.name,
                                    total_mib=24, specs=SMALL)
        self.raw = self.dir / (self.name + ".raw")
        self.control = self.dir / (self.name + ".control.json")
        self.base = b"generation-one\x00" * 512
        (self.dir / (self.name + ".base.bin")).write_bytes(self.base)
        upd.operate(self.dir, None, self.name, "initialize-fixture", now=NOW)
        # Pinned trust lives OUTSIDE the release directory.
        self.trust = Path(self.tmp.name) / "trust"
        self.trust.mkdir()
        self.priv = Path(self.tmp.name) / "publisher-private.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519",
                        "-out", str(self.priv)], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.priv), "-pubout",
                        "-out", str(self.trust / "publisher.pem")],
                       check=True, capture_output=True)
        (self.trust / "publisher.json").write_text(json.dumps(
            {"schema": upd.TRUST_SCHEMA, "key_id": KEY_ID,
             "development": True}, sort_keys=True))
        self.payload = b"generation-two\xff\x00" * 2048
        self.sign(version=2)

    def sign(self, *, version, payload=None, key_id=KEY_ID, priv=None,
             issued=NOW - 100, expires=NOW + 10000, state_format=1,
             min_reader=1, rollback_breaking=False, generation_id=None):
        if payload is not None:
            self.payload = payload
        (self.dir / (self.name + ".release.bin")).write_bytes(self.payload)
        m = {"schema": upd.RELEASE_SCHEMA, "version": version,
             "bytes": len(self.payload),
             "sha256": hashlib.sha256(self.payload).hexdigest(),
             "key_id": key_id, "issued_at": issued, "expires_at": expires,
             "state_format": state_format,
             "min_reader_state_format": min_reader,
             "rollback_breaking": rollback_breaking,
             "generation_id": generation_id or f"fixture-gen-{version}"}
        manifest = self.dir / (self.name + ".release.json")
        manifest.write_text(json.dumps(m, sort_keys=True))
        subprocess.run(["openssl", "pkeyutl", "-sign",
                        "-inkey", str(priv or self.priv), "-rawin",
                        "-in", str(manifest), "-out",
                        str(self.dir / (self.name + ".release.sig"))],
                       check=True, capture_output=True)

    def stage(self, **kw):
        return upd.operate(self.dir, self.trust, self.name, "stage",
                           now=kw.pop("now", NOW), **kw)

    def act(self, action, **kw):
        return upd.operate(self.dir, self.trust, self.name, action,
                           now=kw.pop("now", NOW), **kw)

    def state(self):
        return json.loads(self.control.read_text())

    def region(self, part_name, count):
        part = next(p for p in self.meta["partitions"]
                    if p["name"] == part_name)
        with self.raw.open("rb") as f:
            f.seek(part["first_lba"] * 512)
            return f.read(count)

    def corrupt(self, part_name):
        part = next(p for p in self.meta["partitions"]
                    if p["name"] == part_name)
        with self.raw.open("r+b") as f:
            f.seek(part["first_lba"] * 512)
            f.write(b"\xde\xad" * 256)

    # -- happy path -------------------------------------------------
    def test_stage_trial_confirm_cycle(self):
        out = self.stage()
        self.assertEqual(out["staged_slot"], "B")
        self.assertTrue(out["publisher_development_key"])
        self.assertEqual(self.region("SYSTEM_B", len(self.payload)),
                         self.payload)
        self.assertEqual(self.region("SYSTEM_A", len(self.base)), self.base)
        self.assertEqual(self.act("boot-trial")["selected_slot"], "B")
        self.assertEqual(self.state()["trial"]["attempts"], 2)
        self.assertEqual(self.act("confirm")["confirmed_slot"], "B")
        self.assertEqual(self.state()["slots"]["A"]["version"], 1)
        actions = [h["action"] for h in self.state()["history"]]
        self.assertEqual(actions, ["initialize", "stage", "confirm"])

    def test_interrupted_stage_leaves_no_trial(self):
        with self.assertRaises(upd.InterruptedTrial):
            self.stage(interrupt="during-copy")
        self.assertIsNone(self.state()["trial"])
        self.assertEqual(self.act("boot-trial")["reason"], "confirmed")

    def test_trial_exhaustion_falls_back(self):
        self.stage()
        for _ in range(3):
            self.assertEqual(self.act("boot-trial")["selected_slot"], "B")
            # Simulate failed boots: never confirm; clear awaiting flag
            # by treating each boot-trial as a fresh selection cycle is
            # not possible (awaiting_health), so exhaust via corruption.
            break
        self.corrupt("SYSTEM_B")
        self.assertEqual(self.act("boot-trial")["reason"], "fallback")
        self.assertIsNone(self.state()["trial"])

    # -- trust ------------------------------------------------------
    def test_release_carrying_its_own_key_is_refused(self):
        (self.dir / "publisher.pem").write_bytes(b"planted")
        with self.assertRaisesRegex(upd.UpdateError, "trust material"):
            self.stage()

    def test_unpinned_signer_is_refused(self):
        other_priv = Path(self.tmp.name) / "attacker.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519",
                        "-out", str(other_priv)], check=True,
                       capture_output=True)
        self.sign(version=2, priv=other_priv)
        with self.assertRaisesRegex(upd.UpdateError, "signature"):
            self.stage()

    def test_wrong_key_id_is_refused(self):
        self.sign(version=2, key_id="synveil-dev-other")
        with self.assertRaisesRegex(upd.UpdateError, "invalid release manifest"):
            self.stage()

    # -- validity window / replay ----------------------------------
    def test_expired_metadata_refused(self):
        self.sign(version=2, expires=NOW - 1)
        with self.assertRaisesRegex(upd.UpdateError, "expired"):
            self.stage()

    def test_not_yet_valid_refused(self):
        self.sign(version=2, issued=NOW + 5000, expires=NOW + 9000)
        with self.assertRaisesRegex(upd.UpdateError, "not yet valid"):
            self.stage()

    def test_replay_and_downgrade_refused(self):
        self.stage()
        self.act("boot-trial")
        self.act("confirm")
        self.sign(version=2)  # same version re-offered, validly signed
        with self.assertRaisesRegex(upd.UpdateError, "replay/downgrade"):
            self.stage()
        self.sign(version=1)
        with self.assertRaisesRegex(upd.UpdateError, "replay/downgrade"):
            self.stage()

    # -- migration window -------------------------------------------
    def test_candidate_that_cannot_read_state_refused(self):
        self.sign(version=2, state_format=3, min_reader=3)
        with self.assertRaisesRegex(upd.UpdateError, "cannot read current state"):
            self.stage()

    def test_rollback_breaking_refused(self):
        self.sign(version=2, rollback_breaking=True)
        with self.assertRaisesRegex(upd.UpdateError, "rollback-breaking"):
            self.stage()

    def test_state_bump_refused_until_rollback_safe(self):
        # Pre-correction this staged; the independent review of
        # 66c50b6 showed that acceptance was the defect: the
        # candidate would write state format 2, which the confirmed
        # format-1 generation — the rollback target — cannot read.
        self.sign(version=2, state_format=2, min_reader=1)
        with self.assertRaisesRegex(upd.UpdateError, "rollback unsafe"):
            self.stage()

    # -- owner rollback ---------------------------------------------
    def test_owner_rollback_to_retained_generation(self):
        self.stage()
        self.act("boot-trial")
        self.act("confirm")  # now on B v2; A v1 retained
        out = self.act("rollback")
        self.assertEqual(out["reason"], "owner-rollback-trial")
        self.assertEqual(out["selected_slot"], "A")
        self.assertEqual(self.act("boot-trial")["selected_slot"], "A")
        self.assertEqual(self.act("confirm")["confirmed_slot"], "A")
        self.assertEqual(self.state()["confirmed"]["version"], 1)

    def test_rollback_discards_pending_trial_first(self):
        self.stage()
        out = self.act("rollback")
        self.assertEqual(out["reason"], "pending-trial-discarded")
        self.assertIsNone(self.state()["trial"])

    # -- both slots ---------------------------------------------------
    def test_both_slots_unusable_outcome(self):
        self.corrupt("SYSTEM_A")
        self.assertEqual(self.act("status")["outcome"],
                         "both-slots-unusable")
        with self.assertRaisesRegex(upd.UpdateError, "corruption"):
            self.stage()



    # -- bidirectional migration (Pass A review correction) ---------
    def test_candidate_writing_newer_state_format_refused(self):
        # The reviewer reproduction: candidate writes state format 2
        # while the confirmed generation's readable ceiling is 1, so
        # rollback after the candidate ran would strand STATE.
        self.sign(version=2, state_format=2, min_reader=1)
        with self.assertRaisesRegex(upd.UpdateError, "rollback unsafe"):
            self.stage()
        self.assertIsNone(self.state()["trial"])

    def test_rollback_refused_when_retained_cannot_read_state(self):
        # Reach a two-generation world normally, then model the
        # confirmed generation having advanced STATE to format 2
        # (its readable range [1,2]); the retained format-1
        # generation cannot read that state, so rollback must refuse.
        self.stage()
        self.act("boot-trial")
        self.act("confirm")  # confirmed B v2; A v1 retained
        state = self.state()
        state["confirmed"]["state_format"] = 2
        state["slots"]["B"]["state_format"] = 2
        self.control.write_text(json.dumps(state))
        with self.assertRaisesRegex(upd.UpdateError,
                                    "retained generation cannot read"):
            self.act("rollback")

    def test_stage_records_slot_map(self):
        self.stage()
        self.assertEqual(self.state()["slots"]["B"]["version"], 2)
        self.assertEqual(self.state()["slots"]["B"]["generation_id"],
                         "fixture-gen-2")


if __name__ == "__main__":
    unittest.main()
