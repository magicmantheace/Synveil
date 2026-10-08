#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compile the fixed C probe and exercise it against Unix-socket fixtures."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.probe = Path(cls.temp.name) / "probe"
        subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "tests/protocol-probe.c"), "-o", str(cls.probe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, fault=None):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "core.sock")
            failures = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen()
                server.settimeout(1)

                def serve():
                    try:
                        for code, identifier in (("malformed_message", ""),
                                                 ("unsupported_schema", ""),
                                                 ("unknown_method", "method-test")):
                            stream, _ = server.accept()
                            with stream:
                                stream.settimeout(1)
                                request = b""
                                while not request.endswith(b"\n"):
                                    chunk = stream.recv(4096)
                                    if not chunk:
                                        raise AssertionError("probe sent an incomplete request")
                                    request += chunk
                                if code == "malformed_message":
                                    assert request == b"not-json\n"
                                elif code == "unsupported_schema":
                                    assert json.loads(request)["schema"] == "unsupported/v0"
                                else:
                                    assert json.loads(request)["method"] == "unregistered_test"
                                response = {"schema": "synveil.core/v1", "id": identifier,
                                            "ok": False, "error": {"code": code, "message": "fixture"}}
                                if fault == "ok":
                                    response["ok"] = True
                                elif fault == "code":
                                    response["error"]["code"] = "internal_failure"
                                elif fault == "schema":
                                    response["schema"] = "other/v0"
                                elif fault == "id":
                                    response["id"] = "wrong-id"
                                stream.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")
                                if fault:
                                    break
                    except Exception as error:
                        failures.append(error)

                worker = threading.Thread(target=serve)
                worker.start()
                try:
                    result = subprocess.run([str(self.probe), path], capture_output=True, timeout=5)
                finally:
                    worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(failures, repr(failures))
                return result.returncode

    def test_expected_rejections_pass(self):
        self.assertEqual(self.check(), 0)

    def test_unexpected_success_fails(self):
        self.assertNotEqual(self.check("ok"), 0)

    def test_wrong_error_fails(self):
        self.assertNotEqual(self.check("code"), 0)

    def test_wrong_schema_fails(self):
        self.assertNotEqual(self.check("schema"), 0)

    def test_wrong_correlation_fails(self):
        self.assertNotEqual(self.check("id"), 0)


if __name__ == "__main__":
    unittest.main()
