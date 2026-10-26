#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Gate regression tests for the A/B QEMU proof drivers.

Review correction (2026-10-26): the Pass B receipt recorded S2
"v2 confirmed" with confirmed version 1/slot A, S3 "rollback to
v1" with version 2/slot B, and S0 known_file null — under all-true
pass flags — and finish() emitted
bootable_update_rollback_demonstrated from those flags alone. The
reviewer reproduced the false pass by feeding the saved results
to finish().

These tests pin the fix without QEMU: both drivers validate the
recorded fields semantically (expected confirmed
version/slot/generation per scenario, strict survival, required
GUI click/action evidence), and finish() re-derives its headline
verdict from that validation. A fabricated results dict that is
fully consistent passes; every inconsistent variant — including
the two historical receipts' exact defect patterns — cannot.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ab = load_module("smoke_native_ab", "smoke-native-ab.py")
gui = load_module("smoke_native_ab_gui", "smoke-native-ab-gui.py")

CONF_V1 = {"version": 1, "slot": "A", "generation_id": "synveil-gen-v1"}
CONF_V2 = {"version": 2, "slot": "B", "generation_id": "synveil-gen-v2"}
SURVIVAL = {"credential_same": True, "pref_same": True,
            "known_file_same": True}

BUILD_RECEIPT = {
    "source_commit": "0" * 40,
    "disk": {"sha256": "0" * 64},
    "generations": {"1": {"uki_sha256": "1" * 64, "uki_bytes": 1}},
    "publisher": {"key_id": "synveil-dev-2026-01", "development": True},
}


def consistent_agent_results():
    return {
        "S0-provision-enroll": {
            "pass": True, "home_mounted": True,
            "credential_recorded": True,
            "known_file": ab.EXPECTED_KNOWN_FILE},
        "S1-stage-v2": {"pass": True},
        "S2-trial-v2-confirmed": {
            "pass": True, "confirmed": dict(CONF_V2),
            "survival": dict(SURVIVAL)},
        "S3-owner-rollback-v1": {
            "pass": True, "confirmed": dict(CONF_V1),
            "survival": dict(SURVIVAL)},
        "S4-failing-candidate-fallback": {
            "pass": True, "confirmed": dict(CONF_V1),
            "trial_after_fallback": None, "survival": dict(SURVIVAL)},
        "S5-invalid-signature-refused": {
            "pass": True, "trial": None, "confirmed": dict(CONF_V1)},
        "S6-interrupted-stage": {
            "pass": True, "trial": None, "confirmed": dict(CONF_V1)},
        "S6b-stage-after-interrupt": {"pass": True, "trial": None},
        "S7-corrupt-slots-fail-closed": {"pass": True},
    }


def consistent_gui_results():
    return {
        "G0-provision-enroll-gui-present": {
            "pass": True, "gui_driven": True, "gui_window_seen": True,
            "gui_banner_normal": True,
            "gui_log_ready": True, "gui_log_status": True,
            "credential_recorded": True,
            "known_file": ab.EXPECTED_KNOWN_FILE},
        "G1-gui-stage-trial-confirm": {
            "pass": True, "gui_driven": True, "confirmed": dict(CONF_V2),
            "gui_clicks": {"stage": True, "reboot": True, "confirm": True},
            "gui_actions_ok": {"stage": True, "reboot": True, "confirm": True},
            "survival": {"credential_same": True, "known_file_same": True}},
        "G2-gui-rollback-confirm": {
            "pass": True, "gui_driven": True, "confirmed": dict(CONF_V1),
            "survival": {"credential_same": True, "known_file_same": True}},
        "G3-gui-badsig-refused": {
            "pass": True, "gui_driven": True, "trial": None,
            "confirmed": dict(CONF_V1)},
    }


class AgentGate(unittest.TestCase):
    def test_consistent_results_validate_clean(self):
        self.assertEqual(ab.validate_results(consistent_agent_results()), [])

    def test_pass_b_defect_pattern_rejected(self):
        results = consistent_agent_results()
        results["S0-provision-enroll"]["known_file"] = None
        results["S2-trial-v2-confirmed"]["confirmed"] = dict(CONF_V1)
        results["S3-owner-rollback-v1"]["confirmed"] = dict(CONF_V2)
        violations = ab.validate_results(results)
        self.assertTrue(any(v.startswith("S0") for v in violations), violations)
        self.assertTrue(any(v.startswith("S2") for v in violations), violations)
        self.assertTrue(any(v.startswith("S3") for v in violations), violations)

    def test_missing_artifact_survival_cannot_pass(self):
        # The old survival compared None == None as a match; the
        # recorded survival flags alone no longer suffice because
        # S0's own artifacts are gated too.
        results = consistent_agent_results()
        results["S0-provision-enroll"]["credential_recorded"] = False
        violations = ab.validate_results(results)
        self.assertTrue(any("credential" in v for v in violations), violations)

    def test_false_survival_flag_rejected(self):
        results = consistent_agent_results()
        results["S2-trial-v2-confirmed"]["survival"]["known_file_same"] = False
        violations = ab.validate_results(results)
        self.assertTrue(any("known_file_same" in v for v in violations),
                        violations)

    def test_missing_scenario_rejected(self):
        results = consistent_agent_results()
        del results["S4-failing-candidate-fallback"]
        violations = ab.validate_results(results)
        self.assertTrue(any("S4" in v for v in violations), violations)

    def test_leftover_trial_rejected(self):
        results = consistent_agent_results()
        results["S5-invalid-signature-refused"]["trial"] = {"version": 2}
        violations = ab.validate_results(results)
        self.assertTrue(any("S5" in v for v in violations), violations)

    def test_finish_cannot_certify_inconsistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = ab.OUT
            ab.OUT = Path(tmp)
            try:
                bad = consistent_agent_results()
                bad["S2-trial-v2-confirmed"]["confirmed"] = dict(CONF_V1)
                code = ab.finish(BUILD_RECEIPT, bad, 0)
                receipt = json.loads((Path(tmp) / "smoke-receipt.json").read_text())
            finally:
                ab.OUT = original
        self.assertEqual(code, 1)
        self.assertFalse(receipt["bootable_update_rollback_demonstrated"])
        self.assertTrue(receipt["gate_violations"])

    def test_finish_certifies_consistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = ab.OUT
            ab.OUT = Path(tmp)
            try:
                code = ab.finish(BUILD_RECEIPT, consistent_agent_results(), 0)
                receipt = json.loads((Path(tmp) / "smoke-receipt.json").read_text())
            finally:
                ab.OUT = original
        self.assertEqual(code, 0)
        self.assertTrue(receipt["bootable_update_rollback_demonstrated"])
        self.assertEqual(receipt["gate_violations"], [])


class GuiGate(unittest.TestCase):
    def test_consistent_results_validate_clean(self):
        self.assertEqual(gui.validate_gui_results(consistent_gui_results()), [])

    def test_first_gui_receipt_defect_pattern_rejected(self):
        results = consistent_gui_results()
        results["G0-provision-enroll-gui-present"]["known_file"] = None
        results["G0-provision-enroll-gui-present"]["gui_log_ready"] = False
        results["G1-gui-stage-trial-confirm"]["confirmed"] = dict(CONF_V1)
        results["G1-gui-stage-trial-confirm"]["gui_clicks"]["confirm"] = False
        results["G1-gui-stage-trial-confirm"]["gui_actions_ok"]["confirm"] = False
        results["G2-gui-rollback-confirm"]["confirmed"] = {}
        violations = gui.validate_gui_results(results)
        self.assertTrue(any(v.startswith("G0") for v in violations), violations)
        self.assertTrue(any(v.startswith("G1") for v in violations), violations)
        self.assertTrue(any(v.startswith("G2") for v in violations), violations)

    def test_finish_cannot_certify_inconsistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gui.OUT
            gui.OUT = Path(tmp)
            try:
                bad = consistent_gui_results()
                bad["G1-gui-stage-trial-confirm"]["confirmed"] = dict(CONF_V1)
                code = gui.finish(BUILD_RECEIPT, bad, 0)
                receipt = json.loads(
                    (Path(tmp) / "gui-smoke-receipt.json").read_text())
            finally:
                gui.OUT = original
        self.assertEqual(code, 1)
        self.assertFalse(receipt["graphical_update_controls_demonstrated"])
        self.assertTrue(receipt["gate_violations"])

    def test_finish_certifies_consistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = gui.OUT
            gui.OUT = Path(tmp)
            try:
                code = gui.finish(BUILD_RECEIPT, consistent_gui_results(), 0)
                receipt = json.loads(
                    (Path(tmp) / "gui-smoke-receipt.json").read_text())
            finally:
                gui.OUT = original
        self.assertEqual(code, 0)
        self.assertTrue(receipt["graphical_update_controls_demonstrated"])
        self.assertEqual(receipt["gate_violations"], [])


if __name__ == "__main__":
    unittest.main()
