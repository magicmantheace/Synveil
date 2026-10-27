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

browser = load_module("smoke_native_browser", "smoke-native-browser.py")

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


CA_SHA = "a" * 64
CRED_SHA = "c" * 64


SANDBOX_GOOD = (
    "SYNVEIL_SANDBOX_RENDERER user_ns=user:[4026532001] "
    "session_user_ns=user:[4026531837] seccomp=2 pid=431\n"
    "SYNVEIL_SANDBOX_RENDERER user_ns=user:[4026532102] "
    "session_user_ns=user:[4026531837] seccomp=2 pid=977\n"
    "SYNVEIL_SANDBOX_RENDERER user_ns=user:[4026532255] "
    "session_user_ns=user:[4026531837] seccomp=2 pid=1204\n")


def browser_facts(confirmed):
    return {
        "control": {"confirmed": dict(confirmed)},
        "credential_sha256": CRED_SHA,
        "known_file": ab.EXPECTED_KNOWN_FILE,
        "downloads_listing": ["payload.bin", "payload.sha256",
                              "payload (1).bin",
                              "payload2.bin", "payload2.sha256"],
        "payload_sha256": browser.PAYLOAD_SHA256,
        "payload_size": browser.PAYLOAD_SIZE,
        "payload_guest_sha256": browser.PAYLOAD_SHA256,
        "payload1_sha256": browser.PAYLOAD_SHA256,
        "payload1_size": browser.PAYLOAD_SIZE,
        "payload2_sha256": browser.PAYLOAD2_SHA256,
        "payload2_size": browser.PAYLOAD_SIZE,
        "payload2_guest_sha256": browser.PAYLOAD2_SHA256,
        "big_size": None,
        "big_partial_size": 12345,
        "profile_preferences":
            '{"download":{"default_directory":'
            '"/home/synveil/Downloads"}}',
        "sandbox_status": SANDBOX_GOOD,
        "browser_started_count": 3,
        "files_entry_lines": 9,
        "files_payload_scans": 3,
        "files_payload2_scans": 2,
        "netlog_files": 2,
        "netlog_dns_hosts": ["test.synveil.invalid"],
        "netlog_sockets": ["10.0.2.2:8443", "10.0.2.2:8444",
                           "10.0.2.3:53", "127.0.0.1:443"],
        "netlog_attempted_hosts": ["accounts.google.com",
                                   "test.synveil.invalid",
                                   "update.googleapis.com"],
    }


def _get(t, port, path):
    return {"kind": "GET", "port": port, "path": path, "t": t}


def consistent_browser_results():
    log = [
        _get(10, browser.GOOD_PORT, "/"),
        _get(20, browser.GOOD_PORT, "/payload.bin"),
        _get(30, browser.GOOD_PORT, "/payload.bin"),
        _get(40, browser.GOOD_PORT, "/big.bin"),
        {"kind": "handshake-error", "port": browser.BAD_PORT, "t": 50},
        _get(60, browser.GOOD_PORT, "/"),
        _get(70, browser.GOOD_PORT, "/"),
        _get(80, browser.GOOD_PORT, "/"),
        _get(85, browser.GOOD_PORT, "/payload2.bin"),
        _get(90, browser.GOOD_PORT, "/"),
        _get(100, browser.GOOD_PORT, "/"),
    ]
    return {
        "build": {"browser_test_ca_sha256": CA_SHA},
        "tls": {"ca_sha256": CA_SHA},
        "server_log": log,
        "B0": {"network_ready": True, "page_proof": True, "at": 15},
        "B1": {"download_started": True,
               "artifacts": browser_facts(CONF_V1), "at": 25},
        "B2": {"second_download": True,
               "artifacts": browser_facts(CONF_V1),
               "payload_sha256_after": browser.PAYLOAD_SHA256, "at": 35},
        "B3": {"browser_alive_after": True,
               "artifacts": browser_facts(CONF_V1), "at": 45},
        "B4": {"page_left": True, "back_restored": True, "at": 55},
        "B5": {"artifacts": browser_facts(CONF_V1), "at": 65},
        "B6": {"artifacts": browser_facts(CONF_V2), "at": 88},
        "B7": {"artifacts": browser_facts(CONF_V1), "at": 105},
    }


BROWSER_BUILD_INFO = {"source_commit": "0" * 40, "disk_sha256": "0" * 64,
                      "browser_test_ca_sha256": CA_SHA}
BROWSER_TLS_INFO = {"ca_sha256": CA_SHA, "host_name": browser.HOST_NAME,
                    "host_ip": browser.HOST_IP,
                    "good_port": browser.GOOD_PORT,
                    "bad_port": browser.BAD_PORT}


class BrowserGate(unittest.TestCase):
    def validate(self, results):
        ok, failures = browser.validate_browser_results(results)
        return ok, failures

    def test_consistent_results_validate_clean(self):
        ok, failures = self.validate(consistent_browser_results())
        self.assertTrue(ok, failures)
        self.assertEqual(failures, [])

    def test_vendor_egress_not_gated(self):
        # Owner decision, 2026-10-27: no connection blocking,
        # so vendor DNS and foreign IPv4/IPv6 sockets recorded
        # in the net log are DISCLOSED in the receipt and can
        # never fail the gate.
        results = consistent_browser_results()
        facts = results["B1"]["artifacts"]
        facts["netlog_dns_hosts"] = [
            "accounts.google.com",
            "update.googleapis.com",
            "test.synveil.invalid",
        ]
        facts["netlog_sockets"] = [
            "142.250.80.46:443",
            "[2607:f8b0:4005:805::200e]:443",
            "::1",
            "10.0.2.2:8443",
        ]
        ok, failures = self.validate(results)
        self.assertTrue(ok, failures)
        self.assertEqual(failures, [])

    def test_bad_ca_get_rejected(self):
        results = consistent_browser_results()
        results["server_log"].append(
            _get(52, browser.BAD_PORT, "/"))
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any("bad-CA" in f for f in failures), failures)

    def test_missing_handshake_refusal_rejected(self):
        results = consistent_browser_results()
        results["server_log"] = [
            e for e in results["server_log"]
            if e["kind"] != "handshake-error"]
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B4") for f in failures), failures)

    def test_rollback_losing_download_rejected(self):
        results = consistent_browser_results()
        results["B7"]["artifacts"]["payload_sha256"] = None
        results["B7"]["artifacts"]["payload_guest_sha256"] = None
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B7") for f in failures), failures)

    def test_update_not_confirmed_rejected(self):
        results = consistent_browser_results()
        results["B6"]["artifacts"]["control"] = {
            "confirmed": dict(CONF_V1)}
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B6") for f in failures), failures)

    def test_silent_overwrite_rejected(self):
        results = consistent_browser_results()
        results["B2"]["payload_sha256_after"] = "0" * 64
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B2") for f in failures), failures)

    def test_no_uniquified_collision_copy_rejected(self):
        results = consistent_browser_results()
        results["B2"]["artifacts"]["payload1_sha256"] = None
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B2") for f in failures), failures)

    def test_unsandboxed_renderer_rejected(self):
        results = consistent_browser_results()
        results["B1"]["artifacts"]["sandbox_status"] = (
            "SYNVEIL_SANDBOX_RENDERER user_ns=user:[4026531837] "
            "session_user_ns=user:[4026531837] seccomp=0 pid=431\n")
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any("sandbox" in f for f in failures), failures)

    def test_sandbox_records_missing_rejected(self):
        results = consistent_browser_results()
        results["B7"]["artifacts"]["sandbox_status"] = ""
        ok, failures = self.validate(results)
        self.assertFalse(ok)

    def test_complete_big_file_rejected(self):
        results = consistent_browser_results()
        results["B3"]["artifacts"]["big_size"] = browser.BIG_SIZE
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B3") for f in failures), failures)

    def test_profile_outside_home_rejected(self):
        results = consistent_browser_results()
        results["B6"]["artifacts"]["profile_preferences"] = \
            '{"download":{"default_directory":"/tmp"}}'
        ok, failures = self.validate(results)
        self.assertFalse(ok)
        self.assertTrue(any(f.startswith("B6") for f in failures), failures)

    def test_trust_bundle_mismatch_rejected(self):
        results = consistent_browser_results()
        results["build"]["browser_test_ca_sha256"] = "b" * 64
        ok, failures = self.validate(results)
        self.assertFalse(ok)

    def test_finish_cannot_certify_inconsistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = browser.OUT
            browser.OUT = Path(tmp)
            try:
                bad = consistent_browser_results()
                bad["B7"]["artifacts"]["payload2_sha256"] = None
                code = browser.finish(BROWSER_BUILD_INFO, BROWSER_TLS_INFO,
                                      bad, 0)
                receipt = json.loads(
                    (Path(tmp) / "browser-receipt.json").read_text())
            finally:
                browser.OUT = original
        self.assertEqual(code, 1)
        self.assertFalse(receipt["demonstrated"])
        self.assertTrue(receipt["gate"]["failures"])

    def test_finish_certifies_consistent_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = browser.OUT
            browser.OUT = Path(tmp)
            try:
                code = browser.finish(BROWSER_BUILD_INFO, BROWSER_TLS_INFO,
                                      consistent_browser_results(), 0)
                receipt = json.loads(
                    (Path(tmp) / "browser-receipt.json").read_text())
            finally:
                browser.OUT = original
        self.assertEqual(code, 0)
        self.assertTrue(receipt["demonstrated"])
        self.assertEqual(receipt["gate"]["failures"], [])


class NetlogExtract(unittest.TestCase):
    """Parser-level regressions for the 2026-10-27 review:
    IPv6 endpoints were silently dropped by _netlog_extract,
    so the gate judged an incomplete egress set."""

    def extract(self, blob):
        return browser._netlog_extract(blob.encode())

    def test_ipv6_endpoint_extracted(self):
        _dns, sockets, _att = self.extract(
            '{"params": {"remote_endpoint": '
            '"[2607:f8b0:4006:81a::200e]:443"}, "source": '
            '{"id": 3, "type": 1}}')
        self.assertIn("[2607:f8b0:4006:81a::200e]:443", sockets)

    def test_bare_ipv6_address_extracted(self):
        _dns, sockets, _att = self.extract(
            '{"params": {"address": "2607:f8b0:4006::1"}, '
            '"source": {"id": 3, "type": 1}}')
        self.assertIn("2607:f8b0:4006::1", sockets)

    def test_ipv4_endpoint_still_extracted(self):
        _dns, sockets, _att = self.extract(
            '{"params": {"remote_endpoint": "10.0.2.2:8443"}, '
            '"source": {"id": 3, "type": 1}}')
        self.assertIn("10.0.2.2:8443", sockets)



if __name__ == "__main__":
    unittest.main()
