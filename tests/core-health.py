#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Live, bounded Unix-socket health probe fixtures. No target or QEMU needed."""
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPLAY = ROOT / "src/veil-display"
VALID = (b'{"schema":"synveil.core/v1","id":"synveil-desktop",'
         b'"ok":true,"result":{"service":"veil-core",'
         b'"version":"0.1","state":"ready",'
         b'"protocol":"synveil.core/v1"}}\n')


class CoreHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / "health-check"
        subprocess.run(
            ["gcc", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
             str(DISPLAY / "core_health.c"), str(ROOT / "tests/core-health-test.c"),
             "-I", str(DISPLAY), "-o", str(cls.binary)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_probe(self, response, expected, *, fragmented=False, delay=0):
        with tempfile.TemporaryDirectory() as temp:
            address = str(Path(temp) / "core.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(address)
                listener.listen(1)
                errors = []

                def server():
                    try:
                        listener.settimeout(2)
                        conn, _ = listener.accept()
                        with conn:
                            conn.settimeout(2)
                            request = b""
                            while b"\n" not in request:
                                data = conn.recv(1024)
                                if not data:
                                    raise AssertionError("probe did not send request")
                                request += data
                            assert request == (b'{"schema":"synveil.core/v1",'
                                               b'"id":"synveil-desktop",'
                                               b'"method":"status","params":{}}\n')
                            if delay:
                                time.sleep(delay)
                            if response:
                                try:
                                    if fragmented:
                                        conn.sendall(response[:9])
                                        time.sleep(0.03)
                                        conn.sendall(response[9:])
                                    else:
                                        conn.sendall(response)
                                except (BrokenPipeError, ConnectionResetError):
                                    pass
                    except Exception as error:
                        errors.append(error)

                worker = threading.Thread(target=server)
                worker.start()
                started = time.monotonic()
                result = subprocess.run([str(self.binary), address, str(expected)],
                                        capture_output=True, timeout=2)
                elapsed = time.monotonic() - started
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(errors, errors)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(elapsed, 0.75)

    def test_valid_core_reply(self):
        self.check_probe(VALID, 1)

    def test_split_packet_core_reply(self):
        self.check_probe(VALID, 1, fragmented=True)

    def test_denial_and_error_reply_never_report_ready(self):
        self.check_probe(VALID.replace(b'"ok":true', b'"ok":false'), 0)

    def test_stale_or_wrong_identity_does_not_report_ready(self):
        self.check_probe(VALID.replace(b'"id":"synveil-desktop"', b'"id":"wrong"'), 0)
        self.check_probe(VALID.replace(b'"state":"ready"', b'"state":"failed"'), 0)
        self.check_probe(VALID.replace(b'synveil.core/v1', b'synveil.core/v2'), 0)

    def test_truncated_frame_does_not_report_ready(self):
        self.check_probe(VALID[:-1], 0)

    def test_unresponsive_peer_is_bounded(self):
        self.check_probe(None, 0, delay=0.48)

    def test_missing_or_stale_socket_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as temp:
            address = str(Path(temp) / "core.sock")
            subprocess.run([str(self.binary), address, "0"], check=True, timeout=2)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(address)
            sock.close()
            subprocess.run([str(self.binary), address, "0"], check=True, timeout=2)


if __name__ == "__main__":
    unittest.main()
